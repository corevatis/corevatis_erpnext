"""Script Report „Zahlungseingang“ (7.5): eingelesene Gutschriften des Kontoauszugs mit
Zuordnungsvorschlag (Rechnung, Kunde, Sicherheit, Gründe). Daten:
``corevatis_erpnext.abrechnung.zahlungseingang.vorschlaege`` (Rolle Büro); Knöpfe „Kontoauszug
einlesen“, „Zuordnen“ und „Ignorieren“ in ``zahlungseingang.js``."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from corevatis_erpnext.abrechnung.zahlungseingang import vorschlaege


def execute(filters=None):
	if isinstance(filters, str):
		filters = frappe.parse_json(filters) if filters else {}
	f = frappe._dict(filters or {})
	zeilen = []
	for v in vorschlaege(bankkonto_name=f.get("bankkonto"), mit_ignorierten=cint(f.get("mit_ignorierten"))):
		b = v["bester"] or {}
		zeilen.append(
			{
				"buchung": v["name"],
				"datum": v["datum"],
				"betrag": v["betrag"],
				"zahler": v["zahler"],
				"verwendungszweck": v["verwendungszweck"],
				"rechnung": b.get("rechnung"),
				"kunde_name": b.get("kunde_name"),
				"offen": b.get("offen"),
				"sicherheit": _(b["sicherheit"]) if b else "",
				"gruende": "; ".join(b.get("gruende") or []),
				"weitere": max(len(v["kandidaten"]) - 1, 0),
				"ignoriert": v["ignoriert"],
				"notiz": v["notiz"],
				"aktion": "",
				"currency": v["currency"],
				"vorgang": b.get("projekt"),
			}
		)
	return columns(), zeilen, None, None, summary(zeilen)


def columns() -> list[dict]:
	return [
		{
			"fieldname": "buchung",
			"label": _("Buchung"),
			"fieldtype": "Link",
			"options": "Bank Transaction",
			"width": 150,
		},
		{"fieldname": "datum", "label": _("Datum"), "fieldtype": "Date", "width": 100},
		{
			"fieldname": "betrag",
			"label": _("Betrag"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 110,
		},
		{"fieldname": "zahler", "label": _("Zahler"), "fieldtype": "Data", "width": 170},
		{"fieldname": "verwendungszweck", "label": _("Verwendungszweck"), "fieldtype": "Data", "width": 280},
		{
			"fieldname": "rechnung",
			"label": _("Vorschlag Rechnung"),
			"fieldtype": "Link",
			"options": "Sales Invoice",
			"width": 150,
		},
		{"fieldname": "kunde_name", "label": _("Kunde"), "fieldtype": "Data", "width": 160},
		{
			"fieldname": "offen",
			"label": _("Offen (Rechnung)"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{"fieldname": "sicherheit", "label": _("Sicherheit"), "fieldtype": "Data", "width": 110},
		{"fieldname": "aktion", "label": _("Aktion"), "fieldtype": "Data", "width": 170},
		{"fieldname": "gruende", "label": _("Gründe"), "fieldtype": "Data", "width": 300},
		{"fieldname": "weitere", "label": _("Weitere Treffer"), "fieldtype": "Int", "width": 90},
		{
			"fieldname": "vorgang",
			"label": _("Vorgang"),
			"fieldtype": "Link",
			"options": "Project",
			"width": 140,
		},
		{"fieldname": "notiz", "label": _("Notiz"), "fieldtype": "Data", "width": 160},
		{"fieldname": "ignoriert", "label": _("Ignoriert"), "fieldtype": "Check", "width": 70},
		{
			"fieldname": "currency",
			"label": _("Währung"),
			"fieldtype": "Link",
			"options": "Currency",
			"width": 60,
			"hidden": 1,
		},
	]


def summary(zeilen: list[dict]) -> list[dict]:
	offen = [z for z in zeilen if not z["ignoriert"]]
	return [
		{"value": len(offen), "label": _("offene Gutschriften"), "datatype": "Int", "indicator": "Blue"},
		{
			"value": sum(flt(z["betrag"]) for z in offen),
			"label": _("Summe"),
			"datatype": "Currency",
			"indicator": "Blue",
		},
		{
			"value": sum(1 for z in offen if z["rechnung"]),
			"label": _("mit Vorschlag"),
			"datatype": "Int",
			"indicator": "Green",
		},
		{
			"value": sum(1 for z in offen if not z["rechnung"]),
			"label": _("ohne Vorschlag"),
			"datatype": "Int",
			"indicator": "Orange",
		},
	]
