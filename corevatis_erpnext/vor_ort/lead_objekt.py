"""Aufgabe 2.1: ``Lead.cv_objekt`` - Link auf das Objekt (Doctype ``Objekt`` aus ``corevatis``).

Ein Link-Feld verlangt, dass der Ziel-Doctype existiert (Frappe prüft ``options`` beim
Anlegen des Custom Fields). Das Feld entsteht deshalb nur, wenn ``corevatis`` auf der Site
installiert ist:

- ``after_install``/``after_migrate`` dieses Adapters (Kern schon da),
- ``after_app_install("corevatis")`` (Kern kommt nach dem Adapter),
- ``before_app_uninstall("corevatis")`` entfernt das Feld wieder (sonst zeigt der Lead auf
  einen Doctype, den es nicht mehr gibt).

Ohne Kern (z. B. CI-Bench dieses Repos) passiert nichts. Feldname und Ziel sind Vertrag mit
``corevatis`` (dort geprüft: ``tests/test_erpnext_contract.py``).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "Corevatis ERPNext"
CORE_APP = "corevatis"
OBJEKT_DOCTYPE = "Objekt"
FIELDNAME = "cv_objekt"


def lead_objekt_field() -> dict:
	return {
		"fieldname": FIELDNAME,
		"fieldtype": "Link",
		"options": OBJEKT_DOCTYPE,
		"label": "Property Record",
		"description": "Property (building) of this inquiry; created for the on-site appointment",
		"in_standard_filter": 1,
		"search_index": 1,
		# im Abschnitt „Anfrage“ direkt unter der freien Objektadresse aus 1.1
		"insert_after": "cv_objekt_adresse",
		"module": MODULE,
	}


def kern_vorhanden() -> bool:
	return bool(frappe.db.exists("DocType", OBJEKT_DOCTYPE))


def ensure_lead_objekt_field() -> bool:
	"""Feld anlegen bzw. aktualisieren, wenn ``Objekt`` existiert. Rückgabe: Feld vorhanden."""
	if not kern_vorhanden():
		return False
	create_custom_fields({"Lead": [lead_objekt_field()]}, update=True)
	return True


def remove_lead_objekt_field() -> None:
	name = frappe.db.get_value("Custom Field", {"dt": "Lead", "fieldname": FIELDNAME})
	if name:
		frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)


def after_app_install(app_name: str) -> None:
	if app_name == CORE_APP:
		ensure_lead_objekt_field()


def before_app_uninstall(app_name: str) -> None:
	if app_name == CORE_APP:
		remove_lead_objekt_field()


def objekt_an_lead(doc, method=None) -> None:
	"""``doc_events`` Objekt (``after_insert``/``on_update``): Objekt am Lead eintragen.

	Befund mvp-test: Ein im Desk für einen Lead angelegtes Objekt (``kunde_doctype`` Lead)
	stand nicht am Lead (``cv_objekt`` leer), obwohl das Feld „wird zum Vor-Ort-Termin
	angelegt“ verspricht. Gesetzt wird nur ein leeres Feld; ein Lead mit anderem Objekt
	bleibt unverändert.
	"""
	if doc.get("kunde_doctype") != "Lead" or not doc.get("kunde"):
		return
	if not frappe.db.has_column("Lead", FIELDNAME) or not frappe.db.exists("Lead", doc.kunde):
		return
	if not frappe.db.get_value("Lead", doc.kunde, FIELDNAME):
		frappe.db.set_value("Lead", doc.kunde, FIELDNAME, doc.name)
