"""Abrechnung, Teil 1 (Epic 7, Aufgaben 7.1/7.2): Abschlagsplan, Abschlagsrechnung, Schlussrechnung.

Anwendungsfall: Das Büro stellt nach Bautenstand die erste Abschlagsrechnung (z. B. 30 % bei
Baubeginn), später weitere, und nach der Abnahme die Schlussrechnung mit Ist-Mengen und Nachträgen
abzüglich der Abschläge, mit ausgewiesenem Lohnanteil nach § 35a EStG - aus dem Projekt bzw. der
Prozessleiste heraus, in Handwerkersprache.

| Teil | Modul |
|---|---|
| Doctype ``Abschlagsplan`` (+ Zeilen) je Auftrag, Vorlage aus den Einstellungen, automatisch mit dem Projekt | ``plan`` |
| „Abschlag stellen“: nächste Zeile → Sales Invoice (Entwurf) mit Service-Position | ``abschlag`` |
| „Schlussrechnung erstellen“: Ist-Mengen, Nachträge, Abzug der Abschläge, Lohnanteil | ``schluss`` |
| Abgerechneter Anteil am Auftrag (``per_billed``), Hooks an Rechnung und Zahlung | ``auftrag`` |
| Rechnungsformular: Zustandstext, „Rechnung freigeben“ | ``rechnung`` |
| Prozessleiste, Schritt „Rechnung“ | ``prozess`` |
| Felder an Sales Invoice (Druck #50), Einstellungen, Kern-Aufruf | ``felder``, ``einstellungen``, ``kern`` |
| Offene Posten, Zahlungs- und Mahnstand (7.5) | ``offene_posten`` |
| Mahnwesen auf ERPNext Dunning: Mahntypen, Vorschlag nach Fristen, „Mahnen“ (7.5) | ``mahnung`` |
| Zahlungseingang: Kontoauszug (CSV, CAMT) einlesen, Vorschlag, Zuordnung als Payment Entry (7.5) | ``kontoauszug``, ``zahlungseingang`` |

Rechenlogik ausschließlich im Kern ``corevatis.engine.invoice`` (per Name, Lizenzgrenze); Rechte:
nur Büro (und System Manager) stellt Rechnungen, Beträge kommen aus Auftragsdaten, über die
Auftragssumme hinaus nur mit Bestätigung. Doku: ``docs/7.1-abschlagsplan.md``,
``docs/7.2-schlussrechnung.md`` im Repo Handwerker.
"""

from __future__ import annotations

import frappe

from corevatis_erpnext.abrechnung.abschlag import abschlag_stellen, abschlag_vorschlag
from corevatis_erpnext.abrechnung.mahnung import mahnung_erstellen, mahnvorschlag
from corevatis_erpnext.abrechnung.offene_posten import offene_posten
from corevatis_erpnext.abrechnung.rechnung import zustand
from corevatis_erpnext.abrechnung.schluss import schlussrechnung_erstellen, schlussrechnung_vorschlag
from corevatis_erpnext.abrechnung.zahlungseingang import (
	ignorieren,
	kontoauszug_importieren,
	vorschlaege,
	zuordnen,
)

__all__ = [
	"abschlag_stellen",
	"abschlag_vorschlag",
	"ensure_abrechnung_setup",
	"ignorieren",
	"kontoauszug_importieren",
	"mahnung_erstellen",
	"mahnvorschlag",
	"offene_posten",
	"schlussrechnung_erstellen",
	"schlussrechnung_vorschlag",
	"vorschlaege",
	"zuordnen",
	"zustand",
]

NEGATIVE_PREISE = ("Selling Settings", "allow_negative_rates_for_items")


def ensure_negative_preise() -> bool:
	"""Abzugszeilen der Schlussrechnung haben einen negativen Preis; ERPNext verlangt dafür die
	Verkaufseinstellung „negative Preise erlauben“. Rückgabe: geändert."""
	if frappe.db.get_single_value(*NEGATIVE_PREISE):
		return False
	frappe.db.set_single_value(*NEGATIVE_PREISE, 1)
	return True


def ensure_abrechnung_setup() -> None:
	"""``after_install``/``after_migrate`` (nach den Abnahme-Feldern 6.2): Felder, Artikel, Einstellung;
	7.5: Felder an Dunning/Dunning Type/Bank Transaction, Mahntypen je Firma, Rechte der Rolle Büro."""
	from corevatis_erpnext.abrechnung.abschlag import ensure_abschlag_artikel
	from corevatis_erpnext.abrechnung.felder import ensure_abrechnung_felder, ensure_mahnwesen_felder
	from corevatis_erpnext.abrechnung.mahnung import ensure_mahntypen, ensure_rechte

	ensure_abrechnung_felder()
	ensure_mahnwesen_felder()
	ensure_negative_preise()
	if frappe.db.exists("DocType", "Item") and frappe.db.get_value("Item Group", {"is_group": 0}):
		ensure_abschlag_artikel()
	ensure_rechte()
	ensure_mahntypen()
