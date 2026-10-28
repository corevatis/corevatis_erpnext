"""Leistungskatalog (Epic 2): Custom Fields am Item (2.2, ``item_felder``) und Gewerke-Katalog als
Datenpaket (2.3).

Ein Paket (``daten/<paket>/``) besteht aus Data-Import-Vorlagen (CSV) für UOM, Item Group,
Price List, Workstation Operating Component, Workstation, Operation, Item, Item Price und BOM.
Dieselben Dateien lassen sich im Desk über **Data Import** einspielen (Reihenfolge = Dateiname)
oder per Importer (Onboarding 0.7, ``bench execute``)::

    bench --site <site> execute corevatis_erpnext.katalog.importieren \\
        --kwargs '{"firma": "Hülshoff Sanierungstechnik"}'
    bench --site <site> execute corevatis_erpnext.katalog.uebersicht \\
        --kwargs '{"firma": "Hülshoff Sanierungstechnik"}'

Der Importer legt nur an, was fehlt; abweichende Datensätze (z. B. im Desk geänderte Preise)
bleiben stehen und werden unter ``skipped`` gemeldet, außer mit ``force=True``.

Kalkuliert wird hier nichts: Herstellkosten je Einheit rechnet ERPNext in der Stückliste
(Material zu Einkaufspreisliste + Operation „Montage“ * Stundensatz der Workstation); die
Kalkulationslogik des Angebots gehört in die Kern-App ``corevatis`` (Aufgabe 4.1).
"""


def importieren(firma=None, paket="abdichtung", force=False, commit=True):
	"""Katalogpaket idempotent importieren. Rückgabe: ``{"changes", "skipped", "warnings", ...}``."""
	from corevatis_erpnext.katalog.importer import importieren as _importieren

	return _importieren(firma=firma, paket=paket, force=force, commit=commit)


def uebersicht(firma=None, paket="abdichtung"):
	"""Leistungspositionen des Pakets mit Stückliste, Kosten laut ERPNext und Verkaufspreis (nur lesen)."""
	from corevatis_erpnext.katalog.importer import uebersicht as _uebersicht

	return _uebersicht(firma=firma, paket=paket)


def list_pakete():
	"""Vorhandene Katalogpakete (Allowlist für ``corevatis.setup.onboard``, Aufgabe 0.7)."""
	from corevatis_erpnext.katalog.importer import list_pakete as _list

	return _list()
