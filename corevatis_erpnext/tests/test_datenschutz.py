"""Platzhalterseite ``/datenschutz`` (Link im Web Form 1.2, Pilot ohne Datenschutzerklärung)."""

import frappe
from frappe.tests import IntegrationTestCase

from corevatis_erpnext.webform import datenschutz


class TestDatenschutzPlatzhalter(IntegrationTestCase):
	def _seite(self):
		return frappe.db.get_value(
			"Web Page",
			{"route": datenschutz.ROUTE},
			["name", "title", "published", "content_type", "main_section_html", "modified"],
			as_dict=True,
		)

	def test_seite_nach_installation(self):
		seite = self._seite()
		self.assertTrue(seite, "after_install legt /datenschutz an")
		self.assertEqual(seite.title, datenschutz.TITEL)
		self.assertEqual(seite.published, 1)
		self.assertEqual(seite.content_type, "HTML")
		self.assertIn(datenschutz.HINWEIS, seite.main_section_html)

	def test_idempotent(self):
		vorher = self._seite()
		self.assertIsNone(datenschutz.ensure_datenschutz_seite())
		self.assertEqual(frappe.db.count("Web Page", {"route": datenschutz.ROUTE}), 1)
		self.assertEqual(self._seite().modified, vorher.modified)

	def test_verlegte_seite_wird_nicht_neu_angelegt(self):
		vorher = self._seite()
		frappe.db.set_value("Web Page", vorher.name, "route", "datenschutz-alt", update_modified=False)
		try:
			self.assertIsNone(datenschutz.ensure_datenschutz_seite())
			self.assertFalse(frappe.db.exists("Web Page", {"route": datenschutz.ROUTE}))
		finally:
			frappe.db.set_value("Web Page", vorher.name, "route", datenschutz.ROUTE, update_modified=False)

	def test_eigene_url_und_freie_route(self):
		vorher = self._seite()
		frappe.delete_doc("Web Page", vorher.name, ignore_permissions=True, force=True)
		try:
			# eigene Datenschutz-URL (z. B. WordPress): keine Seite anlegen
			frappe.local.conf["cv_webform_datenschutz_url"] = "https://www.example.com/datenschutz"
			self.assertIsNone(datenschutz.ensure_datenschutz_seite())
			self.assertFalse(frappe.db.exists("Web Page", {"route": datenschutz.ROUTE}))
		finally:
			frappe.local.conf.pop("cv_webform_datenschutz_url", None)
		# Standard-URL und freie Route: (wieder) anlegen
		neu = datenschutz.ensure_datenschutz_seite()
		self.assertTrue(neu)
		self.assertEqual(frappe.db.get_value("Web Page", neu, "route"), datenschutz.ROUTE)

	def test_im_desk_geaenderte_seite_bleibt(self):
		vorher = self._seite()
		frappe.db.set_value(
			"Web Page", vorher.name, "main_section_html", "<p>Echte Erklärung</p>", update_modified=False
		)
		try:
			self.assertIsNone(datenschutz.ensure_datenschutz_seite())
			self.assertEqual(self._seite().main_section_html, "<p>Echte Erklärung</p>")
		finally:
			frappe.db.set_value(
				"Web Page", vorher.name, "main_section_html", vorher.main_section_html, update_modified=False
			)
