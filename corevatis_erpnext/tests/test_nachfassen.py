"""Aufgabe 4.5: Nachfass-Automatik - ToDo nach X Tagen (Zeitreise über den Stichtag), Zyklen,
Idempotenz, Follow-up-Mail in der Email Queue, Button „Nachfassen“, Rechte."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from corevatis_erpnext.angebot import annahme, nachfassen
from corevatis_erpnext.tests import _angebot as td
from corevatis_erpnext.tests._helpers import make_user

BUERO = "cv-test-buero@example.com"
BUERO_NF = "cv-test-buero-nachfassen@example.com"
MONTEUR = "cv-test-monteur-annahme@example.com"

STANDARD = {
	"nachfass_aktiv": 1,
	"nachfass_tage": 7,
	"nachfass_zyklen": 2,
	"nachfass_abgelaufene": 0,
	"nachfass_zuweisung": "Rolle Büro",
	"nachfass_benutzer": None,
	"nachfass_mail_aktiv": 0,
	"nachfass_email_template": "Angebot Nachfassen",
}


def _einstellen(**werte) -> None:
	for key, value in {**STANDARD, **werte}.items():
		frappe.db.set_single_value(nachfassen.EINSTELLUNGEN, key, value)


class TestNachfassen(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(BUERO, "Bea", ["Büro", "Sales User"], "Büro")
		make_user(BUERO_NF, "Nora", ["Büro", "Sales User"], "Nachfassen")
		make_user(MONTEUR, "Moritz", ["Monteur"], "Monteur")
		td.ensure_felder_41()
		td.ensure_artikel()

	def setUp(self):
		frappe.set_user("Administrator")
		# CI-Bench ohne gebaute Assets (--skip-assets): E-Mail-CSS für frappe.sendmail fehlt
		css = patch("frappe.utils.jinja_globals.bundled_asset", return_value="/assets/fehlt/email.css")
		css.start()
		self.addCleanup(css.stop)
		_einstellen()
		self.lead = td.lead_mit_angebot_status()
		self.angebot = self._angebot(tage_alt=10)

	def tearDown(self):
		frappe.set_user("Administrator")
		_einstellen()

	def _angebot(self, tage_alt: int, owner: str = BUERO_NF):
		doc = td.angebot(self.lead.name, zusatz=False, tage_alt=tage_alt)
		frappe.db.set_value("Quotation", doc.name, "owner", owner, update_modified=False)
		return frappe.get_doc("Quotation", doc.name)

	def _todos(self, name=None, status=None):
		filters = {
			"reference_type": "Quotation",
			"reference_name": name or self.angebot.name,
			"cv_nachfass_zyklus": [">", 0],
		}
		if status:
			filters["status"] = status
		return frappe.get_all(
			"ToDo",
			filters=filters,
			fields=["name", "status", "allocated_to", "date", "description", "cv_nachfass_zyklus"],
		)

	def _erledigen(self):
		for t in self._todos(status="Open"):
			todo = frappe.get_doc("ToDo", t.name)
			todo.status = "Closed"
			todo.save(ignore_permissions=True)

	def _angelegt(self, ergebnis, name=None) -> list:
		return [a for a in ergebnis["angelegt"] if a[0] == (name or self.angebot.name)]

	def test_hook_und_einstellungen(self):
		self.assertIn(
			"corevatis_erpnext.angebot.nachfassen.taeglich", frappe.get_hooks("scheduler_events")["daily"]
		)
		self.assertTrue(frappe.db.exists("DocType", nachfassen.EINSTELLUNGEN))
		self.assertTrue(frappe.get_meta(nachfassen.EINSTELLUNGEN).issingle)
		self.assertTrue(frappe.db.exists("Email Template", nachfassen.EMAIL_TEMPLATE))
		self.assertEqual(
			frappe.db.get_value("Email Template", nachfassen.EMAIL_TEMPLATE, "reference_doctype"), "Quotation"
		)
		e = nachfassen.einstellungen()
		self.assertEqual((e.nachfass_tage, e.nachfass_zyklen, e.nachfass_mail_aktiv), (7, 2, 0))

	def test_todo_erscheint(self):
		ergebnis = nachfassen.nachfassen_pruefen()
		self.assertEqual(len(self._angelegt(ergebnis)), 1)
		todos = self._todos()
		self.assertEqual(len(todos), 1)
		todo = todos[0]
		self.assertEqual((todo.status, todo.cv_nachfass_zyklus), ("Open", 1))
		self.assertEqual(getdate(todo.date), getdate(today()))
		# Ersteller hat die Rolle Büro -> bekommt die Aufgabe
		self.assertEqual(todo.allocated_to, BUERO_NF)
		self.assertIn(f"Angebot {self.angebot.name} nachfassen", todo.description)
		# Zuweisung sichtbar am Angebot (_assign) wie bei „Zuweisen“
		self.assertIn(BUERO_NF, frappe.db.get_value("Quotation", self.angebot.name, "_assign") or "")

	def test_kein_doppeltes_todo(self):
		nachfassen.nachfassen_pruefen()
		zweiter = nachfassen.nachfassen_pruefen()
		self.assertEqual(self._angelegt(zweiter), [])
		self.assertEqual(len(self._todos()), 1)

	def test_zeitreise_zyklen(self):
		nachfassen.nachfassen_pruefen()
		self._erledigen()
		# erledigt heute: erst nach weiteren 7 Tagen erneut
		self.assertEqual(self._angelegt(nachfassen.nachfassen_pruefen()), [])
		self.assertEqual(self._angelegt(nachfassen.nachfassen_pruefen(add_days(today(), 6))), [])
		zweiter = self._angelegt(nachfassen.nachfassen_pruefen(add_days(today(), 7)))
		self.assertEqual([z[1] for z in zweiter], [2])
		offen = self._todos(status="Open")
		self.assertEqual([t.cv_nachfass_zyklus for t in offen], [2])
		self.assertIn("2. Nachfassen", offen[0].description)
		self._erledigen()
		# Grenze: höchstens 2 Zyklen
		self.assertEqual(self._angelegt(nachfassen.nachfassen_pruefen(add_days(today(), 60))), [])
		self.assertEqual(len(self._todos()), 2)

	def test_zu_frisch(self):
		frisch = self._angebot(tage_alt=3)
		self.assertEqual(self._angelegt(nachfassen.nachfassen_pruefen(), frisch.name), [])
		self.assertEqual(self._angelegt(nachfassen.nachfassen_pruefen(add_days(today(), 3)), frisch.name), [])
		self.assertEqual(
			len(self._angelegt(nachfassen.nachfassen_pruefen(add_days(today(), 4)), frisch.name)), 1
		)

	def test_nicht_bei_verloren_oder_auftrag(self):
		verloren = self._angebot(tage_alt=10)
		annahme.angebot_ablehnen(verloren.name, "Preis")
		mit_auftrag = self._angebot(tage_alt=10)
		annahme.angebot_annehmen(mit_auftrag.name, einreichen=0)  # Entwurf genügt
		ergebnis = nachfassen.nachfassen_pruefen()
		self.assertEqual(self._angelegt(ergebnis, verloren.name), [])
		self.assertEqual(self._angelegt(ergebnis, mit_auftrag.name), [])

	def test_abgelaufen_nur_mit_einstellung(self):
		frappe.db.set_value(
			"Quotation", self.angebot.name, "valid_till", add_days(today(), -1), update_modified=False
		)
		self.assertEqual(self._angelegt(nachfassen.nachfassen_pruefen()), [])
		_einstellen(nachfass_abgelaufene=1)
		self.assertEqual(len(self._angelegt(nachfassen.nachfassen_pruefen())), 1)

	def test_inaktiv(self):
		_einstellen(nachfass_aktiv=0)
		ergebnis = nachfassen.nachfassen_pruefen()
		self.assertFalse(ergebnis["aktiv"])
		self.assertEqual(self._todos(), [])

	def test_zuweisung_fester_benutzer(self):
		_einstellen(nachfass_zuweisung="Benutzer", nachfass_benutzer=BUERO)
		nachfassen.nachfassen_pruefen()
		self.assertEqual([t.allocated_to for t in self._todos()], [BUERO])

	def test_zuweisung_alle_buero_wenn_ersteller_nicht_buero(self):
		frappe.db.set_value("Quotation", self.angebot.name, "owner", "Administrator", update_modified=False)
		nachfassen.nachfassen_pruefen()
		erwartet = set(
			nachfassen._aktive_systembenutzer(
				frappe.get_all("Has Role", filters={"role": "Büro", "parenttype": "User"}, pluck="parent")
			)
		)
		self.assertIn(BUERO_NF, erwartet)
		self.assertEqual({t.allocated_to for t in self._todos()}, erwartet)

	def test_follow_up_mail_in_email_queue(self):
		_einstellen(nachfass_mail_aktiv=1)
		frappe.db.set_value(
			"Quotation",
			self.angebot.name,
			"contact_email",
			"kunde.nachfassen@example.com",
			update_modified=False,
		)
		ergebnis = nachfassen.nachfassen_pruefen()
		mails = [m for m in ergebnis["mails"] if m[0] == self.angebot.name]
		self.assertEqual(len(mails), 1)
		queue = frappe.get_doc("Email Queue", mails[0][1])
		self.assertEqual(queue.status, "Not Sent")
		self.assertEqual((queue.reference_doctype, queue.reference_name), ("Quotation", self.angebot.name))
		self.assertEqual([r.recipient for r in queue.recipients], ["kunde.nachfassen@example.com"])
		inhalt = nachfassen.mail_rendern(frappe.get_doc("Quotation", self.angebot.name))
		datum = getdate(self.angebot.transaction_date).strftime("%d.%m.%Y")
		self.assertEqual(inhalt["betreff"], f"Ihr Angebot {self.angebot.name} vom {datum}")
		self.assertIn(self.angebot.name, queue.message)

	def test_mail_escaped(self):
		boese = "<b>Böse</b> {{ 7*7 }}"
		frappe.db.set_value(
			"Quotation",
			self.angebot.name,
			{"customer_name": boese, "contact_display": boese},
			update_modified=False,
		)
		inhalt = nachfassen.mail_rendern(frappe.get_doc("Quotation", self.angebot.name))
		self.assertNotIn("<b>Böse</b>", inhalt["html"])
		self.assertNotIn("49", inhalt["text"])
		self.assertIn("{{ 7*7 }}", inhalt["text"])
		self.assertIn(self.angebot.name, inhalt["betreff"])

	def test_button_nachfassen(self):
		frisch = self._angebot(tage_alt=0)
		frappe.set_user(BUERO)
		try:
			r = nachfassen.nachfassen(frisch.name, mail=0)
			self.assertEqual(r["zyklus"], 1)
			self.assertEqual(len(r["todos"]), 1)
			self.assertTrue(r["text"].startswith("Guten Tag"), r["text"])
			self.assertIsNone(r["email_queue"])
			# zweiter Klick: keine zweite offene Aufgabe
			r2 = nachfassen.nachfassen(frisch.name, mail=0)
			self.assertEqual(r2["todos"], r["todos"])
			self.assertEqual(len(r2["warnungen"]), 1)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(len(self._todos(frisch.name)), 1)
		# die Automatik legt neben der offenen Aufgabe nichts an
		self.assertEqual(
			self._angelegt(nachfassen.nachfassen_pruefen(add_days(today(), 30)), frisch.name), []
		)

	def test_button_rechte(self):
		frappe.set_user(MONTEUR)
		try:
			with self.assertRaises(frappe.PermissionError):
				nachfassen.nachfassen(self.angebot.name)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(self._todos(), [])
