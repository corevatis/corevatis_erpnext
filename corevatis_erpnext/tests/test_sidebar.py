"""Desk-Navigation: Struktur der Leiste „Betrieb“ (``sidebar/betrieb/betrieb.json``) und Einträge je Rolle
(``prozess/navigation.py``). Handwerker-Doku ``docs/desk-navigation.md``.

Die Kern-App ``corevatis`` ist auf der CI-Site nicht installiert; ihre Doctypes und Pages stehen in
``KERN`` und werden nur geprüft, wenn der Kern installiert ist."""

from __future__ import annotations

import json
import pathlib
import re
from collections import Counter

import frappe
from frappe.tests import IntegrationTestCase

from corevatis_erpnext.prozess import navigation

BASIS = pathlib.Path(__file__).parents[1]
DATEI = BASIS / "corevatis_erpnext/sidebar/betrieb/betrieb.json"

GRUPPEN = ["Anfrage bis Auftrag", "Ausführung", "Material", "Abrechnung", "Stammdaten", "Einstellungen"]
START = ["Vorgänge", "Baustellen", "Baustellen", "Plantafel", "Termine", "Aufgaben", "Nachrichten"]

KERN = {
	("DocType", "Objekt"),
	("DocType", "Befund"),
	("DocType", "Aufmass"),
	("DocType", "Plan"),
	("DocType", "Tagesrapport"),
	("DocType", "Baustellenmeldung"),
	("DocType", "Bautagebuch Tag"),
	("DocType", "Abnahmeprotokoll"),
	("DocType", "Corevatis Geraet"),
	("Page", "nachrichten"),
	("Page", "mappe"),
}

# Was jede Rolle in der Leiste sieht (Gruppe -> Beschriftungen), wie in docs/desk-navigation.md
ERWARTET = {
	"Büro": {
		"": ["Vorgänge", "Baustellen", "Plantafel", "Termine", "Aufgaben", "Nachrichten"],
		"Anfrage bis Auftrag": [
			"Anfragen",
			"Objekte",
			"Befunde",
			"Aufmaße",
			"Pläne",
			"Angebote",
			"Aufträge",
			"Kundenzugänge",
			"Vorgangsliste",
		],
		"Ausführung": [
			"Projekte",
			"Arbeitspakete",
			"Tagesrapporte",
			"Bautagebuch",
			"Meldungen",
			"Abnahmen",
			"Mappen",
			"Zeiten",
			"Laufende Zeiten",
		],
		"Material": [
			"Materialbuchungen",
			"Materialanforderungen",
			"Bestellungen",
			"Lieferungen",
			"Nachbestellen",
			"Fahrzeuglager",
		],
		"Abrechnung": [
			"Rechnungen",
			"Abschlagspläne",
			"Offene Posten",
			"Zahlungseingang",
			"Mahnungen",
			"Nachkalkulation",
			"DATEV-Export",
		],
		"Stammdaten": [
			"Kunden",
			"Mitarbeiter",
			"Abwesenheiten",
			"Stundenkonto",
			"Fahrzeuge",
			"Katalog",
			"Textbausteine",
			"Lager",
			"Lieferanten",
		],
		"Einstellungen": [
			"Vertrieb",
			"Planung",
			"Prozess",
			"Abnahme",
			"Abrechnung",
			"Regeln nachjustieren",
			"Geokoordinaten",
			"Geräte",
		],
	},
}
ERWARTET["Meister"] = {
	"": ["Vorgänge", "Baustellen", "Plantafel", "Termine", "Aufgaben", "Nachrichten"],
	"Anfrage bis Auftrag": [x for x in ERWARTET["Büro"]["Anfrage bis Auftrag"] if x != "Kundenzugänge"],
	"Ausführung": ERWARTET["Büro"]["Ausführung"],
	"Material": ERWARTET["Büro"]["Material"],
	"Stammdaten": ["Mitarbeiter", "Abwesenheiten", "Stundenkonto", "Katalog"],
}
ERWARTET["Monteur"] = {
	"": ["Baustellen", "Termine", "Aufgaben", "Nachrichten"],
	"Ausführung": ["Zeiten"],
}


def _daten() -> dict:
	return json.loads(DATEI.read_text(encoding="utf-8"))


def _ziel(i) -> tuple[str, str]:
	return (i.get("link_type") or "", i.get("link_to") or "")


def _key(i) -> str:
	return "|".join(i.get(f) or "" for f in ("type", "link_type", "link_to", "url", "filters"))


def _sicht(items, rollen, voll=None) -> dict[str, list[str]]:
	"""Gruppe -> sichtbare Beschriftungen, wie Leiste und betrieb.bundle.js sie zeichnen."""
	items = [dict(i, key=_key(i)) for i in items]
	aus = set(navigation.ausgeblendet(items, rollen, voll))
	sicht: dict[str, list[str]] = {}
	gruppe = ""
	for i in items:
		if i["type"] == "Section Break":
			gruppe = i["label"]
		elif i["key"] not in aus:
			sicht.setdefault(gruppe, []).append(i["label"])
	return sicht


class TestSidebar(IntegrationTestCase):
	def test_struktur(self):
		items = _daten()["items"]
		self.assertEqual([i["idx"] for i in items], list(range(1, len(items) + 1)))
		self.assertEqual([i["label"] for i in items if i["type"] == "Section Break"], GRUPPEN)
		erste_gruppe = next(n for n, i in enumerate(items) if i["type"] == "Section Break")
		# Start: oberste Ebene ohne Abschnitt, immer sichtbar
		self.assertEqual([i["label"] for i in items[:erste_gruppe]], START)
		self.assertTrue(all(i["type"] == "Link" and not i["child"] for i in items[:erste_gruppe]))
		for i in items[erste_gruppe:]:
			if i["type"] == "Section Break":
				# klappbar, anfangs zu, mit Symbol (Frappe merkt den Zustand je Leiste im localStorage)
				self.assertEqual((i["indent"], i["collapsible"], i["keep_closed"]), (1, 1, 1), i["label"])
				self.assertTrue(i.get("icon"), i["label"])
			else:
				self.assertEqual((i["type"], i["child"]), ("Link", 1), i["label"])
		# Gruppen tragen eigene Symbole und heißen nicht wie ein Eintrag der obersten Ebene
		gruppen = [i for i in items if i["type"] == "Section Break"]
		self.assertEqual(len({i["icon"] for i in gruppen}), len(gruppen))
		self.assertFalse({i["label"] for i in gruppen} & set(START))

	def test_keine_doppelten_links(self):
		links = [_key(i) for i in _daten()["items"] if i["type"] == "Link"]
		self.assertEqual([k for k, n in Counter(links).items() if n > 1], [])

	def test_links_zeigen_auf_vorhandenes(self):
		kern = "corevatis" in frappe.get_installed_apps()
		tabelle = {"DocType": "DocType", "Report": "Report", "Page": "Page", "Workspace": "Workspace"}
		for i in _daten()["items"]:
			if i["type"] != "Link":
				continue
			ziel = _ziel(i)
			self.assertIn(ziel[0], tabelle, i["label"])
			if ziel in KERN and not kern:
				continue
			self.assertTrue(frappe.db.exists(tabelle[ziel[0]], ziel[1]), f"{i['label']}: {ziel}")

	def test_symbole_vorhanden(self):
		sprite = pathlib.Path(frappe.get_app_path("frappe", "public", "icons", "lucide.svg")).read_text()
		vorhanden = set(re.findall(r'id="icon-([^"]+)"', sprite))
		for i in _daten()["items"]:
			self.assertIn(i["icon"], vorhanden, i["label"])

	def test_jeder_link_hat_eine_rollenregel(self):
		links = {_ziel(i) for i in _daten()["items"] if i["type"] == "Link"}
		self.assertEqual(links, set(navigation.SICHTBAR))

	def test_sicht_je_rolle(self):
		items = _daten()["items"]
		for rolle, erwartet in ERWARTET.items():
			with self.subTest(rolle=rolle):
				self.assertEqual(_sicht(items, {rolle, "Employee"}), erwartet)
		# Büro und Meister: ein Eintrag „Baustellen“ (Arbeitsbereich), Monteur „Meine Baustellen“
		self.assertEqual(_sicht(items, {"Meister", "Monteur"})[""].count("Baustellen"), 1)
		self.assertEqual(_sicht(items, {"Monteur"}, voll=True)[""].count("Baustellen"), 1)
		self.assertNotIn("Kundenzugänge", _sicht(items, {"Meister", "Monteur"})["Anfrage bis Auftrag"])
		# System Manager und Administrator sehen alles wie das Büro, Leads nur über „Anfragen“
		self.assertEqual(_sicht(items, {"System Manager"}), ERWARTET["Büro"])
		self.assertEqual(_sicht(items, {"Monteur"}, voll=True), ERWARTET["Büro"])
		# Benutzer ohne Fachrolle: Frappes Leserecht entscheidet allein
		ohne = _sicht(items, {"Accounts User"})
		self.assertIn("Rechnungen", ohne["Abrechnung"])
		self.assertNotIn("Alle Anfragen", ohne["Anfrage bis Auftrag"])

	def test_leiste_auf_der_site_und_boot(self):
		"""``migrate`` hat die Leiste übernommen; die Schlüssel des Boot-Hooks passen zu Frappes Payload."""
		from frappe.desk.doctype.sidebar.sidebar import resolve_sidebar

		doc = frappe.get_doc("Sidebar", "Betrieb")
		erwartet = [(i["type"], i["label"], i["child"], i["indent"]) for i in _daten()["items"]]
		self.assertEqual([(i.type, i.label, i.child, i.indent) for i in doc.items], erwartet)

		payload = resolve_sidebar(navigation.SHELL, "Administrator").as_boot_entry()
		keys = {i["key"] for i in payload["items"]}
		self.assertIn("Link|DocType|Lead||", keys)
		boot = frappe._dict(module_sidebars={navigation.SHELL: payload})
		frappe.set_user("Administrator")
		navigation.boot_session(boot)
		aus = boot.cv_navigation[navigation.SHELL]
		self.assertTrue(set(aus) <= keys)
		self.assertEqual(set(aus), {"Link|DocType|Lead||", "Link|Workspace|Meine Baustellen||"})
		# Monteur: Einträge bleiben im Payload (Shell „Betrieb“ hält die Belege), nur ausgeblendet
		aus_monteur = navigation.ausgeblendet(payload["items"], {"Monteur"})
		self.assertIn("Link|DocType|Project||", aus_monteur)
		self.assertIn("Link|DocType|Project||", keys)

	def test_bundle_haengt_sich_ein(self):
		js = (BASIS / "public/js/betrieb.bundle.js").read_text(encoding="utf-8")
		self.assertIn('"sidebar_setup"', js)
		self.assertIn("cv_navigation", js)
		self.assertIn("corevatis_erpnext.prozess.navigation.boot_session", frappe.get_hooks("boot_session"))
