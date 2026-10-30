"""Durchstich D1 bis D3 und A2: eine Oberfläche „Betrieb“, Mitarbeiter automatisch."""

import frappe
from frappe.tests import IntegrationTestCase

from corevatis_erpnext.prozess import desk

BUERO = "test-desk-buero@example.com"
ADMIN2 = "test-desk-sysman@example.com"
MONTEUR = "test-desk-monteur@example.com"


def _user(email: str, rollen: list[str]) -> "frappe.model.document.Document":
	if frappe.db.exists("User", email):
		frappe.delete_doc("User", email, force=True, ignore_permissions=True)
	doc = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": email.split("@")[0],
			"send_welcome_email": 0,
			"user_type": "System User",
			"roles": [{"role": r} for r in rollen],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


class TestDesk(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		for email in (BUERO, ADMIN2, MONTEUR):
			for emp in frappe.get_all("Employee", filters={"user_id": email}, pluck="name"):
				frappe.delete_doc("Employee", emp, force=True, ignore_permissions=True)
			if frappe.db.exists("User", email):
				frappe.delete_doc("User", email, force=True, ignore_permissions=True)

	def test_monteur_bekommt_mitarbeiter(self):
		_user(MONTEUR, ["Monteur"])
		emp = frappe.get_all(
			"Employee", filters={"user_id": MONTEUR}, fields=["name", "status", "employee_name"]
		)
		self.assertEqual(len(emp), 1)
		self.assertEqual(emp[0].status, "Active")
		# erneutes Speichern legt keinen zweiten an
		frappe.get_doc("User", MONTEUR).save(ignore_permissions=True)
		self.assertEqual(len(frappe.get_all("Employee", filters={"user_id": MONTEUR})), 1)
		# Büro bekommt keinen Mitarbeiter
		_user(BUERO, ["Büro"])
		self.assertEqual(frappe.get_all("Employee", filters={"user_id": BUERO}), [])

	def test_fachrolle_sperrt_alle_fremden_module(self):
		doc = _user(BUERO, ["Büro"])
		gesperrt = {r.module for r in doc.block_modules}
		alle = set(frappe.get_all("Module Def", pluck="name"))
		self.assertEqual(gesperrt, alle - set(desk.SICHTBARE_MODULE))
		self.assertNotIn("Corevatis ERPNext", gesperrt)
		# Belege bleiben lesbar: die Sperre betrifft nur das Dock
		frappe.set_user(BUERO)
		self.assertTrue(frappe.has_permission("Lead", "read"))
		frappe.set_user("Administrator")
		# zweites Speichern ändert nichts (idempotent)
		doc.reload()
		anzahl = len(doc.block_modules)
		doc.save(ignore_permissions=True)
		self.assertEqual(len(doc.reload().block_modules), anzahl)

	def test_system_manager_bleibt_unberuehrt(self):
		doc = _user(ADMIN2, ["System Manager", "Büro"])
		self.assertEqual(doc.block_modules, [])

	def test_sidebar_betrieb_listet_die_belege(self):
		self.assertTrue(frappe.db.exists("Sidebar", "Betrieb"))
		sidebar = frappe.get_doc("Sidebar", "Betrieb")
		links = {(i.link_type, i.link_to) for i in sidebar.items if i.type == "Link"}
		for doctype in (
			"Lead",
			"Event",
			"Quotation",
			"Sales Order",
			"Project",
			"Task",
			"Sales Invoice",
			"Employee",
		):
			self.assertIn(("DocType", doctype), links, doctype)
		for ws in ("Vorgänge", "Anfragen", "Meine Baustellen"):
			self.assertIn(("Workspace", ws), links, ws)

	def test_home_pages_je_rolle(self):
		desk.ensure_home_pages()
		self.assertEqual(frappe.db.get_value("Role", "Büro", "home_page"), "/desk/betrieb/vorgänge")
		self.assertEqual(
			frappe.db.get_value("Role", "Monteur", "home_page"), "/desk/betrieb/meine-baustellen"
		)

	def test_kachel_erpnext_nur_fuer_system_manager(self):
		_user(BUERO, ["Büro"])
		frappe.set_user(BUERO)
		namen = [a["name"] for a in desk.get_apps()]
		self.assertNotIn("erpnext", namen)
		self.assertIn("corevatis_erpnext", namen)
		frappe.set_user("Administrator")
		self.assertIn("erpnext", [a["name"] for a in desk.get_apps()])
