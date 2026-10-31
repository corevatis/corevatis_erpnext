"""Custom Fields der Dokumentenmappe (Epic D) an ERPNext-Belegen.

- Project: ``cv_mappe_ampel`` (grün/gelb/rot) und ``cv_mappe_hinweis`` - schreibt der Kern
  (``corevatis.mappe.ampel.aktualisieren``), wenn Dateien oder Prüfungen sich ändern.
- Quotation und Sales Invoice: Übersteuerung der Pflichtprüfung (``cv_mappe_uebersteuert``,
  ``cv_mappe_grund``, ``cv_mappe_uebersteuert_von``) - gesichert vom Kern
  (``corevatis.mappe.pruefung.uebersteuerung_pruefen``: nur Büro/System Manager, Begründung).

Labels sind englische Quelltexte, die deutsche Oberfläche kommt aus ``translations/de.csv``.
Feldnamen und Ampelwerte sind Vertrag mit dem Kern (``corevatis.mappe.konstanten``).
"""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from corevatis_erpnext.angebot.felder import MODULE

PROJEKT_AMPEL = "cv_mappe_ampel"
PROJEKT_HINWEIS = "cv_mappe_hinweis"
UEBERSTEUERT = "cv_mappe_uebersteuert"
UEBERSTEUERT_GRUND = "cv_mappe_grund"
UEBERSTEUERT_VON = "cv_mappe_uebersteuert_von"
AMPEL_WERTE = ("grün", "gelb", "rot")


def projekt_felder() -> list[dict]:
	return [
		{
			"fieldname": "cv_section_mappe",
			"fieldtype": "Section Break",
			"label": "Document Folder",
			"insert_after": "cv_gewaehrleistung_bis",
			"collapsible": 0,
		},
		{
			"fieldname": PROJEKT_AMPEL,
			"fieldtype": "Select",
			"label": "Document Folder Status",
			"options": "\n" + "\n".join(AMPEL_WERTE),
			"read_only": 1,
			"no_copy": 1,
			"in_standard_filter": 1,
			"description": "Set by the document folder: red = a mandatory document is missing for the next step",
			"insert_after": "cv_section_mappe",
		},
		{
			"fieldname": PROJEKT_HINWEIS,
			"fieldtype": "Small Text",
			"label": "Document Folder Note",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": PROJEKT_AMPEL,
		},
	]


def _uebersteuerung(insert_after: str) -> list[dict]:
	return [
		{
			"fieldname": "cv_section_mappe",
			"fieldtype": "Section Break",
			"label": "Document Folder Check",
			"collapsible": 1,
			"insert_after": insert_after,
		},
		{
			"fieldname": UEBERSTEUERT,
			"fieldtype": "Check",
			"label": "Folder Check Overridden",
			"default": "0",
			"no_copy": 1,
			"description": "Office or System Manager only, with a reason; logged in Mappenpruefung",
			"insert_after": "cv_section_mappe",
		},
		{
			"fieldname": UEBERSTEUERT_GRUND,
			"fieldtype": "Small Text",
			"label": "Reason for Folder Override",
			"no_copy": 1,
			"depends_on": f"eval:doc.{UEBERSTEUERT}",
			"mandatory_depends_on": f"eval:doc.{UEBERSTEUERT}",
			"insert_after": UEBERSTEUERT,
		},
		{
			"fieldname": UEBERSTEUERT_VON,
			"fieldtype": "Link",
			"options": "User",
			"label": "Folder Override By",
			"read_only": 1,
			"no_copy": 1,
			"depends_on": f"eval:doc.{UEBERSTEUERT}",
			"insert_after": UEBERSTEUERT_GRUND,
		},
	]


def angebot_felder() -> list[dict]:
	# hinter den Angebotsfeldern aus 4.1/4.3 (cv_section_texte … cv_garantiehinweis)
	return _uebersteuerung("cv_garantiehinweis")


def rechnung_felder() -> list[dict]:
	# hinter der Übersteuerung der Abnahmesperre (6.2)
	return _uebersteuerung("cv_uebersteuert_von")


def ensure_mappe_felder() -> None:
	felder = {"Project": projekt_felder(), "Quotation": angebot_felder(), "Sales Invoice": rechnung_felder()}
	for liste in felder.values():
		for feld in liste:
			feld.setdefault("module", MODULE)
	create_custom_fields(felder, update=True)
	for doctype in felder:
		frappe.clear_cache(doctype=doctype)
