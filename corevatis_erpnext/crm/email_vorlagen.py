"""Email Templates der App (Terminbestätigung 1.3, Angebot Nachfassen 4.5) - anlegen und pflegen, ohne Desk-Änderungen
zu überschreiben (Review Epic 1, E-1).

Bis 0.2 kam die Vorlage als Fixture: Frappe spielt Fixtures bei **jedem** ``migrate`` mit
``import_doc(force)`` ein (löschen + neu anlegen), eine Anpassung des Büros im Desk (Signatur,
Telefonnummer, Tonfall) war nach dem nächsten Deploy weg. Jetzt gilt dieselbe Regel wie beim
Druckformat-Sync von ``erpnext_druckformate``
(``ensure_email_templates``, aus ``after_install`` und ``after_migrate``):

- Vorlage fehlt → wird angelegt;
- Site-Kopie unverändert seit dem letzten Sync, Quelle unverändert → nichts;
- Quelle geändert, Site-Kopie noch genau so, wie der letzte Sync sie geschrieben hat →
  aktualisiert;
- Site-Kopie im Desk geändert (Inhalts-Hash weicht vom letzten Sync ab) → **bleibt**, Hinweis
  im Log. Die ausgelieferte Fassung zurückholen:
  ``bench --site <site> execute corevatis_erpnext.crm.email_vorlagen.ensure_email_templates --kwargs '{"force": true}'``
  oder einmalig ``cv_email_template_force_sync: 1`` in der site_config.

Die Hashes des letzten Syncs (Quelle und Site-Kopie) liegen je Vorlage als globaler Default
(``corevatis_erpnext:email_template:<Name>``). Sites aus der Fixture-Zeit haben noch keinen:
Eine Kopie gleich der Quelle wird übernommen, eine zuletzt von ``Administrator`` (unter dem
Fixtures und Hooks laufen) geänderte gilt als unverändert und wird aktualisiert, alles andere
bleibt.
"""

import hashlib
import json
from pathlib import Path

import frappe

VORLAGEN_DIR = Path(__file__).resolve().parent / "vorlagen"
# Terminbestätigung (1.3), Nachfassen von Angeboten (4.5)
VORLAGEN = ("terminbestaetigung_besichtigung.json", "angebot_nachfassen.json")
FELDER = ("reference_doctype", "subject", "use_html", "response", "response_html")
STATE_PREFIX = "corevatis_erpnext:email_template:"
FORCE_CONF_KEY = "cv_email_template_force_sync"
SYNC_USERS = frozenset({"Administrator"})


def quellen() -> list[dict]:
	return [json.loads((VORLAGEN_DIR / datei).read_text(encoding="utf-8")) for datei in VORLAGEN]


def _norm(value) -> str:
	if value is None or value == "":
		return ""
	if isinstance(value, bool | int | float):
		return str(int(value))
	return str(value).replace("\r\n", "\n")


def inhalt_hash(werte: dict) -> str:
	payload = {feld: _norm(werte.get(feld)) for feld in FELDER}
	return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def plan(quelle: str, site: str | None, state: dict | None, modified_by: str | None, force=False) -> str:
	"""``create``, ``update``, ``unchanged``, ``adopt`` (nur Zustand merken) oder ``customized``."""
	if site is None:
		return "create"
	if force:
		return "unchanged" if site == quelle and state else "update"
	if state:
		if site != state.get("site"):
			return "customized"
		return "unchanged" if state.get("source") == quelle else "update"
	if site == quelle:
		return "adopt"
	return "update" if modified_by in SYNC_USERS else "customized"


def _site(name: str) -> tuple[str | None, str | None]:
	werte = frappe.db.get_value("Email Template", name, [*FELDER, "modified_by"], as_dict=True)
	if not werte:
		return None, None
	return inhalt_hash(werte), werte.modified_by


def _state(name: str) -> dict | None:
	raw = frappe.db.get_global(STATE_PREFIX + name)
	try:
		state = json.loads(raw) if raw else None
	except ValueError:
		return None
	return state if isinstance(state, dict) else None


def _set_state(name: str, quelle: str) -> None:
	site, _modified_by = _site(name)
	frappe.db.set_global(STATE_PREFIX + name, json.dumps({"source": quelle, "site": site}))


def ensure_email_templates(force: bool = False) -> dict[str, list[str]]:
	force = bool(force) or bool(frappe.conf.get(FORCE_CONF_KEY))
	bericht: dict[str, list[str]] = {a: [] for a in ("create", "update", "unchanged", "adopt", "customized")}
	for vorlage in quellen():
		name = vorlage["name"]
		werte = {feld: vorlage.get(feld) for feld in FELDER}
		quelle = inhalt_hash(werte)
		site, modified_by = _site(name)
		aktion = plan(quelle, site, _state(name), modified_by, force=force)
		bericht[aktion].append(name)
		if aktion == "create":
			doc = frappe.get_doc({"doctype": "Email Template", "name": name, **werte})
			doc.insert(ignore_permissions=True, set_name=name)
		elif aktion == "update":
			doc = frappe.get_doc("Email Template", name)
			doc.update(werte)
			doc.save(ignore_permissions=True)
		elif aktion == "customized":
			meldung = (
				f"Email Template {name!r} wurde auf dieser Site geändert - nicht überschrieben. "
				f"Ausgelieferte Fassung: ensure_email_templates(force=True)."
			)
			print(meldung)
			frappe.logger("corevatis_erpnext").warning(meldung)
		if aktion in ("create", "update", "adopt"):
			_set_state(name, quelle)
	return bericht
