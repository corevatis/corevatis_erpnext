"""Web Form „Kostenlose Einschätzung“ (Aufgabe 1.2).

Die Einsendungen laufen als echte HTTP-Anfragen eines Gastes durch die WSGI-App
(werkzeug-Testclient, eigener Thread wie in ``frappe.tests.test_api``): Routing,
``override_whitelisted_methods``, Rate-Limits, Sessions und Commit wie auf der Site.
"""

import base64
import io
import json
import os
import random
import threading
import uuid
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_test_client

from corevatis_erpnext.tests._helpers import make_lead
from corevatis_erpnext.webform import einschaetzung as ez

ACCEPT_URL = "/api/method/frappe.website.doctype.web_form.web_form.accept"
LEAD_FELDER = (
	"cv_objekttyp",
	"cv_bauteil",
	"cv_symptome",
	"cv_symptom_schimmel",
	"cv_dringlichkeit",
	"cv_objekt_adresse",
	"cv_beschreibung",
	"cv_quelle",
	*ez.FOTO_FELDER,
)
BESCHREIBUNG = "Feuchte Flecken im Keller seit dem Frühjahr.\nRiecht muffig."


def _request(method: str, path: str, **kwargs):
	"""Anfrage über die WSGI-App in einem eigenen Thread (eigener frappe.local-Kontext)."""
	site = frappe.local.site
	result = {}

	def run():
		with patch("frappe.app.get_site_name", return_value=site):
			client = get_test_client(use_cookies=False)
			result["response"] = getattr(client, method)(path, **kwargs)

	thread = threading.Thread(target=run)
	thread.start()
	thread.join()
	return result["response"]


def _ohne_assets():
	"""Die CI baut keine Assets (``bench init --skip-assets``): ``assets.json`` fehlt."""
	return patch("frappe.utils.get_assets_json", return_value={})


def _ip() -> str:
	return (
		f"198.51.100.{random.randint(1, 254)}"
		if random.random() < 0.5
		else f"203.0.113.{random.randint(1, 254)}"
	)


def _dataurl(mime: str, content: bytes) -> str:
	return f"data:{mime};base64,{base64.b64encode(content).decode()}"


def _bild(fmt: str) -> bytes:
	"""Echtes, zufälliges 16x16-Bild (Frappe öffnet JPEGs beim Speichern mit Pillow)."""
	from PIL import Image

	buf = io.BytesIO()
	Image.frombytes("RGB", (16, 16), os.urandom(16 * 16 * 3)).save(buf, format=fmt)
	return buf.getvalue()


def _jpg() -> bytes:
	return _bild("JPEG")


def _png() -> bytes:
	return _bild("PNG")


def _heic() -> bytes:
	return b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00" + os.urandom(2048)


class TestWebFormEinschaetzung(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.emails = []
		# eigenes Limit hier großzügig; der Rate-Limit-Test setzt es gezielt herunter
		cls._rl = patch.object(ez, "rate_limit_config", return_value=(1000, 3600, 0))
		cls._rl.start()

	@classmethod
	def tearDownClass(cls):
		cls._rl.stop()
		frappe.db.rollback()
		for email in cls.emails:
			for name in frappe.get_all("Lead", filters={"email_id": email}, pluck="name"):
				try:
					for file_name in frappe.get_all(
						"File",
						filters={"attached_to_doctype": "Lead", "attached_to_name": name},
						pluck="name",
					):
						frappe.delete_doc("File", file_name, force=True, ignore_permissions=True)
					frappe.delete_doc("Lead", name, force=True, ignore_permissions=True)
				except Exception:
					frappe.db.rollback()
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")

	# --- Hilfen -----------------------------------------------------------

	def payload(self, **overrides) -> dict:
		email = f"einschaetzung-{uuid.uuid4().hex[:12]}@example.com"
		self.emails.append(email)
		data = {
			"doctype": "Lead",
			"web_form_name": ez.WEB_FORM,
			"lead_name": "Erika Mustermann",
			"email_id": email,
			"mobile_no": "+49 151 23456789",
			"cv_objekt_adresse": "Musterweg 1\n48143 Münster",
			"cv_objekttyp": "EFH",
			"cv_bauteil": "Keller innen",
			"cv_dringlichkeit": "Dringend",
			"cv_symptom_schimmel": 1,
			"cv_symptom_geruch": 1,
			"cv_beschreibung": BESCHREIBUNG,
			"cv_datenschutz": 1,
			"cv_hp": "",
			"cv_fotos": [
				{"name": "keller.jpg", "data": _dataurl("image/jpeg", _jpg())},
				{"name": "sockel.png", "data": _dataurl("image/png", _png())},
				{"name": "wand.heic", "data": _dataurl("image/heic", _heic())},
			],
		}
		data.update(overrides)
		return data

	def submit(self, data: dict, ip: str | None = None, web_form: str = ez.WEB_FORM):
		ip = ip or _ip()
		return _request(
			"post",
			ACCEPT_URL,
			json={"web_form": web_form, "data": json.dumps(data)},
			headers={"X-Forwarded-For": ip},
		)

	def leads(self, email: str) -> list[str]:
		frappe.db.rollback()  # neuer Snapshot: Anfrage lief in eigener Verbindung
		return frappe.get_all("Lead", filters={"email_id": email}, pluck="name")

	# --- Installation -----------------------------------------------------

	def test_web_form_exists(self):
		wf = frappe.get_doc("Web Form", ez.WEB_FORM)
		self.assertEqual(wf.route, ez.ROUTE)
		self.assertEqual(wf.doc_type, "Lead")
		self.assertEqual(wf.module, "Corevatis ERPNext")
		self.assertTrue(wf.published)
		self.assertTrue(wf.is_standard)
		self.assertFalse(wf.login_required)
		fields = {f.fieldname: f for f in wf.web_form_fields}
		for fieldname in ("lead_name", "email_id", "cv_objekt_adresse", "cv_datenschutz_html", "cv_hp_html"):
			self.assertIn(fieldname, fields)
		self.assertTrue(fields["lead_name"].reqd)
		self.assertTrue(fields["email_id"].reqd)

	def test_lead_fields_from_1_1(self):
		meta = frappe.get_meta("Lead")
		for fieldname in LEAD_FELDER:
			self.assertTrue(meta.has_field(fieldname), fieldname)
		wf = frappe.get_doc("Web Form", ez.WEB_FORM)
		for row in wf.web_form_fields:
			if row.fieldtype == "Select":
				self.assertEqual(meta.get_field(row.fieldname).fieldtype, "Select", row.fieldname)

	def test_hooks(self):
		overrides = frappe.get_hooks("override_whitelisted_methods")
		self.assertEqual(overrides[ez.FRAPPE_ACCEPT][-1], "corevatis_erpnext.webform.einschaetzung.accept")
		self.assertIn(
			"corevatis_erpnext.webform.einschaetzung.before_insert_lead",
			frappe.get_hooks("doc_events")["Lead"]["before_insert"],
		)

	def test_setup(self):
		self.assertTrue(frappe.db.exists("UTM Source", "Website"))
		# E-2: die site-weite Einstellung bleibt aus (ERPNext-Standard), auch nach dem Patch
		ez.setup()
		from corevatis_erpnext.patches.v0_3 import lead_dubletten_pruefung

		frappe.db.set_single_value("CRM Settings", "allow_lead_duplication_based_on_emails", 1)
		lead_dubletten_pruefung.execute()
		self.assertFalse(frappe.db.get_single_value("CRM Settings", "allow_lead_duplication_based_on_emails"))

	def test_dublettenpruefung_desk_bleibt(self):
		# E-2: Desk/Import/E-Mail-Leads prüfen Dubletten weiter
		email = f"desk-{uuid.uuid4().hex[:12]}@example.com"
		self.emails.append(email)
		# Lead im Test-Thread nicht offen lassen: die Namensserie bliebe gesperrt, und die
		# HTTP-Einsendungen der folgenden Tests (eigene Verbindung) warteten darauf
		self.addCleanup(frappe.db.rollback)
		make_lead(first_name="Desk", email_id=email)
		with self.assertRaises(frappe.DuplicateEntryError):
			make_lead(first_name="Desk 2", email_id=email)

	# --- Einsendung -------------------------------------------------------

	def test_guest_submission_creates_lead_with_private_photos(self):
		data = self.payload()
		response = self.submit(data)
		self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
		self.assertEqual(response.json["message"], {"doctype": "Lead"})

		names = self.leads(data["email_id"])
		self.assertEqual(len(names), 1)
		lead = frappe.get_doc("Lead", names[0])
		self.assertEqual(lead.owner, "Guest")
		self.assertEqual(lead.lead_name, "Erika Mustermann")
		self.assertEqual(lead.mobile_no, "+49 151 23456789")
		self.assertEqual(lead.utm_source, "Website")
		self.assertEqual(lead.cv_quelle, "Web Form")
		self.assertEqual(lead.cv_objekttyp, "EFH")
		self.assertEqual(lead.cv_bauteil, "Keller innen")
		self.assertEqual(lead.cv_dringlichkeit, "Dringend")
		self.assertIn("Münster", lead.cv_objekt_adresse)
		self.assertEqual(lead.cv_beschreibung, BESCHREIBUNG)
		# cv_symptome baut der 1.1-Hook aus den Checkboxen
		self.assertEqual(lead.cv_symptom_schimmel, 1)
		self.assertEqual(lead.cv_symptom_geruch, 1)
		self.assertEqual(lead.cv_symptom_feuchte_waende, 0)
		self.assertEqual((lead.cv_symptome or "").splitlines(), ["Schimmel", "Geruch"])

		files = frappe.get_all(
			"File",
			filters={"attached_to_doctype": "Lead", "attached_to_name": lead.name},
			fields=["name", "file_name", "file_url", "is_private", "attached_to_field"],
			order_by="file_name asc",
		)
		self.assertEqual([f.file_name.rsplit(".", 1)[1] for f in files], ["jpg", "png", "heic"])
		for f in files:
			self.assertEqual(f.is_private, 1, f)
			self.assertTrue(
				f.file_url.startswith(("/private/files/", "/api/method/frappe_s3_attachment.")), f.file_url
			)
		# Inhalt unverändert
		sent = base64.b64decode(data["cv_fotos"][0]["data"].split(",", 1)[1])
		jpg = frappe.get_doc("File", files[0].name)
		if jpg.file_url.startswith("/private/files/"):
			self.assertEqual(jpg.get_content(), sent)

		meta = frappe.get_meta("Lead")
		if meta.has_field("cv_foto_1"):
			urls = {lead.get(f) for f in ez.FOTO_FELDER}
			self.assertEqual(urls, {f.file_url for f in files})
			self.assertEqual(sorted(f.attached_to_field for f in files), list(ez.FOTO_FELDER))
		self.assertTrue(lead.image)

		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{"reference_doctype": "Lead", "reference_name": lead.name, "comment_type": "Info"},
			)
		)

	def test_private_photos_not_readable_by_guest(self):
		data = self.payload(cv_fotos=[{"name": "a.jpg", "data": _dataurl("image/jpeg", _jpg())}])
		self.assertEqual(self.submit(data).status_code, 200)
		name = self.leads(data["email_id"])[0]
		file_url = frappe.db.get_value(
			"File", {"attached_to_name": name, "attached_to_doctype": "Lead"}, "file_url"
		)
		response = _request("get", file_url)
		# T-1: ausdrücklich verweigert - kein Redirect zur Anmeldung, kein 404
		self.assertIn(response.status_code, (401, 403), file_url)
		self.assertNotIn("image/", response.headers.get("Content-Type", ""))

	def test_honeypot_creates_no_lead(self):
		data = self.payload(cv_hp="https://spam.example")
		response = self.submit(data)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json["message"], {"doctype": "Lead"})
		self.assertEqual(self.leads(data["email_id"]), [])

	def test_datenschutz_required(self):
		for value in (0, "", None):
			data = self.payload(cv_datenschutz=value)
			response = self.submit(data)
			self.assertEqual(response.status_code, 417, response.get_data(as_text=True))
			self.assertIn("Datenschutz", response.get_data(as_text=True))
			self.assertEqual(self.leads(data["email_id"]), [])

	def test_mandatory_web_form_fields(self):
		data = self.payload(cv_objekt_adresse="")
		response = self.submit(data)
		self.assertNotEqual(response.status_code, 200)
		self.assertEqual(self.leads(data["email_id"]), [])

	def test_text_length_limit(self):
		data = self.payload(cv_beschreibung="x" * (ez.MAX_BESCHREIBUNG + 1))
		self.assertEqual(self.submit(data).status_code, 417)
		self.assertEqual(self.leads(data["email_id"]), [])

	def test_optional_fields(self):
		data = self.payload(
			cv_fotos=[], cv_objekttyp="", cv_bauteil="", cv_symptom_schimmel=0, cv_symptom_geruch=0
		)
		data.pop("mobile_no")
		self.assertEqual(self.submit(data).status_code, 200)
		self.assertEqual(len(self.leads(data["email_id"])), 1)

	def test_photo_type_and_count(self):
		html = _dataurl("image/jpeg", b"<html><script>alert(1)</script></html>")
		kaputt = _dataurl("image/jpeg", b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + os.urandom(512))
		gif = _dataurl("image/gif", b"GIF89a" + os.urandom(64))
		for fotos in (
			[{"name": "x.jpg", "data": html}],
			[{"name": "x.gif", "data": gif}],
			[{"name": "kaputt.jpg", "data": kaputt}],
			[{"name": "x.jpg", "data": "kein-dataurl"}],
			[{"name": f"{i}.jpg", "data": _dataurl("image/jpeg", _jpg())} for i in range(4)],
		):
			data = self.payload(cv_fotos=fotos)
			response = self.submit(data)
			self.assertEqual(response.status_code, 417, response.get_data(as_text=True))
			self.assertEqual(self.leads(data["email_id"]), [])

	def test_photo_too_large(self):
		big = b"\xff\xd8\xff\xe0" + os.urandom(ez.MAX_FOTO_BYTES - 3)
		data = self.payload(cv_fotos=[{"name": "big.jpg", "data": _dataurl("image/jpeg", big)}])
		response = self.submit(data)
		self.assertEqual(response.status_code, 417, response.get_data(as_text=True)[:500])
		self.assertEqual(self.leads(data["email_id"]), [])

	def test_photo_duplicates_attached_once(self):
		jpg = _dataurl("image/jpeg", _jpg())
		data = self.payload(cv_fotos=[{"name": "a.jpg", "data": jpg}, {"name": "b.jpg", "data": jpg}])
		self.assertEqual(self.submit(data).status_code, 200)
		name = self.leads(data["email_id"])[0]
		self.assertEqual(
			frappe.db.count("File", {"attached_to_doctype": "Lead", "attached_to_name": name}), 1
		)

	def test_no_photos(self):
		data = self.payload(cv_fotos=[])
		self.assertEqual(self.submit(data).status_code, 200)
		self.assertEqual(len(self.leads(data["email_id"])), 1)

	def test_guest_cannot_update_lead(self):
		first = self.payload()
		self.assertEqual(self.submit(first).status_code, 200)
		name = self.leads(first["email_id"])[0]
		data = self.payload(name=name, lead_name="Hacker")
		self.assertEqual(self.submit(data).status_code, 403)
		frappe.db.rollback()
		self.assertEqual(frappe.db.get_value("Lead", name, "lead_name"), "Erika Mustermann")

	def _contacts(self, email: str) -> int:
		return len(
			frappe.get_all(
				"Contact Email", filters={"email_id": email, "parenttype": "Contact"}, pluck="parent"
			)
		)

	def test_bekannte_email_haengt_an_offenen_lead(self):
		# E-2: zweite Anfrage derselben Adresse → kein neuer Lead/Contact, Kommentar + Fotos am Lead
		data = self.payload(cv_fotos=[{"name": "a.jpg", "data": _dataurl("image/jpeg", _jpg())}])
		self.assertEqual(self.submit(data).status_code, 200)
		[name] = self.leads(data["email_id"])
		self.assertEqual(self._contacts(data["email_id"]), 1)
		status_vorher = frappe.db.get_value("Lead", name, ["cv_status", "status"])

		again = self.payload(
			email_id=data["email_id"].upper(),
			lead_name="Erika M.",
			cv_beschreibung="Jetzt auch <b>Risse</b> in der Garage.",
			cv_symptom_feuchte_waende=1,
			cv_fotos=[
				{"name": "b.png", "data": _dataurl("image/png", _png())},
				{"name": "c.jpg", "data": _dataurl("image/jpeg", _jpg())},
			],
		)
		self.emails.append(again["email_id"])
		response = self.submit(again)
		self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
		self.assertEqual(response.json["message"], {"doctype": "Lead"})  # gleiche Antwort wie neu

		self.assertEqual(self.leads(data["email_id"]), [name])
		self.assertEqual(self._contacts(data["email_id"]), 1)
		lead = frappe.get_doc("Lead", name)
		self.assertEqual((lead.cv_status, lead.status), tuple(status_vorher))
		self.assertEqual(lead.lead_name, "Erika Mustermann")  # Felder unverändert
		self.assertEqual(lead.cv_beschreibung, BESCHREIBUNG)
		files = frappe.get_all(
			"File",
			filters={"attached_to_doctype": "Lead", "attached_to_name": name},
			fields=["file_name", "is_private", "attached_to_field"],
		)
		self.assertEqual(len(files), 3)
		self.assertTrue(all(f.is_private for f in files))
		# cv_foto_1 bleibt beim ersten Foto, die neuen füllen nur leere Felder
		self.assertEqual(sorted(filter(None, (f.attached_to_field for f in files))), list(ez.FOTO_FELDER))
		kommentare = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Lead", "reference_name": name, "comment_type": "Comment"},
			pluck="content",
		)
		self.assertEqual(len(kommentare), 1)
		self.assertIn("Weitere Anfrage", kommentare[0])
		self.assertIn("Jetzt auch &lt;b&gt;Risse&lt;/b&gt; in der Garage.", kommentare[0])
		self.assertNotIn("<b>Risse", kommentare[0])
		self.assertIn("Erika M.", kommentare[0])
		self.assertIn("Feuchte Wände", kommentare[0])
		self.assertIn("2 Foto(s)", kommentare[0])

	def test_bekannte_email_pflichtfelder(self):
		data = self.payload(cv_fotos=[])
		self.assertEqual(self.submit(data).status_code, 200)
		[name] = self.leads(data["email_id"])
		again = self.payload(email_id=data["email_id"], cv_objekt_adresse="", cv_fotos=[])
		self.assertEqual(self.submit(again).status_code, 417)
		self.assertFalse(
			frappe.db.exists(
				"Comment", {"reference_doctype": "Lead", "reference_name": name, "comment_type": "Comment"}
			)
		)

	def test_abgeschlossener_lead_neue_anfrage(self):
		# E-2: kein offener Lead (Verloren) → neuer Lead trotz Dublettenprüfung
		data = self.payload(cv_fotos=[])
		self.assertEqual(self.submit(data).status_code, 200)
		[erster] = self.leads(data["email_id"])
		frappe.db.set_value("Lead", erster, {"cv_status": "Verloren", "cv_verlustgrund": "Preis"})
		frappe.db.commit()
		again = self.payload(email_id=data["email_id"], cv_fotos=[])
		self.assertEqual(self.submit(again).status_code, 200)
		leads = self.leads(data["email_id"])
		self.assertEqual(len(leads), 2)
		self.assertEqual(frappe.db.get_value("Lead", erster, "cv_status"), "Verloren")
		self.assertFalse(frappe.db.get_single_value("CRM Settings", "allow_lead_duplication_based_on_emails"))

	def test_offener_lead(self):
		self.assertIsNone(ez.offener_lead(""))
		self.assertIsNone(ez.offener_lead(f"unbekannt-{uuid.uuid4().hex[:8]}@example.com"))

	def test_anhang_kommentar_mit_endgueltiger_url(self):
		# E-10: frappe_s3_attachment schreibt file_url in File.after_insert um; der Anhang-Eintrag
		# im Verlauf muss die endgültige URL tragen (vorher /private/files/... → 403)
		from frappe.core.doctype.file.file import File

		self.addCleanup(frappe.db.rollback)  # Namensserie freigeben (siehe oben)
		lead = make_lead()
		original = File.after_insert

		def after_insert_mit_s3(doc):
			original(doc)
			neu = (
				"/api/method/frappe_s3_attachment.controller.generate_file"
				f"?key=Lead%2F{doc.name}&file_name={doc.file_name}"
			)
			frappe.db.set_value("File", doc.name, "file_url", neu, update_modified=False)
			doc.file_url = neu

		with patch.object(File, "after_insert", after_insert_mit_s3):
			ez.attach_fotos(lead, [("foto-1.jpg", _jpg())])
		kommentare = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Lead", "reference_name": lead.name, "comment_type": "Attachment"},
			pluck="content",
		)
		self.assertEqual(len(kommentare), 1, kommentare)
		self.assertIn("/api/method/frappe_s3_attachment.controller.generate_file", kommentare[0])
		self.assertNotIn("/private/files/", kommentare[0])
		self.assertIn("foto-1.jpg", kommentare[0])
		# ohne S3 entsteht der Eintrag weiterhin (mit der lokalen URL)
		ez.attach_fotos(lead, [("foto-2.png", _png())])
		self.assertEqual(
			frappe.db.count(
				"Comment",
				{"reference_doctype": "Lead", "reference_name": lead.name, "comment_type": "Attachment"},
			),
			2,
		)

	def _reset_limits(self, *buckets):
		for bucket in buckets:
			frappe.cache.delete(frappe.cache.make_key(f"cv:webform:{ez.WEB_FORM}:{bucket}"))

	def test_rate_limit_per_ip(self):
		ip = "192.0.2.77"
		self._reset_limits(f"ip:{ip}", "ip:192.0.2.78")
		with patch.object(ez, "rate_limit_config", return_value=(2, 60, 0)):
			self.assertEqual(self.submit(self.payload(cv_fotos=[]), ip=ip).status_code, 200)
			self.assertEqual(self.submit(self.payload(cv_fotos=[]), ip=ip).status_code, 200)
			blocked = self.payload(cv_fotos=[])
			self.assertEqual(self.submit(blocked, ip=ip).status_code, 429)
			self.assertEqual(self.leads(blocked["email_id"]), [])
			# andere IP nicht betroffen
			self.assertEqual(self.submit(self.payload(cv_fotos=[]), ip="192.0.2.78").status_code, 200)

	def test_rate_limit_ignores_spoofed_headers(self):
		ip = "192.0.2.90"
		self._reset_limits(f"ip:{ip}")
		with patch.object(ez, "rate_limit_config", return_value=(1, 60, 0)):
			first = _request(
				"post",
				ACCEPT_URL,
				json={"web_form": ez.WEB_FORM, "data": json.dumps(self.payload(cv_fotos=[]))},
				headers={"X-Forwarded-For": ip, "CF-Connecting-IP": "198.51.100.1"},
			)
			self.assertEqual(first.status_code, 200)
			# gefälschter CF-Connecting-IP ändert den Schlüssel nicht
			second = _request(
				"post",
				ACCEPT_URL,
				json={"web_form": ez.WEB_FORM, "data": json.dumps(self.payload(cv_fotos=[]))},
				headers={"X-Forwarded-For": ip, "CF-Connecting-IP": "198.51.100.2"},
			)
			self.assertEqual(second.status_code, 429)

	def test_client_ip_validated(self):
		for value, expected in (
			("192.0.2.1", "192.0.2.1"),
			("2001:db8::1", "2001:db8::1"),
			("unbekannt", None),
			("1.2.3.4:x", None),
			("", None),
			(None, None),
		):
			with patch.object(frappe.local, "request_ip", value, create=True):
				self.assertEqual(ez.client_ip(), expected)

	def _global_count(self) -> int:
		return int(frappe.cache.get(frappe.cache.make_key(f"cv:webform:{ez.WEB_FORM}:global")) or 0)

	def test_rate_limit_global(self):
		self._reset_limits("global", "ip:192.0.2.101", "ip:192.0.2.102", "ip:192.0.2.103")
		with patch.object(ez, "rate_limit_config", return_value=(100, 60, 2)):
			self.assertEqual(self.submit(self.payload(cv_fotos=[]), ip="192.0.2.101").status_code, 200)
			# abgewiesene Einsendungen (Datenschutz, Honeypot) zählen nicht global
			self.assertEqual(self.submit(self.payload(cv_datenschutz=0), ip="192.0.2.102").status_code, 417)
			self.assertEqual(self.submit(self.payload(cv_hp="x"), ip="192.0.2.102").status_code, 200)
			self.assertEqual(self._global_count(), 1)
			self.assertEqual(self.submit(self.payload(cv_fotos=[]), ip="192.0.2.102").status_code, 200)
			blocked = self.payload(cv_fotos=[])
			self.assertEqual(self.submit(blocked, ip="192.0.2.103").status_code, 429)
			self.assertEqual(self.leads(blocked["email_id"]), [])
		self._reset_limits("global")

	def test_global_limit_vor_fotopruefung(self):
		# E-9: bei globaler Sperre keine Foto-Dekodierung/Pillow-Prüfung mehr
		ip = "192.0.2.130"
		self._reset_limits("global", f"ip:{ip}")
		frappe.cache.incrby(frappe.cache.make_key(f"cv:webform:{ez.WEB_FORM}:global"), 2)
		with (
			patch.object(ez, "rate_limit_config", return_value=(100, 60, 2)),
			patch.object(ez, "parse_fotos", side_effect=AssertionError("Fotos trotz Sperre geprüft")),
		):
			blocked = self.payload()
			self.assertEqual(self.submit(blocked, ip=ip).status_code, 429)
		self.assertEqual(self.leads(blocked["email_id"]), [])
		self._reset_limits("global")

	def test_blocked_ip_does_not_touch_global_counter(self):
		ip = "192.0.2.120"
		self._reset_limits("global", f"ip:{ip}")
		with patch.object(ez, "rate_limit_config", return_value=(1, 60, 1000)):
			self.assertEqual(self.submit(self.payload(cv_fotos=[]), ip=ip).status_code, 200)
			self.assertEqual(self._global_count(), 1)
			body = {"web_form": ez.WEB_FORM, "data": json.dumps(self.payload(cv_fotos=[]))}
			for _ in range(50):
				response = _request("post", ACCEPT_URL, json=body, headers={"X-Forwarded-For": ip})
				self.assertEqual(response.status_code, 429)
			self.assertEqual(self._global_count(), 1)
		self._reset_limits("global")

	def test_guest_upload_file_stays_forbidden(self):
		self.assertFalse(frappe.get_system_settings("allow_guests_to_upload_files"))
		ip = _ip()
		response = _request(
			"post",
			"/api/method/upload_file",
			data={"is_private": "1", "doctype": "Lead", "file": (io.BytesIO(_jpg()), "x.jpg")},
			headers={"X-Forwarded-For": ip},
		)
		self.assertEqual(response.status_code, 403, response.get_data(as_text=True)[:500])

	def test_other_web_forms_delegate_to_frappe(self):
		calls = []

		def fake_accept(**kwargs):
			calls.append(kwargs)
			return "frappe"

		with patch.object(frappe, "get_attr", return_value=fake_accept):
			self.assertEqual(ez.accept(web_form="addresses", data="{}"), "frappe")
		self.assertEqual(calls, [{"web_form": "addresses", "data": "{}", "web_form_request_key": None}])

	# --- Seite ------------------------------------------------------------

	def test_page_renders_for_guest(self):
		with _ohne_assets():
			response = _request("get", f"/{ez.ROUTE}/new?embed=1")
		self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:1000])
		html = response.get_data(as_text=True)
		self.assertIn("cv_fotos_input", html)
		self.assertIn("cv_datenschutz", html)
		self.assertIn("Anfrage senden", html)

	def test_frame_ancestors_only_when_configured(self):
		with _ohne_assets():
			response = _request("get", f"/{ez.ROUTE}/new")
		self.assertEqual(response.status_code, 200)
		self.assertNotIn("frame-ancestors", response.headers.get("Content-Security-Policy", ""))
		with _ohne_assets(), patch.object(ez, "embedding_origins", return_value=["https://www.example.de"]):
			response = _request("get", f"/{ez.ROUTE}/new?embed=1")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			response.headers.get("Content-Security-Policy"), "frame-ancestors 'self' https://www.example.de"
		)

	def test_embedding_origins_validated(self):
		with patch.dict(
			frappe.local.conf,
			{
				"cv_webform_embedding_domains": [
					"https://www.example.de/",
					"https://*.example.org",
					"http://localhost:8080",
					"https://evil.example; script-src *",
					"javascript:alert(1)",
					"'unsafe-inline'",
				]
			},
		):
			self.assertEqual(
				ez.embedding_origins(),
				["https://www.example.de", "https://*.example.org", "http://localhost:8080"],
			)

	def test_embedding_origins_as_text(self):
		# Press' Site Config legt Listen als Text ab (Befund mvp-test)
		for value in (
			"['http://localhost:8080', 'https://www.example.de/']",
			'["http://localhost:8080", "https://www.example.de"]',
			"http://localhost:8080 https://www.example.de",
			"http://localhost:8080,https://www.example.de",
		):
			with patch.dict(frappe.local.conf, {"cv_webform_embedding_domains": value}):
				self.assertEqual(ez.embedding_origins(), ["http://localhost:8080", "https://www.example.de"])
		with patch.dict(
			frappe.local.conf,
			{
				"cv_webform_embedding_domains": "https://evil.example; script-src * 'unsafe-inline' javascript:x"
			},
		):
			self.assertEqual(ez.embedding_origins(), ["https://evil.example"])

	def test_datenschutz_url(self):
		for value, expected in (
			("https://www.example.de/datenschutz", "https://www.example.de/datenschutz"),
			("javascript:alert(1)", "/datenschutz"),
			("//evil.example", "/datenschutz"),
			(None, "/datenschutz"),
		):
			with patch.dict(frappe.local.conf, {"cv_webform_datenschutz_url": value}):
				self.assertEqual(ez.datenschutz_url(), expected)

	def test_foto_typ(self):
		self.assertEqual(ez.foto_typ(_jpg()), "jpg")
		self.assertEqual(ez.foto_typ(_png()), "png")
		self.assertEqual(ez.foto_typ(_heic()), "heic")
		self.assertIsNone(ez.foto_typ(b"GIF89a"))
		self.assertIsNone(ez.foto_typ(b"<svg/>"))
