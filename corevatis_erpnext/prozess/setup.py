"""Einrichtung der Prozessoberfläche (``after_install``/``after_migrate``, idempotent).

1. Kennzahlen (Number Cards vom Typ Custom) und Blöcke (Custom HTML Block) der Arbeitsbereiche
   „Vorgänge“ und „Meine Baustellen“. Frappe synchronisiert beides nicht aus App-Modulen (nur
   Workspaces); die Workspaces selbst liegen als Standard-Workspaces unter
   ``corevatis_erpnext/corevatis_erpnext/workspace/``. Inhalt der Blöcke ist App-Code
   (``prozess/bloecke``): Abweichungen auf der Site überschreibt das nächste ``migrate``.
2. Lead-Formular aufräumen (Befunde mvp-test Nr. 20/21): ERPNext-Felder ohne Nutzen für den
   Handwerksbetrieb ausblenden, den Abschnitt „Organisation“ einklappen, den ERPNext-Status neben
   dem Anfragestatus ausblenden. Per Property Setter; ein im Desk (Formular anpassen) gesetzter
   eigener Wert bleibt stehen.
"""

from __future__ import annotations

from pathlib import Path

import frappe
from frappe.utils import cstr

MODULE = "Corevatis ERPNext"
BLOECKE = Path(__file__).resolve().parent / "bloecke"

ROLLEN_VORGAENGE = ("Büro", "Meister", "System Manager")
ROLLEN_BAUSTELLE = ("Monteur",)

KENNZAHLEN = (
	{
		"name": "Vorgänge offen",
		"method": "corevatis_erpnext.prozess.uebersicht.karte_offen",
		"color": "#2490EF",
	},
	{
		"name": "Vorgänge überfällig",
		"method": "corevatis_erpnext.prozess.uebersicht.karte_ueberfaellig",
		"color": "#E24C4C",
	},
	{
		"name": "Vorgänge: Betrieb am Zug",
		"method": "corevatis_erpnext.prozess.uebersicht.karte_am_zug",
		"color": "#ECAD4B",
	},
)

BLOCK_VORGAENGE = "Vorgänge Übersicht"
BLOCK_BAUSTELLEN = "Meine Baustellen heute"
BLOECKE_DEF = (
	{"name": BLOCK_VORGAENGE, "datei": "vorgaenge", "rollen": ROLLEN_VORGAENGE},
	{"name": BLOCK_BAUSTELLEN, "datei": "baustellen", "rollen": (*ROLLEN_BAUSTELLE, "System Manager")},
)

# Lead-Formular: (Feld, Eigenschaft, Wert, Typ). Grund je Zeile in docs/prozessleiste.md.
LEAD_FORMULAR = (
	# Nummernkreis ist fest (CRM-LEAD-.YYYY.-), das erste Feld des Formulars war die Serie
	("naming_series", "hidden", "1", "Check"),
	# Privatkunden: kein zweiter Vorname, keine Stellenbezeichnung, kein Geschlecht
	("middle_name", "hidden", "1", "Check"),
	("job_title", "hidden", "1", "Check"),
	("gender", "hidden", "1", "Check"),
	# ERPNext-Interessenten-Art und Anfragetyp: ersetzt durch Objekttyp/Quelle (1.1)
	("type", "hidden", "1", "Check"),
	("request_type", "hidden", "1", "Check"),
	# ERPNext-Status (Lead/Opportunity/Quotation/Converted …) neben dem Anfragestatus (1.4):
	# im Formular aus; ERPNext setzt ihn weiter selbst, Liste und Berichte behalten ihn
	("status", "hidden", "1", "Check"),
	# Firmendaten nur für Gewerbekunden: Abschnitt eingeklappt
	("organization_section", "collapsible", "1", "Check"),
	("organization_section", "label", "Firma (nur Gewerbekunden)", "Data"),
	("lead_owner", "label", "Verantwortlich", "Data"),
)


def ensure_prozess_setup() -> None:
	ensure_kennzahlen()
	ensure_bloecke()
	ensure_lead_formular()


def ensure_kennzahlen() -> None:
	for karte in KENNZAHLEN:
		werte = {
			"type": "Custom",
			"method": karte["method"],
			# Custom-Karten sieht nur, wer document_type lesen darf (number_card.has_permission)
			"document_type": "Lead",
			"is_public": 1,
			"is_standard": 0,
			"module": MODULE,
			"color": karte["color"],
			"show_percentage_stats": 0,
		}
		if frappe.db.exists("Number Card", karte["name"]):
			doc = frappe.get_doc("Number Card", karte["name"])
			if any(cstr(doc.get(k)) != cstr(v) for k, v in werte.items() if k != "color"):
				doc.update(werte)
				doc.save(ignore_permissions=True)
			continue
		frappe.get_doc(
			{"doctype": "Number Card", "name": karte["name"], "label": karte["name"], **werte}
		).insert(ignore_permissions=True)


def _datei(name: str, endung: str) -> str:
	return (BLOECKE / f"{name}.{endung}").read_text(encoding="utf-8")


def ensure_bloecke() -> None:
	for block in BLOECKE_DEF:
		rollen = sorted(r for r in block["rollen"] if frappe.db.exists("Role", r))
		werte = {
			"html": _datei(block["datei"], "html"),
			"script": _datei(block["datei"], "js"),
			"style": _datei(block["datei"], "css"),
			"private": 0,
		}
		if frappe.db.exists("Custom HTML Block", block["name"]):
			doc = frappe.get_doc("Custom HTML Block", block["name"])
			gleich = all((doc.get(k) or "") == v for k, v in werte.items() if k != "private")
			if gleich and not doc.private and sorted(r.role for r in doc.roles) == rollen:
				continue
			doc.update(werte)
		else:
			doc = frappe.new_doc("Custom HTML Block")
			doc.name = block["name"]
			doc.update(werte)
		doc.set("roles", [{"role": r} for r in rollen])
		doc.flags.ignore_permissions = True
		if doc.is_new():
			doc.insert(set_name=block["name"])
		else:
			doc.save()


def ensure_lead_formular() -> list[str]:
	"""Property Setter am Lead; Rückgabe: geänderte Einträge. Fremde Property Setter (z. B. aus
	„Formular anpassen“) bleiben unverändert."""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	meta = frappe.get_meta("Lead")
	geaendert = []
	for feld, eigenschaft, wert, typ in LEAD_FORMULAR:
		if not meta.get_field(feld):
			continue
		vorhanden = frappe.db.get_value(
			"Property Setter",
			{"doc_type": "Lead", "field_name": feld, "property": eigenschaft},
			["name", "value", "module"],
			as_dict=True,
		)
		if vorhanden and (vorhanden.module != MODULE or str(vorhanden.value) == wert):
			continue
		ps = make_property_setter("Lead", feld, eigenschaft, wert, typ, validate_fields_for_doctype=False)
		frappe.db.set_value("Property Setter", ps.name, "module", MODULE, update_modified=False)
		geaendert.append(f"{feld}.{eigenschaft}={wert}")
	if geaendert:
		frappe.clear_cache(doctype="Lead")
	return geaendert


# Startkachel „Meine Baustellen“ (hooks.add_to_apps_screen); „Vorgänge“ nutzt
# anfrage.setup.hat_anfragen_zugriff (Büro, Meister, System Manager)
def hat_baustellen_zugriff() -> bool:
	return "Monteur" in frappe.get_roles()
