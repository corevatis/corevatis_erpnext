"""Termin aus der Anfrage in der App (Durchstich A10): Sync-Entität ``termin_anlegen``.

Rückmeldung 31.10.: „In der App kann ich unter Anfragen den Status auf „Termin vereinbaren“
setzen, danach passiert nichts - ich kann nicht sagen, wann der Termin ist.“ Der Status allein
legte keinen Termin an; den konnte nur der Desk (``crm.termin.termin_anlegen``). Jetzt legt die
App den Termin selbst an - offline über die Sync-Warteschlange des Kerns.

Der Kern (``corevatis.api.sync_schema``) ruft diesen Handler über den Hook
``corevatis_sync_handlers`` (``hooks.py``) mit ``(mutation, device_id)`` auf. Rückgabe ist das
Ergebnis als Dict (``applied``/``rejected``); Frappe-Ausnahmen setzt der Kern auf den Savepoint der
Mutation zurück und speichert sie als ``rejected``. Kein Import aus ``corevatis`` (Lizenzgrenze,
import-linter): Kern-Funktionen für die Antwort (Terminzeile, Anfrage) kommen per
``frappe.get_attr``, wenn der Kern installiert ist, sonst gibt es die schlanke Fassung.

Payload ``{"lead", "beginn", "dauer"?, "meister", "notiz"?, "trotzdem"?}``:

- Rechte wie „Termin anlegen“ im Desk: Rollen Büro/Meister (``termin.darf_termine``) und
  Schreibrecht an der Anfrage; ein Monteur darf keinen Termin anlegen (``PERMISSION_DENIED``,
  ``grund = keine_rolle``).
- ``beginn`` (``YYYY-MM-DD HH:MM[:SS]``, Ortszeit der Site) höchstens einen Tag in der
  Vergangenheit (``zeitpunkt_vergangen``) und höchstens zwei Jahre voraus (``zeitpunkt_zu_weit``);
  ``dauer`` 15-720 Minuten (Standard 60, ``dauer_ungueltig``); ``meister`` aktiver Systembenutzer
  mit Rolle Meister (``meister_ungueltig``); ``notiz`` intern, höchstens ``NOTIZ_MAX`` Zeichen.
- Legt den Termin über ``crm.termin.termin_anlegen`` an (Event, Zuweisung an den Meister,
  Vortermin der Anfrage abgesagt, Workflow „Termin vereinbaren“ bei „Neu“ - nie zurück), **ohne**
  Kundenmail (``bestaetigung=0``): Betreff und Text der Bestätigung kommen in der Antwort mit, die
  App gibt sie per Teilen weiter. Der Doppelbuchungsschutz der Terminplanung (D14) gilt wie im
  Desk: eine Überschneidung (``crm.termin.TerminKonflikt``) kommt als ``rejected``/``CONFLICT`` mit
  ``details.grund = ueberschneidung``, dem Klartext als Meldung und ``details.konflikte`` (je Eintrag
  ``meister``, ``von``, ``bis``, ``ort``, ``fahrzeit_min``, ``text``) zurück; ``trotzdem`` wird
  durchgereicht (die App bietet „Trotzdem anlegen“, Vermerk am Termin).
- Idempotent über das Sync-Journal des Kerns (``client_id`` + ``idempotency_key``): dieselbe
  Mutation erneut → ``duplicate`` mit dem ersten Ergebnis, kein zweiter Termin.

``data``: Terminzeile wie ``befunde`` (``corevatis.api.befund.termin_zeilen``: ``termin``,
``beginn``, ``ende``, ``status``, ``lead``, ``lead_name``, ``lead_status``, ``adresse``, ``phase``,
``laufend`` …) plus ``meister``, ``meister_name``, ``eigener`` (dem Aufrufer zugewiesen → gehört in
seine Terminliste), ``abgesagt`` (Vortermin), ``hinweise``, ``anfrage`` (wie ``leads``:
``corevatis.api.lesen.lead_row``) und ``bestaetigung`` (``betreff``, ``text``).
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, get_fullname, now_datetime, strip_html_tags

from corevatis_erpnext.crm import termin

ENTITAET = "termin_anlegen"
NOTIZ_MAX = 2000
VERGANGENHEIT_MAX = timedelta(days=1)
ZUKUNFT_MAX = timedelta(days=730)
CORE_APP = "corevatis"
KERN_TERMINZEILEN = "corevatis.api.befund.termin_zeilen"
KERN_LEAD_ROW = "corevatis.api.lesen.lead_row"
# wie corevatis.api.versionen.FORMAT: Versionen zeichengenau vergleichbar (Mikrosekunden immer)
VERSION_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


class Abgelehnt(Exception):
	"""Fachliche Ablehnung mit Sync-Fehlercode (wird zum Ergebnis ``rejected``)."""

	def __init__(self, code: str, message: str, grund: str | None = None, details: dict | None = None):
		super().__init__(message)
		self.code = code
		self.message = message
		self.grund = grund
		self.details = details


def _rejected(code: str, message: str, grund: str | None = None, details: dict | None = None) -> dict:
	error: dict = {"code": code, "message": message}
	if grund or details:
		error["details"] = {**(details or {}), **({"grund": grund} if grund else {})}
	return {"status": "rejected", "error": error}


def _version(value) -> str | None:
	if value in (None, ""):
		return None
	return get_datetime(value).strftime(VERSION_FORMAT)


def _s(value) -> str | None:
	return None if value in (None, "") else str(value)


def _kern(pfad: str):
	"""Kern-Funktion per Modulpfad, wenn ``corevatis`` installiert ist (kein Import, Lizenzgrenze)."""
	if CORE_APP not in frappe.get_installed_apps():
		return None
	return frappe.get_attr(pfad)


# --- Handler --------------------------------------------------------------------------------


def termin_anlegen(mutation: dict, device_id: str | None = None) -> dict:
	"""Sync-Handler ``termin_anlegen`` (Hook ``corevatis_sync_handlers``)."""
	try:
		return _anlegen(mutation)
	except Abgelehnt as exc:
		return _rejected(exc.code, exc.message, exc.grund, exc.details)


def _anlegen(mutation: dict) -> dict:
	if mutation.get("op") != "create":
		raise Abgelehnt("VALIDATION_ERROR", f"{ENTITAET} supports op 'create' only")
	payload = mutation.get("payload")
	if not isinstance(payload, dict):
		raise Abgelehnt("VALIDATION_ERROR", "payload must be an object")
	# Rechte wie im Desk (Rollen Büro/Meister, System Manager); Monteur legt keine Termine an
	if not termin.darf_termine():
		raise Abgelehnt("PERMISSION_DENIED", "appointments require role Büro or Meister", "keine_rolle")
	lead = _lead(payload)
	if not frappe.has_permission("Lead", "write", doc=lead):
		raise Abgelehnt("PERMISSION_DENIED", "no write permission on lead")

	beginn = _beginn(payload)
	dauer = _dauer(payload)
	meister = _meister(payload)
	notiz = _notiz(payload)
	trotzdem = cint(payload.get("trotzdem"))

	punkt = "cv_termin_mobil_" + frappe.generate_hash(length=8)
	frappe.db.savepoint(punkt)
	try:
		ergebnis = termin.termin_anlegen(
			lead=lead,
			beginn=beginn.strftime("%Y-%m-%d %H:%M:%S"),
			meister=meister,
			dauer=dauer,
			notiz=notiz,
			bestaetigung=0,
			trotzdem=trotzdem,
		)
	except termin.TerminKonflikt as exc:
		# Überschneidung mit der Planung des Meisters (D14): Klartext und Liste, die App bietet
		# „Trotzdem anlegen“. Die Desk-Beigaben (Antwortfeld, Meldung) gehören nicht in die Sync-Antwort.
		frappe.db.rollback(save_point=punkt)
		frappe.local.response.pop("konflikte", None)
		raise Abgelehnt(
			"CONFLICT",
			_klartext(exc),
			"ueberschneidung",
			{"konflikte": list(exc.konflikte or []), "meister": exc.meister or meister},
		)

	event = ergebnis["event"]
	zeile = terminzeile(event, lead, meister)
	zeile["abgesagt"] = ergebnis.get("abgesagt")
	zeile["hinweise"] = list(ergebnis.get("warnungen") or [])
	zeile["anfrage"] = anfrage(lead)
	inhalt = ergebnis.get("bestaetigung") or {}
	zeile["bestaetigung"] = {"betreff": inhalt.get("betreff"), "text": inhalt.get("text")}
	return {
		"status": "applied",
		"server_id": event,
		"server_version": _version(frappe.db.get_value("Event", event, "modified")),
		"data": zeile,
		"_ref": ("Event", event),
	}


# --- Payload --------------------------------------------------------------------------------


def _lead(payload: dict) -> str:
	lead = payload.get("lead")
	if not isinstance(lead, str) or not lead.strip():
		raise Abgelehnt("VALIDATION_ERROR", "payload.lead is required")
	lead = lead.strip()
	if not frappe.db.exists("Lead", lead):
		raise Abgelehnt("NOT_FOUND", f"lead '{lead}' not found")
	return lead


def _beginn(payload: dict):
	wert = payload.get("beginn")
	if not isinstance(wert, str) or not wert.strip():
		raise Abgelehnt("VALIDATION_ERROR", "payload.beginn is required", "beginn_ungueltig")
	try:
		start = get_datetime(wert.strip().replace("T", " ")[:19])
	except (ValueError, TypeError, OverflowError):
		start = None
	if not start:
		raise Abgelehnt("VALIDATION_ERROR", "payload.beginn is not a valid datetime", "beginn_ungueltig")
	jetzt = now_datetime()
	if start < jetzt - VERGANGENHEIT_MAX:
		raise Abgelehnt(
			"VALIDATION_ERROR",
			_("Der Termin liegt in der Vergangenheit (höchstens ein Tag zurück)."),
			"zeitpunkt_vergangen",
		)
	if start > jetzt + ZUKUNFT_MAX:
		raise Abgelehnt(
			"VALIDATION_ERROR", _("Der Termin liegt mehr als zwei Jahre voraus."), "zeitpunkt_zu_weit"
		)
	return start


def _dauer(payload: dict) -> int:
	wert = payload.get("dauer")
	if wert in (None, ""):
		return 60
	text = str(wert).strip()
	if isinstance(wert, bool) or not text.isdigit():
		raise Abgelehnt("VALIDATION_ERROR", "payload.dauer must be whole minutes", "dauer_ungueltig")
	minuten = int(text)
	if not termin.DAUER_MIN <= minuten <= termin.DAUER_MAX:
		raise Abgelehnt(
			"VALIDATION_ERROR",
			_("Die Dauer muss zwischen {0} und {1} Minuten liegen.").format(
				termin.DAUER_MIN, termin.DAUER_MAX
			),
			"dauer_ungueltig",
		)
	return minuten


def _meister(payload: dict) -> str:
	wert = payload.get("meister")
	if not isinstance(wert, str) or not wert.strip():
		raise Abgelehnt("VALIDATION_ERROR", "payload.meister is required", "meister_ungueltig")
	wert = wert.strip()
	if wert not in {m["name"] for m in termin._meister()}:
		raise Abgelehnt(
			"VALIDATION_ERROR",
			_("{0} ist kein aktiver Benutzer mit der Rolle Meister.").format(wert),
			"meister_ungueltig",
		)
	return wert


def _notiz(payload: dict) -> str | None:
	wert = payload.get("notiz")
	if wert in (None, ""):
		return None
	if not isinstance(wert, str):
		raise Abgelehnt("VALIDATION_ERROR", "payload.notiz must be a string", "notiz_ungueltig")
	wert = wert.strip()
	if len(wert) > NOTIZ_MAX:
		raise Abgelehnt("VALIDATION_ERROR", f"payload.notiz exceeds {NOTIZ_MAX} characters", "notiz_zu_lang")
	return wert or None


def _klartext(exc: Exception) -> str:
	text = getattr(exc, "grund", None) or str(exc)
	return (strip_html_tags(text) or type(exc).__name__).strip()[:500]


# --- Antwort --------------------------------------------------------------------------------


def terminzeile(event: str, lead: str, meister: str) -> dict:
	"""Terminzeile wie ``befunde`` (Kern ``termin_zeilen``, Rechte des Benutzers), sonst die schlanke
	Fassung aus Event und Anfrage; immer mit ``meister``, ``meister_name`` und ``eigener``."""
	ev = frappe.db.get_value(
		"Event", event, ["name", "subject", "starts_on", "ends_on", "status"], as_dict=True
	)
	eintrag = {
		"termin": ev.name,
		"betreff": ev.subject,
		"beginn": _s(ev.starts_on),
		"ende": _s(ev.ends_on),
		"status": ev.status,
		"lead": lead,
	}
	zeile = None
	zeilen = _kern(KERN_TERMINZEILEN)
	if zeilen:
		try:
			rows = zeilen(frappe.session.user, [dict(eintrag)])
			zeile = rows[0] if rows else None
		except Exception:
			frappe.log_error(title="termin_anlegen: Terminzeile des Kerns", message=frappe.get_traceback())
	if zeile is None:
		felder = ["lead_name", "company_name", "cv_status", "cv_objekt_adresse"]
		meta = frappe.get_meta("Lead")
		if meta.has_field("cv_objekt"):
			felder.append("cv_objekt")
		lr = frappe.db.get_value("Lead", lead, felder, as_dict=True) or frappe._dict()
		zeile = {
			**eintrag,
			"lead_name": lr.get("lead_name") or lr.get("company_name"),
			"lead_status": lr.get("cv_status"),
			"adresse": lr.get("cv_objekt_adresse"),
			"objekt": lr.get("cv_objekt"),
			"objekt_titel": None,
			"befunde": 0,
			"phase": None,
			"laufend": True,
			"aufmass": None,
			"angebot": None,
		}
	zeile["meister"] = meister
	zeile["meister_name"] = get_fullname(meister)
	zeile["eigener"] = meister == frappe.session.user
	return zeile


def anfrage(lead: str) -> dict:
	"""Die Anfrage wie in ``leads`` (Kern ``lead_row``), sonst die Felder, die die App zum Anzeigen des
	Termins braucht."""
	doc = frappe.get_doc("Lead", lead)
	lead_row = _kern(KERN_LEAD_ROW)
	if lead_row:
		try:
			return lead_row(doc)
		except Exception:
			frappe.log_error(title="termin_anlegen: Anfrage des Kerns", message=frappe.get_traceback())
	event = doc.get(termin.FELD_TERMIN) if doc.meta.has_field(termin.FELD_TERMIN) else None
	details = None
	if event:
		ev = frappe.db.get_value("Event", event, ["starts_on", "ends_on", "status"], as_dict=True)
		if ev and ev.status != "Cancelled":
			user = termin.meister_des_termins(frappe._dict(name=event))
			details = {
				"name": event,
				"beginn": _s(ev.starts_on),
				"ende": _s(ev.ends_on),
				"status": ev.status,
				"meister": user,
				"meister_name": get_fullname(user) if user else None,
			}
	return {
		"name": doc.name,
		"lead_name": doc.get("lead_name"),
		"company_name": doc.get("company_name"),
		"cv_status": doc.get("cv_status"),
		"cv_termin": event,
		"termin": details["beginn"] if details else None,
		"termin_details": details,
		"version": _version(doc.modified),
	}
