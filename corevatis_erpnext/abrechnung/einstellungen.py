"""Single „Corevatis Abrechnung Einstellungen“ (7.1/7.2) mit Standardwerten.

- Abschlagsplan: automatisch mit dem Projekt anlegen (Standard an), Vorlage ``30/40/30`` (bei Baubeginn,
  bei Baufortschritt, bei Abnahme), ``50/50`` (bei Baubeginn, bei Abnahme) oder eigene Zeilen;
  Mindestsumme (Standard 0 = immer).
- Rechnungen: Service-Artikel für Abschlagszeilen und Abzüge (Standard ``ABSCHLAG``, wird angelegt).
- Lohnanteil § 35a EStG: Rückfall-Prozentsatz für Positionen ohne Kalkulation (Standard 0 = kein
  Ausweis), optional Zuschläge anteilig auf den Lohn (Standard aus: Minuten x Stundensatz).
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
STANDARD = {
	"abschlagsplan_automatisch": 1,
	"abschlagsplan_vorlage": VORLAGE_30_40_30,
	"abschlag_mindestsumme": 0.0,
	"abschlag_artikel": None,
	"lohnanteil_prozent_standard": 0.0,
	"lohnanteil_mit_zuschlaegen": 0,
}


def einstellungen() -> frappe._dict:
	werte = frappe._dict(STANDARD)
	if frappe.db.exists("DocType", EINSTELLUNGEN):
		gespeichert = frappe.db.get_singles_dict(EINSTELLUNGEN)
		for key in STANDARD:
			if gespeichert.get(key) not in (None, ""):
				werte[key] = gespeichert[key]
	for key in ("abschlagsplan_automatisch", "lohnanteil_mit_zuschlaegen"):
		werte[key] = cint(werte[key])
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
