"""Endpoint behind the "someone else changed this record" message on a refused save.

Returns the words AND the record's current `modified`, so a screen whose save was just refused can
retry with the current version even when loading the full record fails (review finding 7). The
wording lives in `services/concurrent_edit.py`; this module only serves it.
"""

import frappe
from frappe.utils import get_datetime

from nirmaan_stack.services.concurrent_edit import STALE_FALLBACK, display_name, stale_message


@frappe.whitelist()
def get_stale_message(doctype: str, name: str) -> dict:
	"""For a save of `doctype`/`name` that was just refused as stale: the one-line `message` (toasts),
	the record's current `modified`, and the LAST saver's display name `by`, the time `at` and
	`self` (the caller's own login = another tab). The record holds only the last saver, so a
	screen must not present `by` as the author of every change since it opened (3+ users)."""
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	row = frappe.db.get_value(doctype, name, ["modified_by", "modified"], as_dict=True)
	if not row:
		return {"message": STALE_FALLBACK, "modified": None}
	return {
		"message": stale_message(row.modified_by, row.modified),
		"modified": str(row.modified),
		"by": display_name(row.modified_by) if row.modified_by else None,
		"at": get_datetime(row.modified).strftime("%d %b, %I:%M %p") if row.modified else None,
		"self": row.modified_by == frappe.session.user,
	}
