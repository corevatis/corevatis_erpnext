"""Web Form „Kostenlose Einschätzung“ (Aufgabe 1.2): Gast-Anfrage → Lead mit bis zu 3 Fotos.

Ablauf einer Einsendung (``POST /api/method/frappe.website.doctype.web_form.web_form.accept``):

1. ``hooks.override_whitelisted_methods`` leitet ``accept`` hierher um. Andere Web Forms
   gehen unverändert an Frappe weiter.
2. Für dieses Formular: Rate-Limit je Client-IP, Honeypot (gefüllt → scheinbarer Erfolg,
   kein Lead), Datenschutz-Zustimmung (Pflicht), Fotos prüfen (JPEG/PNG/HEIC nach
   Dateiinhalt, je höchstens 10 MiB, höchstens 3).
3. Frappes ``accept`` legt den Lead an (Web-Form-Felder, Pflichtfelder, Select-Werte);
   ``before_insert_lead`` setzt Quelle, Symptome, Beschreibung (CRM-Notiz).
4. Die Fotos werden danach als **private** ``File`` am Lead angelegt (``is_private=1``).
   Mit ``frappe_s3_attachment`` lädt dessen ``File.after_insert`` sie in den Bucket.

Gäste brauchen dafür **kein** ``allow_guests_to_upload_files``: ``upload_file`` bleibt für
Gäste gesperrt, Dateien kommen nur über dieses Formular (Base64 im ``accept``-Aufruf).

site_config-Schlüssel (alle optional):
    cv_webform_rate_limit         Einsendungen je IP und Zeitfenster, Standard 10 (0 = aus)
    cv_webform_rate_window        Zeitfenster in Sekunden, Standard 3600
    cv_webform_embedding_domains  erlaubte Einbettungs-Origins (Liste oder durch Leerzeichen
                                  getrennt), z. B. ["https://www.example.de"]; setzt
                                  ``Content-Security-Policy: frame-ancestors`` nur für dieses
                                  Formular
    cv_webform_datenschutz_url    Link der Datenschutzerklärung, Standard ``/datenschutz``
"""

import base64
import binascii
import hashlib
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, cstr, escape_html, now_datetime

WEB_FORM = "kostenlose-einschaetzung"
ROUTE = "kostenlose-einschaetzung"
FRAPPE_ACCEPT = "frappe.website.doctype.web_form.web_form.accept"

QUELLE = "Web Form"
UTM_SOURCE = "Website"

# Schlüssel im ``data``-JSON, die keine Lead-Felder sind (Formular-JS setzt sie)
KEY_HONEYPOT = "cv_hp"
KEY_DATENSCHUTZ = "cv_datenschutz"
KEY_FOTOS = "cv_fotos"
KEY_SYMPTOME = "cv_symptome"
KEY_BESCHREIBUNG = "cv_beschreibung"

MAX_FOTOS = 3
MAX_FOTO_BYTES = 10 * 1024 * 1024
MAX_BESCHREIBUNG = 5000
FOTO_FELDER = ("cv_foto_1", "cv_foto_2", "cv_foto_3")

RATE_LIMIT = 10
RATE_WINDOW = 3600

# Fallback, falls ``Lead.cv_symptome`` (Aufgabe 1.1) keine Optionen mitbringt
SYMPTOME = (
	"Feuchte Wände",
	"Schimmel",
	"Salzausblühungen",
	"Wasser nach Regen",
	"Abplatzender Putz/Farbe",
	"Muffiger Geruch",
	"Sonstiges",
)

_DATAURL = re.compile(r"^data:(?P<mime>[\w.+/-]*)(?:;[\w.+=-]+)*;base64,(?P<b64>[A-Za-z0-9+/=]+)$")
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
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
	symptome = clean_symptome(payload.get(KEY_SYMPTOME))
	if not symptome:
		frappe.throw(_("Bitte wählen Sie mindestens ein Symptom aus (notfalls „Sonstiges“)."))
	extras = frappe._dict(
		symptome=symptome,
		beschreibung=clean_beschreibung(payload.get(KEY_BESCHREIBUNG)),
	)
	for key in (KEY_HONEYPOT, KEY_DATENSCHUTZ, KEY_FOTOS, KEY_SYMPTOME, KEY_BESCHREIBUNG):
		payload.pop(key, None)

	frappe_accept = frappe.get_attr(FRAPPE_ACCEPT)
	frappe.flags.cv_einschaetzung = extras
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


def client_ip() -> str:
	"""Client-IP: ``CF-Connecting-IP`` (setzt Cloudflare, vom Client nicht fälschbar), sonst
	Frappes ``request_ip`` (erster Eintrag aus ``X-Forwarded-For``)."""
	return cstr(
		frappe.get_request_header("CF-Connecting-IP") or frappe.local.request_ip or "unbekannt"
	).strip()


def rate_limit_config() -> tuple[int, int]:
	conf = frappe.local.conf
	limit = conf.get("cv_webform_rate_limit")
	window = conf.get("cv_webform_rate_window")
	return (RATE_LIMIT if limit in (None, "") else cint(limit), cint(window) or RATE_WINDOW)


def check_rate_limit():
	"""Höchstens ``limit`` Einsendungen je IP und Zeitfenster (zusätzlich zu Frappes eigenem
	Limit auf ``accept``: 10 je Minute). Zählt jeden Versuch, auch abgewiesene."""
	if not getattr(frappe.local, "request", None):
		return
	limit, window = rate_limit_config()
	if limit <= 0:
		return
	key = frappe.cache.make_key(f"cv:webform:{WEB_FORM}:{client_ip()}")
	count = frappe.cache.incrby(key, 1)
	if count == 1:
		frappe.cache.expire(key, window)
	if count > limit:
		frappe.throw(
			_("Zu viele Anfragen. Bitte versuchen Sie es später erneut oder rufen Sie uns an."),
			frappe.RateLimitExceededError,
		)


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
# Symptome, Beschreibung
# ---------------------------------------------------------------------------


def symptom_field():
	return frappe.get_meta("Lead").get_field("cv_symptome")


def _symptom_link(df):
	"""Für Table MultiSelect: (Kind-DocType, Link-Feld, Ziel-DocType) oder None."""
	if not df or df.fieldtype != "Table MultiSelect" or not df.options:
		return None
	child = frappe.get_meta(df.options)
	link = next((f for f in child.fields if f.fieldtype == "Link"), None)
	return (df.options, link.fieldname, link.options) if link else None


def symptom_options() -> list[str]:
	"""Auswahl für das Formular: Optionen von ``Lead.cv_symptome`` (Select-Zeilen bzw. Einträge
	des Link-Ziels bei Table MultiSelect), sonst ``SYMPTOME``."""
	df = symptom_field()
	if df and df.fieldtype == "Select" and df.options:
		return [o for o in df.options.split("\n") if o.strip()]
	link = _symptom_link(df)
	if link:
		names = frappe.get_all(link[2], pluck="name", order_by="name asc", limit=50)
		if names:
			return names
	return list(SYMPTOME)


def clean_symptome(value) -> list[str]:
	if not value:
		return []
	if isinstance(value, str):
		value = [value]
	if not isinstance(value, list):
		return []
	allowed = symptom_options()
	return [s for s in allowed if s in {cstr(v) for v in value}]


def clean_beschreibung(value) -> str:
	text = cstr(value).replace("\r\n", "\n").strip()
	if len(text) > MAX_BESCHREIBUNG:
		frappe.throw(_("Die Beschreibung ist zu lang (höchstens {0} Zeichen).").format(MAX_BESCHREIBUNG))
	return text


def _set_symptome(doc, symptome: list[str]):
	if not symptome:
		return
	df = symptom_field()
	if not df:
		return
	link = _symptom_link(df)
	if link:
		_child, fieldname, _target = link
		doc.set(df.fieldname, [{fieldname: s} for s in symptome])
	elif df.fieldtype in ("Small Text", "Text", "Long Text", "Data"):
		doc.set(df.fieldname, "\n".join(symptome))
	elif df.fieldtype == "Select":
		doc.set(df.fieldname, symptome[0])


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
	extras = frappe.flags.get("cv_einschaetzung")
	if not extras:
		return
	doc.utm_source = ensure_utm_source()
	if doc.meta.has_field("cv_quelle"):
		doc.cv_quelle = QUELLE
	_set_symptome(doc, extras.symptome)
	if extras.beschreibung:
		doc.append(
			"notes",
			{
				"note": "<p>" + escape_html(extras.beschreibung).replace("\n", "<br>") + "</p>",
				"added_on": now_datetime(),
			},
		)
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
		value = value.split()
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


def _html_symptome() -> str:
	boxes = "".join(
		'<label class="cv-check"><input type="checkbox" name="cv_symptom" value="{0}"> {0}</label>'.format(
			escape_html(s)
		)
		for s in symptom_options()
	)
	return (
		'<div class="form-group cv-symptome"><label class="control-label">Symptome</label>'
		f'<div class="cv-check-group">{boxes}</div>'
		'<p class="help-box small text-muted">Mehrfachauswahl möglich</p></div>'
	)


def _html_beschreibung() -> str:
	return (
		'<div class="form-group"><label class="control-label" for="cv_beschreibung">Beschreibung</label>'
		f'<textarea id="cv_beschreibung" class="form-control" rows="5" maxlength="{MAX_BESCHREIBUNG}" '
		'placeholder="Was ist Ihnen aufgefallen? Seit wann? Wo genau?"></textarea></div>'
	)


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
	"cv_symptome_html": _html_symptome,
	"cv_beschreibung_html": _html_beschreibung,
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
