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

**Feldrechte (Review Epic 4, G-1):** ``cv_kalkulation_json`` (Einkaufspreise, Montageminuten,
Zuschläge, Deckungsbeitrag) und ``cv_einzelkosten`` (Herstellkosten je Einheit) liegen auf
``PERMLEVEL_KALKULATION`` = 2. Ebene 1 genügt nicht: ERPNext gibt Sales User, Maintenance
User/Manager und Sales Manager an Quotation schon Leserecht auf Ebene 1. Ebene 2 lesen (und am
Beleg schreiben - der Generator legt Angebote mit den Rechten des Benutzers an) nur Büro und
Meister (``FELDRECHTE``, ``ensure_feldrechte``); Administrator sieht alles. Der Spiegel (4.4) übernimmt das
``permlevel`` an Sales Order / Sales Order Item. Das Druckformat druckt beide Felder nicht.

Labels sind englische Quelltexte, die deutsche Oberfläche kommt aus ``translations/de.csv``.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint

MODULE = "Corevatis ERPNext"
PERMLEVEL_KALKULATION = 2
# Doctype -> Rolle -> Rechte auf PERMLEVEL_KALKULATION. Schreiben: wer den Beleg mit eigenen
# Rechten anlegt (Generator am Angebot; Büro über „Erstellen → Auftrag“), sonst setzt Frappe die
# Werte beim Anlegen zurück (``validate_higher_perm_levels``). Meister liest Aufträge nur.
FELDRECHTE = {
	"Quotation": {"Büro": ("read", "write"), "Meister": ("read", "write")},
	"Sales Order": {"Büro": ("read", "write"), "Meister": ("read",)},
}

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
			"permlevel": PERMLEVEL_KALKULATION,
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
			"permlevel": PERMLEVEL_KALKULATION,
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
			"permlevel": PERMLEVEL_KALKULATION,
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
	ensure_feldrechte()


def _hat_grundrecht(doctype: str, role: str) -> bool:
	"""Rolle hat Ebene 0 am Doctype (Frappe verlangt Ebene 0 vor höheren Ebenen)."""
	if frappe.db.exists("Custom DocPerm", {"parent": doctype}):
		return bool(frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}))
	return bool(frappe.db.exists("DocPerm", {"parent": doctype, "role": role, "permlevel": 0}))


def feldrechte_fehlend() -> list[str]:
	"""Prüfpunkte für ``verify_site_template``: fehlende Rechte auf Ebene 2 (leer = in Ordnung)."""
	fehlend = []
	for doctype, rollen in FELDRECHTE.items():
		for role, rechte in rollen.items():
			if not frappe.db.exists("Role", role) or not _hat_grundrecht(doctype, role):
				continue
			zeile = frappe.db.get_value(
				"Custom DocPerm",
				{"parent": doctype, "role": role, "permlevel": PERMLEVEL_KALKULATION, "if_owner": 0},
				list(rechte),
				as_dict=True,
			)
			luecken = [r for r in rechte if not (zeile and cint(zeile.get(r)))]
			if luecken:
				fehlend.append(f"{role} auf {doctype} Ebene {PERMLEVEL_KALKULATION}: {', '.join(luecken)}")
	return fehlend


def ensure_feldrechte() -> list[str]:
	"""Büro und Meister lesen (und schreiben laut ``FELDRECHTE``) die Kalkulationsfelder (Ebene 2).

	Nur für Rollen mit Ebene 0 am Doctype - auf einer frischen Site also erst, nachdem das
	Site-Template die Rollenrechte gesetzt hat (es ruft diese Funktion danach auf). Vorher
	übernimmt ``setup_custom_perms`` die Standardrechte nach Custom DocPerm (wie der Role
	Permission Manager und das Site-Template). Rückgabe: Änderungen als Text.
	"""
	from frappe.permissions import setup_custom_perms

	geaendert = []
	for doctype, rollen in FELDRECHTE.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		for role, rechte in rollen.items():
			if not frappe.db.exists("Role", role) or not _hat_grundrecht(doctype, role):
				continue
			setup_custom_perms(doctype)
			filters = {"parent": doctype, "role": role, "permlevel": PERMLEVEL_KALKULATION, "if_owner": 0}
			name = frappe.db.get_value("Custom DocPerm", filters)
			if name:
				doc = frappe.get_doc("Custom DocPerm", name)
				luecken = [r for r in rechte if not cint(doc.get(r))]
				if not luecken:
					continue
				for recht in luecken:
					doc.set(recht, 1)
				doc.save(ignore_permissions=True)
			else:
				frappe.get_doc(
					{
						"doctype": "Custom DocPerm",
						"parenttype": "DocType",
						"parentfield": "permissions",
						**filters,
						**{recht: 1 for recht in rechte},
					}
				).insert(ignore_permissions=True)
			geaendert.append(f"{role} auf {doctype} Ebene {PERMLEVEL_KALKULATION}: {', '.join(rechte)}")
			frappe.clear_cache(doctype=doctype)
	return geaendert
