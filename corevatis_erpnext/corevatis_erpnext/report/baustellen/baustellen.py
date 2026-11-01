"""Script Report „Baustellen“ (Büro-Dashboard, Handwerker#43, 5.9): je laufendem Projekt Soll/Ist Stunden,
Abweichung, Material, Fortschritt, nächster Termin, offene Meldungen/Mängel und Ampel.
Daten: ``corevatis_erpnext.baustellen.uebersicht`` (Rolle Büro/Meister, Leserecht am Projekt)."""

import frappe
from frappe import _
from frappe.utils import flt

from corevatis_erpnext.baustellen import GELB, GRUEN, ROT, uebersicht


def execute(filters=None):
	daten = uebersicht(filters or {})
	return columns(daten), daten["zeilen"], message(daten), None, summary(daten)


def columns(daten: dict) -> list[dict]:
	geld = {"fieldtype": "Currency", "options": "currency", "width": 110}
	spalten = [
		{"fieldname": "ampel", "label": _("Ampel"), "fieldtype": "Data", "width": 70},
		{
			"fieldname": "projekt",
			"label": _("Projekt"),
			"fieldtype": "Link",
			"options": "Project",
			"width": 125,
		},
		{"fieldname": "projekt_name", "label": _("Bezeichnung"), "fieldtype": "Data", "width": 220},
		# Kunde und Meister bewusst als Data, nicht Link: Frappe streicht Berichtszeilen, deren Link-Spalte
		# gegen eine Benutzerberechtigung verstößt (Lektion P2.5, Stundenkonto)
		{"fieldname": "kunde_objekt", "label": _("Kunde / Objekt"), "fieldtype": "Data", "width": 200},
		{"fieldname": "meister", "label": _("Meister"), "fieldtype": "Data", "width": 130},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 85},
		{"fieldname": "soll_h", "label": _("Soll h"), "fieldtype": "Float", "precision": 2, "width": 75},
		{"fieldname": "ist_h", "label": _("Ist h"), "fieldtype": "Float", "precision": 2, "width": 75},
	]
	if daten.get("zeitraum"):
		spalten.append(
			{
				"fieldname": "ist_zeitraum",
				"label": _("Ist h Zeitraum"),
				"fieldtype": "Float",
				"precision": 2,
				"width": 100,
			}
		)
	spalten += [
		{
			"fieldname": "abweichung",
			"label": _("Abweichung %"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 105,
		},
		{"fieldname": "soll_material", "label": _("Material Soll"), **geld},
		{"fieldname": "ist_material", "label": _("Material Ist"), **geld},
		{
			"fieldname": "fortschritt",
			"label": _("Fortschritt %"),
			"fieldtype": "Float",
			"precision": 0,
			"width": 100,
		},
		{"fieldname": "naechster_termin", "label": _("Nächster Termin"), "fieldtype": "Date", "width": 115},
		{"fieldname": "termin_text", "label": _("Termin"), "fieldtype": "Data", "width": 200},
		{"fieldname": "meldungen", "label": _("Meldungen/Mängel offen"), "fieldtype": "Int", "width": 120},
		{"fieldname": "hinweis", "label": _("Hinweis"), "fieldtype": "Data", "width": 260},
		{"fieldname": "nachkalkulation", "label": _("Details"), "fieldtype": "Data", "width": 120},
	]
	return spalten


def message(daten: dict) -> str | None:
	hinweise = daten.get("hinweise") or []
	if not hinweise:
		return None
	return "<br>".join(frappe.utils.escape_html(h) for h in hinweise)


def summary(daten: dict) -> list[dict]:
	zeilen = daten.get("zeilen") or []
	farben = [z["ampel"] for z in zeilen]
	ergebnis = [
		{"value": len(zeilen), "label": _("Baustellen"), "datatype": "Int"},
		{
			"value": farben.count(ROT),
			"label": _("rot"),
			"datatype": "Int",
			"indicator": "Red" if ROT in farben else "Green",
		},
		{
			"value": farben.count(GELB),
			"label": _("gelb"),
			"datatype": "Int",
			"indicator": "Orange" if GELB in farben else "Green",
		},
		{"value": farben.count(GRUEN), "label": _("grün"), "datatype": "Int", "indicator": "Green"},
		{"value": frappe.format_value(flt(sum(z["ist_h"] for z in zeilen)), {"fieldtype": "Float", "precision": 1}), "label": _("Ist h gesamt"), "datatype": "Data"},
	]
	if daten.get("zeitraum"):
		ergebnis.append(
			{
				"value": flt(sum(flt(z["ist_zeitraum"]) for z in zeilen), 1),
				"label": _("Ist h im Zeitraum"),
				"datatype": "Float",
			}
		)
	ergebnis.append(
		{
			"value": sum(z["meldungen"] for z in zeilen),
			"label": _("Meldungen/Mängel offen"),
			"datatype": "Int",
			"indicator": "Orange" if any(z["meldungen"] for z in zeilen) else "Green",
		}
	)
	return ergebnis
