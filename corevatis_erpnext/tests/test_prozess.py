"""Prozessleiste: ``prozess.status`` über die Kette Lead → Termin → Objekt → Aufmaß → Angebot →
Auftrag → Projekt, Rechte (Monteur), Vorgangsübersicht mit Ampel, Idempotenz der Einrichtung.

Die CI-Bench dieses Repos hat ``corevatis`` nicht: ``Objekt`` und ``Aufmass`` sind hier
Stellvertreter-Doctypes (custom, nur für diese Klasse) mit den Feldern, die die Kette liest
(``docs/2.1-datenmodell.md``). Angebot, Annahme, Auftrag und Projekt sind echt (4.4).
"""

from __future__ import annotations

import json
import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, now_datetime, today

from corevatis_erpnext.angebot import annahme
from corevatis_erpnext.prozess import kette, setup, uebersicht
from corevatis_erpnext.prozess.kette import status
from corevatis_erpnext.tests import _angebot as td
from corevatis_erpnext.tests._helpers import make_lead, make_user

BUERO = "cv-prozess-buero@example.com"
MEISTER = "cv-prozess-meister@example.com"
MONTEUR = "cv-prozess-monteur@example.com"
STELLVERTRETER = ("Aufmass", "Objekt")
RECHTE = [
	{"role": role, "read": 1, "write": 1, "create": 1} for role in ("System Manager", "Büro", "Meister")
]


def _stellvertreter() -> None:
	if not frappe.db.exists("DocType", "Objekt"):
		frappe.get_doc(
			{
				"doctype": "DocType",
				"name": "Objekt",
				"module": "Corevatis ERPNext",
				"custom": 1,
				"autoname": "hash",
				"fields": [
					{"fieldname": "titel", "fieldtype": "Data", "label": "Titel"},
					{"fieldname": "strasse", "fieldtype": "Data", "label": "Straße"},
					{"fieldname": "plz", "fieldtype": "Data", "label": "PLZ"},
					{"fieldname": "ort", "fieldtype": "Data", "label": "Ort"},
					{"fieldname": "kunde_doctype", "fieldtype": "Link", "options": "DocType", "label": "Typ"},
					{
						"fieldname": "kunde",
						"fieldtype": "Dynamic Link",
						"options": "kunde_doctype",
						"label": "Kunde",
					},
				],
				"permissions": RECHTE,
			}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("DocType", "Aufmass"):
		frappe.get_doc(
			{
				"doctype": "DocType",
				"name": "Aufmass",
				"module": "Corevatis ERPNext",
				"custom": 1,
				"autoname": "hash",
				"fields": [
					{"fieldname": "objekt", "fieldtype": "Link", "options": "Objekt", "label": "Objekt"},
					{
						"fieldname": "status",
						"fieldtype": "Select",
						"options": "Entwurf\nAbgeschlossen",
						"default": "Entwurf",
						"label": "Status",
					},
					{
						"fieldname": "referenz_doctype",
						"fieldtype": "Link",
						"options": "DocType",
						"label": "Typ",
					},
					{
						"fieldname": "referenz_name",
						"fieldtype": "Dynamic Link",
						"options": "referenz_doctype",
						"label": "Referenz",
					},
				],
				"permissions": RECHTE,
			}
		).insert(ignore_permissions=True)


def _schritt(stand: dict, key: str) -> dict:
	return next(s for s in stand["schritte"] if s["key"] == key)


def _als(user: str, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


class TestProzessleiste(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if "corevatis" in frappe.get_installed_apps():
			raise unittest.SkipTest("corevatis installiert - die Stellvertreter-Doctypes würden kollidieren")
		_stellvertreter()
		make_user(BUERO, "Berta", ["Büro", "Sales User"], "Büro")
		make_user(MEISTER, "Martin", ["Meister", "Sales User"], "Meister")
		make_user(MONTEUR, "Mona", ["Monteur"], "Monteur")
		td.ensure_felder_41()
		td.ensure_artikel()
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for doctype in STELLVERTRETER:
			if frappe.db.exists("DocType", doctype) and frappe.db.get_value("DocType", doctype, "custom"):
				for name in frappe.get_all(doctype, pluck="name"):
					frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
				frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")

	# --- Kette ------------------------------------------------------------------------------

	def _lead(self):
		return make_lead(
			last_name=f"Prozess {frappe.generate_hash(length=6)}",
			cv_objekt_adresse="Kellerweg 7, 48143 Münster",
			mobile_no="0170 1234567",
		)

	def _termin(self, lead, tage: int = -8):
		event = frappe.get_doc(
			{
				"doctype": "Event",
				"subject": f"Besichtigung {lead.name}",
				"event_type": "Public",
				"event_category": "Meeting",
				"status": "Open",
				"starts_on": add_to_date(now_datetime(), days=tage),
				"event_participants": [{"reference_doctype": "Lead", "reference_docname": lead.name}],
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "ToDo",
				"allocated_to": MEISTER,
				"description": "Besichtigung",
				"reference_type": "Event",
				"reference_name": event.name,
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Lead", lead.name, {"cv_termin": event.name, "cv_status": "Termin"})
		return event

	def test_kette_lead_bis_projekt(self):
		lead = self._lead()

		# 1. nur die Anfrage: Termin ist dran, Aktion „Termin anlegen“
		stand = status("Lead", lead.name)
		self.assertEqual(stand["aktuell"], "termin")
		self.assertEqual(_schritt(stand, "anfrage")["status"], "erledigt")
		self.assertEqual(_schritt(stand, "termin")["aktion"]["js"], "corevatis_erpnext.termin.anlegen_dialog")
		self.assertEqual(stand["naechste_aktion"]["label"], "Termin anlegen")
		self.assertEqual(stand["vorgang"]["lead"], lead.name)
		self.assertEqual(stand["ampel"]["farbe"], "gelb")
		self.assertEqual([s["key"] for s in stand["schritte"]], list(kette.SCHRITT_KEYS))

		# 2. Termin vor 8 Tagen, kein Aufmaß: überfällig, Aktion „Objekt anlegen“ mit Anschrift
		event = self._termin(lead)
		stand = status("Lead", lead.name)
		self.assertEqual(_schritt(stand, "termin")["status"], "erledigt")
		self.assertEqual(
			_schritt(stand, "termin")["belege"], [{"doctype": "Event", "name": event.name, "info": ""}]
		)
		self.assertEqual(stand["aktuell"], "aufmass")
		aktion = _schritt(stand, "aufmass")["aktion"]
		self.assertEqual(aktion["neu"], "Objekt")
		self.assertEqual(
			aktion["werte"],
			{
				"kunde_doctype": "Lead",
				"kunde": lead.name,
				"strasse": "Kellerweg 7",
				"plz": "48143",
				"ort": "Münster",
			},
		)
		self.assertEqual(stand["ampel"]["farbe"], "rot")
		self.assertIn("Aufmaß fehlt", stand["ampel"]["text"])
		k = kette.sammeln(lead.name, "Lead", lead.name)
		self.assertEqual(kette.verantwortlich(k, "aufmass"), MEISTER)

		# 3. Objekt am Lead: Aufmaß anlegen
		objekt = frappe.get_doc(
			{
				"doctype": "Objekt",
				"titel": "Kellerweg 7, 48143 Münster",
				"strasse": "Kellerweg 7",
				"plz": "48143",
				"ort": "Münster",
				"kunde_doctype": "Lead",
				"kunde": lead.name,
			}
		).insert(ignore_permissions=True)
		stand = status("Lead", lead.name)
		self.assertEqual(_schritt(stand, "aufmass")["status"], "aktiv")
		aktion = _schritt(stand, "aufmass")["aktion"]
		self.assertEqual(aktion["neu"], "Aufmass")
		self.assertEqual(aktion["werte"]["objekt"], objekt.name)

		# 4. abgeschlossenes Aufmaß: Angebot erzeugen (4.1) am Aufmaß
		aufmass = frappe.get_doc(
			{"doctype": "Aufmass", "objekt": objekt.name, "status": "Abgeschlossen"}
		).insert(ignore_permissions=True)
		for start in (("Lead", lead.name), ("Objekt", objekt.name), ("Aufmass", aufmass.name)):
			with self.subTest(start=start):
				stand = status(*start)
				self.assertEqual(stand["aktuell"], "angebot")
				self.assertEqual(stand["vorgang"]["lead"], lead.name)
				self.assertEqual(_schritt(stand, "aufmass")["status"], "erledigt")
				self.assertEqual(
					_schritt(stand, "angebot")["aktion"],
					{
						"label": "Angebot erzeugen",
						"doctype": "Aufmass",
						"name": aufmass.name,
						"js": "corevatis_erpnext.angebot.erzeugen_dialog",
					},
				)
				self.assertEqual(stand["ampel"]["farbe"], "gelb")

		# 5. eingereichtes Angebot seit 12 Tagen ohne Nachfassen: rot; mit Nachfass-Aufgabe nicht
		frappe.db.set_value("Lead", lead.name, "cv_status", "Angebot")
		angebot = td.angebot(lead.name, zusatz=False, tage_alt=12, cv_aufmass=aufmass.name)
		stand = status("Quotation", angebot.name)
		self.assertEqual(stand["aktuell"], "angebot")
		self.assertEqual(_schritt(stand, "angebot")["status"], "aktiv")
		self.assertEqual(
			_schritt(stand, "angebot")["aktion"]["js"], "corevatis_erpnext.angebot.annehmen_dialog"
		)
		self.assertEqual(stand["ampel"]["farbe"], "rot")
		self.assertIn("ohne Nachfassen", stand["ampel"]["text"])
		todo = frappe.get_doc(
			{
				"doctype": "ToDo",
				"allocated_to": BUERO,
				"description": "nachfassen",
				"reference_type": "Quotation",
				"reference_name": angebot.name,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(status("Quotation", angebot.name)["ampel"]["farbe"], "grün")
		todo.db_set("status", "Closed")

		# 6. Annahme (4.4): Auftrag und Projekt; aktuell ist die Ausführung
		r = _als(BUERO, annahme.angebot_annehmen, angebot.name, einreichen=1)
		self.assertTrue(r["auftrag"] and r["projekt"])
		starts = (
			("Lead", lead.name),
			("Aufmass", aufmass.name),
			("Quotation", angebot.name),
			("Sales Order", r["auftrag"]),
			("Project", r["projekt"]),
		)
		for start in starts:
			with self.subTest(start=start):
				stand = status(*start)
				self.assertEqual(stand["aktuell"], "ausfuehrung", start)
				self.assertEqual(stand["vorgang"]["lead"], lead.name)
				erwartet = {
					"anfrage": "erledigt",
					"termin": "erledigt",
					"aufmass": "erledigt",
					"angebot": "erledigt",
					"auftrag": "erledigt",
					"ausfuehrung": "aktiv",
					"abnahme": "offen",
					"rechnung": "offen",
				}
				self.assertEqual({s["key"]: s["status"] for s in stand["schritte"]}, erwartet)
				self.assertEqual(
					_schritt(stand, "auftrag")["belege"],
					[{"doctype": "Sales Order", "name": r["auftrag"], "info": ""}],
				)
				self.assertEqual(_schritt(stand, "ausfuehrung")["belege"][0]["name"], r["projekt"])
				if not frappe.db.count("Task", {"project": r["projekt"]}):
					# ohne Arbeitspakete: 5.1 erzeugen (Projekt mit Auftrag)
					self.assertEqual(
						_schritt(stand, "ausfuehrung")["aktion"]["js"],
						"corevatis_erpnext.prozess.arbeitspakete_erzeugen",
					)
				self.assertEqual(
					_schritt(stand, "rechnung")["aktion"]["js"],
					"corevatis_erpnext.prozess.rechnung_aus_auftrag",
				)
		self.assertEqual(status("Project", r["projekt"])["ampel"]["farbe"], "grün")

		# geplanter Start vor 5 Tagen, keine Zeitbuchung: überfällig
		frappe.db.set_value("Project", r["projekt"], "expected_start_date", add_days(today(), -5))
		stand = status("Project", r["projekt"])
		self.assertEqual(stand["ampel"]["farbe"], "rot")
		self.assertIn("ohne Zeitbuchung", stand["ampel"]["text"])

		# Vorgangsübersicht: eine Zeile mit Ampel, Filter
		zeilen = [z for z in uebersicht.vorgaenge({}) if z["vorgang"] == lead.name]
		self.assertEqual(len(zeilen), 1)
		zeile = zeilen[0]
		self.assertEqual((zeile["schritt"], zeile["ampel"]), ("Ausführung", "rot"))
		self.assertEqual((zeile["beleg_typ"], zeile["beleg"]), ("Project", r["projekt"]))
		self.assertEqual(zeile["objekt_adresse"], "Kellerweg 7, 48143 Münster")
		if not frappe.db.count("Task", {"project": r["projekt"]}):
			self.assertEqual(zeile["naechster_schritt"], "Arbeitspakete erzeugen")
		rot = uebersicht.vorgaenge({"ampel": "rot"})
		self.assertIn(lead.name, [z["vorgang"] for z in rot])
		self.assertTrue(all(z["ampel"] == "rot" for z in rot))
		self.assertNotIn(lead.name, [z["vorgang"] for z in uebersicht.vorgaenge({"schritt": "Termin"})])
		self.assertIn(lead.name, [z["vorgang"] for z in uebersicht.vorgaenge({"schritt": "Ausführung"})])

		from corevatis_erpnext.corevatis_erpnext.report.vorgangsliste.vorgangsliste import execute

		spalten, daten, _msg, _chart, summary = execute({"ampel": "rot"})
		self.assertIn("ampel", [s["fieldname"] for s in spalten])
		self.assertIn(lead.name, [z["vorgang"] for z in daten])
		self.assertEqual(summary[1]["value"], len(daten))

		self._rechte_monteur(lead, angebot.name, r)

	def _rechte_monteur(self, lead, angebot: str, r: dict):
		"""Monteur sieht nur erlaubte Schritte: keine Namen von Lead, Angebot, Auftrag, Kunde."""
		with self.assertRaises(frappe.PermissionError):
			_als(MONTEUR, status, "Lead", lead.name)
		with self.assertRaises(frappe.PermissionError):
			_als(MONTEUR, status, "Quotation", angebot)
		self.assertEqual(_als(MONTEUR, uebersicht.vorgaenge, {}), [])

		if not _als(MONTEUR, frappe.has_permission, "Project", "read", r["projekt"]):
			self.skipTest("Monteur ohne Leserecht auf Projekte (Site-Template nicht angewendet)")
		stand = _als(MONTEUR, status, "Project", r["projekt"])
		self.assertEqual(stand["aktuell"], "ausfuehrung")
		self.assertIsNone(stand["vorgang"]["lead"])
		self.assertIsNone(stand["vorgang"]["titel"])
		for key in ("anfrage", "angebot", "auftrag"):
			schritt = _schritt(stand, key)
			self.assertTrue(schritt["gesperrt"], key)
			self.assertEqual((schritt["belege"], schritt["text"], schritt["aktion"]), ([], "", None), key)
			self.assertEqual(schritt["status"], "erledigt", key)
		self.assertEqual(_schritt(stand, "ausfuehrung")["belege"][0]["name"], r["projekt"])
		# keine Aktionen, für die der Monteur keine Rechte hat (Rechnung, Angebot)
		self.assertIsNone(_schritt(stand, "rechnung")["aktion"])
		text = json.dumps(stand, default=str)
		kunde_name = frappe.db.get_value("Customer", r["kunde"], "customer_name")
		for geheim in (lead.name, angebot, r["auftrag"], r["kunde"], kunde_name, lead.lead_name):
			self.assertNotIn(geheim, text)

	def test_verloren_und_ohne_lead(self):
		lead = self._lead()
		frappe.db.set_value("Lead", lead.name, {"cv_status": "Verloren", "cv_verlustgrund": "Preis"})
		stand = status("Lead", lead.name)
		self.assertEqual(stand["vorgang"]["status"], "verloren")
		self.assertEqual(stand["aktuell"], "anfrage")
		self.assertEqual(stand["ampel"]["farbe"], "grau")
		self.assertIsNone(_schritt(stand, "termin")["aktion"])
		self.assertNotIn(lead.name, [z["vorgang"] for z in uebersicht.vorgaenge({})])
		self.assertIn(lead.name, [z["vorgang"] for z in uebersicht.vorgaenge({"nur_offene": 0})])

		# Angebot an einen Kunden ohne Anfrage: Kette beginnt beim Angebot, Termin/Aufmaß entfallen
		kunde = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"Prozess Bestandskunde {frappe.generate_hash(length=6)}",
				"customer_type": "Individual",
				"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}),
				"territory": frappe.db.get_value("Territory", {"is_group": 0}),
			}
		).insert(ignore_permissions=True)
		angebot = td.angebot(kunde.name, quotation_to="Customer", zusatz=False, corevatis=False)
		stand = status("Quotation", angebot.name)
		self.assertIsNone(stand["vorgang"]["lead"])
		self.assertEqual(
			[_schritt(stand, key)["status"] for key in ("anfrage", "termin", "aufmass")],
			["entfaellt", "entfaellt", "entfaellt"],
		)
		self.assertEqual(stand["aktuell"], "angebot")

	def test_verantwortlich_ohne_guest(self):
		lead = self._lead()
		frappe.db.set_value("Lead", lead.name, {"owner": "Guest", "lead_owner": None})
		self.assertIsNone(kette.verantwortlich(kette.sammeln(lead.name, "Lead", lead.name), "termin"))
		frappe.db.set_value("Lead", lead.name, "lead_owner", BUERO)
		self.assertEqual(kette.verantwortlich(kette.sammeln(lead.name, "Lead", lead.name), "termin"), BUERO)

	def test_unbekannter_doctype(self):
		with self.assertRaises(frappe.ValidationError):
			status("User", "Administrator")
		with self.assertRaises(frappe.DoesNotExistError):
			status("Lead", "gibt-es-nicht")

	def test_meine_baustellen(self):
		projekt = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"Baustelle {frappe.generate_hash(length=6)}",
				"status": "Open",
				"company": td.firma(),
			}
		).insert(ignore_permissions=True)
		task = frappe.get_doc(
			{
				"doctype": "Task",
				"subject": "Dichtschlämme Keller",
				"project": projekt.name,
				"exp_start_date": today(),
			}
		).insert(ignore_permissions=True)
		spaeter = frappe.get_doc(
			{
				"doctype": "Task",
				"subject": "Sanierputz",
				"project": projekt.name,
				"exp_start_date": add_days(today(), 3),
			}
		).insert(ignore_permissions=True)
		for t in (task, spaeter):
			frappe.db.set_value("Task", t.name, "_assign", json.dumps([MONTEUR]))
		if not _als(MONTEUR, frappe.has_permission, "Task", "read"):
			self.skipTest("Monteur ohne Leserecht auf Arbeitspakete (Site-Template nicht angewendet)")
		daten = _als(MONTEUR, uebersicht.meine_baustellen)
		namen = [t["name"] for t in daten["tasks"]]
		self.assertIn(task.name, namen)
		self.assertNotIn(spaeter.name, namen)
		# Büro/Meister sehen die Vorgangsübersicht, Monteure nicht
		self.assertEqual(_als(MONTEUR, uebersicht.block_vorgaenge)["zeilen"], [])
		self.assertEqual(_als(MONTEUR, uebersicht.karte_ueberfaellig)["value"], 0)


MONTEUR_EINRICHTUNG = "cv-prozess-monteur2@example.com"
OHNE_ROLLE = "cv-prozess-ohne-rolle@example.com"


class TestProzessEinrichtung(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(MONTEUR_EINRICHTUNG, "Moritz", ["Monteur"], "Monteur")
		make_user(OHNE_ROLLE, "Otto", [], "Ohne")

	def test_lead_formular(self):
		setup.ensure_prozess_setup()
		meta = frappe.get_meta("Lead")
		for feld in ("naming_series", "middle_name", "job_title", "gender", "type", "request_type", "status"):
			self.assertTrue(meta.get_field(feld).hidden, feld)
		self.assertTrue(meta.get_field("organization_section").collapsible)
		self.assertEqual(meta.get_field("lead_owner").label, "Verantwortlich")
		# Bauteil/Symptome erst ab Anfragestatus „Termin“ Pflicht (Befunde Nr. 22)
		for feld in ("cv_bauteil", "cv_symptome"):
			ausdruck = meta.get_field(feld).mandatory_depends_on
			self.assertIn("'Termin'", ausdruck)
			self.assertNotIn("'Neu'", ausdruck)

	def test_idempotent(self):
		setup.ensure_prozess_setup()

		def stand():
			return (
				frappe.get_all(
					"Property Setter",
					{"doc_type": "Lead", "module": setup.MODULE},
					["name", "value", "modified"],
					order_by="name",
				),
				frappe.get_all(
					"Number Card", {"name": ["like", "Vorgänge%"]}, ["name", "modified"], order_by="name"
				),
				frappe.get_all(
					"Custom HTML Block",
					{"name": ["in", [b["name"] for b in setup.BLOECKE_DEF]]},
					["name", "modified"],
					order_by="name",
				),
			)

		vorher = stand()
		self.assertEqual(len(vorher[0]), len(setup.LEAD_FORMULAR))
		self.assertEqual(len(vorher[1]), len(setup.KENNZAHLEN))
		self.assertEqual(len(vorher[2]), len(setup.BLOECKE_DEF))
		setup.ensure_prozess_setup()
		setup.ensure_prozess_setup()
		self.assertEqual(stand(), vorher)
		self.assertEqual(setup.ensure_lead_formular(), [])

	def test_eigene_anpassung_bleibt(self):
		"""Ein im Desk gesetzter Property Setter (ohne Modul) wird nicht überschrieben."""
		from frappe.custom.doctype.property_setter.property_setter import make_property_setter

		ps = make_property_setter(
			"Lead", "job_title", "hidden", "0", "Check", validate_fields_for_doctype=False
		)
		frappe.db.set_value("Property Setter", ps.name, "module", None)
		try:
			setup.ensure_lead_formular()
			self.assertEqual(frappe.db.get_value("Property Setter", ps.name, "value"), "0")
		finally:
			frappe.delete_doc("Property Setter", ps.name, force=True, ignore_permissions=True)
			setup.ensure_lead_formular()
		self.assertEqual(
			frappe.db.get_value("Property Setter", {"doc_type": "Lead", "field_name": "job_title"}, "value"),
			"1",
		)

	def test_arbeitsbereiche_und_kacheln(self):
		ws = frappe.get_doc("Workspace", "Vorgänge")
		self.assertEqual(ws.module, "Corevatis ERPNext")
		self.assertEqual({r.role for r in ws.roles}, {"Büro", "Meister", "System Manager"})
		inhalt = frappe.parse_json(ws.content)
		self.assertEqual(inhalt[0]["type"], "custom_block")
		self.assertEqual(inhalt[0]["data"]["custom_block_name"], setup.BLOCK_VORGAENGE)
		self.assertEqual(
			{b["data"]["shortcut_name"] for b in inhalt if b["type"] == "shortcut"},
			{s.label for s in ws.shortcuts},
		)
		for karte in ws.number_cards:
			self.assertTrue(frappe.db.exists("Number Card", karte.number_card_name), karte.number_card_name)
		self.assertTrue(frappe.db.exists("Custom HTML Block", setup.BLOCK_VORGAENGE))
		self.assertTrue(frappe.db.exists("Report", "Vorgangsliste"))

		mb = frappe.get_doc("Workspace", "Meine Baustellen")
		self.assertIn("Monteur", {r.role for r in mb.roles})
		self.assertTrue(frappe.db.exists("Custom HTML Block", setup.BLOCK_BAUSTELLEN))

		kacheln = frappe.get_hooks("add_to_apps_screen", app_name="corevatis_erpnext")
		# v16 zeigt je App nur den ersten Eintrag: eine Kachel, /cv_start leitet je Rolle weiter
		self.assertEqual([(k["title"], k["route"]) for k in kacheln], [("Vorgänge", "/cv_start")])
		frappe.get_attr(kacheln[0]["has_permission"])
		self.assertEqual(setup.ziel_startseite(), setup.ROUTE_VORGAENGE)
		self.assertEqual(_als(MONTEUR_EINRICHTUNG, setup.ziel_startseite), setup.ROUTE_BAUSTELLEN)
		self.assertTrue(_als(MONTEUR_EINRICHTUNG, setup.hat_startkachel_zugriff))
		self.assertEqual(_als(OHNE_ROLLE, setup.ziel_startseite), "/desk")
		self.assertFalse(_als(OHNE_ROLLE, setup.hat_startkachel_zugriff))
		from corevatis_erpnext.www import cv_start

		with self.assertRaises(frappe.Redirect):
			cv_start.get_context(frappe._dict())
		self.assertEqual(frappe.local.flags.redirect_location, setup.ROUTE_VORGAENGE)
		frappe.local.flags.redirect_location = None

		self.assertTrue(frappe.db.exists("DocType", "Corevatis Prozess Einstellungen"))
		from corevatis_erpnext.prozess.einstellungen import einstellungen

		self.assertEqual(einstellungen().ampel_termin_ohne_aufmass_tage, 7)

	def test_hooks_doctype_js(self):
		js = frappe.get_hooks("doctype_js", app_name="corevatis_erpnext")
		for doctype in kette.STARTDOCTYPES:
			self.assertIn("public/js/prozessleiste.js", js.get(doctype) or [], doctype)
		self.assertIn("public/js/prozess_lead.js", js["Lead"])

	def test_uebersetzungen(self):
		from frappe.translate import get_translations_from_csv

		de = get_translations_from_csv("de", "corevatis_erpnext")
		meta = frappe.get_meta("Corevatis Prozess Einstellungen")
		for df in meta.fields:
			if df.label:
				self.assertIn(df.label, de, df.label)
