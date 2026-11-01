"""Handwerker#105: Maschinen-, Geräte- und Fahrtkosten zählen nach § 35a Abs. 3 EStG zu den
Arbeitskosten (BMF-Schreiben vom 9.11.2016), nicht zum Material.

1. Feld ``Item.cv_maschinenkosten`` anlegen (``post_model_sync`` läuft vor ``after_migrate``).
2. Die im Katalog so gekennzeichneten Artikel (Spalte ``cv_maschinenkosten`` der Katalogpakete, z. B.
   MAT-BAGGER „Minibagger … (Gerätemiete)“) auf bestehenden Sites kennzeichnen, sofern es sie gibt.
   Der Katalog-Import überschreibt abweichende Artikel nur mit ``force``, deshalb hier.

Bestehende Angebote rechnet der Patch nicht nach: ihre gespeicherte Kalkulation kennt die
Maschinenkosten nicht. Erst eine neue Kalkulation (Angebot aus dem Aufmaß neu erzeugen) bringt sie
in den Lohnanteil; angenommene Aufträge bleiben, wie sie sind.
"""

import frappe

from corevatis_erpnext.katalog.importer import lese_csv, list_pakete, paket_dir
from corevatis_erpnext.katalog.item_felder import MASCHINENKOSTEN, ensure_item_fields


def execute():
	ensure_item_fields()
	frappe.clear_cache(doctype="Item")
	for paket in list_pakete():
		header, rows = lese_csv(paket_dir(paket) / "06_item.csv")
		if MASCHINENKOSTEN not in header:
			continue
		code, flag = header.index("item_code"), header.index(MASCHINENKOSTEN)
		for row in rows:
			if str(row[flag] or "").strip() != "1" or not frappe.db.exists("Item", row[code]):
				continue
			frappe.db.set_value("Item", row[code], MASCHINENKOSTEN, 1, update_modified=False)
			print(f"Maschinenkosten (§ 35a): Artikel {row[code]} gekennzeichnet")
