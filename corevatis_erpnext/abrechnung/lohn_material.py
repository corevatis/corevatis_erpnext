"""Lohn und Material getrennt ausweisen (Handwerker#105) an Angebot, Auftrag und Rechnung.

Jede Position zeigt ihren Material- und Lohnanteil (``cv_materialanteil``/``cv_lohnanteil``, netto),
der Kopf die Summen netto und brutto (``cv_material_netto``/``_brutto``, ``cv_lohn_netto``/``_brutto``);
Material + Lohn = Netto. Die Rechnung kommt aus dem Kern ``corevatis.engine.anteile`` - per Name, kein
Import über die Lizenzgrenze (wie ``abrechnung.kern``); ohne Kern bleiben die Felder unverändert.

Quelle je Zeile (die Engine nimmt die erste passende):

- **Angebot:** Kalkulation der Position aus ``cv_kalkulation_json`` (4.1, je Artikel), sonst
  Regiestunde (Artikel mit Verfahren „Sonstiges“ und Einheit Stunde: 100 % Lohn), sonst
  „Lohnanteil Standard (%)“. Verteilungsschlüssel aus der Einstellung „Lohnanteil mit Zuschlägen“.
  Artikel mit ``Item.cv_maschinenkosten`` (Maschinen-, Geräte-, Fahrtkosten) als eigene Position:
  100 % Lohn wie eine Regiestunde; in der Kalkulation zählt ``kalkulation.maschinen`` zum Lohn.

„Lohn“ heißt hier: Arbeitskosten nach § 35a Abs. 3 EStG - Arbeitslohn einschließlich Maschinen- und
Fahrtkosten (BMF-Schreiben vom 9.11.2016); Material einschließlich Verbrauchsmaterial ist nicht
begünstigt. Gemeinkosten und Gewinn werden mit der Einstellung anteilig verteilt (Standard).
- **Auftrag:** Spiegel der Angebotszeile (``quotation_item``) anteilig zum Betrag, sonst wie Angebot
  (``cv_kalkulation_json`` ist gespiegelt).
- **Rechnung:** Spiegel der Auftragszeile (``so_detail``) anteilig - Teilmengen anteilig. Abschlags-
  und Schlussrechnungen (``cv_auftrag``): Lohn je Zeile ist der § 35a-Lohnanteil, den die Abrechnung
  gesetzt hat (``cv_lohnanteil``, Server), Material = Rest; Abzugszeilen zählen nicht (Summen =
  Leistung). Bei anderen Rechnungen setzt der Hook auch den § 35a-Lohnanteil am Kopf
  (``cv_lohnanteil_netto`` …) aus denselben Werten.

Hooks: ``Quotation``/``Sales Order``/``Sales Invoice`` ``validate`` (Rechnung nach
``auftrag.rechnung_validieren``). ``nachtragen`` rechnet einen gespeicherten (auch freigegebenen)
Beleg nach - Patch ``v0_5.lohn_material`` für den Bestand.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import flt

from corevatis_erpnext.abrechnung import kern
from corevatis_erpnext.abrechnung.auftrag import steuersatz
from corevatis_erpnext.abrechnung.einstellungen import einstellungen
from corevatis_erpnext.abrechnung.felder import ART_ABZUG
from corevatis_erpnext.katalog.item_felder import MASCHINENKOSTEN
from corevatis_erpnext.planung.bauablauf import VERFAHREN_SONSTIGES
from corevatis_erpnext.planung.positionen import EINHEITEN_STUNDEN

KERN_ANTEILE = "corevatis.engine.anteile.aufteilen"
# Schlüssel der Eingaben (Vertrag mit ``corevatis.engine.anteile``, Vertragstest im Kern)
POSITION_SCHLUESSEL = (
	"id",
	"item_code",
	"betrag",
	"menge",
	"kalkulation",
	"regie",
	"lohnanteil",
	"bezug",
	"in_summe",
)
KALKULATION_SCHLUESSEL = ("material", "materialzuschlag", "lohn", "erschwernis", "einzelkosten", "maschinen")
BEZUG_SCHLUESSEL = ("betrag", "lohn")
OPTIONEN_SCHLUESSEL = ("mit_zuschlaegen", "lohnanteil_prozent_standard", "steuersatz", "netto", "rundung")

FELDER_POSITION = ("cv_materialanteil", "cv_lohnanteil")
FELDER_KOPF = ("cv_material_netto", "cv_material_brutto", "cv_lohn_netto", "cv_lohn_brutto")
DOCTYPES = {
	"Quotation": "Quotation Item",
	"Sales Order": "Sales Order Item",
	"Sales Invoice": "Sales Invoice Item",
}
LOHNANTEIL_KOPF = (
	"cv_lohnanteil_netto",
	"cv_lohnanteil_steuer",
	"cv_lohnanteil_brutto",
	"cv_lohnanteil_prozent",
)


def _engine():
	return kern._aufloesen(KERN_ANTEILE)


def felder_da(doctype: str) -> bool:
	kind = DOCTYPES.get(doctype)
	if not kind:
		return False
	kopf, zeile = frappe.get_meta(doctype), frappe.get_meta(kind)
	return all(kopf.has_field(f) for f in FELDER_KOPF) and all(zeile.has_field(f) for f in FELDER_POSITION)


# --- Eingaben ------------------------------------------------------------------------------------


def _kalkulationen(doc) -> dict[str, dict]:
	"""Kalkulation je Artikel aus ``cv_kalkulation_json`` (Angebot, gespiegelt am Auftrag)."""
	roh = doc.get("cv_kalkulation_json")
	if not roh:
		return {}
	try:
		daten = json.loads(roh) if isinstance(roh, str) else roh
	except ValueError:
		return {}
	if not isinstance(daten, dict) or not daten.get("ok"):
		return {}
	ergebnis = {}
	for p in daten.get("positionen") or []:
		k = p.get("kalkulation") if isinstance(p, dict) else None
		code = isinstance(p, dict) and (p.get("item_code") or p.get("id"))
		if not code or not isinstance(k, dict) or k.get("lohn") in (None, ""):
			continue
		try:
			ergebnis[code] = {
				key: str(flt(k[key], 6)) for key in KALKULATION_SCHLUESSEL if k.get(key) not in (None, "")
			}
		except (TypeError, ValueError):
			continue
	return ergebnis


def _maschinen_artikel(codes) -> set[str]:
	"""Artikel mit ``Item.cv_maschinenkosten`` (Maschinen-, Geräte-, Fahrtkosten): § 35a Arbeitskosten."""
	if not codes or not frappe.db.has_column("Item", MASCHINENKOSTEN):
		return set()
	return set(
		frappe.get_all("Item", filters={"name": ["in", list(codes)], MASCHINENKOSTEN: 1}, pluck="name")
	)


def _regie_artikel(zeilen) -> set[str]:
	"""100 % Lohn: Regiestunden (Verfahren „Sonstiges“, Katalog 2.1, und Einheit Stunde) und Artikel
	mit Maschinen- oder Fahrtkosten (``Item.cv_maschinenkosten``, jede Einheit)."""
	codes = sorted({z.item_code for z in zeilen if z.get("item_code")})
	if not codes:
		return set()
	ergebnis = _maschinen_artikel(codes)
	if not frappe.db.has_column("Item", "cv_verfahren"):
		return ergebnis
	artikel = frappe.get_all(
		"Item",
		filters={"name": ["in", codes], "cv_verfahren": VERFAHREN_SONSTIGES},
		fields=["name", "stock_uom"],
	)
	stunden = {a.name for a in artikel if a.stock_uom in EINHEITEN_STUNDEN}
	return ergebnis | {
		z.item_code
		for z in zeilen
		if z.get("item_code") in stunden and (z.get("uom") or z.get("stock_uom") or "h") in EINHEITEN_STUNDEN
	}


def ist_regie(item_code: str | None, uom: str | None = None) -> bool:
	"""Einzelner Artikel (Nachtrag der Schlussrechnung): Regiestunde oder Maschinen-/Fahrtkosten?"""
	if not item_code:
		return False
	if _maschinen_artikel([item_code]):
		return True
	if not frappe.db.has_column("Item", "cv_verfahren"):
		return False
	artikel = frappe.db.get_value("Item", item_code, ["cv_verfahren", "stock_uom"], as_dict=True)
	return bool(
		artikel
		and artikel.cv_verfahren == VERFAHREN_SONSTIGES
		and artikel.stock_uom in EINHEITEN_STUNDEN
		and (uom or artikel.stock_uom) in EINHEITEN_STUNDEN
	)


def _bezuege(kind: str, namen: list[str]) -> dict[str, dict]:
	"""Anteile der Vorgängerzeilen (nur berechnete: Material + Lohn = Betrag)."""
	namen = [n for n in namen if n]
	if not namen or not frappe.get_meta(kind).has_field("cv_lohnanteil"):
		return {}
	ergebnis = {}
	for z in frappe.get_all(
		kind,
		filters={"name": ["in", namen]},
		fields=["name", "amount", "cv_materialanteil", "cv_lohnanteil"],
	):
		betrag, material, lohn = flt(z.amount, 2), flt(z.cv_materialanteil, 2), flt(z.cv_lohnanteil, 2)
		if betrag and (material or lohn) and abs(material + lohn - betrag) < 0.005:
			ergebnis[z.name] = {"betrag": str(betrag), "lohn": str(lohn)}
	return ergebnis


def eingabe(doc) -> tuple[list, list[dict], dict]:
	"""(Zeilen, Positionen für die Engine, Optionen); Zeilen ohne Position (Abzug) fehlen in beiden."""
	e = einstellungen()
	zeilen_alle = list(doc.get("items") or [])
	kalkulationen = _kalkulationen(doc) if doc.doctype in ("Quotation", "Sales Order") else {}
	regie = _regie_artikel(zeilen_alle)
	bezuege: dict[str, dict] = {}
	abrechnung = doc.doctype == "Sales Invoice" and bool(doc.get("cv_auftrag"))
	if doc.doctype == "Sales Order":
		bezuege = _bezuege("Quotation Item", [z.get("quotation_item") for z in zeilen_alle])
	elif doc.doctype == "Sales Invoice" and not abrechnung:
		bezuege = _bezuege("Sales Order Item", [z.get("so_detail") for z in zeilen_alle])
	zeilen, positionen = [], []
	for z in zeilen_alle:
		if abrechnung and z.get("cv_positionsart") == ART_ABZUG:
			continue
		p = {"id": str(len(positionen) + 1), "betrag": str(flt(z.amount, 2)), "menge": str(flt(z.qty, 6))}
		if z.get("item_code"):
			p["item_code"] = z.item_code
		if doc.doctype == "Quotation" and z.get("is_alternative"):
			p["in_summe"] = False
		if abrechnung:
			p["lohnanteil"] = str(flt(z.get("cv_lohnanteil"), 2))
		else:
			vorher = z.get("quotation_item") if doc.doctype == "Sales Order" else z.get("so_detail")
			if vorher in bezuege:
				p["bezug"] = bezuege[vorher]
			if z.get("item_code") in regie:
				p["regie"] = True
			if z.get("item_code") in kalkulationen:
				p["kalkulation"] = kalkulationen[z.item_code]
		zeilen.append(z)
		positionen.append(p)
	optionen = {
		"mit_zuschlaegen": bool(e.lohnanteil_mit_zuschlaegen),
		"lohnanteil_prozent_standard": str(flt(e.lohnanteil_prozent_standard, 2)),
		"steuersatz": str(flt(steuersatz(doc), 4)),
		"rundung": kern.rundung(),
	}
	if not abrechnung and doc.get("net_total") is not None:
		optionen["netto"] = str(flt(doc.net_total, 2))
	return zeilen, positionen, optionen


# --- Rechnen und setzen --------------------------------------------------------------------------


def berechnen(doc) -> dict | None:
	"""Ergebnis der Engine mit ``zeilen`` (gleiche Reihenfolge wie ``positionen``); ``None`` ohne Kern,
	ohne Felder oder bei Fehlern der Engine (werden protokolliert, der Beleg wird trotzdem gespeichert)."""
	fn = _engine()
	if fn is None or not felder_da(doc.doctype):
		return None
	zeilen, positionen, optionen = eingabe(doc)
	ergebnis = fn(positionen, optionen)
	if not ergebnis.get("ok"):
		frappe.log_error(
			title=f"Lohn/Material {doc.doctype} {doc.name}",
			message=json.dumps(ergebnis.get("fehler"), ensure_ascii=False, indent=1),
		)
		return None
	ergebnis["zeilen"] = zeilen
	return ergebnis


def werte(doc, ergebnis: dict) -> tuple[dict, list[tuple]]:
	"""(Kopf, [(Zeile, Werte)]) - Feldwerte aus dem Ergebnis; Zeilen ohne Position (Abzug) 0/0."""
	s = ergebnis["summen"]
	kopf = {
		"cv_material_netto": flt(s["material"]["netto"]),
		"cv_material_brutto": flt(s["material"]["brutto"]),
		"cv_lohn_netto": flt(s["lohn"]["netto"]),
		"cv_lohn_brutto": flt(s["lohn"]["brutto"]),
	}
	if doc.doctype == "Sales Invoice" and not doc.get("cv_auftrag"):
		# § 35a: derselbe Lohn (Abschlags-/Schlussrechnungen setzt die Abrechnung selbst)
		kopf.update(
			cv_lohnanteil_netto=flt(s["lohn"]["netto"]),
			cv_lohnanteil_steuer=flt(s["lohn"]["steuer"]),
			cv_lohnanteil_brutto=flt(s["lohn"]["brutto"]),
			cv_lohnanteil_prozent=flt(s["lohn_prozent"]),
		)
	kopf = {f: w for f, w in kopf.items() if doc.meta.has_field(f)}
	je_position = {
		id(z): {"cv_materialanteil": flt(p["material"]), "cv_lohnanteil": flt(p["lohn"])}
		for z, p in zip(ergebnis["zeilen"], ergebnis["positionen"], strict=True)
	}
	leer = {"cv_materialanteil": 0.0, "cv_lohnanteil": 0.0}
	return kopf, [(z, je_position.get(id(z), leer)) for z in doc.get("items") or []]


def beleg_validieren(doc, method=None) -> None:
	"""``validate`` an Quotation, Sales Order, Sales Invoice."""
	ergebnis = berechnen(doc)
	if not ergebnis:
		return
	kopf, zeilen = werte(doc, ergebnis)
	doc.update(kopf)
	for z, w in zeilen:
		z.update(w)


def nachtragen(doctype: str, name: str) -> bool:
	"""Gespeicherten Beleg (Entwurf oder freigegeben) nachrechnen und die Felder direkt schreiben
	(ohne ``modified``); ``False`` ohne Kern/Felder."""
	doc = frappe.get_doc(doctype, name)
	ergebnis = berechnen(doc)
	if not ergebnis:
		return False
	kopf, zeilen = werte(doc, ergebnis)
	frappe.db.set_value(doctype, name, kopf, update_modified=False)
	for z, w in zeilen:
		frappe.db.set_value(DOCTYPES[doctype], z.name, w, update_modified=False)
	return True
