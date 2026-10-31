"""Script Report „Mengenregeln nachjustieren“ (Phase 2, P2.1, Lernschleife): je Artikel und Mengenregel
die Angebotsmenge gegen die Ist-Menge der freigegebenen Schlussrechnungen (Mengenfaktor) und je
Verfahren Soll- gegen Ist-Minuten je Einheit (Zeitfaktor), mit Vorschlag. Nur Anzeige.
Daten: ``corevatis_erpnext.nachkalkulation.regeln``."""

import frappe
from frappe import _

from corevatis_erpnext.nachkalkulation import regeln


def execute(filters=None):
	daten = regeln(filters or {})
	return columns(), daten["zeilen"], message(daten), None, summary(daten)


def columns() -> list[dict]:
	return [
		{
			"fieldname": "item_code",
			"label": _("Leistung"),
			"fieldtype": "Link",
			"options": "Item",
			"width": 110,
		},
		{"fieldname": "bezeichnung", "label": _("Bezeichnung"), "fieldtype": "Data", "width": 220},
		{"fieldname": "verfahren", "label": _("Verfahren"), "fieldtype": "Data", "width": 150},
		{"fieldname": "regel", "label": _("Mengenregel"), "fieldtype": "Data", "width": 140},
		{"fieldname": "regelart", "label": _("Regelart"), "fieldtype": "Data", "width": 130},
		{"fieldname": "projekte", "label": _("Projekte"), "fieldtype": "Int", "width": 75},
		{
			"fieldname": "soll_menge",
			"label": _("Menge Angebot"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 110,
		},
		{
			"fieldname": "ist_menge",
			"label": _("Menge Ist"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 95,
		},
		{"fieldname": "einheit", "label": _("Einheit"), "fieldtype": "Data", "width": 65},
		{
			"fieldname": "mengenfaktor",
			"label": _("Mengenfaktor"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 100,
		},
		{
			"fieldname": "soll_min_je_einheit",
			"label": _("Soll min/Einh."),
			"fieldtype": "Float",
			"precision": 1,
			"width": 105,
		},
		{
			"fieldname": "ist_min_je_einheit",
			"label": _("Ist min/Einh."),
			"fieldtype": "Float",
			"precision": 1,
			"width": 100,
		},
		{
			"fieldname": "zeitfaktor",
			"label": _("Zeitfaktor"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 85,
		},
		{"fieldname": "vorschlag", "label": _("Vorschlag"), "fieldtype": "Data", "width": 420},
	]


def message(daten: dict) -> str | None:
	hinweise = daten.get("hinweise") or []
	text = _(
		"Grundlage: freigegebene Schlussrechnungen (Ist-Mengen) und die Kalkulation der angenommenen Angebote. "
		"Vorschläge sind Hinweise für die Pflege der Leistungsregeln und Stücklisten; es wird nichts geändert."
	)
	return "<br>".join([text] + [frappe.utils.escape_html(h) for h in hinweise[:20]])


def summary(daten: dict) -> list[dict]:
	zeilen = daten.get("zeilen") or []
	if not zeilen:
		return []
	abweichend = [z for z in zeilen if z["vorschlag"] != _("passt")]
	return [
		{"value": len(zeilen), "label": _("Leistungen"), "datatype": "Int"},
		{
			"value": len(abweichend),
			"label": _("mit Vorschlag"),
			"datatype": "Int",
			"indicator": "Orange" if abweichend else "Green",
		},
	]
