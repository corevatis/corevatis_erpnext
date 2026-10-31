"""Custom Fields des DATEV-Exports (7.4).

- ``Sales Invoice`` und ``Payment Entry``: ``cv_datev_export`` (letzter Export, der den Beleg enthielt) und
  ``cv_datev_exportiert_am`` - nur der Server schreibt sie (``read_only``), per ``frappe.db.set_value``
  auch an gebuchten Belegen.
- ``DATEV Settings`` (erpnext_datev, je Firma): ``cv_debitor_start`` (erste Debitorennummer, Standard 10000)
  und ``cv_druckformat`` (Druckformat der Belegbilder). Nur wenn erpnext_datev installiert ist.

Labels sind englische Quelltexte, die deutsche Oberfläche kommt aus ``translations/de.csv``.
"""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from corevatis_erpnext.angebot.felder import MODULE

EXPORT_DOCTYPE = "DATEV Export"
SETTINGS_DOCTYPE = "DATEV Settings"
FELD_EXPORT = "cv_datev_export"
FELD_EXPORTIERT_AM = "cv_datev_exportiert_am"
DEBITOR_START_STANDARD = 10000


def _nur_server(feld: dict) -> dict:
	feld.setdefault("read_only", 1)
	feld.setdefault("no_copy", 1)
	return feld


def _markierung(insert_after: str) -> list[dict]:
	return [
		{
			"fieldname": "cv_section_datev",
			"fieldtype": "Section Break",
			"label": "DATEV (Corevatis)",
			"insert_after": insert_after,
			"collapsible": 1,
			"collapsible_depends_on": f"eval:doc.{FELD_EXPORT}",
		},
		_nur_server(
			{
				"fieldname": FELD_EXPORT,
				"fieldtype": "Link",
				"options": EXPORT_DOCTYPE,
				"label": "DATEV Export",
				"description": "Last DATEV export that contained this voucher (set by the export)",
				"search_index": 1,
				"in_standard_filter": 1,
				"insert_after": "cv_section_datev",
			}
		),
		_nur_server(
			{
				"fieldname": FELD_EXPORTIERT_AM,
				"fieldtype": "Datetime",
				"label": "Exported to DATEV On",
				"insert_after": FELD_EXPORT,
			}
		),
	]


def markierungs_felder() -> dict[str, list[dict]]:
	"""Markierung „exportiert am … in Export Nr. …“ an Rechnung und Zahlung."""
	return {
		"Sales Invoice": _markierung("cv_abrechnung_json"),
		"Payment Entry": _markierung("remarks"),
	}


def einstellungs_felder() -> list[dict]:
	"""Ergänzungen an ``DATEV Settings`` (erpnext_datev)."""
	return [
		{
			"fieldname": "cv_section_corevatis",
			"fieldtype": "Section Break",
			"label": "Monthly Export (Corevatis)",
			"insert_after": "opening_against_account_number",
		},
		{
			"fieldname": "cv_debitor_start",
			"fieldtype": "Int",
			"label": "First Debtor Number",
			"default": str(DEBITOR_START_STANDARD),
			"non_negative": 1,
			"description": "Debtor numbers are assigned per customer from this number on (account number length plus one digit; DATEV: 10000 to 69999)",
			"insert_after": "cv_section_corevatis",
		},
		{
			"fieldname": "cv_column_corevatis",
			"fieldtype": "Column Break",
			"insert_after": "cv_debitor_start",
		},
		{
			"fieldname": "cv_druckformat",
			"fieldtype": "Link",
			"options": "Print Format",
			"label": "Print Format for Voucher Images",
			"description": "Invoice PDF in the voucher image ZIP (empty: default print format of the invoice)",
			"insert_after": "cv_column_corevatis",
		},
	]


def ensure_datev_felder() -> None:
	"""``after_install``/``after_migrate`` (nach den Abrechnungsfeldern): Felder anlegen, idempotent."""
	felder = markierungs_felder()
	if frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		felder[SETTINGS_DOCTYPE] = einstellungs_felder()
	for liste in felder.values():
		for feld in liste:
			feld.setdefault("module", MODULE)
	create_custom_fields(felder, update=True)
	for doctype in felder:
		frappe.clear_cache(doctype=doctype)
