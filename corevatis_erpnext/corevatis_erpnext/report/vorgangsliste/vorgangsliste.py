"""Script Report „Vorgangsliste“: offene Vorgänge mit Schritt, nächstem Schritt und Ampel.

Daten: ``corevatis_erpnext.prozess.uebersicht.vorgaenge`` (Rechte des Benutzers). Entscheidung
Script Report statt eigener Page: Filter, Sortierung, Spaltenwahl, Export und Druck bringt Frappe
mit; die Ampel färbt der Formatter in ``vorgangsliste.js`` (``docs/prozessleiste.md``).
"""

from frappe import _

from corevatis_erpnext.prozess.uebersicht import vorgaenge


def execute(filters=None):
	zeilen = vorgaenge(filters)
	return columns(), zeilen, None, None, summary(zeilen)


def columns() -> list[dict]:
	return [
		{"fieldname": "ampel", "label": _("Ampel"), "fieldtype": "Data", "width": 90},
		{"fieldname": "vorgang", "label": _("Anfrage"), "fieldtype": "Link", "options": "Lead", "width": 150},
		{"fieldname": "kunde", "label": _("Kunde"), "fieldtype": "Data", "width": 170},
		{"fieldname": "objekt_adresse", "label": _("Objektadresse"), "fieldtype": "Data", "width": 210},
		{"fieldname": "schritt", "label": _("Schritt"), "fieldtype": "Data", "width": 120},
		{"fieldname": "naechster_schritt", "label": _("Nächster Schritt"), "fieldtype": "Data", "width": 220},
		{
			"fieldname": "beleg_typ",
			"label": _("Belegart"),
			"fieldtype": "Link",
			"options": "DocType",
			"width": 110,
		},
		{
			"fieldname": "beleg",
			"label": _("Beleg"),
			"fieldtype": "Dynamic Link",
			"options": "beleg_typ",
			"width": 150,
		},
		{
			"fieldname": "verantwortlich",
			"label": _("Verantwortlich"),
			"fieldtype": "Link",
			"options": "User",
			"width": 160,
		},
		{"fieldname": "alter_tage", "label": _("Alter (Tage)"), "fieldtype": "Int", "width": 90},
		{"fieldname": "ampel_text", "label": _("Hinweis"), "fieldtype": "Data", "width": 260},
	]


def summary(zeilen: list[dict]) -> list[dict]:
	anzahl = {farbe: sum(1 for z in zeilen if z["ampel"] == farbe) for farbe in ("rot", "gelb", "grün")}
	return [
		{"value": len(zeilen), "label": _("Vorgänge"), "datatype": "Int", "indicator": "Blue"},
		{"value": anzahl["rot"], "label": _("überfällig"), "datatype": "Int", "indicator": "Red"},
		{"value": anzahl["gelb"], "label": _("Betrieb am Zug"), "datatype": "Int", "indicator": "Orange"},
		{"value": anzahl["grün"], "label": _("läuft"), "datatype": "Int", "indicator": "Green"},
	]
