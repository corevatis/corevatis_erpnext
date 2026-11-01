"""Aufgabe 1.5: Bildliste der Foto-Galerie (get_bilder) und Rechte."""

import io

import frappe
from frappe.tests import IntegrationTestCase

from corevatis_erpnext.crm import galerie
from corevatis_erpnext.tests._helpers import make_lead, make_user

LESER = "cv-test-galerie-leser@example.com"
OHNE_RECHT = "cv-test-galerie-ohne@example.com"


def _png(farbe=(200, 30, 30)) -> bytes:
	from PIL import Image

	buf = io.BytesIO()
	Image.new("RGB", (4, 4), farbe).save(buf, format="PNG")
	return buf.getvalue()


def _datei(lead, file_name, content, is_private=1):
	return frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": "Lead",
			"attached_to_name": lead.name,
			"content": content,
			"is_private": is_private,
		}
	).insert(ignore_permissions=True)


class TestGalerie(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(LESER, "Lea", ["Sales User"], "Leser")
		make_user(OHNE_RECHT, "Otto", [], "Ohne")

	def setUp(self):
		frappe.set_user("Administrator")
		self.lead = make_lead()
		self.f1 = _datei(self.lead, "keller-1.png", _png())
		self.txt = _datei(self.lead, "notiz.txt", b"kein Bild")
		self.f2 = _datei(self.lead, "keller-2.png", _png((30, 200, 30)), is_private=0)
		self.f3 = _datei(self.lead, "aussenwand.png", _png((30, 30, 200)))
		# Attach-Image-Feld zeigt auf einen der Anhänge (wie cv_foto_1..3 aus 1.1 oder Lead.image)
		frappe.db.set_value("Lead", self.lead.name, "image", self.f3.file_url)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_bilder_in_upload_reihenfolge_ohne_textdatei(self):
		frappe.set_user(LESER)
		bilder = galerie.get_bilder("Lead", self.lead.name)
		self.assertEqual([b["file_name"] for b in bilder], ["keller-1.png", "keller-2.png", "aussenwand.png"])
		self.assertEqual(
			[b["file_url"] for b in bilder], [self.f1.file_url, self.f2.file_url, self.f3.file_url]
		)
		self.assertTrue(bilder[0]["is_private"])
		self.assertTrue(bilder[0]["file_url"].startswith("/private/files/"))
		self.assertFalse(bilder[1]["is_private"])
		for b in bilder:
			self.assertTrue(b["thumbnail_url"])
			self.assertTrue(b["creation"])
		# Feldwert und Anhang nur einmal, Quelle = Feldbezeichnung
		self.assertNotEqual(bilder[2]["quelle"], frappe._("Anhang"))
		self.assertEqual(bilder[0]["quelle"], frappe._("Anhang"))

	def test_feldwert_ohne_file_datensatz(self):
		url = "/api/method/frappe_s3_attachment.controller.generate_file?key=abc&file_name=foto-s3.jpg"
		frappe.db.set_value("Lead", self.lead.name, "image", url)
		bilder = galerie.get_bilder("Lead", self.lead.name)
		s3 = [b for b in bilder if b["file_url"] == url]
		self.assertEqual(len(s3), 1)
		self.assertEqual(s3[0]["file_name"], "foto-s3.jpg")

	def test_unsichere_urls_werden_ignoriert(self):
		frappe.db.set_value("Lead", self.lead.name, "image", "javascript:alert(1)//x.png")
		urls = [b["file_url"] for b in galerie.get_bilder("Lead", self.lead.name)]
		self.assertNotIn("javascript:alert(1)//x.png", urls)
		for url in (
			"//evil.example/x.png",
			"https://evil.example/x.png",
			"http://evil.example/x.png",
			"data:image/png;base64,AAAA",
			"/\\evil.example/x.png",
			"/files\\..\\site_config.png",
			"/private/files/../../site_config.png",
			"/private/files/%2e%2e/x.png",
			"/app/lead/x.png",
			"/files/x.png\nSet-Cookie: a=b",
		):
			self.assertFalse(galerie._url_erlaubt(url), url)
		for url in (
			"/files/a.png",
			"/private/files/a.jpg",
			"/api/method/frappe_s3_attachment.controller.generate_file?key=a/b&file_name=a.jpg",
		):
			self.assertTrue(galerie._url_erlaubt(url), url)

	def test_externe_url_nicht_eingebunden(self):
		frappe.db.set_value("Lead", self.lead.name, "image", "https://evil.example/tracker.png")
		urls = [b["file_url"] for b in galerie.get_bilder("Lead", self.lead.name)]
		self.assertNotIn("https://evil.example/tracker.png", urls)

	def test_keine_metadaten_fremder_dateien(self):
		# Feldwert zeigt auf den Anhang eines anderen Leads: kein Zugriff auf dessen File-Datensatz
		anderer = make_lead()
		fremd = _datei(anderer, "fremd-geheim.png", _png((1, 2, 3)))
		frappe.db.set_value("Lead", self.lead.name, "image", fremd.file_url)
		eintrag = next(
			b for b in galerie.get_bilder("Lead", self.lead.name) if b["file_url"] == fremd.file_url
		)
		self.assertIsNone(eintrag["name"])
		self.assertEqual(
			str(eintrag["creation"]), str(frappe.db.get_value("Lead", self.lead.name, "creation"))
		)

	def test_ist_bild(self):
		self.assertTrue(galerie.ist_bild("/private/files/FOTO.JPEG"))
		self.assertTrue(
			galerie.ist_bild(
				"/api/method/frappe_s3_attachment.controller.generate_file?key=1&file_name=a.webp"
			)
		)
		self.assertFalse(galerie.ist_bild("/files/plan.pdf"))
		self.assertFalse(galerie.ist_bild("/files/ohne-endung"))
		self.assertFalse(galerie.ist_bild("/files/a.png", "SVG"))

	def test_ohne_leserecht(self):
		frappe.set_user(OHNE_RECHT)
		with self.assertRaises(frappe.PermissionError):
			galerie.get_bilder("Lead", self.lead.name)

	def test_ungueltige_ziele(self):
		with self.assertRaises(frappe.DoesNotExistError):
			galerie.get_bilder("Lead", "GIBT-ES-NICHT")
		# Allowlist: nur Lead, Objekt und Befund
		for doctype in ("Kein Doctype", "Event Participants", "Event", "User", "File"):
			with self.assertRaises(frappe.PermissionError):
				galerie.get_bilder(doctype, "x")
		self.assertIn("Befund", galerie.GALERIE_DOCTYPES)
		# Befund freigegeben (3.7): ohne Kern-App fehlt der Doctype → nicht gefunden statt verboten
		if not frappe.db.exists("DocType", "Befund"):
			with self.assertRaises(frappe.DoesNotExistError):
				galerie.get_bilder("Befund", "x")

	def test_hooks_doctype_js(self):
		hooks = frappe.get_hooks("doctype_js", app_name="corevatis_erpnext")
		self.assertIn("public/js/galerie.js", hooks["Lead"])
		self.assertIn("public/js/lead_galerie.js", hooks["Lead"])
		self.assertIn("public/js/lead_termin.bundle.js", hooks["Lead"])
		# danach ggf. weitere Skripte (Prozessleiste)
		self.assertEqual(hooks["Befund"][:2], ["public/js/galerie.js", "public/js/befund_galerie.js"])
		for path in hooks["Lead"] + hooks["Befund"]:
			self.assertTrue(frappe.read_file(frappe.get_app_path("corevatis_erpnext", path)))

	def test_fotofelder_aus_1_1(self):
		if not frappe.get_meta("Lead").has_field("cv_foto_1"):
			self.skipTest("cv_foto_1 (1.1) fehlt")
		f4 = _datei(self.lead, "riss.png", _png((9, 9, 9)))
		frappe.db.set_value("Lead", self.lead.name, "cv_foto_1", f4.file_url)
		bilder = galerie.get_bilder("Lead", self.lead.name)
		eintrag = [b for b in bilder if b["file_url"] == f4.file_url]
		self.assertEqual(len(eintrag), 1)
		self.assertEqual(eintrag[0]["quelle"], frappe._("Photo 1"))
		self.assertEqual(bilder[-1]["file_url"], f4.file_url)  # zuletzt hochgeladen

	def test_feldrechte_permlevel(self):
		meta = frappe.get_meta("Lead")
		if not meta.has_field("cv_foto_1") or frappe.get_meta("Lead") is not meta:
			self.skipTest("cv_foto_1 fehlt oder Meta nicht gecacht")
		url = "/private/files/nur-mit-feldrecht.png"
		frappe.db.set_value("Lead", self.lead.name, "cv_foto_1", url)
		frappe.set_user(LESER)
		self.assertIn(url, [b["file_url"] for b in galerie.get_bilder("Lead", self.lead.name)])
		df = meta.get_field("cv_foto_1")
		alt = df.permlevel
		df.permlevel = 7  # keine Rolle hat Leserecht auf Ebene 7
		try:
			self.assertNotIn(url, [b["file_url"] for b in galerie.get_bilder("Lead", self.lead.name)])
		finally:
			df.permlevel = alt

	def test_svg_nicht_in_der_galerie(self):
		# E-11: SVG würde über „Original öffnen“ inline auf der Site-Origin geladen (Stored XSS)
		svg = _datei(
			self.lead,
			"skizze.svg",
			b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="4" height="4"/></svg>',
		)
		frappe.set_user(LESER)
		namen = [b["file_name"] for b in galerie.get_bilder("Lead", self.lead.name)]
		self.assertNotIn(svg.file_name, namen)
		self.assertIn("keller-1.png", namen)
		for name in ("x.svg", "x.SVG", "x.bmp", "x.avif", "x.heif"):
			self.assertFalse(galerie.ist_bild(f"/private/files/{name}"), name)
		for name in ("x.jpg", "x.JPEG", "x.png", "x.gif", "x.webp", "x.heic"):
			self.assertTrue(galerie.ist_bild(f"/private/files/{name}"), name)
		# Dateiname .jpg, Ziel-URL .svg (Endung nur im Query-String) → nicht angezeigt
		frappe.set_user("Administrator")
		getarnt = _datei(self.lead, "getarnt.jpg", _png((1, 2, 3)))
		frappe.db.set_value("File", getarnt.name, "file_url", "/private/files/skizze.svg?x=.jpg")
		self.assertFalse(galerie.ist_bild("/private/files/skizze.svg?x=.jpg"))
		self.assertFalse(galerie.ist_bild("/private/files/skizze.svg#.jpg"))
		self.assertFalse(
			galerie.ist_bild(
				"/api/method/frappe_s3_attachment.controller.generate_file?key=a.jpg&file_name=b.svg"
			)
		)
		frappe.set_user(LESER)
		bilder = galerie.get_bilder("Lead", self.lead.name)
		self.assertNotIn("getarnt.jpg", [b["file_name"] for b in bilder])
		self.assertFalse([b for b in bilder if ".svg" in b["file_url"]])
		# gleiche Liste im Client („Original öffnen“ nur für diese Typen)
		with open(
			frappe.get_app_path("corevatis_erpnext", "public", "js", "galerie.js"), encoding="utf-8"
		) as f:
			js = f.read()
		self.assertIn('new Set(["jpg", "jpeg", "png", "gif", "webp", "heic"])', js)
		self.assertIn("return BILD_ENDUNGEN.has(endung_ziel(bild.file_url));", js)
		self.assertEqual(galerie.BILD_ENDUNGEN, {"jpg", "jpeg", "png", "gif", "webp", "heic"})

	def test_markiert_aus_fotozeile(self):
		"""P3.1: Fotozeilen mit ``markiert`` (Befund Foto aus corevatis) kennzeichnen das Bild;
		die CI-Site hat kein corevatis - Meta der Kindtabelle als Attrappe."""
		from types import SimpleNamespace as NS
		from unittest.mock import patch

		def feld(fieldname, fieldtype, permlevel=0, options=None, label=None):
			return NS(
				fieldname=fieldname, fieldtype=fieldtype, permlevel=permlevel, options=options, label=label
			)

		kind = NS(fields=[feld("foto", "Attach Image"), feld("markiert", "Check")])
		zeilen = [
			frappe._dict(idx=1, foto="/private/files/a.jpg", markiert=1),
			frappe._dict(idx=2, foto="/private/files/b.jpg", markiert=0),
		]
		doc = NS(
			meta=NS(
				get_permlevel_access=lambda _p: [0],
				fields=[
					feld("image", "Attach Image"),
					feld("fotos", "Table", options="Befund Foto", label="Fotos"),
				],
			),
			get=lambda f: {"image": "/private/files/c.jpg", "fotos": zeilen}.get(f),
		)
		with patch.object(galerie.frappe, "get_meta", return_value=kind):
			werte = galerie._feldwerte(doc)
		self.assertEqual(
			[(u, m) for u, _q, m in werte],
			[
				("/private/files/c.jpg", False),
				("/private/files/a.jpg", True),
				("/private/files/b.jpg", False),
			],
		)
		# ohne Leserecht auf das Feld markiert (permlevel): kein Kennzeichen
		kind.fields[1].permlevel = 7
		with patch.object(galerie.frappe, "get_meta", return_value=kind):
			self.assertFalse(any(m for _u, _q, m in galerie._feldwerte(doc)))
		# Eintrag: Anhang zuerst gelistet, Feldwert mit markiert setzt das Kennzeichen
		bilder: dict = {}
		datei = frappe._dict(name="F1", file_name="a.jpg", thumbnail_url=None, is_private=1, creation=None)
		galerie._eintrag(bilder, "/private/files/a.jpg", datei, quelle=frappe._("Anhang"))
		self.assertFalse(bilder["/private/files/a.jpg"]["markiert"])
		galerie._eintrag(bilder, "/private/files/a.jpg", datei, quelle="Fotos 1", markiert=True)
		self.assertTrue(bilder["/private/files/a.jpg"]["markiert"])
		# Lead-Bilder ohne Fotozeile: nicht markiert
		self.assertFalse(any(b["markiert"] for b in galerie.get_bilder("Lead", self.lead.name)))
