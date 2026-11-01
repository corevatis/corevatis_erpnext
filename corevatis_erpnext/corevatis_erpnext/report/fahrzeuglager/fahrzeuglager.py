"""Script Report „Fahrzeuglager“ (Phase 2, P2.2): Was liegt auf dem Fahrzeug eines Monteurs, was brauchen
seine Baustellen der nächsten Tage, was fehlt - Vorschlag für „Fahrzeug beladen“. Daten:
``corevatis_erpnext.materialwirtschaft.fahrzeug_vorschlag`` (Rollen Büro/Meister)."""

import frappe
from frappe import _

from corevatis_erpnext.materialwirtschaft import fahrzeug_vorschlag


def execute(filters=None):
	f = frappe._dict(filters or {})
	daten = fahrzeug_vorschlag(f.get("mitarbeiter") or None, f.get("von") or None, f.get("bis") or None)
	zeilen = daten["zeilen"]
	for z in zeilen:
		z["projekte_text"] = ", ".join(z.get("projekte") or [])
	return columns(), zeilen, message(daten), None, summary(daten)


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
			"fieldname": "fahrzeug",
			"label": _("Auf dem Fahrzeug"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 120,
		},
		{
			"fieldname": "bedarf",
			"label": _("Bedarf Baustellen"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 125,
		},
		{
			"fieldname": "mindestbestand",
			"label": _("Mindestbestand"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 115,
		},
		{"fieldname": "fehlt", "label": _("Fehlt"), "fieldtype": "Float", "precision": 2, "width": 80},
		{
			"fieldname": "hauptlager",
			"label": _("Im Hauptlager"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 110,
		},
		{
			"fieldname": "vorschlag",
			"label": _("Vorschlag beladen"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 125,
		},
		{"fieldname": "uom", "label": _("Einheit"), "fieldtype": "Data", "width": 70},
		{"fieldname": "projekte_text", "label": _("Baustellen"), "fieldtype": "Data", "width": 220},
	]


def message(daten: dict) -> str:
	esc = frappe.utils.escape_html
	teile = [
		_("Fahrzeuglager {0} von {1}; Hauptlager {2}; Baustellen {3} bis {4}: {5}.").format(
			esc(daten["fahrzeuglager"]),
			esc(daten["employee_name"]),
			esc(daten.get("hauptlager") or "-"),
			esc(frappe.utils.format_date(daten["von"])),
			esc(frappe.utils.format_date(daten["bis"])),
			esc(", ".join(daten["projekte"]) or _("keine zugewiesen")),
		)
	]
	teile += [esc(h) for h in daten.get("hinweise") or []]
	return "<br>".join(teile)


def summary(daten: dict) -> list[dict]:
	zeilen = daten.get("zeilen") or []
	fehlt = sum(1 for z in zeilen if z.get("fehlt"))
	return [
		{"value": len(zeilen), "label": _("Artikel"), "datatype": "Int"},
		{
			"value": fehlt,
			"label": _("fehlen auf dem Fahrzeug"),
			"datatype": "Int",
			"indicator": "Orange" if fehlt else "Green",
		},
		{"value": len(daten.get("projekte") or []), "label": _("Baustellen"), "datatype": "Int"},
	]
