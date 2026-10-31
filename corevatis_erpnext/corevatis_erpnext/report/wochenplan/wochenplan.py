"""Script Report „Wochenplan“ (Aufgabe 5.2): Mitarbeiter x Arbeitstage zweier Wochen.

Daten: ``corevatis_erpnext.planung.wochenplan.plan``. Entscheidung Script Report statt Page (wie die
Vorgangsliste der Prozessleiste): Filter, Baumansicht (Mitarbeiter → Arbeitspakete), Export und Druck
bringt Frappe mit. Zuweisen per Dialog aus ``public/js/zuweisung.bundle.js`` (``wochenplan.js``).
"""

from frappe import _
from frappe.utils import getdate

from corevatis_erpnext.planung.wochenplan import WOCHENTAGE, plan
from corevatis_erpnext.planung.zuweisung import pruefe_planer


def execute(filters=None):
	pruefe_planer()
	filters = filters or {}
	daten = plan(filters.get("von"), filters.get("projekt") or None, filters.get("mitarbeiter") or None)
	return columns(daten), daten["zeilen"], message(daten), None, summary(daten)


def columns(daten: dict) -> list[dict]:
	spalten = [
		{
			"fieldname": "bezeichnung",
			"label": _("Mitarbeiter / Arbeitspaket"),
			"fieldtype": "Data",
			"width": 270,
		},
		{
			"fieldname": "task",
			"label": _("Arbeitspaket"),
			"fieldtype": "Link",
			"options": "Task",
			"width": 130,
		},
		{
			"fieldname": "projekt",
			"label": _("Projekt"),
			"fieldtype": "Link",
			"options": "Project",
			"width": 120,
		},
		{"fieldname": "sollstunden", "label": _("Soll h"), "fieldtype": "Float", "precision": 2, "width": 70},
	]
	for tag in daten["tage"]:
		datum = getdate(tag["datum"])
		label = f"{WOCHENTAGE[datum.weekday()]} {datum.strftime('%d.%m.')}"
		if tag["frei"]:
			label += " " + _("(frei)")
		spalten.append(
			{"fieldname": tag["spalte"], "label": label, "fieldtype": "Float", "precision": 1, "width": 92}
		)
	spalten += [
		{"fieldname": "summe", "label": _("Summe h"), "fieldtype": "Float", "precision": 1, "width": 80},
		{"fieldname": "zugewiesen", "label": _("Zugewiesen"), "fieldtype": "Data", "width": 170},
		{"fieldname": "aktion", "label": _("Zuweisung"), "fieldtype": "Data", "width": 110},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
	]
	return spalten


def message(daten: dict) -> str:
	return _(
		"Stunden je Tag anteilig aus Beginn und Ende der Arbeitspakete (Arbeitstag {0} h); mehrere "
		"Zugewiesene teilen sich die Stunden. Tageslast eines Mitarbeiters über alle Projekte, rot über {0} h."
	).format(f"{daten['tagesarbeitszeit']:g}")


def summary(daten: dict) -> list[dict]:
	return [
		{"value": daten["mitarbeiter"], "label": _("Mitarbeiter"), "datatype": "Int", "indicator": "Blue"},
		{"value": daten["pakete"], "label": _("Arbeitspakete"), "datatype": "Int", "indicator": "Blue"},
		{
			"value": daten["nicht_zugewiesen"],
			"label": _("nicht zugewiesen"),
			"datatype": "Int",
			"indicator": "Orange" if daten["nicht_zugewiesen"] else "Green",
		},
		{
			"value": daten["ueberlast_tage"],
			"label": _("Tage über {0} h").format(f"{daten['tagesarbeitszeit']:g}"),
			"datatype": "Int",
			"indicator": "Red" if daten["ueberlast_tage"] else "Green",
		},
	]
