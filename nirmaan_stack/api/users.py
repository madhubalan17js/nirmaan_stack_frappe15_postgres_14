import re

import frappe
from frappe import _
from frappe.model.rename_doc import rename_doc

from nirmaan_stack.services import user_directory


@frappe.whitelist()
def create_user(
    first_name: str,
    last_name: str = None,
    email: str = None,
    mobile_no: str = None,
    role_profile_name: str = None
):
    """
    Create a new user with graceful handling of email failures.

    Creates the user first (with send_welcome_email=0), then attempts
    to send the welcome email separately. If email fails, user is still
    created successfully with a warning.

    Args:
        first_name: User's first name (required)
        last_name: User's last name
        email: User's email address (required, becomes the user ID)
        mobile_no: User's mobile number
        role_profile_name: Role profile to assign

    Returns:
        dict: {
            success: bool,
            user: User document name,
            message: str,
            email_sent: bool,
            email_error: str (if email failed)
        }
    """
    if not email:
        frappe.throw(_("Email is required"))

    if not first_name:
        frappe.throw(_("First name is required"))

    email = email.strip().lower()

    # Check if user already exists
    if frappe.db.exists("User", email):
        frappe.throw(_("User with email {0} already exists").format(email))

    # Mobile number is the login key for phone-based login, so it must be
    # exactly 10 digits and unique across users.
    if mobile_no:
        if not re.fullmatch(r"\d{10}", mobile_no):
            frappe.throw(_("Mobile number must be exactly 10 digits"))
        existing_mobile_user = frappe.db.get_value("User", {"mobile_no": mobile_no}, "name")
        if existing_mobile_user:
            frappe.throw(
                _("User with mobile number {0} already exists ({1})").format(
                    mobile_no, existing_mobile_user
                )
            )

    user_doc = None
    full_name = first_name  # Default fallback

    try:
        # Create user WITHOUT sending welcome email
        user_doc = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "mobile_no": mobile_no,
            "role_profile_name": role_profile_name,
            "send_welcome_email": 0,  # Disable automatic welcome email
            "user_type": "System User",
        })
        user_doc.insert(ignore_permissions=True)
        frappe.db.commit()
        full_name = user_doc.full_name or first_name

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(f"Failed to create user {email}: {str(e)}", "User Creation Failed")
        frappe.throw(_("Failed to create user: {0}").format(str(e)))

    # If we reach here, user was created successfully
    # Now attempt to send welcome email - completely isolated
    email_sent = False
    email_error_msg = None

    try:
        if user_doc:
            user_doc.send_welcome_mail_to_user()
            email_sent = True
    except Exception as email_error:
        email_error_msg = str(email_error) if email_error else "Unknown email error"
        # Log error silently - don't let logging failure affect response
        try:
            frappe.log_error(
                f"Failed to send welcome email to {email}: {email_error_msg}",
                "User Creation - Email Failed"
            )
        except Exception:
            pass  # Ignore logging errors

    # Build response - user is definitely created at this point
    if email_sent:
        return {
            "success": True,
            "user": email,
            "full_name": full_name,
            "message": f"User {full_name} created successfully. Welcome email sent.",
            "email_sent": True,
            "email_error": None
        }
    else:
        return {
            "success": True,
            "user": email,
            "full_name": full_name,
            "message": f"User {full_name} created successfully, but welcome email could not be sent. Please contact the tech admin to resolve email configuration issues.",
            "email_sent": False,
            "email_error": email_error_msg
        }


@frappe.whitelist()
def reset_password(user: str):
    """
    Send password reset email to a user with graceful error handling.

    Generates the reset key first (critical operation), then attempts to send
    the email. If email fails, returns success=True with email_sent=False
    since the reset key was generated.

    Args:
        user: User's email/name

    Returns:
        dict: {
            success: bool,
            message: str,
            email_sent: bool,
            reset_link: str (only if email failed, for manual sharing)
        }
    """
    if not user:
        frappe.throw(_("User is required"))

    user = user.strip().lower()

    # Check if user exists
    if not frappe.db.exists("User", user):
        frappe.throw(_("User {0} does not exist").format(user))

    # Step 1: Get user and generate reset key (critical operation)
    user_doc = None
    link = None

    try:
        user_doc = frappe.get_doc("User", user)

        # Generate reset password key
        from frappe.utils import random_string, get_url
        key = random_string(32)
        user_doc.db_set("reset_password_key", key)
        frappe.db.commit()

        # Build the reset link
        link = get_url(f"/update-password?key={key}")

    except Exception as e:
        frappe.log_error(f"Password reset failed for {user}: {str(e)}", "Password Reset Failed")
        frappe.throw(_("Failed to process password reset: {0}").format(str(e)))

    # Step 2: Send email (non-critical, completely isolated)
    email_sent = False
    email_error_msg = None

    try:
        frappe.sendmail(
            recipients=user_doc.email,
            subject="Password Reset",
            template="password_reset",
            args={
                "first_name": user_doc.first_name or user_doc.full_name,
                "last_name": user_doc.last_name or "",
                "user": user_doc.name,
                "link": link
            },
            header=["Password Reset", "green"],
            now=True
        )
        email_sent = True
    except Exception as email_error:
        email_error_msg = str(email_error) if email_error else "Unknown email error"
        # Log error silently
        try:
            frappe.log_error(
                f"Failed to send password reset email to {user}: {email_error_msg}",
                "Password Reset - Email Failed"
            )
        except Exception:
            pass  # Ignore logging errors

    # Build response - reset key is definitely generated at this point
    if email_sent:
        return {
            "success": True,
            "message": f"Password reset email has been sent to {user_doc.email}",
            "email_sent": True
        }
    else:
        return {
            "success": True,
            "message": f"Password reset link generated, but email could not be sent. Please contact the tech admin to resolve email configuration issues.",
            "email_sent": False,
            "email_error": email_error_msg,
            "reset_link": link  # Provide link so admin can share manually if needed
        }


@frappe.whitelist()
def get_user_role_counts():
	"""
	Get counts of users by role in a single database query.
	Dynamically fetches all Nirmaan role profiles from Role Profile doctype.
	Returns: {role_value: count, ...}
	"""
	# Fetch all role profiles starting with "Nirmaan" from Role Profile doctype
	nirmaan_roles = frappe.get_all(
		"Role Profile",
		filters={"name": ["like", "Nirmaan%"]},
		pluck="name"
	)

	role_counts = {}
	for role in nirmaan_roles:
		count = frappe.db.count('Nirmaan Users', filters={'role_profile': role})
		role_counts[role] = count

	return role_counts


@frappe.whitelist()
def rename_user_email(old_email: str, new_email: str):
	"""
	Rename a user's email address across all systems.

	- Admin only: Only admins can rename emails
	- Cannot rename self
	- Cannot rename other admins
	- Forces logout of renamed user

	Args:
		old_email: Current email address (user's name/primary key)
		new_email: New email address to change to

	Returns:
		dict: Success status with new email or error message
	"""
	# Normalize emails
	old_email = old_email.strip().lower()
	new_email = new_email.strip().lower()

	current_user = frappe.session.user

	# 1. Check if current user is admin (either system Administrator or Nirmaan Admin)
	is_admin = False
	if current_user == "Administrator":
		is_admin = True
	else:
		current_user_role = frappe.get_value("Nirmaan Users", current_user, "role_profile")
		if current_user_role == "Nirmaan Admin Profile":
			is_admin = True

	if not is_admin:
		frappe.throw(_("Only admins can rename user emails"), frappe.PermissionError)

	# 2. Cannot rename self
	if old_email == current_user.lower():
		frappe.throw(_("You cannot rename your own email"))

	# 3. Check if target user exists
	if not frappe.db.exists("Nirmaan Users", old_email):
		frappe.throw(_("User {0} does not exist").format(old_email))

	# 4. Cannot rename the system Administrator user
	if old_email.lower() == "administrator":
		frappe.throw(_("Cannot rename the system Administrator user"))

	# 5. Cannot rename other admins
	target_user_role = frappe.get_value("Nirmaan Users", old_email, "role_profile")
	if target_user_role == "Nirmaan Admin Profile":
		frappe.throw(_("Cannot rename admin users"))

	# 6. Validate email format
	if not frappe.utils.validate_email_address(new_email):
		frappe.throw(_("Invalid email format: {0}").format(new_email))

	# 7. Check uniqueness - email should not already exist
	if frappe.db.exists("User", new_email):
		frappe.throw(_("Email {0} already exists in the system").format(new_email))

	if frappe.db.exists("Nirmaan Users", new_email):
		frappe.throw(_("Email {0} already exists in Nirmaan Users").format(new_email))

	try:
		# 8. Rename User doctype first (handles Link fields to User automatically)
		rename_doc("User", old_email, new_email, force=True)

		# 9. Rename Nirmaan Users doctype (handles Link fields to Nirmaan Users automatically)
		rename_doc("Nirmaan Users", old_email, new_email, force=True)

		# 10. Update Data fields that don't auto-update (not Link fields).
		#     `rename_doc` walks Link fields only, so every field storing the user id as
		#     TEXT is invisible to it. Since the 17 user-reference fields were converted
		#     from Link to Data (so a deleted user's connections survive as data), that
		#     set is large -- ~70k values -- and skipping it would silently leave all of
		#     them pointing at the old address. The owning list is USER_ID_DATA_FIELDS.
		renamed_refs = user_directory.rename_user_references(old_email, new_email)

		# The directory caches names + the live/tombstone split, both keyed by email.
		user_directory.clear_cache()

		# 11. Force logout the renamed user by clearing their sessions
		frappe.db.sql("""
			DELETE FROM "tabSessions" WHERE "user" = %s
		""", (new_email,))

		frappe.db.commit()

		return {
			"success": True,
			"message": _("Email renamed from {0} to {1}").format(old_email, new_email),
			"new_email": new_email,
			# Per-field counts of the Data-stored references repointed above, so a rename
			# can be audited rather than assumed. A None means that field's table errored.
			"renamed_references": renamed_refs,
		}

	except Exception as e:
		frappe.db.rollback()
		frappe.log_error("Email rename failed", frappe.get_traceback())
		frappe.throw(_("Failed to rename email: {0}").format(str(e)))


@frappe.whitelist()
def get_user_directory(include_inactive: int = 1):
    """Every user this site has ever had, resolvable by email — for name display.

    Returns ``{"users": [{name, full_name, role_profile, is_active, source}, ...]}``.
    ``name`` is the email, which is what `owner` / `modified_by` / `completed_by`
    actually store, so the client can match on it directly.

    This is deliberately the WHOLE directory in one call (~200 rows) rather than a
    per-email lookup: a list page resolves a hundred owners with zero extra
    requests, and the payload caches client-side like any other list.

    ``source`` tells you where the name came from — ``profile`` (Nirmaan Users),
    ``user`` (a User row with no profile), or ``deleted`` (recovered from the
    Deleted Document tombstone, i.e. this person no longer exists on the site).

    Pass ``include_inactive=0`` for a picker or assignee dropdown, which should
    only ever offer people who still work here. Leave it at 1 for anything that
    RESOLVES a historical id to a name — filtering there is what makes old records
    show a raw email.
    """
    directory = user_directory.get_directory()

    users = sorted(directory.values(), key=lambda u: (u.get("full_name") or "").lower())

    if not int(include_inactive or 0):
        users = [u for u in users if u.get("is_active")]

    return {"users": users}


@frappe.whitelist()
def get_user_display_name(email: str):
    """Display name for a single email. Never blank — falls back to the email itself.

    For one-off lookups only. Resolving a list of rows? Call `get_user_directory`
    once and match client-side instead of calling this per row.
    """
    return {"name": email, "full_name": user_directory.get_user_name(email)}


@frappe.whitelist()
def get_user_offboarding_blockers(email: str):
    """What must be cleared before this person's profile can be removed.

    The 17 historical user-reference fields are Data now, so a record of what someone
    DID never blocks their removal. What still blocks is what they currently HOLD --
    chiefly assets -- and that block is deliberate: a laptop does not become historical
    when its holder leaves, so the delete refusal is the prompt to get it back.

    Frappe's own refusal names the blocking row by id ("linked with Asset Management
    0c68q6kf6g"), which tells nobody what to actually do. This returns the same
    blockers with names attached, so the UI can say what is held and by which person.

    Blockers come from `get_linked_docs` -- the SAME function `delete_doc` calls -- so
    this can never disagree with the real refusal, and a Link field added to
    `Nirmaan Users` in future shows up here with no change to this endpoint.

    Returns ``{"can_offboard": bool, "assets": [...], "other": {doctype: count}}``.
    """
    from frappe.model.delete_doc import get_linked_docs

    if not frappe.db.exists("Nirmaan Users", email):
        frappe.throw(_("No Nirmaan Users profile for {0}").format(email))

    # The link-field cache is per-request and keyed by doctype; clear it so a fieldtype
    # change (Link -> Data) is never served from a stale entry.
    frappe.flags.link_fields = {}
    links = get_linked_docs(frappe.get_doc("Nirmaan Users", email))

    # Assets are queried DIRECTLY, not read out of `links`. `asset_assigned_to` is a
    # Data field now, so it is not a reference and the link check cannot see it -- and
    # because nothing blocks the removal any more, this query is the ONLY remaining
    # warning that someone still holds equipment. Reading it from `links` would return
    # an empty list forever, silently.
    asset_mgmt = frappe.get_all(
        "Asset Management", filters={"asset_assigned_to": email}, pluck="name"
    )

    assets = []
    if asset_mgmt:
        for row in frappe.get_all(
            "Asset Management",
            filters={"name": ["in", asset_mgmt]},
            fields=["name", "asset", "asset_assigned_on"],
        ):
            master = frappe.db.get_value(
                "Asset Master", row.asset,
                ["asset_name", "asset_category", "asset_serial_number", "asset_condition"],
                as_dict=True,
            ) or {}
            assets.append({
                "assignment": row.name,
                "asset": row.asset,
                # Fall back to the id rather than a blank: an asset whose master row is
                # missing is exactly the case someone needs to see, not hide.
                "asset_name": master.get("asset_name") or row.asset,
                "asset_category": master.get("asset_category"),
                "serial_number": master.get("asset_serial_number"),
                "condition": master.get("asset_condition"),
                "assigned_on": row.asset_assigned_on,
            })

    # `links` now holds only genuine Link references -- any future field pointing at
    # `Nirmaan Users`. Assets are no longer among them, which is exactly why they are
    # queried separately above and ORed into the verdict below.
    other = {}
    for link in links:
        other[link["reference_doctype"]] = other.get(link["reference_doctype"], 0) + 1

    return {
        # Assets must be part of this or the answer is a lie: a Data field cannot block,
        # so someone holding twelve laptops would otherwise report as clear to remove.
        "can_offboard": not links and not assets,
        "assets": sorted(assets, key=lambda a: (a["asset_name"] or "").lower()),
        "other": other,
    }
