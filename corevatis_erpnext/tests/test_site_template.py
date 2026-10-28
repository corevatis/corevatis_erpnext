"""Site-Template (Aufgabe 0.4): Anwenden auf frische Site, Kernobjekte, Idempotenz,
Steuerberechnung, Nummernkreise, Rechte, zweite Firma mit SKR04."""

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import add_days, flt, getdate, today
from frappe.utils.nestedset import get_root_of

from corevatis_erpnext.setup import apply_site_template, verify_site_template
from corevatis_erpnext.setup.config import (
	SiteTemplateConfigError,
	deep_merge,
	find_placeholders,
	load_config,
)

# Doctypes, deren Anzahl und letzter Änderungszeitpunkt ein zweiter Lauf nicht ändern darf
SNAPSHOT_DOCTYPES = (
	"Company",
	"Account",
	"Fiscal Year",
	"Cost Center",
	"Sales Taxes and Charges Template",
	"Purchase Taxes and Charges Template",
	"Item Tax Template",
	"Item Group",
	"Item Tax",
	"Property Setter",
	"Custom DocPerm",
	"Role",
	"Role Profile",
	"Has Role",
	"Warehouse",
	"Letter Head",
	"Address Template",
	"Address",
	"Print Format",
	"DefaultValue",
)
SNAPSHOT_SINGLES = ("System Settings", "Global Defaults", "ERPNext Germany Settings")


def snapshot() -> dict:
	state = {}
	for doctype in SNAPSHOT_DOCTYPES:
		table = f"tab{doctype}"
		count, modified = frappe.db.sql(f"select count(*), max(modified) from `{table}`")[0]
		state[doctype] = (count, str(modified))
	for doctype in SNAPSHOT_SINGLES:
		if frappe.db.exists("DocType", doctype):
			state[doctype] = frappe.db.sql(
				"select field, value from tabSingles where doctype=%s order by field", doctype
			)
	return state


class TestSiteTemplateConfig(UnitTestCase):
	def test_huelshoff_extends_standard(self):
		cfg = load_config(template="huelshoff")
		self.assertEqual(cfg["company"]["company_name"], "Hülshoff Sanierungstechnik")
		self.assertEqual(cfg["company"]["abbr"], "HST")
		self.assertIn(cfg["company"]["chart_of_accounts"], ("SKR03", "SKR04"))
		self.assertEqual(cfg["company"]["country"], "Germany")
		self.assertEqual(set(cfg["roles"]), {"Büro", "Meister", "Monteur"})
		self.assertEqual([w["warehouse_name"] for w in cfg["warehouses"]], ["Hauptlager", "Fahrzeug"])
		self.assertEqual(cfg["naming_series"]["Quotation"]["series"], "ANG-.YYYY.-")
		self.assertNotIn("_beschreibung", cfg)
		self.assertNotIn("extends", cfg)

	def test_placeholders_are_reported(self):
		cfg = load_config(template="huelshoff")
		found = find_placeholders(cfg)
		self.assertIn("company.tax_id", found)
		self.assertIn("address.address_line1", found)
		self.assertNotIn("company.company_name", found)

	def test_deep_merge(self):
		merged = deep_merge({"a": {"b": 1, "c": [1]}, "d": 1}, {"a": {"c": [2]}, "e": 2})
		self.assertEqual(merged, {"a": {"b": 1, "c": [2]}, "d": 1, "e": 2})

	def test_invalid_chart(self):
		with self.assertRaises(SiteTemplateConfigError):
			load_config(
				config={
					"extends": "_standard",
					"company": {"company_name": "X", "abbr": "X", "chart_of_accounts": "SKR99"},
				}
			)

	def test_placeholder_company_name(self):
		with self.assertRaises(SiteTemplateConfigError):
			load_config(
				config={"extends": "_standard", "company": {"company_name": "PLATZHALTER", "abbr": "X"}}
			)

	def test_unknown_permission_set(self):
		with self.assertRaises(SiteTemplateConfigError):
			load_config(
				config={
					"extends": "_standard",
					"company": {"company_name": "X", "abbr": "X"},
					"roles": {"Büro": {"permissions": {"Lead": "alles"}}},
				}
			)

	def test_exactly_one_source(self):
		with self.assertRaises(SiteTemplateConfigError):
			load_config()
		with self.assertRaises(SiteTemplateConfigError):
			load_config(template="huelshoff", config_file="/tmp/x.json")


class TestSiteTemplate(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.config = load_config(template="huelshoff")
		# In der CI hat ein vorheriger Schritt das Template per bench execute angewendet;
		# lokal läuft hier der erste Lauf (Setup-Wizard + Firma).
		cls.first_run = apply_site_template(template="huelshoff")
		cls.company = cls.first_run["company"]
		cls.abbr = cls.config["company"]["abbr"]

	def test_verify_all_checks_pass(self):
		result = verify_site_template(template="huelshoff")
		self.assertTrue(result["ok"], "\n".join(result["failed"]))
		self.assertGreater(result["count"], 80)

	def test_second_run_changes_nothing(self):
		before = snapshot()
		report = apply_site_template(template="huelshoff", commit=False)
		self.assertEqual(report["changes"], [])
		self.assertEqual(snapshot(), before)

	def test_company_and_chart(self):
		company = frappe.get_doc("Company", self.company)
		self.assertEqual(company.company_name, "Hülshoff Sanierungstechnik")
		self.assertEqual(company.abbr, self.abbr)
		self.assertEqual(company.country, "Germany")
		self.assertEqual(company.default_currency, "EUR")
		chart = self.config["company"]["chart_of_accounts"]
		self.assertTrue(company.chart_of_accounts.startswith(chart))
		self.assertTrue(frappe.is_setup_complete())
		self.assertEqual(frappe.db.get_single_value("Global Defaults", "default_company"), self.company)

	def test_warehouses(self):
		root = frappe.db.get_value("Warehouse", {"company": self.company, "is_group": 1}, order_by="lft asc")
		for name in ("Hauptlager", "Fahrzeug"):
			wh = frappe.db.get_value(
				"Warehouse",
				f"{name} - {self.abbr}",
				["company", "parent_warehouse", "is_group"],
				as_dict=True,
			)
			self.assertEqual(wh.company, self.company)
			self.assertEqual(wh.parent_warehouse, root)
			self.assertEqual(wh.is_group, 0)
		self.assertEqual(
			frappe.db.get_value("Company", self.company, "default_warehouse"), f"Hauptlager - {self.abbr}"
		)

	def test_system_settings(self):
		settings = frappe.get_single("System Settings")
		self.assertEqual(settings.language, "de")
		self.assertEqual(settings.time_zone, "Europe/Berlin")
		self.assertEqual(settings.date_format, "dd.mm.yyyy")
		self.assertEqual(settings.number_format, "#.###,##")
		self.assertEqual(frappe.db.get_single_value("Global Defaults", "disable_rounded_total"), 1)

	def test_placeholders_not_written(self):
		self.assertFalse(frappe.db.get_value("Company", self.company, "tax_id"))
		self.assertTrue(any("company.tax_id" in w for w in self.first_run["warnings"]))
		# Firmenadresse hat einen Platzhalter in der Straße und wird nicht angelegt
		self.assertFalse(
			frappe.db.exists(
				"Dynamic Link",
				{"link_doctype": "Company", "link_name": self.company, "parenttype": "Address"},
			)
		)

	def test_letter_head(self):
		name = frappe.db.get_value("Company", self.company, "default_letter_head")
		self.assertEqual(name, "Hülshoff Sanierungstechnik")
		footer = frappe.db.get_value("Letter Head", name, "footer")
		self.assertNotIn("PLATZHALTER", footer)
		self.assertNotIn("$owner_line", footer)

	def test_print_formats_if_installed(self):
		if not frappe.db.exists("Print Format", "Angebot"):
			self.skipTest("erpnext_druckformate nicht installiert")
		self.assertEqual(frappe.get_meta("Quotation").default_print_format, "Angebot")
		self.assertEqual(frappe.get_meta("Sales Invoice").default_print_format, "Ausgangsrechnung")

	def test_permissions_by_role(self):
		expected = {
			"Büro": [
				("Quotation", "submit", True),
				("Sales Invoice", "create", True),
				("Lead", "write", True),
				("Stock Entry", "write", False),
			],
			"Meister": [
				("Project", "create", True),
				("Quotation", "submit", True),
				("BOM", "submit", True),
				("Sales Invoice", "read", False),
			],
			"Monteur": [
				("Timesheet", "create", True),
				("Timesheet", "submit", False),
				("Stock Entry", "create", True),
				("Stock Entry", "submit", False),
				("Item", "read", True),
				("Item", "write", False),
				("Quotation", "read", False),
				("Sales Invoice", "read", False),
			],
			# Standardrolle behält ihre Rechte (Custom DocPerm übernimmt die Standardrechte)
			"Sales User": [("Quotation", "create", True), ("Sales Order", "submit", True)],
		}
		for role, rules in expected.items():
			user = self._user(role)
			for doctype, ptype, allowed in rules:
				with self.subTest(role=role, doctype=doctype, ptype=ptype):
					self.assertEqual(bool(frappe.has_permission(doctype, ptype, user=user)), allowed)

	def test_quotation_tax_and_series(self):
		quotation = frappe.get_doc(
			{
				"doctype": "Quotation",
				"quotation_to": "Customer",
				"party_name": self._customer(),
				"company": self.company,
				"transaction_date": today(),
				"valid_till": add_days(today(), 30),
				"currency": "EUR",
				"conversion_rate": 1,
				"selling_price_list": self._price_list(),
				"price_list_currency": "EUR",
				"plc_conversion_rate": 1,
				"items": [{"item_code": self._item(), "qty": 2, "rate": 50}],
			}
		)
		self._add_default_taxes(quotation)
		quotation.insert()
		self.assertTrue(quotation.name.startswith(f"ANG-{getdate().year}-"), quotation.name)
		self.assertEqual(quotation.items[0].item_tax_template, f"19 % - {self.abbr}")
		self.assertEqual(flt(quotation.net_total), 100.0)
		self.assertEqual(flt(quotation.total_taxes_and_charges), 19.0)
		self.assertEqual(flt(quotation.grand_total), 119.0)

	def test_invoice_and_credit_note_series(self):
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return

		invoice = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"customer": self._customer(),
				"company": self.company,
				"posting_date": today(),
				"due_date": today(),
				"currency": "EUR",
				"conversion_rate": 1,
				"selling_price_list": self._price_list(),
				"price_list_currency": "EUR",
				"plc_conversion_rate": 1,
				"items": [{"item_code": self._item(), "qty": 1, "rate": 100}],
			}
		)
		self._add_default_taxes(invoice)
		invoice.insert()
		self.assertTrue(invoice.name.startswith(f"RE-{getdate().year}-"), invoice.name)
		self.assertEqual(flt(invoice.grand_total), 119.0)
		invoice.submit()

		credit_note = make_sales_return(invoice.name)
		credit_note.insert()
		self.assertTrue(credit_note.name.startswith(f"GS-{getdate().year}-"), credit_note.name)
		self.assertEqual(flt(credit_note.grand_total), -119.0)

	def test_second_company_skr04(self):
		config = {
			"extends": "_standard",
			"company": {
				"company_name": "_Test Corevatis SKR04",
				"abbr": "_T04",
				"chart_of_accounts": "SKR04",
			},
		}
		report = apply_site_template(config=config, commit=False)
		self.assertTrue(any("_Test Corevatis SKR04" in c for c in report["changes"]), report["changes"])
		result = verify_site_template(config=config)
		self.assertTrue(result["ok"], "\n".join(result["failed"]))
		self.assertTrue(
			frappe.db.exists("Account", {"company": "_Test Corevatis SKR04", "account_number": "3806"})
		)
		self.assertTrue(frappe.db.exists("Warehouse", "Fahrzeug - _T04"))
		# erste Firma bleibt Standard, zweiter Lauf ändert nichts
		self.assertEqual(frappe.db.get_single_value("Global Defaults", "default_company"), self.company)
		self.assertEqual(apply_site_template(config=config, commit=False)["changes"], [])

	# Hilfen ---------------------------------------------------------------

	def _user(self, role: str) -> str:
		email = f"cv-test-{frappe.scrub(role).replace('ü', 'ue')}@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": f"Test {role}", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		user = frappe.get_doc("User", email)
		user.add_roles(role)
		return email

	def _customer(self) -> str:
		name = "_Test CV Kunde"
		if not frappe.db.exists("Customer", name):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": name,
					"customer_type": "Company",
					"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}),
					"territory": frappe.db.get_value("Territory", {"is_group": 0})
					or get_root_of("Territory"),
				}
			).insert(ignore_permissions=True)
		return name

	def _item(self) -> str:
		code = "_TEST-CV-ABDICHTUNG"
		if not frappe.db.exists("Item", code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_name": "Test Abdichtung",
					"item_group": frappe.db.get_value("Item Group", {"is_group": 0}),
					"stock_uom": "Nos",
					"is_stock_item": 0,
				}
			).insert(ignore_permissions=True)
		return code

	def _price_list(self) -> str:
		return frappe.db.get_value("Price List", {"selling": 1, "enabled": 1})

	def _add_default_taxes(self, doc) -> None:
		from erpnext.controllers.accounts_controller import get_taxes_and_charges

		template = frappe.db.get_value(
			"Sales Taxes and Charges Template", {"company": self.company, "is_default": 1}
		)
		doc.taxes_and_charges = template
		for row in get_taxes_and_charges("Sales Taxes and Charges Template", template):
			doc.append("taxes", row)
