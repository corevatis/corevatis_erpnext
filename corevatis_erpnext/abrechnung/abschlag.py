"""Abschlagsrechnung stellen (7.1): nächste Zeile des Abschlagsplans → Sales Invoice im Entwurf.

Ablauf ``abschlag_stellen``: Rolle Büro, Anlegerecht Rechnung, Auftrag sperren und lesen, Plan
sicherstellen (aus der Vorlage, falls keiner da), Zeile wählen (nächste offene, oder die genannte),
Dialogwerte (Prozent oder Betrag, Text, Fälligkeit, Datum) in die Zeile übernehmen, Engine rechnen
lassen (Kern), Rechnung anlegen (Kopf aus dem Auftrag wie ERPNext „Erstellen → Rechnung“, eine
Service-Position „Abschlagsrechnung Nr. 1 zum Auftrag AB-… (30 % der Auftragssumme)“, Steuer wie der
Auftrag, Zahlungsbedingungen vom Auftrag), Planzeile auf „Entwurf“ mit Rechnung. Freigeben
(„Rechnung freigeben“ = Submit) macht das Büro im Formular; danach zählt der Abschlag am Auftrag
(``auftrag.abrechnung_aktualisieren``).

Sicherheit: Beträge kommen nur aus Auftragssumme x Prozent bzw. dem Betrag der Zeile (Server);
über die Auftragssumme hinaus nur mit ausdrücklicher Bestätigung (``bestaetigen=1``) und Hinweis.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, format_date, getdate

from corevatis_erpnext.abrechnung import kern, plan
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
from corevatis_erpnext.abrechnung.felder import ART_ABSCHLAG
from corevatis_erpnext.abrechnung.kern import AbrechnungFehler


def ensure_abschlag_artikel() -> str:
	"""Service-Artikel für Abschlagszeilen und Abzüge (Einstellung, Standard ``ABSCHLAG``)."""
	code = einstellungen().abschlag_artikel
	if frappe.db.exists("Item", code):
		return code
	gruppe = frappe.db.get_value(
		"Item Group", {"item_group_name": "Allgemeine Leistungen", "is_group": 0}
	) or frappe.db.get_value("Item Group", {"is_group": 0}, order_by="name asc")
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": code,
			"item_name": "Abschlagsrechnung",
			"description": "Abschlagsrechnung zum Auftrag bzw. Abzug einer Abschlagsrechnung (Corevatis Abrechnung)",
			"item_group": gruppe,
			"stock_uom": "psch" if frappe.db.exists("UOM", "psch") else "Nos",
			"is_stock_item": 0,
			"is_sales_item": 1,
			"is_purchase_item": 0,
			"include_item_in_manufacturing": 0,
		}
	).insert(ignore_permissions=True)
	return code


def rechnung_kopf(auftrag):
	"""Rechnungskopf wie ERPNext „Erstellen → Rechnung“ (Kunde, Anschrift, Steuern, Zahlungsbedingungen,
	Projekt), ohne Positionen; Zahlungsplan neu aus den Bedingungen (D12)."""
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	si = make_sales_invoice(auftrag.name, args={"skip_item_mapping": 1}, ignore_permissions=True)
	si.set("items", [])
	si.set("payment_schedule", [])
	si.due_date = None
	si.update_stock = 0
	si.project = auftrag.get("project")
	si.flags.ignore_permissions = False
	return si


def _plan_doc(auftrag, projekt):
	name, _neu = plan.plan_sicherstellen(auftrag, projekt, ignore_permissions=False)
	plan.aktualisieren(name)
	return frappe.get_doc(plan.DOCTYPE, name)


def _zeile_waehlen(doc, zeile, prozent, betrag, bezeichnung, faellig_bei):
	offen = [z for z in doc.zeilen if z.status == plan.STATUS_OFFEN and not z.rechnung]
	if zeile:
		treffer = [z for z in offen if z.name == zeile or str(cint(z.nr)) == str(zeile)]
		if not treffer:
			frappe.throw(
				_("Zeile {0} des Abschlagsplans ist nicht offen.").format(
					frappe.utils.escape_html(str(zeile))
				),
				AbrechnungFehler,
			)
		return treffer[0]
	if offen:
		return offen[0]
	if prozent in (None, "") and betrag in (None, ""):
		frappe.throw(
			_(
				"Alle Zeilen des Abschlagsplans sind gestellt. Für einen weiteren Abschlag bitte Prozent oder Betrag angeben."
			),
			AbrechnungFehler,
		)
	return doc.append(
		"zeilen",
		{
			"bezeichnung": bezeichnung or _("Weiterer Abschlag"),
			"faellig_bei": faellig_bei or "bei Baufortschritt",
			"status": plan.STATUS_OFFEN,
		},
	)


def _beschreibung(text: str, z) -> str:
	teile = [frappe.utils.escape_html(text)]
	if z.bezeichnung and z.bezeichnung.strip().lower() not in text.lower():
		teile.append(frappe.utils.escape_html(z.bezeichnung))
	if z.faellig_bei and z.faellig_bei != "Datum":
		teile.append(_("fällig {0}").format(z.faellig_bei))
	elif z.datum:
		teile.append(_("fällig am {0}").format(format_date(z.datum)))
	return "<br>".join(teile)


def abschlaege_uebersicht(sales_order: str, ausser: str | None = None) -> list[dict]:
	"""Zeilen für ``Sales Invoice.cv_abschlaege``: freigegebene Abschlagsrechnungen (Druck #50)."""
	zeilen = []
	for i, r in enumerate(abschlaege_gestellt(sales_order), start=1):
		if r.name == ausser:
			continue
		zeilen.append(
			{
				"nr": cint(r.cv_abschlag_nr) or i,
				"rechnung": r.name,
				"datum": r.posting_date,
				"bezeichnung": _("Abschlagsrechnung Nr. {0} ({1} %)").format(
					cint(r.cv_abschlag_nr) or i, flt(r.cv_abschlag_prozent, 2)
				),
				"netto": flt(r.net_total),
				"steuer": flt(r.total_taxes_and_charges),
				"brutto": flt(r.grand_total),
				"bezahlt": 1 if ist_bezahlt(r) else 0,
			}
		)
	return zeilen


def _rechnung_anlegen(auftrag, projekt, doc, z, ergebnis):
	si = rechnung_kopf(auftrag)
	artikel = ensure_abschlag_artikel()
	nr = cint(ergebnis["nr"])
	si.append(
		"items",
		{
			"item_code": artikel,
			"qty": 1,
			"rate": flt(ergebnis["netto"]),
			"sales_order": auftrag.name,
			"project": projekt if not auftrag.get("project") else None,
			"cv_positionsart": ART_ABSCHLAG,
		},
	)
	si.set_missing_values()
	zeile = si.items[0]
	zeile.item_name = _("Abschlagsrechnung Nr. {0}").format(nr)
	zeile.description = _beschreibung(ergebnis["text"], z)
	zeile.rate = flt(ergebnis["netto"])
	zeile.price_list_rate = 0
	zeile.discount_percentage = 0
	zeile.discount_amount = 0
	if ergebnis.get("lohnanteil"):
		zeile.cv_lohnanteil = flt(ergebnis["lohnanteil"]["netto"])
	si.cv_abschlag = 1
	si.cv_schlussrechnung = 0
	si.cv_auftrag = auftrag.name
	si.cv_abschlagsplan = doc.name
	si.cv_abschlag_nr = nr
	si.cv_abschlag_prozent = flt(ergebnis["prozent"])
	si.cv_abschlag_zeile = z.name
	si.cv_auftragssumme_netto = flt(auftrag.net_total, 2)
	si.cv_kumuliert_prozent = flt(ergebnis["kumuliert"]["prozent"])
	si.cv_kumuliert_netto = flt(ergebnis["kumuliert"]["netto"])
	if ergebnis.get("lohnanteil"):
		si.cv_lohnanteil_netto = flt(ergebnis["lohnanteil"]["netto"])
		si.cv_lohnanteil_steuer = flt(ergebnis["lohnanteil"]["steuer"])
		si.cv_lohnanteil_brutto = flt(ergebnis["lohnanteil"]["brutto"])
	si.set("cv_abschlaege", abschlaege_uebersicht(auftrag.name))
	si.cv_abrechnung_json = json.dumps(ergebnis, ensure_ascii=False)
	si.insert()
	return si


@frappe.whitelist()
def abschlag_vorschlag(sales_order: str | None = None, project: str | None = None) -> dict:
	"""Vorschlag für den Dialog „Abschlag stellen“: Auftragssumme, Plan mit Zeilen, nächste Zeile,
	bisher gestellte Abschläge (Leserecht Auftrag; Rolle Büro)."""
	pruefe_rolle()
	auftrag, projekt = auftrag_laden(sales_order, project, sperren=False)
	name = plan.plan_zum_auftrag(auftrag.name)
	st = None
	vorlage = None
	if name:
		st = plan.stand(frappe.get_doc(plan.DOCTYPE, name), auftrag)
	else:
		e = einstellungen()
		from corevatis_erpnext.abrechnung.einstellungen import vorlage_zeilen

		vorlage = {"name": e.abschlagsplan_vorlage, "zeilen": vorlage_zeilen(e)}
	schluss = schlussrechnung_zum_auftrag(auftrag.name)
	rechnungen = rechnungen_zum_auftrag(auftrag.name, art="abschlag")
	satz = steuersatz(auftrag)
	return {
		"auftrag": {
			"name": auftrag.name,
			"kunde": auftrag.customer_name,
			"netto": flt(auftrag.net_total, 2),
			"steuersatz": satz,
			"brutto": flt(auftrag.grand_total, 2),
			"datum": auftrag.transaction_date,
		},
		"projekt": projekt,
		"plan": st,
		"vorlage": vorlage,
		"naechste": (st or {}).get("naechste"),
		"abschlaege": [
			{
				"name": r.name,
				"nr": cint(r.cv_abschlag_nr),
				"prozent": flt(r.cv_abschlag_prozent),
				"docstatus": r.docstatus,
				"status": r.status,
				"datum": r.posting_date,
				"netto": flt(r.net_total),
				"brutto": flt(r.grand_total),
				"bezahlt": ist_bezahlt(r),
			}
			for r in rechnungen
		],
		"schlussrechnung": {"name": schluss.name, "docstatus": schluss.docstatus} if schluss else None,
		"faellig_bei": list(einstellungen_faellig()),
	}


def einstellungen_faellig() -> tuple:
	from corevatis_erpnext.abrechnung.einstellungen import FAELLIG_BEI

	return FAELLIG_BEI


@frappe.whitelist(methods=["POST"])
def abschlag_stellen(
	sales_order: str | None = None,
	project: str | None = None,
	zeile: str | None = None,
	prozent=None,
	betrag=None,
	bezeichnung: str | None = None,
	faellig_bei: str | None = None,
	datum: str | None = None,
	bestaetigen=0,
) -> dict:
	"""Abschlagsrechnung (Entwurf) zur nächsten bzw. genannten Zeile des Abschlagsplans anlegen."""
	pruefe_rolle()
	frappe.has_permission("Sales Invoice", "create", throw=True)
	auftrag, projekt = auftrag_laden(sales_order, project)
	schluss = schlussrechnung_zum_auftrag(auftrag.name)
	if schluss:
		frappe.throw(
			_("Zum Auftrag {0} gibt es schon die Schlussrechnung {1} - keine weiteren Abschläge.").format(
				auftrag.name, schluss.name
			),
			AbrechnungFehler,
		)
	if prozent not in (None, "") and betrag not in (None, ""):
		frappe.throw(
			_("Bitte entweder Prozent oder Betrag des Abschlags angeben, nicht beides."),
			frappe.MandatoryError,
		)
	doc = _plan_doc(auftrag, projekt)
	z = _zeile_waehlen(doc, zeile, prozent, betrag, bezeichnung, faellig_bei)
	if prozent not in (None, ""):
		z.prozent, z.betrag = flt(prozent), 0
	elif betrag not in (None, ""):
		z.betrag, z.prozent = flt(betrag), 0
	if bezeichnung:
		z.bezeichnung = bezeichnung
	if faellig_bei:
		z.faellig_bei = faellig_bei
	if datum:
		z.datum = getdate(datum)
	if (flt(z.prozent) > 0) == (flt(z.betrag) > 0):
		frappe.throw(_("Bitte entweder Prozent oder Betrag des Abschlags angeben."), frappe.MandatoryError)
	if flt(z.prozent) > 100 or flt(z.prozent) < 0 or flt(z.betrag) < 0:
		frappe.throw(_("Prozent zwischen 0 und 100, Betrag nicht negativ."), frappe.ValidationError)
	# Zeile (ggf. neu) speichern, damit sie einen Namen hat; über der Auftragssumme nur mit Bestätigung
	doc.flags.cv_server = True
	doc.save(ignore_permissions=True)
	z = next(x for x in doc.zeilen if x.idx == z.idx)

	from corevatis_erpnext.abrechnung.schluss import lohnanteil_des_auftrags

	lohn_auftrag = lohnanteil_des_auftrags(auftrag)
	bereits = [str(flt(r.net_total, 2)) for r in abschlaege_gestellt(auftrag.name)]
	entwuerfe = [r.name for r in rechnungen_zum_auftrag(auftrag.name, docstatus=0, art="abschlag")]
	ergebnis = kern.abschlag_rechnen(
		auftrag_eingabe(auftrag, lohn_auftrag),
		cint(z.nr) or z.idx,
		prozent=str(flt(z.prozent)) if flt(z.prozent) > 0 else None,
		betrag=str(flt(z.betrag, 2)) if flt(z.betrag) > 0 else None,
		bereits_gestellt=bereits,
		bezeichnung=z.bezeichnung,
	)
	warnungen = [w["meldung"] for w in ergebnis.get("warnungen") or []]
	ueber = [w["meldung"] for w in ergebnis.get("warnungen") or [] if w.get("code") == "ueber_auftragssumme"]
	# Entwürfe zählen für die Engine nicht als gestellt - für die Prüfung „über der Auftragssumme“ schon
	# (Security-Review: sonst ließe sich die Bestätigung mit mehreren Entwürfen umgehen)
	entwurf_netto = sum(
		flt(r.net_total) for r in rechnungen_zum_auftrag(auftrag.name, docstatus=0, art="abschlag")
	)
	mit_entwuerfen = flt(ergebnis["kumuliert"]["netto"]) + entwurf_netto
	if entwurf_netto and mit_entwuerfen > flt(auftrag.net_total, 2) + 0.005:
		ueber.append(
			_("Mit den Entwürfen ({0}) sind {1} netto gestellt - mehr als die Auftragssumme {2}").format(
				", ".join(entwuerfe),
				frappe.format_value(mit_entwuerfen, {"fieldtype": "Currency"}),
				frappe.format_value(flt(auftrag.net_total, 2), {"fieldtype": "Currency"}),
			)
		)
	if ueber and not cint(bestaetigen):
		frappe.throw(
			_(
				"{0}. Bitte Prozent oder Betrag anpassen - oder den Abschlag über der Auftragssumme ausdrücklich bestätigen."
			).format(ueber[0]),
			AbrechnungFehler,
			title=_("Über der Auftragssumme"),
		)
	warnungen += [u for u in ueber if u not in warnungen]
	if entwuerfe:
		warnungen.append(
			_(
				"Abschlagsrechnung {0} ist noch im Entwurf - bitte freigeben oder löschen, sonst zählt sie nicht."
			).format(", ".join(entwuerfe))
		)
	si = _rechnung_anlegen(auftrag, projekt, doc, z, ergebnis)
	z.status = plan.STATUS_ENTWURF
	z.rechnung = si.name
	z.datum = getdate(si.posting_date)
	z.netto, z.steuer, z.brutto = flt(si.net_total), flt(si.total_taxes_and_charges), flt(si.grand_total)
	doc.flags.cv_server = True
	doc.save(ignore_permissions=True)
	return {
		"rechnung": si.name,
		"plan": doc.name,
		"zeile": z.name,
		"nr": cint(z.nr),
		"prozent": flt(ergebnis["prozent"]),
		"text": ergebnis["text"],
		"netto": flt(si.net_total),
		"steuer": flt(si.total_taxes_and_charges),
		"brutto": flt(si.grand_total),
		"kumuliert": ergebnis["kumuliert"],
		"rest_netto": flt(ergebnis["rest"]["netto"]),
		"lohnanteil": ergebnis.get("lohnanteil"),
		"warnungen": warnungen,
	}
