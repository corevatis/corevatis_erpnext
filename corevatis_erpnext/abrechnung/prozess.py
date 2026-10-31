"""Prozessleiste, Schritt „Rechnung“ (7.1/7.2): Text und Aktion aus Abschlagsplan und Rechnungen.

Wird von ``prozess.kette`` aufgerufen (dort nur je ein Aufruf in ``sammeln``, ``schritte`` und
``aktionen``). Texte: „Abschlag 1 von 3 gestellt (30 %, 1.431,26 € brutto), 2 offen“,
„Schlussrechnung im Entwurf“, „Schlussrechnung RE-… vom …, bezahlt am …“ bzw. „… offen 1.654,68 €, fällig
seit 5 Tagen, 1. Mahnung vom …“ (7.5: Zahlungs- und Mahnstand aus ``offene_posten``). Aktionen: „Rechnung freigeben“
(Entwurf), „Schlussrechnung erstellen“ (nach der Abnahme), „Abschlag stellen“; der ERPNext-Weg
„Erstellen → Rechnung“ bleibt am Auftrag erreichbar, die Leiste führt über die neuen Knöpfe.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money, format_date

from corevatis_erpnext.abrechnung import plan
from corevatis_erpnext.abrechnung.auftrag import darf_abrechnen, felder_da, ist_bezahlt
from corevatis_erpnext.abrechnung.offene_posten import bezahlt_am, ist_teilbezahlt, mahnstand, zahlungsinfo

BASIS_FELDER = (
	"name",
	"docstatus",
	"status",
	"is_return",
	"creation",
	"posting_date",
	"due_date",
	"grand_total",
	"outstanding_amount",
	"currency",
)
CV_FELDER = ("cv_abschlag", "cv_schlussrechnung", "cv_abschlag_nr", "cv_abschlag_prozent", "cv_auftrag")


def rechnung_felder() -> tuple[str, ...]:
	return BASIS_FELDER + (CV_FELDER if felder_da() else ())


def plan_zu_auftraegen(auftraege: list[str]) -> frappe._dict | None:
	"""Abschlagsplan zum (eingereichten) Auftrag mit Zählern - ohne Rechte, Namen gibt die Leiste nur lesbar aus."""
	if not auftraege or not plan.doctype_da():
		return None
	name = frappe.db.get_value(plan.DOCTYPE, {"auftrag": ["in", auftraege]}, "name")
	if not name:
		return None
	zeilen = frappe.get_all(
		plan.ZEILE,
		filters={"parent": name, "parenttype": plan.DOCTYPE},
		fields=["nr", "prozent", "betrag", "status", "rechnung", "faellig_bei", "brutto"],
		order_by="idx asc",
	)
	offen = [z for z in zeilen if z.status == plan.STATUS_OFFEN and not z.rechnung]
	return frappe._dict(
		name=name, zeilen=zeilen, anzahl=len(zeilen), offen=len(offen), naechste=offen[0] if offen else None
	)


def _geld(wert, currency) -> str:
	return fmt_money(flt(wert), currency=currency or None)


def schritt(
	k: frappe._dict, auftrag, projekt, abgenommen: bool, erledigt: str, aktiv: str, offen: str, lesbar=None
) -> dict:
	"""Rückgabe: ``status``, ``text``, ``belege`` (Liste ``(doctype, name, info)``), ``betrieb``.

	Beträge und Prozente des Abschlagsplans erscheinen nur mit Leserecht auf den Auftrag (``lesbar``;
	Security-Review: ein Monteur mit Projektrecht sähe sonst Abschlagsbeträge)."""
	rechnungen = [r for r in k.rechnungen if not cint(r.get("is_return"))]
	abschlaege = [r for r in rechnungen if cint(r.get("cv_abschlag"))]
	schluss = [r for r in rechnungen if cint(r.get("cv_schlussrechnung"))]
	belege = []
	for r in rechnungen:
		if cint(r.get("cv_abschlag")):
			info = _("Abschlag {0}").format(cint(r.get("cv_abschlag_nr")))
		elif cint(r.get("cv_schlussrechnung")):
			info = _("Schlussrechnung")
		else:
			info = ""
		if r.docstatus == 0:
			info = (info + " · " if info else "") + _("Entwurf")
		elif r.docstatus == 1 and ist_bezahlt(r):
			info = (info + " · " if info else "") + _("bezahlt")
		elif r.docstatus == 1 and ist_teilbezahlt(r):
			info = (info + " · " if info else "") + _("teilbezahlt")
		belege.append(("Sales Invoice", r.name, info))
	# 7.5: Zahlungs- und Mahnstand der gebuchten Rechnungen („bezahlt am …“, „fällig seit …“, Mahnstufe)
	gebuchte_namen = [r.name for r in rechnungen if r.docstatus == 1]
	zahlungen = bezahlt_am(gebuchte_namen) if gebuchte_namen else {}
	mahnungen = mahnstand(gebuchte_namen) if gebuchte_namen else {}

	def zahlung(r) -> str:
		return zahlungsinfo(r, currency, mahnungen.get(r.name), zahlungen.get(r.name))

	plan_info = k.get("abschlagsplan")
	gebucht = [r for r in rechnungen if r.docstatus == 1]
	abgerechnet = flt(auftrag.per_billed) if auftrag and auftrag.docstatus == 1 else 0
	currency = (rechnungen[0].get("currency") if rechnungen else None) or None

	schluss_gebucht = next((r for r in schluss if r.docstatus == 1), None)
	schluss_entwurf = next((r for r in schluss if r.docstatus == 0), None)
	if schluss_gebucht:
		text = _("Schlussrechnung {0} vom {1}").format(
			schluss_gebucht.name, format_date(schluss_gebucht.posting_date)
		)
		text += ", " + zahlung(schluss_gebucht)
		return {
			"status": erledigt,
			"text": text,
			"belege": belege,
			"betrieb": False,
			"datum": schluss_gebucht.posting_date,
		}
	if schluss_entwurf:
		return {
			"status": aktiv,
			"text": _("Schlussrechnung im Entwurf: prüfen und freigeben"),
			"belege": belege,
			"betrieb": True,
			"datum": schluss_entwurf.creation,
		}
	if gebucht and (not auftrag or abgerechnet >= 100) and not abschlaege:
		return {
			"status": erledigt,
			"text": _("Abgerechnet"),
			"belege": belege,
			"betrieb": False,
			"datum": None,
		}

	entwurf = next((r for r in abschlaege if r.docstatus == 0), None)
	gestellt = [r for r in abschlaege if r.docstatus == 1]
	anzahl_plan = plan_info.anzahl if plan_info else 0
	offen_plan = plan_info.offen if plan_info else 0
	if entwurf:
		text = _("Abschlag {0} im Entwurf ({1} %): prüfen und freigeben").format(
			cint(entwurf.get("cv_abschlag_nr")), f"{flt(entwurf.get('cv_abschlag_prozent'), 2):g}"
		)
		return {"status": aktiv, "text": text, "belege": belege, "betrieb": True, "datum": entwurf.creation}
	if gestellt:
		letzter = gestellt[-1]
		von = _(" von {0}").format(anzahl_plan) if anzahl_plan else ""
		text = _("Abschlag {0}{1} gestellt ({2} %, {3} brutto)").format(
			cint(letzter.get("cv_abschlag_nr")) or len(gestellt),
			von,
			f"{flt(letzter.get('cv_abschlag_prozent'), 2):g}",
			_geld(letzter.grand_total, currency),
		)
		if offen_plan:
			text += ", " + _("{0} offen").format(offen_plan)
		text += "; " + zahlung(letzter)
		if abgenommen:
			text += " " + _("- jetzt Schlussrechnung")
		return {
			"status": aktiv,
			"text": text,
			"belege": belege,
			"betrieb": abgenommen,
			"datum": letzter.posting_date,
		}
	if gebucht:
		return {
			"status": aktiv,
			"text": _("Teilweise abgerechnet ({0} %)").format(cint(abgerechnet)),
			"belege": belege,
			"betrieb": False,
			"datum": None,
		}
	if rechnungen:
		return {
			"status": aktiv,
			"text": _("Rechnung im Entwurf"),
			"belege": belege,
			"betrieb": True,
			"datum": None,
		}
	if abgenommen and auftrag:
		return {
			"status": offen,
			"text": _("Abgenommen - Schlussrechnung erstellen"),
			"belege": [],
			"betrieb": True,
			"datum": None,
		}
	if plan_info and plan_info.naechste and auftrag:
		z = plan_info.naechste
		if lesbar is None or lesbar("Sales Order", auftrag.name):
			anteil = f"{flt(z.prozent, 2):g} %" if flt(z.prozent) else _geld(z.betrag, currency)
			text = _("Abschlagsplan: 0 von {0} gestellt - nächster Abschlag {1} {2}").format(
				anzahl_plan, anteil, z.faellig_bei or ""
			)
		else:
			text = _("Abschlagsplan: 0 von {0} gestellt").format(anzahl_plan)
		return {"status": offen, "text": text, "belege": [], "betrieb": False, "datum": None}
	if projekt and not abgenommen:
		return {
			"status": offen,
			"text": _("Schlussrechnung nach der Abnahme (Abschlag möglich)"),
			"belege": [],
			"betrieb": False,
			"datum": None,
		}
	return {"status": offen, "text": _("Noch keine Rechnung"), "belege": [], "betrieb": False, "datum": None}


def aktion(k: frappe._dict, auftrag, projekt, abgenommen: bool, erledigt: str, lesbar, darf) -> dict | None:
	"""Aktion des Schritts (nur mit den Rechten, die die Zielmethode prüft): Rückgabe-Felder wie ``kette._aktion``."""
	rechnungen = [r for r in k.rechnungen if not cint(r.get("is_return"))]
	schluss = [r for r in rechnungen if cint(r.get("cv_schlussrechnung"))]
	if any(r.docstatus == 1 for r in schluss):
		return None
	entwurf = next(
		(
			r
			for r in rechnungen
			if r.docstatus == 0 and (cint(r.get("cv_abschlag")) or cint(r.get("cv_schlussrechnung")))
		),
		None,
	)
	if entwurf and darf("Sales Invoice", "submit", entwurf.name):
		return {
			"label": _("Rechnung freigeben"),
			"doctype": "Sales Invoice",
			"name": entwurf.name,
			"js": "corevatis_erpnext.abrechnung.freigeben",
		}
	if (
		not auftrag
		or auftrag.docstatus != 1
		or not felder_da()
		or not darf_abrechnen()
		or not darf("Sales Invoice", "create")
	):
		return None
	if not lesbar("Sales Order", auftrag.name):
		return None
	ziel = (
		("Project", projekt.name)
		if projekt and lesbar("Project", projekt.name)
		else ("Sales Order", auftrag.name)
	)
	if abgenommen or not projekt:
		return {
			"label": _("Schlussrechnung erstellen"),
			"doctype": ziel[0],
			"name": ziel[1],
			"js": "corevatis_erpnext.abrechnung.schlussrechnung_dialog",
		}
	return {
		"label": _("Abschlag stellen"),
		"doctype": ziel[0],
		"name": ziel[1],
		"js": "corevatis_erpnext.abrechnung.abschlag_dialog",
	}
