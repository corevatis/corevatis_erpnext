"""Textbausteine im Desk (P2.6, Handwerker#89): Felder und Einordnung, Platzhalterprüfung, Auswahl mit
Rückfall, Angebot mit ersetzten Platzhaltern, Vorschau, Mahntext in den Mahntyp, Serienbrief-PDF, Rechte.

Läuft ohne ``corevatis`` (Kern): Angebot und Kunden sind reines ERPNext."""

from __future__ import annotations

from io import BytesIO

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from corevatis_erpnext import textbausteine as tb
from corevatis_erpnext.abrechnung.mahnung import vorhandener_typ
from corevatis_erpnext.angebot.textbausteine import ANGEBOT_STANDARD, VORBEMERKUNG_STANDARD
from corevatis_erpnext.tests import _abrechnung as H
from corevatis_erpnext.tests import _angebot as td
from corevatis_erpnext.textbausteine import platzhalter, serienbrief
from corevatis_erpnext.textbausteine.felder import ART_VORBEMERKUNG, DOCTYPE, FELDER

INNEN = "Innenabdichtung"
TEST_TITEL = (
	"_Test VB allgemein",
	"_Test VB Innen",
	"_Test VB Firma Innen",
	"_Test VB abgelaufen",
	"_Test Unbekannt",
)
KUNDEN = ("_Test Serienbrief A & Co", "_Test Serienbrief B")


def _baustein(titel: str, **werte):
	doc = frappe.get_doc(
		{"doctype": DOCTYPE, "title": titel, "selling": 1, "cv_art": ART_VORBEMERKUNG, **werte}
	)
	doc.insert(ignore_permissions=True)
	return doc


def _kunde(name: str) -> str:
	if frappe.db.exists("Customer", name):
		return name
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": name,
			"customer_type": "Company",
			"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
			"territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"),
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


class TestTextbausteine(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		H.einrichten()
		tb.ensure_setup()
		tb.ensure_setup()  # idempotent
		cls.firma = td.firma()
		for titel in TEST_TITEL:
			frappe.delete_doc(DOCTYPE, titel, force=True, ignore_permissions=True)
		_baustein("_Test VB allgemein", terms="<p>Allgemein</p>")
		_baustein("_Test VB Innen", terms="<p>Innen</p>", cv_verfahren=INNEN)
		_baustein(
			"_Test VB Firma Innen",
			terms="<p>Angebot [[Angebot]] für [[Kunde]], Objekt [[Objekt]], gültig bis [[Gueltig_bis]]</p>",
			cv_verfahren=INNEN,
			cv_firma=cls.firma,
		)
		_baustein(
			"_Test VB abgelaufen",
			terms="<p>Alt</p>",
			cv_verfahren=INNEN,
			cv_firma=cls.firma,
			cv_reihenfolge=-1,
			cv_gueltig_bis=add_days(today(), -1),
		)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")

	def test_felder_rechte_einordnung(self):
		meta = frappe.get_meta(DOCTYPE)
		for feld in FELDER:
			self.assertTrue(meta.has_field(feld), feld)
		self.assertEqual(frappe.db.get_value(DOCTYPE, ANGEBOT_STANDARD, "cv_art"), "Zahlungsbedingungen")
		self.assertEqual(frappe.db.get_value(DOCTYPE, VORBEMERKUNG_STANDARD, "cv_belegart"), "Angebot")
		for stufe in (0, 1, 2):
			self.assertTrue(frappe.db.exists(DOCTYPE, tb.mahntext_titel(stufe)), stufe)
		self.assertTrue(frappe.db.exists(DOCTYPE, tb.ANSCHREIBEN_STANDARD))
		self.assertTrue(frappe.has_permission(DOCTYPE, "write", user=H.BUERO))
		self.assertTrue(frappe.has_permission(DOCTYPE, "read", user=H.MEISTER))
		self.assertFalse(frappe.has_permission(DOCTYPE, "write", user=H.MEISTER))

	def test_platzhalter_pruefung_und_ersetzung(self):
		with self.assertRaises(tb.TextbausteinFehler) as cm:
			_baustein("_Test Unbekannt", terms="<p>[[Falsch]] und [[Kunde]]</p>")
		self.assertIn("[[Falsch]]", str(cm.exception))
		self.assertIn("[[Betrag]]", str(cm.exception))
		self.assertFalse(frappe.db.exists(DOCTYPE, "_Test Unbekannt"))

		d = frappe.new_doc("Dunning")
		d.company = self.firma
		d.customer_name = "Kunde A & B"
		d.currency = "EUR"
		d.total_outstanding = 100
		d.append(
			"overdue_payments", {"sales_invoice": "SINV-TEST-1", "due_date": "2026-10-01", "outstanding": 100}
		)
		k = platzhalter.kontext_aus(d)
		self.assertEqual(k["Rechnung"], "SINV-TEST-1")
		self.assertIn("100", k["Offen"])
		self.assertEqual(k["Anrede"], platzhalter.ANREDE_NEUTRAL)
		text = platzhalter.ersetzen("<p>[[Kunde]]: [[Rechnung]] [[Nix]]</p>", k)
		self.assertEqual(text, "<p>Kunde A &amp; B: SINV-TEST-1 [[Nix]]</p>")
		self.assertEqual(platzhalter.ersetzen("[[Kunde]]", k, html=False), "Kunde A & B")

	def test_auswahl_mit_rueckfall(self):
		b = tb.bester(ART_VORBEMERKUNG, firma=self.firma, belegart="Angebot", verfahren=INNEN)
		self.assertEqual(b.name, "_Test VB Firma Innen")
		b = tb.bester(ART_VORBEMERKUNG, firma="_Andere Firma", belegart="Angebot", verfahren=INNEN)
		self.assertEqual(b.name, "_Test VB Innen")
		b = tb.bester(ART_VORBEMERKUNG, firma=self.firma, verfahren="Drainage")
		self.assertEqual(b.name, "_Test VB allgemein")
		b = tb.bester(ART_VORBEMERKUNG, firma=self.firma, belegart="Angebot", verfahren="Drainage")
		self.assertEqual(b.name, VORBEMERKUNG_STANDARD)
		namen = [z.name for z in tb.finden(ART_VORBEMERKUNG, firma=self.firma, verfahren=INNEN)]
		self.assertNotIn("_Test VB abgelaufen", namen)

	def test_angebot_und_vorschau(self):
		lead = td.lead_mit_angebot_status()
		q = td.angebot(lead.name, zusatz=False)
		self.assertIn(f"Angebot {q.name} für", q.cv_vorbemerkung)
		self.assertNotIn("[[", q.cv_vorbemerkung)
		self.assertIn(frappe.utils.format_date(q.valid_till), q.cv_vorbemerkung)
		self.assertEqual(q.tc_name, ANGEBOT_STANDARD)

		r = tb.vorschau("_Test VB Firma Innen")
		self.assertTrue(r["beleg"])
		self.assertNotIn("[[", r["html"])
		self.assertEqual([w["name"] for w in r["werte"]], ["Angebot", "Kunde", "Objekt", "Gueltig_bis"])
		r = tb.vorschau("_Test VB Firma Innen", beleg_doctype="Quotation", beleg_name=q.name)
		self.assertIn(q.name, r["html"])

	def test_mahntext_in_mahntyp(self):
		doc = frappe.get_doc(DOCTYPE, tb.mahntext_titel(1))
		doc.terms = "<p>Neu: Rechnung [[Rechnung]], offen [[Offen]].</p>"
		doc.save(ignore_permissions=True)
		typ = vorhandener_typ(1, self.firma)
		self.assertTrue(typ)
		self.assertEqual(frappe.db.get_value("Dunning Letter Text", {"parent": typ}, "body_text"), doc.terms)
		with self.assertRaises(tb.TextbausteinFehler):
			doc.cv_mahnstufe = ""
			doc.save(ignore_permissions=True)

	def test_serienbrief_pdf(self):
		from pypdf import PdfReader

		kunden = [_kunde(k) for k in KUNDEN]
		frappe.db.commit()
		r = H._als(
			H.BUERO,
			serienbrief.erstellen,
			kunden=kunden,
			baustein=tb.ANSCHREIBEN_STANDARD,
			betreff="Info [[Kunde]]",
		)
		self.assertEqual(r["anzahl"], 2)
		self.assertEqual(r["ausgelassen"], [])
		datei = frappe.get_doc("File", r["name"])
		self.assertEqual(datei.is_private, 1)
		self.assertEqual(len(PdfReader(BytesIO(datei.get_content())).pages), 2)
		with self.assertRaises(frappe.PermissionError):
			H._als(
				H.MEISTER, serienbrief.erstellen, kunden=kunden, baustein=tb.ANSCHREIBEN_STANDARD, betreff="x"
			)
		with self.assertRaises(tb.TextbausteinFehler):
			H._als(H.BUERO, serienbrief.erstellen, kunden=[], baustein=tb.ANSCHREIBEN_STANDARD, betreff="x")
