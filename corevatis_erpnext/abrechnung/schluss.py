"""Schlussrechnung (7.2): Auftragspositionen mit Ist-Mengen, Nachträge, Abzug der Abschläge,
§ 35a-Lohnanteil → Sales Invoice im Entwurf.

- Ist-Mengen vorbelegt mit der Auftragsmenge; Hinweis, wenn Tagesrapporte (Kern 5.3, ``Rapport
  Position.menge_heute`` je Auftragsposition) andere Mengen melden oder die Abnahme (6.1) eine
  Leistung nicht abgenommen hat.
- Mehrmengen stehen als eigene Zeile „Mehrmenge zu Pos. n“ ohne Auftragszeile (ERPNext prüft sonst
  Überfakturierung je Auftragszeile); Mindermengen kürzen die Auftragszeile; Ist-Menge 0 entfällt.
- Abzugszeilen „abzüglich Abschlagsrechnung Nr. 1 vom … (netto)“ als Positionen mit negativem Preis
  (Selling Settings „negative Preise“, gesetzt von ``ensure_abrechnung_setup``); ERPNext rechnet die
  Steuer auf den verbleibenden Nettobetrag - wie die Engine.
- Lohnanteil je Position aus der Kalkulation 4.1 (``Sales Order.cv_kalkulation_json``: Lohn =
  Minuten x Stundensatz je Einheit, optional mit Zuschlägen), sonst Prozentsatz der Einstellungen;
  das Büro kann ihn je Position überschreiben (höchstens Positionspreis, Engine kappt).
- Vor der Abnahme (6.2) nur als Übersteuerung durch einen System Manager mit Begründung.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, format_date, strip_html

from corevatis_erpnext.abnahme.einstellungen import einstellungen as abnahme_einstellungen
from corevatis_erpnext.abnahme.garantie import abgenommen
from corevatis_erpnext.abnahme.konstanten import ROLLE_UEBERSTEUERN
from corevatis_erpnext.abrechnung import kern, plan
from corevatis_erpnext.abrechnung.abschlag import (
	abschlaege_uebersicht,
	ensure_abschlag_artikel,
	rechnung_kopf,
)
from corevatis_erpnext.abrechnung.auftrag import (
	abschlaege_gestellt,
	auftrag_eingabe,
	auftrag_laden,
	ist_bezahlt,
	pruefe_rolle,
	rechnungen_zum_auftrag,
	schlussrechnung_zum_auftrag,
	steuersatz,
)
from corevatis_erpnext.abrechnung.einstellungen import einstellungen
from corevatis_erpnext.abrechnung.felder import ART_ABZUG, ART_AUFTRAG, ART_MEHRMENGE, ART_NACHTRAG
from corevatis_erpnext.abrechnung.kern import AbrechnungFehler

TAGESRAPPORT, RAPPORT_POSITION = "Tagesrapport", "Rapport Position"
ABNAHMEPROTOKOLL, ABNAHME_LEISTUNG = "Abnahmeprotokoll", "Abnahme Leistung"
RAPPORT_FERTIG = ("Abgeschlossen", "Gegengezeichnet")
LOHN_KALKULATION = "kalkulation"
LOHN_STANDARD = "standard"


def _json(wert):
	if wert in (None, ""):
		return None
	if isinstance(wert, str):
		try:
			return json.loads(wert)
		except ValueError:
			frappe.throw(_("Ungültige Eingabe (JSON erwartet)."), frappe.ValidationError)
	return wert


# --- Lohnanteil aus der Kalkulation ------------------------------------------------------------


def lohn_je_position(auftrag) -> dict[str, float]:
	"""Lohn je Einheit (netto) je Artikel aus ``cv_kalkulation_json`` (4.1): Lohn + Erschwernis =
	Minuten x Stundensatz; mit Einstellung „mit Zuschlägen“ x Einzelpreis ÷ Einzelkosten."""
	if not frappe.db.has_column("Sales Order", "cv_kalkulation_json"):
		return {}
	roh = frappe.db.get_value("Sales Order", auftrag.name, "cv_kalkulation_json")
	if not roh:
		return {}
	try:
		daten = json.loads(roh) if isinstance(roh, str) else roh
	except ValueError:
		return {}
	if not isinstance(daten, dict) or not daten.get("ok"):
		return {}
	mit_zuschlaegen = einstellungen().lohnanteil_mit_zuschlaegen
	ergebnis: dict[str, float] = {}
	for p in daten.get("positionen") or []:
		k = p.get("kalkulation") or {}
		lohn = flt(k.get("lohn")) + flt(k.get("erschwernis"))
		if mit_zuschlaegen and flt(k.get("einzelkosten")) > 0:
			lohn = lohn * flt(k.get("einzelpreis")) / flt(k.get("einzelkosten"))
		code = p.get("item_code") or p.get("id")
		if code and lohn > 0:
			ergebnis[code] = flt(lohn, 6)
	return ergebnis


def lohnanteil_des_auftrags(auftrag) -> float | None:
	"""Lohnanteil des ganzen Auftrags (Σ Lohn je Einheit x Menge) für den anteiligen Ausweis auf
	Abschlagsrechnungen; ``None`` ohne Kalkulation."""
	lohn = lohn_je_position(auftrag)
	if not lohn:
		return None
	return flt(sum(lohn.get(z.item_code, 0.0) * flt(z.qty) for z in auftrag.items), 2)


# --- Ist-Mengen aus Baustelle und Abnahme ----------------------------------------------------


def ist_hinweise(projekt: str | None) -> dict[str, dict]:
	"""Je Auftragszeile: gemeldete Menge der abgeschlossenen Tagesrapporte, „nicht abgenommen“ der Abnahme."""
	hinweise: dict[str, dict] = {}
	if not projekt:
		return hinweise
	if frappe.db.exists("DocType", TAGESRAPPORT) and frappe.db.exists("DocType", RAPPORT_POSITION):
		rapporte = frappe.get_all(
			TAGESRAPPORT, filters={"projekt": projekt, "status": ["in", RAPPORT_FERTIG]}, pluck="name"
		)
		if rapporte:
			for z in frappe.get_all(
				RAPPORT_POSITION,
				filters={"parent": ["in", rapporte], "parenttype": TAGESRAPPORT},
				fields=["auftragsposition", "menge_heute"],
			):
				if z.auftragsposition:
					h = hinweise.setdefault(z.auftragsposition, {})
					h["rapport_menge"] = flt(h.get("rapport_menge")) + flt(z.menge_heute)
					h["rapporte"] = len(rapporte)
	if frappe.db.exists("DocType", ABNAHMEPROTOKOLL) and frappe.db.exists("DocType", ABNAHME_LEISTUNG):
		protokolle = frappe.get_all(
			ABNAHMEPROTOKOLL, filters={"projekt": projekt, "status": "Abgeschlossen"}, pluck="name"
		)
		if protokolle:
			for z in frappe.get_all(
				ABNAHME_LEISTUNG,
				filters={"parent": ["in", protokolle], "parenttype": ABNAHMEPROTOKOLL},
				fields=["auftragsposition", "abgenommen", "bemerkung"],
			):
				if z.auftragsposition and not cint(z.abgenommen):
					hinweise.setdefault(z.auftragsposition, {})["nicht_abgenommen"] = z.bemerkung or True
	return hinweise


def _hinweis_text(zeile, h: dict | None) -> str | None:
	if not h:
		return None
	teile = []
	if "rapport_menge" in h and abs(flt(h["rapport_menge"]) - flt(zeile.qty)) > 0.005:
		teile.append(
			_("Tagesrapporte melden {0} {1} (Auftrag {2} {1})").format(
				frappe.format_value(flt(h["rapport_menge"]), {"fieldtype": "Float"}),
				zeile.uom or "",
				frappe.format_value(flt(zeile.qty), {"fieldtype": "Float"}),
			)
		)
	if h.get("nicht_abgenommen"):
		text = _("In der Abnahme nicht abgenommen")
		if isinstance(h["nicht_abgenommen"], str):
			text += f": {h['nicht_abgenommen']}"
		teile.append(text)
	return "; ".join(teile) or None


# --- Vorschlag -------------------------------------------------------------------------------


@frappe.whitelist()
def schlussrechnung_vorschlag(sales_order: str | None = None, project: str | None = None) -> dict:
	"""Positionen mit Auftrags- und Ist-Mengen, Lohnanteil, Hinweise, gestellte Abschläge, Abnahme."""
	pruefe_rolle()
	auftrag, projekt = auftrag_laden(sales_order, project, sperren=False)
	lohn = lohn_je_position(auftrag)
	hinweise = ist_hinweise(projekt)
	e = einstellungen()
	positionen = []
	for z in auftrag.items:
		h = hinweise.get(z.name)
		positionen.append(
			{
				"so_detail": z.name,
				"idx": z.idx,
				"item_code": z.item_code,
				"item_name": z.item_name,
				"bezeichnung": strip_html(z.description or "") or z.item_name,
				"einheit": z.uom,
				"menge_auftrag": flt(z.qty),
				"menge_ist": flt(z.qty),
				"menge_rapport": flt(h["rapport_menge"]) if h and "rapport_menge" in h else None,
				"einzelpreis": flt(z.rate),
				"betrag_auftrag": flt(z.amount),
				"lohn_je_einheit": lohn.get(z.item_code),
				"lohnanteil": flt(lohn.get(z.item_code, 0.0) * flt(z.qty), 2)
				if z.item_code in lohn
				else None,
				"lohn_quelle": LOHN_KALKULATION if z.item_code in lohn else LOHN_STANDARD,
				"hinweis": _hinweis_text(z, h),
			}
		)
	abschlaege = rechnungen_zum_auftrag(auftrag.name, art="abschlag")
	schluss = schlussrechnung_zum_auftrag(auftrag.name)
	abgen = abgenommen(projekt) if projekt else None
	return {
		"auftrag": {
			"name": auftrag.name,
			"kunde": auftrag.customer_name,
			"netto": flt(auftrag.net_total, 2),
			"steuersatz": steuersatz(auftrag),
			"brutto": flt(auftrag.grand_total, 2),
			"datum": auftrag.transaction_date,
		},
		"projekt": projekt,
		"positionen": positionen,
		"abschlaege": [
			{
				"name": r.name,
				"nr": cint(r.cv_abschlag_nr),
				"prozent": flt(r.cv_abschlag_prozent),
				"datum": r.posting_date,
				"netto": flt(r.net_total),
				"steuer": flt(r.total_taxes_and_charges),
				"brutto": flt(r.grand_total),
				"bezahlt": ist_bezahlt(r),
			}
			for r in abschlaege
			if r.docstatus == 1
		],
		"entwuerfe": [r.name for r in abschlaege if r.docstatus == 0],
		"abnahme": {
			"abgenommen": abgen,
			"datum": frappe.db.get_value("Project", projekt, "cv_abgenommen_am")
			if projekt and frappe.db.has_column("Project", "cv_abgenommen_am")
			else None,
			"sperre": cint(abnahme_einstellungen().rechnung_ohne_abnahme_sperren),
			"darf_uebersteuern": ROLLE_UEBERSTEUERN in frappe.get_roles()
			or frappe.session.user == "Administrator",
		},
		"lohnanteil_prozent_standard": e.lohnanteil_prozent_standard,
		"schlussrechnung": {"name": schluss.name, "docstatus": schluss.docstatus} if schluss else None,
	}


# --- Erstellen -------------------------------------------------------------------------------


def _positionen_eingabe(auftrag, eingaben: dict, lohn: dict) -> list[dict]:
	so_zeilen = {z.name: z for z in auftrag.items}
	for key in eingaben:
		if key not in so_zeilen:
			frappe.throw(
				_("Unbekannte Auftragsposition {0}.").format(frappe.utils.escape_html(str(key))),
				AbrechnungFehler,
			)
	positionen = []
	for z in auftrag.items:
		e = eingaben.get(z.name) or {}
		menge_ist = flt(e.get("menge_ist")) if e.get("menge_ist") not in (None, "") else flt(z.qty)
		if menge_ist < 0:
			frappe.throw(
				_("Position {0}: Ist-Menge darf nicht negativ sein.").format(z.idx), frappe.ValidationError
			)
		p = {
			"id": z.name,
			"item_code": z.item_code,
			"bezeichnung": z.item_name,
			"einheit": z.uom,
			"menge_auftrag": str(flt(z.qty)),
			"menge_ist": str(menge_ist),
			"einzelpreis": str(flt(z.rate)),
		}
		if e.get("lohnanteil") not in (None, ""):
			if flt(e["lohnanteil"]) < 0:
				frappe.throw(
					_("Position {0}: Lohnanteil darf nicht negativ sein.").format(z.idx),
					frappe.ValidationError,
				)
			p["lohnanteil"] = str(flt(e["lohnanteil"], 2))
		elif z.item_code in lohn:
			p["lohn_je_einheit"] = str(lohn[z.item_code])
		positionen.append(p)
	return positionen


def _nachtraege_eingabe(nachtraege: list, lohn: dict) -> list[dict]:
	ergebnis = []
	for i, n in enumerate(nachtraege or [], start=1):
		if not isinstance(n, dict):
			frappe.throw(_("Nachtrag {0}: ungültige Eingabe.").format(i), frappe.ValidationError)
		code = n.get("item_code")
		if not code or not frappe.db.exists("Item", code):
			frappe.throw(
				_("Nachtrag {0}: bitte einen Artikel aus dem Katalog wählen.").format(i),
				frappe.MandatoryError,
			)
		artikel = frappe.db.get_value(
			"Item", code, ["item_name", "stock_uom", "is_sales_item", "disabled"], as_dict=True
		)
		if not cint(artikel.is_sales_item) or cint(artikel.disabled):
			frappe.throw(
				_("Nachtrag {0}: Artikel {1} ist kein Verkaufsartikel.").format(i, code),
				frappe.ValidationError,
			)
		menge, preis = flt(n.get("menge")), flt(n.get("einzelpreis"))
		if menge <= 0:
			frappe.throw(_("Nachtrag {0}: Menge muss größer als 0 sein.").format(i), frappe.ValidationError)
		if preis < 0:
			frappe.throw(
				_("Nachtrag {0}: Einzelpreis darf nicht negativ sein.").format(i), frappe.ValidationError
			)
		p = {
			"id": f"NT-{i}",
			"item_code": code,
			"bezeichnung": (n.get("bezeichnung") or artikel.item_name or code)[:140],
			"einheit": n.get("einheit") or artikel.stock_uom,
			"menge": str(menge),
			"einzelpreis": str(preis),
		}
		if n.get("lohnanteil") not in (None, ""):
			if flt(n["lohnanteil"]) < 0:
				frappe.throw(
					_("Nachtrag {0}: Lohnanteil darf nicht negativ sein.").format(i), frappe.ValidationError
				)
			p["lohnanteil"] = str(flt(n["lohnanteil"], 2))
		elif code in lohn:
			p["lohn_je_einheit"] = str(lohn[code])
		ergebnis.append(p)
	return ergebnis


def _abschlaege_eingabe(auftrag) -> tuple[list[dict], list[frappe._dict]]:
	gestellt = abschlaege_gestellt(auftrag.name)
	return [
		{
			"nr": cint(r.cv_abschlag_nr) or i,
			"rechnung": r.name,
			"datum": format_date(r.posting_date),
			"netto": str(flt(r.net_total, 2)),
			"steuer": str(flt(r.total_taxes_and_charges, 2)),
			"brutto": str(flt(r.grand_total, 2)),
		}
		for i, r in enumerate(gestellt, start=1)
	], gestellt


def _abnahme_pruefen(projekt: str | None, uebersteuern, grund) -> bool:
	"""6.2: vor der Abnahme nur Übersteuerung (System Manager, Begründung) - Rückgabe: übersteuert."""
	if not projekt or not cint(abnahme_einstellungen().rechnung_ohne_abnahme_sperren) or abgenommen(projekt):
		return False
	if not cint(uebersteuern):
		frappe.throw(
			_(
				"Projekt {0} ist noch nicht abgenommen. Bitte zuerst das Abnahmeprotokoll abschließen; "
				"nur ein System Manager kann die Schlussrechnung mit Begründung vorher freigeben."
			).format(projekt),
			AbrechnungFehler,
			title=_("Schlussrechnung ohne Abnahme"),
		)
	if ROLLE_UEBERSTEUERN not in frappe.get_roles() and frappe.session.user != "Administrator":
		frappe.throw(
			_("Eine Schlussrechnung ohne Abnahme kann nur ein System Manager freigeben (Übersteuerung)."),
			frappe.PermissionError,
		)
	if len((grund or "").strip()) < 5:
		frappe.throw(
			_("Bitte eine Begründung für die Schlussrechnung ohne Abnahme angeben (mindestens 5 Zeichen)."),
			frappe.MandatoryError,
		)
	return True


def _lohn_aufteilen(p: dict, basis: float, menge_ist: float) -> tuple[float, float]:
	"""Lohnanteil der Position anteilig auf Auftragszeile (basis) und Mehrmenge verteilen."""
	gesamt = flt(p["lohnanteil"]["netto"])
	if menge_ist <= 0 or basis >= menge_ist:
		return gesamt, 0.0
	anteil = flt(gesamt * basis / menge_ist, 2)
	return anteil, flt(gesamt - anteil, 2)


def _rechnung_anlegen(auftrag, projekt, ergebnis, gestellt, uebersteuert, grund, plan_name):
	si = rechnung_kopf(auftrag)
	artikel = ensure_abschlag_artikel()
	projekt_zeile = projekt if not auftrag.get("project") else None
	je_id = {p["id"]: p for p in ergebnis["positionen"]}
	entfallen = []
	for z in auftrag.items:
		p = je_id[z.name]
		menge_ist, menge_auftrag = flt(p["menge_ist"]), flt(z.qty)
		if menge_ist <= 0:
			entfallen.append(z.idx)
			continue
		basis = min(menge_ist, menge_auftrag)
		lohn_basis, lohn_mehr = _lohn_aufteilen(p, basis, menge_ist)
		si.append(
			"items",
			{
				"item_code": z.item_code,
				"item_name": z.item_name,
				"description": z.description,
				"qty": basis,
				"uom": z.uom,
				"conversion_factor": z.conversion_factor or 1,
				"rate": flt(z.rate),
				"so_detail": z.name,
				"sales_order": auftrag.name,
				"project": projekt_zeile,
				"cv_positionsart": ART_AUFTRAG,
				"cv_menge_auftrag": menge_auftrag,
				"cv_lohnanteil": lohn_basis,
			},
		)
		if menge_ist > menge_auftrag:
			si.append(
				"items",
				{
					"item_code": z.item_code,
					"item_name": z.item_name,
					"description": _("Mehrmenge zu Pos. {0}: {1}").format(
						z.idx, strip_html(z.description or "") or z.item_name
					),
					"qty": flt(menge_ist - menge_auftrag, 6),
					"uom": z.uom,
					"conversion_factor": z.conversion_factor or 1,
					"rate": flt(z.rate),
					"sales_order": auftrag.name,
					"project": projekt_zeile,
					"cv_positionsart": ART_MEHRMENGE,
					"cv_bezug": z.name,
					"cv_menge_auftrag": 0,
					"cv_lohnanteil": lohn_mehr,
				},
			)
	for p in ergebnis["positionen"]:
		if not p["nachtrag"]:
			continue
		si.append(
			"items",
			{
				"item_code": p["item_code"],
				"item_name": p["bezeichnung"],
				"description": frappe.utils.escape_html(p["bezeichnung"]),
				"qty": flt(p["menge_ist"]),
				"uom": p["einheit"],
				"conversion_factor": 1,
				"rate": flt(p["einzelpreis"]),
				"sales_order": auftrag.name,
				"project": projekt_zeile,
				"cv_positionsart": ART_NACHTRAG,
				"cv_lohnanteil": flt(p["lohnanteil"]["netto"]),
			},
		)
	for ab in ergebnis["abschlaege"]:
		si.append(
			"items",
			{
				"item_code": artikel,
				"item_name": _("Abzug Abschlagsrechnung Nr. {0}").format(ab["nr"]),
				"description": frappe.utils.escape_html(ab["text"]),
				"qty": 1,
				"rate": -flt(ab["netto"]),
				"sales_order": auftrag.name,
				"project": projekt_zeile,
				"cv_positionsart": ART_ABZUG,
				"cv_bezug": ab["rechnung"],
			},
		)
	if not si.items:
		frappe.throw(_("Die Schlussrechnung hätte keine Positionen (alle Ist-Mengen 0)."), AbrechnungFehler)
	beschreibungen = {z.idx: (z.item_name, z.description, z.rate) for z in si.items}
	si.set_missing_values()
	for z in si.items:
		name, text, rate = beschreibungen[z.idx]
		z.item_name, z.description, z.rate = name, text, rate
		z.price_list_rate = 0
		z.discount_percentage = 0
		z.discount_amount = 0
	s = ergebnis["summen"]
	si.cv_schlussrechnung = 1
	si.cv_abschlag = 0
	si.cv_auftrag = auftrag.name
	si.cv_abschlagsplan = plan_name
	si.cv_auftragssumme_netto = flt(auftrag.net_total, 2)
	si.cv_kumuliert_prozent = 100
	si.cv_kumuliert_netto = flt(s["leistung"]["netto"])
	si.cv_leistung_netto = flt(s["leistung"]["netto"])
	si.cv_leistung_steuer = flt(s["leistung"]["steuer"])
	si.cv_leistung_brutto = flt(s["leistung"]["brutto"])
	si.cv_abschlaege_netto = flt(s["abschlaege"]["netto"])
	si.cv_abschlaege_steuer = flt(s["abschlaege"]["steuer"])
	si.cv_abschlaege_brutto = flt(s["abschlaege"]["brutto"])
	si.cv_lohnanteil_netto = flt(s["lohnanteil"]["netto"])
	si.cv_lohnanteil_steuer = flt(s["lohnanteil"]["steuer"])
	si.cv_lohnanteil_brutto = flt(s["lohnanteil"]["brutto"])
	si.cv_lohnanteil_prozent = flt(s["lohnanteil"]["prozent"])
	si.set("cv_abschlaege", abschlaege_uebersicht(auftrag.name))
	si.cv_abrechnung_json = json.dumps(ergebnis, ensure_ascii=False)
	if uebersteuert:
		si.cv_abnahme_uebersteuert = 1
		si.cv_uebersteuerung_grund = grund
	si.insert()
	return si, entfallen


@frappe.whitelist(methods=["POST"])
def schlussrechnung_erstellen(
	sales_order: str | None = None,
	project: str | None = None,
	positionen=None,
	nachtraege=None,
	uebersteuern=0,
	grund: str | None = None,
) -> dict:
	"""Schlussrechnung (Entwurf) zum Auftrag anlegen.

	``positionen``: JSON-Objekt ``{so_detail: {"menge_ist": …, "lohnanteil": …}}`` (fehlende Zeilen =
	Auftragsmenge); ``nachtraege``: JSON-Liste ``[{"item_code", "bezeichnung", "menge", "einheit",
	"einzelpreis", "lohnanteil"}]``. Preise der Auftragspositionen kommen aus dem Auftrag.
	"""
	pruefe_rolle()
	frappe.has_permission("Sales Invoice", "create", throw=True)
	auftrag, projekt = auftrag_laden(sales_order, project)
	vorhanden = schlussrechnung_zum_auftrag(auftrag.name)
	if vorhanden:
		return {
			"rechnung": vorhanden.name,
			"docstatus": vorhanden.docstatus,
			"bereits_vorhanden": True,
			"warnungen": [
				_("Zum Auftrag {0} gibt es schon die Schlussrechnung {1}.").format(
					auftrag.name, vorhanden.name
				)
			],
		}
	entwuerfe = [r.name for r in rechnungen_zum_auftrag(auftrag.name, docstatus=0, art="abschlag")]
	if entwuerfe:
		frappe.throw(
			_(
				"Abschlagsrechnung {0} ist noch im Entwurf: bitte erst freigeben oder löschen, dann die Schlussrechnung erstellen."
			).format(", ".join(entwuerfe)),
			AbrechnungFehler,
		)
	uebersteuert = _abnahme_pruefen(projekt, uebersteuern, grund)
	eingaben = _json(positionen) or {}
	if not isinstance(eingaben, dict):
		frappe.throw(_("Positionen: Objekt je Auftragszeile erwartet."), frappe.ValidationError)
	nts = _json(nachtraege) or []
	if not isinstance(nts, list):
		frappe.throw(_("Nachträge: Liste erwartet."), frappe.ValidationError)
	lohn = lohn_je_position(auftrag)
	e = einstellungen()
	engine_pos = _positionen_eingabe(auftrag, eingaben, lohn)
	engine_nt = _nachtraege_eingabe(nts, lohn)
	engine_ab, gestellt = _abschlaege_eingabe(auftrag)
	ergebnis = kern.schlussrechnung_rechnen(
		auftrag_eingabe(auftrag),
		engine_pos,
		engine_nt,
		engine_ab,
		{"lohnanteil_prozent_standard": str(e.lohnanteil_prozent_standard)},
	)
	warnungen = [w["meldung"] for w in ergebnis.get("warnungen") or []]
	if any(w.get("code") == "rest_negativ" for w in ergebnis.get("warnungen") or []):
		frappe.throw(
			_("{0} Eine Gutschrift gehört nicht zu diesem Schritt.").format(
				next(w["meldung"] for w in ergebnis["warnungen"] if w.get("code") == "rest_negativ")
			),
			AbrechnungFehler,
			title=_("Abschläge über der Leistung"),
		)
	plan_name = plan.plan_zum_auftrag(auftrag.name)
	si, entfallen = _rechnung_anlegen(auftrag, projekt, ergebnis, gestellt, uebersteuert, grund, plan_name)
	for idx in entfallen:
		warnungen.append(
			_("Position {0} entfällt (Ist-Menge 0) und steht nicht in der Rechnung.").format(idx)
		)
	soll = flt(ergebnis["summen"]["rest"]["brutto"])
	if abs(flt(si.grand_total) - soll) > 0.005:
		warnungen.append(
			_("Rechnungsbetrag {0} weicht vom Rechenweg {1} ab (Rundung der Site) - bitte prüfen.").format(
				frappe.format_value(si.grand_total, {"fieldtype": "Currency"}),
				frappe.format_value(soll, {"fieldtype": "Currency"}),
			)
		)
	if plan_name:
		plan.aktualisieren(plan_name)
	return {
		"rechnung": si.name,
		"docstatus": si.docstatus,
		"bereits_vorhanden": False,
		"netto": flt(si.net_total),
		"steuer": flt(si.total_taxes_and_charges),
		"brutto": flt(si.grand_total),
		"summen": ergebnis["summen"],
		"positionen": len([p for p in ergebnis["positionen"] if not p["nachtrag"]]),
		"nachtraege": len([p for p in ergebnis["positionen"] if p["nachtrag"]]),
		"abschlaege": len(ergebnis["abschlaege"]),
		"uebersteuert": uebersteuert,
		"warnungen": warnungen,
	}
