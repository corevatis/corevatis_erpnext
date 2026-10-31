"""Script Report „Stundenkonto“ (Phase 2 P2.5, Handwerker#88).

Ansicht „Mitarbeiter“: eine Zeile je sichtbarem Mitarbeiter mit Soll, Ist, Saldo des Monats, Saldo
Jahr und Urlaubskonto. Ansicht „Tage“: Monatsübersicht eines Mitarbeiters (je Kalendertag Soll, Ist,
Saldo, Abwesenheit, Feiertag). Daten: ``corevatis_erpnext.mitarbeiter.stundenkonto``; Rechte dort
(Büro/Meister alle, sonst nur der eigene Mitarbeiter; Art der Abwesenheit nur Büro und Betroffener).
"""

from frappe import _
from frappe.utils import flt, getdate, today

from corevatis_erpnext.mitarbeiter import stundenkonto as sk

ANSICHT_MITARBEITER = "Mitarbeiter"
ANSICHT_TAGE = "Tage"


def execute(filters=None):
	filters = filters or {}
	tag = getdate(filters.get("monat") or today())
	jahr, monat = tag.year, tag.month
	mitarbeiter = filters.get("mitarbeiter") or None
	if filters.get("ansicht") == ANSICHT_TAGE:
		if not mitarbeiter:
			eigene = sk.mitarbeiter_liste()
			if len(eigene) != 1:
				return columns_tage(), [], _("Für die Tagesansicht bitte einen Mitarbeiter wählen."), None, []
			mitarbeiter = eigene[0]["name"]
		m = sk.monatsuebersicht(mitarbeiter, jahr, monat)
		return columns_tage(), m["tage"], message_tage(m), None, summary_tage(m)
	daten = sk.uebersicht(jahr, monat, mitarbeiter)
	return (
		columns_mitarbeiter(daten),
		daten["zeilen"],
		message_mitarbeiter(daten),
		None,
		summary_mitarbeiter(daten),
	)


def columns_mitarbeiter(daten: dict) -> list[dict]:
	return [
		# Bewusst kein Link: Frappe streicht Berichtszeilen, deren Link-Spalte gegen eine Benutzerberechtigung
		# verstößt (ERPNext legt je Employee eine User Permission an); der Meister sähe nur sich selbst.
		{"fieldname": "mitarbeiter", "label": _("Mitarbeiter"), "fieldtype": "Data", "width": 120},
		{"fieldname": "mitarbeiter_name", "label": _("Name"), "fieldtype": "Data", "width": 160},
		{"fieldname": "solltage", "label": _("Solltage"), "fieldtype": "Int", "width": 75},
		{"fieldname": "soll_brutto", "label": _("Soll h"), "fieldtype": "Float", "precision": 1, "width": 70},
		{
			"fieldname": "abwesend",
			"label": _("Abwesend h"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 90,
		},
		{"fieldname": "soll", "label": _("Soll netto h"), "fieldtype": "Float", "precision": 1, "width": 95},
		{"fieldname": "ist", "label": _("Ist h"), "fieldtype": "Float", "precision": 1, "width": 70},
		{
			"fieldname": "saldo",
			"label": _("Saldo Monat h"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 105,
		},
		{
			"fieldname": "saldo_jahr",
			"label": _("Saldo Jahr h"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 100,
		},
		{
			"fieldname": "urlaub_anspruch",
			"label": _("Urlaub Anspruch"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 115,
		},
		{
			"fieldname": "urlaub_genommen",
			"label": _("Urlaub genommen"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 125,
		},
		{
			"fieldname": "urlaub_geplant",
			"label": _("Urlaub geplant"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 110,
		},
		{
			"fieldname": "urlaub_rest",
			"label": _("Resturlaub"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 90,
		},
		{
			"fieldname": "kranktage_jahr",
			"label": _("Kranktage Jahr"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 110,
		},
		{
			"fieldname": "abwesend_tage",
			"label": _("Abwesend Tage"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 110,
		},
		{"fieldname": "aktion", "label": _("Monat"), "fieldtype": "Data", "width": 90},
	]


def columns_tage() -> list[dict]:
	return [
		{"fieldname": "datum", "label": _("Datum"), "fieldtype": "Date", "width": 100},
		{"fieldname": "wochentag", "label": _("Tag"), "fieldtype": "Data", "width": 50},
		{"fieldname": "feiertag", "label": _("Feiertag"), "fieldtype": "Data", "width": 130},
		{"fieldname": "abwesenheit", "label": _("Abwesenheit"), "fieldtype": "Data", "width": 120},
		{"fieldname": "soll_brutto", "label": _("Soll h"), "fieldtype": "Float", "precision": 1, "width": 70},
		{
			"fieldname": "abwesend",
			"label": _("Abwesend h"),
			"fieldtype": "Float",
			"precision": 1,
			"width": 90,
		},
		{"fieldname": "soll", "label": _("Soll netto h"), "fieldtype": "Float", "precision": 1, "width": 95},
		{"fieldname": "ist", "label": _("Ist h"), "fieldtype": "Float", "precision": 2, "width": 70},
		{"fieldname": "saldo", "label": _("Saldo h"), "fieldtype": "Float", "precision": 2, "width": 80},
		{
			"fieldname": "laufend",
			"label": _("Saldo laufend h"),
			"fieldtype": "Float",
			"precision": 2,
			"width": 115,
		},
	]


def message_mitarbeiter(daten: dict) -> str:
	return _(
		"Soll: {0} h je Arbeitstag (Mo-Fr) ohne Feiertage; Abwesenheiten (Urlaub, Krank, Schulung, Sonstiges) "
		"senken das Soll. Ist: Zeiten aus der Zeiterfassung ohne Pausen (Entwurf und eingereicht). "
		"Tage nach heute zählen nicht ins Soll. Saldo Jahr: Übertrag zum 1. Januar plus Monate bis zum gewählten. "
		"Urlaub in Arbeitstagen; Art der Abwesenheit sehen nur Büro und der Mitarbeiter selbst."
	).format(f"{flt(daten['tagesarbeitszeit']):g}")


def message_tage(m: dict) -> str:
	text = _("{0}, {1}/{2}: Soll {3} h, Ist {4} h, Saldo {5} h; Saldo Jahr {6} h.").format(
		m.get("mitarbeiter_name") or m["mitarbeiter"],
		f"{m['monat']:02d}",
		m["jahr"],
		f"{flt(m['soll']):g}",
		f"{flt(m['ist']):g}",
		f"{flt(m['saldo']):+g}",
		f"{flt(m['saldo_jahr']):+g}",
	)
	u = m.get("urlaub")
	if u:
		text += " " + _("Urlaub {0}: Anspruch {1}, genommen {2}, geplant {3}, Rest {4} Tage.").format(
			m["jahr"],
			f"{flt(u['anspruch'] + u['uebertrag']):g}",
			f"{flt(u['genommen']):g}",
			f"{flt(u['geplant']):g}",
			f"{flt(u['rest']):g}",
		)
	return text


def summary_mitarbeiter(daten: dict) -> list[dict]:
	zeilen = daten["zeilen"]
	saldo = flt(sum(z["saldo"] for z in zeilen), 1)
	ueber = flt(sum(max(z["saldo"], 0.0) for z in zeilen), 1)
	minus = flt(sum(max(-z["saldo"], 0.0) for z in zeilen), 1)
	return [
		{"value": len(zeilen), "label": _("Mitarbeiter"), "datatype": "Int", "indicator": "Blue"},
		{
			"value": saldo,
			"label": _("Saldo Monat h"),
			"datatype": "Float",
			"indicator": "Green" if saldo >= 0 else "Red",
		},
		{
			"value": ueber,
			"label": _("Überstunden h"),
			"datatype": "Float",
			"indicator": "Orange" if ueber else "Green",
		},
		{
			"value": minus,
			"label": _("Minusstunden h"),
			"datatype": "Float",
			"indicator": "Red" if minus else "Green",
		},
	]


def summary_tage(m: dict) -> list[dict]:
	return [
		{"value": m["soll"], "label": _("Soll h"), "datatype": "Float", "indicator": "Blue"},
		{"value": m["ist"], "label": _("Ist h"), "datatype": "Float", "indicator": "Blue"},
		{
			"value": m["saldo"],
			"label": _("Saldo Monat h"),
			"datatype": "Float",
			"indicator": "Green" if m["saldo"] >= 0 else "Red",
		},
		{
			"value": m["saldo_jahr"],
			"label": _("Saldo Jahr h"),
			"datatype": "Float",
			"indicator": "Green" if m["saldo_jahr"] >= 0 else "Red",
		},
	]
