"""Aufgabe 2.1: ``Lead.cv_objekt`` (Link auf ``Objekt`` aus ``corevatis``).

Die CI-Bench dieses Repos hat ``corevatis`` nicht (privates Repo). Geprüft wird deshalb:

- ohne Doctype ``Objekt`` entsteht kein Feld und nichts bricht (Install/Migrate wie bisher),
- mit einem Stellvertreter-Doctype ``Objekt`` (custom, nur für den Test) entsteht das Feld
  als Link, idempotent, über ``after_app_install("corevatis")``; ``before_app_uninstall``
  entfernt es wieder.

Dass der echte Doctype ``Objekt`` so heißt, prüft die CI von ``corevatis`` gegen den
Quellstand dieses Repos (Vertragstest, wie Review C-3).
"""

import frappe
from frappe.tests import IntegrationTestCase

from corevatis_erpnext.setup.install import after_migrate
from corevatis_erpnext.tests._helpers import make_lead
from corevatis_erpnext.vor_ort import lead_objekt
from corevatis_erpnext.vor_ort.lead_objekt import FIELDNAME, OBJEKT_DOCTYPE


def _feld():
	return frappe.db.get_value(
		"Custom Field",
		{"dt": "Lead", "fieldname": FIELDNAME},
		["name", "fieldtype", "options", "modified"],
		as_dict=True,
	)


class TestLeadObjekt(IntegrationTestCase):
	def setUp(self):
		if "corevatis" in frappe.get_installed_apps():
			self.skipTest("corevatis installiert - der Stellvertreter-Doctype würde kollidieren")
		self.assertFalse(frappe.db.exists("DocType", OBJEKT_DOCTYPE), "Doctype Objekt existiert unerwartet")

	def tearDown(self):
		lead_objekt.remove_lead_objekt_field()
		if frappe.db.exists("DocType", OBJEKT_DOCTYPE):
			frappe.delete_doc("DocType", OBJEKT_DOCTYPE, force=True, ignore_permissions=True)
		frappe.db.commit()
		frappe.clear_cache(doctype="Lead")

	def _stellvertreter(self):
		frappe.get_doc(
			{
				"doctype": "DocType",
				"name": OBJEKT_DOCTYPE,
				"module": "Corevatis ERPNext",
				"custom": 1,
				"autoname": "hash",
				"fields": [{"fieldname": "titel", "fieldtype": "Data", "label": "Titel"}],
				"permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1}],
			}
		).insert(ignore_permissions=True)

	def test_ohne_kern_kein_feld(self):
		self.assertFalse(lead_objekt.ensure_lead_objekt_field())
		after_migrate()
		self.assertIsNone(_feld())
		self.assertIsNone(frappe.get_meta("Lead").get_field(FIELDNAME))

	def test_mit_kern_link_feld(self):
		self._stellvertreter()
		lead_objekt.after_app_install("erpnext")  # andere App: nichts
		self.assertIsNone(_feld())
		lead_objekt.after_app_install("corevatis")
		feld = _feld()
		self.assertEqual((feld.fieldtype, feld.options), ("Link", OBJEKT_DOCTYPE))
		# idempotent: zweiter Lauf (after_migrate) ändert nichts
		after_migrate()
		self.assertEqual(_feld().modified, feld.modified)

		meta = frappe.get_meta("Lead")
		fieldnames = [df.fieldname for df in meta.fields]
		self.assertEqual(fieldnames.index(FIELDNAME), fieldnames.index("cv_objekt_adresse") + 1)
		self.assertTrue(meta.get_field(FIELDNAME).in_standard_filter)

		# Lead mit Objekt speicherbar, Link wird geprüft
		objekt = frappe.get_doc({"doctype": OBJEKT_DOCTYPE, "titel": "Kellerweg 1"}).insert(
			ignore_permissions=True
		)
		lead = make_lead(**{FIELDNAME: objekt.name})
		self.assertEqual(frappe.db.get_value("Lead", lead.name, FIELDNAME), objekt.name)
		with self.assertRaises(frappe.LinkValidationError):
			make_lead(**{FIELDNAME: "gibt-es-nicht"})
		frappe.delete_doc("Lead", lead.name, force=True, ignore_permissions=True)

		lead_objekt.before_app_uninstall("corevatis")
		self.assertIsNone(_feld())

	def test_hooks(self):
		self.assertIn(
			"corevatis_erpnext.vor_ort.lead_objekt.after_app_install", frappe.get_hooks("after_app_install")
		)
		self.assertIn(
			"corevatis_erpnext.vor_ort.lead_objekt.before_app_uninstall",
			frappe.get_hooks("before_app_uninstall"),
		)

	def test_uebersetzung(self):
		from frappe.translate import get_translations_from_csv

		de = get_translations_from_csv("de", "corevatis_erpnext")
		feld = lead_objekt.lead_objekt_field()
		self.assertEqual(de[feld["label"]], "Objekt")
		self.assertIn(feld["description"], de)
