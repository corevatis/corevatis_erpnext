"""Befunde mvp-test: Steuerzeilen und Drucken auf bestehenden Sites nachziehen.

- Accounts Settings ``add_taxes_from_item_tax_template = 0``: Sites, deren Template vor
  corevatis_erpnext#24 lief (z. B. mvp-kunde2), hängen im Desk sonst bis zu sechs weitere
  19-%-Zeilen aus der Artikelsteuervorlage an (Vorsteuer-, EU-, § 13b-Konten).
- Verkaufs-Steuervorlage „Umsatzsteuer 19 %“ (eine Zeile, fester Satz) je deutscher Firma mit
  den ERPNext-Steuervorlagen anlegen und zum Standard machen, wenn der Standard noch die
  ERPNext-Vorlage „Lieferung oder sonstige Leistung im Inland“ ist (zwei Zeilen 19 %/7 % mit
  Satz 0). Eigene Standards bleiben. Betrifft auch Firmen, auf die das Template nie lief.
- Print Settings ``print_generated_pdf = 1``: „Drucken“ druckt das PDF statt der
  Browser-Vorschau.
- Standard-Preislisten (Selling/Buying Settings) auf die Katalog-Preislisten, wenn der Katalog
  importiert ist und noch die ERPNext-Vorgabe gilt (sonst Angebotspositionen mit 0,00 €).

Bestehende Belege bleiben unverändert. Werte aus ``setup/templates/_standard.json``.
"""

import frappe

from corevatis_erpnext.setup.config import _read, _template_path, strip_comments
from corevatis_erpnext.setup.site_template import _set_default_template, ensure_sales_tax_templates


def execute():
	cfg = strip_comments(_read(_template_path("_standard")))
	taxes = cfg.get("taxes") or {}
	replace = taxes.get("replace_default_sales_templates") or []
	default = taxes.get("default_sales_template")

	if "add_taxes_from_item_tax_template" in taxes:
		frappe.db.set_single_value(
			"Accounts Settings", "add_taxes_from_item_tax_template", taxes["add_taxes_from_item_tax_template"]
		)

	for company in frappe.get_all("Company", {"country": "Germany"}, ["name", "chart_of_accounts"]):
		# nur Firmen mit den deutschen ERPNext-Steuervorlagen
		if not frappe.db.exists(
			"Sales Taxes and Charges Template", {"company": company.name, "title": ("in", replace)}
		):
			continue
		for message in ensure_sales_tax_templates(company.name, company.chart_of_accounts, taxes):
			print(message)
		name = default and frappe.db.get_value(
			"Sales Taxes and Charges Template", {"company": company.name, "title": default}
		)
		if name:
			message = _set_default_template("Sales Taxes and Charges Template", company.name, name, replace)
			if message:
				print(message)

	for field, value in (cfg.get("print_settings") or {}).items():
		if frappe.get_meta("Print Settings").has_field(field):
			frappe.db.set_single_value("Print Settings", field, value)

	from corevatis_erpnext.katalog.importer import standard_preislisten

	for message in standard_preislisten():
		print(message)
