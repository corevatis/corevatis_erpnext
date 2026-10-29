"""Geführte Prozessoberfläche („Prozessleiste“) für Büro und Meister.

- ``status(doctype, name)``: Stand des Vorgangs zu einem Datensatz (``prozess.kette``)
- Vorgangsübersicht, Kennzahlen, Startseite Monteur (``prozess.uebersicht``)
- Einrichtung: Arbeitsbereichs-Bausteine, Lead-Formular (``prozess.setup``)

Konzept, Regeln und Annahmen: ``docs/prozessleiste.md`` im Repo Handwerker.
"""

from corevatis_erpnext.prozess.kette import status

__all__ = ["status"]
