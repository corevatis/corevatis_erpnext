"""Mitarbeiter: Urlaub, Krank, Stundenkonto (Phase 2 P2.5, Handwerker#88).

Fester Monat März 2026 (22 Arbeitstage, halber Tag 13.3., eigene Holiday List): Urlaub 2.-6.3.,
Krank 9.3., Schulung 10.3. 8-12 Uhr, Zeiten am 16./17.3. aus der Zeiterfassung. Dazu Urlaub im
Dezember (geplant) und Übertrag/Anspruch am Employee. Rechte: Büro sieht alles, Meister ohne Arten,
Monteur nur sich selbst; Kern-Hook ``fuer_benutzer``.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from corevatis_erpnext import mitarbeiter as mit
from corevatis_erpnext import zeiterfassung as z
from corevatis_erpnext.corevatis_erpnext.report.stundenkonto.stundenkonto import execute
from corevatis_erpnext.mitarbeiter import stundenkonto as sk
from corevatis_erpnext.tests._helpers import make_user
from corevatis_erpnext.tests.test_wochenplan import employee

BUERO = "cv-konto-buero@test.corevatis.invalid"
MEISTER = "cv-konto-meister@test.corevatis.invalid"
MONTEUR = "cv-konto-monteur@test.corevatis.invalid"
OHNE = "cv-konto-ohne@test.corevatis.invalid"
LISTE = "_Test CV Mitarbeiter 2026"
HEUTE = dt.date(2026, 10, 31)


def holiday_list() -> str:
	if frappe.db.exists("Holiday List", LISTE):
		return LISTE
	frappe.get_doc(
		{
			"doctype": "Holiday List",
			"holiday_list_name": LISTE,
			"from_date": "2026-01-01",
			"to_date": "2026-12-31",
			"holidays": [
				{"holiday_date": "2026-01-01", "description": "Neujahr"},
				{"holiday_date": "2026-03-07", "description": "Samstag", "weekly_off": 1},
				{"holiday_date": "2026-03-13", "description": "Betriebsfeier", "is_half_day": 1},
				{"holiday_date": "2026-04-03", "description": "Karfreitag"},
				{"holiday_date": "2026-04-06", "description": "Ostermontag"},
			],
		}
	).insert(ignore_permissions=True)
	return LISTE


class TestMitarbeiter(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		mit.ensure_setup()
		z.ensure_setup()
		make_user(BUERO, "Bea", ["Büro"], "Konto")
		make_user(MEISTER, "Martin", ["Meister"], "Konto")
		make_user(MONTEUR, "Moritz", ["Monteur"], "Konto")
		# ohne Fachrolle: kein automatischer Employee (prozess.mitarbeiter legt ihn für Monteur/Meister an)
		make_user(OHNE, "Ohne", ["Sales User"], "Konto")
		cls.emp = employee(MONTEUR, "Moritz")
		cls.emp_meister = employee(MEISTER, "Martin")
		frappe.db.set_value(
			"Employee",
			cls.emp,
			{
				"holiday_list": holiday_list(),
				sk.FELD_ANSPRUCH: 28,
				sk.FELD_UEBERTRAG: 2,
				sk.FELD_SALDO: 10,
			},
		)
		cls.projekt = frappe.get_doc(
			{"doctype": "Project", "project_name": f"_TEST-CV-KONTO {frappe.generate_hash(length=6)}"}
		).insert(ignore_permissions=True)
		cls.task = frappe.get_doc(
			{"doctype": "Task", "subject": "Horizontalsperre", "project": cls.projekt.name}
		).insert(ignore_permissions=True)
		for name in frappe.get_all("Abwesenheit", filters={"mitarbeiter": cls.emp}, pluck="name"):
			frappe.delete_doc("Abwesenheit", name, force=True, ignore_permissions=True)
		cls.abwesenheit(cls.emp, "Urlaub", "2026-03-02", "2026-03-06")
		cls.abwesenheit(cls.emp, "Krank", "2026-03-09", "2026-03-09")
		cls.abwesenheit(
			cls.emp,
			"Schulung",
			"2026-03-10",
			"2026-03-10",
			ganztags=0,
			von_uhrzeit="08:00:00",
			bis_uhrzeit="12:00:00",
		)
		cls.abwesenheit(cls.emp, "Urlaub", "2026-12-28", "2026-12-30")
		for name in frappe.get_all("Timesheet", filters={"employee": cls.emp}, pluck="name"):
			frappe.delete_doc("Timesheet", name, force=True, ignore_permissions=True)
		# der Testmonat liegt länger als 31 Tage zurück: die Frist der Zeiterfassung (zeitraum_pruefen) aussetzen
		with patch.object(z, "zeitraum_pruefen"):
			cls.buchen("2026-03-16 07:00:00", "2026-03-16 12:00:00", z.ARBEITSZEIT)
			cls.buchen("2026-03-16 12:00:00", "2026-03-16 12:30:00", z.PAUSE)
			cls.buchen("2026-03-16 12:30:00", "2026-03-16 16:00:00", z.ARBEITSZEIT)
			cls.buchen("2026-03-17 06:30:00", "2026-03-17 07:30:00", z.FAHRZEIT)
			cls.buchen("2026-03-17 07:30:00", "2026-03-17 15:30:00", z.ARBEITSZEIT)
		frappe.db.commit()

	@classmethod
	def abwesenheit(cls, emp, art, von, bis, **werte) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Abwesenheit",
					"mitarbeiter": emp,
					"art": art,
					"von": von,
					"bis": bis,
					"ganztags": 1,
					**werte,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	@classmethod
	def buchen(cls, von, bis, art):
		return z.zeit_buchen(
			cls.emp,
			cls.projekt.name,
			cls.task.name,
			art,
			von,
			bis,
			idempotency_key=frappe.generate_hash(length=16),
		)

	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# --- Rechnen -----------------------------------------------------------------------------------

	def test_meister_ohne_benutzerberechtigung(self):
		# ERPNexts User Permission auf den eigenen Employee sperrt dem Meister Team-Listen, Berichte und
		# Buchungen für andere (Abnahme Build 33): Bestand bereinigen, Flag am Employee aus, validate hält es
		from frappe.permissions import add_user_permission

		from corevatis_erpnext.prozess import mitarbeiter as pm

		frappe.db.set_value("Employee", self.emp_meister, "create_user_permission", 1, update_modified=False)
		add_user_permission("Employee", self.emp_meister, MEISTER, ignore_permissions=True)
		self.assertTrue(frappe.db.exists("User Permission", {"user": MEISTER, "allow": "Employee"}))
		entfernt = pm.ensure_meister_ohne_benutzerberechtigung()
		self.assertTrue(entfernt)
		self.assertFalse(frappe.db.exists("User Permission", {"user": MEISTER, "allow": "Employee"}))
		self.assertEqual(frappe.db.get_value("Employee", self.emp_meister, "create_user_permission"), 0)
		# Speichern mit gesetztem Haken: validate schaltet ihn ab, ERPNext legt keine Berechtigung an
		emp = frappe.get_doc("Employee", self.emp_meister)
		emp.create_user_permission = 1
		emp.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Employee", self.emp_meister, "create_user_permission"), 0)
		self.assertFalse(frappe.db.exists("User Permission", {"user": MEISTER, "allow": "Employee"}))
		# Monteur behält seine (nur eigene Belege im Desk)
		add_user_permission("Employee", self.emp, MONTEUR, ignore_permissions=True)
		pm.ensure_meister_ohne_benutzerberechtigung()
		self.assertTrue(frappe.db.exists("User Permission", {"user": MONTEUR, "allow": "Employee"}))
		frappe.db.delete("User Permission", {"user": MONTEUR, "allow": "Employee"})

	def test_rechte_am_employee(self):
		# Meister liest Mitarbeiter (Link-Filter in Berichten), sieht aber Urlaubsanspruch, Resturlaub und
		# Stundenkonto-Übertrag nicht (Stufe 2: Büro, System Manager); persönliche Felder bleiben Stufe 1
		from frappe.model import get_permitted_fields

		self.assertTrue(frappe.has_permission("Employee", "read", user=MEISTER))
		self.assertFalse(frappe.has_permission("Employee", "write", user=MEISTER))
		self.assertTrue(frappe.has_permission("Employee", "write", user=BUERO))
		konto = {sk.FELD_ANSPRUCH, sk.FELD_UEBERTRAG, sk.FELD_SALDO}
		self.assertFalse(konto & set(get_permitted_fields("Employee", user=MEISTER)))
		self.assertFalse({"date_of_birth", "iban"} & set(get_permitted_fields("Employee", user=MEISTER)))
		self.assertEqual(konto & set(get_permitted_fields("Employee", user=BUERO)), konto)
		self.assertFalse({"date_of_birth", "iban"} & set(get_permitted_fields("Employee", user=BUERO)))
		frappe.set_user(MEISTER)
		try:
			doc = frappe.get_doc("Employee", self.emp)
			doc.apply_fieldlevel_read_permissions()
			self.assertIsNone(doc.get(sk.FELD_ANSPRUCH))
			self.assertEqual(doc.employee_name, "Moritz Konto")
		finally:
			frappe.set_user("Administrator")
		frappe.set_user(BUERO)
		try:
			doc = frappe.get_doc("Employee", self.emp)
			doc.apply_fieldlevel_read_permissions()
			self.assertEqual(doc.get(sk.FELD_ANSPRUCH), 28)
		finally:
			frappe.set_user("Administrator")

	def test_felder_am_employee(self):
		meta = frappe.get_meta("Employee")
		for feld in (sk.FELD_ANSPRUCH, sk.FELD_UEBERTRAG, sk.FELD_SALDO):
			self.assertIsNotNone(meta.get_field(feld), feld)

	def test_monat_maerz(self):
		m = sk.monat(self.emp, 2026, 3, heute=HEUTE)
		self.assertEqual(
			(m["solltage"], m["soll_brutto"], m["abwesend"], m["soll"]), (22, 172.0, 52.0, 120.0)
		)
		self.assertEqual(
			(m["ist"], m["saldo"], m["ueberstunden"], m["minusstunden"]), (17.5, -102.5, 0.0, 102.5)
		)
		self.assertEqual((m["urlaubstage"], m["kranktage"], m["sonstige_tage"]), (5.0, 1.0, 0.5))
		self.assertTrue(m["abgeschlossen"])
		tage = {t["datum"]: t for t in m["tage"]}
		self.assertEqual((tage["2026-03-02"]["abwesenheit"], tage["2026-03-02"]["soll"]), ("Urlaub", 0.0))
		self.assertEqual(
			(tage["2026-03-10"]["abwesenheit"], tage["2026-03-10"]["abwesend"], tage["2026-03-10"]["soll"]),
			("Schulung", 4.0, 4.0),
		)
		self.assertEqual(
			(tage["2026-03-13"]["feiertag"], tage["2026-03-13"]["soll_brutto"]), ("Betriebsfeier", 4.0)
		)
		self.assertEqual(
			(tage["2026-03-07"]["frei"], tage["2026-03-07"]["wochenende"], tage["2026-03-07"]["soll"]),
			(True, True, 0.0),
		)
		self.assertEqual((tage["2026-03-16"]["ist"], tage["2026-03-16"]["saldo"]), (8.5, 0.5))
		self.assertEqual((tage["2026-03-17"]["ist"], tage["2026-03-17"]["saldo"]), (9.0, 1.0))
		self.assertEqual(tage["2026-03-31"]["laufend"], -102.5)

	def test_zukunft_zaehlt_nicht(self):
		m = sk.monat(self.emp, 2026, 3, heute=dt.date(2026, 3, 16))
		# Soll bis einschließlich 16.3.: 2.-6. Urlaub (0), 9. Krank (0), 10. halb (4), 11., 12. (16), 13. halb (4), 16. (8)
		self.assertEqual(m["soll"], 32.0)
		self.assertEqual(m["ist"], 17.5)
		self.assertFalse(m["abgeschlossen"])

	def test_saldo_jahr(self):
		# Januar 21 Arbeitstage (Neujahr), Februar 20, März -102,5; Übertrag +10
		self.assertEqual(sk.saldo_jahr(self.emp, 2026, 3, 10.0, heute=HEUTE), -420.5)

	def test_urlaubskonto(self):
		u = sk.urlaubskonto(self.emp, 2026, heute=HEUTE)
		self.assertEqual(
			(u["anspruch"], u["uebertrag"], u["genommen"], u["geplant"], u["rest"]),
			(28.0, 2.0, 5.0, 3.0, 22.0),
		)
		self.assertEqual((u["kranktage"], u["sonstige_tage"]), (1.0, 0.5))
		# Standardanspruch 30 ohne Wert; Meister ohne Abwesenheiten
		u2 = sk.urlaubskonto(self.emp_meister, 2026, heute=HEUTE)
		self.assertEqual((u2["anspruch"], u2["rest"]), (30.0, 30.0))
		# Urlaubstage eines Zeitraums: 23.-27.3. = 5 Tage; 12.-13.3. = 1,5 (halber Tag); halbtags 2 h = 0,25
		self.assertEqual(sk.urlaubstage(self.emp, "2026-03-23", "2026-03-27"), 5.0)
		self.assertEqual(sk.urlaubstage(self.emp, "2026-03-12", "2026-03-13"), 1.5)
		self.assertEqual(
			sk.urlaubstage(self.emp, "2026-03-23", "2026-03-23", 0, "08:00:00", "10:00:00"), 0.25
		)

	# --- Rechte und Übersicht ----------------------------------------------------------------------

	def test_uebersicht_buero(self):
		frappe.set_user(BUERO)
		daten = sk.uebersicht(2026, 3, self.emp)
		self.assertTrue(daten["alle"])
		(zeile,) = daten["zeilen"]
		self.assertTrue(zeile["arten_sichtbar"])
		self.assertEqual(
			(zeile["solltage"], zeile["soll"], zeile["ist"], zeile["saldo"]), (22, 120.0, 17.5, -102.5)
		)
		self.assertEqual(zeile["saldo_jahr"], -420.5)
		self.assertEqual(
			(zeile["urlaub_anspruch"], zeile["urlaub_rest"], zeile["kranktage_jahr"]), (30.0, 22.0, 1.0)
		)
		self.assertEqual(flt(zeile["urlaub_genommen"]) + flt(zeile["urlaub_geplant"]), 8.0)
		self.assertEqual(zeile["abwesend_tage"], 6.5)
		# alle aktiven Mitarbeiter ohne Filter
		alle = sk.uebersicht(2026, 3)
		self.assertIn(self.emp, [r["mitarbeiter"] for r in alle["zeilen"]])
		self.assertIn(self.emp_meister, [r["mitarbeiter"] for r in alle["zeilen"]])

	def test_uebersicht_meister_ohne_arten(self):
		frappe.set_user(MEISTER)
		daten = sk.uebersicht(2026, 3, self.emp)
		(zeile,) = daten["zeilen"]
		self.assertFalse(zeile["arten_sichtbar"])
		self.assertEqual((zeile["soll"], zeile["saldo"]), (120.0, -102.5))
		self.assertIsNone(zeile["urlaub_rest"])
		self.assertIsNone(zeile["kranktage_jahr"])
		m = sk.monatsuebersicht(self.emp, 2026, 3)
		tage = {t["datum"]: t for t in m["tage"]}
		self.assertEqual(tage["2026-03-09"]["abwesenheit"], "Abwesend")
		self.assertIsNone(m["urlaub"])
		self.assertEqual(
			(m["abwesend_tage"], m["urlaubstage"], m["kranktage"], m["sonstige_tage"]),
			(6.5, None, None, None),
		)
		# die eigene Zeile zeigt die Arten
		eigene = sk.uebersicht(2026, 3, self.emp_meister)["zeilen"][0]
		self.assertTrue(eigene["arten_sichtbar"])

	def test_monteur_nur_selbst(self):
		frappe.set_user(MONTEUR)
		daten = sk.uebersicht(2026, 3)
		self.assertFalse(daten["alle"])
		self.assertEqual([r["mitarbeiter"] for r in daten["zeilen"]], [self.emp])
		self.assertTrue(daten["zeilen"][0]["arten_sichtbar"])
		self.assertRaises(frappe.PermissionError, sk.uebersicht, 2026, 3, self.emp_meister)
		self.assertRaises(frappe.PermissionError, sk.monatsuebersicht, self.emp_meister, 2026, 3)
		m = sk.monatsuebersicht(self.emp, 2026, 3)
		self.assertEqual((m["saldo"], m["saldo_jahr"], m["urlaub"]["rest"]), (-102.5, -420.5, 22.0))
		frappe.set_user(OHNE)
		self.assertEqual(sk.uebersicht(2026, 3)["zeilen"], [])

	def test_report(self):
		frappe.set_user(BUERO)
		columns, rows, message, _chart, summary = execute({"monat": "2026-03-01", "mitarbeiter": self.emp})
		self.assertEqual(
			[c["fieldname"] for c in columns][:3], ["mitarbeiter", "mitarbeiter_name", "solltage"]
		)
		self.assertEqual((rows[0]["mitarbeiter"], rows[0]["saldo"]), (self.emp, -102.5))
		self.assertIn("8 h", message)
		self.assertEqual(summary[0]["value"], 1)
		columns, rows, message, _chart, summary = execute(
			{"monat": "2026-03-15", "mitarbeiter": self.emp, "ansicht": "Tage"}
		)
		self.assertEqual(len(rows), 31)
		self.assertEqual(rows[0]["datum"], "2026-03-01")
		self.assertIn("Saldo Jahr", message)
		self.assertEqual(summary[2]["value"], -102.5)
		# Monteur: Tage ohne Mitarbeiter = der eigene
		frappe.set_user(MONTEUR)
		_c, rows, _m, _ch, _s = execute({"monat": "2026-03-01", "ansicht": "Tage"})
		self.assertEqual(len(rows), 31)
		_c, rows, _m, _ch, _s = execute({"monat": "2026-03-01"})
		self.assertEqual([r["mitarbeiter"] for r in rows], [self.emp])

	# --- Formular und App --------------------------------------------------------------------------

	def test_urlaub_hinweis_und_warnung(self):
		frappe.set_user(BUERO)
		k = mit.urlaub_hinweis(self.emp, "2026-03-23", "2026-03-27", 1)
		self.assertEqual((k["tage_eintrag"], k["rest"], k["rest_nachher"]), (5.0, 22.0, 17.0))
		frappe.set_user(MONTEUR)
		self.assertEqual(mit.urlaub_hinweis(self.emp, "2026-03-23", "2026-03-23", 1)["tage_eintrag"], 1.0)
		self.assertRaises(
			frappe.PermissionError, mit.urlaub_hinweis, self.emp_meister, "2026-03-23", "2026-03-23", 1
		)
		# Urlaub über den Rest: Hinweis, keine Sperre
		frappe.set_user("Administrator")
		frappe.clear_messages()
		name = self.abwesenheit(self.emp, "Urlaub", "2026-05-04", "2026-06-12")
		self.assertTrue(frappe.db.exists("Abwesenheit", name))
		self.assertTrue(any("Resturlaub" in str(m.get("message")) for m in frappe.get_message_log()))

	def test_fuer_benutzer(self):
		frappe.set_user(MONTEUR)
		d = mit.fuer_benutzer(MONTEUR, 2026, 3)
		self.assertEqual((d["mitarbeiter"], d["jahr"], d["monat"]), (self.emp, 2026, 3))
		self.assertEqual((d["soll"], d["ist"], d["saldo"], d["saldo_jahr"]), (120.0, 17.5, -102.5, -420.5))
		self.assertEqual(d["urlaub"]["rest"], 22.0)
		self.assertEqual(len(d["tage"]), 31)
		self.assertEqual(
			set(d["tage"][0]),
			{"datum", "wochentag", "soll", "ist", "saldo", "abwesenheit", "feiertag", "wochenende", "zaehlt"},
		)
		self.assertRaises(frappe.PermissionError, mit.fuer_benutzer, MEISTER, 2026, 3)
		frappe.set_user(OHNE)
		self.assertIsNone(mit.fuer_benutzer(OHNE, 2026, 3))
