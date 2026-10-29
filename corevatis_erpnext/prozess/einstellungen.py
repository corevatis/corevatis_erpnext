"""Ampel-Schwellen der Vorgangsübersicht (Single „Corevatis Prozess Einstellungen“)."""

import frappe
from frappe.utils import cint

EINSTELLUNGEN = "Corevatis Prozess Einstellungen"
STANDARD = {
	# Anfrage ohne Besichtigungstermin (Reaktionszeit, Kennzahl Prozess 1) - ANNAHME
	"ampel_anfrage_ohne_termin_tage": 2,
	# Termin ohne abgeschlossenes Aufmaß bzw. Angebot
	"ampel_termin_ohne_aufmass_tage": 7,
	# eingereichtes Angebot ohne Antwort und ohne Nachfass-Aufgabe in diesem Zeitraum
	# (Nachfassen 4.5 nach 7 Tagen + 3 Tage Toleranz) - ANNAHME
	"ampel_angebot_ohne_nachfass_tage": 10,
	# Auftrag ohne Zeitbuchung/Tagesrapport ab dem geplanten Start
	"ampel_auftrag_ohne_rapport_tage": 3,
	"uebersicht_max_vorgaenge": 500,
}
SCHWELLEN = (
	"ampel_anfrage_ohne_termin_tage",
	"ampel_termin_ohne_aufmass_tage",
	"ampel_angebot_ohne_nachfass_tage",
	"ampel_auftrag_ohne_rapport_tage",
)


def einstellungen() -> frappe._dict:
	werte = frappe._dict(STANDARD)
	if frappe.db.exists("DocType", EINSTELLUNGEN):
		gespeichert = frappe.db.get_singles_dict(EINSTELLUNGEN)
		for key in STANDARD:
			if gespeichert.get(key) not in (None, "", 0, "0"):
				werte[key] = gespeichert[key]
	for key in STANDARD:
		werte[key] = max(cint(werte[key]), 1)
	return werte
