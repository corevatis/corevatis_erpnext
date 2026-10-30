"""Vertrag mit dem Kern ``corevatis`` (Aufgabe 6.1): Doctype-, Feld- und Wertenamen des
Abnahmeprotokolls. Der Adapter importiert den Kern nicht (Lizenzgrenze); der Vertragstest im Kern
(``corevatis/tests/test_erpnext_contract.py``) prüft diese Werte gegen ``corevatis.abnahme``.
"""

CORE_APP = "corevatis"
ABNAHMEPROTOKOLL = "Abnahmeprotokoll"
ABNAHME_MANGEL = "Abnahme Mangel"
STATUS_ABGESCHLOSSEN = "Abgeschlossen"
STATUS_ENTWURF = "Entwurf"
ERGEBNIS_ABGENOMMEN = "Abgenommen"
ERGEBNIS_MIT_MAENGELN = "Abgenommen mit Mängeln"
ERGEBNIS_VERWEIGERT = "Abnahme verweigert"
ERGEBNISSE = (ERGEBNIS_ABGENOMMEN, ERGEBNIS_MIT_MAENGELN, ERGEBNIS_VERWEIGERT)
ERGEBNISSE_ABGENOMMEN = (ERGEBNIS_ABGENOMMEN, ERGEBNIS_MIT_MAENGELN)
MANGEL_OFFEN = "Offen"
MANGEL_BEHOBEN = "Behoben"
# Felder, die der Adapter liest (Protokoll) bzw. liest und schreibt (Mangelzeile: task)
PROTOKOLL_FELDER = (
	"projekt",
	"status",
	"ergebnis",
	"datum",
	"gewaehrleistung_beginn",
	"abgeschlossen_am",
	"vorbehalt_maengel",
	"vorbehalt_vertragsstrafe",
	"maengel",
)
MANGEL_FELDER = ("beschreibung", "raum", "bauteil", "frist", "status", "task", "foto")
# Mangelstatus setzen (Protokolleintrag, modified) - per frappe.get_attr, kein Import
KERN_MANGEL_STATUS = "corevatis.abnahme.mangel.status_setzen"

# Rollen
ROLLEN_ABNAHME = ("Büro", "Meister", "System Manager")
ROLLE_UEBERSTEUERN = "System Manager"
