"""Leiste „Betrieb“ je Rolle (Desk-Navigation, Handwerker-Doku ``docs/desk-navigation.md``).

Frappe v16 blendet Einträge der Seitenleiste schon nach Leserecht aus (``is_item_allowed``:
DocType-Leserecht, Rollen an Page, Report und Workspace). Ein Rollenfeld je Eintrag hat der
Doctype ``Sidebar Item`` nicht. Büro, Meister und Monteur dürfen aber mehr lesen, als sie in der
Leiste sehen sollen (der Meister liest Kunden und Rechnungen aus dem Projekt heraus).

Deshalb legt ``boot_session`` je Benutzer die Schlüssel der Einträge, die er nicht sehen soll, in
``frappe.boot.cv_navigation`` ab; ``public/js/betrieb.bundle.js`` lässt sie beim Zeichnen der Leiste
weg. Die Einträge bleiben in ``frappe.boot.module_sidebars``: Frappe entscheidet anhand dieser Liste,
ob ein geöffneter Beleg in der Shell „Betrieb“ bleibt (``shell_can_show``). Fehlte dort z. B. der
Kunde, spränge der Meister beim Öffnen eines Kunden aus dem Projekt in die Shell „Vertrieb“.

Die Rechte bleiben an den Doctypes; das hier ist nur Ordnung in der Leiste.
"""

from __future__ import annotations

import frappe

SHELL = "Betrieb"

BUERO = "Büro"
MEISTER = "Meister"
MONTEUR = "Monteur"
ROLLEN_FACH = frozenset({BUERO, MEISTER, MONTEUR})
ROLLEN_VOLL = frozenset({"System Manager"})

NIEMAND: frozenset[str] = frozenset()
B = frozenset({BUERO})
BM = frozenset({BUERO, MEISTER})
BMM = frozenset({BUERO, MEISTER, MONTEUR})

# Wer welchen Eintrag sieht, je (link_type, link_to). Jeder Link in ``betrieb.json`` steht hier
# (``tests/test_sidebar.py``). System Manager und Administrator sehen alles, Benutzer ohne Fachrolle
# werden nicht gefiltert (nur Frappes Leserecht).
SICHTBAR: dict[tuple[str, str], frozenset[str]] = {
	# Start (oberste Ebene)
	("Workspace", "Vorgänge"): BM,
	("Workspace", "Baustellen"): BM,
	("Workspace", "Meine Baustellen"): frozenset({MONTEUR}),
	("Page", "plantafel"): BM,
	("DocType", "Event"): BMM,
	("DocType", "ToDo"): BMM,
	("Page", "nachrichten"): BMM,
	# Anfrage bis Auftrag
	("Workspace", "Anfragen"): BM,
	# Anker für die Shell: Leads öffnen sich in „Betrieb“, die Liste erreicht man über „Anfragen“
	("DocType", "Lead"): NIEMAND,
	("DocType", "Objekt"): BM,
	("DocType", "Befund"): BM,
	("DocType", "Aufmass"): BM,
	("DocType", "Plan"): BM,
	("DocType", "Quotation"): BM,
	("DocType", "Sales Order"): BM,
	("DocType", "Kundenzugang"): B,
	("Report", "Vorgangsliste"): BM,
	# Ausführung
	("DocType", "Project"): BM,
	("DocType", "Task"): BM,
	("DocType", "Tagesrapport"): BM,
	("DocType", "Bautagebuch Tag"): BM,
	("DocType", "Baustellenmeldung"): BM,
	("DocType", "Abnahmeprotokoll"): BM,
	("Page", "mappe"): BM,
	("DocType", "Timesheet"): BMM,
	("Report", "Stundenkonto"): BMM,  # Monteur: eigene Zeile (P2.5)
	("DocType", "Zeitbuchung Lauf"): BM,
	("Report", "Nachkalkulation"): BM,
	# Material
	("DocType", "Stock Entry"): BM,
	("DocType", "Material Request"): BM,
	("DocType", "Purchase Order"): BM,
	("DocType", "Purchase Receipt"): BM,
	("Report", "Nachbestellen"): BM,
	("Report", "Fahrzeuglager"): BM,
	# Abrechnung
	("DocType", "Sales Invoice"): B,
	("DocType", "Abschlagsplan"): B,
	("Report", "Offene Posten"): B,
	("Report", "Zahlungseingang"): B,
	("DocType", "Dunning"): B,
	("DocType", "DATEV Export"): B,
	# Stammdaten
	("DocType", "Customer"): B,
	("DocType", "Employee"): BM,
	("DocType", "Abwesenheit"): BM,
	("DocType", "Fahrzeug"): B,
	("DocType", "Item"): BM,
	("DocType", "Terms and Conditions"): B,
	("DocType", "Warehouse"): B,
	("DocType", "Supplier"): B,
	# Einstellungen
	("DocType", "Corevatis Vertrieb Einstellungen"): B,
	("DocType", "Corevatis Planung Einstellungen"): B,
	("DocType", "Corevatis Prozess Einstellungen"): B,
	("DocType", "Corevatis Abnahme Einstellungen"): B,
	("DocType", "Corevatis Abrechnung Einstellungen"): B,
	("Report", "Mengenregeln nachjustieren"): B,
	("DocType", "Geokoordinate"): B,
	("DocType", "Corevatis Geraet"): B,
}

# Ein Eintrag „Baustellen“ je Benutzer: „Meine Baustellen“ nur, wenn „Baustellen“ nicht sichtbar ist
# (Administrator und Benutzer mit Büro/Meister und Monteur sähen sonst beide).
ERSETZT_DURCH: dict[tuple[str, str], tuple[str, str]] = {
	("Workspace", "Meine Baustellen"): ("Workspace", "Baustellen"),
}


def _ziel(item) -> tuple[str, str]:
	return (item.get("link_type") or "", item.get("link_to") or "")


def _schluessel(item) -> str:
	if item.get("key"):
		return item["key"]
	from frappe.desk.doctype.sidebar.sidebar import item_key

	return item_key(item)


def _erlaubt(ziel, rollen: set[str], voll: bool) -> bool:
	erlaubt = SICHTBAR.get(ziel)
	if erlaubt is None:
		return True  # unbekannter Eintrag (z. B. vom Benutzer ergänzt): Frappe entscheidet
	if not erlaubt:
		return False
	fach = rollen & ROLLEN_FACH
	if voll or not fach:
		return True
	return bool(fach & erlaubt)


def ausgeblendet(items, rollen, voll: bool | None = None) -> list[str]:
	"""Schlüssel der Links, die ein Benutzer mit ``rollen`` in der Leiste nicht sehen soll."""
	rollen = set(rollen)
	if voll is None:
		voll = bool(rollen & ROLLEN_VOLL)
	links = [i for i in items or [] if i.get("type") == "Link"]
	sichtbar = {_ziel(i) for i in links if _erlaubt(_ziel(i), rollen, voll)}
	sichtbar -= {ziel for ziel, ersatz in ERSETZT_DURCH.items() if ersatz in sichtbar}
	return [_schluessel(i) for i in links if _ziel(i) not in sichtbar]


def boot_session(bootinfo) -> None:
	"""``frappe.boot.cv_navigation = {shell: [Schlüssel, …]}`` für die Leiste „Betrieb“."""
	leisten = bootinfo.get("module_sidebars") or {}
	leiste = leisten.get(SHELL)
	if not leiste:
		return
	voll = frappe.session.user == "Administrator" or None
	bootinfo["cv_navigation"] = {SHELL: ausgeblendet(leiste.get("items"), frappe.get_roles(), voll)}
