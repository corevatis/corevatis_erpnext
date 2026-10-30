"""Aufgabe 5.2: Wochenplan und Zuweisung (ToDo) - gegen echtes ERPNext v16.

Abnahme „Plan für 2 Wochen sichtbar“ (Adapter-Seite): Zuweisung legt genau ein ToDo an, eine
Wiederholung keins, Entfernen storniert, Monteur 403, Selbstzuweisung abgelehnt, der Plan liefert
zwei Wochen mit den Tagesstunden (von Hand nachgerechnet) und markiert Überlast.

Eigene Testdaten je Test (Projekt, Tasks ab dem nächsten Montag ``M``), Feiertage außen vor
(eigener Test). Sollwerte (Arbeitsbeginn 07:00, Tagesarbeitszeit 8 h):

| Task | Beginn - Ende | Soll h | je Tag |
|---|---|---|---|
| T1 | M 07:00 - M 15:00 | 8 | M 8 |
| T2 | M 07:00 - Di 11:00 | 12 | M 8 (8 h Überschneidung), Di 4 (4 h) |
| T3 (nach T1) | Mi 07:00 - Mi 11:00 | 8 | Mi 8 |
| T4 | Do - Fr (nur Datum) | 16 | Do 8, Fr 8 |
| T5 | Fr 11:00 - Mo (Woche 2) 11:00 | 16 | Fr 8, Mo+7 8 (Wochenende frei) |
| T6 | Mo+14 (Woche 3) | 8 | nicht im Plan |
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from corevatis_erpnext.corevatis_erpnext.report.wochenplan import wochenplan as report
from corevatis_erpnext.planung import wochenplan, zuweisung
from corevatis_erpnext.planung.auftrag import PlanungFehler
from corevatis_erpnext.tests._angebot import firma
from corevatis_erpnext.tests._helpers import make_user
from corevatis_erpnext.tests.test_zeiterfassung import ensure_gender

BUERO = "cv-plan52-buero@example.com"
MEISTER = "cv-plan52-meister@example.com"
MONTEUR = "cv-plan52-monteur@example.com"
MONTEUR2 = "cv-plan52-monteur2@example.com"
OHNE_ROLLE = "cv-plan52-ohne@example.com"
DEAKTIVIERT = "cv-plan52-deaktiviert@example.com"


def naechster_montag() -> dt.date:
	tag = getdate(today())
	return tag + dt.timedelta(days=7 - tag.weekday())


def um(tag: dt.date, uhrzeit: str = "00:00") -> dt.datetime:
	return dt.datetime.combine(tag, dt.time.fromisoformat(uhrzeit))


def employee(user: str, name: str, status: str = "Active") -> str:
	vorhanden = frappe.db.get_value("Employee", {"user_id": user})
	if vorhanden:
		return vorhanden
	return (
		frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": name,
				"last_name": "Plan52",
				"company": firma(),
				"gender": ensure_gender(),
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2020-01-01",
				"status": status,
				"user_id": user,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def als(user: str, funktion, *args, **kwargs):
	frappe.set_user(user)
	try:
		return funktion(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


class TestWochenplan(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		from corevatis_erpnext.planung.felder import ensure_planung_felder

		ensure_planung_felder()
		make_user(BUERO, "Büro Plan", ["Büro", "Projects User"])
		make_user(MEISTER, "Meister Plan", ["Meister", "Projects Manager"])
		make_user(MONTEUR, "Anton Monteur", ["Monteur"])
		make_user(MONTEUR2, "Berta Monteurin", ["Monteur"])
		make_user(OHNE_ROLLE, "Ohne Rolle", ["Projects User"])
		make_user(DEAKTIVIERT, "Deaktiviert", ["Monteur"])
		cls.e_meister = employee(MEISTER, "Meister")
		cls.e_monteur = employee(MONTEUR, "Anton")
		cls.e_monteur2 = employee(MONTEUR2, "Berta")
		cls.e_ohne = employee(OHNE_ROLLE, "Ohne")
		cls.e_deaktiviert = employee(DEAKTIVIERT, "Deaktiviert")
		frappe.db.set_value("User", DEAKTIVIERT, "enabled", 0)
		cls.e_inaktiv = frappe.db.get_value("Employee", {"employee_name": "Inaktiv Plan52"}) or (
			frappe.get_doc(
				{
					"doctype": "Employee",
					"first_name": "Inaktiv",
					"last_name": "Plan52",
					"company": firma(),
					"gender": ensure_gender(),
					"date_of_birth": "1990-01-01",
					"date_of_joining": "2020-01-01",
					"relieving_date": "2025-12-31",
					"status": "Left",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		cls.e_ohne_user = frappe.db.get_value("Employee", {"employee_name": "Ohne Benutzer Plan52"}) or (
			frappe.get_doc(
				{
					"doctype": "Employee",
					"first_name": "Ohne Benutzer",
					"last_name": "Plan52",
					"company": firma(),
					"gender": ensure_gender(),
					"date_of_birth": "1990-01-01",
					"date_of_joining": "2020-01-01",
					"status": "Active",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.savepoint("wochenplan_test")
		self.addCleanup(self._zuruecksetzen)
		p = patch.object(wochenplan, "freie_tage", lambda von: set())
		p.start()
		self.addCleanup(p.stop)
		self.m = naechster_montag()
		m = self.m
		self.projekt = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"Wochenplan Test {frappe.generate_hash(length=6)}",
				"company": firma(),
				"expected_start_date": add_days(m, -7),
				"expected_end_date": add_days(m, 30),
			}
		).insert(ignore_permissions=True)
		self.t1 = self.task("Allgemeine Leistungen - AL-01", um(m, "07:00"), um(m, "15:00"), 8, "Sonstiges")
		self.t2 = self.task("Außenabdichtung - Sockel", um(m, "07:00"), um(m + dt.timedelta(1), "11:00"), 12)
		self.t3 = self.task(
			"Innenabdichtung - Keller",
			um(m + dt.timedelta(2), "07:00"),
			um(m + dt.timedelta(2), "11:00"),
			8,
			"Innenabdichtung",
			vorgaenger=self.t1,
		)
		self.t4 = self.task(
			"Sanierputz - Keller", um(m + dt.timedelta(3)), um(m + dt.timedelta(4)), 16, "Sanierputz"
		)
		self.t5 = self.task(
			"Horizontalsperre - Keller",
			um(m + dt.timedelta(4), "11:00"),
			um(m + dt.timedelta(7), "11:00"),
			16,
			"Horizontalsperre Injektion",
		)
		self.t6 = self.task(
			"Woche 3", um(m + dt.timedelta(14), "07:00"), um(m + dt.timedelta(14), "15:00"), 8
		)

	def _zuruecksetzen(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="wochenplan_test")
		from frappe.model.document import clear_document_cache

		for doctype in ("Project", "Task", "ToDo"):
			clear_document_cache(doctype)

	def task(self, titel, start, ende, stunden, verfahren="Außenabdichtung", vorgaenger=None) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Task",
					"subject": titel,
					"project": self.projekt.name,
					"company": firma(),
					"status": "Open",
					"exp_start_date": start,
					"exp_end_date": ende,
					"expected_time": stunden,
					"cv_verfahren": verfahren,
					"depends_on": [{"task": vorgaenger}] if vorgaenger else [],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def todos(self, task: str, **filters) -> list[frappe._dict]:
		return frappe.get_all(
			"ToDo",
			filters={"reference_type": "Task", "reference_name": task, **filters},
			fields=["name", "allocated_to", "status", "owner", "assigned_by", "date"],
		)

	# --- Zuweisung -----------------------------------------------------------------------------

	def test_zuweisen_genau_ein_todo_wiederholung_keins(self):
		r = als(BUERO, zuweisung.zuweisen, self.t1, self.e_monteur)
		self.assertTrue(r["neu"])
		self.assertEqual((r["user"], r["employee"]), (MONTEUR, self.e_monteur))
		todos = self.todos(self.t1)
		self.assertEqual(len(todos), 1)
		todo = todos[0]
		self.assertEqual(
			(todo.allocated_to, todo.status, todo.owner, todo.assigned_by), (MONTEUR, "Open", BUERO, BUERO)
		)
		# kein Datum: maßgeblich ist der Beginn der Task (baustellen_heute)
		self.assertIsNone(todo.date)
		self.assertEqual(r["todo"], todo.name)
		self.assertIn(MONTEUR, frappe.parse_json(frappe.db.get_value("Task", self.t1, "_assign")))
		# Wiederholung (Büro und Meister): kein weiteres ToDo
		for user in (BUERO, MEISTER):
			r = als(user, zuweisung.zuweisen, self.t1, self.e_monteur)
			self.assertFalse(r["neu"])
			self.assertEqual(r["todo"], todo.name)
			self.assertIn("bereits zugewiesen", r["hinweise"][0])
		self.assertEqual(len(self.todos(self.t1)), 1)
		# zweiter Mitarbeiter: eigenes ToDo
		als(MEISTER, zuweisung.zuweisen, self.t1, self.e_monteur2)
		self.assertEqual(
			sorted(t.allocated_to for t in self.todos(self.t1, status="Open")), sorted([MONTEUR, MONTEUR2])
		)

	def test_entfernen_storniert_und_ist_idempotent(self):
		als(BUERO, zuweisung.zuweisen, self.t1, self.e_monteur)
		r = als(BUERO, zuweisung.zuweisung_entfernen, self.t1, self.e_monteur)
		self.assertTrue(r["entfernt"])
		self.assertEqual([t.status for t in self.todos(self.t1)], ["Cancelled"])
		self.assertEqual(r["zugewiesen"], [])
		self.assertNotIn(MONTEUR, frappe.parse_json(frappe.db.get_value("Task", self.t1, "_assign") or "[]"))
		r = als(MEISTER, zuweisung.zuweisung_entfernen, self.t1, self.e_monteur)
		self.assertFalse(r["entfernt"])
		self.assertEqual(len(self.todos(self.t1)), 1)
		# neu zuweisen: neues offenes ToDo, das stornierte bleibt
		self.assertTrue(als(BUERO, zuweisung.zuweisen, self.t1, self.e_monteur)["neu"])
		self.assertEqual(sorted(t.status for t in self.todos(self.t1)), ["Cancelled", "Open"])

	def test_rechte_monteur_403(self):
		for funktion, args in (
			(zuweisung.zuweisen, (self.t1, self.e_monteur)),  # Selbstzuweisung
			(zuweisung.zuweisen, (self.t1, self.e_monteur2)),
			(zuweisung.zuweisung_entfernen, (self.t1, self.e_monteur)),
			(zuweisung.zuweisungen, (self.t1,)),
			(zuweisung.mitarbeiter, ()),
			(wochenplan.wochenplan, (str(self.m),)),
			(report.execute, ({"von": str(self.m)},)),
		):
			with self.assertRaises(frappe.PermissionError, msg=funktion.__name__):
				als(MONTEUR, funktion, *args)
		self.assertEqual(self.todos(self.t1), [])
		# ohne Fachrolle ebenso
		with self.assertRaises(frappe.PermissionError):
			als(OHNE_ROLLE, zuweisung.zuweisen, self.t1, self.e_monteur)

	def test_selbstzuweisung_per_todo_abgelehnt(self):
		"""Ein Monteur legt sich kein ToDo auf eine Task an (ToDo-Hook des Kerns, 3.12).
		Die CI-Bench hat den Kern nicht - dort prüft nur der Kern-Test; hier der Weg über die
		Whitelist (oben) und, mit Kern, der direkte Weg."""
		if "corevatis" not in frappe.get_installed_apps():
			self.skipTest("Kern corevatis nicht installiert (ToDo-Hook projekt_zugang.todo_pruefen)")
		with self.assertRaises(frappe.PermissionError):
			als(
				MONTEUR,
				lambda: frappe.get_doc(
					{
						"doctype": "ToDo",
						"allocated_to": MONTEUR,
						"reference_type": "Task",
						"reference_name": self.t1,
						"description": "x",
					}
				).insert(),
			)

	def test_mitarbeiter_pruefung(self):
		for employee_, fehler in (
			(self.e_ohne, PlanungFehler),  # Benutzer ohne Rolle Monteur/Meister
			(self.e_deaktiviert, PlanungFehler),  # Benutzer deaktiviert
			(self.e_inaktiv, PlanungFehler),  # Employee nicht aktiv
			(self.e_ohne_user, PlanungFehler),  # kein Benutzer
			("HR-EMP-GIBTSNICHT", frappe.DoesNotExistError),
		):
			with self.assertRaises(fehler, msg=employee_):
				als(BUERO, zuweisung.zuweisen, self.t1, employee_)
		self.assertEqual(self.todos(self.t1), [])
		# Meister ist planbar
		self.assertTrue(als(BUERO, zuweisung.zuweisen, self.t1, self.e_meister)["neu"])
		# Auswahl: nur aktive Employees mit aktivem Benutzer und Rolle Monteur/Meister
		auswahl = {m["value"] for m in als(BUERO, zuweisung.mitarbeiter)}
		self.assertLessEqual({self.e_meister, self.e_monteur, self.e_monteur2}, auswahl)
		self.assertFalse({self.e_ohne, self.e_deaktiviert, self.e_inaktiv, self.e_ohne_user} & auswahl)
		# erledigte/stornierte Task: keine neue Zuweisung
		frappe.db.set_value("Task", self.t6, "status", "Cancelled")
		with self.assertRaises(PlanungFehler):
			als(BUERO, zuweisung.zuweisen, self.t6, self.e_monteur)
		with self.assertRaises(frappe.DoesNotExistError):
			als(BUERO, zuweisung.zuweisen, "TASK-GIBTSNICHT", self.e_monteur)

	def test_hinweis_vorgaenger_nicht_fertig(self):
		r = als(BUERO, zuweisung.zuweisen, self.t3, self.e_monteur2)
		self.assertTrue(any(self.t1 in h and "nicht fertig" in h for h in r["hinweise"]), r["hinweise"])
		stand = als(BUERO, zuweisung.zuweisungen, self.t3)
		self.assertEqual([v["task"] for v in stand["vorgaenger_offen"]], [self.t1])
		self.assertEqual([z["employee"] for z in stand["zugewiesen"]], [self.e_monteur2])
		# T1 erledigt (ERPNext schließt dessen Zuweisungen) → kein Hinweis mehr
		als(BUERO, zuweisung.zuweisen, self.t1, self.e_monteur)
		t1 = frappe.get_doc("Task", self.t1)
		t1.status = "Completed"
		t1.save(ignore_permissions=True)
		self.assertEqual([t.status for t in self.todos(self.t1)], ["Closed"])
		self.assertEqual(als(BUERO, zuweisung.zuweisungen, self.t3)["vorgaenger_offen"], [])
		als(BUERO, zuweisung.zuweisung_entfernen, self.t3, self.e_monteur2)
		r = als(BUERO, zuweisung.zuweisen, self.t3, self.e_monteur2)
		self.assertFalse([h for h in r["hinweise"] if "nicht fertig" in h])

	def test_hinweis_ueberlast_bei_zuweisung(self):
		self.assertEqual(als(BUERO, zuweisung.zuweisen, self.t1, self.e_monteur)["hinweise"], [])
		r = als(BUERO, zuweisung.zuweisen, self.t2, self.e_monteur)
		self.assertEqual(len(r["hinweise"]), 1, r["hinweise"])
		self.assertIn("Tageslast über 8", r["hinweise"][0])
		self.assertIn("16", r["hinweise"][0])

	# --- Wochenplan ----------------------------------------------------------------------------

	def zuweisen_wie_im_test(self):
		for task, employee_ in (
			(self.t1, self.e_monteur),
			(self.t2, self.e_monteur),
			(self.t3, self.e_monteur),
			(self.t3, self.e_monteur2),
			(self.t5, self.e_meister),
			(self.t6, self.e_monteur),
		):
			als(BUERO, zuweisung.zuweisen, task, employee_)

	def zeile(self, plan: dict, zeile: str) -> dict:
		return next(z for z in plan["zeilen"] if z["zeile"] == zeile)

	def test_plan_zwei_wochen_tagesstunden_und_ueberlast(self):
		self.zuweisen_wie_im_test()
		m = self.m
		s = wochenplan.spalte
		tag = lambda n: m + dt.timedelta(days=n)  # noqa: E731
		# beliebiger Tag der Woche → Montag; 10 Arbeitstage
		plan = als(BUERO, wochenplan.wochenplan, str(tag(2)), self.projekt.name)
		self.assertEqual((plan["von"], plan["bis"]), (str(m), str(tag(13))))
		self.assertEqual(
			[t["datum"] for t in plan["tage"]], [str(tag(n)) for n in (0, 1, 2, 3, 4, 7, 8, 9, 10, 11)]
		)
		# Anton: T1 8 + T2 8 am Montag (Überlast), Di 4, Mi T3 halb (mit Berta) 4
		anton = self.zeile(plan, "M:" + MONTEUR)
		self.assertEqual(
			(anton[s(m)], anton[s(tag(1))], anton[s(tag(2))], anton[s(tag(3))]), (16.0, 4.0, 4.0, 0.0)
		)
		self.assertEqual(anton["ueberlast"], [s(m)])
		self.assertEqual(anton["summe"], 24.0)
		self.assertEqual(anton["mitarbeiter"], self.e_monteur)
		kinder = [z for z in plan["zeilen"] if z["eltern"] == "M:" + MONTEUR]
		self.assertEqual({k["task"] for k in kinder}, {self.t1, self.t2, self.t3})  # T6 in Woche 3
		t2 = next(k for k in kinder if k["task"] == self.t2)
		self.assertEqual((t2[s(m)], t2[s(tag(1))], t2["sollstunden"], t2["indent"]), (8.0, 4.0, 12.0, 1))
		t3 = next(k for k in kinder if k["task"] == self.t3)
		self.assertEqual((t3[s(tag(2))], t3["anteil"]), (4.0, 2))
		self.assertIn("Innenabdichtung", t3["bezeichnung"])
		self.assertIn(self.projekt.project_name, t3["bezeichnung"])
		berta = self.zeile(plan, "M:" + MONTEUR2)
		self.assertEqual((berta[s(tag(2))], berta["ueberlast"]), (4.0, []))
		# Meister: T5 Fr 8, Mo der zweiten Woche 8 - genau 8 ist keine Überlast
		meister = self.zeile(plan, "M:" + MEISTER)
		self.assertEqual((meister[s(tag(4))], meister[s(tag(7))], meister["ueberlast"]), (8.0, 8.0, []))
		# nicht zugewiesen: T4 Do 8, Fr 8 (Termine nur als Datum)
		offen = self.zeile(plan, "M:" + wochenplan.NICHT_ZUGEWIESEN)
		self.assertEqual((offen[s(tag(3))], offen[s(tag(4))], offen["anzahl"]), (8.0, 8.0, 1))
		self.assertEqual([z["task"] for z in plan["zeilen"] if z["eltern"] == offen["zeile"]], [self.t4])
		self.assertEqual((plan["pakete"], plan["nicht_zugewiesen"]), (5, 1))
		self.assertGreaterEqual(plan["ueberlast_tage"], 1)
		# Baum: Kinder direkt nach ihrem Mitarbeiter
		for i, z in enumerate(plan["zeilen"]):
			if z["indent"] == 1:
				self.assertEqual(
					plan["zeilen"][i - 1]["zeile"]
					if plan["zeilen"][i - 1]["indent"] == 0
					else plan["zeilen"][i - 1]["eltern"],
					z["eltern"],
				)

		# nächste Woche umschalten: Woche 2 und 3, T6 erscheint bei Anton
		plan2 = als(BUERO, wochenplan.wochenplan, str(tag(7)), self.projekt.name)
		anton2 = self.zeile(plan2, "M:" + MONTEUR)
		self.assertEqual((anton2[s(tag(14))], anton2["ueberlast"]), (8.0, []))
		self.assertEqual(self.zeile(plan2, "M:" + MEISTER)[s(tag(7))], 8.0)

		# Filter Mitarbeiter: nur Berta, keine Zeile „Nicht zugewiesen“
		nur_berta = als(MEISTER, wochenplan.wochenplan, str(m), None, self.e_monteur2)
		self.assertEqual({z["eltern"] or z["zeile"] for z in nur_berta["zeilen"]}, {"M:" + MONTEUR2})

	def test_tageslast_ueber_alle_projekte(self):
		"""Mit Projektfilter bleibt die Tageslast die über alle Projekte (Kapazität)."""
		als(BUERO, zuweisung.zuweisen, self.t1, self.e_monteur)
		anderes = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"Anderes {frappe.generate_hash(length=6)}",
				"company": firma(),
			}
		).insert(ignore_permissions=True)
		fremd = frappe.get_doc(
			{
				"doctype": "Task",
				"subject": "Fremdes Paket",
				"project": anderes.name,
				"exp_start_date": um(self.m, "07:00"),
				"exp_end_date": um(self.m, "11:00"),
				"expected_time": 4,
			}
		).insert(ignore_permissions=True)
		als(BUERO, zuweisung.zuweisen, fremd.name, self.e_monteur)
		plan = als(BUERO, wochenplan.wochenplan, str(self.m), self.projekt.name)
		anton = self.zeile(plan, "M:" + MONTEUR)
		self.assertEqual(
			(anton[wochenplan.spalte(self.m)], anton["ueberlast"]), (12.0, [wochenplan.spalte(self.m)])
		)
		self.assertEqual([z["task"] for z in plan["zeilen"] if z["eltern"] == "M:" + MONTEUR], [self.t1])

	def test_report_spalten_und_zusammenfassung(self):
		self.zuweisen_wie_im_test()
		spalten, daten, nachricht, _chart, zusammenfassung = als(
			MEISTER, report.execute, frappe._dict(von=str(self.m), projekt=self.projekt.name)
		)
		tage = [c for c in spalten if c["fieldname"].startswith("d_")]
		self.assertEqual(len(tage), 10)
		self.assertEqual(tage[0]["label"], "Mo " + self.m.strftime("%d.%m."))
		self.assertEqual(tage[5]["label"], "Mo " + (self.m + dt.timedelta(days=7)).strftime("%d.%m."))
		self.assertIn("aktion", [c["fieldname"] for c in spalten])
		self.assertEqual(daten, wochenplan.plan(self.m, self.projekt.name)["zeilen"])
		self.assertIn("8 h", nachricht)
		werte = {z["label"]: z["value"] for z in zusammenfassung}
		self.assertEqual((werte["Arbeitspakete"], werte["nicht zugewiesen"]), (5, 1))
		self.assertGreaterEqual(werte["Tage über 8 h"], 1)

	def test_stunden_je_tag(self):
		f = wochenplan.stunden_je_tag
		m = self.m
		# Feiertag am Dienstag: Stunden auf Mo und Mi
		self.assertEqual(
			f(um(m, "07:00"), um(m + dt.timedelta(2), "15:00"), 16, "07:00:00", 8, {m + dt.timedelta(1)}),
			{m: 8.0, m + dt.timedelta(2): 8.0},
		)
		# nur außerhalb der Arbeitszeit (z. B. 18-20 Uhr): gleichmäßig auf den Tag
		self.assertEqual(f(um(m, "18:00"), um(m, "20:00"), 2, "07:00:00", 8, set()), {m: 2.0})
		# ohne Ende, ohne Stunden, ohne Beginn
		self.assertEqual(f(um(m, "07:00"), None, 3, "07:00:00", 8, set()), {m: 3.0})
		# ohne Sollstunden sichtbar mit 0 h; nur Datum ohne Ende = ganzer Tag
		self.assertEqual(f(um(m, "07:00"), um(m, "15:00"), 0, "07:00:00", 8, set()), {m: 0.0})
		self.assertEqual(f(um(m), None, 4, "07:00:00", 8, set()), {m: 4.0})
		self.assertEqual(f(None, um(m, "15:00"), 8, "07:00:00", 8, set()), {})
		# Zeitplan aus 5.1 (Team 2): 20,59 h ab Mo 08:30 bis Di 10:47:42 → 6,5 h + 3,795 h Uhrzeit
		v = f(
			um(m, "08:30"),
			dt.datetime.combine(m + dt.timedelta(1), dt.time(10, 47, 42)),
			20.59,
			"07:00:00",
			8,
			set(),
		)
		self.assertAlmostEqual(v[m], 20.59 * 6.5 / (6.5 + 3.795), places=6)
		self.assertAlmostEqual(sum(v.values()), 20.59, places=6)

	# --- Monteur-Sicht und Desk ----------------------------------------------------------------

	def test_monteur_sieht_zuweisung_von_heute(self):
		"""Die Zuweisung erfüllt die Bedingungen von ``baustellen_heute`` (Kern, 3.1): offenes ToDo des
		Monteurs ohne Datum auf eine offene Task mit Beginn ≤ heute, angelegt von einem anderen
		(Projektzugang 3.12). Im Desk zeigt „Meine Baustellen“ sie (``meine_baustellen``)."""
		from corevatis_erpnext.prozess import uebersicht

		heute = getdate(today())
		frappe.db.set_value("Project", self.projekt.name, "expected_start_date", add_days(heute, -1))
		jetzt = self.task("Heute", um(heute, "07:00"), um(heute, "15:00"), 8)
		als(BUERO, zuweisung.zuweisen, jetzt, self.e_monteur)
		todo = self.todos(jetzt)[0]
		self.assertEqual((todo.allocated_to, todo.status, todo.date), (MONTEUR, "Open", None))
		self.assertNotEqual(todo.owner, MONTEUR)
		ergebnis = als(MONTEUR, uebersicht.meine_baustellen)
		self.assertIn(jetzt, [t["name"] for t in ergebnis["tasks"]])
		# T1 beginnt erst am nächsten Montag: heute noch nicht
		als(BUERO, zuweisung.zuweisen, self.t1, self.e_monteur)
		self.assertNotIn(self.t1, [t["name"] for t in als(MONTEUR, uebersicht.meine_baustellen)["tasks"]])

	def test_felder_hooks_report_workspace(self):
		meta = frappe.get_meta("Task")
		self.assertEqual(meta.get_field("cv_zuweisung").fieldtype, "HTML")
		self.assertEqual(meta.get_field("cv_section_zuweisung").fieldtype, "Section Break")
		js = frappe.get_hooks("doctype_js", app_name="corevatis_erpnext")
		self.assertEqual(js["Task"], ["public/js/zuweisung.js", "public/js/task_zuweisung.js"])
		self.assertIn("public/js/planung.js", js["Project"])
		bericht = frappe.get_doc("Report", "Wochenplan")
		self.assertEqual((bericht.report_type, bericht.ref_doctype), ("Script Report", "Task"))
		self.assertEqual({r.role for r in bericht.roles}, {"Büro", "Meister", "System Manager"})
		ws = frappe.get_doc("Workspace", "Vorgänge")
		self.assertIn("Wochenplan", {s.link_to for s in ws.shortcuts if s.type == "Report"})
		self.assertIn("Wochenplan", {l.link_to for l in ws.links if l.type == "Link"})
		inhalt = frappe.parse_json(ws.content)
		self.assertIn(
			"Wochenplan", {b["data"].get("shortcut_name") for b in inhalt if b["type"] == "shortcut"}
		)
		from frappe.translate import get_translations_from_csv

		de = get_translations_from_csv("de", "corevatis_erpnext")
		for feld in ("cv_section_zuweisung", "cv_zuweisung"):
			self.assertIn(meta.get_field(feld).label, de)
