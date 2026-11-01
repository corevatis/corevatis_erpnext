"""Büro-Dashboard „Baustellen“ (5.9, Handwerker#43): Zeile je laufendem Projekt, Ampel, Fortschritt, nächster
Termin, Mängel, Filter, Rechte, Kennzahlen und Arbeitsbereich.

Wie ``test_nachkalkulation``: Die CI-Bench hat ``corevatis`` nicht, ``KernStellvertreter`` ersetzt die Engine.
Angebot mit Kalkulation (10 Einheiten x 22 min = 3,67 Sollstunden), Annahme, Auftrag, Projekt, Arbeitspaket und
Timesheets sind echt (ERPNext). Soll und Ist kommen aus der Nachkalkulation; geprüft wird, was das Dashboard
daraus macht.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, get_fullname, getdate, today

from corevatis_erpnext import baustellen as b
from corevatis_erpnext import zeiterfassung as z
from corevatis_erpnext.abrechnung import kern
from corevatis_erpnext.tests import _abrechnung as H
from corevatis_erpnext.tests._abrechnung import BUERO, MEISTER, MONTEUR, _als
from corevatis_erpnext.tests.test_zeiterfassung import ensure_employee, ts

HEUTE = getdate(today())


class TestBaustellen(IntegrationTestCase):
	_lauf = 0  # je Test ein eigener Tag (Zeiten desselben Monteurs dürfen sich nicht überschneiden)

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if kern.KERN_APP in frappe.get_installed_apps():
			raise unittest.SkipTest(
				"corevatis installiert - Stellvertreter der Engine würde die echte überdecken"
			)
		frappe.set_user("Administrator")
		H.einrichten()
		z.ensure_setup()
		b.ensure_setup()
		cls.employee = ensure_employee(MONTEUR)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.cache.delete_keys("cv_baustellen")
		H.kern_patch(self)
		self.auftrag, self.projekt = H.vorgang(kalkulation=True)
		self.task = self.arbeitspaket()
		# feste Termine: Paket beginnt morgen, Projektende in 30 Tagen (nichts überfällig)
		frappe.db.set_value("Project", self.projekt, "expected_end_date", add_days(HEUTE, 30))
		for name in frappe.get_all("Task", filters={"project": self.projekt}, pluck="name"):
			frappe.db.set_value(
				"Task",
				name,
				{"exp_start_date": add_days(HEUTE, 1), "exp_end_date": add_days(HEUTE, 3), "progress": 0},
			)
		TestBaustellen._lauf += 1
		self.tag = add_days(HEUTE, -20 - TestBaustellen._lauf)
		self.uhr = ts(self.tag, "05:00")

	# --- Hilfen ---------------------------------------------------------------------------------

	def arbeitspaket(self) -> str:
		vorhanden = frappe.get_all(
			"Task", filters={"project": self.projekt}, pluck="name", order_by="creation"
		)
		if vorhanden:
			return vorhanden[0]
		return (
			frappe.get_doc(
				{
					"doctype": "Task",
					"subject": "Innenabdichtung",
					"project": self.projekt,
					"cv_auftrag": self.auftrag,
					"cv_verfahren": "Innenabdichtung",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def neues_paket(self, titel: str, **werte) -> str:
		doc = frappe.get_doc({"doctype": "Task", "subject": titel, "project": self.projekt})
		if werte.pop("mangel", False):
			doc.cv_abnahmeprotokoll = "ABN-TEST-5.9"
			doc.cv_mangel_zeile = frappe.generate_hash(length=10)
			doc.flags.cv_abnahme_intern = True
		doc.insert(ignore_permissions=True)
		if werte:
			# Status/Termine direkt (ERPNext verlangt sonst „Erledigt am“ und Projektzeitraum)
			frappe.db.set_value("Task", doc.name, werte)
		return doc.name

	def zeit(self, minuten: int) -> None:
		"""Arbeitszeit am Testtag buchen, lückenlos hintereinander ab 05:00."""
		von = self.uhr
		self.uhr = von + dt.timedelta(minutes=minuten)
		z.zeit_buchen(
			self.employee, self.projekt, self.task, z.ARBEITSZEIT, von, self.uhr, frappe.generate_hash()
		)

	def zeile(self, filters: dict | None = None, user: str = BUERO) -> dict | None:
		zeilen = _als(user, b.uebersicht, filters or {})["zeilen"]
		return next((r for r in zeilen if r["projekt"] == self.projekt), None)

	# --- Regeln (rein) --------------------------------------------------------------------------

	def test_regeln_fortschritt_abweichung_ampel(self):
		t = frappe._dict
		# gewichtet nach Sollstunden: 6 h erledigt, 2 h offen → 75 %; offen mit 50 % Fortschritt anteilig
		self.assertEqual(
			b.fortschritt([t(status="Completed", cv_sollstunden=6), t(status="Open", cv_sollstunden=2)]), 75.0
		)
		self.assertEqual(
			b.fortschritt(
				[t(status="Completed", cv_sollstunden=6), t(status="Open", cv_sollstunden=2, progress=50)]
			),
			87.5,
		)
		# ohne Sollstunden nach Anzahl; expected_time als Ersatz
		self.assertEqual(b.fortschritt([t(status="Completed"), t(status="Open")]), 50.0)
		self.assertEqual(
			b.fortschritt([t(status="Completed", expected_time=1), t(status="Open", expected_time=3)]), 25.0
		)
		self.assertIsNone(b.fortschritt([]))
		# Abweichung gegen Soll x Fortschritt (0/100 %/unbekannt: volles Soll)
		self.assertEqual(b.abweichung(10, 10.5, None), 5.0)
		self.assertEqual(b.abweichung(10, 6, 50), 20.0)
		self.assertEqual(b.abweichung(10, 0, 0), -100.0)
		self.assertIsNone(b.abweichung(0, 3, None))
		# Ampel: ≤ 5 % grün, ≤ 15 % gelb, darüber rot; überfällig rot; Ist ohne Soll gelb
		self.assertEqual(b.ampel(10, 10.5, None, False), b.GRUEN)
		self.assertEqual(b.ampel(10, 10.6, None, False), b.GELB)
		self.assertEqual(b.ampel(10, 11.5, None, False), b.GELB)
		self.assertEqual(b.ampel(10, 11.6, None, False), b.ROT)
		self.assertEqual(b.ampel(10, 5.4, 50, False), b.GELB)
		self.assertEqual(b.ampel(10, 1, None, True), b.ROT)
		self.assertEqual(b.ampel(0, 2, None, False), b.GELB)
		self.assertEqual(b.ampel(0, 0, None, False), b.GRUEN)

	# --- Zeile je Projekt ------------------------------------------------------------------------

	def test_zeile_je_projekt_ampel_und_zeitraum(self):
		r = self.zeile()
		self.assertIsNotNone(r, "laufendes Projekt mit Auftrag fehlt")
		self.assertGreater(r["soll_h"], 0)
		self.assertEqual(
			(r["ist_h"], r["status"], r["ampel"], r["fortschritt"]), (0.0, b.STATUS_OFFEN, b.GRUEN, 0.0)
		)
		self.assertEqual(r["abweichung"], -100.0)
		self.assertIsNone(r["ist_zeitraum"])
		soll = r["soll_h"]

		# knapp unter Soll → grün, in Arbeit; 5-15 % darüber → gelb; mehr → rot
		minuten = int(soll * 60 * 0.97)
		self.zeit(minuten)
		r = self.zeile()
		self.assertEqual((r["status"], r["ampel"]), (b.STATUS_IN_ARBEIT, b.GRUEN))
		self.assertAlmostEqual(r["ist_h"], minuten / 60, places=1)
		self.assertEqual(r["abweichung"], b.abweichung(soll, r["ist_h"], 0))
		self.zeit(int(soll * 60 * 1.10) - minuten)
		r = self.zeile()
		self.assertEqual(r["ampel"], b.GELB, r)
		self.assertIsNotNone(self.zeile({"ampel": b.FILTER_ROT_GELB}))
		self.assertIsNone(self.zeile({"ampel": b.FILTER_ROT}))
		self.zeit(int(soll * 60 * 0.15))
		r = self.zeile()
		self.assertEqual(r["ampel"], b.ROT, r)
		# Filter Ampel und Status
		self.assertIsNotNone(self.zeile({"ampel": b.FILTER_ROT}))
		self.assertIsNone(self.zeile({"status": b.STATUS_OFFEN}))
		self.assertIsNotNone(self.zeile({"status": b.STATUS_IN_ARBEIT}))
		# Zeitraum: Ist im Zeitraum, Soll/Ist gesamt unverändert
		r = self.zeile({"von": self.tag, "bis": self.tag})
		self.assertEqual(r["ist_zeitraum"], r["ist_h"])
		r = self.zeile({"von": add_days(self.tag, 1), "bis": add_days(self.tag, 2)})
		self.assertEqual(r["ist_zeitraum"], 0.0)
		self.assertGreater(r["ist_h"], 0)

	def test_fortschritt_termine_und_maengel(self):
		if frappe.db.has_column("Task", "cv_sollstunden"):
			frappe.db.set_value("Task", self.task, "cv_sollstunden", 1)
		for name in frappe.get_all(
			"Task", filters={"project": self.projekt, "name": ["!=", self.task]}, pluck="name"
		):
			frappe.db.set_value("Task", name, "status", "Cancelled")
		self.neues_paket("Sanierputz", status="Completed", expected_time=3, cv_sollstunden=3, progress=100)
		r = self.zeile()
		self.assertEqual(r["fortschritt"], 75.0)
		self.assertEqual(r["status"], b.STATUS_IN_ARBEIT)
		# nächster Termin: Beginn des offenen Pakets morgen (vor dem Projektende in 30 Tagen)
		self.assertEqual(getdate(r["naechster_termin"]), add_days(HEUTE, 1))
		self.assertTrue(r["termin_text"].startswith("Beginn"))
		self.assertEqual((r["meldungen"], r["ueberfaellig"]), (0, False))

		# Mangel-Aufgabe mit Frist: zählt als Mangel, nicht für den Fortschritt
		mangel = self.neues_paket(
			"Mangel: Riss im Putz",
			mangel=True,
			status="Open",
			exp_end_date=add_days(HEUTE, 5),
			priority="High",
		)
		r = self.zeile()
		self.assertEqual((r["meldungen"], r["fortschritt"], r["ampel"]), (1, 75.0, b.GRUEN))
		# Frist überschritten → rot, Hinweis, Termin ist der nächste künftige
		frappe.db.set_value("Task", mangel, "exp_end_date", add_days(HEUTE, -1))
		r = self.zeile()
		self.assertEqual((r["ampel"], r["ueberfaellig"]), (b.ROT, True))
		self.assertIn("Mangelfrist", r["hinweis"])
		self.assertEqual(getdate(r["naechster_termin"]), add_days(HEUTE, 1))
		# Mangel behoben, Paket überfällig → weiter rot mit Paket als Grund
		frappe.db.set_value("Task", mangel, "status", "Completed")
		frappe.db.set_value(
			"Task", self.task, {"exp_start_date": add_days(HEUTE, -5), "exp_end_date": add_days(HEUTE, -2)}
		)
		r = self.zeile()
		self.assertEqual((r["meldungen"], r["ampel"]), (0, b.ROT))
		self.assertIn("überfällig", r["hinweis"])
		# nur noch das Projektende liegt in der Zukunft
		self.assertEqual(getdate(r["naechster_termin"]), add_days(HEUTE, 30))
		self.assertEqual(r["termin_text"], "Geplantes Ende")

	def test_privater_termin_ohne_betreff(self):
		"""Privates Event eines anderen Benutzers: Datum ja, Betreff nur für Berechtigte (Review)."""
		frappe.db.set_value(
			"Task", self.task, {"exp_start_date": add_days(HEUTE, 3), "exp_end_date": add_days(HEUTE, 4)}
		)
		event = frappe.get_doc(
			{
				"doctype": "Event",
				"subject": "Privat 5.9 Arzttermin",
				"event_type": "Private",
				"starts_on": dt.datetime.combine(add_days(HEUTE, 1), dt.time(10, 0)),
				"event_participants": [{"reference_doctype": "Project", "reference_docname": self.projekt}],
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Event", event.name, "owner", MEISTER, update_modified=False)
		r = self.zeile()
		self.assertEqual(getdate(r["naechster_termin"]), add_days(HEUTE, 1))
		self.assertEqual(r["termin_text"], "Termin")
		self.assertNotIn("Privat", r["termin_text"])
		r = self.zeile(user=MEISTER)
		self.assertEqual(r["termin_text"], "Termin: Privat 5.9 Arzttermin")
		# öffentlicher Termin: Betreff für alle mit Leserecht
		frappe.db.set_value("Event", event.name, "event_type", "Public", update_modified=False)
		self.assertEqual(self.zeile()["termin_text"], "Termin: Privat 5.9 Arzttermin")

	# --- Rechte, Bericht, Meisterfilter, Kennzahlen -----------------------------------------------

	def test_rechte_bericht_meister_und_kennzahlen(self):
		with self.assertRaises(frappe.PermissionError):
			_als(MONTEUR, b.uebersicht, {})
		# Meister = zugewiesener Benutzer mit Rolle Meister (5.2)
		frappe.get_doc(
			{
				"doctype": "ToDo",
				"allocated_to": MEISTER,
				"reference_type": "Task",
				"reference_name": self.task,
				"description": "Baustelle 5.9",
			}
		).insert(ignore_permissions=True)
		r = self.zeile(user=MEISTER)
		self.assertEqual((r["meister_user"], r["meister"]), (MEISTER, get_fullname(MEISTER)))
		self.assertIsNotNone(self.zeile({"meister": MEISTER}))
		self.assertIsNone(self.zeile({"meister": BUERO}))

		from corevatis_erpnext.corevatis_erpnext.report.baustellen.baustellen import execute

		spalten, zeilen, _nachricht, _chart, kennzahlen = _als(MEISTER, execute, {})
		typ = {c["fieldname"]: c["fieldtype"] for c in spalten}
		# Mitarbeiter und Kunde als Data (Link-Spalten filtert Frappe nach Benutzerberechtigungen)
		self.assertEqual((typ["meister"], typ["kunde_objekt"], typ["projekt"]), ("Data", "Data", "Link"))
		self.assertNotIn("ist_zeitraum", typ)
		self.assertIn(self.projekt, [z_["projekt"] for z_ in zeilen])
		self.assertEqual(kennzahlen[0]["label"], "Baustellen")
		spalten, *_rest = _als(BUERO, execute, {"von": HEUTE, "bis": HEUTE})
		self.assertIn("ist_zeitraum", {c["fieldname"] for c in spalten})

		# Kennzahlen: Büro zählt das Projekt, Monteur sieht 0
		karte = _als(BUERO, b.karte_laufend)
		self.assertGreaterEqual(karte["value"], 1)
		self.assertEqual(karte["route"], ["query-report", "Baustellen"])
		self.assertEqual(_als(BUERO, b.karte_rot)["route_options"]["ampel"], b.FILTER_ROT)
		self.assertGreaterEqual(_als(BUERO, b.karte_stunden_woche)["value"], 0)
		self.assertGreaterEqual(_als(BUERO, b.karte_maengel)["value"], 0)
		self.assertEqual(_als(MONTEUR, b.karte_laufend)["value"], 0)

		# Einrichtung: Number Cards (idempotent), Arbeitsbereich, Leiste
		b.ensure_setup()
		for k in b.KENNZAHLEN:
			nc = frappe.db.get_value(
				"Number Card", k["name"], ["type", "method", "document_type", "currency"], as_dict=True
			)
			self.assertEqual((nc.type, nc.method, nc.document_type), ("Custom", k["method"], "Project"))
			self.assertFalse(nc.currency)
		ws = frappe.get_doc("Workspace", "Baustellen")
		self.assertEqual({r_.role for r_ in ws.roles}, {"Büro", "Meister", "System Manager"})
		self.assertEqual({k.number_card_name for k in ws.number_cards}, {k["name"] for k in b.KENNZAHLEN})
		self.assertIn("Baustellen", {s.link_to for s in ws.shortcuts if s.type == "Report"})
		self.assertTrue(frappe.db.exists("Report", "Baustellen"))
		basis = pathlib.Path(b.__file__).resolve().parents[1]
		leiste = json.loads(
			(basis / "corevatis_erpnext/sidebar/betrieb/betrieb.json").read_text(encoding="utf-8")
		)
		self.assertIn(
			("Workspace", "Baustellen"),
			{(i.get("link_type"), i.get("link_to")) for i in leiste["items"] if i.get("type") == "Link"},
		)
