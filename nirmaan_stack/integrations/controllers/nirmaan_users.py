import frappe


def unassign_all_assets(email):
    """Return every asset this person holds to the unassigned pool.

    Asset assignments used to be Link fields, so they REFUSED to let a holder be
    removed -- that refusal was the thing making anyone chase the laptop. Once they
    became Data nothing refused any more, which would have left equipment assigned to
    someone who no longer exists. Releasing it here restores the outcome the block used
    to force, without the block.

    Mirrors the two existing unassign dialogs exactly: delete the `Asset Management`
    row and clear `Asset Master.current_assignee`. The delete is what writes the
    `Deleted Document` tombstone carrying asset + holder + assigned_on, so custody
    history survives the release -- do NOT switch this to a raw SQL delete, which would
    skip both the tombstone and the controller that clears the master.

    Returns the number of assets released.
    """
    released = 0

    for name in frappe.get_all("Asset Management", filters={"asset_assigned_to": email}, pluck="name"):
        # delete_doc fires asset_management.on_trash, which clears current_assignee
        # on the master -- so the asset is genuinely free, not just unlinked.
        frappe.delete_doc("Asset Management", name, ignore_permissions=True)
        released += 1

    # Belt and braces: an Asset Master can point at someone with no assignment row
    # (a legacy or hand-edited record). Those would otherwise stay stuck on a deleted
    # user with nothing left to find them.
    for asset in frappe.get_all("Asset Master", filters={"current_assignee": email}, pluck="name"):
        frappe.db.set_value("Asset Master", asset, "current_assignee", None)
        released += 1

    return released


def on_trash(doc, method):
    """
    Release held assets, then delete associated User Permissions, Notifications,
    and the User doctype when a Nirmaan Users record is deleted.

    Handles edge cases where associated records may not exist.
    """
    email = doc.name

    # Release assets FIRST -- it is the only step with a physical counterpart, and a
    # later failure must not leave equipment assigned to someone being removed.
    try:
        unassign_all_assets(email)
    except Exception as e:
        frappe.log_error(
            "Nirmaan Users Delete",
            f"Failed to unassign assets for {email}: {str(e)}",
        )

    # Delete User Permissions (safe even if none exist)
    try:
        frappe.db.delete("User Permission", {"user": ("=", email)})
    except Exception as e:
        frappe.log_error(
            "Nirmaan Users Delete",
            f"Failed to delete User Permissions for {email}: {str(e)}",
        )

    # Delete Nirmaan Notifications (safe even if none exist)
    try:
        frappe.db.delete("Nirmaan Notifications", {"recipient": ("=", email)})
        frappe.db.delete("Nirmaan Notifications", {"sender": ("=", email)})
    except Exception as e:
        frappe.log_error(
            "Nirmaan Users Delete",
            f"Failed to delete Notifications for {email}: {str(e)}",
        )

    # Delete Frappe User - with existence check
    try:
        if frappe.db.exists("User", email):
            user = frappe.get_doc("User", email)
            user.delete()
        else:
            # Log for debugging - User doesn't exist but Nirmaan Users did
            frappe.log_error(
                "Nirmaan Users Sync Warning",
                f"Frappe User {email} does not exist but Nirmaan Users did. "
                    "This indicates a sync issue between User and Nirmaan Users doctypes.",
            )
    except Exception as e:
        frappe.log_error(
            "Nirmaan Users Delete",
            f"Failed to delete Frappe User {email}: {str(e)}",
        )


def after_rename(doc, method, old_name, new_name, merge):
    """
    Update email field after document rename.
    Called when Nirmaan Users document is renamed (email change).
    """
    try:
        # Update the email field to match the new name (primary key)
        frappe.db.set_value("Nirmaan Users", new_name, "email", new_name, update_modified=False)
    except Exception as e:
        frappe.log_error(
            "Nirmaan Users Rename",
            f"Failed to update email after rename from {old_name} to {new_name}: {str(e)}",
        )
