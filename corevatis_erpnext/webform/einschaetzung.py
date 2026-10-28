"""Web Form „Kostenlose Einschätzung“ (Aufgabe 1.2): Gast-Anfrage → Lead mit bis zu 3 Fotos.

Ablauf einer Einsendung (``POST /api/method/frappe.website.doctype.web_form.web_form.accept``):

1. ``hooks.override_whitelisted_methods`` leitet ``accept`` hierher um. Andere Web Forms
   gehen unverändert an Frappe weiter.
2. Für dieses Formular: Rate-Limit je Client-IP, Honeypot (gefüllt → scheinbarer Erfolg,
   kein Lead), Datenschutz-Zustimmung (Pflicht), Fotos prüfen (JPEG/PNG/HEIC nach
   Dateiinhalt, je höchstens 10 MiB, höchstens 3).
3. Frappes ``accept`` legt den Lead an (Web-Form-Felder inkl. der Symptom-Checkboxen
   ``cv_symptom_*`` aus 1.1, Pflichtfelder, Select-Werte); ``before_insert_lead`` setzt
   ``utm_source`` und ``cv_quelle``. ``cv_symptome`` baut der 1.1-Hook ``sync_symptome``.
4. Die Fotos werden danach als **private** ``File`` am Lead angelegt (``is_private=1``).
   Mit ``frappe_s3_attachment`` lädt dessen ``File.after_insert`` sie in den Bucket.

Gäste brauchen dafür **kein** ``allow_guests_to_upload_files``: ``upload_file`` bleibt für
Gäste gesperrt, Dateien kommen nur über dieses Formular (Base64 im ``accept``-Aufruf).

site_config-Schlüssel (alle optional):
    cv_webform_rate_limit         Einsendungen je IP und Zeitfenster, Standard 10 (0 = aus)
    cv_webform_rate_window        Zeitfenster in Sekunden, Standard 3600
    cv_webform_rate_limit_global  angenommene Einsendungen je Zeitfenster insgesamt, Standard 100
                                  (0 = aus)
    cv_webform_embedding_domains  erlaubte Einbettungs-Origins (Liste oder durch Leerzeichen
                                  getrennt), z. B. ["https://www.example.de"]; setzt
                                  ``Content-Security-Policy: frame-ancestors`` nur für dieses
                                  Formular
    cv_webform_datenschutz_url    Link der Datenschutzerklärung, Standard ``/datenschutz``
"""

import base64
import binascii
import hashlib
import io
import ipaddress
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, cstr, escape_html

WEB_FORM = "kostenlose-einschaetzung"
ROUTE = "kostenlose-einschaetzung"
FRAPPE_ACCEPT = "frappe.website.doctype.web_form.web_form.accept"

QUELLE = "Web Form"
UTM_SOURCE = "Website"

# Schlüssel im ``data``-JSON, die keine Lead-Felder sind (Formular-JS setzt sie)
KEY_HONEYPOT = "cv_hp"
KEY_DATENSCHUTZ = "cv_datenschutz"
KEY_FOTOS = "cv_fotos"

MAX_FOTOS = 3
MAX_FOTO_BYTES = 10 * 1024 * 1024
MAX_BESCHREIBUNG = 5000
MAX_PIXEL = 60_000_000
FOTO_FELDER = ("cv_foto_1", "cv_foto_2", "cv_foto_3")

RATE_LIMIT = 10
RATE_WINDOW = 3600
RATE_LIMIT_GLOBAL = 100

_DATAURL = re.compile(r"^data:(?P<mime>[\w.+/-]*)(?:;[\w.+=-]+)*;base64,(?P<b64>[A-Za-z0-9+/=]+)$")
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
_EMBED_TEXT_SPLIT = re.compile(r"[\s,;\[\]'\"]+")
_EMBED_ORIGIN = re.compile(
	r"^https?://(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:\d{1,5})?$"
)


# ---------------------------------------------------------------------------
# Einsendung
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST", "PUT"], allow_guest=True)
def accept(web_form: str, data: str | dict, web_form_request_key: str | None = None):
	"""Ersetzt ``frappe.website.doctype.web_form.web_form.accept`` (hooks.py).

	Nur das Formular „Kostenlose Einschätzung“ wird hier behandelt, alle anderen Web Forms
	gehen unverändert an Frappe."""
	frappe_accept = frappe.get_attr(FRAPPE_ACCEPT)
	if web_form != WEB_FORM:
		return frappe_accept(web_form=web_form, data=data, web_form_request_key=web_form_request_key)
	return accept_einschaetzung(data)


def accept_einschaetzung(data: str | dict):
	check_rate_limit()

	payload = _load_payload(data)
	if payload.get("name"):
		# Gäste dürfen nur anlegen, nie bestehende Leads ändern
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if cstr(payload.get(KEY_HONEYPOT)).strip():
		frappe.logger("corevatis_erpnext").info("Web Form %s: Honeypot gefüllt, verworfen", WEB_FORM)
		return _antwort()

	if not cint(payload.get(KEY_DATENSCHUTZ)):
		frappe.throw(
			_("Bitte stimmen Sie der Datenschutzerklärung zu."),
			title=_("Datenschutz"),
			exc=frappe.ValidationError,
		)

	fotos = parse_fotos(payload.get(KEY_FOTOS))
	check_text_lengths(payload)
	check_global_limit()
	for key in (KEY_HONEYPOT, KEY_DATENSCHUTZ, KEY_FOTOS, *FOTO_FELDER, "image"):
		payload.pop(key, None)

	frappe_accept = frappe.get_attr(FRAPPE_ACCEPT)
	frappe.flags.cv_einschaetzung = True
	try:
		lead = frappe_accept(web_form=WEB_FORM, data=json.dumps(payload), web_form_request_key=None)
	except frappe.DuplicateEntryError:
		# ERPNext nennt sonst den vorhandenen Lead (Name + Link) - nicht an Gäste ausgeben
		frappe.clear_messages()
		frappe.throw(
			_(
				"Zu dieser E-Mail-Adresse liegt bereits eine Anfrage vor. Wir melden uns bei Ihnen - "
				"gern auch telefonisch."
			)
		)
	finally:
		frappe.flags.cv_einschaetzung = None

	count_global()
	attach_fotos(lead, fotos)
	lead.add_comment(
		"Info",
		_("Datenschutzerklärung im Web Form „Kostenlose Einschätzung“ akzeptiert ({0}).").format(
			escape_html(datenschutz_url())
		),
	)
	return _antwort()


def _antwort():
	"""Antwort an den Browser: ohne Lead-Namen und ohne Felder (keine Rückschlüsse, gleich bei
	Honeypot und echter Einsendung)."""
	return {"doctype": "Lead"}


def _load_payload(data) -> frappe._dict:
	if isinstance(data, str):
		try:
			data = json.loads(data)
		except ValueError:
			frappe.throw(_("Invalid request"))
	if not isinstance(data, dict):
		frappe.throw(_("Invalid request"))
	return frappe._dict(data)


def client_ip() -> str | None:
	"""Client-IP ausschließlich aus Frappes ``request_ip`` (erster Eintrag aus
	``X-Forwarded-For``). Kein Header wird hier selbst ausgewertet: Der Press-Proxy setzt
	die echte Adresse per nginx ``real_ip`` (``CF-Connecting-IP``, vertraut nur Cloudflare
	und dem Tunnel-Connector) und überschreibt ``X-Forwarded-For`` mit ``$remote_addr``.
	Ungültige Werte → ``None``."""
	try:
		return str(ipaddress.ip_address(cstr(frappe.local.request_ip).strip()))
	except ValueError:
		return None


def rate_limit_config() -> tuple[int, int, int]:
	"""(Limit je IP, Zeitfenster in s, globales Limit je Formular)."""
	conf = frappe.local.conf

	def _int(key, default):
		value = conf.get(key)
		return default if value in (None, "") else cint(value)

	return (
		_int("cv_webform_rate_limit", RATE_LIMIT),
		_int("cv_webform_rate_window", RATE_WINDOW) or RATE_WINDOW,
		_int("cv_webform_rate_limit_global", RATE_LIMIT_GLOBAL),
	)


def _key(bucket: str):
	return frappe.cache.make_key(f"cv:webform:{WEB_FORM}:{bucket}")


def _count(bucket: str, window: int) -> int:
	key = _key(bucket)
	count = frappe.cache.incrby(key, 1)
	if count == 1:
		frappe.cache.expire(key, window)
	return count


def _rate_limit_error():
	frappe.throw(
		_("Zu viele Anfragen. Bitte versuchen Sie es später erneut oder rufen Sie uns an."),
		frappe.RateLimitExceededError,
	)


def check_rate_limit():
	"""Limit je Client-IP (zusätzlich zu Frappes eigenem Limit auf ``accept``: 10 je Minute):
	höchstens ``cv_webform_rate_limit`` Versuche im Zeitfenster, ohne gültige IP ein gemeinsamer
	Bucket „unbekannt“ mit einem Drittel des Limits. Zählt jeden Versuch, auch abgewiesene,
	und lehnt sofort ab - der globale Zähler wird dabei nicht berührt. 0 = aus."""
	if not getattr(frappe.local, "request", None):
		return
	limit, window, _global = rate_limit_config()
	if limit <= 0:
		return
	ip = client_ip()
	bucket_limit = limit if ip else max(1, limit // 3)
	if _count(f"ip:{ip or 'unbekannt'}", window) > bucket_limit:
		_rate_limit_error()


def check_global_limit():
	"""Rückfallschranke je Formular: höchstens ``cv_webform_rate_limit_global`` angenommene
	Einsendungen im Zeitfenster. Wird erst nach IP-Limit, Honeypot, Datenschutz und
	Fotoprüfung aufgerufen; gezählt wird nur eine tatsächlich angelegte Anfrage
	(``count_global``) - abgewiesene Versuche einer IP können das Formular so nicht für alle
	sperren."""
	if not getattr(frappe.local, "request", None):
		return
	_limit, _window, limit_global = rate_limit_config()
	if limit_global > 0 and cint(frappe.cache.get(_key("global"))) >= limit_global:
		_rate_limit_error()


def count_global():
	if not getattr(frappe.local, "request", None):
		return
	_limit, window, limit_global = rate_limit_config()
	if limit_global > 0:
		_count("global", window)


# ---------------------------------------------------------------------------
# Fotos
# ---------------------------------------------------------------------------


def foto_typ(content: bytes) -> str | None:
	"""Dateityp aus dem Inhalt (nicht aus Name oder MIME-Angabe des Browsers)."""
	if content.startswith(b"\xff\xd8\xff"):
		return "jpg"
	if content.startswith(b"\x89PNG\r\n\x1a\n"):
		return "png"
	if len(content) >= 12 and content[4:8] == b"ftyp" and content[8:12] in _HEIF_BRANDS:
		return "heic"
	return None


def bild_lesbar(content: bytes) -> bool:
	"""JPEG/PNG mit Pillow prüfen (Frappe öffnet JPEGs beim Speichern zum Entfernen der
	EXIF-Daten; ein kaputtes Bild würde sonst als 500 enden). Schützt auch vor
	Dekompressionsbomben (Pillow ``MAX_IMAGE_PIXELS``) und Bildern über 60 Megapixel.
	HEIC kann Pillow ohne Zusatzmodul nicht lesen - dort nur die Signatur."""
	from PIL import Image

	try:
		with Image.open(io.BytesIO(content)) as image:
			width, height = image.size
			image.verify()
	except Exception:
		return False
	return 0 < width * height <= MAX_PIXEL


def parse_fotos(value) -> list[tuple[str, bytes]]:
	"""``[{"name": ..., "data": "data:image/jpeg;base64,..."}]`` → ``[(dateiname, inhalt)]``.

	Wirft eine verständliche Meldung bei mehr als 3 Fotos, falschem Typ oder mehr als 10 MiB.
	Gleiche Fotos (gleicher Inhalt) werden nur einmal angehängt."""
	if not value:
		return []
	if not isinstance(value, list):
		frappe.throw(_("Invalid request"))
	if len(value) > MAX_FOTOS:
		frappe.throw(_("Bitte höchstens {0} Fotos hochladen.").format(MAX_FOTOS))

	fotos, seen = [], set()
	max_b64 = (MAX_FOTO_BYTES + 2) // 3 * 4
	for foto in value:
		raw = foto.get("data") if isinstance(foto, dict) else foto
		match = _DATAURL.match(cstr(raw).strip())
		if not match:
			frappe.throw(_("Ein Foto konnte nicht gelesen werden. Bitte wählen Sie es erneut aus."))
		b64 = match.group("b64")
		if len(b64) > max_b64:
			frappe.throw(_("Ein Foto ist größer als 10 MB."))
		try:
			content = base64.b64decode(b64, validate=True)
		except (binascii.Error, ValueError):
			frappe.throw(_("Ein Foto konnte nicht gelesen werden. Bitte wählen Sie es erneut aus."))
		if len(content) > MAX_FOTO_BYTES:
			frappe.throw(_("Ein Foto ist größer als 10 MB."))
		typ = foto_typ(content)
		if not typ:
			frappe.throw(_("Bitte nur Fotos im Format JPG, PNG oder HEIC hochladen."))
		if typ != "heic" and not bild_lesbar(content):
			frappe.throw(_("Ein Foto ist beschädigt oder zu groß. Bitte wählen Sie ein anderes."))
		digest = hashlib.sha256(content).hexdigest()
		if digest in seen:
			continue
		seen.add(digest)
		fotos.append((f"foto-{len(fotos) + 1}.{typ}", content))
	return fotos


def attach_fotos(lead, fotos: list[tuple[str, bytes]]):
	"""Fotos als private Dateien am Lead; Felder ``cv_foto_1..3`` bzw. ``image`` setzen."""
	if not fotos:
		return
	meta = frappe.get_meta("Lead")
	felder = [f for f in FOTO_FELDER if meta.has_field(f)]
	values = {}
	for i, (file_name, content) in enumerate(fotos):
		feld = felder[i] if i < len(felder) else None
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"attached_to_doctype": "Lead",
				"attached_to_name": lead.name,
				"attached_to_field": feld,
				"is_private": 1,
				"content": content,
			}
		)
		file_doc.insert(ignore_permissions=True)
		if feld:
			values[feld] = file_doc.file_url
	# frappe_s3_attachment setzt ``image`` selbst, ohne S3 übernehmen wir das
	image = frappe.db.get_value("Lead", lead.name, "image")
	if not image:
		first = frappe.get_all(
			"File",
			filters={"attached_to_doctype": "Lead", "attached_to_name": lead.name},
			fields=["file_url"],
			order_by="creation asc",
			limit=1,
		)
		if first:
			values["image"] = first[0].file_url
	if values:
		frappe.db.set_value("Lead", lead.name, values, update_modified=False)


# ---------------------------------------------------------------------------
# Lead
# ---------------------------------------------------------------------------

# Freitextfelder: Längengrenze gegen Missbrauch (Small Text hat in der DB kein Limit)
MAX_LAENGE = {"cv_beschreibung": MAX_BESCHREIBUNG, "cv_objekt_adresse": 500, "lead_name": 140}


def check_text_lengths(payload):
	for fieldname, limit in MAX_LAENGE.items():
		if len(cstr(payload.get(fieldname))) > limit:
			frappe.throw(_("Eine Eingabe ist zu lang (höchstens {0} Zeichen).").format(limit))


def ensure_utm_source() -> str:
	if not frappe.db.exists("UTM Source", UTM_SOURCE):
		try:
			frappe.get_doc({"doctype": "UTM Source", "description": "Website / Web Form"}).insert(
				ignore_permissions=True, set_name=UTM_SOURCE
			)
		except frappe.DuplicateEntryError:
			pass
	return UTM_SOURCE


def before_insert_lead(doc, method=None):
	"""``doc_events`` Lead ``before_insert``: nur bei Einsendungen dieses Formulars."""
	if not frappe.flags.get("cv_einschaetzung"):
		return
	doc.utm_source = ensure_utm_source()
	if doc.meta.has_field("cv_quelle"):
		doc.cv_quelle = QUELLE
	# ERPNext legt den Kontakt an und speichert ihn in after_insert erneut (link_to_contact);
	# als Gast fehlt dafür das Schreibrecht.
	contact = getattr(doc, "contact_doc", None)
	if contact:
		contact.flags.ignore_permissions = True


# ---------------------------------------------------------------------------
# Seite (Web-Form-Modul get_context)
# ---------------------------------------------------------------------------


def datenschutz_url() -> str:
	url = cstr(frappe.local.conf.get("cv_webform_datenschutz_url")).strip()
	if url.startswith(("https://", "http://", "/")) and not url.startswith("//"):
		return url
	return "/datenschutz"


def embedding_origins() -> list[str]:
	value = frappe.local.conf.get("cv_webform_embedding_domains") or []
	if isinstance(value, str):
		# Text: Leerzeichen/Komma-getrennt oder eine als Text abgelegte Liste - Press' Site Config
		# speichert Werte als Text (z. B. "['http://localhost:8080']", Befund mvp-test)
		value = [o for o in _EMBED_TEXT_SPLIT.split(value) if o]
	return [o.rstrip("/").lower() for o in value if _EMBED_ORIGIN.match(cstr(o).rstrip("/").lower())]


def frame_ancestors_header() -> str | None:
	origins = embedding_origins()
	if not origins:
		return None
	return "frame-ancestors 'self' " + " ".join(origins)


def page_context(context):
	"""Wird aus ``web_form/kostenlose_einschaetzung/kostenlose_einschaetzung.py`` aufgerufen."""
	embed = bool(cint(frappe.form_dict.get("embed")))
	context.cv_embed = embed
	if embed:
		context.hide_navbar = 1
		context.hide_footer = 1
		context.body_class = (cstr(context.get("body_class")) + " cv-embed").strip()

	header = frame_ancestors_header()
	if header:
		frappe.local.response_headers["Content-Security-Policy"] = header

	web_form_doc = context.get("web_form_doc")
	if not web_form_doc:
		return
	meta = frappe.get_meta("Lead")
	for df in web_form_doc.get("web_form_fields") or []:
		fieldname = df.get("fieldname")
		if df.get("fieldtype") == "Select":
			lead_df = meta.get_field(fieldname)
			if lead_df and lead_df.fieldtype == "Select" and lead_df.options:
				df["options"] = lead_df.options
		elif fieldname in HTML_FELDER:
			df["options"] = HTML_FELDER[fieldname]()


def _html_fotos() -> str:
	return (
		'<div class="form-group cv-fotos"><label class="control-label" for="cv_fotos_input">'
		"Fotos (bis zu 3)</label>"
		'<input id="cv_fotos_input" type="file" accept="image/*,.heic,.heif" multiple class="form-control">'
		'<p class="help-box small text-muted">JPG, PNG oder HEIC, je bis 10 MB. Große Fotos werden vor dem '
		"Senden verkleinert.</p>"
		'<ul class="cv-foto-liste list-unstyled"></ul><p class="cv-foto-fehler text-danger small"></p></div>'
	)


def _html_datenschutz() -> str:
	url = escape_html(datenschutz_url())
	return (
		'<div class="form-group cv-datenschutz"><label class="cv-check">'
		'<input type="checkbox" id="cv_datenschutz" required> '
		f'Ich habe die <a href="{url}" target="_blank" rel="noopener">Datenschutzerklärung</a> gelesen und '
		"bin mit der Verarbeitung meiner Angaben und Fotos zur Bearbeitung meiner Anfrage einverstanden. "
		'<span class="text-danger">*</span></label>'
		'<p class="cv-datenschutz-fehler text-danger small"></p></div>'
	)


def _html_honeypot() -> str:
	# für Menschen unsichtbar (CSS), Bots füllen „website“ gern aus
	return (
		'<div class="cv-hp" aria-hidden="true"><label for="cv_hp">Website</label>'
		'<input type="text" id="cv_hp" name="website" tabindex="-1" autocomplete="off"></div>'
	)


HTML_FELDER = {
	"cv_fotos_html": _html_fotos,
	"cv_datenschutz_html": _html_datenschutz,
	"cv_hp_html": _html_honeypot,
}


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


def setup():
	"""after_install / Patch: UTM Source „Website“, Lead-Dubletten per E-Mail erlauben
	(Kund:innen schicken mehrere Anfragen, z. B. für verschiedene Objekte; das Büro führt
	Dubletten zusammen). Danach im CRM-Settings änderbar - wird nicht bei jedem migrate
	erzwungen."""
	ensure_utm_source()
	frappe.db.set_single_value("CRM Settings", "allow_lead_duplication_based_on_emails", 1)
