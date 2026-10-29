"""Custom Fields der Planung (5.1) an ``Task`` und ``Material Request``.

- Task: ``cv_auftrag`` (Link Sales Order, Idempotenz), ``cv_verfahren``, ``cv_sollstunden``
  (Sollstunden aus der Stückliste, unverändert - ``expected_time`` darf die Planung ändern),
  ``cv_positionen`` (JSON: Positionen des Arbeitspakets mit Menge, Raum, Minuten je Einheit).
- Material Request: ``cv_auftrag`` (Link Sales Order, Idempotenz, Listenansicht).

Labels sind englische Quelltexte, die deutsche Oberfläche kommt aus ``translations/de.csv``.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from corevatis_erpnext.angebot.felder import MODULE


def task_felder() -> list[dict]:
	return [
		{
			"fieldname": "cv_section_planung",
			"fieldtype": "Section Break",
			"label": "Work Package (Corevatis)",
			"insert_after": "description",
			"collapsible": 1,
		},
		{
			"fieldname": "cv_auftrag",
			"fieldtype": "Link",
			"options": "Sales Order",
			"label": "Sales Order",
			"read_only": 1,
			"no_copy": 1,
			"search_index": 1,
			"insert_after": "cv_section_planung",
		},
		{
			"fieldname": "cv_verfahren",
			"fieldtype": "Data",
			"label": "Remediation Method",
			"read_only": 1,
			"no_copy": 1,
			"in_standard_filter": 1,
			"insert_after": "cv_auftrag",
		},
		{
			"fieldname": "cv_column_planung",
			"fieldtype": "Column Break",
			"insert_after": "cv_verfahren",
		},
		{
			"fieldname": "cv_sollstunden",
			"fieldtype": "Float",
			"label": "Target Hours (BOM)",
			"precision": "2",
			"read_only": 1,
			"no_copy": 1,
			"description": "Assembly minutes per unit from the BOMs x quantity, summed",
			"insert_after": "cv_column_planung",
		},
		{
			"fieldname": "cv_positionen",
			"fieldtype": "JSON",
			"label": "Positions",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "cv_sollstunden",
		},
	]


def material_request_felder() -> list[dict]:
	return [
		{
			"fieldname": "cv_auftrag",
			"fieldtype": "Link",
			"options": "Sales Order",
			"label": "Sales Order",
			"read_only": 1,
			"no_copy": 1,
			"search_index": 1,
			"in_list_view": 0,
			"in_standard_filter": 1,
			"insert_after": "material_request_type",
		},
	]


def ensure_planung_felder() -> None:
	felder = {"Task": task_felder(), "Material Request": material_request_felder()}
	for liste in felder.values():
		for feld in liste:
			feld.setdefault("module", MODULE)
	create_custom_fields(felder, update=True)
	frappe.clear_cache(doctype="Task")
	frappe.clear_cache(doctype="Material Request")
