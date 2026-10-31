"""Rechnungsdruck 7.3 (Handwerker#50): Druckformat „Rechnung Corevatis“ aus erpnext_druckformate mit den
Abrechnungsdaten aus 7.1/7.2 - Abschlagsrechnung, Schlussrechnung mit Abschlagsübersicht und § 35a-Block
(eine Seite), Schlussrechnung mit vielen Positionen (mehrere Seiten) - und „Mahnung Corevatis“ (7.5).
Mit ``CV_MUSTER_DIR`` (CI) landen die PDFs dort (Muster für die Freigabe). Ohne die Formate oder ohne
wkhtmltopdf werden die Drucktests übersprungen."""

from __future__ import annotations

import json
import os
import shutil
import unittest
from io import BytesIO
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, fmt_money, today

from corevatis_erpnext.abrechnung import abschlag, kern, mahnung, schluss
from corevatis_erpnext.abrechnung.einstellungen import EINSTELLUNGEN
from corevatis_erpnext.angebot import annahme
from corevatis_erpnext.setup import apply_site_template
from corevatis_erpnext.tests import _abrechnung as H
from corevatis_erpnext.tests import _angebot as td

FORMAT_RECHNUNG = "Rechnung Corevatis"
FORMAT_MAHNUNG = "Mahnung Corevatis"
NACHTRAG_ARTIKEL = "_TEST-CV-RD-NACHTRAG"
LANGTEXT = (
	"<div class='ql-editor read-mode'><p>Untergrund vorbereiten: lose und mürbe Putz- und Mörtelteile bis auf "
	"tragfähigen Untergrund entfernen, Fugen auskratzen, Flächen abfegen und vornässen.</p>"
	"<p>Einschließlich Abdecken angrenzender Bauteile und Reinigung des Arbeitsbereichs.</p></div>"
)


def wkhtmltopdf_vorhanden() -> bool:
	return bool(shutil.which("wkhtmltopdf"))


class TestRechnungsdruck(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if kern.KERN_APP in frappe.get_installed_apps():
			raise unittest.SkipTest(
				"corevatis installiert - Stellvertreter der Engine würde die echte überdecken"
			)
		cls._lang = frappe.local.lang
		frappe.set_user("Administrator")
		# Briefkopf, Standardformate (Rechnung Corevatis, Mahnung Corevatis), Steuern wie auf der Zielsite
		apply_site_template(template="huelshoff")
		H.einrichten()
		td.artikel(NACHTRAG_ARTIKEL, "Sonstiges")
		cls.firma = td.firma()
		cls.muster = os.environ.get("CV_MUSTER_DIR")
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.local.lang = cls._lang
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		if not frappe.db.exists("Print Format", FORMAT_RECHNUNG):
			self.skipTest("erpnext_druckformate ohne „Rechnung Corevatis“")
		if not wkhtmltopdf_vorhanden():
			self.skipTest("wkhtmltopdf fehlt")
		self.lang = frappe.local.lang
		self.addCleanup(setattr, frappe.local, "lang", self.lang)
		frappe.db.delete("Singles", {"doctype": EINSTELLUNGEN})
		frappe.db.delete("Singles", {"doctype": "Corevatis Abnahme Einstellungen"})
		H.kern_patch(self)

	# --- Hilfen ------------------------------------------------------------------------------

	def pdf(self, doctype: str, name: str, print_format: str, muster: str | None = None):
		"""PDF rendern; Rückgabe (Seitenzahl, Text je Seite, HTML). Mit ``muster`` als Muster speichern."""
		from pypdf import PdfReader

		html = frappe.get_print(doctype, name, print_format=print_format)
		inhalt = frappe.get_print(doctype, name, print_format=print_format, as_pdf=True)
		if muster and self.muster:
			Path(self.muster).mkdir(parents=True, exist_ok=True)
			(Path(self.muster) / f"{muster}.pdf").write_bytes(inhalt)
		reader = PdfReader(BytesIO(inhalt))
		return len(reader.pages), [page.extract_text() or "" for page in reader.pages], html

	def pruefe_seiten(self, texte: list[str]) -> str:
		alles = "\n".join(texte)
		self.assertNotIn("None", alles)
		for nr, text in enumerate(texte, start=1):
			self.assertIn(f"Seite {nr} von {len(texte)}", " ".join(text.split()))
		self.assertIn(self.firma, alles)
		return alles

	@staticmethod
	def geld(wert) -> str:
		return fmt_money(flt(wert), currency="EUR")

	def angebot_viele_positionen(self, anzahl: int):
		"""Angebot mit ``anzahl`` Positionen und Langtexten an einen Lead, eingereicht."""
		from erpnext.controllers.accounts_controller import get_taxes_and_charges

		lead = td.lead_mit_angebot_status()
		zeilen = []
		for i in range(1, anzahl + 1):
			code = td.artikel(f"_TEST-CV-RD-{i:02d}", "Innenabdichtung")
			frappe.db.set_value(
				"Item",
				code,
				{"item_name": f"Leistung {i:02d} Innenabdichtung", "description": LANGTEXT},
				update_modified=False,
			)
			zeilen.append(
				{
					"item_code": code,
					"item_name": f"Leistung {i:02d} Innenabdichtung",
					"description": LANGTEXT,
					"qty": 10 + i,
					"rate": 20 + i,
					"uom": "Nos",
				}
			)
		doc = frappe.get_doc(
			{
				"doctype": "Quotation",
				"quotation_to": "Lead",
				"party_name": lead.name,
				"company": self.firma,
				"transaction_date": today(),
				"valid_till": add_days(today(), 30),
				"currency": "EUR",
				"conversion_rate": 1,
				"selling_price_list": frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}),
				"price_list_currency": "EUR",
				"plc_conversion_rate": 1,
				"cv_aufmass": td.AUFMASS,
				"cv_titel": "Kellerabdichtung Musterweg 3, 47877 Willich",
				"items": zeilen,
			}
		)
		vorlage = frappe.db.get_value(
			"Sales Taxes and Charges Template", {"company": self.firma, "is_default": 1, "disabled": 0}
		)
		if vorlage:
			doc.taxes_and_charges = vorlage
			for row in get_taxes_and_charges("Sales Taxes and Charges Template", vorlage):
				doc.append("taxes", row)
		doc.insert(ignore_permissions=True)
		doc.submit()
		return doc

	# --- Tests -------------------------------------------------------------------------------

	def test_abschlag_und_schlussrechnung(self):
		auftrag, projekt = H.vorgang()
		r1 = H._als(H.BUERO, abschlag.abschlag_stellen, project=projekt)  # 30 % = 150 netto
		# Abschlag zurückdatieren (Mahnung unten) und freigeben
		H.datieren(r1["rechnung"], tage_alt=40)
		si1 = H.freigeben(r1["rechnung"])
		seiten, texte, html = self.pdf("Sales Invoice", si1.name, FORMAT_RECHNUNG, "rechnung-abschlag")
		alles = self.pruefe_seiten(texte)
		self.assertEqual(seiten, 1)
		self.assertIn(f"Abschlagsrechnung Nr. 1 zum Auftrag {auftrag}", alles)
		self.assertIn("Stand der Abrechnung", alles)
		self.assertIn("Auftragssumme " + self.geld(500), alles)
		self.assertIn("30 % der Auftragssumme", alles)
		self.assertIn(self.geld(150), alles)
		self.assertIn(self.geld(178.5), alles)
		# § 35a: Lohnanteil anteilig (30 % von 176 = 52,80 netto, 62,83 brutto)
		self.assertIn("§ 35a EStG", alles)
		self.assertIn("Lohn-, Fahrt- und Maschinenkosten", alles)
		self.assertIn(self.geld(52.8), alles)
		self.assertIn(self.geld(62.83), alles)
		self.assertIn("Zahlbar bis", alles)
		self.assertIn("Rechnungs-Nr.", alles)
		self.assertIn("Leistungszeitraum", alles)
		self.assertIn(si1.name, alles)
		self.assertNotIn("Abzüglich bereits gestellter", alles)
		# Positionen aus dem Formular (HTML): eine Abschlagsposition, Langtext mit Fälligkeit
		self.assertIn("fällig bei Baubeginn", html)
		# zweiter Abschlag, Abnahme, Schlussrechnung mit Mehrmenge und Nachtrag
		r2 = H._als(H.BUERO, abschlag.abschlag_stellen, project=projekt)  # 40 % = 200
		si2 = H.freigeben(r2["rechnung"])
		H.abnehmen(projekt)
		so_detail = frappe.get_doc("Sales Order", auftrag).items[0].name
		r = H._als(
			H.BUERO,
			schluss.schlussrechnung_erstellen,
			project=projekt,
			positionen=json.dumps({so_detail: {"menge_ist": 12}}),
			nachtraege=json.dumps(
				[
					{
						"item_code": NACHTRAG_ARTIKEL,
						"bezeichnung": "Durchbruch verschließen",
						"menge": 1,
						"einzelpreis": 80,
						"lohnanteil": 60,
					}
				]
			),
		)
		si = H.freigeben(r["rechnung"])
		seiten, texte, html = self.pdf("Sales Invoice", si.name, FORMAT_RECHNUNG, "rechnung-schluss-kurz")
		alles = self.pruefe_seiten(texte)
		self.assertEqual(seiten, 1)
		self.assertIn(f"Schlussrechnung zum Auftrag {auftrag}", alles)
		self.assertIn("Abzüglich bereits gestellter Abschlagsrechnungen", alles)
		for name in (si1.name, si2.name):
			self.assertIn(name, alles)
		# Leistung 680 / 129,20 / 809,20; Abschläge 350 / 66,50 / 416,50; Rest 330 / 62,70 / 392,70
		for betrag in (680, 129.2, 809.2, 350, 66.5, 416.5, 330, 62.7, 392.7):
			self.assertIn(self.geld(betrag), alles, betrag)
		self.assertIn("Leistung brutto", alles)
		self.assertIn("Verbleibender Betrag netto", alles)
		self.assertIn("Zu zahlen", alles)
		self.assertIn("Mehrmenge", alles)
		self.assertIn("Nachtrag", alles)
		# Abzugszeilen (negativer Preis) stehen nicht in der Positionsliste
		self.assertNotIn(self.geld(-150), alles)
		self.assertNotIn("-150,00", alles)
		# § 35a: 12 x 17,60 + 60 = 271,20 netto / 322,73 brutto
		self.assertIn(self.geld(271.2), alles)
		self.assertIn(self.geld(322.73), alles)
		# Summen stimmen mit dem Beleg überein
		self.assertIn(si.get_formatted("grand_total"), alles)
		self.assertEqual((flt(si.net_total), flt(si.grand_total)), (330.0, 392.7))
		# Mahnung zum überfälligen Abschlag 1 drucken
		if not frappe.db.exists("Print Format", FORMAT_MAHNUNG):
			self.skipTest("erpnext_druckformate ohne „Mahnung Corevatis“")
		m = H._als(H.BUERO, mahnung.mahnung_erstellen, si1.name)
		seiten, texte, _html = self.pdf("Dunning", m["mahnung"], FORMAT_MAHNUNG, "mahnung")
		alles = self.pruefe_seiten(texte)
		self.assertEqual(seiten, 1)
		self.assertIn("Zahlungserinnerung", alles)
		self.assertIn(si1.name, alles)
		self.assertIn(f"Abschlagsrechnung Nr. 1 zum Auftrag {auftrag}", alles)
		self.assertIn("Aufmerksamkeit entgangen", alles)
		self.assertIn(self.geld(178.5), alles)
		self.assertIn("bis zum " + frappe.utils.formatdate(add_days(today(), 7), "dd.MM.yyyy"), alles)
		self.assertIn("Gesamtbetrag", alles)

	def test_schlussrechnung_viele_positionen(self):
		angebot = self.angebot_viele_positionen(24)
		a = H._als(H.BUERO, annahme.angebot_annehmen, angebot.name, einreichen=1)
		auftrag, projekt = a["auftrag"], a["projekt"]
		H.abnehmen(projekt)
		r = H._als(H.BUERO, schluss.schlussrechnung_erstellen, project=projekt)
		si = H.freigeben(r["rechnung"])
		seiten, texte, _html = self.pdf("Sales Invoice", si.name, FORMAT_RECHNUNG, "rechnung-schluss-lang")
		alles = self.pruefe_seiten(texte)
		self.assertGreaterEqual(seiten, 2)
		self.assertIn(f"Schlussrechnung zum Auftrag {auftrag}", alles)
		self.assertIn("Kellerabdichtung Musterweg 3", alles)
		self.assertIn(si.get_formatted("grand_total"), alles)
		# jede Position vollständig auf einer Seite (Kurztext und Einzelpreis zusammen), keine Abschläge
		for row in si.items:
			self.assertTrue(
				any(row.item_name[:20] in t and row.get_formatted("rate") in t for t in texte), row.item_name
			)
		self.assertNotIn("Abzüglich bereits gestellter", alles)
		self.assertIn("Rechnungsbetrag brutto", alles)
