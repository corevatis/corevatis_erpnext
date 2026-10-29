"""Aufgabe 4.1: Custom Fields an ``Quotation`` und ``Quotation Item`` (Angebot aus dem Aufmaß).

Gemeinsamer Vertrag für den Generator (4.1), das Angebotslayout (4.3) und die Annahme (4.4).
Die Feldnamen sind verbindlich und dürfen nicht umbenannt werden.

- Verweise auf Kern-Doctypes (``Aufmass``, ``Objekt``, ``Kalkulationsschema`` aus
  ``corevatis``) sind **Data** mit dem Namen des Datensatzes, keine Links: Das Schema des
  Angebots hängt so nicht davon ab, ob ``corevatis`` installiert ist (gleiche Begründung wie
  ``Item.cv_leistungsregel``, docs/2.2-leistungsregel.md, Entscheidung 1).
- ``cv_kalkulation_json`` hält die vollständige Herleitung der Engine
  (``corevatis.engine.quote``); nur lesen, nicht gedruckt.
- ``cv_einzelkosten`` = Herstellkosten je Einheit (Material + Lohn ohne Zuschläge) aus der
  Engine; nur lesen, nicht gedruckt (Nachkalkulation, Planung).
- ``cv_eventualposition``/``cv_alternativposition``: Kennzeichen für das Druckformat (4.3).
  ERPNext hat zusätzlich ``Quotation Item.is_alternative`` (Position zählt nicht zur Summe);
  der Generator setzt beide Kennzeichen nicht, das Büro pflegt sie im Desk.

Labels sind englische Quelltexte, die deutsche Oberfläche kommt aus ``translations/de.csv``.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "Corevatis ERPNext"

QUOTATION_FELDER = (
	"cv_titel",
	"cv_aufmass",
	"cv_objekt",
	"cv_kalkulationsschema",
	"cv_vorbemerkung",
	"cv_garantiehinweis",
	"cv_kalkulation_json",
	"cv_erzeugt_aus_aufmass",
)
QUOTATION_ITEM_FELDER = (
	"cv_raum",
	"cv_bauteil",
	"cv_verfahren",
	"cv_aufmass_position",
	"cv_mengen_herkunft",
	"cv_einzelkosten",
	"cv_eventualposition",
	"cv_alternativposition",
)


def quotation_fields() -> list[dict]:
	fields = [
		# Kopf: nach Firma/Gültigkeit, vor Währung und Preisliste
		{
			"fieldname": "cv_section_angebot",
			"fieldtype": "Section Break",
			"label": "Corevatis Offer",
			"insert_after": "amended_from",
		},
		{
			"fieldname": "cv_titel",
			"fieldtype": "Data",
			"label": "Offer Title",
			"description": "Heading of the offer, e.g. object and remediation method",
			"insert_after": "cv_section_angebot",
		},
		{
			"fieldname": "cv_objekt",
			"fieldtype": "Data",
			"label": "Remediation Property",
			"description": "Name of the Objekt (Corevatis)",
			"in_standard_filter": 1,
			"search_index": 1,
			"insert_after": "cv_titel",
		},
		{
			"fieldname": "cv_aufmass",
			"fieldtype": "Data",
			"label": "Source Measurement",
			"description": "Name of the Aufmaß (Corevatis) the offer was generated from",
			"in_standard_filter": 1,
			"search_index": 1,
			"print_hide": 1,
			"insert_after": "cv_objekt",
		},
		{
			"fieldname": "cv_column_angebot",
			"fieldtype": "Column Break",
			"insert_after": "cv_aufmass",
		},
		{
			"fieldname": "cv_kalkulationsschema",
			"fieldtype": "Data",
			"label": "Costing Scheme",
			"description": "Name of the Kalkulationsschema (Corevatis) used for the prices",
			"print_hide": 1,
			"insert_after": "cv_column_angebot",
		},
		{
			"fieldname": "cv_erzeugt_aus_aufmass",
			"fieldtype": "Check",
			"label": "Generated from Measurement",
			"read_only": 1,
			"no_copy": 1,
			"print_hide": 1,
			"insert_after": "cv_kalkulationsschema",
		},
		{
			"fieldname": "cv_section_texte",
			"fieldtype": "Section Break",
			"label": "Offer Texts",
			"collapsible": 1,
			"insert_after": "cv_erzeugt_aus_aufmass",
		},
		{
			"fieldname": "cv_vorbemerkung",
			"fieldtype": "Text Editor",
			"label": "Offer Introduction",
			"insert_after": "cv_section_texte",
		},
		{
			"fieldname": "cv_garantiehinweis",
			"fieldtype": "Small Text",
			"label": "Warranty Note",
			"insert_after": "cv_vorbemerkung",
		},
		# Weitere Informationen: Herleitung der Engine (lang, nur lesen)
		{
			"fieldname": "cv_section_kalkulation",
			"fieldtype": "Section Break",
			"label": "Costing (Corevatis)",
			"collapsible": 1,
			"insert_after": "supplier_quotation",
		},
		{
			"fieldname": "cv_kalkulation_json",
			"fieldtype": "Code",
			"options": "JSON",
			"label": "Costing (JSON)",
			"description": "Complete calculation of the quote engine: quantities, costs, surcharges, prices",
			"read_only": 1,
			"no_copy": 1,
			"print_hide": 1,
			"insert_after": "cv_section_kalkulation",
		},
	]
	for field in fields:
		field.setdefault("module", MODULE)
	return fields


def quotation_item_fields() -> list[dict]:
	fields = [
		{
			"fieldname": "cv_section_herkunft",
			"fieldtype": "Section Break",
			"label": "Origin (Corevatis)",
			"collapsible": 1,
			"insert_after": "page_break",
		},
		{
			"fieldname": "cv_raum",
			"fieldtype": "Data",
			"label": "Measured Room",
			"insert_after": "cv_section_herkunft",
		},
		{
			"fieldname": "cv_bauteil",
			"fieldtype": "Data",
			"label": "Building Component",
			"insert_after": "cv_raum",
		},
		{
			"fieldname": "cv_verfahren",
			"fieldtype": "Data",
			"label": "Remediation Method",
			"insert_after": "cv_bauteil",
		},
		{
			"fieldname": "cv_aufmass_position",
			"fieldtype": "Data",
			"label": "Measurement Row",
			"description": "Name of the Aufmaßposition row(s)",
			"print_hide": 1,
			"insert_after": "cv_verfahren",
		},
		{
			"fieldname": "cv_column_herkunft",
			"fieldtype": "Column Break",
			"insert_after": "cv_aufmass_position",
		},
		{
			"fieldname": "cv_mengen_herkunft",
			"fieldtype": "Small Text",
			"label": "Quantity Derivation",
			"description": "Quantity rule, formula and rounding",
			"print_hide": 1,
			"insert_after": "cv_column_herkunft",
		},
		{
			"fieldname": "cv_einzelkosten",
			"fieldtype": "Currency",
			"options": "currency",
			"label": "Unit Production Cost",
			"description": "Material and labour per unit without surcharges",
			"read_only": 1,
			"print_hide": 1,
			"insert_after": "cv_mengen_herkunft",
		},
		{
			"fieldname": "cv_eventualposition",
			"fieldtype": "Check",
			"label": "Optional Position",
			"insert_after": "cv_einzelkosten",
		},
		{
			"fieldname": "cv_alternativposition",
			"fieldtype": "Check",
			"label": "Alternative Position",
			"insert_after": "cv_eventualposition",
		},
	]
	for field in fields:
		field.setdefault("module", MODULE)
	return fields


def ensure_angebot_felder() -> None:
	create_custom_fields(
		{"Quotation": quotation_fields(), "Quotation Item": quotation_item_fields()},
		update=True,
	)
