"""Einstellungen der Planung (Single „Corevatis Planung Einstellungen“) mit Standardwerten."""

import frappe
from frappe.utils import cint, flt

EINSTELLUNGEN = "Corevatis Planung Einstellungen"
TYP_EINKAUF = "Purchase"
TYP_UMLAGERUNG = "Material Transfer"
STANDARD = {
	"arbeitspakete_automatisch": 1,
	"teamgroesse": 2,
	"tagesarbeitszeit": 8.0,
	"arbeitsbeginn": "07:00:00",
	"materialbedarf_automatisch": 1,
	"material_request_typ": TYP_EINKAUF,
	"verschnitt_prozent": 5.0,
	"bestand_beruecksichtigen": 0,
	"lager_haupt": None,
	"lager_fahrzeug": None,
	# Terminplanung Besichtigung (Durchstich D14, ``planung.terminplanung``)
	"besichtigung_dauer": 60,
	"fahrzeit_umwegfaktor": 1.3,
	"fahrzeit_kmh": 45.0,
	"fahrzeit_minimum": 10,
	"fahrzeit_standard": 30,
	"fahrzeit_puffer": 10,
	"geokodierung_aus": 0,
	# Plantafel (P1.4): Reihenfolge der Mitarbeiter-Zeilen (Employee-Namen, einer je Zeile)
	"plantafel_reihenfolge": "",
}


def einstellungen() -> frappe._dict:
	werte = frappe._dict(STANDARD)
	if frappe.db.exists("DocType", EINSTELLUNGEN):
		gespeichert = frappe.db.get_singles_dict(EINSTELLUNGEN)
		for key in STANDARD:
			if gespeichert.get(key) not in (None, ""):
				werte[key] = gespeichert[key]
	for key in (
		"arbeitspakete_automatisch",
		"materialbedarf_automatisch",
		"bestand_beruecksichtigen",
		"geokodierung_aus",
	):
		werte[key] = cint(werte[key])
	werte.teamgroesse = max(cint(werte.teamgroesse), 1)
	werte.tagesarbeitszeit = flt(werte.tagesarbeitszeit) if 0 < flt(werte.tagesarbeitszeit) <= 24 else 8.0
	werte.verschnitt_prozent = min(max(flt(werte.verschnitt_prozent), 0.0), 100.0)
	if werte.material_request_typ not in (TYP_EINKAUF, TYP_UMLAGERUNG):
		werte.material_request_typ = TYP_EINKAUF
	werte.arbeitsbeginn = str(werte.arbeitsbeginn or STANDARD["arbeitsbeginn"])
	# Terminplanung: unsinnige Werte fallen auf den Standard zurück
	dauer = cint(werte.besichtigung_dauer)
	werte.besichtigung_dauer = dauer if 15 <= dauer <= 720 else STANDARD["besichtigung_dauer"]
	faktor = flt(werte.fahrzeit_umwegfaktor)
	werte.fahrzeit_umwegfaktor = faktor if 1.0 <= faktor <= 3.0 else STANDARD["fahrzeit_umwegfaktor"]
	kmh = flt(werte.fahrzeit_kmh)
	werte.fahrzeit_kmh = kmh if 5 <= kmh <= 130 else STANDARD["fahrzeit_kmh"]
	for key in ("fahrzeit_minimum", "fahrzeit_standard", "fahrzeit_puffer"):
		minuten = cint(werte[key])
		werte[key] = minuten if 0 <= minuten <= 240 else STANDARD[key]
	werte.plantafel_reihenfolge = str(werte.plantafel_reihenfolge or "")
	return werte
