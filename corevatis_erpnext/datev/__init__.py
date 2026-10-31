"""DATEV-Monatsexport (Epic 7, Aufgabe 7.4, Handwerker#51).

Anwendungsfall: Das Büro erzeugt am Monatsende mit einem Klick den DATEV-Buchungsstapel (EXTF-CSV für
DATEV Unternehmen online / Kanzlei-Rechnungswesen) für Ausgangsrechnungen, Zahlungseingänge und
Gutschriften, dazu die Debitoren-Stammdaten der betroffenen Kunden und die Belegbilder (Rechnungs-PDFs)
als ZIP, und übergibt alles dem Steuerberater. Der Export ist nachvollziehbar (Protokoll), wiederholbar
und markiert exportierte Belege.

| Teil | Modul |
|---|---|
| Doctype ``DATEV Export`` (Firma, Zeitraum, Optionen, Status, Kennzahlen, Dateien, Protokoll, Belege) | ``corevatis_erpnext/doctype/datev_export`` |
| Buchungsstapel über erpnext_datev, Ergänzungen, Dateien, Markierung, Protokoll | ``export`` |
| Debitorennummern (ab 10000 je Kunde) und Debitoren-Stammdaten | ``debitoren`` |
| Formatprüfung EXTF (Kopf, Spalten, Pflichtfelder, Längen, Datum, Dezimalkomma) - ohne Frappe | ``pruefung`` |
| Custom Fields: Markierung an Rechnung/Zahlung, Ergänzungen an DATEV Settings | ``felder`` |

Rechte: nur Büro und System Manager erstellen Exporte; Dateien sind privat am Export.
Doku: ``docs/7.4-datev-export.md`` im Repo Handwerker.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import get_first_day, get_last_day, getdate

__all__ = ["ensure_datev_setup", "export_erstellen", "monatsexport", "pruefe_rolle"]

ROLLEN = ("Büro", "System Manager")
EXPORT_DOCTYPE = "DATEV Export"


def pruefe_rolle() -> None:
	if not set(frappe.get_roles()) & set(ROLLEN):
		frappe.throw(_("Den DATEV-Export erstellt nur das Büro."), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def export_erstellen(name: str) -> dict:
	"""Knopf „Export erstellen“ am ``DATEV Export``: Buchungsstapel, Debitoren, Belegbilder, Markierung."""
	from corevatis_erpnext.datev.export import erstellen

	pruefe_rolle()
	doc = frappe.get_doc(EXPORT_DOCTYPE, name)
	doc.check_permission("write")
	return erstellen(doc)


def monatsexport(firma: str, jahr: int, monat: int, festschreibung: int = 0, nur_neue: int = 0) -> dict:
	"""``bench execute corevatis_erpnext.datev.monatsexport --kwargs '{"firma": "...", "jahr": 2026, "monat": 10}'``:
	Export für den Kalendermonat anlegen und erstellen (Abnahme, Wiederholung von der Kommandozeile)."""
	from corevatis_erpnext.datev.export import BELEGARTEN_STANDARD, erstellen

	pruefe_rolle()
	von = getdate(f"{int(jahr):04d}-{int(monat):02d}-01")
	doc = frappe.get_doc(
		{
			"doctype": EXPORT_DOCTYPE,
			"firma": firma,
			"von": get_first_day(von),
			"bis": get_last_day(von),
			"belegarten": BELEGARTEN_STANDARD,
			"festschreibung": int(festschreibung),
			"nur_neue": int(nur_neue),
			"mit_belegbildern": 1,
		}
	)
	doc.insert()
	ergebnis = erstellen(doc)
	frappe.db.commit()
	return ergebnis


def ensure_datev_setup() -> None:
	"""``after_install``/``after_migrate`` (nach den Abrechnungsfeldern): Felder an Rechnung, Zahlung und -
	wenn erpnext_datev installiert ist - an DATEV Settings."""
	from corevatis_erpnext.datev.felder import ensure_datev_felder

	ensure_datev_felder()
