"""Aufgaben am Vorgang (Phase 3 P3.4, Handwerker#96): Desk-Anbindung im Adapter.

Die Aufgaben sind ToDos mit Kern-Feldern (App corevatis). Die CI des Adapters installiert den Kern
nicht: die Tests prüfen Hooks und Seitenleiste, die Leiste ohne Kern und die Zählung der Pille mit
nachgestellten Kern-Feldern am ToDo (ohne Kern-Regeln, ToDos direkt angelegt).
"""

from __future__ import annotations

import json
import unittest

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from corevatis_erpnext import aufgaben
from corevatis_erpnext.prozess.kette import status
from corevatis_erpnext.tests._helpers import make_lead

JS = "public/js/aufgaben.js"
KERN_APP = "corevatis"
# dieselben Feldnamen wie im Kern (corevatis.aufgaben.felder), nur was der Adapter liest
FELDER = {
	"ToDo": [
		{
			"fieldname": "cv_quelle",
			"fieldtype": "Select",
			"options": "\nApp\nDesk\nMeldung",
			"label": "Source",
		},
		{"fieldname": "cv_vorgang_doctype", "fieldtype": "Link", "options": "DocType", "label": "Case Type"},
		{
			"fieldname": "cv_vorgang",
			"fieldtype": "Dynamic Link",
			"options": "cv_vorgang_doctype",
			"label": "Case",
		},
	]
}


class TestAufgabenDesk(IntegrationTestCase):
	def test_hooks(self):
		js = frappe.get_hooks("doctype_js", app_name="corevatis_erpnext")
		for doctype in ("Lead", "Sales Order", "Project", "ToDo"):
			self.assertIn(JS, js.get(doctype) or [], doctype)
		liste = frappe.get_hooks("doctype_list_js", app_name="corevatis_erpnext")
		self.assertIn(JS, liste.get("ToDo") or [])

	def test_seitenleiste(self):
		sidebar = frappe.get_doc("Sidebar", "Betrieb")
		eintrag = next((i for i in sidebar.items if i.type == "Link" and i.label == "Aufgaben"), None)
		self.assertIsNotNone(eintrag)
		self.assertEqual((eintrag.link_type, eintrag.link_to), ("DocType", "ToDo"))
		filter_ = json.loads(eintrag.filters)
		self.assertIn(["ToDo", "cv_quelle", "is", "set"], filter_)
		self.assertIn(["ToDo", "status", "=", "Open"], filter_)
		# direkt nach „Nachrichten“
		labels = [i.label for i in sidebar.items]
		self.assertEqual(labels.index("Aufgaben"), labels.index("Nachrichten") + 1)


class TestAufgabenLeiste(IntegrationTestCase):
	"""Pille „Aufgaben: n offen“ in der Prozessleiste."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if KERN_APP in frappe.get_installed_apps():
			raise unittest.SkipTest("corevatis installiert - die Tests stellen die Kern-Felder nach")
		cls.lead = make_lead(last_name=f"Aufgaben {frappe.generate_hash(length=6)}")
		cls.anderer = make_lead(last_name=f"Aufgaben {frappe.generate_hash(length=6)}")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for lead in (cls.lead, cls.anderer):
			frappe.delete_doc_if_exists("Lead", lead.name, force=True)
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_cache(doctype="ToDo")

	def _felder(self):
		create_custom_fields(FELDER, ignore_validate=True)
		frappe.clear_cache(doctype="ToDo")
		self.addCleanup(self._felder_weg)

	def _felder_weg(self):
		for f in FELDER["ToDo"]:
			name = frappe.db.get_value("Custom Field", {"dt": "ToDo", "fieldname": f["fieldname"]})
			if name:
				frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)
		frappe.db.commit()
		frappe.clear_cache(doctype="ToDo")

	def _todo(self, vorgang: str, status_: str = "Open", datum: str | None = None, quelle: str = "Desk"):
		return frappe.get_doc(
			{
				"doctype": "ToDo",
				"description": "Dichtschlämme nachbestellen",
				"status": status_,
				"date": datum,
				"allocated_to": "Administrator",
				"cv_quelle": quelle,
				"cv_vorgang_doctype": "Lead",
				"cv_vorgang": vorgang,
			}
		).insert(ignore_permissions=True)

	def test_ohne_kern(self):
		self.assertFalse(aufgaben.kern_da())
		self.assertIsNone(aufgaben.stand([("Lead", self.lead.name)]))
		stand = status("Lead", self.lead.name)
		self.assertIn("aufgaben", stand)
		self.assertIsNone(stand["aufgaben"])

	def test_zaehlt_offene_und_ueberfaellige(self):
		self._felder()
		self.assertTrue(aufgaben.kern_da())
		self.assertIsNone(aufgaben.stand([("Lead", self.lead.name)]))
		self._todo(self.lead.name, datum=add_days(today(), -2))
		self._todo(self.lead.name, datum=add_days(today(), 3))
		self._todo(self.lead.name, status_="Closed")
		self._todo(self.lead.name, status_="Cancelled")
		self._todo(self.anderer.name)
		# ein gewöhnliches ToDo (ohne Quelle) ist keine Aufgabe
		frappe.get_doc(
			{
				"doctype": "ToDo",
				"description": "Rückruf",
				"allocated_to": "Administrator",
				"cv_vorgang_doctype": "Lead",
				"cv_vorgang": self.lead.name,
			}
		).insert(ignore_permissions=True)
		s = aufgaben.stand([("Lead", self.lead.name)])
		self.assertEqual((s["offen"], s["ueberfaellig"], s["gesamt"]), (2, 1, 3))
		self.assertEqual(s["ampel"], "rot")
		self.assertEqual(s["text"], "Aufgaben: 2 offen, 1 überfällig")
		self.assertEqual(s["vorgaenge"], [self.lead.name])
		self.assertEqual(s["anker"], {"doctype": "Lead", "name": self.lead.name})
		# in der Leiste der Anfrage
		self.assertEqual(status("Lead", self.lead.name)["aufgaben"]["offen"], 2)
		# nur fremde Doctypes: nichts
		self.assertIsNone(aufgaben.stand([("Quotation", self.lead.name)]))

	def test_alle_erledigt(self):
		self._felder()
		self._todo(self.lead.name, status_="Closed")
		s = aufgaben.stand([("Lead", self.lead.name)])
		self.assertEqual((s["offen"], s["ampel"], s["text"]), (0, "grün", "Aufgaben: alle erledigt"))
