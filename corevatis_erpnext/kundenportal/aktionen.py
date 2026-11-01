"""Aktionen des Kunden im Portal (P2.7): Gast-Endpunkte hinter dem Token.

Alle Methoden sind ``allow_guest`` und nehmen das Token entgegen; sie leiten jeden Beleg aus dem Zugang
ab (kein frei wählbarer Belegname). Frappe prüft bei Gästen kein CSRF-Token - Schutz ist das geheime
Token im Anfragekörper: Ohne den Link kann niemand eine Aktion auslösen. Server-Arbeit (Annahme,
Dateien, Kommentare) läuft als Administrator (``_System``) nach eigener Prüfung; der Kunde bekommt nur
Klartext-Meldungen, nie Stacktraces (``_fehler``), Fehler landen im Error Log.

- ``termin_bestaetigen``: ``Event.cv_kunde_bestaetigt`` setzen, Kommentar an Termin und Anfrage.
- ``angebot_annehmen``: Unterschrift (PNG aus dem Canvas, Magic Bytes, höchstens 500 KB) prüfen, dann die
  vorhandene Annahme ``angebot.annahme.angebot_annehmen`` (Kunde, Auftrag, Projekt, Anfrage „Gewonnen“),
  Unterschrift als private Datei am Angebot (Mappe 03 Angebot, Dokumentart „Unterschrift Kunde“),
  Zeitpunkt und Datei am Angebot, Kommentar mit Absender-Kennung (gekürzter IP-Hash).
- ``foto_hochladen``: JPEG/PNG/HEIC bis 10 MB, Typ aus den Magic Bytes (``filetype``), Name vom Server,
  private Datei an Anfrage bzw. Projekt (Mappe sortiert als Kundenfoto ein), für den Kunden sichtbar.
- ``datei``: Download eines freigegebenen Dokuments oder des Beleg-PDFs (Angebot, Auftrag, Rechnung) -
  Mappendatei, sonst frisch gedruckt.
"""

from __future__ import annotations

import base64
import hashlib
import re

import frappe
from frappe import _
from frappe.utils import cint, cstr, format_datetime, now_datetime

from corevatis_erpnext.kundenportal import felder as F
from corevatis_erpnext.kundenportal import vorgang as V
from corevatis_erpnext.kundenportal import zugang as Z

MAX_UNTERSCHRIFT = 500 * 1024
MIN_UNTERSCHRIFT = 300
MAX_FOTO = 10 * 1024 * 1024
BILD_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/heic": "heic", "image/heif": "heif"}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
ORDNER_ANGEBOT = "03 Angebot"
DRUCKFORMAT_ANGEBOT = "Angebot Corevatis"
KERN_INHALT = "corevatis.mappe.inhalt.datei_inhalt"
MELDUNG_UNGUELTIG = "Dieser Link ist nicht (mehr) gültig."
MELDUNG_SPAETER = (
	"Das hat gerade nicht geklappt. Bitte versuchen Sie es später noch einmal oder melden Sie sich bei uns."
)


class _System:
	"""Serverarbeit als Administrator nach eigener Prüfung; danach wieder der Gast."""

	def __enter__(self):
		self.user = frappe.session.user
		frappe.set_user("Administrator")
		return self

	def __exit__(self, *args):
		frappe.set_user(self.user)
		return False


def _fehler(meldung: str, status: int = 400) -> dict:
	frappe.local.response["http_status_code"] = status
	return {"ok": False, "meldung": meldung}


def _ungueltig() -> dict:
	return _fehler(_(MELDUNG_UNGUELTIG), 404)


def _kennung() -> str:
	"""Gekürzter Hash der Absenderadresse für das Protokoll (keine IP im Klartext)."""
	return hashlib.sha256(Z._ip().encode("utf-8")).hexdigest()[:16]


def _nutzung_zeit(z) -> None:
	frappe.db.set_value(Z.DOCTYPE, z.name, "letzte_nutzung", now_datetime(), update_modified=False)


# --- Termin -------------------------------------------------------------------------------------------


@frappe.whitelist(allow_guest=True, methods=["POST"])
def termin_bestaetigen(token: str) -> dict:
	z = Z.anfrage_pruefen(token, aktion=True)
	if not z:
		return _ungueltig()
	k = V.kette_fuer(z)
	t = V.termin(k)
	if not t:
		return _fehler(_("Zu diesem Vorgang gibt es keinen Termin."))
	if t["bestaetigt"]:
		return {"ok": True, "bereits": True, "bestaetigt_text": t["bestaetigt_text"]}
	if not t["bestaetigbar"]:
		return _fehler(
			_("Dieser Termin kann online nicht mehr bestätigt werden. Bitte melden Sie sich bei uns.")
		)
	if not F.felder_da():
		return _fehler(_(MELDUNG_SPAETER), 503)
	jetzt = now_datetime()
	with _System():
		frappe.db.set_value(
			"Event",
			t["name"],
			{F.FELD_TERMIN_BESTAETIGT: jetzt, F.FELD_TERMIN_ZUGANG: z.name},
			update_modified=False,
		)
		text = _("Termin vom Kunden über den Kundenzugang {0} bestätigt am {1} (Kennung {2}).").format(
			z.name, format_datetime(jetzt, "dd.MM.yyyy HH:mm"), _kennung()
		)
		frappe.get_doc("Event", t["name"]).add_comment("Info", text)
		if k.lead:
			frappe.get_doc("Lead", k.lead.name).add_comment("Info", text)
		_nutzung_zeit(z)
	return {"ok": True, "bestaetigt_text": format_datetime(jetzt, "dd.MM.yyyy HH:mm")}


# --- Angebot ------------------------------------------------------------------------------------------


def unterschrift_png(wert) -> bytes:
	"""PNG aus dem Canvas (Data-URL oder Base64) prüfen: Typ, Magic Bytes, Größe."""
	s = cstr(wert).strip()
	if not s:
		raise ValueError("leer")
	if s.startswith("data:"):
		kopf, _trenner, s = s.partition(",")
		if "image/png" not in kopf or "base64" not in kopf:
			raise ValueError("kein PNG")
	if len(s) > MAX_UNTERSCHRIFT * 4 // 3 + 64:
		raise ValueError("zu groß")
	try:
		roh = base64.b64decode(s, validate=True)
	except Exception as e:
		raise ValueError("kein Base64") from e
	if not roh.startswith(PNG_MAGIC):
		raise ValueError("kein PNG")
	if not MIN_UNTERSCHRIFT <= len(roh) <= MAX_UNTERSCHRIFT:
		raise ValueError("Größe")
	return roh


def datei_anlegen(
	inhalt: bytes,
	dateiname: str,
	doctype: str,
	name: str,
	ordner: str | None = None,
	dokumentart: str | None = None,
	sichtbar: bool = False,
):
	"""Private Datei am Beleg; Mappenfelder nur, wenn der Kern sie angelegt hat. Herkunft „Webform“
	(Gast), damit die Mappenregeln Kundenuploads als Kundenfoto einsortieren."""
	meta = frappe.get_meta("File")
	werte: dict = {
		"doctype": "File",
		"file_name": dateiname,
		"attached_to_doctype": doctype,
		"attached_to_name": name,
		"is_private": 1,
		"content": inhalt,
	}
	if ordner and meta.has_field("cv_ordner"):
		werte["cv_ordner"] = ordner
	if dokumentart and meta.has_field("cv_dokumentart"):
		werte["cv_dokumentart"] = dokumentart
	if meta.has_field(F.FELD_DATEI_SICHTBAR):
		werte[F.FELD_DATEI_SICHTBAR] = 1 if sichtbar else 0
	datei = frappe.get_doc(werte)
	datei.flags.cv_mappe_intern = True
	datei.flags.ignore_permissions = True
	alt = frappe.flags.get("cv_mappe_herkunft")
	frappe.flags.cv_mappe_herkunft = "Webform"
	try:
		datei.insert(ignore_permissions=True)
	finally:
		frappe.flags.cv_mappe_herkunft = alt
	return datei


@frappe.whitelist(allow_guest=True, methods=["POST"])
def angebot_annehmen(token: str, unterschrift: str | None = None, kenntnis: int | str | None = 0) -> dict:
	z = Z.anfrage_pruefen(token, aktion=True)
	if not z:
		return _ungueltig()
	k = V.kette_fuer(z)
	a = V.angebot(k)
	if not a:
		return _fehler(_("Zu diesem Vorgang liegt kein freigegebenes Angebot vor."))
	if a["status"] == "angenommen":
		return {"ok": True, "bereits": True, "angebot": a["name"], "angenommen_am": a["angenommen_am"]}
	if not a["annehmbar"]:
		return _fehler(
			_("Das Angebot kann online nicht mehr angenommen werden. Bitte melden Sie sich bei uns.")
		)
	if not cint(kenntnis):
		return _fehler(_("Bitte bestätigen Sie, dass Sie das Angebot und die Bedingungen gelesen haben."))
	try:
		png = unterschrift_png(unterschrift)
	except ValueError:
		return _fehler(_("Bitte unterschreiben Sie im Feld (die Unterschrift fehlt oder ist ungültig)."))

	from corevatis_erpnext.angebot import annahme

	jetzt = now_datetime()
	with _System():
		try:
			r = annahme.angebot_annehmen(a["name"], einreichen=1)
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Kundenportal: Annahme {a['name']} fehlgeschlagen")
			return _fehler(_(MELDUNG_SPAETER), 409)
		try:
			datei = datei_anlegen(
				png,
				f"Unterschrift-{a['name']}.png".replace("/", "-"),
				"Quotation",
				a["name"],
				ordner=ORDNER_ANGEBOT,
				dokumentart=V.DOKUMENTART_UNTERSCHRIFT,
			)
			if F.felder_da():
				frappe.db.set_value(
					"Quotation",
					a["name"],
					{F.FELD_ANGEBOT_ANGENOMMEN: jetzt, F.FELD_ANGEBOT_UNTERSCHRIFT: datei.file_url},
					update_modified=False,
				)
			text = _(
				"Angebot vom Kunden über den Kundenzugang {0} angenommen am {1} (Unterschrift {2}, Kennung {3})."
			).format(z.name, format_datetime(jetzt, "dd.MM.yyyy HH:mm"), datei.name, _kennung())
			frappe.get_doc("Quotation", a["name"]).add_comment("Info", text)
			if r.get("auftrag"):
				frappe.get_doc("Sales Order", r["auftrag"]).add_comment("Info", text)
			_nutzung_zeit(z)
		except Exception:
			# Annahme steht; Unterschrift/Protokoll dürfen sie nicht zurückdrehen
			frappe.log_error(title=f"Kundenportal: Unterschrift zu {a['name']}")
	return {
		"ok": True,
		"angebot": a["name"],
		"auftrag": r.get("auftrag"),
		"angenommen_am": format_datetime(jetzt, "dd.MM.yyyy HH:mm"),
	}


# --- Fotos --------------------------------------------------------------------------------------------


def bildtyp(inhalt: bytes) -> str | None:
	"""MIME-Typ aus den Magic Bytes (nur JPEG, PNG, HEIC/HEIF)."""
	try:
		import filetype

		art = filetype.guess(inhalt[:8192])
		mime = art.mime if art else None
	except ImportError:
		mime = None
		if inhalt.startswith(b"\xff\xd8\xff"):
			mime = "image/jpeg"
		elif inhalt.startswith(PNG_MAGIC):
			mime = "image/png"
	return mime if mime in BILD_MIME else None


def _upload_inhalt(datei_b64) -> bytes | None:
	"""Dateiinhalt aus dem Multipart-Feld ``datei`` oder aus ``datei_b64`` (Data-URL)."""
	anfrage = getattr(frappe.local, "request", None)
	if anfrage is not None and getattr(anfrage, "files", None):
		f = anfrage.files.get("datei")
		if f is not None:
			return f.stream.read(MAX_FOTO + 1)
	s = cstr(datei_b64).strip()
	if not s:
		return None
	if s.startswith("data:"):
		s = s.partition(",")[2]
	if len(s) > (MAX_FOTO + 1) * 4 // 3 + 64:
		return b"\0" * (MAX_FOTO + 1)
	try:
		return base64.b64decode(s, validate=True)
	except Exception:
		return None


def _upload_ziel(k: frappe._dict, z) -> tuple[str, str]:
	if k.lead:
		return "Lead", k.lead.name
	if k.projekte:
		return "Project", k.projekte[-1].name
	return z.anker_doctype, z.anker


@frappe.whitelist(allow_guest=True, methods=["POST"])
def foto_hochladen(token: str, datei_b64: str | None = None) -> dict:
	z = Z.anfrage_pruefen(token, aktion=True)
	if not z:
		return _ungueltig()
	if cint(z.uploads) >= Z.MAX_UPLOADS:
		return _fehler(_("Die Höchstzahl an Fotos für diesen Zugang ist erreicht."))
	inhalt = _upload_inhalt(datei_b64)
	if not inhalt:
		return _fehler(_("Keine Datei übermittelt."))
	if len(inhalt) > MAX_FOTO:
		return _fehler(_("Die Datei ist größer als 10 MB."), 413)
	mime = bildtyp(inhalt)
	if not mime:
		return _fehler(_("Nur Fotos (JPEG, PNG, HEIC) sind möglich."), 415)
	k = V.kette_fuer(z)
	ziel_doctype, ziel = _upload_ziel(k, z)
	nr = cint(z.uploads) + 1
	name = re.sub(r"[^A-Za-z0-9._-]+", "-", f"Kundenfoto-{ziel}-{nr:02d}.{BILD_MIME[mime]}")
	with _System():
		datei = datei_anlegen(
			inhalt, name, ziel_doctype, ziel, dokumentart=V.DOKUMENTART_KUNDENFOTO, sichtbar=True
		)
		Z.nutzung(z, upload=True)
		try:
			frappe.get_doc(ziel_doctype, ziel).add_comment(
				"Info",
				_("Kunde hat über den Kundenzugang {0} ein Foto hochgeladen: {1}").format(
					z.name, datei.file_name
				),
			)
		except Exception:
			frappe.log_error(title="Kundenportal: Kommentar zum Foto")
	return {
		"ok": True,
		"datei": datei.name,
		"titel": datei.file_name,
		"uploads": cint(z.uploads),
		"frei": max(0, Z.MAX_UPLOADS - cint(z.uploads)),
	}


# --- Download -----------------------------------------------------------------------------------------


def _inhalt(datei: str) -> bytes | None:
	if "corevatis" in frappe.get_installed_apps():
		try:
			return frappe.get_attr(KERN_INHALT)(datei)
		except Exception:
			frappe.log_error(title="Kundenportal: Datei aus der Mappe")
			return None
	inhalt = frappe.get_doc("File", datei).get_content()
	return inhalt.encode("utf-8") if isinstance(inhalt, str) else inhalt


def _beleg_pdf(k: frappe._dict, beleg: str) -> tuple[bytes | None, str]:
	"""PDF zu „angebot“, „auftrag“ oder einer Rechnung des Vorgangs: Mappendatei, sonst Druck."""
	if beleg == "angebot":
		a = V.angebot(k)
		doctype, name, art, druckformat = "Quotation", a and a["name"], "Angebot", DRUCKFORMAT_ANGEBOT
	elif beleg == "auftrag":
		a = V.auftrag(k)
		doctype, name, art, druckformat = "Sales Order", a and a["name"], "Auftragsbestätigung", None
	else:
		namen = {r["name"] for r in V.rechnungen(k)}
		doctype, name, art, druckformat = "Sales Invoice", beleg if beleg in namen else None, "Rechnung", None
	if not name:
		return None, ""
	dateiname = f"{name}.pdf".replace("/", "-")
	datei = V.beleg_pdf_datei(doctype, name, art)
	if datei:
		inhalt = _inhalt(datei)
		if inhalt:
			return inhalt, dateiname
	if druckformat and not frappe.db.exists("Print Format", druckformat):
		druckformat = None
	try:
		return frappe.get_print(doctype, name, print_format=druckformat, as_pdf=True), dateiname
	except Exception:
		frappe.log_error(title=f"Kundenportal: PDF {name}")
		return None, ""


@frappe.whitelist(allow_guest=True, methods=["GET"])
def datei(token: str, datei: str | None = None, beleg: str | None = None):
	z = Z.anfrage_pruefen(token, aktion=True)
	if not z:
		frappe.local.response["http_status_code"] = 404
		return {"ok": False}
	k = V.kette_fuer(z)
	inhalt, name = None, ""
	with _System():
		if beleg:
			inhalt, name = _beleg_pdf(k, cstr(beleg))
		elif datei and V.datei_erlaubt(k, cstr(datei)):
			f = frappe.db.get_value("File", datei, ["file_name", "file_url"], as_dict=True)
			if f:
				name = cstr(f.file_name or f.file_url).rsplit("/", 1)[-1]
				inhalt = _inhalt(cstr(datei))
		if inhalt:
			_nutzung_zeit(z)
	if not inhalt:
		frappe.local.response["http_status_code"] = 404
		return {"ok": False}
	frappe.local.response.filename = name
	frappe.local.response.filecontent = inhalt
	frappe.local.response.type = "pdf" if name.lower().endswith(".pdf") else "download"
