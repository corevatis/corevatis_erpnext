"""Kundenzugang per Link (P2.7, Handwerker#91): Token erzeugen, prüfen, Rate-Limit.

Kein Benutzerkonto, kein Passwort, keine E-Mail: Das Büro erzeugt am Vorgang (Anfrage, Kunde, Projekt,
Angebot …) einen **Kundenzugang**. Der Link ``/kunde/<token>`` trägt ein Zufallstoken
(``secrets.token_urlsafe(32)``); gespeichert wird nur sein SHA-256 (``token_hash``). Der Klartext ist
genau einmal sichtbar (Dialog mit Link und QR-Code) und wird außerhalb des Systems weitergegeben
(eigenes Mailprogramm, WhatsApp, Ausdruck).

Prüfung (``pruefen``): Hash nachschlagen, zeitkonstant vergleichen, nicht widerrufen, nicht abgelaufen.
Unbekannt, abgelaufen und widerrufen sind für den Aufrufer dasselbe (``None`` → 404), damit sich Tokens
nicht ausforschen lassen. ``anfrage_pruefen`` zählt dazu je IP Seitenaufrufe und Fehlversuche und je
Zugang Aktionen (Redis-Zähler mit Zeitfenster); über dem Limit gilt der Zugang als unbekannt.

Ein neuer Link widerruft die aktiven Zugänge desselben Vorgangs (ein Link je Vorgang).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import re
import secrets

import frappe
from frappe import _
from frappe.utils import add_days, cint, cstr, get_url, getdate, now_datetime, today

DOCTYPE = "Kundenzugang"
ROUTE = "kunde"
TOKEN_BYTES = 32
TOKEN_MUSTER = re.compile(r"^[A-Za-z0-9_-]{20,128}$")
GUELTIG_TAGE = 90
GUELTIG_TAGE_MAX = 365
ROLLEN_VERWALTEN = ("Büro", "System Manager")
# Vorgänge, an denen das Büro einen Zugang anlegen kann
VORGANG_DOCTYPES = ("Lead", "Customer", "Project", "Quotation", "Sales Order", "Sales Invoice")
ANKER_DOCTYPES = ("Lead", "Project", "Quotation", "Sales Order")

# Rate-Limits (Zeitfenster in Sekunden)
FENSTER = 600
LIMIT_SEITE_IP = 120  # Seitenaufrufe je IP im Fenster
LIMIT_FEHL_IP = 20  # unbekannte oder ungültige Tokens je IP im Fenster
LIMIT_AKTION_ZUGANG = 40  # Aktionen (Bestätigen, Annehmen, Upload, Download) je Zugang im Fenster
MAX_UPLOADS = 30  # Fotos je Zugang insgesamt


class ZugangFehler(frappe.ValidationError):
	pass


# --- Rechte (Desk) ----------------------------------------------------------------------------------


def darf_verwalten(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(set(ROLLEN_VERWALTEN) & set(frappe.get_roles(user)))


def pruefe_rolle() -> None:
	if not darf_verwalten():
		frappe.throw(_("Kundenzugänge legt nur das Büro an."), frappe.PermissionError)


# --- Token --------------------------------------------------------------------------------------------


def token_neu() -> str:
	return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> str:
	return hashlib.sha256(cstr(token).encode("utf-8")).hexdigest()


def url(token: str) -> str:
	return get_url(f"/{ROUTE}/{token}")


def qr_svg(text: str) -> str:
	"""QR-Code als SVG (``pyqrcode`` ist eine Abhängigkeit von Frappe, kein externer Dienst)."""
	try:
		import pyqrcode
	except ImportError:
		return ""
	puffer = io.BytesIO()
	pyqrcode.create(text, error="M").svg(
		puffer, scale=4, quiet_zone=2, module_color="#111", background="#fff"
	)
	return puffer.getvalue().decode("utf-8")


# --- Anker --------------------------------------------------------------------------------------------


def anker_fuer(doctype: str, name: str) -> tuple[str, str]:
	"""Vorgang (Anfrage, sonst Projekt, Angebot oder Auftrag) zu dem Datensatz, an dem das Büro steht."""
	from corevatis_erpnext.prozess import kette

	if doctype not in VORGANG_DOCTYPES:
		frappe.throw(_("Für {0} gibt es keinen Kundenzugang.").format(_(doctype)), ZugangFehler)
	if not name or not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} nicht gefunden.").format(_(doctype), name), frappe.DoesNotExistError)
	if doctype == "Customer":
		lead = frappe.db.get_value("Customer", name, "lead_name")
		if lead and frappe.db.exists("Lead", lead):
			return "Lead", lead
		projekt = frappe.db.get_value(
			"Project", {"customer": name, "status": ["!=", "Cancelled"]}, "name", order_by="creation desc"
		)
		if projekt:
			return "Project", projekt
		frappe.throw(_("Zu diesem Kunden gibt es noch keinen Vorgang (Anfrage oder Projekt)."), ZugangFehler)
	lead = kette.lead_von(doctype, name)
	if lead:
		return "Lead", lead
	if doctype in ANKER_DOCTYPES:
		return doctype, name
	if doctype == "Sales Invoice":
		w = frappe.db.get_value("Sales Invoice", name, ["project", "customer"], as_dict=True) or {}
		if w.get("project"):
			return "Project", w.project
		if w.get("customer"):
			return anker_fuer("Customer", w.customer)
	frappe.throw(_("Zu {0} lässt sich kein Vorgang ermitteln.").format(name), ZugangFehler)
	return doctype, name  # pragma: no cover (frappe.throw bricht ab)


def kunden_name(anker_doctype: str, anker: str) -> str:
	if anker_doctype == "Lead":
		w = frappe.db.get_value("Lead", anker, ["lead_name", "company_name"], as_dict=True) or {}
		return cstr(w.get("lead_name") or w.get("company_name"))
	if anker_doctype == "Project":
		w = frappe.db.get_value("Project", anker, ["customer"], as_dict=True) or {}
		if w.get("customer"):
			return cstr(frappe.db.get_value("Customer", w.customer, "customer_name") or w.customer)
		return ""
	return cstr(frappe.db.get_value(anker_doctype, anker, "customer_name") or "")


# --- Anlegen und Widerrufen -------------------------------------------------------------------------


def widerrufen(name: str, grund: str | None = None) -> None:
	doc = frappe.get_doc(DOCTYPE, name)
	if cint(doc.widerrufen):
		return
	doc.widerrufen = 1
	doc.widerrufen_am = now_datetime()
	doc.flags.ignore_permissions = True
	doc.save()
	if grund:
		doc.add_comment("Info", grund)


def erzeugen(doctype: str, name: str, gueltig_tage: int | None = None) -> dict:
	"""Neuen Zugang anlegen (frühere aktive Zugänge des Vorgangs widerrufen) und den Klartext-Link
	**einmal** zurückgeben. Rechte: Büro/System Manager und Leserecht am Datensatz."""
	pruefe_rolle()
	frappe.has_permission(doctype, "read", name, throw=True)
	anker_doctype, anker = anker_fuer(doctype, name)
	tage = cint(gueltig_tage) or GUELTIG_TAGE
	if not 1 <= tage <= GUELTIG_TAGE_MAX:
		frappe.throw(_("Gültigkeit zwischen 1 und {0} Tagen wählen.").format(GUELTIG_TAGE_MAX), ZugangFehler)
	for alt in frappe.get_all(
		DOCTYPE, filters={"anker_doctype": anker_doctype, "anker": anker, "widerrufen": 0}, pluck="name"
	):
		widerrufen(alt, _("Ersetzt durch einen neuen Link."))
	token = token_neu()
	# Anker, Kundenname und Hash setzt der Controller (``validate``) selbst - nie aus Eingaben
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"vorgang_doctype": doctype,
			"vorgang": name,
			"gueltig_bis": add_days(today(), tage),
		}
	)
	doc.flags.cv_token_klar = token
	doc.flags.cv_geprueft = True  # Leserecht am Vorgang oben geprüft
	doc.flags.cv_stumm = True
	doc.insert(ignore_permissions=True)
	anker_doctype, anker = doc.anker_doctype, doc.anker
	try:
		frappe.get_doc(anker_doctype, anker).add_comment(
			"Info",
			_("Kundenzugang {0} angelegt (gültig bis {1}).").format(
				doc.name, frappe.format(doc.gueltig_bis, {"fieldtype": "Date"})
			),
		)
	except Exception:
		frappe.log_error(title="Kundenzugang: Kommentar am Vorgang")
	link = url(token)
	return {
		"name": doc.name,
		"url": link,
		"qr_svg": qr_svg(link),
		"gueltig_bis": doc.gueltig_bis,
		"anker_doctype": anker_doctype,
		"anker": anker,
		"kunde_name": doc.kunde_name,
	}


# --- Prüfung ------------------------------------------------------------------------------------------

FELDER = (
	"name",
	"token_hash",
	"vorgang_doctype",
	"vorgang",
	"anker_doctype",
	"anker",
	"kunde_name",
	"gueltig_bis",
	"widerrufen",
	"aufrufe",
	"uploads",
)


def pruefen(token) -> frappe._dict | None:
	"""Zugang zum Token oder ``None`` (unbekannt, widerrufen, abgelaufen - ohne Unterschied)."""
	if not isinstance(token, str) or not TOKEN_MUSTER.match(token):
		return None
	h = token_hash(token)
	z = frappe.db.get_value(DOCTYPE, {"token_hash": h}, list(FELDER), as_dict=True)
	if not z or not hmac.compare_digest(cstr(z.token_hash), h):
		return None
	if cint(z.widerrufen) or not z.gueltig_bis or getdate(z.gueltig_bis) < getdate(today()):
		return None
	return z


def _ip() -> str:
	return cstr(getattr(frappe.local, "request_ip", None) or "lokal")


def erlaubt(schluessel: str, limit: int, sekunden: int = FENSTER, zaehlen: bool = True) -> bool:
	"""Zähler im Cache: ``True``, solange ``limit`` im Fenster nicht überschritten ist. Ohne Cache
	wird nicht gesperrt (Fehler landet im Error Log)."""
	key = frappe.cache.make_key(f"cv_kundenportal|{schluessel}")
	try:
		if zaehlen:
			n = cint(frappe.cache.incr(key))
			if n == 1:
				frappe.cache.expire(key, sekunden)
		else:
			n = cint(frappe.cache.get(key) or 0)
	except Exception:
		frappe.log_error(title="Kundenportal: Rate-Limit-Zähler")
		return True
	return n <= limit


def zaehler_loeschen(schluessel: str) -> None:
	frappe.cache.delete(frappe.cache.make_key(f"cv_kundenportal|{schluessel}"))


def anfrage_pruefen(token, aktion: bool = False) -> frappe._dict | None:
	"""``pruefen`` mit Rate-Limit je IP (Seitenaufrufe, Fehlversuche) und je Zugang (Aktionen).
	``None`` bei unbekanntem Token oder über dem Limit - der Aufrufer antwortet neutral (404)."""
	ip = _ip()
	if not erlaubt(f"seite:{ip}", LIMIT_SEITE_IP):
		return None
	if not erlaubt(f"fehl:{ip}", LIMIT_FEHL_IP, zaehlen=False):
		return None
	z = pruefen(token)
	if not z:
		erlaubt(f"fehl:{ip}", LIMIT_FEHL_IP)
		return None
	if aktion and not erlaubt(f"aktion:{z.name}", LIMIT_AKTION_ZUGANG):
		return None
	return z


def nutzung(z: frappe._dict, upload: bool = False) -> None:
	"""Aufrufe/Uploads und letzte Nutzung fortschreiben (ohne ``modified``, ohne Versionen)."""
	werte = {"letzte_nutzung": now_datetime()}
	if upload:
		werte["uploads"] = cint(z.get("uploads")) + 1
	else:
		werte["aufrufe"] = cint(z.get("aufrufe")) + 1
	frappe.db.set_value(DOCTYPE, z.name, werte, update_modified=False)
	for k, v in werte.items():
		z[k] = v
