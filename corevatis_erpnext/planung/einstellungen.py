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
}


def einstellungen() -> frappe._dict:
	werte = frappe._dict(STANDARD)
	if frappe.db.exists("DocType", EINSTELLUNGEN):
		gespeichert = frappe.db.get_singles_dict(EINSTELLUNGEN)
		for key in STANDARD:
			if gespeichert.get(key) not in (None, ""):
				werte[key] = gespeichert[key]
	for key in ("arbeitspakete_automatisch", "materialbedarf_automatisch", "bestand_beruecksichtigen"):
		werte[key] = cint(werte[key])
	werte.teamgroesse = max(cint(werte.teamgroesse), 1)
	werte.tagesarbeitszeit = flt(werte.tagesarbeitszeit) if 0 < flt(werte.tagesarbeitszeit) <= 24 else 8.0
	werte.verschnitt_prozent = min(max(flt(werte.verschnitt_prozent), 0.0), 100.0)
	if werte.material_request_typ not in (TYP_EINKAUF, TYP_UMLAGERUNG):
		werte.material_request_typ = TYP_EINKAUF
	werte.arbeitsbeginn = str(werte.arbeitsbeginn or STANDARD["arbeitsbeginn"])
	return werte
