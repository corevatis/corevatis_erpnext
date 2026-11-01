"""Custom Fields der Abrechnung (7.1/7.2) an ``Sales Invoice`` und ``Sales Invoice Item``.

Alles, was der Rechnungsdruck (#50) braucht, liegt als Feld an der Rechnung: Art der Rechnung
(Abschlag ``cv_abschlag`` aus 6.2, Schlussrechnung ``cv_schlussrechnung``), der abgerechnete
Auftrag, Nummer und Prozent des Abschlags, kumulierte Werte, Leistungs- und Abschlagssummen der
Schlussrechnung, die Abschlagsübersicht (Tabelle ``cv_abschlaege``) und der Lohnanteil nach § 35a
EStG (netto, Steuer, brutto; je Position ``cv_lohnanteil``). Die Felder setzt nur der Server
(``read_only``); der Rechenweg steht in ``cv_abrechnung_json``.

Material und Lohn (Handwerker#105) an jeder Rechnung: je Position ``cv_materialanteil`` neben
``cv_lohnanteil`` (Material + Lohn = Betrag), am Kopf ``cv_material_netto``/``_brutto`` und
``cv_lohn_netto``/``_brutto`` (gleiche Namen wie an Angebot und Auftrag); gesetzt von
``abrechnung.lohn_material``. Der Lohn ist derselbe Wert wie der § 35a-Lohnanteil.

Mahnwesen und Zahlungseingang (7.5): ``Dunning.cv_mahnstufe`` (0 Zahlungserinnerung, 1/2 Mahnung),
``cv_zahlungsfrist`` (zahlbar bis, Druck), ``cv_projekt`` (Vorgang); ``Dunning Type.cv_mahnstufe``;
``Bank Transaction.cv_ignoriert``/``cv_notiz`` (Buchung ohne Rechnungsbezug) und ``cv_import`` (Datei).

Labels sind englische Quelltexte, die deutsche Oberfläche kommt aus ``translations/de.csv``.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from corevatis_erpnext.angebot.felder import MODULE

RECHNUNG_ABSCHLAG = "Rechnung Abschlag"
# Positionsarten (``Sales Invoice Item.cv_positionsart``)
ART_AUFTRAG, ART_MEHRMENGE, ART_NACHTRAG, ART_ABSCHLAG, ART_ABZUG = (
	"Auftragsposition",
	"Mehrmenge",
	"Nachtrag",
	"Abschlag",
	"Abzug Abschlag",
)
POSITIONSARTEN = (ART_AUFTRAG, ART_MEHRMENGE, ART_NACHTRAG, ART_ABSCHLAG, ART_ABZUG)


def _nur_server(feld: dict) -> dict:
	feld.setdefault("read_only", 1)
	feld.setdefault("no_copy", 1)
	return feld


def rechnung_felder() -> list[dict]:
	felder = [
		{
			"fieldname": "cv_section_abrechnung",
			"fieldtype": "Section Break",
			"label": "Down Payments and Final Invoice (Corevatis)",
			"insert_after": "cv_uebersteuert_von",
			"collapsible": 1,
			"collapsible_depends_on": "eval:doc.cv_auftrag",
		},
		{
			"fieldname": "cv_schlussrechnung",
			"fieldtype": "Check",
			"label": "Final Invoice",
			"default": "0",
			"description": "Final invoice of the order: actual quantities, supplements, down payments deducted",
			"insert_after": "cv_section_abrechnung",
		},
		{
			"fieldname": "cv_auftrag",
			"fieldtype": "Link",
			"options": "Sales Order",
			"label": "Settled Sales Order",
			"search_index": 1,
			"in_standard_filter": 1,
			"insert_after": "cv_schlussrechnung",
		},
		{
			"fieldname": "cv_abschlagsplan",
			"fieldtype": "Link",
			"options": "Abschlagsplan",
			"label": "Down Payment Schedule",
			"insert_after": "cv_auftrag",
		},
		{
			"fieldname": "cv_abschlag_nr",
			"fieldtype": "Int",
			"label": "Down Payment No.",
			"non_negative": 1,
			"insert_after": "cv_abschlagsplan",
		},
		{
			"fieldname": "cv_abschlag_prozent",
			"fieldtype": "Percent",
			"label": "Down Payment Percent",
			"insert_after": "cv_abschlag_nr",
		},
		{
			"fieldname": "cv_abschlag_zeile",
			"fieldtype": "Data",
			"label": "Schedule Line",
			"hidden": 1,
			"insert_after": "cv_abschlag_prozent",
		},
		{
			"fieldname": "cv_column_abrechnung",
			"fieldtype": "Column Break",
			"insert_after": "cv_abschlag_zeile",
		},
		{
			"fieldname": "cv_auftragssumme_netto",
			"fieldtype": "Currency",
			"label": "Order Total (Net)",
			"insert_after": "cv_column_abrechnung",
		},
		{
			"fieldname": "cv_kumuliert_prozent",
			"fieldtype": "Percent",
			"label": "Invoiced Including This (%)",
			"insert_after": "cv_auftragssumme_netto",
		},
		{
			"fieldname": "cv_kumuliert_netto",
			"fieldtype": "Currency",
			"label": "Invoiced Including This (Net)",
			"insert_after": "cv_kumuliert_prozent",
		},
		{
			"fieldname": "cv_leistung_netto",
			"fieldtype": "Currency",
			"label": "Services (Net)",
			"description": "Final invoice: order positions with actual quantities plus supplements, before deductions",
			"insert_after": "cv_kumuliert_netto",
		},
		{
			"fieldname": "cv_leistung_steuer",
			"fieldtype": "Currency",
			"label": "Services (Tax)",
			"insert_after": "cv_leistung_netto",
		},
		{
			"fieldname": "cv_leistung_brutto",
			"fieldtype": "Currency",
			"label": "Services (Gross)",
			"insert_after": "cv_leistung_steuer",
		},
		{
			"fieldname": "cv_abschlaege_netto",
			"fieldtype": "Currency",
			"label": "Down Payments Deducted (Net)",
			"insert_after": "cv_leistung_brutto",
		},
		{
			"fieldname": "cv_abschlaege_steuer",
			"fieldtype": "Currency",
			"label": "Down Payments Deducted (Tax)",
			"insert_after": "cv_abschlaege_netto",
		},
		{
			"fieldname": "cv_abschlaege_brutto",
			"fieldtype": "Currency",
			"label": "Down Payments Deducted (Gross)",
			"insert_after": "cv_abschlaege_steuer",
		},
		{
			"fieldname": "cv_section_abschlaege",
			"fieldtype": "Section Break",
			"label": "Down Payment Overview",
			"insert_after": "cv_abschlaege_brutto",
			"depends_on": "eval:doc.cv_schlussrechnung || doc.cv_abschlag",
		},
		{
			"fieldname": "cv_abschlaege",
			"fieldtype": "Table",
			"options": RECHNUNG_ABSCHLAG,
			"label": "Down Payment Invoices",
			"description": "Final invoice: deducted down payment invoices. Down payment invoice: earlier down payments.",
			"insert_after": "cv_section_abschlaege",
		},
		{
			"fieldname": "cv_section_lohnanteil",
			"fieldtype": "Section Break",
			"label": "Labour Share (§ 35a EStG)",
			"insert_after": "cv_abschlaege",
			"depends_on": "eval:doc.cv_schlussrechnung || doc.cv_abschlag || doc.cv_lohnanteil_netto",
		},
		{
			"fieldname": "cv_lohnanteil_netto",
			"fieldtype": "Currency",
			"label": "Labour Share (Net)",
			"description": "Labour costs contained in this invoice (household-related services, § 35a EStG)",
			"insert_after": "cv_section_lohnanteil",
		},
		{
			"fieldname": "cv_lohnanteil_steuer",
			"fieldtype": "Currency",
			"label": "Labour Share (Tax)",
			"insert_after": "cv_lohnanteil_netto",
		},
		{
			"fieldname": "cv_column_lohnanteil",
			"fieldtype": "Column Break",
			"insert_after": "cv_lohnanteil_steuer",
		},
		{
			"fieldname": "cv_lohnanteil_brutto",
			"fieldtype": "Currency",
			"label": "Labour Share (Gross)",
			"insert_after": "cv_column_lohnanteil",
		},
		{
			"fieldname": "cv_lohnanteil_prozent",
			"fieldtype": "Percent",
			"label": "Labour Share (%)",
			"insert_after": "cv_lohnanteil_brutto",
		},
		{
			"fieldname": "cv_abrechnung_json",
			"fieldtype": "Code",
			"label": "Invoice Calculation (JSON)",
			"options": "JSON",
			"hidden": 1,
			"print_hide": 1,
			"insert_after": "cv_lohnanteil_prozent",
		},
		# Material und Lohn (#105): an jeder Rechnung; Schlussrechnung: der Leistung vor den Abzügen
		{
			"fieldname": "cv_section_lohn_material",
			"fieldtype": "Section Break",
			"label": "Material and Labour",
			"insert_after": "cv_abrechnung_json",
		},
		{
			"fieldname": "cv_material_netto",
			"fieldtype": "Currency",
			"options": "currency",
			"label": "Material Costs (Net)",
			"description": "Material share of the invoiced services incl. its share of the surcharges",
			"print_hide": 1,
			"insert_after": "cv_section_lohn_material",
		},
		{
			"fieldname": "cv_material_brutto",
			"fieldtype": "Currency",
			"options": "currency",
			"label": "Material Costs (Gross)",
			"print_hide": 1,
			"insert_after": "cv_material_netto",
		},
		{
			"fieldname": "cv_column_lohn_material",
			"fieldtype": "Column Break",
			"insert_after": "cv_material_brutto",
		},
		{
			"fieldname": "cv_lohn_netto",
			"fieldtype": "Currency",
			"options": "currency",
			"label": "Labour Costs (Net)",
			"description": "Same value as the labour share (§ 35a EStG)",
			"print_hide": 1,
			"insert_after": "cv_column_lohn_material",
		},
		{
			"fieldname": "cv_lohn_brutto",
			"fieldtype": "Currency",
			"options": "currency",
			"label": "Labour Costs (Gross)",
			"print_hide": 1,
			"insert_after": "cv_lohn_netto",
		},
	]
	return [
		f if f["fieldtype"] in ("Section Break", "Column Break", "Table") else _nur_server(f) for f in felder
	]


def rechnung_position_felder() -> list[dict]:
	return [
		_nur_server(
			{
				"fieldname": "cv_positionsart",
				"fieldtype": "Select",
				"label": "Position Type",
				"options": "\n" + "\n".join(POSITIONSARTEN),
				"insert_after": "amount",
			}
		),
		_nur_server(
			{
				"fieldname": "cv_menge_auftrag",
				"fieldtype": "Float",
				"label": "Ordered Quantity",
				"description": "Quantity of the order line (final invoice)",
				"insert_after": "cv_positionsart",
			}
		),
		_nur_server(
			{
				"fieldname": "cv_lohnanteil",
				"fieldtype": "Currency",
				"label": "Labour Share (Net)",
				"insert_after": "cv_menge_auftrag",
			}
		),
		_nur_server(
			{
				"fieldname": "cv_materialanteil",
				"fieldtype": "Currency",
				"options": "currency",
				"label": "Material Share (Net)",
				"insert_after": "cv_lohnanteil",
			}
		),
		_nur_server(
			{
				"fieldname": "cv_bezug",
				"fieldtype": "Data",
				"label": "Related Order Line",
				"hidden": 1,
				"insert_after": "cv_materialanteil",
			}
		),
	]


def mahnwesen_felder() -> dict[str, list[dict]]:
	"""Felder an Dunning, Dunning Type und Bank Transaction (7.5)."""
	return {
		"Dunning": [
			_nur_server(
				{
					"fieldname": "cv_mahnstufe",
					"fieldtype": "Int",
					"label": "Dunning Level (Corevatis)",
					"description": "0 = payment reminder, 1 = 1st dunning notice, 2 = 2nd dunning notice",
					"insert_after": "dunning_type",
				}
			),
			_nur_server(
				{
					"fieldname": "cv_zahlungsfrist",
					"fieldtype": "Date",
					"label": "Pay By",
					"description": "Printed on the notice: posting date plus the deadline from the settings",
					"insert_after": "cv_mahnstufe",
				}
			),
			_nur_server(
				{
					"fieldname": "cv_projekt",
					"fieldtype": "Link",
					"options": "Project",
					"label": "Project",
					"insert_after": "cv_zahlungsfrist",
				}
			),
		],
		"Dunning Type": [
			{
				"fieldname": "cv_mahnstufe",
				"fieldtype": "Int",
				"label": "Dunning Level (Corevatis)",
				"description": "0 = payment reminder, 1 = 1st dunning notice, 2 = 2nd dunning notice; used by the dunning proposal",
				"insert_after": "is_default",
			},
		],
		"Bank Transaction": [
			{
				"fieldname": "cv_section_zuordnung",
				"fieldtype": "Section Break",
				"label": "Incoming Payment (Corevatis)",
				"insert_after": "party",
			},
			{
				"fieldname": "cv_ignoriert",
				"fieldtype": "Check",
				"label": "Ignored",
				"default": "0",
				"allow_on_submit": 1,
				"read_only": 1,
				"description": "No invoice behind this entry (set via “Ignore” in the incoming payments list)",
				"insert_after": "cv_section_zuordnung",
			},
			{
				"fieldname": "cv_notiz",
				"fieldtype": "Small Text",
				"label": "Note",
				"allow_on_submit": 1,
				"read_only": 1,
				"insert_after": "cv_ignoriert",
			},
			{
				"fieldname": "cv_import",
				"fieldtype": "Data",
				"label": "Imported From",
				"read_only": 1,
				"description": "Statement file and line the entry was imported from",
				"insert_after": "cv_notiz",
			},
		],
	}


def ensure_mahnwesen_felder() -> None:
	felder = {
		doctype: liste
		for doctype, liste in mahnwesen_felder().items()
		if frappe.db.exists("DocType", doctype)
	}
	for liste in felder.values():
		for feld in liste:
			feld.setdefault("module", MODULE)
	if felder:
		create_custom_fields(felder, update=True)
	for doctype in felder:
		frappe.clear_cache(doctype=doctype)


def ensure_abrechnung_felder() -> None:
	felder = {"Sales Invoice": rechnung_felder(), "Sales Invoice Item": rechnung_position_felder()}
	for liste in felder.values():
		for feld in liste:
			feld.setdefault("module", MODULE)
	create_custom_fields(felder, update=True)
	for doctype in felder:
		frappe.clear_cache(doctype=doctype)
