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
XSS = "<script>alert(1)</script>"
PRAEFIX = "_T43-"

# Zusatz für „lange Positionen“ (Muster lang): ausführlicher Langtext wie aus dem Text-Editor
LANGTEXT = (
	"<div class='ql-editor read-mode'><p>Untergrund vorbereiten: lose und mürbe Putz- und Mörtelteile "
	"bis auf tragfähigen Untergrund entfernen, Fugen 2 cm tief auskratzen, Flächen abfegen und vornässen.</p>"
	"<p>Einschließlich Abdecken angrenzender Bauteile und Reinigung des Arbeitsbereichs.</p></div>"
)

RAEUME = ("Keller 1", "Keller 2", "Heizungsraum", "Treppenhaus", "Waschküche", "Vorratsraum")
BAUTEILE = ("Außenwand innen", "Innenwand", "Boden")


def katalog() -> dict[str, dict]:
	"""Verkaufspositionen des Katalogs Abdichtung (Kurztext, Langtext, Einheit, Preis)."""
	from corevatis_erpnext.katalog.importer import lese_csv, paket_dir

	def zeilen(datei):
		header, rows = lese_csv(paket_dir("abdichtung") / datei)
		return [dict(zip(header, row, strict=True)) for row in rows]

	preise = {z["item_code"]: flt(z["price_list_rate"]) for z in zeilen("07_item_price.csv")}
	return {
		z["item_code"]: {**z, "rate": preise.get(z["item_code"], 0)}
		for z in zeilen("06_item.csv")
		if z["is_sales_item"] == "1"
	}


def einheiten() -> dict[str, dict]:
	from corevatis_erpnext.katalog.importer import lese_csv, paket_dir

	header, rows = lese_csv(paket_dir("abdichtung") / "01_uom.csv")
	return {row[0]: dict(zip(header, row, strict=True)) for row in rows}


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
		# Druck und Template setzen frappe.local.lang auf Deutsch; andere Tests (Data-Import-
		# Meldungen in test_katalog) erwarten die Sprache, mit der sie gestartet sind
		cls._lang = frappe.local.lang
		frappe.set_user("Administrator")
		# in der CI schon per bench execute gelaufen, lokal der erste Lauf
		apply_site_template(template="huelshoff")
		felder_sicherstellen()
		tb.ensure_textbausteine()
		cls.company = FIRMA
		cls.katalog = katalog()
		uoms = einheiten()
		for code, zeile in cls.katalog.items():
			uom = zeile["stock_uom"]
			if not frappe.db.exists("UOM", uom):
				# wie die Katalogvorlage 01_uom.csv (test_katalog gleicht vorhandene Einheiten ab)
				frappe.get_doc(
					{
						"doctype": "UOM",
						"uom_name": uom,
						"must_be_whole_number": int(uoms[uom]["must_be_whole_number"]),
						"common_code": uoms[uom]["common_code"],
						"description": uoms[uom]["description"],
					}
				).insert(ignore_permissions=True)
			cls._item(code, zeile)
		cls.kunde = cls._kunde()
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.local.lang = cls._lang
		super().tearDownClass()

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
	def _item(cls, code: str, zeile: dict) -> str:
		"""Testartikel mit Text und Einheit der Katalogposition (eigener Code: der Katalog-Import
		in test_katalog bleibt unberührt)."""
		name = PRAEFIX + code
		if not frappe.db.exists("Item", name):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": name,
					"item_name": zeile["item_name"],
					"description": zeile["description"],
					"item_group": frappe.db.get_value("Item Group", {"is_group": 0}),
					"stock_uom": zeile["stock_uom"],
					"is_stock_item": 0,
				}
			).insert(ignore_permissions=True)
		return name

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

	def position(self, code: str, menge: float, raum=None, bauteil=None, lang=False, **extra) -> dict:
		zeile = self.katalog[code]
		werte = {
			"item_code": PRAEFIX + code,
			"qty": menge,
			"rate": zeile["rate"],
			"uom": zeile["stock_uom"],
			"cv_raum": raum,
			"cv_bauteil": bauteil,
			**extra,
		}
		if lang:
			werte["description"] = f"<p>{zeile['description']}</p>{LANGTEXT}"
		return werte


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

	def test_alte_fassung_angebot_standard_wird_ersetzt(self):
		# 4.1 hatte „Angebot Standard“ als Vorbemerkung angelegt
		name = tb.ANGEBOT_STANDARD
		frappe.db.set_value("Terms and Conditions", name, "terms", tb.ALTE_FASSUNGEN[name])
		self.assertEqual(tb.ensure_textbausteine(), [name])
		self.assertIn("Zahlungsbedingungen", frappe.db.get_value("Terms and Conditions", name, "terms"))
		self.assertEqual(tb.ensure_textbausteine(), [])

	def test_firma_hat_zahlungsbedingungen(self):
		self.assertEqual(
			frappe.db.get_value("Company", self.company, "default_selling_terms"), tb.ANGEBOT_STANDARD
		)

	def test_neues_angebot_bekommt_texte(self):
		doc = self.angebot([self.position("IA-05", 18)])
		self.assertEqual(doc.tc_name, tb.ANGEBOT_STANDARD)
		self.assertIn("Zahlungsbedingungen", doc.terms)
		self.assertIn("DIN 18533", doc.cv_vorbemerkung)
		self.assertIn("§ 634a BGB", doc.cv_garantiehinweis)
		self.assertNotIn("<p>", doc.cv_garantiehinweis)

	def test_vorhandene_texte_bleiben(self):
		doc = self.angebot(
			[self.position("IA-05", 18)],
			cv_vorbemerkung="<p>Eigene Vorbemerkung</p>",
			cv_garantiehinweis="Eigener Hinweis",
		)
		self.assertEqual(doc.cv_vorbemerkung, "<p>Eigene Vorbemerkung</p>")
		self.assertEqual(doc.cv_garantiehinweis, "Eigener Hinweis")


class TestSummen(AngebotsdruckBasis):
	def test_eventual_und_alternativ_nicht_in_summe(self):
		doc = self.angebot(
			[
				self.position("IA-05", 18),
				self.position("HS-02", 9, cv_eventualposition=1),
				self.position("IA-06", 18, cv_alternativposition=1),
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
		# der Druck setzt frappe.local.lang auf die Drucksprache; andere Tests erwarten Englisch
		self.lang = frappe.local.lang
		self.addCleanup(setattr, frappe.local, "lang", self.lang)

	def html(self, doc) -> str:
		return frappe.get_print("Quotation", doc.name, print_format=FORMAT, doc=doc)

	def pdf(self, doc, name: str | None = None):
		"""PDF rendern; Rückgabe (Seitenzahl, Text je Seite, PDF). Mit ``name`` als Muster speichern."""
		if not wkhtmltopdf_vorhanden():
			self.skipTest("wkhtmltopdf fehlt")
		from pypdf import PdfReader

		inhalt = frappe.get_print("Quotation", doc.name, print_format=FORMAT, doc=doc, as_pdf=True)
		if name:
			self.muster_speichern(name, inhalt)
		reader = PdfReader(BytesIO(inhalt))
		return len(reader.pages), [page.extract_text() or "" for page in reader.pages], inhalt

	def muster_speichern(self, name: str, inhalt: bytes) -> None:
		if self.muster:
			Path(self.muster).mkdir(parents=True, exist_ok=True)
			(Path(self.muster) / f"angebot-{name}.pdf").write_bytes(inhalt)

	def auffuellen(self, baue, seiten: int, name: str, von: int, bis: int):
		"""Größtes Angebot ``baue(n)`` (n = von … bis), das genau ``seiten`` Seiten hat.

		Die Seitenzahl hängt an Schriften und wkhtmltopdf-Version der Umgebung; statt einer
		festen Positionszahl wird aufgefüllt, bis die nächste Position eine Seite mehr bringt.
		Eine Position mehr ergibt höchstens eine Seite mehr, ``seiten`` wird also getroffen."""
		treffer = None
		for anzahl in range(von, bis + 1):
			doc = baue(anzahl)
			anzahl_seiten, texte, inhalt = self.pdf(doc)
			if anzahl_seiten > seiten:
				break
			if anzahl_seiten == seiten:
				treffer = (doc, texte, inhalt)
		self.assertIsNotNone(treffer, f"kein Angebot mit {seiten} Seiten ({von}-{bis} Positionen)")
		doc, texte, inhalt = treffer
		self.muster_speichern(name, inhalt)
		return doc, texte

	def pruefe(self, doc, seiten: int, texte: list[str]):
		self.assertEqual(len(texte), seiten)
		alles = "\n".join(texte)
		self.assertNotIn("None", alles)
		self.assertIn(f"Seite 1 von {seiten}", texte[0].replace("\n", " "))
		self.assertIn(f"Seite {seiten} von {seiten}", texte[-1].replace("\n", " "))
		self.assertIn(doc.get_formatted("net_total"), alles)
		self.assertIn(doc.get_formatted("grand_total"), alles)
		self.assertIn(doc.taxes[0].get_formatted("tax_amount"), alles)
		self.assertIn(FIRMA, alles)
		# Summen: Netto = Positionen ohne Eventual-/Alternativpositionen (- Nachlass)
		self.assertEqual(flt(doc.total, 2), flt(sum(r.amount for r in doc.items if not r.is_alternative), 2))
		self.assertEqual(flt(doc.net_total, 2), flt(doc.total - flt(doc.discount_amount), 2))
		self.assertEqual(flt(doc.grand_total, 2), flt(doc.net_total + doc.total_taxes_and_charges, 2))
		# jede Position steht vollständig auf einer Seite (Kurztext und Preis zusammen)
		for row in doc.items:
			self.assertTrue(
				any(row.item_name[:25] in t and row.get_formatted("rate") in t for t in texte), row.item_name
			)

	def test_kurz_eine_seite(self):
		# kurzes Angebot (Feuchtemessung): kurze Vorbemerkung und Gewährleistung
		doc = self.angebot(
			[self.position("AL-01", 1), self.position("AL-03", 4)],
			cv_vorbemerkung="<p>Sehr geehrte Frau Feucht, wie besprochen bieten wir Ihnen an:</p>",
			cv_garantiehinweis="Gewährleistung nach den gesetzlichen Bestimmungen (§ 634a BGB).",
		)
		html = self.html(doc)
		self.assertNotIn("None", html)
		self.assertNotIn("Zusammenstellung", html)
		_seiten, texte, _pdf = self.pdf(doc, "kurz")
		self.pruefe(doc, 1, texte)
		self.assertIn("Musterweg 3", texte[0])
		self.assertIn("Angebot", texte[0])

	def positionen_mittel(self, anzahl: int) -> list[dict]:
		"""Allgemeine Leistungen, ``anzahl`` Positionen Wand, zwei Boden, eine Eventualposition."""
		wand, boden = ("Keller 1", "Außenwand innen"), ("Keller 2", "Boden")
		codes = ("IA-01", "IA-02", "IA-03", "IA-04", "IA-05", "IA-07", "SP-06", "HS-02")
		positionen = [self.position("AL-01", 1)]
		positionen += [self.position(codes[i % len(codes)], 18.5, *wand) for i in range(anzahl)]
		positionen += [self.position("IA-08", 12, *boden), self.position("IA-04", 14, *boden)]
		positionen.append(self.position("HS-02", 9.2, *wand, cv_eventualposition=1))
		return positionen

	def test_mittel_zwei_seiten_mit_gruppen(self):
		doc, texte = self.auffuellen(
			lambda n: self.angebot(self.positionen_mittel(n), cv_titel="Kellerabdichtung von innen"),
			2,
			"mittel",
			1,
			20,
		)
		self.pruefe(doc, 2, texte)
		html = self.html(doc)
		self.assertNotIn("None", html)
		for teil in (
			"1.1",
			"2.1",
			"3.2",
			"Keller 1 \u2013 Außenwand innen",
			"Zusammenstellung",
			"Eventualposition",
		):
			self.assertIn(teil, html)
		self.assertIn("Kellerabdichtung von innen", texte[0])

	def positionen_lang(self, anzahl: int) -> list[dict]:
		"""Allgemeine Leistungen, dann ``anzahl`` lange Positionen in Gruppen à 5 (Raum/Bauteil),
		zuletzt eine Eventual- und eine Alternativposition."""
		codes = ("IA-01", "IA-02", "IA-03", "IA-05", "IA-07", "SP-03", "SP-04", "SP-05", "SP-06", "HS-02")
		positionen = [self.position("AL-01", 1), self.position("AL-02", 6)]
		for i in range(anzahl):
			g = i // 5
			raum, bauteil = RAEUME[g % len(RAEUME)], BAUTEILE[g % len(BAUTEILE)]
			positionen.append(self.position(codes[i % len(codes)], 12.5 + g, raum, bauteil, lang=True))
		positionen.append(self.position("IA-09", 12, "Waschküche", "Boden", lang=True, cv_eventualposition=1))
		positionen.append(
			self.position("IA-06", 12, "Waschküche", "Boden", lang=True, cv_alternativposition=1)
		)
		return positionen

	def test_lang_sechs_seiten(self):
		doc, texte = self.auffuellen(
			lambda n: self.angebot(
				self.positionen_lang(n),
				cv_titel="Abdichtung Keller und Erdgeschoss",
				additional_discount_percentage=3,
			),
			6,
			"lang",
			8,
			80,
		)
		self.pruefe(doc, 6, texte)
		alles = "\n".join(texte)
		self.assertIn("Nachlass", alles)
		self.assertIn("Zusammenstellung", alles)
		self.assertIn("Alternativposition", alles)

	def test_maskierung(self):
		img = "<img src=x onerror=alert(1)>"
		doc = self.angebot(
			[self.position("IA-05", 18, f"Keller {XSS}", "Boden"), self.position("IA-01", 18)],
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
