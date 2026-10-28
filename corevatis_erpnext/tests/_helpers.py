"""Gemeinsame Testdaten für die CRM-Tests (Benutzer, Leads)."""

import frappe


def ensure_role(role: str) -> None:
	if not frappe.db.exists("Role", role):
		frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def make_user(email: str, first_name: str, roles: list[str], last_name: str | None = None) -> str:
	for role in roles:
		ensure_role(role)
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": first_name,
				"last_name": last_name,
				"send_welcome_email": 0,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	if roles:
		user.add_roles(*roles)
	return email


def make_lead(**values) -> "frappe.Document":
	data = {
		"doctype": "Lead",
		"first_name": "Erika",
		"last_name": "Musterfrau",
		"email_id": f"erika.{frappe.generate_hash(length=8)}@example.com",
	}
	data.update(values)
	lead = frappe.get_doc(data)
	# Pflichtfelder aus 1.1 (Objekttyp, Symptome …) spielen für diese Tests keine Rolle
	lead.flags.ignore_mandatory = True
	lead.insert(ignore_permissions=True)
	return lead
