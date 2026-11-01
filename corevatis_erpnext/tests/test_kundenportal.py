"""Kundenportal (P2.7, Handwerker#91): Zugang per Token, Kundenseite, Terminbestätigung, Annahme mit
Unterschrift, Fotoupload, Dokumente/Download, Rechnungen, Rate-Limit.

Die CI-Bench dieses Repos hat ``corevatis`` nicht: keine Mappenfelder am File (``cv_dokumentart`` …) -
Dokumente erkennt der Adapter dann als PDF am gebuchten Beleg, Freigaben über ``File.cv_kunde_sichtbar``.
Angebot, Annahme, Auftrag, Projekt, Rechnung sind echt (ERPNext).
"""

from __future__ import annotations

import base64
import io
import os
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, now_datetime, today

from corevatis_erpnext import hooks, kundenportal
from corevatis_erpnext.kundenportal import aktionen, felder, vorgang, zugang
from corevatis_erpnext.prozess.kette import status
from corevatis_erpnext.tests import _abrechnung
from corevatis_erpnext.tests import _angebot as td
from corevatis_erpnext.tests._helpers import make_user

BUERO = "cv-kp-buero@example.com"
MEISTER = "cv-kp-meister@example.com"
MONTEUR = "cv-kp-monteur@example.com"


def _als(user: str, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def bild_bytes(format: str = "JPEG", breite: int = 240, hoehe: int = 80) -> bytes:
	"""Echtes Bild mit Rauschen (Frappe liest Bilder beim Speichern mit Pillow; Rauschen komprimiert nicht weg)."""
	from PIL import Image

	bild = Image.frombytes("L", (breite, hoehe), os.urandom(breite * hoehe))
	puffer = io.BytesIO()
	bild.save(puffer, format=format)
	return puffer.getvalue()


def pdf_bytes(text: str = "Kundenportal Test") -> bytes:
	"""Kleinstes gültiges PDF (Frappe prüft PDFs beim Speichern mit pypdf), nur ASCII."""
	inhalt = f"BT /F1 12 Tf 20 50 Td ({text}) Tj ET".encode()
	objekte = [
		b"<< /Type /Catalog /Pages 2 0 R >>",
		b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
		b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Contents 4 0 R "
		b"/Resources << /Font << /F1 5 0 R >> >> >>",
		b"<< /Length " + str(len(inhalt)).encode() + b" >>\nstream\n" + inhalt + b"\nendstream",
		b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
	]
	aus = bytearray(b"%PDF-1.4\n")
	offsets = []
	for i, o in enumerate(objekte, 1):
		offsets.append(len(aus))
		aus += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
	xref = len(aus)
	aus += f"xref\n0 {len(objekte) + 1}\n".encode() + b"0000000000 65535 f \n"
	for off in offsets:
		aus += f"{off:010d} 00000 n \n".encode()
	aus += f"trailer\n<< /Size {len(objekte) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
	return bytes(aus)


PDF = pdf_bytes()
PDF_INTERN = pdf_bytes("intern")
JPEG = bild_bytes("JPEG")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_bytes(breite: int = 240, hoehe: int = 80) -> bytes:
	return bild_bytes("PNG", breite, hoehe)


def data_url(inhalt: bytes, mime: str) -> str:
	return f"data:{mime};base64," + base64.b64encode(inhalt).decode()


def _token(r: dict) -> str:
	return r["url"].rsplit("/", 1)[-1]


class TestKundenportal(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(
			BUERO, "Bea", ["Büro", "Sales User", "Accounts User", "Projects User", "Projects Manager"], "Büro"
		)
		make_user(MEISTER, "Max", ["Meister", "Sales User", "Projects User"], "Meister")
		make_user(MONTEUR, "Moritz", ["Monteur"], "Monteur")
		td.ensure_felder_41()
		td.ensure_artikel()
		from corevatis_erpnext.textbausteine import ensure_setup as tb_setup

		tb_setup()
		kundenportal.ensure_setup()
		kundenportal.ensure_setup()  # idempotent

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.local.response = frappe._dict()
		for schluessel in ("seite:lokal", "fehl:lokal"):
			zugang.zaehler_loeschen(schluessel)
		self.lead = td.lead_mit_angebot_status()
		self.angebot = td.angebot(self.lead.name, zusatz=False)
		self.termin = self._termin(self.lead)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.response = frappe._dict()

	def _termin(self, lead, tage: int = 3):
		event = frappe.get_doc(
			{
				"doctype": "Event",
				"subject": f"Besichtigung {lead.name}",
				"event_type": "Public",
				"event_category": "Meeting",
				"status": "Open",
				"starts_on": add_to_date(now_datetime(), days=tage),
				"ends_on": add_to_date(now_datetime(), days=tage, hours=1),
				"event_participants": [{"reference_doctype": "Lead", "reference_docname": lead.name}],
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "ToDo",
				"allocated_to": MEISTER,
				"description": "Besichtigung",
				"reference_type": "Event",
				"reference_name": event.name,
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Lead", lead.name, "cv_termin", event.name)
		return event

	def _zugang(self, doctype: str | None = None, name: str | None = None) -> tuple[frappe._dict, str]:
		r = _als(BUERO, kundenportal.zugang_anlegen, doctype or "Lead", name or self.lead.name)
		token = _token(r)
		z = zugang.pruefen(token)
		self.assertIsNotNone(z, r)
		zugang.zaehler_loeschen(f"aktion:{z.name}")
		return z, token

	# --- Einrichtung ------------------------------------------------------------------------------

	def test_felder_und_einrichtung(self):
		for doctype, feld in (
			("Event", felder.FELD_TERMIN_BESTAETIGT),
			("Event", felder.FELD_TERMIN_ZUGANG),
			("Quotation", felder.FELD_ANGEBOT_ANGENOMMEN),
			("Quotation", felder.FELD_ANGEBOT_UNTERSCHRIFT),
			("File", felder.FELD_DATEI_SICHTBAR),
		):
			with self.subTest(doctype=doctype, feld=feld):
				self.assertTrue(frappe.get_meta(doctype).has_field(feld))
		self.assertTrue(frappe.get_meta("Quotation").get_field(felder.FELD_ANGEBOT_ANGENOMMEN).read_only)
		self.assertTrue(frappe.db.exists("DocType", "Kundenzugang"))
		self.assertTrue(frappe.db.exists("Terms and Conditions", kundenportal.BEGRUESSUNG_TITEL))
		self.assertIn("Kundenportal", frappe.get_meta("Terms and Conditions").get_options("cv_art"))
		self.assertEqual(hooks.website_route_rules, [{"from_route": "/kunde/<token>", "to_route": "kunde"}])
		for dt in ("Lead", "Customer", "Project", "Quotation", "Kundenzugang"):
			self.assertIn("public/js/kundenzugang.js", hooks.doctype_js[dt], dt)
		import json
		import pathlib

		leiste = json.loads(
			(pathlib.Path(__file__).parents[1] / "corevatis_erpnext/sidebar/betrieb/betrieb.json").read_text()
		)
		self.assertIn(
			("Kundenzugänge", "Kundenzugang"), [(i.get("label"), i.get("link_to")) for i in leiste["items"]]
		)

	# --- Zugang -----------------------------------------------------------------------------------

	def test_zugang_anlegen_pruefen_widerrufen(self):
		r = _als(BUERO, kundenportal.zugang_anlegen, "Lead", self.lead.name)
		token = _token(r)
		self.assertIn("/kunde/", r["url"])
		self.assertTrue(zugang.TOKEN_MUSTER.match(token), token)
		self.assertIn("<svg", r["qr_svg"])
		doc = frappe.get_doc("Kundenzugang", r["name"])
		self.assertEqual(
			(doc.anker_doctype, doc.anker, doc.vorgang), ("Lead", self.lead.name, self.lead.name)
		)
		self.assertEqual(doc.token_hash, zugang.token_hash(token))
		self.assertNotIn(token, frappe.as_json(doc.as_dict()))
		self.assertEqual(str(doc.gueltig_bis), add_days(today(), zugang.GUELTIG_TAGE))
		self.assertEqual(doc.kunde_name, self.lead.lead_name)
		# prüfen: richtig, falsch, zu kurz, Müll
		self.assertEqual(zugang.pruefen(token).name, doc.name)
		self.assertIsNone(zugang.pruefen(token[:-1] + ("a" if token[-1] != "a" else "b")))
		self.assertIsNone(zugang.pruefen("kurz"))
		self.assertIsNone(zugang.pruefen(None))
		self.assertIsNone(zugang.pruefen("x" * 30 + "'; drop"))
		# Übersicht ohne Token
		u = _als(BUERO, kundenportal.uebersicht, "Lead", self.lead.name)
		self.assertEqual([z["name"] for z in u["zugaenge"]], [doc.name])
		self.assertNotIn(token, frappe.as_json(u))
		# neuer Link ersetzt den alten
		r2 = _als(BUERO, kundenportal.zugang_anlegen, "Quotation", self.angebot.name)
		self.assertEqual(frappe.get_doc("Kundenzugang", r2["name"]).anker, self.lead.name)
		self.assertIsNone(zugang.pruefen(token))
		self.assertEqual(frappe.db.get_value("Kundenzugang", doc.name, "widerrufen"), 1)
		token2 = _token(r2)
		self.assertIsNotNone(zugang.pruefen(token2))
		# widerrufen und abgelaufen
		_als(BUERO, kundenportal.zugang_widerrufen, r2["name"])
		self.assertIsNone(zugang.pruefen(token2))
		frappe.db.set_value("Kundenzugang", r2["name"], "widerrufen", 0)
		self.assertIsNotNone(zugang.pruefen(token2))
		frappe.db.set_value("Kundenzugang", r2["name"], "gueltig_bis", add_days(today(), -1))
		self.assertIsNone(zugang.pruefen(token2))
		# Rechte: Meister und Monteur legen keinen Zugang an
		for user in (MEISTER, MONTEUR):
			with self.assertRaises(frappe.PermissionError, msg=user):
				_als(user, kundenportal.zugang_anlegen, "Lead", self.lead.name)
		# Formular-Anlage (System Manager): Token entsteht beim Speichern, Link einmal als Meldung
		doc = frappe.get_doc(
			{"doctype": "Kundenzugang", "vorgang_doctype": "Lead", "vorgang": self.lead.name}
		)
		doc.insert()
		self.assertEqual(len(doc.token_hash), 64)
		self.assertTrue(any("/kunde/" in str(m) for m in frappe.get_message_log()), frappe.get_message_log())
		frappe.clear_messages()

	def test_anker_kunde_und_projekt(self):
		auftrag, projekt = _abrechnung_vorgang(self)
		kunde = frappe.db.get_value("Project", projekt, "customer")
		self.assertEqual(zugang.anker_fuer("Customer", kunde)[0], "Lead")
		self.assertEqual(zugang.anker_fuer("Project", projekt), zugang.anker_fuer("Sales Order", auftrag))
		# Projekt ohne Anfrage: Anker ist das Projekt selbst
		p = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": "Ohne Anfrage KP",
				"customer": kunde,
				"company": td.firma(),
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Project", p.name, {"cv_lead": None, "sales_order": None}, update_modified=False)
		frappe.db.set_value("Customer", kunde, "lead_name", None, update_modified=False)
		self.assertEqual(zugang.anker_fuer("Project", p.name), ("Project", p.name))
		with self.assertRaises(zugang.ZugangFehler):
			zugang.anker_fuer("Employee", "x")

	# --- Seite ------------------------------------------------------------------------------------

	def test_seite_und_daten(self):
		from corevatis_erpnext.www import kunde as seite

		z, token = self._zugang()
		d = vorgang.daten(z)
		self.assertEqual(d["kunde"], self.lead.lead_name)
		self.assertEqual(d["anfrage"], self.lead.name)
		self.assertTrue(d["firma"]["anzeige"])
		self.assertIn("Münster", d["objekt"])
		self.assertEqual(d["termin"]["name"], self.termin.name)
		self.assertTrue(d["termin"]["bestaetigbar"])
		self.assertEqual(d["termin"]["meister"], "Max Meister")
		self.assertEqual((d["angebot"]["name"], d["angebot"]["status"]), (self.angebot.name, "offen"))
		self.assertTrue(d["angebot"]["annehmbar"])
		self.assertEqual((d["rechnungen"], d["dokumente"], d["auftrag"], d["projekt"]), ([], [], None, None))
		self.assertIn("hier finden Sie alles", d["begruessung"])
		self.assertIn(d["firma"]["anzeige"], d["begruessung"])
		# Seite: Kontext und Zähler
		frappe.form_dict = frappe._dict(token=token)
		kontext = frappe._dict()
		seite.get_context(kontext)
		self.assertEqual(kontext.daten["zugang"], z.name)
		self.assertEqual(kontext.token, token)
		self.assertEqual(frappe.db.get_value("Kundenzugang", z.name, "aufrufe"), 1)
		# unbekanntes Token: 404
		frappe.form_dict = frappe._dict(token="x" * 40)
		with self.assertRaises(frappe.PageDoesNotExistError):
			seite.get_context(frappe._dict())
		frappe.form_dict = frappe._dict()
		# HTML-Vorlage maskiert Kundendaten (Jinja-Autoescape)
		frappe.db.set_value(
			"Lead", self.lead.name, "lead_name", "<b>Böse</b> Musterfrau", update_modified=False
		)
		frappe.db.set_value("Kundenzugang", z.name, "kunde_name", "<b>Böse</b> Musterfrau")
		vorlage = frappe.get_template("corevatis_erpnext/www/kunde.html")
		html = "".join(
			vorlage.blocks["page_content"](
				vorlage.new_context(
					{
						"daten": vorgang.daten(zugang.pruefen(token)),
						"token": token,
						"api": "/api/method/corevatis_erpnext.kundenportal.aktionen.",
						"datenschutz_url": "/datenschutz",
					}
				)
			)
		)
		self.assertIn("&lt;b&gt;Böse&lt;/b&gt;", html)
		self.assertNotIn("<b>Böse</b>", html)
		self.assertIn("Termin bestätigen", html)
		self.assertIn("Angebot verbindlich annehmen", html)
		self.assertIn("/datenschutz", html)

	# --- Termin -----------------------------------------------------------------------------------

	def test_termin_bestaetigen(self):
		z, token = self._zugang()
		# falsches Token: neutral 404
		r = aktionen.termin_bestaetigen("x" * 40)
		self.assertEqual((r["ok"], frappe.local.response.get("http_status_code")), (False, 404))
		frappe.local.response = frappe._dict()
		frappe.set_user("Guest")
		try:
			r = aktionen.termin_bestaetigen(token)
			self.assertEqual(frappe.session.user, "Guest")
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(r["ok"], r)
		e = frappe.db.get_value(
			"Event",
			self.termin.name,
			[felder.FELD_TERMIN_BESTAETIGT, felder.FELD_TERMIN_ZUGANG],
			as_dict=True,
		)
		self.assertTrue(e[felder.FELD_TERMIN_BESTAETIGT])
		self.assertEqual(e[felder.FELD_TERMIN_ZUGANG], z.name)
		kommentare = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Lead", "reference_name": self.lead.name, "comment_type": "Info"},
			pluck="content",
		)
		self.assertTrue(any("bestätigt" in k and z.name in k for k in kommentare), kommentare)
		# Prozessleiste: Hinweis am Schritt Termin
		stand = _als(BUERO, status, "Lead", self.lead.name)
		termin = next(s for s in stand["schritte"] if s["key"] == "termin")
		self.assertIn("vom Kunden bestätigt", termin["text"])
		# zweiter Klick: schon bestätigt
		r2 = aktionen.termin_bestaetigen(token)
		self.assertTrue(r2["ok"] and r2.get("bereits"))
		self.assertFalse(vorgang.daten(zugang.pruefen(token))["termin"]["bestaetigbar"])
		# vergangener Termin lässt sich nicht bestätigen
		frappe.db.set_value("Event", self.termin.name, {felder.FELD_TERMIN_BESTAETIGT: None})
		frappe.db.set_value("Event", self.termin.name, "starts_on", add_to_date(now_datetime(), days=-2))
		r3 = aktionen.termin_bestaetigen(token)
		self.assertFalse(r3["ok"])

	# --- Angebot ----------------------------------------------------------------------------------

	def test_angebot_annehmen_mit_unterschrift(self):
		z, token = self._zugang()
		png = png_bytes()
		# ohne Haken, ohne/mit ungültiger Unterschrift
		self.assertFalse(aktionen.angebot_annehmen(token, data_url(png, "image/png"), 0)["ok"])
		self.assertFalse(aktionen.angebot_annehmen(token, "", 1)["ok"])
		self.assertFalse(aktionen.angebot_annehmen(token, data_url(JPEG, "image/jpeg"), 1)["ok"])
		self.assertFalse(
			aktionen.angebot_annehmen(token, data_url(b"\x89PNG\r\n\x1a\nkurz", "image/png"), 1)["ok"]
		)
		self.assertEqual(frappe.db.get_value("Quotation", self.angebot.name, "status"), "Open")
		with self.assertRaises(ValueError):
			aktionen.unterschrift_png(data_url(png_bytes(1200, 600), "image/png"))  # > 500 KB
		# gültig, als Gast
		frappe.local.response = frappe._dict()
		frappe.set_user("Guest")
		try:
			r = aktionen.angebot_annehmen(token, data_url(png, "image/png"), "1")
			self.assertEqual(frappe.session.user, "Guest")
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(r["ok"], r)
		self.assertEqual(r["angebot"], self.angebot.name)
		auftrag = frappe.get_doc("Sales Order", r["auftrag"])
		self.assertEqual(auftrag.docstatus, 1)
		self.assertTrue(auftrag.name.startswith("AB-"))
		self.assertEqual(frappe.db.get_value("Quotation", self.angebot.name, "status"), "Ordered")
		projekt = frappe.db.get_value("Project", {"sales_order": auftrag.name}, "name")
		self.assertTrue(projekt)
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_status"), "Gewonnen")
		q = frappe.db.get_value(
			"Quotation",
			self.angebot.name,
			[felder.FELD_ANGEBOT_ANGENOMMEN, felder.FELD_ANGEBOT_UNTERSCHRIFT],
			as_dict=True,
		)
		self.assertTrue(q[felder.FELD_ANGEBOT_ANGENOMMEN])
		datei = frappe.get_doc(
			"File",
			{
				"attached_to_doctype": "Quotation",
				"attached_to_name": self.angebot.name,
				"file_name": ["like", "Unterschrift-%"],
			},
		)
		self.assertEqual((datei.is_private, datei.file_url), (1, q[felder.FELD_ANGEBOT_UNTERSCHRIFT]))
		self.assertTrue(datei.get_content().startswith(PNG_MAGIC))
		self.assertEqual(datei.get(felder.FELD_DATEI_SICHTBAR), 0)
		for doctype, name in (("Quotation", self.angebot.name), ("Sales Order", auftrag.name)):
			kommentare = frappe.get_all(
				"Comment", filters={"reference_doctype": doctype, "reference_name": name}, pluck="content"
			)
			self.assertTrue(
				any("Kundenzugang" in k and z.name in k for k in kommentare), (doctype, kommentare)
			)
		# Seite danach: angenommen, Auftrag, Projekt; Unterschrift nicht in den Dokumenten
		d = vorgang.daten(zugang.pruefen(token))
		self.assertEqual((d["angebot"]["status"], d["angebot"]["annehmbar"]), ("angenommen", False))
		self.assertEqual(d["auftrag"]["name"], auftrag.name)
		self.assertEqual(d["projekt"]["name"], projekt)
		self.assertNotIn(datei.name, [f["name"] for f in d["dokumente"]])
		# zweiter Klick: bereits angenommen, kein zweiter Auftrag
		r2 = aktionen.angebot_annehmen(token, data_url(png, "image/png"), 1)
		self.assertTrue(r2["ok"] and r2.get("bereits"), r2)
		self.assertEqual(
			frappe.db.count("Sales Order Item", {"prevdoc_docname": self.angebot.name, "docstatus": 1}), 1
		)

	def test_angebot_abgelaufen_nicht_annehmbar(self):
		z, token = self._zugang()
		frappe.db.set_value(
			"Quotation", self.angebot.name, "valid_till", add_days(today(), -1), update_modified=False
		)
		d = vorgang.daten(z)
		self.assertEqual((d["angebot"]["status"], d["angebot"]["annehmbar"]), ("abgelaufen", False))
		r = aktionen.angebot_annehmen(token, data_url(png_bytes(), "image/png"), 1)
		self.assertFalse(r["ok"])
		self.assertEqual(frappe.db.get_value("Quotation", self.angebot.name, "status"), "Open")

	# --- Fotos ------------------------------------------------------------------------------------

	def test_foto_hochladen(self):
		z, token = self._zugang()
		self.assertFalse(aktionen.foto_hochladen(token, data_url(PDF, "application/pdf"))["ok"])
		self.assertEqual(frappe.local.response.get("http_status_code"), 415)
		frappe.local.response = frappe._dict()
		self.assertFalse(aktionen.foto_hochladen(token, "")["ok"])
		with patch.object(aktionen, "MAX_FOTO", 1000):
			r = aktionen.foto_hochladen(token, data_url(JPEG, "image/jpeg"))
			self.assertEqual((r["ok"], frappe.local.response.get("http_status_code")), (False, 413))
		frappe.local.response = frappe._dict()
		frappe.set_user("Guest")
		try:
			r = aktionen.foto_hochladen(token, data_url(JPEG, "image/jpeg"))
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(r["ok"], r)
		datei = frappe.get_doc("File", r["datei"])
		self.assertEqual((datei.attached_to_doctype, datei.attached_to_name), ("Lead", self.lead.name))
		self.assertEqual((datei.is_private, datei.get(felder.FELD_DATEI_SICHTBAR)), (1, 1))
		self.assertTrue(datei.file_name.startswith("Kundenfoto-") and datei.file_name.endswith(".jpg"))
		self.assertTrue(datei.get_content().startswith(b"\xff\xd8\xff"))
		self.assertEqual(frappe.db.get_value("Kundenzugang", z.name, "uploads"), 1)
		# erscheint als Dokument und ist herunterladbar
		d = vorgang.daten(zugang.pruefen(token))
		self.assertIn(datei.name, [f["name"] for f in d["dokumente"]])
		self.assertEqual(d["uploads"], 1)
		# Höchstzahl
		frappe.db.set_value("Kundenzugang", z.name, "uploads", zugang.MAX_UPLOADS)
		r = aktionen.foto_hochladen(token, data_url(JPEG, "image/jpeg"))
		self.assertFalse(r["ok"])
		self.assertIn("Höchstzahl", r["meldung"])
		# Kommentar an der Anfrage
		kommentare = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Lead", "reference_name": self.lead.name},
			pluck="content",
		)
		self.assertTrue(any("Foto hochgeladen" in k for k in kommentare), kommentare)

	# --- Dokumente und Download -----------------------------------------------------------------------

	def _datei(self, doctype: str, name: str, dateiname: str, inhalt: bytes = PDF, **werte):
		return frappe.get_doc(
			{
				"doctype": "File",
				"file_name": dateiname,
				"attached_to_doctype": doctype,
				"attached_to_name": name,
				"is_private": 1,
				"content": inhalt,
				**werte,
			}
		).insert(ignore_permissions=True)

	def test_dokumente_und_download(self):
		z, token = self._zugang()
		pdf = self._datei("Quotation", self.angebot.name, f"{self.angebot.name}.pdf")
		intern = self._datei("Lead", self.lead.name, "intern.pdf", PDF_INTERN)
		notiz = self._datei("Quotation", self.angebot.name, "kalkulation.xlsx", b"PK intern")
		d = vorgang.daten(z)
		namen = [f["name"] for f in d["dokumente"]]
		self.assertIn(pdf.name, namen)
		self.assertNotIn(intern.name, namen)
		self.assertNotIn(notiz.name, namen)
		# Freigabe durch das Büro
		frappe.db.set_value("File", intern.name, felder.FELD_DATEI_SICHTBAR, 1)
		self.assertIn(intern.name, [f["name"] for f in vorgang.dokumente(vorgang.kette_fuer(z))])
		# Download: freigegebene Datei, fremde Datei, Angebots-PDF aus der Mappe
		frappe.set_user("Guest")
		try:
			aktionen.datei(token, datei=pdf.name)
			self.assertEqual(frappe.session.user, "Guest")
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(frappe.local.response.filecontent, PDF)
		self.assertEqual(frappe.local.response.type, "pdf")
		self.assertEqual(frappe.local.response.filename, f"{self.angebot.name}.pdf")
		frappe.local.response = frappe._dict()
		aktionen.datei(token, datei=notiz.name)
		self.assertEqual(frappe.local.response.get("http_status_code"), 404)
		self.assertIsNone(frappe.local.response.get("filecontent"))
		frappe.local.response = frappe._dict()
		aktionen.datei(token, beleg="angebot")
		self.assertEqual(frappe.local.response.filecontent, PDF)
		frappe.local.response = frappe._dict()
		aktionen.datei(token, beleg="RE-GIBT-ES-NICHT")
		self.assertEqual(frappe.local.response.get("http_status_code"), 404)
		frappe.local.response = frappe._dict()
		aktionen.datei("x" * 40, datei=pdf.name)
		self.assertEqual(frappe.local.response.get("http_status_code"), 404)

	# --- Rechnungen -------------------------------------------------------------------------------

	def test_rechnungen(self):
		_auftrag, projekt = _abrechnung_vorgang(self)
		z, token = self._zugang("Project", projekt)
		self.assertEqual(vorgang.daten(z)["rechnungen"], [])
		# Abschlagsrechnung über den Abschlagsplan (7.1), freigegeben durch das Büro
		from corevatis_erpnext.abrechnung import abschlag

		_abrechnung.kern_patch(self)
		r = _als(BUERO, abschlag.abschlag_stellen, project=projekt)
		si = frappe.get_doc("Sales Invoice", r["rechnung"])
		_als(BUERO, si.submit)
		d = vorgang.daten(zugang.pruefen(token))
		self.assertEqual(len(d["rechnungen"]), 1)
		z1 = d["rechnungen"][0]
		self.assertEqual(
			(z1["name"], z1["art"], z1["stand"], z1["farbe"]),
			(si.name, "Abschlagsrechnung 1", "offen", "gelb"),
		)
		self.assertIn("178", z1["brutto"])
		pdf = self._datei("Sales Invoice", si.name, f"{si.name}.pdf")
		self.assertIn(pdf.name, [f["name"] for f in vorgang.dokumente(vorgang.kette_fuer(z))])
		frappe.local.response = frappe._dict()
		aktionen.datei(token, beleg=si.name)
		self.assertEqual(frappe.local.response.filecontent, PDF)
		# bezahlt: Stand grün
		frappe.db.set_value("Sales Invoice", si.name, "outstanding_amount", 0, update_modified=False)
		self.assertEqual(vorgang.daten(zugang.pruefen(token))["rechnungen"][0]["stand"], "bezahlt")

	# --- Rate-Limit -------------------------------------------------------------------------------

	def test_rate_limit(self):
		z, token = self._zugang()
		with patch.object(zugang, "LIMIT_FEHL_IP", 3):
			for _i in range(3):
				self.assertIsNone(zugang.anfrage_pruefen("x" * 40))
			# noch erlaubt: der richtige Link geht
			self.assertIsNotNone(zugang.anfrage_pruefen(token))
			self.assertIsNone(zugang.anfrage_pruefen("y" * 40))  # vierter Fehlversuch
			self.assertIsNone(zugang.anfrage_pruefen(token))  # gesperrt für die IP
		zugang.zaehler_loeschen("fehl:lokal")
		self.assertIsNotNone(zugang.anfrage_pruefen(token))
		with patch.object(zugang, "LIMIT_AKTION_ZUGANG", 2):
			self.assertIsNotNone(zugang.anfrage_pruefen(token, aktion=True))
			self.assertIsNotNone(zugang.anfrage_pruefen(token, aktion=True))
			self.assertIsNone(zugang.anfrage_pruefen(token, aktion=True))
		zugang.zaehler_loeschen(f"aktion:{z.name}")
		with patch.object(zugang, "LIMIT_SEITE_IP", 1):
			zugang.zaehler_loeschen("seite:lokal")
			self.assertIsNotNone(zugang.anfrage_pruefen(token))
			self.assertIsNone(zugang.anfrage_pruefen(token))


def _abrechnung_vorgang(testcase) -> tuple[str, str]:
	"""Angenommener Vorgang (Auftrag eingereicht, Projekt) über die Annahme 4.4 als Büro."""
	from corevatis_erpnext.angebot import annahme

	r = _als(BUERO, annahme.angebot_annehmen, testcase.angebot.name, einreichen=1)
	return r["auftrag"], r["projekt"]
