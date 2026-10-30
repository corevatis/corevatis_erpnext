"""Custom Fields der Abnahme (6.1/6.2) an ``Task``, ``Project`` und ``Sales Invoice``.

- Task: ``cv_abnahmeprotokoll`` und ``cv_mangel_zeile`` (Herkunft des Mangel-Tasks, Idempotenz).
- Project: Abnahmeprotokoll, Ergebnis, abgenommen am, Vertragsgrundlage, Gewährleistung (Jahre,
  bis - berechnet) - gesetzt beim Abschluss des Protokolls.
- Sales Invoice: ``cv_abschlag`` (Abschlagsrechnung, Vorgabe 0 - ANNAHME, 7.1 übernimmt),
  ``cv_abnahme_uebersteuert`` + Begründung + ``cv_uebersteuert_von`` (nur System Manager).

Labels sind englische Quelltexte, die deutsche Oberfläche kommt aus ``translations/de.csv``.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from corevatis_erpnext.angebot.felder import MODULE


def task_felder() -> list[dict]:
	return [
		{
			"fieldname": "cv_abnahmeprotokoll",
			"fieldtype": "Data",
			"label": "Acceptance Protocol",
			"read_only": 1,
			"no_copy": 1,
			"search_index": 1,
			"in_standard_filter": 1,
			"insert_after": "cv_positionen",
		},
		{
			"fieldname": "cv_mangel_zeile",
			"fieldtype": "Data",
			"label": "Defect Line",
			"read_only": 1,
			"no_copy": 1,
			"hidden": 1,
			"insert_after": "cv_abnahmeprotokoll",
		},
	]


def projekt_felder() -> list[dict]:
	return [
		{
			"fieldname": "cv_section_abnahme",
			"fieldtype": "Section Break",
			"label": "Acceptance and Warranty",
			"insert_after": "cv_aufmass",
			"collapsible": 0,
		},
		{
			"fieldname": "cv_abnahmeprotokoll",
			"fieldtype": "Data",
			"label": "Acceptance Protocol",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "cv_section_abnahme",
		},
		{
			"fieldname": "cv_abnahme_ergebnis",
			"fieldtype": "Data",
			"label": "Acceptance Result",
			"read_only": 1,
			"no_copy": 1,
			"in_standard_filter": 1,
			"insert_after": "cv_abnahmeprotokoll",
		},
		{
			"fieldname": "cv_abgenommen_am",
			"fieldtype": "Date",
			"label": "Accepted On",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "cv_abnahme_ergebnis",
		},
		{
			"fieldname": "cv_column_abnahme",
			"fieldtype": "Column Break",
			"insert_after": "cv_abgenommen_am",
		},
		{
			"fieldname": "cv_vertragsgrundlage",
			"fieldtype": "Select",
			"label": "Contract Basis",
			"options": "\nBGB\nVOB/B",
			"description": "Empty: default from the acceptance settings",
			"no_copy": 1,
			"insert_after": "cv_column_abnahme",
		},
		{
			"fieldname": "cv_gewaehrleistung_jahre",
			"fieldtype": "Int",
			"label": "Warranty (Years)",
			"non_negative": 1,
			"no_copy": 1,
			"description": "Empty: from the contract basis (BGB 5, VOB/B 4 years)",
			"insert_after": "cv_vertragsgrundlage",
		},
		{
			"fieldname": "cv_gewaehrleistung_bis",
			"fieldtype": "Date",
			"label": "Warranty Until",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "cv_gewaehrleistung_jahre",
		},
	]


def rechnung_felder() -> list[dict]:
	return [
		{
			"fieldname": "cv_abschlag",
			"fieldtype": "Check",
			"label": "Down Payment Invoice",
			"default": "0",
			"description": "Down payment invoices are allowed before the acceptance of the project",
			"insert_after": "is_return",
		},
		{
			"fieldname": "cv_abnahme_uebersteuert",
			"fieldtype": "Check",
			"label": "Invoice without Acceptance (Override)",
			"default": "0",
			"no_copy": 1,
			"description": "System Manager only, with a reason",
			"insert_after": "cv_abschlag",
		},
		{
			"fieldname": "cv_uebersteuerung_grund",
			"fieldtype": "Small Text",
			"label": "Reason for Override",
			"no_copy": 1,
			"depends_on": "eval:doc.cv_abnahme_uebersteuert",
			"insert_after": "cv_abnahme_uebersteuert",
		},
		{
			"fieldname": "cv_uebersteuert_von",
			"fieldtype": "Link",
			"options": "User",
			"label": "Overridden By",
			"read_only": 1,
			"no_copy": 1,
			"depends_on": "eval:doc.cv_abnahme_uebersteuert",
			"insert_after": "cv_uebersteuerung_grund",
		},
	]


def ensure_abnahme_felder() -> None:
	felder = {"Task": task_felder(), "Project": projekt_felder(), "Sales Invoice": rechnung_felder()}
	for liste in felder.values():
		for feld in liste:
			feld.setdefault("module", MODULE)
	create_custom_fields(felder, update=True)
	for doctype in felder:
		frappe.clear_cache(doctype=doctype)
