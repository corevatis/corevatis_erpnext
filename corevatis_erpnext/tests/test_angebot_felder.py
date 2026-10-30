"""Aufgabe 4.1: Custom Fields an Quotation und Quotation Item (gemeinsamer Vertrag 4.1/4.3/4.4)."""

import frappe
from frappe.tests import IntegrationTestCase

from corevatis_erpnext.angebot.felder import (
	QUOTATION_FELDER,
	QUOTATION_ITEM_FELDER,
	ensure_angebot_felder,
	quotation_fields,
	quotation_item_fields,
)

ERWARTET = {
	"Quotation": {
		"cv_titel": "Data",
		"cv_aufmass": "Data",
		"cv_objekt": "Data",
		"cv_kalkulationsschema": "Data",
		"cv_vorbemerkung": "Text Editor",
		"cv_garantiehinweis": "Small Text",
		"cv_kalkulation_json": "Code",
		"cv_erzeugt_aus_aufmass": "Check",
	},
	"Quotation Item": {
		"cv_raum": "Data",
		"cv_waende": "Small Text",
		"cv_bauteil": "Data",
		"cv_verfahren": "Data",
		"cv_aufmass_position": "Data",
		"cv_mengen_herkunft": "Small Text",
		"cv_einzelkosten": "Currency",
		"cv_eventualposition": "Check",
		"cv_alternativposition": "Check",
	},
}


class TestAngebotFelder(IntegrationTestCase):
	def test_vertrag_feldnamen(self):
		self.assertEqual(set(QUOTATION_FELDER), set(ERWARTET["Quotation"]))
		self.assertEqual(set(QUOTATION_ITEM_FELDER), set(ERWARTET["Quotation Item"]))
		for doctype, fields in (
			("Quotation", quotation_fields()),
			("Quotation Item", quotation_item_fields()),
		):
			definiert = {f["fieldname"]: f["fieldtype"] for f in fields}
			for fieldname, fieldtype in ERWARTET[doctype].items():
				self.assertEqual(definiert.get(fieldname), fieldtype, f"{doctype}.{fieldname}")

	def test_felder_angelegt_und_idempotent(self):
		ensure_angebot_felder()
		ensure_angebot_felder()
		for doctype, felder in ERWARTET.items():
			meta = frappe.get_meta(doctype)
			for fieldname, fieldtype in felder.items():
				df = meta.get_field(fieldname)
				self.assertIsNotNone(df, f"{doctype}.{fieldname} fehlt")
				self.assertEqual(df.fieldtype, fieldtype)
				self.assertEqual(frappe.db.count("Custom Field", {"dt": doctype, "fieldname": fieldname}), 1)
		meta = frappe.get_meta("Quotation")
		self.assertTrue(meta.get_field("cv_kalkulation_json").read_only)
		self.assertTrue(meta.get_field("cv_kalkulation_json").print_hide)
		self.assertTrue(frappe.get_meta("Quotation Item").get_field("cv_einzelkosten").read_only)

	def test_felder_speicherbar(self):
		"""Werte an einem ungespeicherten Angebot setzen und validieren (Typen passen)."""
		doc = frappe.new_doc("Quotation")
		doc.cv_titel = "Innenabdichtung Musterstraße 1"
		doc.cv_kalkulation_json = '{"ok": true}'
		row = doc.append("items", {"item_code": "x", "qty": 1})
		row.cv_raum = "R1"
		row.cv_einzelkosten = 24.8
		row.cv_eventualposition = 1
		self.assertEqual(doc.get("cv_titel"), "Innenabdichtung Musterstraße 1")
		self.assertEqual(row.get("cv_einzelkosten"), 24.8)
