"""Aufgabe 5.1: Materialbedarf (Material Request) aus den Stücklisten der Auftragspositionen.

Bedarf je Material = Σ über alle Positionen (Material je Stücklisteneinheit mal Positionsmenge in
Stücklisteneinheit, Lagereinheit des Materials) mal (1 + Verschnitt). Gerundet wird **auf**: ganzzahlige
Einheiten (``UOM.must_be_whole_number``, z. B. Stk) auf ganze Zahlen, sonst auf zwei Nachkommastellen.

Mit „Bestand berücksichtigen“ wird der voraussichtliche Bestand (``Bin.projected_qty``) des
Ziellagers abgezogen; Materialien ohne Restbedarf entfallen.

Beleg (Entwurf, Einstellung ``material_request_typ``):

- **Purchase** (Standard): Bestellvorschlag ins Hauptlager (``lager_haupt``, sonst Standardlager
  der Firma) - daraus macht der Einkauf über ERPNext „Erstellen → Bestellung“ die Bestellung.
- **Material Transfer**: Umlagerung vom Hauptlager ins Fahrzeuglager (``lager_fahrzeug``, sonst
  Lager „Fahrzeug“ der Firma).

Je Position ``project`` und ``sales_order`` (Verbindungen an Projekt und Auftrag), im Kopf
``cv_auftrag``. Bedarfsdatum = Projektstart, frühestens heute. Idempotent über ``cv_auftrag``
(nicht storniert): ein zweiter Lauf meldet den vorhandenen Beleg.
"""

from __future__ import annotations

import math

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, today

from corevatis_erpnext.planung.auftrag import PlanungFehler, auftrag_laden, pruefe_rolle
from corevatis_erpnext.planung.einstellungen import TYP_UMLAGERUNG, einstellungen
from corevatis_erpnext.planung.positionen import positionen


def bestehender_materialbedarf(auftrag: str) -> str | None:
	zeilen = frappe.db.sql(
		"""select name from `tabMaterial Request` where cv_auftrag=%s and docstatus < 2
		order by creation asc for update""",
		auftrag,
	)
	return zeilen[0][0] if zeilen else None


def aufrunden(menge: float, ganzzahlig: bool) -> float:
	if ganzzahlig:
		return float(math.ceil(flt(menge, 6) - 1e-9))
	return math.ceil(flt(menge, 6) * 100 - 1e-6) / 100


def bedarf(liste: list[dict], verschnitt_prozent: float) -> list[dict]:
	"""Aggregierter Bedarf je Material in Reihenfolge der ersten Nennung."""
	summe: dict[str, dict] = {}
	for p in liste:
		for m in p["material"]:
			eintrag = summe.setdefault(
				m["item_code"],
				{"item_code": m["item_code"], "uom": m["stock_uom"], "netto": 0.0, "positionen": []},
			)
			eintrag["netto"] += flt(m["menge"])
			if p["pos"] not in eintrag["positionen"]:
				eintrag["positionen"].append(p["pos"])
	faktor = 1 + flt(verschnitt_prozent) / 100
	for eintrag in summe.values():
		uom = eintrag["uom"] or frappe.get_cached_value("Item", eintrag["item_code"], "stock_uom")
		eintrag["uom"] = uom
		eintrag["ganzzahlig"] = bool(cint(frappe.get_cached_value("UOM", uom, "must_be_whole_number")))
		eintrag["netto"] = flt(eintrag["netto"], 6)
		eintrag["bedarf"] = aufrunden(eintrag["netto"] * faktor, eintrag["ganzzahlig"])
	return list(summe.values())


def _lager_der_firma(name: str | None, firma: str) -> str | None:
	if name and frappe.db.get_value("Warehouse", name, "company") == firma:
		return name
	return None


def lager(firma: str, e) -> tuple[str | None, str | None]:
	"""(Hauptlager, Fahrzeuglager) der Firma - Einstellung, sonst Standardlager bzw. „Fahrzeug“."""
	haupt = (
		_lager_der_firma(e.lager_haupt, firma)
		or _lager_der_firma(frappe.db.get_value("Company", firma, "default_warehouse"), firma)
		or frappe.db.get_value("Warehouse", {"warehouse_name": "Hauptlager", "company": firma, "is_group": 0})
	)
	fahrzeug = _lager_der_firma(e.lager_fahrzeug, firma) or frappe.db.get_value(
		"Warehouse", {"warehouse_name": "Fahrzeug", "company": firma, "is_group": 0}
	)
	return haupt, fahrzeug


def bestand(item_code: str, lager_name: str) -> float:
	return flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": lager_name}, "projected_qty"))


def materialbedarf_anlegen(auftrag, projekt: str, e=None) -> dict:
	"""Material Request (Entwurf) anlegen (ohne Rechte- und Sperrprüfung - Aufrufer: Hook, Whitelist)."""
	e = e or einstellungen()
	hinweise: list[str] = []
	vorhanden = bestehender_materialbedarf(auftrag.name)
	if vorhanden:
		hinweise.append(
			_("Zum Auftrag {0} gibt es bereits die Materialanforderung {1} - nichts angelegt.").format(
				auftrag.name, vorhanden
			)
		)
		return _ergebnis(auftrag, projekt, vorhanden, False, [], hinweise)

	liste, hinweise_pos = positionen(auftrag)
	hinweise += hinweise_pos
	zeilen = bedarf(liste, e.verschnitt_prozent)
	if not zeilen:
		hinweise.append(_("Die Positionen des Auftrags {0} haben kein Material.").format(auftrag.name))
		return _ergebnis(auftrag, projekt, None, False, [], hinweise)

	umlagerung = e.material_request_typ == TYP_UMLAGERUNG
	haupt, fahrzeug = lager(auftrag.company, e)
	ziel = fahrzeug if umlagerung else haupt
	if not haupt or (umlagerung and not fahrzeug):
		raise PlanungFehler(
			_("Kein {0} für die Firma {1} - bitte in den Planungs-Einstellungen festlegen.").format(
				_("Fahrzeuglager") if umlagerung and haupt else _("Hauptlager"), auftrag.company
			)
		)
	if umlagerung and haupt == fahrzeug:
		raise PlanungFehler(_("Für eine Umlagerung müssen Haupt- und Fahrzeuglager verschieden sein."))

	for z in zeilen:
		z["bestand"] = None
		z["menge"] = z["bedarf"]
		if e.bestand_beruecksichtigen:
			z["bestand"] = max(bestand(z["item_code"], ziel), 0.0)
			z["menge"] = aufrunden(max(z["bedarf"] - z["bestand"], 0.0), z["ganzzahlig"])
	offen = [z for z in zeilen if z["menge"] > 0]
	if not offen:
		hinweise.append(
			_("Der Bestand im Lager {0} deckt den Bedarf - keine Materialanforderung.").format(ziel)
		)
		return _ergebnis(auftrag, projekt, None, False, zeilen, hinweise)

	start = frappe.db.get_value("Project", projekt, "expected_start_date") or auftrag.transaction_date
	termin = max(getdate(start), getdate(today()))
	items = []
	for z in offen:
		item = frappe.get_cached_value("Item", z["item_code"], ["item_name", "description"], as_dict=True)
		items.append(
			{
				"item_code": z["item_code"],
				"item_name": item.item_name,
				"description": item.description or item.item_name,
				"qty": z["menge"],
				"uom": z["uom"],
				"stock_uom": z["uom"],
				"conversion_factor": 1,
				"schedule_date": termin,
				"warehouse": ziel,
				"from_warehouse": haupt if umlagerung else None,
				"project": projekt,
				"sales_order": auftrag.name,
			}
		)
	mr = frappe.get_doc(
		{
			"doctype": "Material Request",
			"material_request_type": e.material_request_typ,
			"company": auftrag.company,
			"transaction_date": today(),
			"schedule_date": termin,
			"set_warehouse": ziel,
			"set_from_warehouse": haupt if umlagerung else None,
			"cv_auftrag": auftrag.name,
			"items": items,
		}
	)
	mr.flags.ignore_permissions = True
	mr.insert()
	return _ergebnis(auftrag, projekt, mr.name, True, zeilen, hinweise)


def _ergebnis(auftrag, projekt, name: str | None, neu: bool, zeilen: list[dict], hinweise: list[str]) -> dict:
	kopf = (
		frappe.db.get_value(
			"Material Request", name, ["material_request_type", "set_warehouse", "docstatus"], as_dict=True
		)
		if name
		else None
	)
	return {
		"auftrag": auftrag.name if auftrag else None,
		"projekt": projekt,
		"neu": neu,
		"material_request": name,
		"typ": kopf.material_request_type if kopf else None,
		"lager": kopf.set_warehouse if kopf else None,
		"materialien": [
			{
				"item_code": z["item_code"],
				"uom": z["uom"],
				"netto": z["netto"],
				"bedarf": z["bedarf"],
				"bestand": z.get("bestand"),
				"menge": z.get("menge", z["bedarf"]),
				"positionen": z["positionen"],
			}
			for z in zeilen
		],
		"hinweise": hinweise,
	}


@frappe.whitelist(methods=["POST"])
def materialbedarf_erzeugen(sales_order: str | None = None, project: str | None = None) -> dict:
	"""Materialanforderung (Entwurf) aus den Stücklisten eines eingereichten Auftrags mit Projekt.

	Für Büro/Meister (Button „Materialbedarf erzeugen“ an Auftrag und Projekt). Ohne Projekt kein
	Fehler, nur ein Hinweis. Idempotent.
	"""
	pruefe_rolle()
	hinweise: list[str] = []
	auftrag, projekt = auftrag_laden(sales_order, project, hinweise)
	if not auftrag or not projekt:
		return _ergebnis(auftrag, projekt, None, False, [], hinweise)
	ergebnis = materialbedarf_anlegen(auftrag, projekt)
	ergebnis["hinweise"] = hinweise + ergebnis["hinweise"]
	return ergebnis
