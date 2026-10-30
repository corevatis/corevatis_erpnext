"""Durchstich (Rückmeldung 30.10.): Kommt aus der App ein neues Angebot, bekommt das Büro eine Aufgabe."""

import frappe
from frappe.tests import IntegrationTestCase

from corevatis_erpnext.angebot import mobil
from corevatis_erpnext.tests import _angebot as td
from corevatis_erpnext.tests._helpers import make_user

BUERO = "test-buero-hinweis@example.com"


class TestAngebotBueroHinweis(IntegrationTestCase):
	def setUp(self):
		td.ensure_felder_41()
		td.ensure_artikel()
		make_user(BUERO, "Büro Hinweis", ["Büro"])
		self.lead = td.lead_mit_angebot_status()
		self.angebot = td.angebot(self.lead.name, zusatz=False)

	def tearDown(self):
		frappe.db.delete("ToDo", {"reference_type": "Quotation", "reference_name": self.angebot.name})

	def test_aufgabe_fuer_das_buero_genau_einmal(self):
		todos = mobil.buero_informieren(self.angebot.name)
		self.assertEqual(len(todos), 1)
		todo = frappe.get_doc("ToDo", todos[0])
		self.assertIn("prüfen und freigeben", todo.description)
		self.assertEqual(todo.reference_name, self.angebot.name)
		self.assertTrue(frappe.db.exists("Has Role", {"parent": todo.allocated_to, "role": "Büro"}))
		# zweiter Aufruf legt keine zweite Aufgabe an
		self.assertEqual(mobil.buero_informieren(self.angebot.name), [])
