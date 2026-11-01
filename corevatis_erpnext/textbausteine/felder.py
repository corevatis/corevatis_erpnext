"""Custom Fields an ``Terms and Conditions`` (P2.6): die ERPNext-Bausteine bekommen Art, Belegart,
Verfahren, Firma, Reihenfolge, Gültigkeit und Mahnstufe. Labels sind englische Quelltexte, die
deutsche Oberfläche kommt aus ``translations/de.csv``."""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from corevatis_erpnext.katalog.item_felder import VERFAHREN

MODULE = "Corevatis ERPNext"
DOCTYPE = "Terms and Conditions"

ART_VORBEMERKUNG = "Vorbemerkung"
ART_ZAHLUNG = "Zahlungsbedingungen"
ART_GEWAEHRLEISTUNG = "Gewährleistung"
ART_SCHLUSSTEXT = "Schlusstext"
ART_MAHNTEXT = "Mahntext"
ART_ANSCHREIBEN = "Anschreiben"
# Kundenportal (P2.7): Begleittext der Kundenseite
ART_KUNDENPORTAL = "Kundenportal"
ART_SONSTIGES = "Sonstiges"
ARTEN = (
	ART_VORBEMERKUNG,
	ART_ZAHLUNG,
	ART_GEWAEHRLEISTUNG,
	ART_SCHLUSSTEXT,
	ART_MAHNTEXT,
	ART_ANSCHREIBEN,
	ART_KUNDENPORTAL,
	ART_SONSTIGES,
)

BELEGART_ANGEBOT = "Angebot"
BELEGART_AUFTRAG = "Auftragsbestätigung"
BELEGART_RECHNUNG = "Rechnung"
BELEGART_MAHNUNG = "Mahnung"
BELEGART_ANSCHREIBEN = "Anschreiben"
BELEGARTEN = (BELEGART_ANGEBOT, BELEGART_AUFTRAG, BELEGART_RECHNUNG, BELEGART_MAHNUNG, BELEGART_ANSCHREIBEN)
BELEGART_JE_DOCTYPE = {
	"Quotation": BELEGART_ANGEBOT,
	"Sales Order": BELEGART_AUFTRAG,
	"Sales Invoice": BELEGART_RECHNUNG,
	"Dunning": BELEGART_MAHNUNG,
	"Customer": BELEGART_ANSCHREIBEN,
}

FELDER = (
	"cv_art",
	"cv_belegart",
	"cv_verfahren",
	"cv_firma",
	"cv_reihenfolge",
	"cv_gueltig_ab",
	"cv_gueltig_bis",
	"cv_mahnstufe",
)


def _select(*werte: str) -> str:
	return "\n".join(("", *werte))


def felder() -> list[dict]:
	return [
		{
			"fieldname": "cv_section_baustein",
			"fieldtype": "Section Break",
			"label": "Text Block (Corevatis)",
			"insert_after": "disabled",
		},
		{
			"fieldname": "cv_art",
			"fieldtype": "Select",
			"label": "Text Block Type",
			"options": _select(*ARTEN),
			"in_list_view": 1,
			"in_standard_filter": 1,
			"insert_after": "cv_section_baustein",
		},
		{
			"fieldname": "cv_belegart",
			"fieldtype": "Select",
			"label": "Document Type (Text Block)",
			"description": "Empty: for all document types",
			"options": _select(*BELEGARTEN),
			"in_list_view": 1,
			"in_standard_filter": 1,
			"insert_after": "cv_art",
		},
		{
			"fieldname": "cv_verfahren",
			"fieldtype": "Select",
			"label": "Remediation Method",
			"description": "Empty: for all methods",
			"options": _select(*VERFAHREN),
			"in_standard_filter": 1,
			"insert_after": "cv_belegart",
		},
		{
			"fieldname": "cv_mahnstufe",
			"fieldtype": "Select",
			"label": "Dunning Level",
			"options": _select("0", "1", "2"),
			"description": "0 payment reminder, 1 first dunning, 2 second dunning",
			"depends_on": 'eval:doc.cv_art=="Mahntext"',
			"mandatory_depends_on": 'eval:doc.cv_art=="Mahntext"',
			"insert_after": "cv_verfahren",
		},
		{
			"fieldname": "cv_column_baustein",
			"fieldtype": "Column Break",
			"insert_after": "cv_mahnstufe",
		},
		{
			"fieldname": "cv_firma",
			"fieldtype": "Link",
			"label": "Company",
			"options": "Company",
			"description": "Empty: for all companies",
			"in_standard_filter": 1,
			"insert_after": "cv_column_baustein",
		},
		{
			"fieldname": "cv_reihenfolge",
			"fieldtype": "Int",
			"label": "Sort Order",
			"description": "Lower comes first when several text blocks match",
			"insert_after": "cv_firma",
		},
		{
			"fieldname": "cv_gueltig_ab",
			"fieldtype": "Date",
			"label": "Valid From",
			"insert_after": "cv_reihenfolge",
		},
		{
			"fieldname": "cv_gueltig_bis",
			"fieldtype": "Date",
			"label": "Valid Until",
			"insert_after": "cv_gueltig_ab",
		},
		{
			"fieldname": "cv_platzhalter_hilfe",
			"fieldtype": "HTML",
			"label": "Placeholders",
			"insert_after": "terms",
		},
	]


def ensure_felder() -> None:
	if not frappe.db.exists("DocType", DOCTYPE):
		return
	create_custom_fields({DOCTYPE: felder()}, ignore_validate=True, update=True)
	frappe.clear_cache(doctype=DOCTYPE)
