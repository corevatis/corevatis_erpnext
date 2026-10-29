"""Zeiterfassung (Aufgabe 3.9): Activity Types, Timesheet-Zeilen je Tag, Überlappung,
Idempotenz, laufende Buchung, Tagesliste und Sollstunden - gegen echtes ERPNext v16."""

import datetime as dt

import frappe
from frappe.tests import IntegrationTestCase

from corevatis_erpnext import zeiterfassung as z
from corevatis_erpnext.tests._angebot import firma
from corevatis_erpnext.tests._helpers import make_user

USER = "cv-test-zeit-monteur@example.com"
USER_OHNE = "cv-test-zeit-ohne@example.com"
USER_FREMD = "cv-test-zeit-fremd@example.com"
LISTE = "_TEST-CV-ZEIT-2026"
# Montag vor 2 bis 3 Wochen (Buchungen nur bis 31 Tage zurück), je Test eigene Tage
_heute = dt.date.today()
MO = _heute - dt.timedelta(days=_heute.weekday() + 14)


def ts(tag: dt.date, uhrzeit: str) -> dt.datetime:
	return dt.datetime.combine(tag, dt.time.fromisoformat(uhrzeit))


def ensure_gender() -> str:
	if not frappe.db.exists("Gender", "Male"):
		frappe.get_doc({"doctype": "Gender", "gender": "Male"}).insert(ignore_permissions=True)
	return "Male"


def ensure_employee(user: str, holiday_list: str | None = None) -> str:
	name = frappe.db.get_value("Employee", {"user_id": user})
	if name:
		frappe.db.set_value("Employee", name, "holiday_list", holiday_list)
		return name
	doc = frappe.get_doc(
		{
			"doctype": "Employee",
			"first_name": "Zeit",
			"last_name": "Monteur",
			"company": firma(),
			"gender": ensure_gender(),
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"status": "Active",
			"user_id": user,
			"holiday_list": holiday_list,
		}
	).insert(ignore_permissions=True)
	return doc.name


def ensure_holiday_list() -> str:
	if not frappe.db.exists("Holiday List", LISTE):
		frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": LISTE,
				"from_date": "2026-03-01",
				"to_date": "2026-06-30",
				"holidays": [
					{"holiday_date": "2026-04-06", "description": "Ostermontag"},
					{"holiday_date": "2026-03-07", "description": "Samstag", "weekly_off": 1},
					{"holiday_date": "2026-03-13", "description": "Betriebsfeier", "is_half_day": 1},
				],
			}
		).insert(ignore_permissions=True)
	return LISTE


class TestZeiterfassung(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		z.ensure_setup()
		make_user(USER, "Zeit", ["Monteur"])
		make_user(USER_OHNE, "Ohne", ["Monteur"])
		cls.employee = ensure_employee(USER)
		cls.projekt = frappe.get_doc(
			{"doctype": "Project", "project_name": f"_TEST-CV-ZEIT {frappe.generate_hash(length=6)}"}
		).insert(ignore_permissions=True)
		cls.task = frappe.get_doc(
			{"doctype": "Task", "subject": "Abdichtung Nordwand", "project": cls.projekt.name}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		for name in frappe.get_all("Timesheet", filters={"employee": self.employee}, pluck="name"):
			doc = frappe.get_doc("Timesheet", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("Timesheet", name, force=True, ignore_permissions=True)
		frappe.db.delete(z.LAUF_DOCTYPE, {"employee": self.employee})

	def tearDown(self):
		frappe.db.rollback()

	def buchen(self, von, bis, art=z.ARBEITSZEIT, key=None, task=True, notiz=None):
		return z.zeit_buchen(
			self.employee,
			self.projekt.name,
			self.task.name if task else None,
			art,
			von,
			bis,
			idempotency_key=key or frappe.generate_hash(length=16),
			notiz=notiz,
		)

	def test_activity_types(self):
		z.activity_types_sicherstellen()
		for name in ("Arbeitszeit", "Fahrzeit", "Pause"):
			self.assertTrue(frappe.db.exists("Activity Type", name), name)
		self.assertEqual(z.activity_types_sicherstellen(), [])
		self.assertTrue(frappe.get_meta("Timesheet Detail").has_field(z.SYNC_KEY_FELD))

	def test_arbeits_und_fahrzeit_in_einem_timesheet(self):
		a = self.buchen(ts(MO, "07:00"), ts(MO, "09:30"), notiz="Vorbereitung")
		f = self.buchen(ts(MO, "09:30"), ts(MO, "10:00"), art=z.FAHRZEIT)
		p = self.buchen(ts(MO, "10:00"), ts(MO, "10:15"), art=z.PAUSE, task=False)
		self.assertEqual(a["timesheet"], f["timesheet"])
		self.assertEqual(a["timesheet"], p["timesheet"])
		doc = frappe.get_doc("Timesheet", a["timesheet"])
		self.assertEqual(doc.docstatus, 0)
		self.assertEqual(doc.employee, self.employee)
		self.assertEqual(doc.user, USER)
		self.assertEqual(str(doc.start_date), str(MO))
		rows = sorted(doc.time_logs, key=lambda r: r.from_time)
		self.assertEqual(
			[(r.activity_type, r.hours, r.is_billable) for r in rows],
			[("Arbeitszeit", 2.5, 1), ("Fahrzeit", 0.5, 1), ("Pause", 0.25, 0)],
		)
		self.assertEqual(rows[0].task, self.task.name)
		self.assertEqual(rows[0].project, self.projekt.name)
		self.assertEqual(rows[0].description, "Vorbereitung")
		self.assertIsNone(rows[2].task)
		self.assertAlmostEqual(doc.total_hours, 3.25)
		self.assertEqual(a["zeilen"][0]["stunden"], 2.5)
		self.assertEqual(a["zeilen"][0]["von"], f"{MO}T07:00:00")

	def test_projekt_aus_task(self):
		r = z.zeit_buchen(
			self.employee, None, self.task.name, z.ARBEITSZEIT, ts(MO, "13:00"), ts(MO, "14:00")
		)
		self.assertEqual(r["zeilen"][0]["project"], self.projekt.name)

	def test_idempotenz_dreifach(self):
		key = "6f1c4c2e-0000-4000-8000-000000000001"
		ergebnisse = [
			self.buchen(
				ts(MO + dt.timedelta(days=1), "07:00"), ts(MO + dt.timedelta(days=1), "08:00"), key=key
			)
			for _ in range(3)
		]
		self.assertEqual([e["duplikat"] for e in ergebnisse], [False, True, True])
		self.assertEqual(len({e["zeilen"][0]["name"] for e in ergebnisse}), 1)
		self.assertEqual(frappe.db.count("Timesheet Detail", {z.SYNC_KEY_FELD: key}), 1)

	def test_ueberlappung(self):
		tag = MO + dt.timedelta(days=2)
		self.buchen(ts(tag, "07:00"), ts(tag, "09:00"))
		with self.assertRaises(z.ZeitKonflikt) as ctx:
			self.buchen(ts(tag, "08:00"), ts(tag, "10:00"), art=z.FAHRZEIT)
		self.assertEqual(ctx.exception.grund, "ueberlappung")
		self.assertIsInstance(ctx.exception, frappe.ValidationError)
		frappe.db.rollback()
		# angrenzend ist keine Überlappung
		self.buchen(ts(tag, "07:00"), ts(tag, "09:00"))
		self.buchen(ts(tag, "09:00"), ts(tag, "10:00"))

	def test_ueberlappung_mit_eingereichtem_timesheet(self):
		tag = MO + dt.timedelta(days=3)
		r = self.buchen(ts(tag, "07:00"), ts(tag, "09:00"))
		frappe.get_doc("Timesheet", r["timesheet"]).submit()
		# Tages-Timesheet eingereicht → neuer Entwurf; Überlappung über Timesheets hinweg erkannt
		neu = self.buchen(ts(tag, "09:00"), ts(tag, "10:00"))
		self.assertNotEqual(neu["timesheet"], r["timesheet"])
		with self.assertRaises(z.ZeitKonflikt):
			self.buchen(ts(tag, "08:30"), ts(tag, "08:45"))

	def test_ende_vor_beginn(self):
		with self.assertRaises(z.ZeitKonflikt) as ctx:
			self.buchen(ts(MO, "09:00"), ts(MO, "09:00"))
		self.assertEqual(ctx.exception.grund, "ungueltig")

	def test_ueber_mitternacht(self):
		tag = MO + dt.timedelta(days=4)
		r = self.buchen(ts(tag, "22:00"), ts(tag + dt.timedelta(days=1), "01:30"))
		self.assertEqual([zl["stunden"] for zl in r["zeilen"]], [2.0, 1.5])
		self.assertNotEqual(r["zeilen"][0]["timesheet"], r["zeilen"][1]["timesheet"])
		self.assertEqual(r["zeilen"][1]["von"], f"{tag + dt.timedelta(days=1)}T00:00:00")

	def test_laufende_buchung(self):
		self.assertIsNone(z.offene_buchung(self.employee))
		lauf = z.lauf_starten(
			self.employee, self.projekt.name, self.task.name, z.ARBEITSZEIT, ts(MO, "07:00"), "key-1"
		)
		self.assertEqual(lauf["von"], f"{MO}T07:00:00")
		self.assertEqual(z.offene_buchung(self.employee)["task"], self.task.name)
		# derselbe Start noch einmal: keine zweite, kein Fehler
		self.assertEqual(
			z.lauf_starten(self.employee, None, None, z.ARBEITSZEIT, ts(MO, "07:00"), "key-1"), lauf
		)
		with self.assertRaises(z.ZeitKonflikt) as ctx:
			z.lauf_starten(self.employee, None, None, z.FAHRZEIT, ts(MO, "08:00"), "key-2")
		self.assertEqual(ctx.exception.grund, "laeuft_bereits")
		self.assertEqual(z.lauf_beenden(self.employee)["sync_key"], "key-1")
		self.assertIsNone(z.offene_buchung(self.employee))
		self.assertIsNone(z.lauf_beenden(self.employee))
		# kein Timesheet durch Start/Ende des Laufs
		self.assertFalse(frappe.db.exists("Timesheet", {"employee": self.employee}))

	def test_tagesbuchungen(self):
		tag = MO + dt.timedelta(days=7)
		self.buchen(ts(tag, "09:30"), ts(tag, "10:00"), art=z.FAHRZEIT)
		self.buchen(ts(tag, "07:00"), ts(tag, "09:30"))
		self.buchen(ts(tag + dt.timedelta(days=1), "07:00"), ts(tag + dt.timedelta(days=1), "08:00"))
		zeilen = z.tagesbuchungen(self.employee, tag)
		self.assertEqual([r["activity_type"] for r in zeilen], ["Arbeitszeit", "Fahrzeit"])
		self.assertEqual(zeilen[0]["task_titel"], "Abdichtung Nordwand")
		self.assertEqual(zeilen[0]["project_name"], self.projekt.project_name)
		self.assertEqual(zeilen[0]["bis"], f"{tag}T09:30:00")
		self.assertTrue(zeilen[0]["sync_key"])

	def test_employee_fuer_user(self):
		self.assertEqual(z.employee_fuer_user(USER)["name"], self.employee)
		self.assertIsNone(z.employee_fuer_user(USER_OHNE))
		self.assertIsNone(z.employee_fuer_user(""))
		self.assertEqual(z.mitarbeiter(self.employee)["user_id"], USER)

	def test_sollstunden(self):
		liste = ensure_holiday_list()
		employee = ensure_employee(USER, liste)
		self.assertEqual(z.sollstunden(employee, "2026-03-02")["stunden"], 8.0)
		ostern = z.sollstunden(employee, "2026-04-06")
		self.assertEqual(
			(ostern["stunden"], ostern["feiertag"], ostern["quelle"]), (0.0, True, "holiday_list")
		)
		self.assertEqual(ostern["beschreibung"], "Ostermontag")
		self.assertEqual(z.sollstunden(employee, "2026-03-07")["stunden"], 0.0)
		self.assertEqual(z.sollstunden(employee, "2026-03-13")["stunden"], 4.0)
		# außerhalb des Zeitraums der Liste: Annahme Mo-Fr 8 h, Sa/So 0 h
		ausserhalb = z.sollstunden(employee, "2026-09-01")
		self.assertEqual((ausserhalb["stunden"], ausserhalb["quelle"]), (8.0, "annahme"))
		self.assertEqual(z.sollstunden(employee, "2026-09-05")["stunden"], 0.0)

	def test_schluessel_fremder_mitarbeiter(self):
		"""Security-Review: ein Key aus fremden Timesheets liefert keine fremden Zeilen."""
		make_user(USER_FREMD, "Fremd", ["Monteur"])
		anderer = ensure_employee(USER_FREMD)
		key = "6f1c4c2e-0000-4000-8000-000000000002"
		tag = MO + dt.timedelta(days=8)
		self.buchen(ts(tag, "07:00"), ts(tag, "08:00"), key=key)
		with self.assertRaises(z.ZeitKonflikt) as ctx:
			z.zeit_buchen(anderer, None, None, z.PAUSE, ts(tag, "12:00"), ts(tag, "12:30"), key)
		self.assertEqual(ctx.exception.grund, "schluessel_fremd")
		self.assertNotIn("zeilen", ctx.exception.details)

	def test_grenzen(self):
		"""Security-Review: höchstens 24 h je Buchung, höchstens 31 Tage zurück, 1 Tag voraus."""
		tag = MO + dt.timedelta(days=9)
		with self.assertRaises(z.ZeitKonflikt) as ctx:
			self.buchen(ts(tag, "07:00"), ts(tag + dt.timedelta(days=400), "07:00"))
		self.assertIn(ctx.exception.grund, ("zu_lang", "ausserhalb_zeitraum"))
		with self.assertRaises(z.ZeitKonflikt) as ctx:
			self.buchen(ts(tag, "07:00"), ts(tag + dt.timedelta(days=1), "07:01"))
		self.assertEqual(ctx.exception.grund, "zu_lang")
		alt = dt.date.today() - dt.timedelta(days=40)
		with self.assertRaises(z.ZeitKonflikt) as ctx:
			self.buchen(ts(alt, "07:00"), ts(alt, "08:00"))
		self.assertEqual(ctx.exception.grund, "ausserhalb_zeitraum")
		zukunft = dt.date.today() + dt.timedelta(days=3)
		with self.assertRaises(z.ZeitKonflikt):
			z.lauf_starten(self.employee, None, None, z.ARBEITSZEIT, ts(zukunft, "07:00"), "k")
		self.assertFalse(frappe.db.exists("Timesheet", {"employee": self.employee}))
		# genau 24 h sind erlaubt (zwei Tagesteile)
		r = self.buchen(ts(tag, "06:00"), ts(tag + dt.timedelta(days=1), "06:00"))
		self.assertEqual([zl["stunden"] for zl in r["zeilen"]], [18.0, 6.0])
