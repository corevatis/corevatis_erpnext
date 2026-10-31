"""Aufgabe 1.3: Besichtigungstermin aus dem Lead und Terminbestätigung für den Kunden.

Ablauf (Button „Termin anlegen“ am Lead, ``public/js/lead_termin.js``):

1. ``termin_anlegen`` legt ein ``Event`` an (Typ Private, freigegeben für den Meister und
   die Büro-Benutzer, Kategorie Meeting, Lead als Teilnehmer über ``event_participants`` -
   damit erscheint der Termin in den Aktivitäten und im Verlauf des Leads), weist es dem
   Meister zu (ToDo mit der internen Notiz) und setzt ``Lead.cv_termin``. Das Event trägt
   nur den Verweis auf den Lead und PLZ/Ort - Telefon, Adresse und Notizen bleiben am Lead.
   Ein vorheriger offener Termin *dieses* Leads wird abgesagt (Status Cancelled) - „Termin
   anlegen“ ist damit auch „Termin verschieben“. ``cv_termin`` lässt sich nur hierüber
   setzen (``lead_validate``).
2. Ist ein aktiver Lead-Workflow (Aufgabe 1.4) vorhanden und erlaubt er dem Benutzer
   einen Übergang in einen Zustand „Termin…“, wird dieser ausgeführt.
3. Die Kundenbestätigung entsteht aus dem Email Template
   „Terminbestätigung Besichtigung“ (``crm.email_vorlagen``, Desk-Änderungen bleiben
   erhalten) und wird mit ``frappe.sendmail`` in die Email Queue gelegt. Im MVP gibt es kein SMTP: ohne ausgehendes E-Mail-Konto bleibt
   der Eintrag „Not Sent“; der gerenderte Text steht zusätzlich zum Kopieren bereit
   (WhatsApp/Telefon).

Rechte: Rollen Büro oder Meister (System Manager/Administrator ebenfalls) und
Schreibrecht auf den Lead.

Doppelbuchungsschutz (Durchstich D14, ``planung.terminplanung``): Vor dem Anlegen wird geprüft, ob
der Termin samt Fahrzeit und Puffer mit Terminen, Arbeitspaketen oder Feiertagen des Meisters
zusammenstößt. Bei Konflikt ``TerminKonflikt`` (417) mit dem Grund in Klartext; das Büro kann mit
``trotzdem=1`` übersteuern - der Termin bekommt dann einen Vermerk.

Google Calendar: Der Termin wird nur mit dem Google-Kalender des Meisters synchronisiert,
wenn die Site das ausdrücklich erlaubt (site_config ``cv_termin_google_sync: 1``, Standard
aus) - Betreff (Kundenname) und PLZ/Ort gingen sonst ohne Entscheidung der Site an Google
(Datenschutz, Review Epic 1 E-4).
"""

import html
import re
from datetime import timedelta
from urllib.parse import quote

import frappe
import frappe.share
from frappe import _
from frappe.utils import cint, escape_html, get_datetime, get_fullname

ROLLEN_TERMIN = ("Büro", "Meister", "System Manager")
ROLLE_MEISTER = "Meister"
ROLLE_BUERO = "Büro"
EMAIL_TEMPLATE = "Terminbestätigung Besichtigung"
FELD_TERMIN = "cv_termin"
FELD_OBJEKT_ADRESSE = "cv_objekt_adresse"  # Aufgabe 1.1
DAUER_MIN, DAUER_MAX = 15, 12 * 60
CONF_GOOGLE_SYNC = "cv_termin_google_sync"

WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


class TerminKonflikt(frappe.ValidationError):
	"""Der Termin überschneidet sich mit der Planung des Meisters (HTTP 417, ``exc_type``; D14).

	``grund`` ist der Klartext (eine Zeile je Konflikt), ``konflikte`` die maschinenlesbare Liste -
	je Eintrag ``meister``, ``meister_name``, ``von``, ``bis``, ``ort``, ``fahrzeit_min``, ``art``,
	``doctype``, ``name``, ``text`` -, ``meister`` der Benutzer. Sync-Handler (App) können eine Ablehnung
	damit mit Grund weitergeben; im Desk steht der Grund zusätzlich als Meldung (``frappe.msgprint``).
	"""

	def __init__(self, grund: str = "", konflikte: list[dict] | None = None, meister: str | None = None):
		super().__init__(grund)
		self.grund = grund
		self.konflikte = konflikte or []
		self.meister = meister

	def as_dict(self) -> dict:
		return {"grund": self.grund, "meister": self.meister, "konflikte": self.konflikte}


# --- Rechte ---------------------------------------------------------------------------------


def darf_termine(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(set(ROLLEN_TERMIN) & set(frappe.get_roles(user)))


def _pruefe_rolle() -> None:
	# frappe.only_for wird bewusst nicht benutzt: Die Prüfung soll auch in Tests greifen.
	if not darf_termine():
		frappe.throw(
			_("Termine und Terminbestätigungen sind den Rollen Büro und Meister vorbehalten."),
			frappe.PermissionError,
		)


# --- Whitelist ------------------------------------------------------------------------------


@frappe.whitelist()
def meister_liste() -> list[dict]:
	"""Aktive Systembenutzer mit der Rolle Meister (Auswahl im Termin-Dialog)."""
	_pruefe_rolle()
	return _meister()


@frappe.whitelist(methods=["POST"])
def termin_anlegen(
	lead: str,
	beginn: str,
	meister: str,
	dauer: int | str | None = 60,
	notiz: str | None = None,
	bestaetigung: int | str | bool | None = 1,
	trotzdem: int | str | bool | None = 0,
) -> dict:
	"""Legt den Besichtigungstermin an und gibt Event, Status und Bestätigung zurück.

	:param lead: Name des Leads
	:param beginn: Beginn (``YYYY-MM-DD HH:MM[:SS]``, Systemzeitzone)
	:param meister: Benutzer (E-Mail) mit Rolle Meister
	:param dauer: Dauer in Minuten (15-720; leer = „Dauer Besichtigung“ aus den Planungseinstellungen)
	:param notiz: interne Notiz für den Meister (nicht in der Kundenbestätigung)
	:param bestaetigung: 1 = Kundenbestätigung in die Email Queue legen (falls E-Mail vorhanden)
	:param trotzdem: 1 = Überschneidung mit der Planung des Meisters bewusst übersteuern (Vermerk)
	"""
	_pruefe_rolle()
	doc = frappe.get_doc("Lead", lead)
	doc.check_permission("write")

	start = _beginn(beginn)
	minuten = _dauer(dauer)
	if not DAUER_MIN <= minuten <= DAUER_MAX:
		frappe.throw(_("Die Dauer muss zwischen {0} und {1} Minuten liegen.").format(DAUER_MIN, DAUER_MAX))
	if meister not in {m["name"] for m in _meister()}:
		frappe.throw(_("{0} ist kein aktiver Benutzer mit der Rolle Meister.").format(meister))

	# Doppelbuchungsschutz (D14): Termine, Arbeitspakete, Feiertage des Meisters plus Fahrzeit und Puffer
	from corevatis_erpnext.planung import terminplanung

	ende = start + timedelta(minutes=minuten)
	konflikte = terminplanung.konflikte(doc, meister, start, ende)
	if konflikte and not cint(trotzdem):
		terminplanung.konflikt_werfen(konflikte, meister)

	kunde = kunden_name(doc)
	adresse = objekt_adresse(doc)
	event = frappe.get_doc(
		{
			"doctype": "Event",
			"subject": _("Besichtigung {0}: {1}").format(doc.name, kunde),
			"event_category": "Meeting",
			# Private + Freigabe an Meister und Büro (_freigeben): Kundendaten bleiben aus dem
			# allgemeinen Kalender anderer Desk-Benutzer heraus.
			"event_type": "Private",
			"status": "Open",
			"starts_on": start,
			"ends_on": ende,
			"send_reminder": 1,
			# nur PLZ/Ort - vollständige Adresse, Telefon und Notizen stehen am Lead
			"location": _ort(adresse) or None,
			"description": _beschreibung(doc),
			"event_participants": [{"reference_doctype": "Lead", "reference_docname": doc.name}],
		}
	)
	_google_calendar(event, meister)
	event.insert()
	_freigeben(event, meister)
	if konflikte:
		# Übersteuerung durch das Büro: am Termin vermerkt (Doppelbuchungsschutz D14)
		event.add_comment(
			"Info",
			_("Trotz Überschneidung angelegt von {0}: {1}").format(
				escape_html(get_fullname(frappe.session.user) or frappe.session.user),
				" ".join(escape_html(k["text"]) for k in konflikte),
			),
		)

	from frappe.desk.form.assign_to import add as assign

	assign(
		{
			"assign_to": [meister],
			"doctype": "Event",
			"name": event.name,
			"description": _todo_text(event.subject, notiz),
			"date": start.date(),
		}
	)

	abgesagt = _alten_termin_absagen(doc, event.name)
	if doc.meta.has_field(FELD_TERMIN):
		doc.flags.cv_termin_setzen = True
		doc.db_set(FELD_TERMIN, event.name)

	status, warnung = status_nach_termin(doc.name)

	result = {
		"event": event.name,
		"abgesagt": abgesagt,
		"status": status,
		"warnungen": [warnung] if warnung else [],
		"email_queue": None,
		"empfaenger": doc.get("email_id") or None,
		"konflikte": konflikte,
	}
	inhalt = bestaetigung_rendern(doc, event)
	result["bestaetigung"] = inhalt
	if cint(bestaetigung) and doc.get("email_id"):
		result["email_queue"] = in_email_queue(doc, inhalt)
	elif cint(bestaetigung):
		result["warnungen"].append(
			_("Der Lead hat keine E-Mail-Adresse - bitte die Bestätigung manuell übermitteln.")
		)
	return result


@frappe.whitelist()
def bestaetigung_anzeigen(lead: str) -> dict:
	"""Gerenderte Terminbestätigung zum aktuellen Termin (Betreff, HTML, Text)."""
	_pruefe_rolle()
	doc = frappe.get_doc("Lead", lead)
	doc.check_permission("read")
	event = aktueller_termin(doc)
	if not event:
		frappe.throw(_("Für diesen Lead ist noch kein Termin angelegt."))
	inhalt = bestaetigung_rendern(doc, event)
	inhalt["event"] = event.name
	inhalt["empfaenger"] = doc.get("email_id") or None
	return inhalt


@frappe.whitelist(methods=["POST"])
def bestaetigung_senden(lead: str) -> str:
	"""Legt die Bestätigung zum aktuellen Termin (erneut) in die Email Queue."""
	_pruefe_rolle()
	doc = frappe.get_doc("Lead", lead)
	doc.check_permission("write")
	if not doc.get("email_id"):
		frappe.throw(_("Der Lead hat keine E-Mail-Adresse."))
	event = aktueller_termin(doc, "read")
	if not event:
		frappe.throw(_("Für diesen Lead ist noch kein Termin angelegt."))
	return in_email_queue(doc, bestaetigung_rendern(doc, event))


# --- Bausteine (auch für Tests) -------------------------------------------------------------


def kunden_name(doc) -> str:
	name = (doc.get("lead_name") or "").strip()
	if not name:
		name = " ".join(filter(None, [doc.get("first_name"), doc.get("last_name")])).strip()
	return name or (doc.get("company_name") or "").strip() or doc.name


def objekt_adresse(doc) -> str:
	"""Objektadresse mehrzeilig: Feld aus 1.1, sonst verknüpfte Adresse, sonst Ort.

	Ein Address-Name im Feld wird nur aufgelöst, wenn die Adresse mit *diesem* Lead
	verknüpft ist (Dynamic Link) - sonst bleibt der Wert reiner Text. So lässt sich über
	einen Lead keine fremde Adresse der Site auslesen.
	"""
	adressen = frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": doc.doctype, "link_name": doc.name, "parenttype": "Address"},
		pluck="parent",
		order_by="creation asc",
	)
	wert = doc.get(FELD_OBJEKT_ADRESSE) if doc.meta.has_field(FELD_OBJEKT_ADRESSE) else None
	if wert:
		wert = str(wert)
		if wert in adressen:
			return _adresse_text(wert)
		return _html_zu_text(wert).strip()

	if adressen:
		return _adresse_text(adressen[0])
	return (doc.get("city") or "").strip()


def firma(doc) -> str:
	return (
		doc.get("company")
		or frappe.defaults.get_user_default("Company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
		or ""
	)


def gehoert_zum_lead(event: str | None, doc) -> bool:
	"""Ist ``doc`` Teilnehmer des Events? Schutz gegen fremde Event-Namen in ``cv_termin``."""
	return bool(
		event
		and frappe.db.exists(
			"Event Participants",
			{
				"parenttype": "Event",
				"parent": event,
				"reference_doctype": doc.doctype,
				"reference_docname": doc.name,
			},
		)
	)


def lead_validate(doc, method=None) -> None:
	"""``doc_events`` Lead ``validate``: ``cv_termin`` nur über ``termin_anlegen`` ändern."""
	if doc.flags.cv_termin_setzen or not doc.meta.has_field(FELD_TERMIN):
		return
	vorher = doc.get_doc_before_save()
	alt = (vorher.get(FELD_TERMIN) if vorher else None) or None
	if (doc.get(FELD_TERMIN) or None) != alt:
		frappe.throw(
			_("Der Besichtigungstermin wird nur über „Termin anlegen“ gesetzt."), frappe.PermissionError
		)


def aktueller_termin(doc, ptype: str = "read"):
	"""Aktueller Termin des Leads (``cv_termin``, sonst jüngster offener mit Lead als
	Teilnehmer); Event-Rechte des Benutzers werden geprüft."""
	name = doc.get(FELD_TERMIN) if doc.meta.has_field(FELD_TERMIN) else None
	if not gehoert_zum_lead(name, doc):
		events = frappe.get_all(
			"Event Participants",
			filters={"reference_doctype": doc.doctype, "reference_docname": doc.name, "parenttype": "Event"},
			pluck="parent",
		)
		if not events:
			return None
		name = frappe.get_all(
			"Event",
			filters={"name": ["in", events], "status": "Open"},
			order_by="starts_on desc",
			pluck="name",
			limit=1,
		)
		name = name[0] if name else None
	if not name:
		return None
	frappe.has_permission("Event", ptype, doc=name, throw=True)
	return frappe.get_doc("Event", name)


def meister_des_termins(event) -> str | None:
	users = frappe.get_all(
		"ToDo",
		filters={"reference_type": "Event", "reference_name": event.name, "status": ["!=", "Cancelled"]},
		pluck="allocated_to",
		order_by="creation asc",
		limit=1,
	)
	return users[0] if users else None


def kontext(doc, event) -> dict:
	start = get_datetime(event.starts_on)
	ende = get_datetime(event.ends_on) if event.ends_on else None
	minuten = int((ende - start).total_seconds() // 60) if ende else 0
	meister = meister_des_termins(event)
	return {
		"kunde": kunden_name(doc),
		"datum": f"{WOCHENTAGE[start.weekday()]}, {start:%d.%m.%Y}",
		"uhrzeit": f"{start:%H:%M}",
		"uhrzeit_bis": f"{ende:%H:%M}" if ende else "",
		"dauer": _dauer_text(minuten),
		"meister": get_fullname(meister) if meister else "",
		"objekt_adresse": objekt_adresse(doc),
		"firma": firma(doc),
		"termin": event.name,
		"lead": doc.name,
	}


def bestaetigung_rendern(doc, event) -> dict:
	"""Rendert das Email Template; Werte für den HTML-Teil HTML-escaped."""
	template = frappe.get_doc("Email Template", EMAIL_TEMPLATE)
	werte = kontext(doc, event)
	werte_html = {key: escape_html(value).replace("\n", "<br>") for key, value in werte.items()}
	nachricht = frappe.render_template(
		template.response_html if template.use_html else template.response, werte_html
	)
	return {
		"betreff": frappe.render_template(template.subject, werte).strip(),
		"html": nachricht,
		"text": _html_zu_text(nachricht).strip(),
		"werte": werte,
	}


def in_email_queue(doc, inhalt: dict) -> str | None:
	"""``frappe.sendmail`` in die Email Queue (kein sofortiger Versand).

	Ohne ausgehendes E-Mail-Konto (MVP ohne SMTP) bricht ``frappe.sendmail`` sonst mit
	„Please setup default outgoing Email Account“ ab. Für genau diesen Aufruf wird
	deshalb ``frappe.flags.mute_emails`` gesetzt: Frappe nimmt dann sein Platzhalter-Konto,
	der Eintrag entsteht mit Status „Not Sent“, versendet wird nichts.
	"""
	ohne_konto = not frappe.db.exists("Email Account", {"enable_outgoing": 1}) and not frappe.conf.get(
		"mail_server"
	)
	stumm = frappe.flags.mute_emails
	if ohne_konto:
		frappe.flags.mute_emails = True
	try:
		queue = frappe.sendmail(
			recipients=[doc.email_id],
			subject=inhalt["betreff"],
			message=inhalt["html"],
			reference_doctype=doc.doctype,
			reference_name=doc.name,
			now=False,
			add_unsubscribe_link=0,
		)
	finally:
		frappe.flags.mute_emails = stumm
		if ohne_konto and hasattr(frappe.local, "outgoing_email_account"):
			# Platzhalter-Konto nicht für spätere Aufrufe im selben Request zwischenspeichern
			frappe.local.outgoing_email_account = {}

	name = getattr(queue, "name", None)
	hinweis = (
		_("Terminbestätigung an {0} in die E-Mail-Warteschlange gelegt ({1}).")
		if not ohne_konto
		else _(
			"Terminbestätigung an {0} in der E-Mail-Warteschlange abgelegt ({1}); kein E-Mail-Konto eingerichtet - bitte manuell übermitteln."
		)
	)
	doc.add_comment("Info", hinweis.format(doc.email_id, name or "-"))
	return name


def status_nach_termin(lead: str) -> tuple[str | None, str | None]:
	"""Workflow-Übergang nach „Termin…“, falls ein aktiver Lead-Workflow (1.4) ihn erlaubt.

	Rückgabe ``(neuer_zustand, warnung)``; ohne Workflow ``(None, None)``. Ein
	fehlgeschlagener Übergang macht den Termin nicht rückgängig, sondern wird gemeldet.
	"""
	from frappe.model.workflow import apply_workflow, get_transitions, get_workflow_name

	if not get_workflow_name("Lead"):
		return None, None
	doc = frappe.get_doc("Lead", lead)
	meldungen = len(frappe.local.message_log)
	try:
		uebergaenge = get_transitions(doc, raise_exception=True)
	except frappe.ValidationError:
		del frappe.local.message_log[meldungen:]
		return None, _("Lead-Workflow: kein Status gesetzt, Übergang nach „Termin“ übersprungen.")

	from corevatis_erpnext.anfrage.workflow import ACTION_TERMIN

	# Aktion aus der Workflow-Definition dieser App; Namenspräfix nur als Rückfall (E-8)
	ziel = next((t for t in uebergaenge if t.get("action") == ACTION_TERMIN), None) or next(
		(t for t in uebergaenge if (t.get("next_state") or "").strip().lower().startswith("termin")), None
	)
	if not ziel:
		return None, None

	frappe.db.savepoint("cv_termin_workflow")
	try:
		apply_workflow(doc, ziel["action"])
	except frappe.ValidationError as e:
		frappe.db.rollback(save_point="cv_termin_workflow")
		del frappe.local.message_log[meldungen:]
		return None, _("Status-Übergang nach „{0}“ nicht möglich: {1}").format(
			ziel["next_state"], _html_zu_text(str(e)).strip()
		)
	return ziel["next_state"], None


# --- intern ---------------------------------------------------------------------------------


def _beginn(beginn):
	"""Beginn als datetime; nicht lesbarer Text → verständliche Meldung (417) statt 500."""
	if not beginn or not str(beginn).strip():
		frappe.throw(_("Bitte Datum und Uhrzeit des Termins angeben."))
	try:
		start = get_datetime(beginn)
	except (ValueError, TypeError, OverflowError):
		start = None
	if not start:
		frappe.throw(
			_("„{0}“ ist kein gültiges Datum mit Uhrzeit (erwartet z. B. 2030-03-05 09:30).").format(
				escape_html(str(beginn)[:40])
			)
		)
	return start


def standard_dauer() -> int:
	"""„Dauer Besichtigung“ aus den Planungseinstellungen (Standard 60 Minuten, D14)."""
	from corevatis_erpnext.planung.einstellungen import einstellungen

	return cint(einstellungen().besichtigung_dauer) or 60


def _dauer(dauer) -> int:
	"""Dauer in Minuten; leer → Standard aus den Einstellungen, kein ganzzahliger Wert → Meldung."""
	if dauer is None or (isinstance(dauer, str) and not dauer.strip()):
		return standard_dauer()
	text = str(dauer).strip()
	if isinstance(dauer, bool) or not text.lstrip("-").isdigit():
		frappe.throw(_("Die Dauer muss in ganzen Minuten angegeben werden."))
	return int(text)


def _meister() -> list[dict]:
	users = frappe.get_all(
		"Has Role", filters={"role": ROLLE_MEISTER, "parenttype": "User"}, pluck="parent", distinct=True
	)
	if not users:
		return []
	return frappe.get_all(
		"User",
		filters={"name": ["in", users], "enabled": 1, "user_type": "System User"},
		fields=["name", "full_name"],
		order_by="full_name asc",
	)


def google_sync_erlaubt() -> bool:
	"""Opt-in der Site (site_config ``cv_termin_google_sync``), Standard aus."""
	return bool(cint(frappe.conf.get(CONF_GOOGLE_SYNC)))


def google_kalender(meister: str) -> str | None:
	"""Aktiver Google-Kalender des Meisters mit „Push“, sonst ``None``."""
	if not frappe.db.table_exists("Google Calendar"):
		return None
	return frappe.db.get_value(
		"Google Calendar", {"user": meister, "enable": 1, "push_to_google_calendar": 1}, "name"
	)


def _google_calendar(event, meister: str) -> None:
	"""Mit dem Google-Kalender des Meisters synchronisieren - nur mit Opt-in der Site und wenn
	der Meister einen Kalender mit „Push“ verbunden hat."""
	if not google_sync_erlaubt():
		return
	kalender = google_kalender(meister)
	if kalender:
		event.sync_with_google_calendar = 1
		event.google_calendar = kalender


def _alten_termin_absagen(doc, neu: str) -> str | None:
	alt = doc.get(FELD_TERMIN) if doc.meta.has_field(FELD_TERMIN) else None
	if not alt or alt == neu or not gehoert_zum_lead(alt, doc):
		# fremdes oder gelöschtes Event in cv_termin: nicht anfassen
		return None
	if frappe.db.get_value("Event", alt, "status") != "Open":
		return None
	# Über save(): Rechteprüfung, Version, modified und die Event-Hooks (u. a. Google-Sync
	# ``update_event_in_google_calendar``) laufen - der alte Termin verschwindet auch dort (E-3)
	event = frappe.get_doc("Event", alt)
	event.check_permission("write")
	event.status = "Cancelled"
	event.save()
	from frappe.desk.form.assign_to import clear

	clear("Event", alt, ignore_permissions=True)
	event.add_comment("Info", _("Abgesagt: neuer Besichtigungstermin {0} für {1}.").format(neu, doc.name))
	return alt


def _beschreibung(doc) -> str:
	# Nur der Verweis: Telefon, Adresse und Notizen bleiben am Lead (Zugriff über Lead-Rechte)
	link = f"/app/lead/{quote(str(doc.name))}"
	return f'<p>{escape_html(_("Anfrage"))}: <a href="{escape_html(link)}">{escape_html(doc.name)}</a></p>'


def _todo_text(subject: str, notiz: str | None) -> str:
	text = escape_html(subject)
	if notiz:
		text += "<br>" + escape_html(notiz).replace("\n", "<br>")
	return text


_PLZ_ORT = re.compile(r"\b(\d{5})\s+([^,;\n]+)")


def _ort(adresse: str) -> str:
	"""Nur PLZ/Ort für das Event (keine Straße im Kalender).

	Mehrzeilig: letzte Zeile. Einzeilig wie aus dem Web Form („Testweg 1, 48143 Münster“):
	PLZ + Ort aus dem Text, ohne PLZ der Teil nach dem letzten Komma.
	"""
	zeilen = [z.strip() for z in (adresse or "").splitlines() if z.strip()]
	if not zeilen:
		return ""
	treffer = list(_PLZ_ORT.finditer(adresse))
	if treffer:
		plz, ort = treffer[-1].groups()
		return f"{plz} {ort.strip()}"[:140]
	return zeilen[-1].rsplit(",", 1)[-1].strip()[:140]


def _freigeben(event, meister: str) -> None:
	"""Privates Event für den Meister und alle aktiven Büro-Benutzer freigeben (lesen/schreiben)."""
	users = set(
		frappe.get_all("Has Role", filters={"role": ROLLE_BUERO, "parenttype": "User"}, pluck="parent")
	)
	users = set(
		frappe.get_all(
			"User",
			filters={"name": ["in", list(users) or [""]], "enabled": 1, "user_type": "System User"},
			pluck="name",
		)
	)
	users.add(meister)
	users.discard(event.owner)
	for user in sorted(users):
		frappe.share.add_docshare(
			"Event", event.name, user, read=1, write=1, flags={"ignore_share_permission": True}, notify=0
		)


def _adresse_text(name: str) -> str:
	a = frappe.db.get_value(
		"Address", name, ["address_line1", "address_line2", "pincode", "city"], as_dict=True
	)
	if not a:
		return ""
	ort = " ".join(filter(None, [a.pincode, a.city]))
	return "\n".join(filter(None, [a.address_line1, a.address_line2, ort]))


def _dauer_text(minuten: int) -> str:
	if not minuten:
		return ""
	stunden, rest = divmod(minuten, 60)
	if not stunden:
		return f"{rest} Minuten"
	text = "1 Stunde" if stunden == 1 else f"{stunden} Stunden"
	return f"{text} {rest} Minuten" if rest else text


def _html_zu_text(value: str) -> str:
	"""Einfache HTML→Text-Wandlung für Bestätigung und Meldungen (Absätze, Zeilenumbrüche)."""
	# Zeilenumbrüche im Quelltext sind in HTML bedeutungslos, nur Tags zählen
	text = re.sub(r"\s*\r?\n\s*", " ", value or "") if "<" in (value or "") else (value or "")
	text = re.sub(r"(?i)<br\s*/?>", "\n", text)
	text = re.sub(r"(?i)</p\s*>|</div\s*>|</h\d\s*>", "\n\n", text)
	text = re.sub(r"(?i)<li[^>]*>", "- ", text)
	text = re.sub(r"<[^>]+>", "", text)
	text = html.unescape(text).replace("\xa0", " ")
	text = re.sub(r"[ \t]+\n", "\n", text)
	text = re.sub(r"\n[ \t]+", "\n", text)
	return re.sub(r"\n{3,}", "\n\n", text)
