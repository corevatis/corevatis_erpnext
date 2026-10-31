"""Script Report „Nachkalkulation“ (Phase 2, P2.1): je Projekt eine Summenzeile, darunter die Verfahren
(Arbeitspakete) mit Soll- und Ist-Stunden, Material, Lohn, Erlös, Marge und Ampel.
Daten: ``corevatis_erpnext.nachkalkulation.uebersicht`` (Rolle Büro/Meister, Leserecht am Projekt)."""

import frappe
from frappe import _
from frappe.utils import flt

from corevatis_erpnext.nachkalkulation import GELB, GRUEN, ROT, uebersicht


def execute(filters=None):
	daten = uebersicht(filters or {})
	return columns(), daten["zeilen"], message(daten), None, summary(daten)


def columns() -> list[dict]:
	geld = {"fieldtype": "Currency", "options": "currency", "width": 110}
	return [
		{"fieldname": "bezeichnung", "label": _("Projekt / Verfahren"), "fieldtype": "Data", "width": 260},
		{
			"fieldname": "projekt",
			"label": _("Projekt"),
			"fieldtype": "Link",
			"options": "Project",
			"width": 110,
		},
		{
			"fieldname": "arbeitspaket",
			"label": _("Arbeitspaket"),
			"fieldtype": "Link",
			"options": "Task",
			"width": 120,
		},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "ampel", "label": _("Ampel"), "fieldtype": "Data", "width": 70},
		{"fieldname": "soll_h", "label": _("Soll h"), "fieldtype": "Float", "precision": 2, "width": 75},
		{"fieldname": "ist_h", "label": _("Ist h"), "fieldtype": "Float", "precision": 2, "width": 75},
		{
			"fieldname": "fahrt_h",
			"label": _("davon Fahrt h"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 90,
		},
		{"fieldname": "verbrauch", "label": _("Verbrauch %"), "fieldtype": "Percent", "width": 95},
		{"fieldname": "fortschritt", "label": _("Fortschritt %"), "fieldtype": "Percent", "width": 95},
		{"fieldname": "soll_lohn", "label": _("Lohn Soll"), **geld},
		{"fieldname": "ist_lohn", "label": _("Lohn Ist"), **geld},
		{"fieldname": "soll_material", "label": _("Material Soll"), **geld},
		{"fieldname": "ist_material", "label": _("Material Ist"), **geld},
		{"fieldname": "erloes", "label": _("Erlös netto"), **geld},
		{"fieldname": "marge_soll", "label": _("Marge Soll"), **geld},
		{"fieldname": "marge_ist", "label": _("Marge Ist"), **geld},
		{"fieldname": "marge_prozent", "label": _("Marge Ist %"), "fieldtype": "Percent", "width": 90},
		{"fieldname": "hinweis", "label": _("Hinweis"), "fieldtype": "Data", "width": 260},
	]


def message(daten: dict) -> str | None:
	hinweise = daten.get("hinweise") or []
	if not hinweise:
		return None
	return "<br>".join(frappe.utils.escape_html(h) for h in hinweise[:20])


def summary(daten: dict) -> list[dict]:
	auswertungen = daten.get("auswertungen") or []
	if not auswertungen:
		return []
	soll = flt(sum(a["summe"]["soll_h"] for a in auswertungen), 1)
	ist = flt(sum(a["summe"]["ist_h"] for a in auswertungen), 1)
	marge = flt(sum(a["summe"]["marge_ist"] for a in auswertungen), 2)
	erloes = flt(sum(a["summe"]["erloes"] for a in auswertungen), 2)
	farben = [a["summe"]["ampel"] for a in auswertungen]
	waehrung = auswertungen[0]["summe"].get("currency")
	return [
		{"value": len(auswertungen), "label": _("Projekte"), "datatype": "Int"},
		{"value": soll, "label": _("Soll h"), "datatype": "Float"},
		{
			"value": ist,
			"label": _("Ist h"),
			"datatype": "Float",
			"indicator": "Red"
			if soll and ist > soll * 1.15
			else "Orange"
			if soll and ist > soll
			else "Green",
		},
		{"value": marge, "label": _("Marge Ist"), "datatype": "Currency", "currency": waehrung},
		{
			"value": flt(marge / erloes * 100, 1) if erloes else 0,
			"label": _("Marge Ist %"),
			"datatype": "Percent",
			"indicator": "Green" if erloes and marge > 0 else "Red",
		},
		{
			"value": _("{0} rot, {1} gelb, {2} grün").format(
				farben.count(ROT), farben.count(GELB), farben.count(GRUEN)
			),
			"label": _("Ampel"),
			"datatype": "Data",
			"indicator": "Red" if ROT in farben else "Orange" if GELB in farben else "Green",
		},
	]
