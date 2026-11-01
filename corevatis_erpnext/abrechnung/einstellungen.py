"""Single „Corevatis Abrechnung Einstellungen“ (7.1/7.2) mit Standardwerten.

- Abschlagsplan: automatisch mit dem Projekt anlegen (Standard an), Vorlage ``30/40/30`` (bei Baubeginn,
  bei Baufortschritt, bei Abnahme), ``50/50`` (bei Baubeginn, bei Abnahme) oder eigene Zeilen;
  Mindestsumme (Standard 0 = immer).
- Rechnungen: Service-Artikel für Abschlagszeilen und Abzüge (Standard ``ABSCHLAG``, wird angelegt).
- Lohnanteil § 35a EStG und Ausweis Lohn/Material (#105): Rückfall-Prozentsatz für Positionen ohne
  Kalkulation (Standard 0 = alles Material), Zuschläge nach Kostenanteilen auf Material und Lohn
  verteilt (Standard an seit #105: Material + Lohn = Netto; aus: Minuten x Stundensatz + Erschwernis),
  Material und Lohn je Position drucken (Standard an).
- Mahnwesen (7.5): Fristen Zahlungserinnerung 7 Tage nach Fälligkeit, 1. Mahnung 14 Tage danach,
  2. Mahnung 14 Tage danach; Zahlungsfrist in der Mahnung 7 Tage; Mahngebühr 1. Mahnung 0 €,
  2. Mahnung 5 €; Ertragskonto der Gebühr (leer = SKR-Konto 2650/7100, sonst Standard-Erlöskonto).
- Zahlungseingang (7.5): Bankkonto des Betriebs (leer = Standard-Firmenkonto, beim ersten Import
  angelegt), nur Gutschriften einlesen (Standard an), CSV-Trennzeichen und Spalten (leer = automatisch).
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt

EINSTELLUNGEN = "Corevatis Abrechnung Einstellungen"
ABSCHLAG_ARTIKEL = "ABSCHLAG"
VORLAGE_30_40_30 = "30/40/30"
VORLAGE_50_50 = "50/50"
VORLAGE_EIGENE = "Eigene Vorlage"
# Fälligkeiten (Select-Optionen der Zeile, Vertrag mit ``corevatis.engine.invoice.FAELLIG_BEI``)
BAUBEGINN, BAUFORTSCHRITT, ABNAHME, DATUM = "bei Baubeginn", "bei Baufortschritt", "bei Abnahme", "Datum"
FAELLIG_BEI = (BAUBEGINN, BAUFORTSCHRITT, ABNAHME, DATUM)
VORLAGEN = {
	VORLAGE_30_40_30: (
		("Abschlag bei Baubeginn", 30.0, BAUBEGINN),
		("Abschlag nach Baufortschritt", 40.0, BAUFORTSCHRITT),
		("Abschlag bei Abnahme", 30.0, ABNAHME),
	),
	VORLAGE_50_50: (
		("Abschlag bei Baubeginn", 50.0, BAUBEGINN),
		("Abschlag bei Abnahme", 50.0, ABNAHME),
	),
}
CSV_AUTOMATISCH = "Automatisch"
STANDARD = {
	"abschlagsplan_automatisch": 1,
	"abschlagsplan_vorlage": VORLAGE_30_40_30,
	"abschlag_mindestsumme": 0.0,
	"abschlag_artikel": None,
	"lohnanteil_prozent_standard": 0.0,
	"lohnanteil_mit_zuschlaegen": 1,
	"lohn_material_je_position": 1,
	# Mahnwesen (7.5)
	"mahnfrist_erinnerung": 7,
	"mahnfrist_mahnung1": 14,
	"mahnfrist_mahnung2": 14,
	"mahnung_zahlungsfrist": 7,
	"mahngebuehr_mahnung1": 0.0,
	"mahngebuehr_mahnung2": 5.0,
	"mahngebuehr_konto": None,
	# Zahlungseingang (7.5)
	"bankkonto": None,
	"nur_gutschriften": 1,
	"csv_trennzeichen": CSV_AUTOMATISCH,
	"csv_spalte_datum": None,
	"csv_spalte_betrag": None,
	"csv_spalte_verwendungszweck": None,
	"csv_spalte_name": None,
	"csv_spalte_iban": None,
}
GANZZAHLEN = ("mahnfrist_erinnerung", "mahnfrist_mahnung1", "mahnfrist_mahnung2", "mahnung_zahlungsfrist")
GEBUEHREN = ("mahngebuehr_mahnung1", "mahngebuehr_mahnung2")


def einstellungen() -> frappe._dict:
	werte = frappe._dict(STANDARD)
	if frappe.db.exists("DocType", EINSTELLUNGEN):
		gespeichert = frappe.db.get_singles_dict(EINSTELLUNGEN)
		for key in STANDARD:
			if gespeichert.get(key) not in (None, ""):
				werte[key] = gespeichert[key]
	for key in (
		"abschlagsplan_automatisch",
		"lohnanteil_mit_zuschlaegen",
		"lohn_material_je_position",
		"nur_gutschriften",
	):
		werte[key] = cint(werte[key])
	for key in GANZZAHLEN:
		werte[key] = max(cint(werte[key]), 0)
	for key in GEBUEHREN:
		werte[key] = max(flt(werte[key], 2), 0.0)
	if werte.csv_trennzeichen not in (CSV_AUTOMATISCH, ";", ",", "Tab"):
		werte.csv_trennzeichen = CSV_AUTOMATISCH
	if werte.abschlagsplan_vorlage not in (*VORLAGEN, VORLAGE_EIGENE):
		werte.abschlagsplan_vorlage = VORLAGE_30_40_30
	werte.abschlag_mindestsumme = max(flt(werte.abschlag_mindestsumme), 0.0)
	werte.lohnanteil_prozent_standard = min(max(flt(werte.lohnanteil_prozent_standard), 0.0), 100.0)
	werte.abschlag_artikel = werte.abschlag_artikel or ABSCHLAG_ARTIKEL
	return werte


def vorlage_zeilen(e: frappe._dict | None = None) -> list[dict]:
	"""Zeilen der eingestellten Vorlage: ``bezeichnung``, ``prozent``, ``faellig_bei`` (Reihenfolge = Nr.)."""
	e = e or einstellungen()
	if e.abschlagsplan_vorlage == VORLAGE_EIGENE and frappe.db.exists("DocType", EINSTELLUNGEN):
		zeilen = [
			{"bezeichnung": z.bezeichnung, "prozent": flt(z.prozent), "faellig_bei": z.faellig_bei}
			for z in frappe.get_single(EINSTELLUNGEN).get("vorlage_zeilen") or []
			if flt(z.prozent) > 0
		]
		if zeilen:
			return zeilen
	return [
		{"bezeichnung": b, "prozent": p, "faellig_bei": f}
		for b, p, f in VORLAGEN.get(e.abschlagsplan_vorlage, VORLAGEN[VORLAGE_30_40_30])
	]
