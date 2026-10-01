# Concurrent Edit — refusing a save made on an out-of-date copy

As-built reference for the stale-save guard (branch `feature/concurrent-edit-overwrite`: first
round 2026-09-29; the 2026-10-01 review round — 10 findings fixed plus the owner's conflict rules —
is described here as built). Before it, two people editing the same record meant **the
last save won**: the second save silently wrote its old copy of every field over the first
person's change. Now a screen sends the version of the record it loaded, and the server refuses
the save if the record has changed since.

⚠️ **Read "The key limitation — records with child tables" before adding this to any new
doctype.** The check watches one timestamp on the parent record. That fits small flat records and
does not fit Projects, Procurement Orders, Service Requests or Vendors.

---

## How it works

**No schema change, no migration, no new server code for plain edits.** It uses Frappe's own
optimistic-lock check:

1. The screen puts the `modified` it loaded into the save payload: `{ ...changes, modified }`.
2. Frappe's REST update (`frappe.api.v1.update_doc`) runs `doc.update(data)` then `save()`.
3. `set_user_and_timestamp` keeps the sent value as `_original_modified`.
4. `check_if_latest` takes a `SELECT ... FOR UPDATE` row lock and compares it with the stored
   `modified`. If they differ, the save is refused with **`TimestampMismatchError`** and nothing is
   written.

**Sending no `modified` keeps the old behaviour** (last save wins). So a screen that is not wired,
or a list that did not load `modified`, works exactly as before.

### The message

The server writes the words, so every screen and the bulk engines say the same thing. The wording
and the version helpers live in `nirmaan_stack/services/concurrent_edit.py`; the endpoint the
screens call is `nirmaan_stack/api/concurrent_edit/last_change.py`.

- `get_stale_message(doctype, name)` — whitelisted; the screens call it after a refusal. Checks read
  permission, then returns `{message, modified, by, at, self}`: the sentence (for toasts), the
  record's CURRENT version (so "Save again" can never resend the refused one, even when loading the
  record fails), the LAST saver's display name and save time, and whether that saver is the caller.
- `stale_message(modified_by, modified)` — the sentence builder, also used by the bulk engines.
  - another user: *"Nitesh Kumar changed this record at 29 Sep, 12:40 PM, after you opened it.
    Refresh and try again."*
  - the same user: *"You already changed this record at …, in another tab or window. Refresh and
    try again."*
  - no `modified_by`: the plain fallback, *"Someone else changed this record after you opened it.
    Refresh and try again."*
- `display_name(user)` — Nirmaan Users, then User, then the login.
- `parse_expected_modified`, `is_stale(doc, expected)`, `stale_reason(doc)` — the shared helpers
  for endpoints that take `expected_modified` (below). `is_stale` compares `cstr(doc.modified)`, the
  same comparison Frappe's `check_if_latest` makes; a row absent from the map is not checked.
- **The record holds only the LAST saver.** With three or more people, earlier changes may be
  someone else's, so no screen may present `by` as the author of every change.

### Frontend pieces

`frontend/src/utils/frappeErrors.ts`:

| Export | Use |
|---|---|
| `staleGuard(record)` | `{ modified }` when the record has one, else `{}`. Spread into an `updateDoc` payload. |
| `isStaleRecordError(error)` | True for `TimestampMismatchError` **and** PostgreSQL `SerializationFailure` ("could not serialize access due to concurrent update" — two saves in the same second; the loser gets HTTP 500 with that error). Both mean "someone saved first", so both get the same banner. |
| `fetchStaleInfo(call, doctype, name)` | Calls `get_stale_message` through the SDK client (never a raw `fetch`); returns `StaleInfo` `{message, modified, by, at, self}`, or the plain message on any failure. |
| `describeWriteError(error, fallback)` | Prefers the server's own `_server_messages` over the SDK's generic "There was an error." |

`frontend/src/hooks/useStaleConflict.tsx` (tests: `useStaleConflict.test.ts`):

- `useStaleConflict({ doctype, record, open, onRefresh? })` returns
  `{ conflict, guard, handle, isSaveBlocked }`.
  - `guard()` — spread into the save payload. Before a conflict it is `staleGuard(record)`; after
    one it carries the **latest** `modified`, so "Save again" is applied on top of the other
    person's version.
  - `handle(error, onLatest)` — on a stale refusal: fetches the message (`fetchStaleInfo`) and the
    latest record **via `frappe.client.get`** (the REST read drops null fields, so a field the other
    person cleared would never be named), sets the banner, calls `onLatest(latest, opened)`, asks
    the lists behind the dialog to re-fetch, and returns `true` (keep the dialog open). Any other
    error returns `false` and is left to the dialog.
  - `isSaveBlocked(form)` — for the "Save again" button's `disabled`. True while the banner shows
    and nothing in the form changed since the refill (pass everything the form holds: fields,
    picked file, attachment action, section toggles). Never blocks when the latest could not be
    loaded (`conflict.unloaded`). ⚠️ **Put it FIRST in the expression:**
    `disabled={stale.isSaveBlocked(form) || other}`. Its first call after a warning photographs
    the form; behind `other ||` it is skipped while `other` is true (a required field the latest
    data left empty), the photo is taken only once the user has re-entered their change, and the
    button never enables again. `useStaleConflict.test.ts` scans `pages/` and `components/` for
    the wrong order.
  - A **moving baseline**: after a conflict, the next comparison is against that conflict's latest
    version, so a second conflict on the same open dialog lists only what changed since the first.
  - The conflict is cleared when the dialog opens/closes or the record changes.
- `changedFields(opened, latest, labels)` — pure; the changed fields' **names only**, using the
  form's own labels sent by `get_stale_message` ("UTR", "Payment Date"), else the tidied fieldname.
  Skips system fields, `_` fields, child tables / JSON, and fields the screen never loaded.
- `takeLatest(current, opened, latest)` — pure; the form becomes the LATEST saved version for
  every field the mapping covers. What the user typed is not kept.
- `followAttachment(opened, latest)` — pure; the attachment always shows the latest saved file; the
  caller clears the user's own picked file.
- `conflictHeadline(info)` — pure; the banner title, naming only the last saver.
- `resolveConflict(record, base, latest, info)` — pure; one refusal worked out (banner, merge
  base, next base).
- `formSignature(value)` — pure; the form fingerprint behind `isSaveBlocked` (a File compares by
  name, size, date; null / undefined / "" compare equal).
- `useWriteErrorMessage()` — the toast text for a failed one-click action: the named stale message,
  else `describeWriteError`.
- `RECORD_CHANGED_EVENT` — the window event that makes `useServerDataTable` lists of the doctype
  re-fetch.
- `<StaleConflictBanner conflict={stale.conflict} />` — the warning banner inside the dialog.

`frontend/src/components/helpers/CustomAttachment.tsx` clears its hidden file input when the parent
clears the picked file, so the user can pick the **same** file again after a conflict.

### Server endpoints that act on what the user saw

A plain `updateDoc` gets the check for free. Endpoints that change a record through their own code
take the version explicitly as **`expected_modified`**:

| Endpoint | Behaviour |
|---|---|
| `api/payments/bulk_actions.bulk_lead_approve_payments` / `bulk_ceo_approve_payments` | `expected_modified` = `{name: modified}` JSON. Each row is checked under its row lock; a changed row is refused on its own with the named reason, the rest of the batch goes through. L1 approve also re-derives each payment's landing status from the **locked** amount (`target_for_amount`); a payment whose amount moved into another approval level is refused on its own (H3 — built, waiting for the owner's sign-off). |
| `api/approvals/expense_actions.bulk_lead_approve_expenses` / `bulk_ceo_approve_expenses` | Same, per expense. The L1 tier (Approved vs CEO Pending) is now read from the amount **under the lock**, not from the list read before it. |
| `api/payments/project_payments.ceo_approve_payment` | `expected_modified` = the payment's version. `_lock_and_check_ceo_approvable` locks the row first, refuses a stale payment with `TimestampMismatchError`, and also refuses a project on **CEO Hold** (the plain full approve used to rely on the screen alone). |
| `api/payments/project_payments.update_payment_request` (action `fulfil`) | `expected_modified` = the version the fulfil screen showed (`UpdatePaymentDialog`). `_lock_unchanged_payment` locks the payment and refuses it if it changed, so a payment is never closed at Paid on figures the accountant did not see. Delete stays unguarded by design. |
| `api/assets/assign_asset.assign_asset` | Updates `Asset Master.current_assignee` AND creates the `Asset Management` record in **one transaction** (it used to be two browser calls; a failed second call left an assignee with no assignment). `expected_modified` checked under the row lock. Used by `AssignAssetDialog` and `AssignAssetToUserDialog`. |

All of them use the shared helpers in `services/concurrent_edit.py` (`parse_expected_modified`,
`is_stale`, `stale_reason`). Not sent = no check.

**Separate concurrency fixes in the same branch:**
- `api/tds_challan/pay_tds._apply` locks the selected deduction rows (`SELECT ... FOR UPDATE`,
  sorted by name) after the challan lock. Two people paying the same deduction against two
  different challans used to both see it Pending.
- **One lock order for every TDS writer: challans first, then deductions** —
  `services/payment_tds.lock_challans_then_deductions`, used by paying, by restating a deduction on
  a payment edit, and by the payment `on_trash` controller. Paying locked challan → deduction while
  restate / delete locked deduction → challan; two real connections deadlocked (reproduced
  2026-10-01). Now the second writer simply waits. See `payment-tds.md`.

---

## What the user sees — one rule per kind of screen

| Screen kind | On a stale refusal |
|---|---|
| **Edit form** (dialog with fields) | No toast. The dialog stays open with the banner; the form is refilled with the LATEST saved version; the button reads **"Save again"**, is **disabled until the user changes something**, and that save carries the latest version. Same-second saves (HTTP 500 `SerializationFailure`) get the same banner. |
| **One-click action** (approve, reject, mark paid, assign, unassign) | A toast naming who changed it and when; the dialog closes and the list reloads, so the user acts on the current record. |
| **Bulk action** | A summary toast ("2 succeeded, 4 failed") and a failure list with each row's reason. Unchanged rows still go through. |

**After a refusal the LATEST saved data wins, for every field (owner rule, 2026-10-01).** The form
shows the latest version (`takeLatest`); what the user typed is NOT kept — they enter again only
what they still need. Attachments follow the same rule (`followAttachment`): the latest saved file
becomes "Current attachment", even when the other person did not touch it, and the user's own
picked file is dropped; to use theirs they Replace / Remove and upload again. Do NOT reintroduce
keep-what-I-typed merging.

**The banner is short (owner rule, 2026-10-01)** — three lines:

> **Updated by Nitesh Kumar at 01 Oct, 06:07 PM, after you opened it.**
> Changed: Invoice ref, Invoice date
> The form now shows the latest. Make your changes again to save.

- The title names only the LAST saver (`conflictHeadline`); the same user in another tab reads
  "You updated this record in another tab or window at …".
- "Changed:" lists field names only — the form below already shows the new values.
- When the latest version could not be loaded, the last line reads "Their changes could not be
  loaded. Save again saves this form as shown." and Save again is never blocked.

**Measured live (2026-10-01, two browsers, clicks 2 ms apart):** the winner sees the green
"Success!" toast and the dialog closes; the loser's PUT returns 500 `SerializationFailure`, and the
loser sees no toast — the banner, the refilled form and a disabled Save again. Only the winner's
change is written.

**The list behind the dialog re-fetches on a refusal** (`useStaleConflict.handle`). It fires
`RECORD_CHANGED_EVENT` (every `useServerDataTable` list of that doctype re-fetches) and revalidates
every SWR cache entry whose key names the doctype (the SDK's default `useFrappeGetDoc` /
`useFrappeGetDocList` keys, `Items <id>`, `Asset Master_<id>`, …). A list with a custom key that
does not name the doctype passes `onRefresh` (the Commission tabs pass their `mutate`). A dialog
that refills its form when its record re-fetches skips that while `stale.conflict` is set, or the
refresh would wipe what the user typed.

---

## ⚠️ Known gaps — read before testing

These are real and expected. They are not caused by the check, and a tester should not report them
as failures of these commits.

### 1. Other people's tables do not update live (frappe-react-sdk bug, NOT fixed here)

⚠️ **This is a FUTURE GOAL, on hold by the owner (2026-10-01) — not done, not forgotten.** See
"On hold and future goals" below for why it is held and how it must be checked when built.

The re-fetch above runs **in the browser whose save was refused only** — `RECORD_CHANGED_EVENT` is a
`window` event and never leaves that tab.

**Not universal (seen live 2026-10-01):** the product page (`/products/:id`), the asset page
(`/asset-management/:id`) and the payments approval queue (CEO Pending) DID re-fetch within a second
of another user's save. The expense, inflow and invoice lists did not. Two consequences:
- On those screens a "stale" action is usually not stale any more — the screen already shows the
  latest, so the action is (correctly) accepted. Testing a refusal there needs a screen that does
  not refresh; testing a bulk/approve action there makes it REAL.
- A dialog on such a screen must keep the record **as opened** (a snapshot) for both its form and
  the version it sends — see Gotchas.

| Who | Their table after someone else's save | What updates it |
|---|---|---|
| The person whose save was **refused** | ✅ updates | the refusal's re-fetch in their own tab |
| The person whose save **succeeded** | ✅ updates | their own save's success refresh |
| **Anyone else** just viewing the list | ❌ **stays stale until reload** | would need Frappe's live `list_update` socket event — dropped by the SDK bug |

Measured in Chrome (2026-09-29, In-Flow Payments): Priyanka saved ₹19,60,003; her table showed it,
while Nitesh, only watching the same list, still showed ₹19,60,000 eight seconds later.

**Cause:** frappe-react-sdk's `useFrappeEventListener` cleans up with `socket.off(event)` **without
the handler**, which removes **every** component's handler for that event; the last component to
register is the only one still listening. `useFrappeDocTypeEventListener` also sends
`doctype_unsubscribe` when any one listener unmounts, taking the whole tab out of the doctype room.
Frappe does send the event (a second component on the same page logs it), the table just no longer
has a handler. Pre-existing, app-wide (42 files use these hooks), and **out of scope for this
branch** — the fix is a separate job (our own event hook: remove only its own handler, ref-count
the room subscription).

**Why this is still safe:** the viewer's stale table cannot cause an overwrite — if they open the
record and save, the check refuses it, and the refusal then refreshes their table.

#### How to solve (planned — a separate branch, not part of this one)

The fix is ours to make: Frappe already sends the right events; only the frontend drops them.

1. **Add one event hook of our own** — `frontend/src/hooks/useRealtimeEvent.ts`, same call
   signatures as the SDK hooks so the switch is mechanical:
   - `useRealtimeEvent(event, handler)` — registers a **stable wrapper** (it reads the latest
     `handler` from a ref) with `socket.on(event, wrapper)` and cleans up with
     `socket.off(event, wrapper)`: **removes only its own handler**, never another component's.
     The stable wrapper also stops the listener being re-registered on every render.
   - `useDoctypeListUpdates(doctype, handler)` — replaces `useFrappeDocTypeEventListener`. The
     room subscription is **reference-counted** in a module-level `Map<doctype, count>`:
     `doctype_subscribe` is sent only when the count goes 0 → 1 and `doctype_unsubscribe` only
     when it goes 1 → 0, so one component unmounting no longer pulls the whole tab out of the
     room. Re-subscribe every room with a count > 0 once on socket `reconnect`.
   - `useDocumentUpdates(doctype, name, handler)` — the same for `useFrappeDocumentEventListener`
     (`doc_subscribe` / `doc_unsubscribe`, ref-counted per `doctype/name`).
2. **Switch the callers.** 78 call sites in 42 files use the three SDK hooks. Start with
   `hooks/useServerDataTable.ts` — one change fixes every DataTable list — then the rest, which is
   an import swap per file. Also fix our own handler-less cleanup in
   `pages/projects/TDSRepository/TDSRepositoryView.tsx` (lines ~216–218: `socket.off("tds_export_…")`
   → pass the handler).
3. **Stop it coming back:** a residence / lint check that fails on a new import of
   `useFrappeEventListener` / `useFrappeDocTypeEventListener` / `useFrappeDocumentEventListener`
   from `frappe-react-sdk`, and on `socket.off("<event>")` with no handler.
4. **Before building, check whether a newer `frappe-react-sdk` (we are on 1.17.0) already fixes
   `off(event)`.** If it does, upgrading is an option, but it brings other changes and still leaves
   the room-unsubscribe problem to verify. Patching `node_modules` is not an option (lost on every
   install).
5. **Test (two or three browsers):** a viewer's table updates within a few seconds of someone else's
   save, with the page also holding a second listener for the same doctype (In-Flow Payments has
   one in `NewInflowPayment`); closing a dialog that listens does not stop the table updating; a
   socket reconnect keeps updates flowing.

Once this lands, the refusal re-fetch in `useStaleConflict` stays — it still gives the refused user
an immediate refresh even if a live event is slow or missed.

### 2. A direct database update is invisible

A write that does not move `modified` — raw SQL, `frappe.db.set_value(..., update_modified=False)`,
a data fix run in the console — is **not refused, not listed in the banner, not in the Version
history, and not pushed to open pages** (no live event is sent). Test by changing records **through
the app or Desk** (`/app/<doctype>/<name>`), never directly in the table, or the results will look
wrong. Known writers of this kind: TDS bulk linking (`api/tds/linking.py`, `Items.linked_tds_item`,
`update_modified=False`) — a user who has Edit Product open with the Linked TDS Item field can put
the old link back — and CEO Hold (`services/ceo_hold/core.py`, Projects, not covered).

#### How to solve

The rule: **a server write to a field that some screen also sends must move `modified`.** Then the
check, the banner, the Version history and live refresh all see it.
- **TDS bulk linking** — `api/tds/linking.py` lines ~311 and ~373 write `Items.linked_tds_item`
  with `update_modified=False`. Change both to the default (`update_modified=True`); the product
  then shows as changed in the TDS module too. Needs the owner's OK (it changes TDS behaviour).
- **CEO Hold** — `update_modified=False` there is a standing owner ruling (a system hold must not
  restamp the project). Leave it; it is safe while no screen sends `status` / `ceo_hold_by` back
  (the Edit Project form does not). Re-check this whenever a Projects screen starts sending them.
- **Data fixes by hand** — run them as a normal save (`doc.save()` in the console) or `set_value`
  without `update_modified=False`, never raw SQL, when users may have the record open.
- **New code** — follow root `CLAUDE.md` "Raw SQL and `frappe.db.set_value` BYPASS the document
  lifecycle"; add "does a screen send this field?" to that review.

### 3. Records with child tables

Not covered by this method — see **"⚠️ The key limitation — records with child tables"** below.

#### How to solve

Build the **scoped check** described under "What this means" in that section: the screen sends the
values it loaded for the fields / child rows it is saving; one server helper compares only those with
the database under the row lock and refuses when they differ. It sees the changes the parent version
misses (point 2 there) and gives no false alarm for unrelated changes (point 1). First user: Vendors
(4 save points), then Projects, Procurement Orders, Work Orders.

---

## On hold and future goals (owner, 2026-10-01)

Everything below is **deliberately not finished**. "On hold" means the owner has not decided yet;
"future goal" means it is agreed it must be built, later, on its own branch.

⚠️ **Every item here must be checked in REAL TIME before it is called done: two (or three) live
browsers, two different logins, acting on the same record at the same moment** (the two-screen
test, Nitesh + Priyanka). A unit test or a single browser cannot show any of these — the bug only
exists when two people act together.

### H1. Live updates for other viewers (decisions D2–D4) — FUTURE GOAL, on hold

**What is missing:** when Nitesh saves, Priyanka's open table and open dialog do not change until
she refreshes or tries to save (Known gap 1). Seen live 2026-10-01 in both directions on the
expense / inflow / invoice lists — but the product page, asset page and the approvals queue already
refresh (Known gap 1, "Not universal"), so H1 starts from those as the working examples.

**Why it is on hold:**
- It is **app-wide, not finance-only**: 78 call sites in 42 files use the broken SDK hooks; this
  branch was scoped to the save guard.
- It needs **load limits decided first** (D4): without them, one bulk import makes every open
  browser re-fetch at once.
- The **dialog behaviour is an owner choice** (D3): show a "changed by X — load their changes" bar,
  or refresh the form automatically when the user has not typed yet.
- Nothing is unsafe meanwhile: a stale viewer cannot overwrite — the guard refuses the save and the
  refusal refreshes their screen.

**The decisions still open:**

| # | Question | Recommended |
|---|---|---|
| D2 | Who sees an update, and when | B now (tables update by themselves in ~1–2 s, via our own event hook), C next (open dialogs show a notice bar) |
| D3 | Open dialog when the other person saves (only with C) | A: show the bar, change nothing until the user clicks |
| D4 | Live-update limits (only with B) | All three: at most one re-fetch per 1–2 s per table, visible tab only, on-screen table only |

**How to build:** "How to solve (planned)" under Known gap 1.
**Real-time check when built:** Nitesh saves → Priyanka's table updates within a few seconds
without a reload; closing another dialog on her page does not stop it; a socket reconnect keeps it
working; a bulk change does not flood the server (count the re-fetches).

### H2. Stale actions after a status change (item 4 / D6) — on hold

**What is missing:** the version check guards EDITS, not one-click ACTIONS that are only valid in a
certain status. Seen live: "Record Invoice" was saved on an expense that had been approved
meanwhile.

**Why it is on hold:** it changes what users are allowed to do (the server would refuse an action
with "This expense is now Approved; Record Invoice is no longer available"), so it is a business
rule, not a bug fix — the owner decides it. Recommended: D6-A (refuse, with the reason).
**Real-time check when built:** Priyanka opens the action, Nitesh approves, Priyanka submits → refused
with the reason, nothing written.

### H3. Bulk approve re-checks the approval level under the lock (item 3) — built, waiting for sign-off

**What it fixes:** bulk L1 approve chose each payment's landing status (Approved, or CEO Pending
above ₹50,000) from the amount read BEFORE the row lock. An amount edited in that gap (40,000 →
60,000) still landed at Approved, skipping the CEO. Now `_process_group` re-derives the status from
the LOCKED amount (`target_for_amount` in `api/payments/bulk_actions.py`) and refuses that one
payment ("The amount is now 60,000, which needs a different approval level. Refresh and approve
again."); the rest of the batch goes through.

**Why it is on hold:** the code is in; it only waits for the owner's yes, because it adds a new
refusal users will see. Keep → ships as is. Drop → remove only the `target_for_amount` part.
**Real-time check:** lead has the bulk list open, accountant raises an amount past ₹50,000, lead
approves → that payment is refused, the others land.

### Future work (agreed, not on hold — build later)

1. Expense Request edit / approve / reject guard (no `expected_modified` yet).
2. Project GST form and Cashflow Plan (JSON `items`) guard.
3. The scoped check for records with child tables — Vendors, Projects, Procurement Orders, Work
   Orders (Known gap 3).

---

## Covered

### Money
- **Project Payments** — single approve / reject (`ApprovePayments.tsx`), bulk lead / CEO approve,
  CEO approve (`ceo_approve_payment`), Mark as Paid (`AccountantTabs.tsx`, per row), fulfil
  (`update_payment_request`, from `UpdatePaymentDialog`).
- **Payments-table edit** (`QueueRowEditDialog`, the expense edit opened from the Payments / Approvals
  table) — loads the record via `frappe.client.get` so empty fields are kept and named in the banner.
- **Project Expenses, Non Project Expenses** — edit, update payment, update invoice dialogs; list
  status actions; bulk approve.
- **Project Inflows** (`EditInflowPayment`), **Non Project Inflows** (`NonProjectInflowDialog`).
- **Project Invoices** — edit dialog (`EditProjectInvoiceDialog`).

### Assets
- Asset Master edit (`AssetOverview`), Asset Category edit (`AssetCategoryView`,
  `AssetCategoriesList`).
- Assign / unassign on the asset pages and on the user profile Assets tab.
  - Assign goes through ONE server call, `api/assets/assign_asset` (asset + assignment record in
    one transaction, version checked under the lock).
  - Unassign still uses `updateDoc` with `staleGuard`.
  - The user profile reloads the asset list after assign / unassign, so the next action on the same
    asset carries the current version.

### Other masters
- **PR Tag Headers** (`PRHeaderTagMaster`), **Help Repository** edit (`EditHelpDialog`).
- **Items (Products)** — the Edit Product dialog (`pages/Items/components/EditItemDialog.tsx`),
  opened from the product page, the Products list and the TDS Repository items tab. The Products
  list query now loads `modified` (`ITEM_LIST_FIELDS_TO_FETCH`; before/after 3,537 rows, DIFF 0).
  The daily `item_status_update` job moves the version, which is a real conflict: the dialog sends
  `item_status`.
- **Package settings tabs:** Product (Category edit — the category is saved before its makes are
  changed, so a refusal leaves the makes alone), Design Tracker Category / Tasks, Commission Report
  Category / Tasks + the template editor (`SourceFormatDialog`), PMO Task Category / Master,
  Critical PO Category / Items.
  - A save that also **renames** the record skips the check (see Gotchas).
  - Task / item dialogs that filled their form once now refill it on every open, so the form and
    the version sent always belong together.

---

## Not covered, and why (owner decisions 2026-09-29)

**Reverted by the owner** — records with child tables, or not worth it now:
- User edit
- Reminder Schedule
- Expense Type (frontend and its backend)
- Work Milestones / Work Headers (the whole Milestones Packages tab)

**Not guarded by design:**
- Creates and deletes.
- Drag-reorders (they change display order only).
- Attachment-only uploads.
- Push / FCM settings.
- File re-links written right after a create.
- The Critical PO **"Link Milestones"** dialog. Its trigger has been commented out since
  `f1787eb75`. ⚠️ Its endpoint syncs the **full** link set, so it needs a guard before it is
  switched back on.

**Rejected alternative — "Plan C", a central axios interceptor** that attaches the last version the
tab saw of each record. Rejected because background refreshes (tab focus, socket updates) give the
tab a **newer** version than the data the open dialog holds. The interceptor would send that newer
version, and a real conflict would be accepted silently. The version must come from the data the
form was filled from, which only the screen knows.

---

## ⚠️ The key limitation — records with child tables

**The check watches ONE timestamp: the parent record's `modified`.** It knows nothing about which
fields or child rows changed. For a record with child tables, JSON fields or background writers,
that produces three problems.

Examples used below:
- **Projects** — child tables `drive_links`, `customer_po_details`, `project_zones`,
  `project_work_header_entries`, `project_wp_category_makes`; JSON fields such as `project_scopes`.
- **Procurement Orders** — child tables `items`, `payment_terms`, `critical_po_tasks`.

### 1. False alarms

Any change anywhere in the record moves the parent version: another tab's child-table edit, a
server `set_value` (e.g. `api/po_adjustments/_payment_utils.py` writing PO `amount_paid`), module
controls toggling Projects flags. An unrelated save on another screen is then refused.

> Nitesh edits a project's drive links while Priyanka adds a Customer PO row. Priyanka saves first;
> Nitesh's save is refused, although the two edits never touched the same data.

### 2. Blind spots

Server code that changes child rows or fields **without moving the parent version** is invisible
to the check:
- Critical PO task links are inserted / deleted directly as PO child rows; the PO is never saved
  and its `modified` does not move (root `CLAUDE.md` → Domain Gotchas).
- The `invoice_qty` recompute on PO item rows.
- `services/ceo_hold/core.py` writes with `update_modified=False` (standing owner rule).

This is safe only while no screen sends those same fields or tables back in a save.

### 3. Whole-table replace

When a save sends a child table, Frappe **replaces the whole table**: a row missing from the sent
list is deleted. The check catches this only because the parent version moved. If the other change
came through a path that does not move it (point 2), those rows are lost silently.

### What this means

- **This method is NOT applied to big multi-part records:** Projects, Procurement Orders, Service
  Requests / Work Orders, Vendors.
- They need a **scoped check**: compare only the fields and child rows the save sends against the
  database (for example, the `payment_terms` rows the dialog started from), and refuse only when
  those differ. **Not built yet.**
- **Small records without child tables are a good fit.** All the money records and masters above
  qualify, and none of them has hidden background writes (checked: no `set_value` / `db_set` / raw
  `UPDATE` outside patches).

---

## Adding the guard to a new screen

First check the record against the limitation above: no child tables the screen sends, no
background writer that skips `modified`.

1. **The list or query must load `modified`.** Add it to `fields`. Without it `staleGuard` sends
   nothing and the save is unguarded.
2. **One line in the save payload:** `...staleGuard(record)` (or `...stale.guard()` with the hook).
3. **On error:** for a one-click action, `useWriteErrorMessage()` for the toast text, and on
   `isStaleRecordError` close and reload. For an edit form:

   ```tsx
   const stale = useStaleConflict({ doctype, record, open });
   await updateDoc(doctype, record.name, { ...changes, ...stale.guard() });
   if (await stale.handle(error, (latest, opened) => setForm(f => takeLatest(f, formFrom(opened), formFrom(latest))))) return;
   <StaleConflictBanner conflict={stale.conflict} />
   <Button disabled={stale.isSaveBlocked({ ...form, pickedFile }) || saving}>…</Button>   {/* check FIRST */}
   ```

   `formFrom(record)` is the pure record-to-form mapping the dialog already uses to fill itself. An
   attachment field uses `followAttachment` and clears the picked file. A dialog that loads its own
   record must load it with `frappe.client.get`, not the REST read (which drops null fields).
4. **A custom endpoint** that changes the record takes `expected_modified`, locks the row, and uses
   `is_stale` / `stale_reason` from `services/concurrent_edit.py`. Not sent = no check.

---

## Gotchas

- **`isSaveBlocked` goes first in the button's `disabled`** (see Frontend pieces). Fixed on all 22
  buttons 2026-10-01 after it locked Mark as Paid and the Commission template editor.
- **A dialog's own "nothing changed" check must apply only BEFORE a warning**:
  `(!stale.conflict && <unchanged-vs-opened>)`. It compares with the record as first opened, so
  after a warning it blocks the user's real choice (Asset Category could never be saved). After a
  warning `isSaveBlocked` already answers "unchanged since the latest".
- **A dialog on a live-refreshing page keeps the record AS OPENED** (Edit Product, Asset edit's
  `editBase`). Refilling on every re-fetch silently wipes the user's typing; refilling nothing but
  sending the re-fetched version would let the user's older form overwrite the newer save unseen.
  Snapshot at open, fill from it, send its version; a newer save then shows as the normal warning.

- **A rename skips the guard.** `rename_doc` / `update_document_title` can move the version, so a
  save that also renames sends no `modified` (`...(nameChanged ? {} : stale.guard())`).
- **A handler that writes the same record twice** must send the version returned by the first
  write on the second one. Sending the loaded version again refuses the user's own second write.
- **Asset Management `after_insert` bumps Asset Master.** Creating an assignment writes to the
  asset. Hence the assign order (asset first) and the user-profile reload after assign / unassign.
- **`frappe.db.set_value(..., update_modified=False)` is invisible.** The version does not move, so
  the check cannot see that write.
- **A dialog that refills its form from its record** (an effect on `[open, record]`) must return
  early while `stale.conflict` is set — the refusal re-fetches the record, and the refill would
  wipe what the user typed after the warning.
- **`changedFields` only reports fields the screen loaded.** A list query that fetched a few
  columns shows only those in the banner; child tables and JSON are never listed.
- **The REST read (`useFrappeGetDoc`, `/api/resource`) drops null fields.** A record loaded that way
  has no key for an empty field, so the banner cannot name a field that was empty when the dialog
  opened. Load the record with `frappe.client.get` (the hook and `QueueRowEditDialog` do).
- **Two saves in the same second fail as HTTP 500, not 417.** PostgreSQL refuses the second while it
  waits on the row lock (`SerializationFailure`). `isStaleRecordError` treats it as a conflict; a
  new error check must use `isStaleRecordError`, never `exc_type === "TimestampMismatchError"`.

---

## Behaviour changes users may notice without a conflict

- Error toasts show the server's actual reason instead of a generic one (`describeWriteError`).
- Four expense AlertDialogs stay open on an error instead of closing.
- Assign asset: one server call, both writes in one transaction.
- After a conflict, "Save again" stays disabled until the user changes something.
- Product category: the category is saved before its makes are changed.
- Task / item dialogs in the package settings tabs refill their form each time they open.
- Commission mutations no longer report an expected stale refusal to Sentry.

---

## Testing

- **Frontend (2026-10-01):** 4,559 unit tests pass, including `useStaleConflict.test.ts` (34 tests:
  `changedFields`, `takeLatest`, `resolveConflict`, `fetchStaleInfo`, `followAttachment`,
  `formSignature`, `isStaleRecordError`, `conflictHeadline`). `writeOffControl` sometimes times out
  near the 5 s limit — proven unrelated (same on the old code). Type check: 0 new errors on changed
  lines.
- **Server (2026-10-01, rolled back):** per-doctype regression 57 PASS / 0 FAIL; fulfil guard: right
  version → Paid, old version → refused, none → Paid.
- **Server (rolled back, localhost):** for every covered doctype — a save with the loaded version
  passes; a save after someone else saved is refused; a save without a version works as before.
  Bulk with a mixed batch: the fresh row is processed, the stale row is refused and left untouched.
  CEO approve checked the same way.
- **Residence check:** adds 0 violations (F5 117 / F2 219, identical on HEAD).
- **Browser, money screens (2026-09-29):** edit banner, approve toast, bulk "2 succeeded, 4 failed"
  — all as described above.
- **Browser, 2026-09-29, two real sessions (Nitesh / Priyanka):**
  - Help, PR tags, Design task, Products: refused with the banner naming Priyanka and the changed
    field; untouched fields filled in; typed fields kept; "Save again" saved both changes; a
    normal save with no conflict went through with no banner.
  - In-Flow: refused save keeps the typed amount; the table behind the dialog re-fetches to the
    other person's value; "Save again" saves and the table follows.
  - Design task (SWR list): the row behind the dialog shows the other person's change; the typed
    offset is kept.
  - Known gap 1 reproduced (a third viewer's table stays stale).
- **Browser, 2026-10-01, two real sessions (Nitesh / Priyanka), Payments-table NPE edit:** latest
  data refills every field; attachment follows the latest file (A1–A3); the invoice section follows
  the latest; Save again disabled until a change; the short banner; same-second saves → loser gets
  the banner (PUT 500), winner the success toast.
- **Browser, 2026-10-01, every guarded save point (two sessions):** 22 edit dialogs, 11 one-click
  actions, 2 bulk actions. No stale save overwrote anyone. Found and fixed the same day, each
  re-tested live: Save again stuck in PE / NPE Mark as Paid and the Commission template editor
  (check order); Asset Category never savable after a warning (own "unchanged" check); Edit
  Product on the product page silently reset by a live re-fetch (now a snapshot); Help Edit
  opened empty (pre-existing); Mark-as-Paid summary showed the old comment; banner field names
  now use the form's labels.
- **Live-refreshing screens (asset page assign / unassign, bulk CEO approve, bulk expense approve),
  2026-10-01, both paths:** (A) the other user's save arrives live → the screen shows it and the
  action runs on the latest (correctly accepted, nothing overwritten); (B) the live event is late or
  lost (simulated with a version-only change) → the action is refused naming who changed it; a
  mixed bulk batch reports "1 succeeded, 1 failed" with the reason on the stale row. Real changes
  made by the tests were reverted (an approved PO keeps today's `modified`).
- **⚠️ Testing hazard:** restoring a record by writing its values back does NOT undo what its save
  pushed elsewhere. Critical PO Items → Critical PO Tasks + PO link rows; PMO Task Master → every
  PMO Project Task; Commission task rename → tracker rows; Items billing category → PO / PR lines.
  Check the save hooks first and revert through a proper save or targeted update.
- **Not yet clicked through in a browser (before 2026-10-01):** Commission, PMO, Critical PO, Product Packages
  category, Assets (edit, assign / unassign), Invoices, Non Project Inflows. Server tests cover
  them.
- **Found while testing, pre-existing, not fixed:** the Help edit dialog opens with empty fields
  (it fills itself in `handleOpenChange`, which does not run when the parent opens it); a Help
  article's title cannot be changed (the record is named after its title).

To reproduce a conflict by hand: open the record in two windows (two logins for the "Name changed
this record" text, one login for the "another tab or window" text), save in one, then save in the
other.

---

## Test plan — try to break it (2026-10-01 review round)

> **Our tests proved the check works on the intended path. They didn't try edge cases:
> attachments, a second conflict, the queue screen, network failures, calls without the version,
> and two transactions at once. That's where all 8 bugs were. We now reproduce each one first,
> and every fix must turn its reproduction from failing to passing.**

### Why the earlier tests missed the review's findings

The tests above were written from the implementation's side — "does the check refuse a stale save
and let a fresh one through?" — and walked the intended path once. They proved the **mechanism**.
Every bug the manager's review found sat on a path they never took:

| # | Finding | What the earlier test did | What it never tried |
|---|---|---|---|
| 1 | Attachment wiped on "Save again" | The other person changed a **text** field | An **attachment** change |
| 2 | PR Tag dialog keeps stale values | The change landed **while** the dialog was open | The list refreshing **before** the dialog opens |
| 3 | Second conflict keeps an old value | **One** conflict per dialog | A **second** conflict on the same open dialog |
| 4 | Half-done asset assignment | Every step **succeeded** | Step 2 **failing** |
| 5 | Payment tier decided before the lock | Calls **with** each row's version | A call **without** it; an amount changed mid-click |
| 6 | Approvals queue not refreshed | Lists holding **one** doctype (In-Flow, Design) | An edit opened from the **mixed-doctype queue** |
| 7 | "Save again" stuck forever | Every request **succeeded** | A request **failing** |
| 8 | TDS deadlock | **One** DB connection, steps in sequence | **Two simultaneous** transactions on different code paths |
| 9–10 | Raw `fetch`; helpers in the wrong home | Residence check + "does normal behaviour change?" | The diff checked against the `CLAUDE.md` coding rules |

### The edge-case matrix — every guard / lock / sync change is tested across all rows

| Row | What to vary | Minimum cases |
|---|---|---|
| **A. Field kinds** | What the other person changes | text, number, select / link, **attachment**, child-table row, JSON |
| **B. Event order and count** | When their change lands, and how often | before the dialog opens (list refreshed first), while it is open, **twice** (two conflicts in a row) |
| **C. Entry points** | Where the edit / action is opened from | **every** screen that opens it (e.g. an expense from its own list **and** from the Approvals queue) |
| **D. Failure injection** | A step that does not succeed | each network call failing (latest-version fetch, message fetch, upload); the **second** write of a two-write action failing |
| **E. Callers** | Who calls the endpoint | the current screen (sends the version) **and** a caller without it (old cached JS bundle, direct API call) |
| **F. Concurrency** | Two transactions at the same moment | two **real** DB connections replaying each pair of paths that lock the same rows (e.g. TDS pay vs restate vs payment delete) |
| **G. Repo rules** | The diff against `CLAUDE.md` | Frappe calls only through `frappe-react-sdk`; API files under `api/<feature>/`; one owner per concept (ADR-0010); no private names imported across modules |

A gap is never marked "safe" without a test that tried to make it fail.

### The 8 reproductions — run BEFORE a fix (must reproduce) and AFTER it (must not)

Status on the current branch (2026-10-01): **all 8 reproduced before the fixes, and all 8 pass
after them** (finding 5's "pass" column predates the owner's later rule: the bulk path now refuses
a payment whose level changed — H3 — instead of moving it to CEO Pending). Data restored after every run;
server runs are rolled back. Chrome runs use two incognito sessions (Nitesh / Priyanka, logins only
with the owner's permission) and change the "other person's" field from the server, snapshotting
the record first and restoring it after (values, `modified`, `modified_by`).

⚠️ Rows 1 and 3 were written for the old keep-what-I-typed rule. Under today's latest-wins rule the
pass condition is: the form shows the latest data (the other person's proof / P2), and the user's
re-entered change saves on top of it.

| # | Matrix row | Reproduction | Today (bug) | Pass after the fix |
|---|---|---|---|---|
| 1 | A | Inflow with **no** attachment (e.g. `PAYIN-00072-19`): Nitesh opens Edit, changes the UTR; Priyanka attaches a proof; Nitesh saves → banner → Save again | Banner says "Inflow attachment: added", dialog never shows it, Save again sets it to **null** | The proof is shown after the conflict and **survives** Save again; the UTR is saved. Repeat on the other 5 attachment dialogs (NPE Update Invoice / Update Payment / Edit, PE Update Payment, Invoice Edit) |
| 2 | B | PR Header Packages: Priyanka changes "PA System"'s Tag Package; Nitesh's list refreshes (tab focus); Nitesh then opens the row's Edit and saves without changes | Form shows the **old** package; save goes through with **no banner** and reverts her change | The form opens with **her** package; nothing is reverted |
| 3 | B | Asset edit (snapshot dialog, e.g. `ASSET-THR-001`): Nitesh edits the name; Priyanka sets description P1 → conflict 1; Priyanka sets P2 → conflict 2; Save again | Form keeps **P1** at conflict 2; Save again saves **P1**, losing P2 | Form shows **P2** at conflict 2; the final record has Nitesh's name **and** P2 |
| 4 | D | Server, rolled back: assign = update the asset, then creating the Asset Management record fails | Asset shows an assignee with **0** assignment records | Nothing changes — both or neither |
| 5 | E | Server, rolled back: payment staged Requested at ₹20,000, raised to ₹60,000 after the list read, lead-approved **without** `expected_modified` | Finishes **Approved** at L1 | Goes to **CEO Pending** (tier from the amount read under the lock) |
| 6 | C | Approvals queue: an expense staged Requested; Nitesh opens Edit from the queue; Priyanka changes the amount; Nitesh saves → refused; closes; clicks Approve | Row stays at the old amount; Approve dialog says the old amount over details showing the new one; Approve **refused again** | The queue row shows the new amount right after the refusal; Approve works first time |
| 7 | D | Asset edit with the browser's latest-version request forced to fail; Priyanka changes the record; Nitesh saves, then Save again ×3 | **3 / 3** refused; the typing can only be lost | The second save goes through with the current version |
| 8 | F | Two real Postgres connections: pay locks challan then deductions; restate / payment delete writes the deduction then the challan | `deadlock detected` | No deadlock — the second path waits, then proceeds or is refused with a clear message |

Findings 9 and 10 are checked against the diff (matrix row G): no raw `fetch` for Frappe calls,
helpers moved to one owner with public names.

**Where the reproductions live:** the server ones are local scripts in `frappe-bench/sites/`
(`.repro_server.py` for 4 and 5, `.repro_deadlock.py` for 8, `.stale_full_test.py` for the
per-doctype regression), run with `bench --site localhost console` + `exec(open(...).read(),
globals())`. They are **not** in the repo. With the fixes, the pure parts become permanent
`vitest` tests (`resolveConflict` with a moving baseline, `followAttachment`) and the server parts
become rolled-back test modules, so the proof stays with the code.

### Regression suite — run after every fix, all must stay green

- Full `vitest` (baseline 2026-10-01 after the fixes: **4,559** tests pass).
- Type check filtered to changed lines (no new errors).
- `python3 scripts/residence_check.py` — **0 new** violations (baseline F5 117 / F2 219).
- Per-doctype server test, rolled back (baseline **57 PASS / 0 FAIL**): for every covered doctype,
  a save with the loaded version passes, a save after someone else saved is refused, a save
  without a version works as before.
- The Chrome happy-path checks from "Testing" above still behave the same (banner, toast, bulk
  summary, list re-fetch).
