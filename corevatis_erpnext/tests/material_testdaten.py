"""Testdaten Materialentnahme (Aufgabe 3.11) - Anfangsbestand im Fahrzeuglager.

Kein Produktcode: nur für Tests und Testsites, Aufruf z. B.::

    bench --site <site> execute corevatis_erpnext.tests.material_testdaten.fahrzeug_auffuellen \\
        --kwargs '{"warehouse": "Fahrzeug - HST"}'

Füllt jedes Material auf die Zielmenge auf (Stock Entry „Material Receipt“, eingereicht, Preis
aus der Einkaufspreisliste ``Standard-Einkauf`` bzw. 1,00 €). Liegt der Bestand schon auf oder über
der Zielmenge, passiert nichts - mehrfach aufrufbar.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

# Zielbestand eines Fahrzeugs (Annahme: typische Bordausstattung Innenabdichtung/Injektion)
STANDARD_MENGEN = {
	"MAT-DICHTSCHL": 100,
	"MAT-SULFATEX": 50,
	"MAT-DICHTSPACHTEL": 40,
	"MAT-DICHTBAND": 30,
	"MAT-MANSCHETTE": 10,
	"MAT-PACKER": 200,
	"MAT-INJ-CREME": 20,
	"MAT-SP-PUTZ": 150,
}
EINKAUF = "Standard-Einkauf"


def _preis(item_code: str) -> float:
	rate = frappe.db.get_value(
		"Item Price", {"price_list": EINKAUF, "item_code": item_code}, "price_list_rate"
	)
	return flt(rate) or 1.0


def fahrzeug_auffuellen(
	warehouse: str = "Fahrzeug - HST", mengen: dict | None = None, commit: bool = True
) -> dict:
	"""Bestand im Lager auf ``mengen`` (Item Code → Zielmenge in Lagereinheit) auffüllen."""
	if isinstance(mengen, str):
		mengen = frappe.parse_json(mengen)
	mengen = mengen or STANDARD_MENGEN
	company = frappe.db.get_value("Warehouse", warehouse, "company")
	if not company:
		frappe.throw(f"Lager {warehouse} fehlt")
	zeilen = []
	for item_code, ziel in mengen.items():
		if not frappe.db.exists("Item", item_code):
			continue
		ist = flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"))
		if flt(ziel) > ist:
			zeilen.append(
				{
					"item_code": item_code,
					"qty": flt(ziel) - ist,
					"t_warehouse": warehouse,
					"basic_rate": _preis(item_code),
					"conversion_factor": 1,
				}
			)
	if not zeilen:
		return {"stock_entry": None, "positionen": 0}
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"purpose": "Material Receipt",
			"company": company,
			"to_warehouse": warehouse,
			"remarks": "Anfangsbestand Fahrzeuglager (Testdaten 3.11)",
			"items": zeilen,
		}
	)
	se.insert(ignore_permissions=True)
	se.submit()
	if commit:
		frappe.db.commit()
	return {"stock_entry": se.name, "positionen": len(zeilen)}
