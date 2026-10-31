"""Script Report „Offene Posten“ (7.5): offene Rechnungen mit Rest, Fälligkeit, Tagen überfällig,
Mahnstufe, Mahnvorschlag und Vorgang. Daten: ``corevatis_erpnext.abrechnung.offene_posten.offene_posten``
(Rechte des Benutzers); der Knopf „Mahnen“ je Zeile kommt aus ``offene_posten.js``."""

from frappe import _
from frappe.utils import flt

from corevatis_erpnext.abrechnung.offene_posten import offene_posten


def execute(filters=None):
	zeilen = offene_posten(filters)
	return columns(), zeilen, None, None, summary(zeilen)


def columns() -> list[dict]:
	return [
		{
			"fieldname": "rechnung",
			"label": _("Rechnung"),
			"fieldtype": "Link",
			"options": "Sales Invoice",
			"width": 150,
		},
		{"fieldname": "art", "label": _("Art"), "fieldtype": "Data", "width": 110},
		{"fieldname": "kunde_name", "label": _("Kunde"), "fieldtype": "Data", "width": 170},
		{"fieldname": "datum", "label": _("Rechnungsdatum"), "fieldtype": "Date", "width": 110},
		{"fieldname": "faellig", "label": _("Fällig am"), "fieldtype": "Date", "width": 100},
		{"fieldname": "tage_ueberfaellig", "label": _("Tage überfällig"), "fieldtype": "Int", "width": 90},
		{
			"fieldname": "brutto",
			"label": _("Rechnungsbetrag"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "offen",
			"label": _("Offen"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{"fieldname": "mahnstufe", "label": _("Mahnstufe"), "fieldtype": "Data", "width": 130},
		{"fieldname": "mahnung_datum", "label": _("Gemahnt am"), "fieldtype": "Date", "width": 100},
		{
			"fieldname": "mahnung",
			"label": _("Mahnung"),
			"fieldtype": "Link",
			"options": "Dunning",
			"width": 140,
		},
		{"fieldname": "vorschlag", "label": _("Vorschlag"), "fieldtype": "Data", "width": 140},
		{"fieldname": "aktion", "label": _("Aktion"), "fieldtype": "Data", "width": 110},
		{"fieldname": "hinweis", "label": _("Hinweis"), "fieldtype": "Data", "width": 260},
		{
			"fieldname": "vorgang",
			"label": _("Vorgang"),
			"fieldtype": "Link",
			"options": "Project",
			"width": 150,
		},
		{
			"fieldname": "auftrag",
			"label": _("Auftrag"),
			"fieldtype": "Link",
			"options": "Sales Order",
			"width": 140,
		},
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
	offen = sum(flt(z["offen"]) for z in zeilen)
	ueberfaellig = [z for z in zeilen if z["tage_ueberfaellig"] > 0]
	return [
		{"value": len(zeilen), "label": _("offene Rechnungen"), "datatype": "Int", "indicator": "Blue"},
		{"value": offen, "label": _("offen zusammen"), "datatype": "Currency", "indicator": "Blue"},
		{"value": len(ueberfaellig), "label": _("überfällig"), "datatype": "Int", "indicator": "Red"},
		{
			"value": sum(flt(z["offen"]) for z in ueberfaellig),
			"label": _("überfällig zusammen"),
			"datatype": "Currency",
			"indicator": "Red",
		},
		{
			"value": sum(1 for z in zeilen if z["vorschlag"]),
			"label": _("mahnbar"),
			"datatype": "Int",
			"indicator": "Orange",
		},
	]
