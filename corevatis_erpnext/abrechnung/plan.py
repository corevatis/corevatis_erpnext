"""Abschlagsplan (7.1): aus der Vorlage anlegen, mit der Engine durchrechnen, Stand aus den Rechnungen
nachführen.

- Beim Anlegen des Projekts aus der Annahme (4.4, ``Project.after_insert``) bzw. beim Einreichen eines
  Auftrags mit Projekt (``Sales Order.on_submit``) entsteht ein Plan aus der Vorlage der Einstellungen
  (Standard 30/40/30), wenn „automatisch“ an ist und noch keiner existiert. Fehler halten die Annahme
  nicht auf (Savepoint, Error Log, Hinweis - Muster 5.1).
- ``durchrechnen`` schreibt das Engine-Ergebnis (Beträge je Zeile, kumuliert, Summen) in den Plan;
  ``aktualisieren`` liest den Stand der verknüpften Rechnungen (Entwurf, gestellt, bezahlt, storniert).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from corevatis_erpnext.abrechnung import kern
from corevatis_erpnext.abrechnung.auftrag import (
	auftrag_eingabe,
	felder_da,
	ist_bezahlt,
	schlussrechnung_zum_auftrag,
	steuersatz,
)
from corevatis_erpnext.abrechnung.einstellungen import einstellungen, vorlage_zeilen

DOCTYPE = "Abschlagsplan"
ZEILE = "Abschlagsplan Zeile"
STATUS_OFFEN, STATUS_ENTWURF, STATUS_GESTELLT, STATUS_BEZAHLT = "offen", "Entwurf", "gestellt", "bezahlt"
PLAN_OFFEN, PLAN_TEIL, PLAN_VOLL, PLAN_SCHLUSS = (
	"Offen",
	"Teilweise gestellt",
	"Vollständig gestellt",
	"Schlussrechnung",
)
UEBER_AUFTRAGSSUMME = "ueber_auftragssumme"


def doctype_da() -> bool:
	return bool(frappe.db.exists("DocType", DOCTYPE))


def plan_zum_auftrag(sales_order: str | None) -> str | None:
	if not sales_order or not doctype_da():
		return None
	return frappe.db.get_value(DOCTYPE, {"auftrag": sales_order}, "name")


def plan_eingabe(doc) -> dict:
	"""Zeilen des Plans für ``corevatis.engine.invoice.abschlagsplan``."""
	zeilen = []
	for z in doc.get("zeilen") or []:
		zeile = {
			"nr": cint(z.nr) or z.idx,
			"bezeichnung": z.bezeichnung or None,
			"faellig_bei": z.faellig_bei or None,
			"datum": str(z.datum) if z.datum else None,
			"status": z.status or STATUS_OFFEN,
			"rechnung": z.rechnung or None,
		}
		if flt(z.prozent) > 0:
			zeile["prozent"] = str(flt(z.prozent))
		else:
			zeile["betrag"] = str(flt(z.betrag, 2))
		if z.rechnung and z.status != STATUS_OFFEN and z.netto is not None:
			zeile["netto"] = str(flt(z.netto, 2))
		zeilen.append(zeile)
	return {"zeilen": zeilen}


def _plan_status(doc, schluss) -> str:
	if schluss and schluss.docstatus == 1:
		return PLAN_SCHLUSS
	zeilen = doc.get("zeilen") or []
	if zeilen and all(z.status in (STATUS_GESTELLT, STATUS_BEZAHLT) for z in zeilen):
		return PLAN_VOLL
	if any(z.rechnung for z in zeilen):
		return PLAN_TEIL
	return PLAN_OFFEN


def durchrechnen(doc, auftrag=None) -> dict | None:
	"""Engine-Ergebnis in Zeilen und Stand schreiben (ohne Speichern); ``None`` ohne Kern.

	Über der Auftragssumme lehnt die Pflege im Desk ab; der Server (``flags.cv_server``, „Abschlag
	stellen“ mit Bestätigung) darf.
	"""
	auftrag = auftrag or frappe.get_doc("Sales Order", doc.auftrag)
	doc.auftragssumme_netto = flt(auftrag.net_total, 2)
	doc.steuersatz = steuersatz(auftrag)
	doc.titel = f"{_('Abschlagsplan')} {doc.auftrag}" + (
		f" · {auftrag.customer_name}" if auftrag.customer_name else ""
	)
	schluss = schlussrechnung_zum_auftrag(doc.auftrag)
	doc.schlussrechnung = schluss.name if schluss else None
	doc.status = _plan_status(doc, schluss)
	if not kern.verfuegbar():
		return None
	ergebnis = kern.plan_rechnen(plan_eingabe(doc), auftrag_eingabe(auftrag))
	je_nr = {z["nr"]: z for z in ergebnis["zeilen"]}
	for z in doc.get("zeilen") or []:
		e = je_nr.get(cint(z.nr) or z.idx)
		if not e:
			continue
		z.netto, z.steuer, z.brutto = flt(e["netto"]), flt(e["steuer"]), flt(e["brutto"])
		z.kumuliert_prozent, z.kumuliert_netto = flt(e["kumuliert_prozent"]), flt(e["kumuliert_netto"])
	s = ergebnis["summen"]
	doc.gestellt_prozent = flt(s["gestellt_prozent"])
	doc.gestellt_netto = flt(s["gestellt_netto"])
	doc.gestellt_brutto = flt(s["gestellt_brutto"])
	doc.offen_netto = flt(s["offen_netto"])
	ueber = [w for w in ergebnis.get("warnungen") or [] if w.get("code") == UEBER_AUFTRAGSSUMME]
	if ueber and not doc.flags.cv_server:
		frappe.throw(
			_("Die Abschläge übersteigen zusammen die Auftragssumme: {0}").format(ueber[0]["meldung"]),
			frappe.ValidationError,
			title=_("Über der Auftragssumme"),
		)
	return ergebnis


def stand(doc, auftrag=None) -> dict:
	"""Stand für Dialoge und Prozessleiste: Zeilen (aus dem Plan, frisch durchgerechnet), Summen, nächste offene Zeile."""
	ergebnis = durchrechnen(doc, auftrag)
	zeilen = [
		{
			"name": z.name,
			"nr": cint(z.nr) or z.idx,
			"bezeichnung": z.bezeichnung,
			"prozent": flt(z.prozent),
			"betrag": flt(z.betrag),
			"faellig_bei": z.faellig_bei,
			"datum": z.datum,
			"status": z.status or STATUS_OFFEN,
			"rechnung": z.rechnung,
			"netto": flt(z.netto),
			"steuer": flt(z.steuer),
			"brutto": flt(z.brutto),
			"kumuliert_prozent": flt(z.kumuliert_prozent),
			"kumuliert_netto": flt(z.kumuliert_netto),
		}
		for z in doc.get("zeilen") or []
	]
	naechste = next((z for z in zeilen if z["status"] == STATUS_OFFEN and not z["rechnung"]), None)
	return {
		"plan": doc.name,
		"status": doc.status,
		"vorlage": doc.vorlage,
		"auftragssumme_netto": flt(doc.auftragssumme_netto),
		"steuersatz": flt(doc.steuersatz),
		"zeilen": zeilen,
		"gestellt_prozent": flt(doc.gestellt_prozent),
		"gestellt_netto": flt(doc.gestellt_netto),
		"gestellt_brutto": flt(doc.gestellt_brutto),
		"offen_netto": flt(doc.offen_netto),
		"schlussrechnung": doc.schlussrechnung,
		"naechste": naechste,
		"warnungen": [
			w["meldung"]
			for w in (ergebnis or {}).get("warnungen") or []
			if w.get("code") != "plan_unvollstaendig"
		],
	}


# --- Anlegen ---------------------------------------------------------------------------------


def plan_anlegen(auftrag, projekt: str | None = None, ignore_permissions: bool = False):
	e = einstellungen()
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"auftrag": auftrag.name,
			"projekt": projekt,
			"vorlage": e.abschlagsplan_vorlage,
			"zeilen": [
				{
					"nr": i,
					"bezeichnung": z["bezeichnung"],
					"prozent": z["prozent"],
					"faellig_bei": z["faellig_bei"],
					"status": STATUS_OFFEN,
				}
				for i, z in enumerate(vorlage_zeilen(e), start=1)
			],
		}
	)
	doc.flags.cv_server = True
	doc.flags.ignore_permissions = ignore_permissions
	doc.insert()
	return doc


def plan_sicherstellen(auftrag, projekt: str | None, ignore_permissions: bool = False) -> tuple[str, bool]:
	vorhanden = plan_zum_auftrag(auftrag.name)
	if vorhanden:
		return vorhanden, False
	return plan_anlegen(auftrag, projekt, ignore_permissions).name, True


def _automatisch(sales_order: str | None, projekt: str | None) -> None:
	"""Plan aus der Vorlage, wenn eingestellt und noch keiner da; Fehler halten den Aufrufer nicht auf."""
	if not sales_order or not projekt or not doctype_da() or not felder_da():
		return
	e = einstellungen()
	if not e.abschlagsplan_automatisch or plan_zum_auftrag(sales_order):
		return
	auftrag = frappe.get_doc("Sales Order", sales_order)
	if auftrag.docstatus == 2 or (
		e.abschlag_mindestsumme and flt(auftrag.net_total) < e.abschlag_mindestsumme
	):
		return
	savepoint = "cv_abschlagsplan"
	frappe.db.savepoint(savepoint)
	try:
		plan_anlegen(auftrag, projekt, ignore_permissions=True)
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(
			title=_("Abschlagsplan zu {0} nicht angelegt").format(sales_order),
			reference_doctype="Sales Order",
			reference_name=sales_order,
		)
		frappe.msgprint(
			_(
				"Der Abschlagsplan zu Auftrag {0} konnte nicht angelegt werden - er entsteht beim ersten „Abschlag stellen“."
			).format(sales_order),
			indicator="orange",
			alert=True,
		)


def projekt_angelegt(doc, method=None) -> None:
	"""``Project.after_insert``: Abschlagsplan zum Auftrag des Projekts (Annahme 4.4)."""
	_automatisch(doc.get("sales_order"), doc.name)


def auftrag_eingereicht(doc, method=None) -> None:
	"""``Sales Order.on_submit`` (nach 4.4/5.1): Abschlagsplan, wenn der Auftrag ein Projekt hat."""
	projekt = doc.get("project") or frappe.db.get_value(
		"Project", {"sales_order": doc.name, "status": ["!=", "Cancelled"]}, "name"
	)
	_automatisch(doc.name, projekt)


# --- Stand aus den Rechnungen ----------------------------------------------------------------


def aktualisieren(name: str, ohne_rechnung: str | None = None):
	"""Status, Rechnung und Beträge der Zeilen aus den verknüpften Rechnungen; speichert den Plan."""
	doc = frappe.get_doc(DOCTYPE, name)
	for z in doc.get("zeilen") or []:
		if not z.rechnung:
			continue
		if z.rechnung == ohne_rechnung or not frappe.db.exists("Sales Invoice", z.rechnung):
			_zeile_offen(z)
			continue
		r = frappe.db.get_value(
			"Sales Invoice",
			z.rechnung,
			[
				"docstatus",
				"status",
				"posting_date",
				"net_total",
				"total_taxes_and_charges",
				"grand_total",
				"outstanding_amount",
			],
			as_dict=True,
		)
		if r.docstatus == 2:
			_zeile_offen(z)
			continue
		z.status = (
			STATUS_ENTWURF if r.docstatus == 0 else (STATUS_BEZAHLT if ist_bezahlt(r) else STATUS_GESTELLT)
		)
		z.datum = getdate(r.posting_date)
		z.netto, z.steuer, z.brutto = flt(r.net_total), flt(r.total_taxes_and_charges), flt(r.grand_total)
	doc.flags.cv_server = True
	doc.flags.ignore_permissions = True
	doc.save()
	return doc


def _zeile_offen(z) -> None:
	z.status = STATUS_OFFEN
	z.rechnung = None
	z.netto = z.steuer = z.brutto = None
	if z.faellig_bei != "Datum":
		z.datum = None


def aktualisieren_zum_auftrag(sales_order: str):
	name = plan_zum_auftrag(sales_order)
	return aktualisieren(name) if name else None
