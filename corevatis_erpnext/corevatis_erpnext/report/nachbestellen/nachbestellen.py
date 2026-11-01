"""Script Report „Nachbestellen“ (Phase 2, P2.2): Artikel mit Mindestbestand je Lager, Bestand, verfügbare
Menge, Nachbestellmenge, Art (Bestellung oder Umlagerung) und Lieferant. Daten:
``corevatis_erpnext.materialwirtschaft.nachbestellen`` (Rollen Büro/Meister)."""

import frappe
from frappe import _

from corevatis_erpnext.materialwirtschaft import nachbestellen


def execute(filters=None):
	daten = nachbestellen(filters or {})
	return columns(), daten["zeilen"], message(daten), None, summary(daten)


def columns() -> list[dict]:
	return [
		{
			"fieldname": "item_code",
			"label": _("Artikel"),
			"fieldtype": "Link",
			"options": "Item",
			"width": 140,
		},
		{"fieldname": "item_name", "label": _("Bezeichnung"), "fieldtype": "Data", "width": 220},
		{
			"fieldname": "lager",
			"label": _("Lager"),
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 160,
		},
		{"fieldname": "bestand", "label": _("Bestand"), "fieldtype": "Float", "precision": 2, "width": 90},
		{
			"fieldname": "verfuegbar",
			"label": _("Verfügbar"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 95,
		},
		{
			"fieldname": "mindestbestand",
			"label": _("Mindestbestand"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 115,
		},
		{
			"fieldname": "nachbestellmenge",
			"label": _("Nachbestellen"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 110,
		},
		{"fieldname": "uom", "label": _("Einheit"), "fieldtype": "Data", "width": 70},
		{"fieldname": "art_text", "label": _("Art"), "fieldtype": "Data", "width": 100},
		{
			"fieldname": "lieferant",
			"label": _("Lieferant"),
			"fieldtype": "Link",
			"options": "Supplier",
			"width": 160,
		},
	]


def message(daten: dict) -> str | None:
	hinweise = daten.get("hinweise") or []
	if not hinweise:
		return None
	return "<br>".join(frappe.utils.escape_html(h) for h in hinweise)


def summary(daten: dict) -> list[dict]:
	zeilen = daten.get("zeilen") or []
	unter = sum(1 for z in zeilen if z.get("unter"))
	return [
		{"value": len(zeilen), "label": _("Mindestbestände"), "datatype": "Int"},
		{
			"value": unter,
			"label": _("unter Mindestbestand"),
			"datatype": "Int",
			"indicator": "Red" if unter else "Green",
		},
	]
