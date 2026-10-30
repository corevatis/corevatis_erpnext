"""Single „Corevatis Abnahme Einstellungen“ (6.1/6.2): Vertragsgrundlage und Gewährleistungsdauer
(Vorgabe BGB 5 Jahre, VOB/B 4 Jahre), Mangel-Tasks und Rechnungssperre."""

import frappe
from frappe.utils import cint

EINSTELLUNGEN = "Corevatis Abnahme Einstellungen"
BGB = "BGB"
VOB = "VOB/B"
GRUNDLAGEN = (BGB, VOB)
STANDARD = {
	"vertragsgrundlage": BGB,
	"gewaehrleistung_bgb_jahre": 5,
	"gewaehrleistung_vob_jahre": 4,
	"mangel_tasks_automatisch": 1,
	"rechnung_ohne_abnahme_sperren": 1,
}


def einstellungen() -> frappe._dict:
	werte = frappe._dict(STANDARD)
	if frappe.db.exists("DocType", EINSTELLUNGEN):
		gespeichert = frappe.db.get_singles_dict(EINSTELLUNGEN)
		for key in STANDARD:
			if gespeichert.get(key) not in (None, ""):
				werte[key] = gespeichert[key]
	if werte.vertragsgrundlage not in GRUNDLAGEN:
		werte.vertragsgrundlage = BGB
	for key in ("gewaehrleistung_bgb_jahre", "gewaehrleistung_vob_jahre"):
		werte[key] = cint(werte[key]) if 0 < cint(werte[key]) <= 30 else STANDARD[key]
	for key in ("mangel_tasks_automatisch", "rechnung_ohne_abnahme_sperren"):
		werte[key] = cint(werte[key])
	return werte


def jahre_fuer(grundlage: str | None, e: frappe._dict | None = None) -> int:
	e = e or einstellungen()
	grundlage = grundlage if grundlage in GRUNDLAGEN else e.vertragsgrundlage
	return e.gewaehrleistung_vob_jahre if grundlage == VOB else e.gewaehrleistung_bgb_jahre
