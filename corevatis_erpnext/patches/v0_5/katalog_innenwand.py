"""Handwerker#71: Katalog Abdichtung - Innenwände (IA-10) und positionsgenaue Regeln der Innenabdichtung.

Auf Sites mit eingespieltem Katalog (Artikel IA-05 mit Stückliste):

1. Fehlendes ergänzen (``importer.nachziehen``): Artikel IA-10 „Innenwand: Sockelabdichtung …“ mit
   Verkaufspreis und Stückliste (Firma der Stückliste von IA-05) sowie die Regeln aus
   ``10_leistungsregel_position.csv`` (IA-05 Außenwand, IA-10 Innenwand, IA-08 Boden). Vorhandenes bleibt.
2. Langtext von IA-05 präzisieren („Innenseite erdberührter Kelleraußenwände“) - nur, wenn er noch der
   alte Vorlagentext ist; ein im Desk geänderter Text bleibt.
3. Testregel zum Artikel IA-150 abschalten (``aktiv`` = 0, nichts gelöscht): IA-150 stammt aus der Abnahme
   2.2, steht nicht im Katalog und hat keine Stückliste. Ihre Regel („bis 1,50 m“, alle Wände) deckt
   dieselbe Leistung ab wie IA-05 (Außenwände) und IA-10 (Innenwände); mit Stückliste stünde die
   Dichtschlämme doppelt im Angebot, ohne erzeugt sie bei jedem Innenabdichtungs-Aufmaß die Warnung
   „keine gebuchte Stückliste“. Hat IA-150 inzwischen eine Stückliste, bleibt die Regel an.
"""

import frappe
from frappe.utils import strip_html_tags

from corevatis_erpnext.katalog import importer

PAKET = "abdichtung"
NEUE_ARTIKEL = ("IA-10",)
IA05 = "IA-05"
IA05_ALT = (
	"Mineralische Dichtschlämme in zwei Arbeitsgängen auf die Innenwand bis 1,50 m Höhe gegen Bodenfeuchte."
)
TESTARTIKEL = "IA-150"


def execute():
	firma = frappe.db.get_value(
		"BOM",
		{"item": IA05, "docstatus": 1, "is_active": 1},
		"company",
		order_by="is_default desc, creation desc",
	)
	if not firma:
		print("Katalog Abdichtung: IA-05 ohne Stückliste - Katalog nicht eingespielt, nichts nachzuziehen")
		return
	bericht = importer.nachziehen(firma, NEUE_ARTIKEL, PAKET)
	for zeile in bericht["changes"] + bericht["skipped"] + bericht["warnings"]:
		print(f"Katalog Abdichtung: {zeile}")
	_ia05_langtext()
	_testregel_abschalten()


def _ia05_langtext():
	neu = next(i["description"] for i in importer.lese_paket(PAKET)["Item"] if i["item_code"] == IA05)
	alt = strip_html_tags(frappe.db.get_value("Item", IA05, "description") or "").strip()
	if alt == IA05_ALT:
		frappe.db.set_value("Item", IA05, "description", neu, update_modified=False)
		print(f"Katalog Abdichtung: Langtext {IA05} präzisiert")


def _testregel_abschalten():
	if not frappe.db.exists("DocType", "Leistungsregel"):
		return
	if frappe.db.exists("BOM", {"item": TESTARTIKEL, "docstatus": 1, "is_active": 1}):
		return
	for name in frappe.get_all(
		"Leistungsregel", filters={"item_code": TESTARTIKEL, "aktiv": 1}, pluck="name"
	):
		frappe.db.set_value("Leistungsregel", name, "aktiv", 0)
		print(
			f"Katalog Abdichtung: Leistungsregel {name} ({TESTARTIKEL}, ohne Stückliste) abgeschaltet - "
			"IA-05 und IA-10 decken Außen- und Innenwände ab"
		)
