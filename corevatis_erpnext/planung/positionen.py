"""Auftragspositionen mit Montageminuten und Material je Einheit (5.1).

Gemeinsame Grundlage für Arbeitspakete (Sollstunden), Materialbedarf und Nachkalkulation (P2.1).
Gerechnet wird nur Menge mal Wert je Einheit - die Kalkulation (Preise, Zuschläge) bleibt im Kern
(Architekturregel 7).

**Minuten je Einheit, eine Quelle (Handwerker#104):** zuerst aus der Kalkulation des angenommenen
Angebots (``planung.kalkulation``, ``Quotation.cv_kalkulation_json``, Zuordnung wie P2.1), sonst
aus der Stückliste. ``minuten_quelle`` je Position: ``kalkulation``, ``stueckliste``, ``einheit``
(Positionen in Stunden/Minuten) oder ``keine``. Das Material kommt immer aus der Stückliste (die
Kalkulation hält nur den Materialwert, keine Mengen).

Je Position:

- **Stückliste:** ``Sales Order Item.bom_no``, sonst die gebuchte, aktive Stückliste der Firma
  des Auftrags (Standard zuerst, dann die neueste) - wie der Angebotsgenerator (4.1).
- **Menge in Stücklisteneinheit:** ``stock_qty`` der Position (Lagereinheit des Artikels); weicht
  die Einheit der Stückliste (``BOM.uom``) ab, wird über die Umrechnung des Artikels umgerechnet.
- **Minuten je Einheit (Rückfall):** Summe ``time_in_mins`` der Arbeitsgänge ÷ ``BOM.quantity`` (bei
  „Kosten nach Stücklistenmenge“ ÷ ``batch_size``).
- **Material je Einheit:** aufgelöste Stückliste (``exploded_items``, auch Unterstücklisten),
  ``stock_qty`` ÷ ``BOM.quantity`` in der Lagereinheit des Materials.
- **Ohne Stückliste:** Positionen in Stunden (``h``) bzw. Minuten (``min``) - z. B.
  Regiestunden - zählen mit ihrer Menge als Sollzeit; alles andere bekommt keine Sollzeit und
  einen Hinweis.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

from corevatis_erpnext.planung.bauablauf import VERFAHREN_SONSTIGES, phase, phasen_rang
from corevatis_erpnext.planung.kalkulation import kalkulation_laden, zuordnen

EINHEITEN_STUNDEN = ("h", "Hour", "Stunde")
EINHEITEN_MINUTEN = ("min", "Minute")
QUELLE_KALKULATION, QUELLE_STUECKLISTE, QUELLE_EINHEIT, QUELLE_KEINE = (
	"kalkulation",
	"stueckliste",
	"einheit",
	"keine",
)


def _stueckliste(item_code: str, firma: str, bom_no: str | None):
	if bom_no and frappe.db.get_value("BOM", bom_no, "docstatus") == 1:
		return frappe.get_doc("BOM", bom_no)
	namen = frappe.get_all(
		"BOM",
		filters={"item": item_code, "company": firma, "docstatus": 1, "is_active": 1},
		order_by="is_default desc, creation desc",
		pluck="name",
		limit=1,
	)
	return frappe.get_doc("BOM", namen[0]) if namen else None


def _umrechnung(item_code: str, uom: str | None, stock_uom: str | None) -> float:
	"""Lagereinheiten je ``uom`` (UOM Conversion Detail des Artikels), 1 bei gleicher Einheit."""
	if not uom or uom == stock_uom:
		return 1.0
	faktor = frappe.db.get_value(
		"UOM Conversion Detail", {"parent": item_code, "parenttype": "Item", "uom": uom}, "conversion_factor"
	)
	return flt(faktor) or 1.0


def stueckliste_je_einheit(bom) -> dict:
	"""Minuten und Material je Stücklisteneinheit."""
	menge = flt(bom.quantity) or 1.0
	minuten = 0.0
	if bom.get("with_operations"):
		for op in bom.get("operations") or []:
			if op.get("set_cost_based_on_bom_qty"):
				minuten += flt(op.time_in_mins) / (flt(op.get("batch_size")) or 1.0)
			else:
				minuten += flt(op.time_in_mins) / menge
	zeilen = bom.get("exploded_items") or bom.get("items") or []
	material: dict[str, dict] = {}
	for z in zeilen:
		if not z.item_code:
			continue
		eintrag = material.setdefault(
			z.item_code, {"item_code": z.item_code, "stock_uom": z.stock_uom, "je_einheit": 0.0}
		)
		eintrag["je_einheit"] += flt(z.stock_qty) / menge
	return {"bom": bom.name, "uom": bom.get("uom"), "minuten": minuten, "material": list(material.values())}


def verfahren_je_artikel(codes: list[str]) -> dict[str, str]:
	if not codes or not frappe.db.has_column("Item", "cv_verfahren"):
		return {}
	return dict(
		frappe.get_all("Item", filters={"name": ["in", codes]}, fields=["name", "cv_verfahren"], as_list=True)
	)


def _text(wert) -> str:
	return " ".join(str(wert or "").split())


def positionen(auftrag, kalk: dict[str, list[dict]] | None = None) -> tuple[list[dict], list[str]]:
	"""Positionen des Auftrags mit Verfahren, Phase, Sollminuten und Material. Rückgabe ``(liste, hinweise)``.

	``kalk``: Kalkulation je Artikel (``planung.kalkulation.kalkulation_laden``); ohne Angabe wird sie
	hier gelesen. Je Position steht die zugeordnete Kalkulationsposition unter ``kalkulation``.
	"""
	hinweise: list[str] = []
	if kalk is None:
		kalk = kalkulation_laden(auftrag)[0]
	zeilen = [z for z in auftrag.items if z.item_code]
	je_artikel = verfahren_je_artikel([z.item_code for z in zeilen])
	zugeordnet = zuordnen([z.item_code for z in zeilen], kalk)
	ergebnis = []
	for z, k in zip(zeilen, zugeordnet, strict=True):
		verfahren = _text(z.get("cv_verfahren")) or _text(je_artikel.get(z.item_code)) or VERFAHREN_SONSTIGES
		stock_uom = z.get("stock_uom") or frappe.get_cached_value("Item", z.item_code, "stock_uom")
		menge = flt(z.get("stock_qty")) or flt(z.qty) * (flt(z.get("conversion_factor")) or 1.0)
		eintrag = {
			"pos": z.idx,
			"sales_order_item": z.name,
			"item_code": z.item_code,
			"item_name": z.item_name,
			"menge": flt(menge, 6),
			"einheit": stock_uom,
			"verfahren": verfahren,
			"phase": phase(z.item_code),
			"phasen_rang": phasen_rang(z.item_code),
			"raum": _text(z.get("cv_raum")) or None,
			"bauteil": _text(z.get("cv_bauteil")) or None,
			"aufmass_position": z.get("cv_aufmass_position") or None,
			"bom": None,
			"minuten_je_einheit": 0.0,
			"sollminuten": 0.0,
			"minuten_quelle": QUELLE_KEINE,
			"minuten_stueckliste": None,
			"kalkulation": k,
			"material": [],
		}
		bom = _stueckliste(z.item_code, auftrag.company, z.get("bom_no"))
		if bom:
			daten = stueckliste_je_einheit(bom)
			umrechnung = _umrechnung(z.item_code, daten["uom"], stock_uom)
			menge_bom = menge / umrechnung
			eintrag.update(
				bom=daten["bom"],
				minuten_je_einheit=flt(daten["minuten"], 6),
				sollminuten=flt(daten["minuten"] * menge_bom, 6),
				minuten_quelle=QUELLE_STUECKLISTE,
				# je Lagereinheit wie die Kalkulation, zum Vergleich in der Beschreibung
				minuten_stueckliste=flt(daten["minuten"] / umrechnung, 6),
				material=[{**m, "menge": flt(m["je_einheit"] * menge_bom, 9)} for m in daten["material"]],
			)
		elif stock_uom in EINHEITEN_STUNDEN:
			eintrag.update(
				minuten_je_einheit=60.0, sollminuten=flt(menge * 60, 6), minuten_quelle=QUELLE_EINHEIT
			)
		elif stock_uom in EINHEITEN_MINUTEN:
			eintrag.update(minuten_je_einheit=1.0, sollminuten=flt(menge, 6), minuten_quelle=QUELLE_EINHEIT)
		if k and k["minuten"]:
			# eingefrorene Kalkulation des Angebots (je Lagereinheit, wie die Engine sie rechnet)
			eintrag.update(
				minuten_je_einheit=flt(k["minuten"], 6),
				sollminuten=flt(k["minuten"] * menge, 6),
				minuten_quelle=QUELLE_KALKULATION,
			)
		if not bom and eintrag["minuten_quelle"] == QUELLE_KEINE:
			hinweise.append(
				_("Position {0} ({1}) hat keine Stückliste: keine Sollstunden, kein Material.").format(
					z.idx, z.item_code
				)
			)
		elif not bom and eintrag["minuten_quelle"] == QUELLE_KALKULATION:
			hinweise.append(
				_("Position {0} ({1}) hat keine Stückliste: kein Material.").format(z.idx, z.item_code)
			)
		ergebnis.append(eintrag)
	return ergebnis, hinweise
