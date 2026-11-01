"""Custom Fields des Kundenportals (P2.7, Handwerker#91).

- Event: ``cv_kunde_bestaetigt`` (Zeitpunkt der Bestätigung durch den Kunden) und ``cv_kundenzugang``
  (über welchen Zugang) - schreibt nur der Server (``aktionen.termin_bestaetigen``).
- Quotation: ``cv_kunde_angenommen_am`` und ``cv_kunde_unterschrift`` (Datei der Unterschrift) - schreibt
  nur der Server bei der Annahme über das Portal.
- File: ``cv_kunde_sichtbar`` - das Büro gibt eine Datei der Mappe für den Kunden frei; Belege (Angebot,
  Auftragsbestätigung, Abnahmeprotokoll, Rechnung) sind ohne Haken sichtbar, Kundenuploads ebenfalls.

Labels sind englische Quelltexte, die deutsche Oberfläche kommt aus ``translations/de.csv``.
"""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "Corevatis ERPNext"
DOCTYPE = "Kundenzugang"

FELD_TERMIN_BESTAETIGT = "cv_kunde_bestaetigt"
FELD_TERMIN_ZUGANG = "cv_kundenzugang"
FELD_ANGEBOT_ANGENOMMEN = "cv_kunde_angenommen_am"
FELD_ANGEBOT_UNTERSCHRIFT = "cv_kunde_unterschrift"
FELD_DATEI_SICHTBAR = "cv_kunde_sichtbar"


def _insert_after(doctype: str, *kandidaten: str) -> str:
	meta = frappe.get_meta(doctype)
	for k in kandidaten:
		if meta.has_field(k):
			return k
	return meta.fields[-1].fieldname


def felder() -> dict[str, list[dict]]:
	return {
		"Event": [
			{
				"fieldname": "cv_section_kunde",
				"fieldtype": "Section Break",
				"label": "Customer Portal",
				"insert_after": "description",
				"collapsible": 1,
				"module": MODULE,
			},
			{
				"fieldname": FELD_TERMIN_BESTAETIGT,
				"fieldtype": "Datetime",
				"label": "Confirmed by Customer",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
				"insert_after": "cv_section_kunde",
				"description": "Set when the customer confirms the appointment in the customer portal",
				"module": MODULE,
			},
			{
				"fieldname": FELD_TERMIN_ZUGANG,
				"fieldtype": "Link",
				"options": DOCTYPE,
				"label": "Customer Access",
				"read_only": 1,
				"no_copy": 1,
				"insert_after": FELD_TERMIN_BESTAETIGT,
				"module": MODULE,
			},
		],
		"Quotation": [
			{
				"fieldname": "cv_section_kunde",
				"fieldtype": "Section Break",
				"label": "Customer Portal",
				"insert_after": _insert_after("Quotation", "cv_mappe_uebersteuert_von", "cv_lead", "terms"),
				"collapsible": 1,
				"module": MODULE,
			},
			{
				"fieldname": FELD_ANGEBOT_ANGENOMMEN,
				"fieldtype": "Datetime",
				"label": "Accepted by Customer",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1,
				"insert_after": "cv_section_kunde",
				"description": "Set when the customer accepts the quotation with signature in the customer portal",
				"module": MODULE,
			},
			{
				"fieldname": FELD_ANGEBOT_UNTERSCHRIFT,
				"fieldtype": "Attach",
				"label": "Customer Signature",
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 1,
				"insert_after": FELD_ANGEBOT_ANGENOMMEN,
				"module": MODULE,
			},
		],
		"File": [
			{
				"fieldname": FELD_DATEI_SICHTBAR,
				"fieldtype": "Check",
				"label": "Visible to Customer",
				"default": "0",
				"in_standard_filter": 1,
				"insert_after": _insert_after("File", "cv_gesperrt", "is_private"),
				"description": "Customer portal: the customer can download this file",
				"module": MODULE,
			},
		],
	}


def ensure_felder() -> None:
	create_custom_fields(felder(), ignore_validate=True, update=True)
	for doctype in ("Event", "Quotation", "File"):
		frappe.clear_cache(doctype=doctype)


def felder_da() -> bool:
	return frappe.get_meta("Event").has_field(FELD_TERMIN_BESTAETIGT)
