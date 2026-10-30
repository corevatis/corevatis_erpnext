"""Aufgaben 6.1/6.2 im Adapter: Mangel → Task (Fristen, Idempotenz, Rückmeldung „Behoben“, Fehler
halten den Abschluss nicht auf), Abnahme und Gewährleistung am Projekt, Rechnungsfreigabe
(Rechnung ohne Abnahme blockiert, Abschlag erlaubt, Übersteuerung nur System Manager) und die
Prozessleiste (Schritt „Abnahme“ über das Protokoll).

Die CI-Bench dieses Repos hat ``corevatis`` nicht: ``Abnahmeprotokoll`` und ``Abnahme Mangel``
sind Stellvertreter-Doctypes (custom, nur für diese Klasse) mit den Feldern des Vertrags
(``corevatis_erpnext.abnahme.konstanten``, geprüft im Vertragstest des Kerns). Angebot, Annahme,
Auftrag, Projekt, Task und Rechnung sind echt (4.4).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_years, getdate, now_datetime, today

from corevatis_erpnext import abnahme
from corevatis_erpnext.abnahme import konstanten as K
from corevatis_erpnext.abnahme.einstellungen import EINSTELLUNGEN
from corevatis_erpnext.abnahme.felder import ensure_abnahme_felder
from corevatis_erpnext.abnahme.rechnung import RechnungOhneAbnahme
from corevatis_erpnext.angebot import annahme
from corevatis_erpnext.prozess.kette import status
from corevatis_erpnext.tests import _angebot as td
from corevatis_erpnext.tests._helpers import make_user

BUERO = "cv-abnahme-buero@example.com"
MEISTER = "cv-abnahme-meister@example.com"
MONTEUR = "cv-abnahme-monteur@example.com"
STELLVERTRETER = (K.ABNAHMEPROTOKOLL, K.ABNAHME_MANGEL)
RECHTE = [
	{"role": role, "read": 1, "write": 1, "create": 1, "delete": 1}
	for role in ("System Manager", "Büro", "Meister")
]


def _stellvertreter() -> None:
	if not frappe.db.exists("DocType", K.ABNAHME_MANGEL):
		frappe.get_doc(
			{
				"doctype": "DocType",
				"name": K.ABNAHME_MANGEL,
				"module": "Corevatis ERPNext",
				"custom": 1,
				"istable": 1,
				"fields": [
					{"fieldname": "beschreibung", "fieldtype": "Small Text", "label": "Beschreibung"},
					{"fieldname": "raum", "fieldtype": "Data", "label": "Raum"},
					{"fieldname": "bauteil", "fieldtype": "Data", "label": "Bauteil"},
					{"fieldname": "foto", "fieldtype": "Data", "label": "Foto"},
					{"fieldname": "frist", "fieldtype": "Date", "label": "Frist"},
					{
						"fieldname": "status",
						"fieldtype": "Select",
						"options": "Offen\nBehoben",
						"default": "Offen",
						"label": "Status",
					},
					{"fieldname": "behoben_am", "fieldtype": "Datetime", "label": "Behoben am"},
					{"fieldname": "task", "fieldtype": "Data", "label": "Task"},
				],
			}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("DocType", K.ABNAHMEPROTOKOLL):
		frappe.get_doc(
			{
				"doctype": "DocType",
				"name": K.ABNAHMEPROTOKOLL,
				"module": "Corevatis ERPNext",
				"custom": 1,
				"autoname": "hash",
				"fields": [
					{"fieldname": "projekt", "fieldtype": "Data", "label": "Projekt"},
					{
						"fieldname": "status",
						"fieldtype": "Select",
						"options": "Entwurf\nAbgeschlossen",
						"default": "Entwurf",
						"label": "Status",
					},
					{
						"fieldname": "ergebnis",
						"fieldtype": "Select",
						"options": "\n" + "\n".join(K.ERGEBNISSE),
						"label": "Ergebnis",
					},
					{"fieldname": "datum", "fieldtype": "Date", "label": "Datum"},
					{"fieldname": "gewaehrleistung_beginn", "fieldtype": "Date", "label": "Beginn"},
					{"fieldname": "abgeschlossen_am", "fieldtype": "Datetime", "label": "Abgeschlossen am"},
					{"fieldname": "vorbehalt_maengel", "fieldtype": "Check", "label": "Vorbehalt Mängel"},
					{
						"fieldname": "vorbehalt_vertragsstrafe",
						"fieldtype": "Check",
						"label": "Vorbehalt Strafe",
					},
					{
						"fieldname": "maengel",
						"fieldtype": "Table",
						"options": K.ABNAHME_MANGEL,
						"label": "Mängel",
					},
				],
				"permissions": RECHTE,
			}
		).insert(ignore_permissions=True)


def _als(user: str, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def _schritt(stand: dict, key: str) -> dict:
	return next(s for s in stand["schritte"] if s["key"] == key)


class TestAbnahme(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if K.CORE_APP in frappe.get_installed_apps():
			raise unittest.SkipTest("corevatis installiert - die Stellvertreter-Doctypes würden kollidieren")
		_stellvertreter()
		ensure_abnahme_felder()
		make_user(
			BUERO,
			"Berta",
			["Büro", "Sales User", "Accounts User", "Projects User", "Projects Manager"],
			"Büro",
		)
		make_user(
			MEISTER, "Martin", ["Meister", "Sales User", "Projects User", "Projects Manager"], "Meister"
		)
		make_user(MONTEUR, "Mona", ["Monteur"], "Monteur")
		td.ensure_felder_41()
		td.ensure_artikel()
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for doctype in STELLVERTRETER:
			if frappe.db.exists("DocType", doctype) and frappe.db.get_value("DocType", doctype, "custom"):
				if not frappe.get_meta(doctype).istable:
					for name in frappe.get_all(doctype, pluck="name"):
						frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		for doctype in STELLVERTRETER:
			if frappe.db.exists("DocType", doctype) and frappe.db.get_value("DocType", doctype, "custom"):
				frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Singles", {"doctype": EINSTELLUNGEN})
		# Vorgang wie auf der Site: Angebot (aus dem Aufmaß) annehmen -> Auftrag + Projekt (4.4)
		lead = td.lead_mit_angebot_status()
		angebot = td.angebot(lead.name, zusatz=False)
		r = _als(BUERO, annahme.angebot_annehmen, angebot.name, einreichen=1)
		self.auftrag, self.projekt = r["auftrag"], r["projekt"]
		self.ende = getdate(frappe.db.get_value("Project", self.projekt, "expected_end_date"))

	def protokoll(self, maengel=(), **werte):
		data = {
			"doctype": K.ABNAHMEPROTOKOLL,
			"projekt": self.projekt,
			"datum": today(),
			"maengel": [
				{"beschreibung": text, "raum": "Keller 1", "bauteil": "Nordwand", "frist": frist}
				for text, frist in maengel
			],
		}
		data.update(werte)
		return frappe.get_doc(data).insert()

	def abschliessen(self, doc, ergebnis=K.ERGEBNIS_MIT_MAENGELN):
		doc.status = K.STATUS_ABGESCHLOSSEN
		doc.ergebnis = ergebnis
		doc.abgeschlossen_am = now_datetime()
		doc.gewaehrleistung_beginn = doc.datum if ergebnis in K.ERGEBNISSE_ABGENOMMEN else None
		doc.save()
		doc.reload()
		return doc

	def tasks(self, doc) -> list:
		return frappe.get_all(
			"Task",
			filters={"cv_abnahmeprotokoll": doc.name},
			fields=["name", "subject", "project", "exp_end_date", "status", "cv_mangel_zeile", "priority"],
			order_by="creation asc",
		)

	def rechnung(self, **werte):
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

		si = make_sales_invoice(self.auftrag)
		si.update(werte)
		return si

	# --- Mangel -> Task ---------------------------------------------------------------------

	def test_mangel_tasks_mit_frist_idempotent(self):
		frist1, frist2 = add_days(today(), 14), add_days(self.ende, 10)
		doc = _als(
			MEISTER,
			self.protokoll,
			maengel=[("Haarriss in der Hohlkehle\nca. 40 cm", frist1), ("Sockel nicht deckend", frist2)],
		)
		self.assertEqual(self.tasks(doc), [])  # Entwurf: keine Tasks
		doc = _als(MEISTER, self.abschliessen, doc)
		tasks = self.tasks(doc)
		self.assertEqual(len(tasks), 2)
		self.assertEqual(
			[t.subject for t in tasks], ["Mangel: Haarriss in der Hohlkehle", "Mangel: Sockel nicht deckend"]
		)
		self.assertEqual([getdate(t.exp_end_date) for t in tasks], [getdate(frist1), getdate(frist2)])
		self.assertEqual({t.project for t in tasks}, {self.projekt})
		self.assertEqual([t.cv_mangel_zeile for t in tasks], [z.name for z in doc.maengel])
		self.assertEqual([z.task for z in doc.maengel], [t.name for t in tasks])
		self.assertIn(doc.name, frappe.db.get_value("Task", tasks[0].name, "description"))
		# Frist nach dem geplanten Projektende: Projektende verschoben
		self.assertEqual(
			getdate(frappe.db.get_value("Project", self.projekt, "expected_end_date")), getdate(frist2)
		)
		# Wiederholung (erneutes Speichern, Button) legt nichts doppelt an
		_als(MEISTER, doc.save)
		frappe.db.set_value(K.ABNAHME_MANGEL, doc.maengel[0].name, "task", None)
		r = _als(BUERO, abnahme.maengel_tasks_erzeugen, doc.name)
		self.assertEqual([t["neu"] for t in r["tasks"]], [False, False])
		self.assertEqual(len(self.tasks(doc)), 2)
		self.assertEqual(frappe.db.get_value(K.ABNAHME_MANGEL, doc.maengel[0].name, "task"), tasks[0].name)
		with self.assertRaises(frappe.PermissionError):
			_als(MONTEUR, abnahme.maengel_tasks_erzeugen, doc.name)

	def test_task_erledigt_setzt_mangel_behoben(self):
		doc = self.abschliessen(
			self.protokoll(maengel=[("Riss", add_days(today(), 7)), ("Fleck", add_days(today(), 7))])
		)
		t1, t2 = (frappe.get_doc("Task", t.name) for t in self.tasks(doc))
		t1.status = "Completed"
		_als(MEISTER, t1.save)
		doc.reload()
		self.assertEqual([z.status for z in doc.maengel], [K.MANGEL_BEHOBEN, K.MANGEL_OFFEN])
		self.assertTrue(doc.maengel[0].behoben_am)
		t1.reload()
		t1.status = "Open"
		_als(MEISTER, t1.save)
		doc.reload()
		self.assertEqual(doc.maengel[0].status, K.MANGEL_OFFEN)
		# Meister setzt die Zeile im Protokoll auf „Behoben“: Task wird erledigt
		doc.maengel[1].status = K.MANGEL_BEHOBEN
		_als(MEISTER, doc.save)
		self.assertEqual(frappe.db.get_value("Task", t2.name, "status"), "Completed")

	def test_fehler_haelt_abschluss_nicht_auf(self):
		doc = self.protokoll(maengel=[("Riss", add_days(today(), 7))])
		with patch.object(abnahme, "maengel_tasks_anlegen", side_effect=RuntimeError("Testfehler")):
			doc = self.abschliessen(doc)
		self.assertEqual(doc.status, K.STATUS_ABGESCHLOSSEN)
		self.assertEqual(self.tasks(doc), [])
		self.assertTrue(frappe.db.exists("Error Log", {"reference_name": doc.name}))
		# Abnahme am Projekt trotzdem gesetzt; Button holt die Tasks nach
		self.assertEqual(
			getdate(frappe.db.get_value("Project", self.projekt, "cv_abgenommen_am")), getdate(today())
		)
		r = _als(BUERO, abnahme.maengel_tasks_erzeugen, doc.name)
		self.assertEqual([t["neu"] for t in r["tasks"]], [True])

	# --- 6.2 Gewährleistung und Rechnung ----------------------------------------------------

	def test_gewaehrleistung_am_projekt(self):
		doc = self.abschliessen(self.protokoll(maengel=[("Riss", add_days(today(), 7))]))
		p = frappe.get_doc("Project", self.projekt)
		self.assertEqual(p.cv_abnahmeprotokoll, doc.name)
		self.assertEqual(p.cv_abnahme_ergebnis, K.ERGEBNIS_MIT_MAENGELN)
		self.assertEqual(getdate(p.cv_abgenommen_am), getdate(today()))
		self.assertEqual((p.cv_vertragsgrundlage, p.cv_gewaehrleistung_jahre), ("BGB", 5))
		self.assertEqual(getdate(p.cv_gewaehrleistung_bis), getdate(add_years(today(), 5)))
		# VOB/B am Projekt: 4 Jahre; Jahre von Hand: neu gerechnet
		p.cv_vertragsgrundlage, p.cv_gewaehrleistung_jahre = "VOB/B", 0
		_als(BUERO, p.save)
		p.reload()
		self.assertEqual(p.cv_gewaehrleistung_jahre, 4)
		self.assertEqual(getdate(p.cv_gewaehrleistung_bis), getdate(add_years(today(), 4)))
		# Abnahmefelder nicht fälschbar
		p.cv_abnahme_ergebnis, p.cv_abgenommen_am = "Abgenommen", add_days(today(), -100)
		_als(BUERO, p.save)
		p.reload()
		self.assertEqual(
			(p.cv_abnahme_ergebnis, getdate(p.cv_abgenommen_am)), (K.ERGEBNIS_MIT_MAENGELN, getdate(today()))
		)
		# Wiederöffnen hebt die Abnahme auf
		doc.status = K.STATUS_ENTWURF
		doc.save()
		p.reload()
		self.assertFalse(p.cv_abgenommen_am or p.cv_abnahmeprotokoll or p.cv_gewaehrleistung_bis)

	def test_gewaehrleistung_vob_aus_einstellungen(self):
		e = frappe.get_single(EINSTELLUNGEN)
		e.vertragsgrundlage = "VOB/B"
		e.save()
		self.abschliessen(self.protokoll(), ergebnis=K.ERGEBNIS_ABGENOMMEN)
		p = frappe.get_doc("Project", self.projekt)
		self.assertEqual((p.cv_vertragsgrundlage, p.cv_gewaehrleistung_jahre), ("VOB/B", 4))
		self.assertEqual(getdate(p.cv_gewaehrleistung_bis), getdate(add_years(today(), 4)))

	def test_rechnung_ohne_abnahme_blockiert(self):
		with self.assertRaises(RechnungOhneAbnahme):
			_als(BUERO, self.rechnung().insert)
		# Abschlagsrechnung erlaubt
		abschlag = _als(BUERO, self.rechnung(cv_abschlag=1).insert)
		self.assertTrue(abschlag.name)
		# Übersteuern: Büro nicht, System Manager nur mit Begründung (protokolliert)
		with self.assertRaises(frappe.PermissionError):
			_als(
				BUERO,
				self.rechnung(cv_abnahme_uebersteuert=1, cv_uebersteuerung_grund="Kunde zahlt vorab").insert,
			)
		with self.assertRaises(frappe.MandatoryError):
			self.rechnung(cv_abnahme_uebersteuert=1, cv_uebersteuerung_grund="kurz").insert()
		frei = self.rechnung(
			cv_abnahme_uebersteuert=1, cv_uebersteuerung_grund="Kunde zahlt vorab, Abnahme folgt"
		)
		frei.insert()
		self.assertEqual(frei.cv_uebersteuert_von, "Administrator")
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "Sales Invoice",
					"reference_name": frei.name,
					"content": ["like", "%ohne Abnahme freigegeben%"],
				},
			)
		)
		# Büro ändert die freigegebene Rechnung (Übersteuerung unverändert) - erlaubt
		frei.reload()
		frei.remarks = "geprüft"
		_als(BUERO, frei.save)
		# Abnahme verweigert: weiter blockiert; abgenommen: Rechnung möglich
		doc = self.abschliessen(
			self.protokoll(maengel=[("Nordwand fehlt", add_days(today(), 7))]), ergebnis=K.ERGEBNIS_VERWEIGERT
		)
		with self.assertRaises(RechnungOhneAbnahme):
			_als(BUERO, self.rechnung().insert)
		doc.status = K.STATUS_ENTWURF
		doc.save()
		self.abschliessen(doc, ergebnis=K.ERGEBNIS_MIT_MAENGELN)
		schluss = _als(BUERO, self.rechnung().insert)
		self.assertTrue(schluss.name)

	def test_rechnung_projekt_in_position_und_sperre_abschaltbar(self):
		si = self.rechnung()
		si.project = None
		for zeile in si.items:
			zeile.sales_order = None
			zeile.so_detail = None
			zeile.project = self.projekt
		with self.assertRaises(RechnungOhneAbnahme):
			si.insert()
		e = frappe.get_single(EINSTELLUNGEN)
		e.rechnung_ohne_abnahme_sperren = 0
		e.save()
		self.assertTrue(self.rechnung().insert().name)

	# --- Prozessleiste ----------------------------------------------------------------------

	def test_prozessleiste_schritt_abnahme(self):
		stand = _als(BUERO, status, "Project", self.projekt)
		abn = _schritt(stand, "abnahme")
		self.assertEqual(abn["status"], "offen")
		self.assertEqual(abn["aktion"]["neu"], K.ABNAHMEPROTOKOLL)
		self.assertEqual(abn["aktion"]["werte"], {"projekt": self.projekt})
		self.assertEqual(
			_schritt(stand, "rechnung")["text"], "Schlussrechnung nach der Abnahme (Abschlag möglich)"
		)
		# Projekt „Completed“ zählt nicht mehr als Abnahme
		frappe.db.set_value("Project", self.projekt, "status", "Completed")
		self.assertEqual(_schritt(status("Project", self.projekt), "abnahme")["status"], "offen")
		frappe.db.set_value("Project", self.projekt, "status", "Open")
		doc = self.protokoll(maengel=[("Riss", add_days(today(), 7))])
		abn = _schritt(_als(BUERO, status, "Project", self.projekt), "abnahme")
		self.assertEqual((abn["status"], abn["aktion"]), ("aktiv", None))
		self.assertEqual(abn["belege"][0]["name"], doc.name)
		self.abschliessen(doc, ergebnis=K.ERGEBNIS_VERWEIGERT)
		abn = _schritt(status("Project", self.projekt), "abnahme")
		self.assertEqual(abn["status"], "aktiv")
		self.assertIn("verweigert", abn["text"])
		doc.status = K.STATUS_ENTWURF
		doc.save()
		self.abschliessen(doc, ergebnis=K.ERGEBNIS_MIT_MAENGELN)
		for start in (
			("Project", self.projekt),
			(K.ABNAHMEPROTOKOLL, doc.name),
			("Sales Order", self.auftrag),
		):
			with self.subTest(start=start):
				stand = status(*start)
				self.assertEqual(_schritt(stand, "abnahme")["status"], "erledigt")
				self.assertEqual(_schritt(stand, "ausfuehrung")["status"], "erledigt")
				self.assertEqual(stand["aktuell"], "rechnung")

	def test_felder_und_hooks(self):
		for dt, feld in (
			("Task", "cv_abnahmeprotokoll"),
			("Task", "cv_mangel_zeile"),
			("Project", "cv_abgenommen_am"),
			("Project", "cv_abnahme_ergebnis"),
			("Project", "cv_gewaehrleistung_jahre"),
			("Project", "cv_gewaehrleistung_bis"),
			("Sales Invoice", "cv_abschlag"),
			("Sales Invoice", "cv_abnahme_uebersteuert"),
		):
			self.assertTrue(frappe.get_meta(dt).has_field(feld), f"{dt}.{feld}")
		self.assertEqual(frappe.get_meta("Sales Invoice").get_field("cv_abschlag").default, "0")
		events = frappe.get_hooks("doc_events")
		self.assertIn(
			"corevatis_erpnext.abnahme.protokoll_aktualisiert", events[K.ABNAHMEPROTOKOLL]["on_update"]
		)
		self.assertIn("corevatis_erpnext.abnahme.task_aktualisiert", events["Task"]["on_update"])
		self.assertIn("corevatis_erpnext.abnahme.rechnung_pruefen", events["Sales Invoice"]["validate"])
		self.assertIn("corevatis_erpnext.abnahme.projekt_validieren", events["Project"]["validate"])
		self.assertTrue(frappe.db.exists("DocType", EINSTELLUNGEN))

	# --- Security-Review --------------------------------------------------------------------

	def test_task_herkunft_nicht_faelschbar(self):
		doc = self.abschliessen(
			self.protokoll(maengel=[("Riss", add_days(today(), 7)), ("Fleck", add_days(today(), 7))])
		)
		echt = frappe.get_doc("Task", doc.maengel[0].task)
		# neuer Task mit gefälschter Herkunft: Felder verworfen, Zeile bleibt offen
		falsch = frappe.get_doc(
			{
				"doctype": "Task",
				"subject": "fremd",
				"project": self.projekt,
				"cv_abnahmeprotokoll": doc.name,
				"cv_mangel_zeile": doc.maengel[1].name,
			}
		)
		_als(MEISTER, falsch.insert)
		self.assertFalse(falsch.cv_abnahmeprotokoll or falsch.cv_mangel_zeile)
		falsch.status = "Completed"
		_als(MEISTER, falsch.save)
		doc.reload()
		self.assertEqual(doc.maengel[1].status, K.MANGEL_OFFEN)
		# echter Task: Zeile umhängen wird verworfen
		echt.cv_mangel_zeile = doc.maengel[1].name
		_als(MEISTER, echt.save)
		self.assertEqual(frappe.db.get_value("Task", echt.name, "cv_mangel_zeile"), doc.maengel[0].name)
		# an der Prüfung vorbei gesetzte Herkunft (DB) zählt nicht: Zeile verweist auf einen anderen Task
		frappe.db.set_value(
			"Task",
			falsch.name,
			{"cv_abnahmeprotokoll": doc.name, "cv_mangel_zeile": doc.maengel[1].name, "status": "Open"},
		)
		falsch.reload()
		falsch.status = "Completed"
		_als(MEISTER, falsch.save)
		doc.reload()
		self.assertEqual(doc.maengel[1].status, K.MANGEL_OFFEN)

	def test_behoben_erledigt_nur_eigenen_task(self):
		doc = self.abschliessen(self.protokoll(maengel=[("Riss", add_days(today(), 7))]))
		fremd = frappe.get_doc(
			{"doctype": "Task", "subject": "fremder Task", "project": self.projekt}
		).insert()
		echt = doc.maengel[0].task
		# Zeile zeigt (an der Prüfung vorbei) auf einen fremden Task: der fremde bleibt offen
		frappe.db.set_value(K.ABNAHME_MANGEL, doc.maengel[0].name, "task", fremd.name)
		doc.reload()
		doc.maengel[0].status = K.MANGEL_BEHOBEN
		_als(MEISTER, doc.save)
		self.assertEqual(frappe.db.get_value("Task", fremd.name, "status"), "Open")
		self.assertEqual(frappe.db.get_value("Task", echt, "status"), "Completed")

	def test_abschluss_nur_mit_schreibrecht_am_projekt(self):
		doc = self.protokoll(maengel=[("Riss", add_days(today(), 7))])
		with patch.object(abnahme, "darf_projekt_schreiben", return_value=False):
			with self.assertRaises(frappe.PermissionError):
				self.abschliessen(doc)
		doc.reload()
		self.assertEqual(doc.status, K.STATUS_ENTWURF)
		self.assertEqual(self.tasks(doc), [])
		self.assertFalse(frappe.db.get_value("Project", self.projekt, "cv_abgenommen_am"))
