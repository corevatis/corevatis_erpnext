"""Installationstest des ERPNext-Adapters: installiert neben ERPNext v16,
Hooks geladen, Modul angelegt, Übersetzung lesbar."""

import frappe
from frappe.tests import IntegrationTestCase

import corevatis_erpnext


class TestInstallation(IntegrationTestCase):
	def test_apps_installed(self):
		apps = frappe.get_installed_apps()
		self.assertIn("erpnext", apps)
		self.assertIn("corevatis_erpnext", apps)
		self.assertLess(apps.index("erpnext"), apps.index("corevatis_erpnext"))

	def test_erpnext_is_v16(self):
		self.assertEqual(frappe.get_attr("erpnext.__version__").split(".")[0], "16")

	def test_hooks(self):
		hooks = frappe.get_hooks(app_name="corevatis_erpnext")
		self.assertEqual(hooks.get("app_name"), ["corevatis_erpnext"])
		self.assertEqual(hooks.get("required_apps"), ["erpnext"])
		self.assertEqual(hooks.get("after_install"), ["corevatis_erpnext.setup.install.after_install"])
		frappe.get_attr(hooks["after_install"][0])

	def test_module_def(self):
		self.assertEqual(
			frappe.db.get_value("Module Def", "Corevatis ERPNext", "app_name"), "corevatis_erpnext"
		)

	def test_version_is_semver(self):
		parts = corevatis_erpnext.__version__.split(".")
		self.assertEqual(len(parts), 3)
		self.assertTrue(all(part.isdigit() for part in parts))

	def test_german_translations(self):
		from frappe.translate import get_translations_from_csv

		translations = get_translations_from_csv("de", "corevatis_erpnext")
		self.assertEqual(translations.get("ERPNext adapter for Corevatis"), "ERPNext-Adapter für Corevatis")
