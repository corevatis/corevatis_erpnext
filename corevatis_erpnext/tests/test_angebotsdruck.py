"""Angebotsdruck (Aufgabe 4.3): Druckformat „Angebot Corevatis“, Textbausteine, Standardformat.

Rendert drei Angebote (kurz, mittel, lang) als PDF mit wkhtmltopdf und prüft Seitenzahl,
„Seite x von y“, Summen, Eventual-/Alternativpositionen, kein „None“ und Maskierung. Mit
``CV_MUSTER_DIR`` (CI) landen die PDFs dort (Muster für die Freigabe).

Das Format kommt aus ``erpnext_druckformate`` (Branch ``version-16-corevatis``); ohne die App
oder ohne wkhtmltopdf werden die Drucktests übersprungen.
"""

import os
import shutil
from io import BytesIO
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, today
from frappe.utils.nestedset import get_root_of

from corevatis_erpnext.angebot import textbausteine as tb
from corevatis_erpnext.setup import apply_site_template

FORMAT = "Angebot Corevatis"
FIRMA = "Hülshoff Sanierungstechnik"
KUNDE = "_Test CV43 Familie Feucht"
EINHEIT = "m²"
XSS = "<script>alert(1)</script>"

LANGTEXT = (
	"<div class='ql-editor read-mode'><p>Untergrund vorbereiten: lose und mürbe Putz- und Mörtelteile "
	"bis auf tragfähigen Untergrund entfernen, Fugen 2 cm tief auskratzen, Flächen abfegen und vornässen.</p>"
	"<p>Dichtschlämme zweilagig frisch in frisch auftragen, Verbrauch mind. 4,0 kg/m², Schichtdicke "
	"trocken mind. 2 mm, Hohlkehle am Wand-Boden-Anschluss aus Reparaturmörtel ausbilden.</p>"
	"<p>Einschließlich Abdecken angrenzender Bauteile und Reinigung des Arbeitsbereichs.</p></div>"
)

# (Kurztext, Menge, Preis)
LEISTUNGEN = (
	("Baustelleneinrichtung, An- und Abfahrt", 1, 280.0),
	("Putz abschlagen bis 1,50 m Höhe, Schutt entsorgen", 18.5, 32.5),
	("Innenabdichtung Dichtschlämme 2-lagig bis 1,50 m Höhe", 18.5, 74.0),
	("Hohlkehle aus Reparaturmörtel", 9.2, 28.0),
	("Sanierputz WTA zweilagig, 20 mm", 18.5, 58.0),
	("Horizontalsperre Injektion, Bohrlochabstand 12 cm", 9.2, 145.0),
	("Bodenabdichtung mit Reaktivabdichtung 2-lagig", 12.0, 89.0),
	("Kontrollmessung Mauerfeuchte nach 3 Monaten", 1, 145.0),
)

RAEUME = ("Keller 1", "Keller 2", "Heizungsraum", "Treppenhaus", "Waschküche")
BAUTEILE = ("Außenwand innen", "Innenwand", "Boden")


def wkhtmltopdf_vorhanden() -> bool:
	return bool(shutil.which("wkhtmltopdf"))


def felder_sicherstellen() -> None:
	"""Custom Fields aus 4.1 (corevatis_erpnext.angebot.felder)."""
	from corevatis_erpnext.angebot.felder import ensure_angebot_felder

	ensure_angebot_felder()


class AngebotsdruckBasis(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		# in der CI schon per bench execute gelaufen, lokal der erste Lauf
		apply_site_template(template="huelshoff")
		felder_sicherstellen()
		tb.ensure_textbausteine()
		cls.company = FIRMA
		if not frappe.db.exists("UOM", EINHEIT):
			frappe.get_doc({"doctype": "UOM", "uom_name": EINHEIT, "must_be_whole_number": 0}).insert(
				ignore_permissions=True
			)
		cls.kunde = cls._kunde()
		cls.items = [cls._item(i, text) for i, (text, _menge, _preis) in enumerate(LEISTUNGEN)]
		frappe.db.commit()

	@classmethod
	def _kunde(cls) -> str:
		if not frappe.db.exists("Customer", KUNDE):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": KUNDE,
					"customer_type": "Individual",
					"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}),
					"territory": frappe.db.get_value("Territory", {"is_group": 0})
					or get_root_of("Territory"),
				}
			).insert(ignore_permissions=True)
			frappe.get_doc(
				{
					"doctype": "Address",
					"address_title": KUNDE,
					"address_type": "Billing",
					"address_line1": "Musterweg 3",
					"pincode": "47877",
					"city": "Willich",
					"country": "Germany",
					"is_primary_address": 1,
					"links": [{"link_doctype": "Customer", "link_name": KUNDE}],
				}
			).insert(ignore_permissions=True)
		return KUNDE

	@classmethod
	def _item(cls, index: int, kurztext: str) -> str:
		code = f"_TEST-CV43-{index + 1:02d}"
		if not frappe.db.exists("Item", code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_name": kurztext,
					"description": LANGTEXT,
					"item_group": frappe.db.get_value("Item Group", {"is_group": 0}),
					"stock_uom": EINHEIT,
					"is_stock_item": 0,
				}
			).insert(ignore_permissions=True)
		return code

	def angebot(self, positionen: list[dict], **werte) -> "frappe.Document":
		from erpnext.controllers.accounts_controller import get_taxes_and_charges

		doc = frappe.get_doc(
			{
				"doctype": "Quotation",
				"quotation_to": "Customer",
				"party_name": self.kunde,
				"company": self.company,
				"transaction_date": today(),
				"valid_till": add_days(today(), 30),
				"currency": "EUR",
				"conversion_rate": 1,
				"selling_price_list": frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}),
				"price_list_currency": "EUR",
				"plc_conversion_rate": 1,
				"letter_head": frappe.db.get_value("Company", self.company, "default_letter_head"),
				"items": positionen,
				**werte,
			}
		)
		vorlage = frappe.db.get_value(
			"Sales Taxes and Charges Template", {"company": self.company, "is_default": 1}
		)
		doc.taxes_and_charges = vorlage
		for row in get_taxes_and_charges("Sales Taxes and Charges Template", vorlage):
			doc.append("taxes", row)
		doc.insert(ignore_permissions=True)
		return doc

	def position(self, index: int, raum=None, bauteil=None, **extra) -> dict:
		_kurztext, menge, preis = LEISTUNGEN[index % len(LEISTUNGEN)]
		return {
			"item_code": self.items[index % len(LEISTUNGEN)],
			"qty": menge,
			"rate": preis,
			"uom": EINHEIT,
			"cv_raum": raum,
			"cv_bauteil": bauteil,
			**extra,
		}


class TestTextbausteine(AngebotsdruckBasis):
	def test_angelegt_und_idempotent(self):
		for titel in tb.TEXTBAUSTEINE:
			self.assertTrue(frappe.db.exists("Terms and Conditions", titel), titel)
		self.assertEqual(tb.ensure_textbausteine(), [])

	def test_aenderung_im_desk_bleibt(self):
		doc = frappe.get_doc("Terms and Conditions", tb.GARANTIE_STANDARD)
		alt = doc.terms
		doc.terms = "<p>Eigener Text des Betriebs</p>"
		doc.save(ignore_permissions=True)
		try:
			self.assertEqual(tb.ensure_textbausteine(), [])
			self.assertIn("Eigener Text", frappe.db.get_value("Terms and Conditions", doc.name, "terms"))
		finally:
			frappe.db.set_value("Terms and Conditions", doc.name, "terms", alt)

	def test_firma_hat_zahlungsbedingungen(self):
		self.assertEqual(
			frappe.db.get_value("Company", self.company, "default_selling_terms"), tb.ANGEBOT_STANDARD
		)

	def test_neues_angebot_bekommt_texte(self):
		doc = self.angebot([self.position(0)])
		self.assertEqual(doc.tc_name, tb.ANGEBOT_STANDARD)
		self.assertIn("Zahlungsbedingungen", doc.terms)
		self.assertIn("DIN 18533", doc.cv_vorbemerkung)
		self.assertIn("§ 634a BGB", doc.cv_garantiehinweis)
		self.assertNotIn("<p>", doc.cv_garantiehinweis)

	def test_vorhandene_texte_bleiben(self):
		doc = self.angebot(
			[self.position(0)],
			cv_vorbemerkung="<p>Eigene Vorbemerkung</p>",
			cv_garantiehinweis="Eigener Hinweis",
		)
		self.assertEqual(doc.cv_vorbemerkung, "<p>Eigene Vorbemerkung</p>")
		self.assertEqual(doc.cv_garantiehinweis, "Eigener Hinweis")


class TestSummen(AngebotsdruckBasis):
	def test_eventual_und_alternativ_nicht_in_summe(self):
		doc = self.angebot(
			[
				self.position(0),
				self.position(1, cv_eventualposition=1),
				self.position(2, cv_alternativposition=1),
			]
		)
		self.assertEqual([row.is_alternative for row in doc.items], [0, 1, 1])
		self.assertEqual(flt(doc.net_total), flt(doc.items[0].amount))
		self.assertEqual(flt(doc.grand_total), flt(doc.net_total * 1.19, 2))


class TestStandardformat(AngebotsdruckBasis):
	def test_angebot_corevatis_ist_standard(self):
		if not frappe.db.exists("Print Format", FORMAT):
			self.skipTest("erpnext_druckformate ohne „Angebot Corevatis“")
		self.assertEqual(frappe.get_meta("Quotation").default_print_format, FORMAT)

	def test_nur_freigegebene_formate_werden_ersetzt(self):
		from corevatis_erpnext.setup.site_template import Report, _ensure_print_formats

		if not frappe.db.exists("Print Format", FORMAT):
			self.skipTest("erpnext_druckformate ohne „Angebot Corevatis“")
		eigenes = frappe.copy_doc(frappe.get_doc("Print Format", "Angebot"))
		eigenes.name = eigenes.__newname = "_Test CV43 eigenes Angebot"
		eigenes.insert(ignore_permissions=True, ignore_if_duplicate=True)
		from frappe.custom.doctype.property_setter.property_setter import make_property_setter

		try:
			make_property_setter(
				"Quotation", None, "default_print_format", eigenes.name, "Data", for_doctype=True
			)
			frappe.clear_cache(doctype="Quotation")
			report = Report()
			cfg = {
				"print_formats": {"Quotation": FORMAT},
				"print_formats_replace": {"Quotation": ["Angebot"]},
			}
			_ensure_print_formats(cfg, report)
			self.assertEqual(frappe.get_meta("Quotation").default_print_format, eigenes.name)
			self.assertTrue(any("eigenen Standard" in s for s in report.skipped), report.skipped)

			make_property_setter(
				"Quotation", None, "default_print_format", "Angebot", "Data", for_doctype=True
			)
			frappe.clear_cache(doctype="Quotation")
			_ensure_print_formats(cfg, Report())
			self.assertEqual(frappe.get_meta("Quotation").default_print_format, FORMAT)
		finally:
			make_property_setter("Quotation", None, "default_print_format", FORMAT, "Data", for_doctype=True)
			frappe.clear_cache(doctype="Quotation")


class TestAngebotsdruck(AngebotsdruckBasis):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.muster = os.environ.get("CV_MUSTER_DIR")

	def setUp(self):
		if not frappe.db.exists("Print Format", FORMAT):
			self.skipTest("erpnext_druckformate ohne „Angebot Corevatis“")

	def html(self, doc) -> str:
		return frappe.get_print("Quotation", doc.name, print_format=FORMAT, doc=doc)

	def pdf(self, doc, name: str):
		if not wkhtmltopdf_vorhanden():
			self.skipTest("wkhtmltopdf fehlt")
		from pypdf import PdfReader

		inhalt = frappe.get_print("Quotation", doc.name, print_format=FORMAT, doc=doc, as_pdf=True)
		if self.muster:
			Path(self.muster).mkdir(parents=True, exist_ok=True)
			(Path(self.muster) / f"angebot-{name}.pdf").write_bytes(inhalt)
		reader = PdfReader(BytesIO(inhalt))
		texte = [page.extract_text() or "" for page in reader.pages]
		return len(reader.pages), texte

	def pruefe(self, doc, seiten: int, texte: list[str]):
		alles = "\n".join(texte)
		self.assertNotIn("None", alles)
		self.assertIn(f"Seite 1 von {seiten}", texte[0].replace("\n", " "))
		self.assertIn(f"Seite {seiten} von {seiten}", texte[-1].replace("\n", " "))
		self.assertIn(doc.get_formatted("net_total"), alles)
		self.assertIn(doc.get_formatted("grand_total"), alles)
		self.assertIn(doc.taxes[0].get_formatted("tax_amount"), alles)
		self.assertIn(FIRMA, alles)

	def test_kurz_eine_seite(self):
		doc = self.angebot([self.position(i) for i in (0, 2, 7)])
		html = self.html(doc)
		self.assertNotIn("None", html)
		self.assertNotIn("Zusammenstellung", html)
		self.assertIn(">1</td>", html.replace(" ", "").replace("\n", ""))
		seiten, texte = self.pdf(doc, "kurz")
		self.assertEqual(seiten, 1, texte)
		self.pruefe(doc, 1, texte)
		self.assertIn("Angebot", texte[0])
		self.assertIn("Musterweg 3", texte[0])

	def test_mittel_zwei_seiten_mit_gruppen(self):
		positionen = [self.position(i, "Keller 1", "Außenwand innen") for i in range(5)]
		positionen += [self.position(i, "Keller 2", "Boden") for i in (1, 6)]
		positionen.append(self.position(7, "Keller 2", "Boden", cv_eventualposition=1))
		doc = self.angebot(positionen, cv_titel="Kellerabdichtung von innen")
		self.assertEqual(flt(doc.net_total), flt(sum(r.amount for r in doc.items if not r.is_alternative)))
		html = self.html(doc)
		self.assertNotIn("None", html)
		for teil in (
			"1.1",
			"1.5",
			"2.1",
			"2.3",
			"Keller 1 \u2013 Außenwand innen",
			"Zusammenstellung",
			"Eventualposition",
		):
			self.assertIn(teil, html)
		seiten, texte = self.pdf(doc, "mittel")
		self.assertEqual(seiten, 2, [t[-300:] for t in texte])
		self.pruefe(doc, 2, texte)
		self.assertIn("Kellerabdichtung von innen", texte[0])

	def test_lang_sechs_seiten(self):
		positionen = []
		for g in range(6):
			raum, bauteil = RAEUME[g % len(RAEUME)], BAUTEILE[g % len(BAUTEILE)]
			positionen += [self.position(g + i, raum, bauteil) for i in range(5)]
		positionen.append(self.position(5, "Waschküche", "Boden", cv_eventualposition=1))
		positionen.append(self.position(6, "Waschküche", "Boden", cv_alternativposition=1))
		doc = self.angebot(
			positionen, cv_titel="Abdichtung Keller und Erdgeschoss", additional_discount_percentage=3
		)
		self.assertEqual(len(doc.items), 32)
		seiten, texte = self.pdf(doc, "lang")
		self.assertEqual(seiten, 6, [t[-300:] for t in texte])
		self.pruefe(doc, 6, texte)
		self.assertIn("Nachlass", "\n".join(texte))
		# Positionen werden nicht getrennt: jede Kurztext-Zeile steht mit ihrem Preis auf einer Seite
		for row in doc.items:
			self.assertTrue(any(row.item_name[:30] in t for t in texte), row.item_name)

	def test_maskierung(self):
		img = "<img src=x onerror=alert(1)>"
		doc = self.angebot(
			[self.position(0, f"Keller {XSS}", "Boden"), self.position(1)],
			cv_titel=f"Titel {XSS}",
			cv_vorbemerkung=f"Vorbemerkung ohne führendes Tag {img}{XSS}",
			cv_garantiehinweis=f"Hinweis {XSS}",
		)
		doc.items[0].description = f"<p>Langtext</p>{XSS}{img}"
		doc.items[1].description = '{"a": "<script>alert(1)</script>"}'
		doc.terms = f"Bedingungen {img}"
		html = self.html(doc)
		for verboten in (XSS, "<img", "onerror", '"<script>'):
			self.assertNotIn(verboten, html)
		self.assertIn("&lt;script&gt;", html)
		for text in ("Langtext", "Vorbemerkung ohne führendes Tag", "Bedingungen", "{&quot;a&quot;"):
			self.assertIn(text, html)
