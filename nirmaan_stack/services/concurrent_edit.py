"""Concurrent edit -- the one owner of "was this record changed since the user loaded it?".

A screen sends the `modified` it loaded; Frappe's own `check_if_latest` refuses a stale REST save.
Endpoints that act on what a user was looking at (bulk approve, CEO approve) take
`expected_modified` and check each row here, under the row lock the caller already holds.
Everything that says WHO changed a record uses `stale_message`, so every screen and every engine
words it the same way.

Reference: `.claude/context/domain/concurrent-edit.md`.
"""

import json

import frappe
from frappe import _
from frappe.utils import cstr, get_datetime

STALE_FALLBACK = "Someone else changed this record after you opened it. Refresh and try again."


def stale_message(modified_by: str | None, modified) -> str:
	"""Name the last saver and the time. The caller's own login = another tab or window of theirs."""
	if not modified_by:
		return STALE_FALLBACK
	at = f" at {get_datetime(modified).strftime('%d %b, %I:%M %p')}" if modified else ""
	if modified_by == frappe.session.user:
		return f"You already changed this record{at}, in another tab or window. Refresh and try again."
	return f"{display_name(modified_by)} changed this record{at}, after you opened it. Refresh and try again."


def display_name(user: str) -> str:
	"""Nirmaan Users first (the name the app shows), then User, then the login itself."""
	if user == "Administrator":
		return "Administrator"
	return (
		frappe.db.get_value("Nirmaan Users", user, "full_name")
		or frappe.db.get_value("User", user, "full_name")
		or user
	)


def parse_expected_modified(expected_modified) -> dict:
	"""`{name: modified}` as the caller's screen loaded them; `{}` when not sent (= no check)."""
	if not expected_modified:
		return {}
	if isinstance(expected_modified, str):
		try:
			expected_modified = json.loads(expected_modified)
		except json.JSONDecodeError:
			frappe.throw(_("expected_modified must be a JSON object of name -> modified."))
	if not isinstance(expected_modified, dict):
		frappe.throw(_("expected_modified must be a JSON object of name -> modified."))
	return {str(name): str(modified) for name, modified in expected_modified.items() if modified}


def is_stale(doc, expected: dict) -> bool:
	"""Was `doc` saved since the caller loaded it? Call it under the row lock, so the answer holds.

	`cstr(modified)` is the comparison Frappe's own `check_if_latest` makes, and the string the
	queue endpoints serialise -- so an untouched row always matches.
	"""
	loaded = expected.get(doc.name)
	return bool(loaded) and cstr(doc.modified) != loaded


def stale_reason(doc) -> str:
	"""The refusal reason for a stale row: who changed it and when."""
	return stale_message(doc.modified_by, doc.modified)
