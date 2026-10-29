"""Testdaten für Angebotsannahme (4.4) und Nachfassen (4.5): Firma, Artikel, Angebot."""

import frappe
from frappe.utils import add_days, today

from corevatis_erpnext.tests._helpers import make_lead

FIRMA_TEMPLATE = "Hülshoff Sanierungstechnik"
ARTIKEL = "_TEST-CV-ANNAHME-IA"
ARTIKEL_EVENTUAL = "_TEST-CV-ANNAHME-AA"
ARTIKEL_ALTERNATIV = "_TEST-CV-ANNAHME-HS"
# Angebote aus dem Aufmaß bekommen bei der Annahme immer ein Projekt (Review Epic 4 A-5)
AUFMASS = "_TEST-CV-AUFMASS-ANNAHME"


def firma() -> str:
	if frappe.db.exists("Company", FIRMA_TEMPLATE):
		return FIRMA_TEMPLATE
	return (
		frappe.db.get_single_value("Global Defaults", "default_company")
		or frappe.get_all("Company", pluck="name")[0]
	)


def ensure_felder_41() -> None:
	"""Felder der Angebotsengine (4.1) und ihr Spiegel nach Sales Order (4.4) - wie nach ``migrate``."""
	from corevatis_erpnext.angebot.annahme_felder import ensure_felder
	from corevatis_erpnext.angebot.felder import ensure_angebot_felder

	ensure_angebot_felder()
	ensure_felder()


def artikel(code: str, verfahren: str) -> str:
	if not frappe.db.exists("Item", code):
		werte = {
			"doctype": "Item",
			"item_code": code,
			"item_name": f"Test {verfahren}",
			"item_group": frappe.db.get_value("Item Group", {"is_group": 0}),
			"stock_uom": "Nos",
			"is_stock_item": 0,
		}
		if frappe.get_meta("Item").has_field("cv_verfahren"):
			werte["cv_verfahren"] = verfahren
		frappe.get_doc(werte).insert(ignore_permissions=True)
	return code


def ensure_artikel() -> None:
	artikel(ARTIKEL, "Innenabdichtung")
	artikel(ARTIKEL_EVENTUAL, "Außenabdichtung")
	artikel(ARTIKEL_ALTERNATIV, "Horizontalsperre Injektion")


def lead_mit_angebot_status(**werte):
	werte.setdefault("cv_objekt_adresse", "Kellerweg 7, 48143 Münster")
	werte.setdefault("mobile_no", "0170 1234567")
	# eindeutiger Name: gleicher Name + PLZ gilt als Kundendublette (Review Epic 4 A-3)
	werte.setdefault("last_name", f"Musterfrau {frappe.generate_hash(length=6)}")
	lead = make_lead(**werte)
	# Workflow-Zustand „Angebot“ (1.4) - direkt gesetzt, die Übergänge prüft test_lead_workflow
	frappe.db.set_value("Lead", lead.name, "cv_status", "Angebot", update_modified=False)
	return frappe.get_doc("Lead", lead.name)


def angebot(
	party: str,
	quotation_to: str = "Lead",
	zusatz: bool = True,
	tage_alt: int = 0,
	corevatis: bool = True,
	**werte,
):
	"""Eingereichtes Angebot: Hauptposition 10 x 50 €, optional Eventual- und Alternativposition.

	``corevatis``: Angebot aus dem Aufmaß (``cv_aufmass`` gesetzt) - sonst ein gewöhnliches Angebot.
	"""
	company = firma()
	if corevatis and frappe.get_meta("Quotation").has_field("cv_aufmass"):
		werte.setdefault("cv_aufmass", AUFMASS)
	datum = add_days(today(), -tage_alt)
	zeilen = [{"item_code": ARTIKEL, "qty": 10, "rate": 50}]
	if zusatz:
		zeilen += [
			{"item_code": ARTIKEL_EVENTUAL, "qty": 1, "rate": 100, "cv_eventualposition": 1},
			{"item_code": ARTIKEL_ALTERNATIV, "qty": 2, "rate": 30, "cv_alternativposition": 1},
		]
	doc = frappe.get_doc(
		{
			"doctype": "Quotation",
			"quotation_to": quotation_to,
			"party_name": party,
			"company": company,
			"transaction_date": datum,
			"valid_till": add_days(datum, 60),
			"currency": "EUR",
			"conversion_rate": 1,
			"selling_price_list": frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}),
			"price_list_currency": "EUR",
			"plc_conversion_rate": 1,
			"items": zeilen,
			**werte,
		}
	)
	from erpnext.controllers.accounts_controller import get_taxes_and_charges

	vorlage = frappe.db.get_value(
		"Sales Taxes and Charges Template", {"company": company, "is_default": 1, "disabled": 0}
	)
	if vorlage:
		doc.taxes_and_charges = vorlage
		for row in get_taxes_and_charges("Sales Taxes and Charges Template", vorlage):
			doc.append("taxes", row)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc
