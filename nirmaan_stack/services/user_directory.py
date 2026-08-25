"""One place that turns a user email into a display name — including for users who are gone.

Every surface that shows "Created By" / "Last Modified By" / "Approved By" is
reading `owner`, `modified_by` or `completed_by`. Those are NOT links to a user:
`owner` and `modified_by` are plain `varchar(140)` columns from
``frappe.model.default_fields`` with zero DocField rows anywhere, so nothing
validates them, nothing cascades, and nothing cleans them up. They hold an email
string forever, long after the account it names has been deleted.

That is why names go missing. When a `User` row is hard-deleted the name goes
with it, and every one of those stamps degrades to a raw login id on screen. As
of Aug 2026 that is ~13.7k rows across 15 deleted identities, most of them shared
operational accounts (``accounts@nirmaan.app`` alone is on 9.4k rows).

This module resolves an email through three tiers, in the order a name is most
likely to be correct:

1. ``Nirmaan Users``  — the profile of record, what the frontend already matches on
2. ``User``           — covers accounts that never got a profile row
3. ``Deleted Document`` — the tombstone: Frappe stores the COMPLETE JSON of every
   deleted doc, so a deleted user's ``full_name`` is still recoverable. All 73
   deleted Users on this site still have one.

Falls back to the email itself, never to a blank — the same contract the frontend
already uses (``full_name || id || "--"``), so this is a drop-in for the
per-page ``getUserName`` closures.

Lives in ``services/`` rather than ``api/`` because ``api/``, ``integrations/``
and Jinja print formats all consume it, and api -> service is the one legal
direction (same reasoning as ``services/role_profiles.py``). Reads ``frappe.db``
but never ``frappe.session``, so it stays callable from a background job too.

The whole directory is ~200 rows, so it is loaded and cached WHOLE rather than
queried per email. That keeps every lookup O(1) with no ``IN`` list, which also
sidesteps the sqlparse token cap that big name-in lists trip in production.
"""

import json

import frappe

# Two keys, not one: the live half changes when someone is renamed or added, the
# tombstone half only when a delete happens. They are invalidated by different
# events (see hooks.py -> doc_events).
LIVE_CACHE_KEY = "nirmaan:user_directory:live"
TOMBSTONE_CACHE_KEY = "nirmaan:user_directory:tombstones"

# The live half self-heals even if an invalidation hook is ever missed. The
# tombstone half has no TTL: a deleted doc's JSON is immutable, so the only thing
# that can change it is a NEW delete, which clears it explicitly.
LIVE_TTL_SEC = 300

SOURCE_PROFILE = "profile"
SOURCE_USER = "user"
SOURCE_DELETED = "deleted"

_RESOLVABLE_DOCTYPES = ("User", "Nirmaan Users")


def _full_name_from(payload) -> str | None:
    """Best available name from a User / Nirmaan Users row, or from its deleted JSON.

    Both doctypes derive `full_name` from first + last, but a legacy or
    partially-populated row can have the parts without the derived field, so fall
    back to joining them rather than reporting the user as unresolvable.
    """
    full = (payload.get("full_name") or "").strip()
    if full:
        return full

    parts = [(payload.get("first_name") or "").strip(), (payload.get("last_name") or "").strip()]
    return " ".join(p for p in parts if p) or None


def _live_directory() -> dict:
    """`User` overlaid by `Nirmaan Users` — the two tables that hold a CURRENT name."""
    cached = frappe.cache().get_value(LIVE_CACHE_KEY)
    if cached is not None:
        return cached

    directory = {}

    # `frappe.get_all` ignores permissions by design, which is what we want: this
    # is a name directory, and a Procurement Executive cannot list `User` directly.
    for row in frappe.get_all(
        "User",
        fields=["name", "full_name", "first_name", "last_name", "enabled", "role_profile_name"],
        limit_page_length=0,
    ):
        directory[row.name] = {
            "name": row.name,
            "full_name": _full_name_from(row) or row.name,
            "role_profile": row.role_profile_name,
            # The ONLY definition of "still working here" that needs no new column:
            # a live, enabled User row. A profile with no User row is offboarded.
            "is_active": 1 if row.enabled else 0,
            "source": SOURCE_USER,
        }

    # Overlaid second, so the profile wins: it is the doctype the app treats as
    # the record of who someone is, and the one the frontend already matches on.
    for row in frappe.get_all(
        "Nirmaan Users",
        fields=["name", "full_name", "first_name", "last_name", "role_profile"],
        limit_page_length=0,
    ):
        entry = directory.get(row.name) or {"name": row.name, "is_active": 0, "role_profile": None}
        entry["full_name"] = _full_name_from(row) or entry.get("full_name") or row.name
        entry["role_profile"] = row.role_profile or entry.get("role_profile")
        entry["source"] = SOURCE_PROFILE
        directory[row.name] = entry

    frappe.cache().set_value(LIVE_CACHE_KEY, directory, expires_in_sec=LIVE_TTL_SEC)
    return directory


def _tombstones() -> dict:
    """Names recovered from `Deleted Document.data` — the last tier, and the whole point.

    A user can appear more than once (a profile deleted, resurrected by a Desk
    save, then deleted again), so rows are read oldest-first and the newest one
    wins on overwrite.
    """
    cached = frappe.cache().get_value(TOMBSTONE_CACHE_KEY)
    if cached is not None:
        return cached

    tombstones = {}

    for row in frappe.get_all(
        "Deleted Document",
        filters={"deleted_doctype": ["in", _RESOLVABLE_DOCTYPES]},
        fields=["deleted_name", "data"],
        order_by="creation asc",
        limit_page_length=0,
    ):
        try:
            payload = json.loads(row.data or "{}")
        except (ValueError, TypeError):
            # A truncated or non-JSON payload is not worth failing a page render over.
            continue

        full_name = _full_name_from(payload)
        if not full_name:
            continue

        tombstones[row.deleted_name] = {
            "name": row.deleted_name,
            "full_name": full_name,
            # `role_profile` on Nirmaan Users, `role_profile_name` on User.
            "role_profile": payload.get("role_profile") or payload.get("role_profile_name"),
            "is_active": 0,
            "source": SOURCE_DELETED,
        }

    frappe.cache().set_value(TOMBSTONE_CACHE_KEY, tombstones)
    return tombstones


def get_directory() -> dict:
    """``{email: {name, full_name, role_profile, is_active, source}}`` for everyone, ever.

    Tombstones are laid down first so a live row always wins — a re-hired user
    resolves to their current name, not the one captured at deletion.
    """
    directory = dict(_tombstones())
    directory.update(_live_directory())
    return directory


def get_user_name(email: str | None) -> str:
    """Display name for one email. Never blank — falls back to the email itself.

    This is the function to reach for from Python and from a print format.
    Resolving many emails at once? Use `get_user_names`; resolving a whole page
    of rows client-side? Use the `get_user_directory` endpoint and match locally.
    """
    if not email:
        return ""

    entry = get_directory().get(email)
    return (entry or {}).get("full_name") or email


def get_user_names(emails) -> dict:
    """Bulk form of `get_user_name`. One directory load regardless of how many emails."""
    if not emails:
        return {}

    directory = get_directory()
    return {email: (directory.get(email) or {}).get("full_name") or email for email in emails if email}


def clear_cache(doc=None, method=None) -> None:
    """Drop both halves. Signature takes (doc, method) so it can be a doc_event directly."""
    frappe.cache().delete_value(LIVE_CACHE_KEY)
    frappe.cache().delete_value(TOMBSTONE_CACHE_KEY)


def on_deleted_document(doc, method=None) -> None:
    """A delete just moved a name out of the live directory and into the tombstones.

    `Deleted Document` is written for EVERY doctype, so this fires often — hence
    the doctype guard before touching redis.
    """
    if doc.deleted_doctype in _RESOLVABLE_DOCTYPES:
        clear_cache()


# ---------------------------------------------------------------------------
# Data-stored user references
# ---------------------------------------------------------------------------
# These 17 fields hold a user id as DATA, not as a Link. They were converted from
# Link so that deleting a user no longer destroys or blocks the connection -- the
# email simply stays put and resolves to a name through this module.
#
# The cost of that conversion is that `rename_doc` CANNOT SEE THEM: it walks Link
# fields only, so an email rename would silently leave every one of these pointing
# at the old address. `rename_user_references` below is what pays that cost, and it
# is the reason this list must be updated whenever a field joins or leaves the set.
# Frappe solves the identical problem for `owner`/`modified_by` in `User.after_rename`
# by sweeping every table with raw SQL, for exactly the same reason.
#
# `Nirmaan User Permissions.user` predates the conversion -- it was ALWAYS Data, and
# `api/users.rename_user_email` has always hand-updated it. It belongs in this list so
# there is one place that knows where a user id is stored as text.
USER_ID_DATA_FIELDS = (
    ("Nirmaan Notifications", "recipient"),
    ("Nirmaan Notifications", "sender"),
    ("BoQ Row Category", "human_verdict_by"),
    ("BoQ Review Row", "revision_reviewed_by"),
    ("Pricing Access Log", "user"),
    ("BOQs", "uploaded_by"),
    ("Commission Report Task Child Table", "response_filled_by"),
    ("Pricing Workbook Version", "saved_by"),
    ("Project Schedule Milestone", "edited_by_user"),
    ("Internal Transfer Memo", "approved_by"),
    ("Internal Transfer Memo", "dispatched_by"),
    ("Internal Transfer Memo", "requested_by"),
    ("Project Snag Batch", "uploaded_by"),
    ("Reminder Schedule Log", "completed_by"),
    ("Project Snag", "status_changed_by"),
    ("Procurement Requests", "project_lead"),
    ("Procurement Requests", "procurement_executive"),
    ("Nirmaan User Permissions", "user"),
    # Asset holdings. Unlike everything above these are CURRENT STATE, not history --
    # converted on the owner's instruction so an asset stops refusing a removal. The
    # consequence is that NOTHING blocks that removal any more, so
    # `api/users.get_user_offboarding_blockers` is now the only thing that will tell
    # anyone a laptop was never handed back. Do not remove that endpoint.
    ("Asset Management", "asset_assigned_to"),
    ("Asset Master", "current_assignee"),
)


def rename_user_references(old_email: str, new_email: str) -> dict:
    """Repoint every Data-stored user id from `old_email` to `new_email`.

    Call this from an email-rename flow AFTER `rename_doc`, which handles the Link
    fields and (via `User.after_rename`) `owner` / `modified_by`, but is blind to
    everything in `USER_ID_DATA_FIELDS`.

    Returns ``{"Doctype.field": rows_updated}`` for the caller to log or assert on.
    Raw SQL by necessity -- these are bulk repoints of a stored id, and no derived
    field anywhere reads them, so no `doc_events` recompute is being skipped.
    """
    if not old_email or not new_email or old_email == new_email:
        return {}

    updated = {}
    for doctype, fieldname in USER_ID_DATA_FIELDS:
        key = f"{doctype}.{fieldname}"
        try:
            # Counted before the update rather than read off the cursor: `rowcount` is a
            # private DB-API detail on `frappe.db`, and this figure is reported to the caller.
            hits = frappe.db.sql(
                'SELECT count(*) c FROM "tab{d}" WHERE "{f}" = %s'.format(d=doctype, f=fieldname),
                (old_email,),
                as_dict=True,
            )[0]["c"]
            if hits:
                frappe.db.sql(
                    'UPDATE "tab{d}" SET "{f}" = %s WHERE "{f}" = %s'.format(d=doctype, f=fieldname),
                    (new_email, old_email),
                )
            updated[key] = hits
        except Exception:
            # A doctype can be absent on a site that never installed its module. Never
            # let one missing table abort a rename that has already renamed the User.
            # Title FIRST. `frappe.log_error` treats a newline-free first argument as the
            # TITLE, and Error Log.method is Data(140) -- so passing a long message first
            # raises CharacterLengthExceededError from inside the error handler itself.
            frappe.log_error(
                "Nirmaan Users Rename",
                f"rename_user_references failed for {key} ({old_email} -> {new_email})\n"
                + frappe.get_traceback(),
            )
            updated[key] = None

    return updated
