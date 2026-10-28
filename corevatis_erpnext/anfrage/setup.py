"""Idempotente Einrichtung von Prozess 1 (``after_install``/``after_migrate``)."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from corevatis_erpnext.anfrage.custom_fields import custom_fields

# Fachrollen aus dem Site-Template (docs/0.4-site-template.md). Workspace und Workflow
# brauchen sie auch auf Sites, auf denen das Template (noch) nicht lief; beide Apps
# legen sie idempotent an.
ROLES = ("Büro", "Meister")


def ensure_roles() -> None:
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)


def ensure_lead_fields() -> None:
	create_custom_fields(custom_fields(), update=True)


def ensure_anfrage_setup() -> None:
	ensure_roles()
	ensure_lead_fields()
