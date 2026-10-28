"""Review Epic 1, E-1: Email Template der Terminbestätigung ohne Fixture - Desk-Änderungen bleiben."""

import os
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from corevatis_erpnext.crm import email_vorlagen as ev
from corevatis_erpnext.crm import termin
from corevatis_erpnext.tests._helpers import make_user

BUERO = "cv-test-vorlagen@example.com"


class TestEmailVorlagen(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(BUERO, "Vera", ["System Manager"], "Vorlage")

	def tearDown(self):
		frappe.set_user("Administrator")
		# ausgelieferte Fassung für die übrigen Tests wiederherstellen
		ev.ensure_email_templates(force=True)

	def _quelle(self) -> dict:
		return ev.quellen()[0]

	def test_keine_fixture_mehr(self):
		self.assertFalse(frappe.get_hooks("fixtures", app_name="corevatis_erpnext"))
		self.assertFalse(os.path.exists(frappe.get_app_path("corevatis_erpnext", "fixtures")))
		self.assertIn("corevatis_erpnext.setup.install.after_migrate", frappe.get_hooks("after_migrate"))

	def test_vorlage_angelegt(self):
		self.assertEqual(self._quelle()["name"], termin.EMAIL_TEMPLATE)
		template = frappe.get_doc("Email Template", termin.EMAIL_TEMPLATE)
		self.assertEqual(template.reference_doctype, "Lead")
		self.assertEqual(template.subject, self._quelle()["subject"])
		# zweiter Lauf: nichts zu tun
		self.assertEqual(ev.ensure_email_templates()["unchanged"], [termin.EMAIL_TEMPLATE])

	def test_desk_aenderung_ueberlebt_migrate(self):
		ev.ensure_email_templates(force=True)
		frappe.set_user(BUERO)
		template = frappe.get_doc("Email Template", termin.EMAIL_TEMPLATE)
		template.response = template.response.replace("Mit freundlichen Grüßen", "Herzliche Grüße, Ihr Team")
		template.save()
		frappe.set_user("Administrator")

		# after_migrate (wie bei jedem Deploy) - auch wenn sich die Quelle geändert hat
		from corevatis_erpnext.setup.install import after_migrate

		after_migrate()
		quelle = dict(self._quelle(), subject="Neuer Betreff aus der App {{ datum }}")
		with patch.object(ev, "quellen", return_value=[quelle]):
			bericht = ev.ensure_email_templates()
		self.assertEqual(bericht["customized"], [termin.EMAIL_TEMPLATE])
		template.reload()
		self.assertIn("Herzliche Grüße, Ihr Team", template.response)
		self.assertNotEqual(template.subject, quelle["subject"])

		# force holt die ausgelieferte Fassung zurück
		ev.ensure_email_templates(force=True)
		template.reload()
		self.assertNotIn("Herzliche Grüße", template.response)

	def test_quellaenderung_wird_uebernommen(self):
		ev.ensure_email_templates(force=True)
		quelle = dict(self._quelle(), subject="Geänderter Betreff {{ datum }}")
		with patch.object(ev, "quellen", return_value=[quelle]):
			self.assertEqual(ev.ensure_email_templates()["update"], [termin.EMAIL_TEMPLATE])
			self.assertEqual(ev.ensure_email_templates()["unchanged"], [termin.EMAIL_TEMPLATE])
		self.assertEqual(
			frappe.db.get_value("Email Template", termin.EMAIL_TEMPLATE, "subject"), quelle["subject"]
		)

	def test_fehlende_vorlage_wird_angelegt(self):
		frappe.delete_doc("Email Template", termin.EMAIL_TEMPLATE, force=True, ignore_permissions=True)
		self.assertEqual(ev.ensure_email_templates()["create"], [termin.EMAIL_TEMPLATE])
		self.assertTrue(frappe.db.exists("Email Template", termin.EMAIL_TEMPLATE))

	def test_altbestand_aus_der_fixture_zeit(self):
		# ohne gespeicherten Zustand: gleich der Quelle → übernehmen; von Administrator
		# (Fixture-Import) geändert → aktualisieren; von einem Benutzer geändert → behalten
		self.assertEqual(ev.plan("a", "a", None, "Administrator"), "adopt")
		self.assertEqual(ev.plan("a", "b", None, "Administrator"), "update")
		self.assertEqual(ev.plan("a", "b", None, BUERO), "customized")
		self.assertEqual(ev.plan("a", None, None, None), "create")
		self.assertEqual(ev.plan("a", "b", {"source": "a", "site": "c"}, "Administrator"), "customized")
		self.assertEqual(ev.plan("a", "b", {"source": "a", "site": "c"}, BUERO, force=True), "update")
