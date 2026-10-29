"""Idempotente Einrichtung von Prozess 1 (``after_install``/``after_migrate``)."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from corevatis_erpnext.anfrage.custom_fields import custom_fields
from corevatis_erpnext.anfrage.workflow import ensure_workflow

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
	# nach den Feldern: der Workflow braucht cv_status und die Rollen
	ensure_workflow()
	ensure_kennzahlen()


# Kennzahlen im Arbeitsbereich „Anfragen“ (Number Cards). Frappe synchronisiert Number Cards
# aus App-Modulen nicht (nur Workspaces); deshalb hier idempotent angelegt. Namen = Verweise
# in workspace/anfragen/anfragen.json.
KENNZAHLEN = (
	{
		"name": "Neue Anfragen (7 Tage)",
		"document_type": "Lead",
		"filters_json": '[["Lead","creation","Timespan","last 7 days",false]]',
	},
	{
		"name": "Offene Termine",
		"document_type": "Event",
		"filters_json": '[["Event","status","=","Open",false]]',
		"dynamic_filters_json": '[["Event","starts_on",">=","frappe.datetime.now_date()"]]',
	},
	{
		"name": "Angebote offen",
		"document_type": "Quotation",
		"filters_json": '[["Quotation","status","in",["Draft","Open","Replied","Partially Ordered"],false]]',
	},
)


def ensure_kennzahlen() -> None:
	for karte in KENNZAHLEN:
		if frappe.db.exists("Number Card", karte["name"]):
			continue
		frappe.get_doc(
			{
				"doctype": "Number Card",
				"label": karte["name"],
				"type": "Document Type",
				"function": "Count",
				"document_type": karte["document_type"],
				"filters_json": karte["filters_json"],
				"dynamic_filters_json": karte.get("dynamic_filters_json") or "[]",
				"is_public": 1,
				"is_standard": 0,
				"module": "Corevatis ERPNext",
				"show_percentage_stats": 0,
			}
		).insert(ignore_permissions=True)


# Rollen, die den Arbeitsbereich „Anfragen“ und die Startkachel sehen
ANFRAGEN_ROLLEN = ("Büro", "Meister", "System Manager")


def hat_anfragen_zugriff() -> bool:
	"""``add_to_apps_screen.has_permission``: Kachel „Anfragen“ nur für Büro, Meister, System Manager."""
	return bool(set(frappe.get_roles()) & set(ANFRAGEN_ROLLEN))
