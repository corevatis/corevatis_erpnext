"""Aufgabe 2.2: Custom Fields am Item (Gewerk, Verfahren, Leistungsregel).

Abnahme „Regel an Item speicherbar“: Die CI-Bench hat ``corevatis`` nicht. Geprüft wird

- ohne Kern: Item mit Gewerk, Verfahren und Regelname speicherbar (Data, keine Prüfung),
- mit Stellvertreter-Doctype ``Leistungsregel`` (custom, nur im Test): bekannte Regel
  speicherbar, unbekannte abgewiesen, ohne Leserecht abgewiesen.

Dass Optionen und Doctype-Name zu ``corevatis`` passen, prüft die CI von ``corevatis``
gegen den Quellstand dieses Repos (``tests/test_erpnext_contract.py``).
"""

import frappe
from frappe.tests import IntegrationTestCase

from corevatis_erpnext.katalog.item_felder import (
	GEWERKE,
	LEISTUNGSREGEL_DOCTYPE,
	VERFAHREN,
	ensure_item_fields,
	item_fields,
)
from corevatis_erpnext.tests._helpers import make_user

ITEM_USER = "cv-test-item@example.com"


def _item_group() -> str:
	name = "CV Test Leistungen"
	if not frappe.db.exists("Item Group", name):
		root = frappe.db.get_value("Item Group", {"is_group": 1}, "name", order_by="lft asc")
		if not root:
			root = (
				frappe.get_doc({"doctype": "Item Group", "item_group_name": "All Item Groups", "is_group": 1})
				.insert(ignore_permissions=True)
				.name
			)
		frappe.get_doc(
			{"doctype": "Item Group", "item_group_name": name, "parent_item_group": root, "is_group": 0}
		).insert(ignore_permissions=True)
	return name


def _uom() -> str:
	if not frappe.db.exists("UOM", "m²"):
		frappe.get_doc({"doctype": "UOM", "uom_name": "m²"}).insert(ignore_permissions=True)
	return "m²"


def make_item(**values):
	data = {
		"doctype": "Item",
		"item_code": "CV-TEST-" + frappe.generate_hash(length=8),
		"item_name": "Innenabdichtung Test",
		"item_group": _item_group(),
		"stock_uom": _uom(),
		"is_stock_item": 0,
	}
	data.update(values)
	return frappe.get_doc(data).insert(ignore_permissions=True)


class TestItemFelder(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		if "corevatis" in frappe.get_installed_apps():
			self.skipTest("corevatis installiert - der Stellvertreter-Doctype würde kollidieren")

	def tearDown(self):
		frappe.set_user("Administrator")
		if frappe.db.exists("DocType", LEISTUNGSREGEL_DOCTYPE):
			frappe.delete_doc("DocType", LEISTUNGSREGEL_DOCTYPE, force=True, ignore_permissions=True)
			frappe.db.commit()

	def _stellvertreter(self):
		frappe.get_doc(
			{
				"doctype": "DocType",
				"name": LEISTUNGSREGEL_DOCTYPE,
				"module": "Corevatis ERPNext",
				"custom": 1,
				"autoname": "field:regel_name",
				"fields": [{"fieldname": "regel_name", "fieldtype": "Data", "label": "Regel", "unique": 1}],
				"permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1}],
			}
		).insert(ignore_permissions=True)
		return frappe.get_doc(
			{"doctype": LEISTUNGSREGEL_DOCTYPE, "regel_name": "Innenabdichtung bis 1,50 m"}
		).insert(ignore_permissions=True)

	def test_felder(self):
		meta = frappe.get_meta("Item")
		self.assertEqual(meta.get_field("cv_gewerk").fieldtype, "Select")
		self.assertEqual(meta.get_field("cv_gewerk").options.split("\n"), ["", *GEWERKE])
		self.assertEqual(meta.get_field("cv_verfahren").options.split("\n"), ["", *VERFAHREN])
		self.assertEqual(meta.get_field("cv_leistungsregel").fieldtype, "Data")
		fieldnames = [df.fieldname for df in meta.fields]
		# nach der Beschreibung, noch im Tab „Details“ (andere Apps hängen ggf. auch hinter description)
		self.assertGreater(fieldnames.index("cv_section_leistung"), fieldnames.index("description"))
		self.assertLess(fieldnames.index("cv_leistungsregel"), fieldnames.index("accounting"))
		for name in ("cv_gewerk", "cv_verfahren", "cv_leistungsregel"):
			self.assertEqual(meta.get_field(name).module, "Corevatis ERPNext")

	def test_idempotent(self):
		filters = {"dt": "Item", "fieldname": ["like", "cv_%"]}
		before = frappe.get_all("Custom Field", filters, ["name", "modified"], order_by="name")
		ensure_item_fields()
		ensure_item_fields()
		self.assertEqual(
			before, frappe.get_all("Custom Field", filters, ["name", "modified"], order_by="name")
		)
		self.assertEqual(len(before), len(item_fields()))

	def test_regel_an_item_speicherbar_ohne_kern(self):
		item = make_item(
			cv_gewerk="Bauwerksabdichtung", cv_verfahren="Innenabdichtung", cv_leistungsregel=" IA-150 "
		)
		item.reload()
		self.assertEqual(
			(item.cv_gewerk, item.cv_verfahren, item.cv_leistungsregel),
			("Bauwerksabdichtung", "Innenabdichtung", "IA-150"),
		)
		with self.assertRaises(frappe.ValidationError):
			make_item(cv_verfahren="Dachdeckung")

	def test_regel_wird_geprueft_mit_kern(self):
		regel = self._stellvertreter()
		item = make_item(cv_verfahren="Innenabdichtung", cv_leistungsregel=regel.name)
		self.assertEqual(frappe.db.get_value("Item", item.name, "cv_leistungsregel"), regel.name)
		with self.assertRaises(frappe.ValidationError):
			make_item(cv_leistungsregel="gibt es nicht")
		# ohne Leserecht auf Leistungsregel: Verknüpfen abgelehnt
		make_user(ITEM_USER, "Ida", ["Item Manager"], "Item")
		frappe.set_user(ITEM_USER)
		with self.assertRaises(frappe.PermissionError):
			make_item(cv_leistungsregel=regel.name)

	def test_uebersetzungen(self):
		from frappe.translate import get_translations_from_csv

		de = get_translations_from_csv("de", "corevatis_erpnext")
		for field in item_fields():
			if field.get("label"):
				self.assertIn(field["label"], de)
		self.assertEqual(de["Quantity Rule"], "Leistungsregel")
