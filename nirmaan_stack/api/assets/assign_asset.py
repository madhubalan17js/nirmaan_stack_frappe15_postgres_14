"""Assign an asset to a user -- the asset's assignee AND its assignment record, in ONE transaction.

The screens used to make two browser calls: update `Asset Master.current_assignee`, then create
the `Asset Management` record. When the second call failed (network, permission, validation) the
first had already committed, leaving an asset that names an assignee with no assignment record
behind it -- nothing to unassign, no declaration to attach. Here both writes commit together or
not at all.

The stale guard is the same one the screens send everywhere else: `expected_modified` is the
version the screen showed, checked under the row lock, and a mismatch raises
`TimestampMismatchError` naming who changed the asset.
"""

import frappe
from frappe.utils import cstr

from nirmaan_stack.services.concurrent_edit import is_stale, stale_reason

ASSET_MASTER_DOCTYPE = "Asset Master"
ASSET_MANAGEMENT_DOCTYPE = "Asset Management"


@frappe.whitelist(methods=["POST"])
def assign_asset(
	asset: str,
	assigned_to: str,
	assigned_on: str,
	project: str | None = None,
	declaration_attachment: str | None = None,
	expected_modified: str | None = None,
) -> dict:
	doc = frappe.get_doc(ASSET_MASTER_DOCTYPE, asset, for_update=True)
	if is_stale(doc, {doc.name: cstr(expected_modified)} if expected_modified else {}):
		frappe.throw(stale_reason(doc), exc=frappe.TimestampMismatchError)

	doc.current_assignee = assigned_to
	if project:
		doc.project = project
	doc.save()

	assignment = frappe.get_doc({
		"doctype": ASSET_MANAGEMENT_DOCTYPE,
		"asset": asset,
		"asset_assigned_to": assigned_to,
		"asset_assigned_on": assigned_on,
		"asset_declaration_attachment": declaration_attachment or None,
	}).insert()

	frappe.db.commit()
	return {"asset": asset, "assignment": assignment.name}
