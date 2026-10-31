"""Rechnungsformular verständlich (wie Angebot D8): Zustandstext oben (Herkunft, nächster Schritt),
ein Hauptknopf „Rechnung freigeben“ im Entwurf; ERPNext-Ballast blendet ``public/js/abrechnung.js`` aus.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money, format_date

from corevatis_erpnext.abrechnung import plan
from corevatis_erpnext.abrechnung.auftrag import RECHNUNG_FELDER, darf_abrechnen, felder_da, ist_bezahlt

ART_ABSCHLAG, ART_SCHLUSS, ART_RECHNUNG = "abschlag", "schlussrechnung", "rechnung"


def art_der_rechnung(r) -> str:
	if cint(r.get("cv_abschlag")):
		return ART_ABSCHLAG
	if cint(r.get("cv_schlussrechnung")):
		return ART_SCHLUSS
	return ART_RECHNUNG


def geld(wert, currency: str | None) -> str:
	return fmt_money(flt(wert), currency=currency or None)


def naechste_zeile_text(auftrag: str | None, currency: str | None) -> str | None:
	"""„Nächster Abschlag: Nr. 2, 40 % bei Baufortschritt (1.908,34 €)“ aus dem Plan, sonst ``None``."""
	name = plan.plan_zum_auftrag(auftrag)
	if not name:
		return None
	doc = frappe.get_doc(plan.DOCTYPE, name)
	if doc.schlussrechnung:
		return _("Schlussrechnung {0} liegt vor.").format(doc.schlussrechnung)
	z = next((z for z in doc.zeilen if z.status == plan.STATUS_OFFEN and not z.rechnung), None)
	if not z:
		return _("Alle Abschläge sind gestellt - nach der Abnahme folgt die Schlussrechnung.")
	betrag = f" ({geld(z.brutto, currency)} brutto)" if flt(z.brutto) else ""
	anteil = f"{flt(z.prozent, 2):g} %" if flt(z.prozent) else geld(z.betrag, currency)
	return _("Nächster Abschlag: Nr. {0}, {1} {2}{3}.").format(
		cint(z.nr), anteil, z.faellig_bei or "", betrag
	)


@frappe.whitelist()
def zustand(sales_invoice: str) -> dict:
	"""Zustandstext und Hauptknopf für das Rechnungsformular (Leserecht Rechnung)."""
	frappe.has_permission("Sales Invoice", "read", sales_invoice, throw=True)
	felder = (
		[*RECHNUNG_FELDER, "currency", "customer_name", "project"]
		if felder_da()
		else [
			"name",
			"docstatus",
			"status",
			"posting_date",
			"grand_total",
			"outstanding_amount",
			"currency",
			"customer_name",
			"project",
			"is_return",
		]
	)
	r = frappe.db.get_value("Sales Invoice", sales_invoice, felder, as_dict=True)
	if not r:
		frappe.throw(_("Rechnung {0} nicht gefunden.").format(sales_invoice), frappe.DoesNotExistError)
	art = art_der_rechnung(r)
	ergebnis = {
		"art": art,
		"auftrag": r.get("cv_auftrag"),
		"projekt": r.get("project"),
		"plan": r.get("cv_abschlagsplan"),
		"darf_abrechnen": darf_abrechnen(),
		"text": None,
		"farbe": "blue",
		"aktion": None,
	}
	if art == ART_RECHNUNG or cint(r.get("is_return")):
		return ergebnis
	if art == ART_ABSCHLAG:
		herkunft = _("Abschlagsrechnung Nr. {0} ({1} %) zum Auftrag {2}").format(
			cint(r.cv_abschlag_nr), f"{flt(r.cv_abschlag_prozent, 2):g}", r.cv_auftrag
		)
	else:
		anzahl = frappe.db.count("Rechnung Abschlag", {"parent": r.name, "parenttype": "Sales Invoice"})
		herkunft = _("Schlussrechnung zum Auftrag {0}").format(r.cv_auftrag)
		if anzahl:
			herkunft += " " + _("abzüglich {0} Abschlagsrechnung(en)").format(anzahl)
	if r.docstatus == 0:
		ergebnis["text"] = (
			herkunft
			+ " "
			+ _(
				"- Entwurf. Positionen und Beträge prüfen, dann oben „Rechnung freigeben“. Danach das PDF an den Kunden schicken."
			)
		)
		ergebnis["farbe"] = "orange"
		if frappe.has_permission("Sales Invoice", "submit", sales_invoice):
			ergebnis["aktion"] = {"key": "freigeben", "label": _("Rechnung freigeben")}
	elif r.docstatus == 1 and ist_bezahlt(r):
		ergebnis["text"] = (
			herkunft + " " + _("- freigegeben am {0}, bezahlt.").format(format_date(r.posting_date))
		)
		ergebnis["farbe"] = "green"
	elif r.docstatus == 1:
		ergebnis["text"] = (
			herkunft
			+ " "
			+ _("- freigegeben am {0}, offen {1}.").format(
				format_date(r.posting_date), geld(r.outstanding_amount, r.currency)
			)
		)
		if art == ART_ABSCHLAG:
			naechste = naechste_zeile_text(r.cv_auftrag, r.currency)
			if naechste:
				ergebnis["text"] += " " + naechste
	else:
		ergebnis["text"] = herkunft + " " + _("- storniert.")
		ergebnis["farbe"] = "red"
	return ergebnis
