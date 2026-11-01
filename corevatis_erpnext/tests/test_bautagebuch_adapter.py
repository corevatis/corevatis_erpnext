"""Bautagebuch (Phase 3 P3.2) im Adapter: Anwesenheit aus Timesheets, Projekte mit Zeiten, Koordinaten
des Projekts (Geokodierung gemockt), Stand für die Prozessleiste und Desk-Anbindung (Hooks, Sidebar).
Gegen echtes ERPNext v16; der Kern ``corevatis`` ist auf der CI-Site nicht installiert."""

from __future__ import annotations

import datetime as dt
import json
import os
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from corevatis_erpnext import bautagebuch as B
from corevatis_erpnext import zeiterfassung as z
from corevatis_erpnext.planung import geokodierung
from corevatis_erpnext.planung.slotrechner import UNBEKANNT, Ort
from corevatis_erpnext.prozess import kette
from corevatis_erpnext.tests._helpers import make_user
from corevatis_erpnext.tests.test_zeiterfassung import ensure_employee

USER = "cv-test-btb-monteur@example.com"
_heute = dt.date.today()
TAG = _heute - dt.timedelta(days=_heute.weekday() + 7)  # Montag der Vorwoche


def ts(tag: dt.date, uhrzeit: str) -> dt.datetime:
	return dt.datetime.combine(tag, dt.time.fromisoformat(uhrzeit))


class TestBautagebuchAdapter(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		z.ensure_setup()
		make_user(USER, "Bautagebuch", ["Monteur"])
		cls.employee = ensure_employee(USER)
		cls.projekt = frappe.get_doc(
			{"doctype": "Project", "project_name": f"_TEST-CV-BTB {frappe.generate_hash(length=6)}"}
		).insert(ignore_permissions=True)
		cls.anderes = frappe.get_doc(
			{"doctype": "Project", "project_name": f"_TEST-CV-BTB-2 {frappe.generate_hash(length=6)}"}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.db.rollback()

	def buchen(self, projekt, von, bis, art=z.ARBEITSZEIT):
		return z.zeit_buchen(
			self.employee, projekt, None, art, von, bis, idempotency_key=frappe.generate_hash(length=16)
		)

	def test_anwesenheit_und_projekte(self):
		self.buchen(self.projekt.name, ts(TAG, "06:30"), ts(TAG, "07:00"), z.FAHRZEIT)
		self.buchen(self.projekt.name, ts(TAG, "07:00"), ts(TAG, "12:00"))
		self.buchen(self.projekt.name, ts(TAG, "12:00"), ts(TAG, "12:30"), z.PAUSE)
		self.buchen(self.anderes.name, ts(TAG, "13:00"), ts(TAG, "14:00"))
		zeilen = B.anwesenheit(self.projekt.name, TAG, TAG)
		self.assertEqual(
			[(r["aktivitaet"], r["von"][11:16], r["bis"][11:16], r["stunden"]) for r in zeilen],
			[
				(z.FAHRZEIT, "06:30", "07:00", 0.5),
				(z.ARBEITSZEIT, "07:00", "12:00", 5.0),
				(z.PAUSE, "12:00", "12:30", 0.5),
			],
		)
		r = zeilen[0]
		self.assertEqual(
			(r["datum"], r["mitarbeiter"], r["benutzer"]), (TAG.isoformat(), self.employee, USER)
		)
		self.assertTrue(r["mitarbeiter_name"])
		self.assertEqual(
			set(r),
			{"datum", "mitarbeiter", "mitarbeiter_name", "benutzer", "von", "bis", "stunden", "aktivitaet"},
		)
		folgetag = TAG + dt.timedelta(days=1)
		self.assertEqual(B.anwesenheit(self.projekt.name, folgetag, folgetag), [])
		self.assertEqual(B.anwesenheit("", TAG, TAG), [])
		projekte = B.projekte_mit_zeiten(TAG, TAG)
		self.assertIn(self.projekt.name, projekte)
		self.assertIn(self.anderes.name, projekte)
		self.assertNotIn(self.projekt.name, B.projekte_mit_zeiten(folgetag, folgetag))
		with self.assertRaises(ValueError):
			B.anwesenheit(self.projekt.name, folgetag, TAG)
		with self.assertRaises(ValueError):
			B.projekte_mit_zeiten(TAG - dt.timedelta(days=400), TAG)

	def test_koordinaten(self):
		adresse = ("Kellerweg 1, 48143 Münster", None)
		with (
			patch("corevatis_erpnext.planung.terminplanung._projekt_adresse", return_value=adresse),
			patch.object(geokodierung, "koordinaten", return_value=Ort("x", 51.961234, 7.625678)) as geo,
		):
			self.assertEqual(B.koordinaten(self.projekt.name), (51.961234, 7.625678))
		self.assertEqual(geo.call_args.args[0], "Kellerweg 1, 48143 Münster")
		self.assertEqual(geo.call_args.args[1].rest, 1, "höchstens eine neue Anfrage je Aufruf")
		with (
			patch("corevatis_erpnext.planung.terminplanung._projekt_adresse", return_value=adresse),
			patch.object(geokodierung, "koordinaten", return_value=UNBEKANNT),
		):
			self.assertIsNone(B.koordinaten(self.projekt.name))
		with patch("corevatis_erpnext.planung.terminplanung._projekt_adresse", return_value=("", None)):
			self.assertIsNone(B.koordinaten(self.projekt.name))
		with (
			patch("corevatis_erpnext.planung.terminplanung._projekt_adresse", side_effect=RuntimeError("x")),
			patch.object(frappe, "log_error") as log,
		):
			self.assertIsNone(B.koordinaten(self.projekt.name))
		log.assert_called_once()
		self.assertIsNone(B.koordinaten("gibt-es-nicht"))

	def test_stand_und_prozessleiste(self):
		k = frappe._dict(start=("Project", self.projekt.name))
		if not B.kern_da():
			self.assertIsNone(B.stand(self.projekt.name))
			self.assertIsNone(kette._bautagebuch(k))
		kern = {
			"projekt": self.projekt.name,
			"tage": 3,
			"text": "Bautagebuch: 3 Tage",
			"erster": "2026-10-01",
			"letzter": "2026-10-03",
			"darf_erzeugen": True,
		}
		with (
			patch.object(B, "kern_da", return_value=True),
			patch.object(B, "_kern", return_value=lambda p: kern),
		):
			self.assertEqual(kette._bautagebuch(k)["text"], "Bautagebuch: 3 Tage")
			self.assertIsNone(kette._bautagebuch(frappe._dict(start=("Lead", "x"))))
		leer = {**kern, "tage": 0, "darf_erzeugen": False}
		with (
			patch.object(B, "kern_da", return_value=True),
			patch.object(B, "_kern", return_value=lambda p: leer),
		):
			self.assertIsNone(B.stand(self.projekt.name))
		with (
			patch.object(B, "kern_da", return_value=True),
			patch.object(B, "_kern", return_value=lambda p: None),
		):
			self.assertIsNone(B.stand(self.projekt.name))

	def test_desk_anbindung(self):
		from corevatis_erpnext import hooks

		self.assertEqual(hooks.corevatis_anwesenheit_adapter, "corevatis_erpnext.bautagebuch")
		self.assertEqual(hooks.corevatis_koordinaten_adapter, "corevatis_erpnext.bautagebuch")
		self.assertIn("public/js/bautagebuch.js", hooks.doctype_js["Project"])
		basis = os.path.dirname(hooks.__file__)
		self.assertTrue(os.path.exists(os.path.join(basis, "public", "js", "bautagebuch.js")))
		with open(
			os.path.join(basis, "corevatis_erpnext", "sidebar", "betrieb", "betrieb.json"), encoding="utf-8"
		) as f:
			items = json.load(f)["items"]
		labels = [i.get("label") for i in items]
		eintrag = items[labels.index("Bautagebuch")]
		self.assertEqual((eintrag["link_type"], eintrag["link_to"]), ("DocType", "Bautagebuch Tag"))
		# Desk-Navigation: in der Gruppe „Ausführung“ hinter den Tagesrapporten
		self.assertEqual(labels.index("Bautagebuch"), labels.index("Tagesrapporte") + 1)
		self.assertLess(labels.index("Ausführung"), labels.index("Bautagebuch"))
