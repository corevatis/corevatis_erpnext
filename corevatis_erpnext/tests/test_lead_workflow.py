"""Aufgabe 1.4: Workflow „Lead Anfrage“, Übergänge je Rolle, Verlustgrund, Kanban."""

import frappe
from frappe.model.workflow import WorkflowPermissionError, WorkflowTransitionError, apply_workflow
from frappe.tests import IntegrationTestCase

from corevatis_erpnext.anfrage import ensure_anfrage_setup
from corevatis_erpnext.anfrage.custom_fields import ANFRAGE_STATUS, VERLUSTGRUENDE
from corevatis_erpnext.anfrage.workflow import (
	ACTION_ANGEBOT,
	ACTION_BESICHTIGT,
	ACTION_GEWONNEN,
	ACTION_TERMIN,
	ACTION_VERLOREN,
	ACTION_WIEDER_OEFFNEN,
	STATE_STYLES,
	WORKFLOW_NAME,
)

BUERO_USER = "cv-test-buero@example.com"
MEISTER_USER = "cv-test-meister@example.com"


def _user(email: str, role: str) -> str:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": role,
				"send_welcome_email": 0,
				# Sales User: Standardrecht Lead lesen/schreiben (ERPNext), Fachrolle steuert
				# die Workflow-Übergänge
				"roles": [{"role": role}, {"role": "Sales User"}],
			}
		).insert(ignore_permissions=True)
	return email


class TestLeadWorkflow(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_user(BUERO_USER, "Büro")
		_user(MEISTER_USER, "Meister")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _lead(self, **values):
		return frappe.get_doc(
			{"doctype": "Lead", "first_name": "Workflow", "last_name": "Test", **values}
		).insert(ignore_permissions=True)

	def _apply(self, user: str, lead: str, action: str):
		frappe.set_user(user)
		try:
			return apply_workflow(frappe.get_doc("Lead", lead), action)
		finally:
			frappe.set_user("Administrator")

	def test_workflow_definition(self):
		wf = frappe.get_doc("Workflow", WORKFLOW_NAME)
		self.assertTrue(wf.is_active)
		self.assertEqual(wf.document_type, "Lead")
		self.assertEqual(wf.workflow_state_field, "cv_status")
		self.assertFalse(wf.override_status)
		self.assertEqual(list(dict.fromkeys(s.state for s in wf.states)), list(ANFRAGE_STATUS))
		for state, style in STATE_STYLES.items():
			self.assertEqual(frappe.db.get_value("Workflow State", state, "style"), style)
		field = frappe.get_meta("Lead").get_field("cv_status")
		self.assertEqual(field.fieldtype, "Select")
		self.assertEqual(field.options.split("\n"), list(ANFRAGE_STATUS))
		reason = frappe.get_meta("Lead").get_field("cv_verlustgrund")
		self.assertEqual([o for o in reason.options.split("\n") if o], list(VERLUSTGRUENDE))

	def test_new_lead_starts_neu(self):
		self.assertEqual(self._lead().cv_status, "Neu")
		# Web Form / API: ERPNext-Status bleibt unabhängig
		self.assertEqual(self._lead(cv_quelle="Web Form").status, "Lead")

	def test_happy_path_by_role(self):
		lead = self._lead().name
		self.assertEqual(self._apply(BUERO_USER, lead, ACTION_TERMIN).cv_status, "Termin")
		self.assertEqual(self._apply(MEISTER_USER, lead, ACTION_BESICHTIGT).cv_status, "Besichtigt")
		self.assertEqual(self._apply(MEISTER_USER, lead, ACTION_ANGEBOT).cv_status, "Angebot")
		self.assertEqual(self._apply(BUERO_USER, lead, ACTION_GEWONNEN).cv_status, "Gewonnen")

	def test_forbidden_transitions(self):
		lead = self._lead().name
		self._apply(MEISTER_USER, lead, ACTION_TERMIN)
		# Besichtigung schließt nur der Meister ab
		with self.assertRaises(WorkflowTransitionError):
			self._apply(BUERO_USER, lead, ACTION_BESICHTIGT)
		self._apply(MEISTER_USER, lead, ACTION_BESICHTIGT)
		self._apply(BUERO_USER, lead, ACTION_ANGEBOT)
		# Gewonnen setzt nur das Büro
		with self.assertRaises(WorkflowTransitionError):
			self._apply(MEISTER_USER, lead, ACTION_GEWONNEN)
		# Aktion passt nicht zum Zustand
		with self.assertRaises(WorkflowTransitionError):
			self._apply(BUERO_USER, lead, ACTION_TERMIN)

	def test_skipping_states_by_save_is_blocked(self):
		lead = self._lead()
		frappe.set_user(BUERO_USER)
		lead = frappe.get_doc("Lead", lead.name)
		lead.cv_status = "Gewonnen"
		with self.assertRaises(WorkflowPermissionError):
			lead.save()

	def test_verlustgrund_required(self):
		lead = self._lead().name
		with self.assertRaises(frappe.MandatoryError):
			self._apply(MEISTER_USER, lead, ACTION_VERLOREN)
		self.assertEqual(frappe.db.get_value("Lead", lead, "cv_status"), "Neu")

		frappe.db.set_value("Lead", lead, "cv_verlustgrund", "Preis")
		self.assertEqual(self._apply(MEISTER_USER, lead, ACTION_VERLOREN).cv_status, "Verloren")

		# im Zustand Verloren lässt sich der Grund nicht entfernen
		doc = frappe.get_doc("Lead", lead)
		doc.cv_verlustgrund = None
		with self.assertRaises(frappe.MandatoryError):
			doc.save(ignore_permissions=True)

	def test_reopen_only_buero(self):
		lead = self._lead(cv_verlustgrund="keine Rückmeldung").name
		self._apply(BUERO_USER, lead, ACTION_VERLOREN)
		with self.assertRaises(WorkflowTransitionError):
			self._apply(MEISTER_USER, lead, ACTION_WIEDER_OEFFNEN)
		self.assertEqual(self._apply(BUERO_USER, lead, ACTION_WIEDER_OEFFNEN).cv_status, "Neu")

	def test_idempotent(self):
		before = frappe.db.get_value("Workflow", WORKFLOW_NAME, "modified")
		ensure_anfrage_setup()
		self.assertEqual(frappe.db.get_value("Workflow", WORKFLOW_NAME, "modified"), before)
		self.assertEqual(frappe.db.count("Workflow", {"document_type": "Lead", "is_active": 1}), 1)

	def test_kanban_board(self):
		self.assertTrue(frappe.db.exists("Kanban Board", "Anfragen"))
		board = frappe.get_doc("Kanban Board", "Anfragen")
		self.assertEqual(board.reference_doctype, "Lead")
		self.assertEqual(board.field_name, "cv_status")
		self.assertEqual([c.column_name for c in board.columns], list(ANFRAGE_STATUS))
		ws = frappe.get_doc("Workspace", "Anfragen")
		self.assertTrue(any(s.doc_view == "Kanban" and s.kanban_board == "Anfragen" for s in ws.shortcuts))
