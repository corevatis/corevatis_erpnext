"""Aufgabe 4.5: Nachfass-Automatik für Angebote - ToDo nach X Tagen, optional Follow-up-Mail.

Täglicher Scheduler-Job (``hooks.scheduler_events["daily"]`` → ``taeglich``):

- Kandidaten: eingereichte Angebote mit Status „Open“ oder „Replied“ (mit Einstellung
  „Auch abgelaufene“ zusätzlich „Expired“ und ``valid_till`` in der Vergangenheit), ohne
  Auftrag (auch kein Entwurf - angenommen ist angenommen), nicht „Lost“/„Ordered“.
- Fällig: Angebotsdatum (``transaction_date`` = Versanddatum) mindestens ``nachfass_tage``
  zurück; nach einem erledigten Nachfassen zählen die Tage ab dem Schließen der Aufgabe.
- Ergebnis: ToDo „Angebot <Nr> nachfassen“ mit Referenz auf das Angebot, Fälligkeit heute,
  ``cv_nachfass_zyklus`` = 1, 2, ... Höchstens ``nachfass_zyklen`` Nachfass-Aufgaben je Angebot.
- Idempotent: Solange eine Nachfass-Aufgabe offen ist, entsteht keine weitere; ein zweiter Lauf
  am selben Tag legt nichts an.
- Zuweisung: „Rolle Büro“ = Ersteller des Angebots, wenn er die Rolle Büro hat, sonst alle
  aktiven Büro-Benutzer; „Benutzer“ = fester Benutzer aus den Einstellungen.
- Follow-up-Mail (Standard aus): Email Template „Angebot Nachfassen“ an ``contact_email`` des
  Angebots in die Email Queue. Im MVP gibt es kein SMTP - ohne ausgehendes E-Mail-Konto bleibt
  der Eintrag „Not Sent“ (wie die Terminbestätigung 1.3), die Aufgabe ist das ToDo.

Manuell: ``nachfassen`` (Button „Nachfassen“ am Angebot) legt sofort eine Nachfass-Aufgabe an
(ohne Wartezeit und ohne Zyklus-Grenze, aber nie neben einer offenen) und liefert den Text der
Follow-up-Mail zum Kopieren.

Einstellungen: Single „Corevatis Vertrieb Einstellungen“ (``einstellungen()`` mit Standard-
werten, auch wenn das Formular nie gespeichert wurde).
"""

import frappe
import frappe.share
from frappe import _
from frappe.utils import add_days, cint, date_diff, escape_html, fmt_money, get_fullname, getdate, today

from corevatis_erpnext.angebot.annahme import bestehender_auftrag, pruefe_rolle

EINSTELLUNGEN = "Corevatis Vertrieb Einstellungen"
EMAIL_TEMPLATE = "Angebot Nachfassen"
FELD_ZYKLUS = "cv_nachfass_zyklus"
ROLLE_BUERO = "Büro"
STANDARD = {
	"nachfass_aktiv": 1,
	"nachfass_tage": 7,
	"nachfass_zyklen": 2,
	"nachfass_abgelaufene": 0,
	"nachfass_zuweisung": "Rolle Büro",
	"nachfass_benutzer": None,
	"nachfass_mail_aktiv": 0,
	"nachfass_email_template": None,
}
GANZZAHLEN = (
	"nachfass_aktiv",
	"nachfass_tage",
	"nachfass_zyklen",
	"nachfass_abgelaufene",
	"nachfass_mail_aktiv",
)
STATUS_NACHFASSEN = ("Open", "Replied")


def einstellungen() -> frappe._dict:
	werte = frappe._dict(STANDARD)
	if frappe.db.exists("DocType", EINSTELLUNGEN):
		gespeichert = frappe.db.get_singles_dict(EINSTELLUNGEN)
		for key in STANDARD:
			if gespeichert.get(key) not in (None, ""):
				werte[key] = gespeichert[key]
	for key in GANZZAHLEN:
		werte[key] = cint(werte[key])
	werte.nachfass_tage = max(werte.nachfass_tage, 1)
	return werte


# --- Scheduler ------------------------------------------------------------------------------


def taeglich() -> None:
	"""``scheduler_events["daily"]``."""
	nachfassen_pruefen()


def nachfassen_pruefen(stichtag=None) -> dict:
	"""Fällige Nachfass-Aufgaben anlegen. ``stichtag`` (Standard heute) erlaubt Tests mit Zeitreise.

	Rückgabe: ``{"angelegt": [(Angebot, Zyklus, [ToDo])], "mails": [...], "fehler": [...]}``.
	"""
	e = einstellungen()
	ergebnis = {"aktiv": bool(e.nachfass_aktiv), "angelegt": [], "mails": [], "fehler": []}
	if not e.nachfass_aktiv:
		return ergebnis
	stichtag = getdate(stichtag or today())
	for name in kandidaten(stichtag, e):
		zyklus = faelliger_zyklus(name, stichtag, e)
		if not zyklus:
			continue
		frappe.db.savepoint("cv_nachfassen")
		try:
			angebot = frappe.get_doc("Quotation", name)
			todos = aufgabe_anlegen(angebot, zyklus, stichtag, e)
			ergebnis["angelegt"].append((name, zyklus, todos))
			if e.nachfass_mail_aktiv and angebot.get("contact_email"):
				queue = in_email_queue(angebot, mail_rendern(angebot, e))
				ergebnis["mails"].append((name, queue))
		except Exception:
			frappe.db.rollback(save_point="cv_nachfassen")
			frappe.log_error(
				title=f"Nachfassen Angebot {name}", reference_doctype="Quotation", reference_name=name
			)
			ergebnis["fehler"].append(name)
	return ergebnis


def kandidaten(stichtag, e) -> list[str]:
	status = list(STATUS_NACHFASSEN) + (["Expired"] if e.nachfass_abgelaufene else [])
	zeilen = frappe.get_all(
		"Quotation",
		filters={
			"docstatus": 1,
			"status": ["in", status],
			"transaction_date": ["<=", add_days(stichtag, -e.nachfass_tage)],
		},
		fields=["name", "valid_till"],
		order_by="transaction_date asc, name asc",
	)
	namen = []
	for z in zeilen:
		if not e.nachfass_abgelaufene and z.valid_till and getdate(z.valid_till) < stichtag:
			continue
		if bestehender_auftrag(z.name):
			continue
		namen.append(z.name)
	return namen


def nachfass_todos(quotation: str) -> list:
	return frappe.get_all(
		"ToDo",
		filters={"reference_type": "Quotation", "reference_name": quotation, FELD_ZYKLUS: [">", 0]},
		fields=["name", "status", FELD_ZYKLUS, "modified", "allocated_to"],
		order_by=f"{FELD_ZYKLUS} desc, modified desc",
	)


def faelliger_zyklus(quotation: str, stichtag, e) -> int | None:
	"""Nächster Zyklus, wenn fällig - sonst ``None`` (offene Aufgabe, Grenze erreicht, zu früh)."""
	todos = nachfass_todos(quotation)
	if any(t.status == "Open" for t in todos):
		return None
	zyklus = max((cint(t.get(FELD_ZYKLUS)) for t in todos), default=0)
	if zyklus >= e.nachfass_zyklen:
		return None
	if zyklus:
		bezug = max(getdate(t.modified) for t in todos if cint(t.get(FELD_ZYKLUS)) == zyklus)
	else:
		bezug = getdate(frappe.db.get_value("Quotation", quotation, "transaction_date"))
	if date_diff(stichtag, bezug) < e.nachfass_tage:
		return None
	return zyklus + 1


# --- Aufgabe --------------------------------------------------------------------------------


def _aktive_systembenutzer(namen) -> list[str]:
	namen = [n for n in namen if n and n != "Guest"]
	if not namen:
		return []
	return frappe.get_all(
		"User",
		filters={"name": ["in", namen], "enabled": 1, "user_type": "System User"},
		pluck="name",
		order_by="name asc",
	)


def empfaenger(angebot, e) -> list[str]:
	if e.nachfass_zuweisung == "Benutzer" and e.nachfass_benutzer:
		benutzer = _aktive_systembenutzer([e.nachfass_benutzer])
		if benutzer:
			return benutzer
	buero = _aktive_systembenutzer(
		frappe.get_all("Has Role", filters={"role": ROLLE_BUERO, "parenttype": "User"}, pluck="parent")
	)
	if angebot.owner in buero:
		return [angebot.owner]
	if buero:
		return buero
	return _aktive_systembenutzer([angebot.owner]) or ["Administrator"]


def aufgabe_text(angebot, zyklus: int) -> str:
	text = _("Angebot {0} nachfassen").format(angebot.name)
	if angebot.get("customer_name"):
		text += f" - {angebot.customer_name}"
	if zyklus > 1:
		text += " " + _("({0}. Nachfassen)").format(zyklus)
	return text


def aufgabe_anlegen(angebot, zyklus: int, stichtag, e) -> list[str]:
	"""ToDo je Empfänger (wie ``frappe.desk.form.assign_to.add``, zusätzlich ``cv_nachfass_zyklus``)."""
	from frappe.desk.form.assign_to import notify_assignment

	beschreibung = escape_html(aufgabe_text(angebot, zyklus))
	namen = []
	benutzer = empfaenger(angebot, e)
	for user in benutzer:
		todo = frappe.get_doc(
			{
				"doctype": "ToDo",
				"allocated_to": user,
				"reference_type": "Quotation",
				"reference_name": angebot.name,
				"description": beschreibung,
				"priority": "Medium",
				"status": "Open",
				"date": stichtag,
				"assigned_by": frappe.session.user,
				FELD_ZYKLUS: zyklus,
			}
		).insert(ignore_permissions=True)
		namen.append(todo.name)
		if not frappe.has_permission("Quotation", "read", doc=angebot, user=user):
			frappe.share.add_docshare(
				"Quotation", angebot.name, user, read=1, flags={"ignore_share_permission": True}, notify=0
			)
		notify_assignment(
			todo.assigned_by, user, "Quotation", angebot.name, action="ASSIGN", description=beschreibung
		)
	angebot.add_comment(
		"Info", _("Nachfass-Aufgabe ({0}. Nachfassen) angelegt für {1}.").format(zyklus, ", ".join(benutzer))
	)
	return namen


# --- Follow-up-Mail -------------------------------------------------------------------------


def _datum(wert) -> str:
	return getdate(wert).strftime("%d.%m.%Y") if wert else ""


def mail_kontext(angebot) -> dict:
	return {
		"kunde": angebot.get("contact_display")
		or angebot.get("customer_name")
		or angebot.get("party_name")
		or "",
		"angebot": angebot.name,
		"datum": _datum(angebot.transaction_date),
		"gueltig_bis": _datum(angebot.get("valid_till")),
		"betrag": fmt_money(angebot.grand_total, currency=angebot.currency)
		if angebot.get("grand_total")
		else "",
		"objekt": angebot.get("cv_titel") or "",
		"firma": angebot.company or "",
		"ansprechpartner": get_fullname(angebot.owner)
		if angebot.owner not in ("Administrator", "Guest")
		else "",
	}


def mail_rendern(angebot, e=None) -> dict:
	"""Email Template rendern; Werte für den HTML-Teil escaped (wie die Terminbestätigung 1.3)."""
	from corevatis_erpnext.crm.termin import _html_zu_text

	e = e or einstellungen()
	name = e.nachfass_email_template or EMAIL_TEMPLATE
	if not frappe.db.exists("Email Template", name):
		name = EMAIL_TEMPLATE
	template = frappe.get_doc("Email Template", name)
	werte = mail_kontext(angebot)
	werte_html = {key: escape_html(str(value)).replace("\n", "<br>") for key, value in werte.items()}
	nachricht = frappe.render_template(
		template.response_html if template.use_html else template.response, werte_html
	)
	return {
		"betreff": frappe.render_template(template.subject, werte).strip(),
		"html": nachricht,
		"text": _html_zu_text(nachricht).strip(),
		"empfaenger": angebot.get("contact_email") or None,
	}


def in_email_queue(angebot, inhalt: dict) -> str | None:
	"""In die Email Queue (kein sofortiger Versand). Ohne ausgehendes Konto mit ``mute_emails``
	(wie ``crm.termin.in_email_queue``): Eintrag „Not Sent“, versendet wird nichts."""
	ohne_konto = not frappe.db.exists("Email Account", {"enable_outgoing": 1}) and not frappe.conf.get(
		"mail_server"
	)
	stumm = frappe.flags.mute_emails
	if ohne_konto:
		frappe.flags.mute_emails = True
	try:
		queue = frappe.sendmail(
			recipients=[angebot.contact_email],
			subject=inhalt["betreff"],
			message=inhalt["html"],
			reference_doctype="Quotation",
			reference_name=angebot.name,
			now=False,
			add_unsubscribe_link=0,
		)
	finally:
		frappe.flags.mute_emails = stumm
		if ohne_konto and hasattr(frappe.local, "outgoing_email_account"):
			frappe.local.outgoing_email_account = {}
	name = getattr(queue, "name", None)
	hinweis = (
		_("Nachfass-E-Mail an {0} in die E-Mail-Warteschlange gelegt ({1}).")
		if not ohne_konto
		else _(
			"Nachfass-E-Mail an {0} in der E-Mail-Warteschlange abgelegt ({1}); kein E-Mail-Konto eingerichtet - bitte manuell übermitteln."
		)
	)
	angebot.add_comment("Info", hinweis.format(angebot.contact_email, name or "-"))
	return name


# --- Whitelist ------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def nachfassen(quotation: str, mail: int | str | None = None) -> dict:
	"""Button „Nachfassen“: Nachfass-Aufgabe sofort anlegen (keine zweite neben einer offenen).

	:param mail: 1 = Follow-up-Mail in die Email Queue, 0 = nicht; leer = Einstellung
	"""
	pruefe_rolle()
	frappe.has_permission("Quotation", "read", quotation, throw=True)
	frappe.db.get_value("Quotation", quotation, "name", for_update=True)
	angebot = frappe.get_doc("Quotation", quotation)
	if angebot.docstatus != 1 or angebot.status not in (*STATUS_NACHFASSEN, "Expired"):
		frappe.throw(_("Nachfassen nur bei eingereichten, offenen Angeboten."))
	auftrag = bestehender_auftrag(angebot.name)
	if auftrag:
		frappe.throw(_("Zum Angebot {0} gibt es bereits den Auftrag {1}.").format(angebot.name, auftrag.name))

	e = einstellungen()
	todos = nachfass_todos(angebot.name)
	offen = [t.name for t in todos if t.status == "Open"]
	warnungen = []
	if offen:
		zyklus = max(cint(t.get(FELD_ZYKLUS)) for t in todos if t.status == "Open")
		warnungen.append(_("Es gibt bereits eine offene Nachfass-Aufgabe."))
	else:
		zyklus = max((cint(t.get(FELD_ZYKLUS)) for t in todos), default=0) + 1
		offen = aufgabe_anlegen(angebot, zyklus, getdate(today()), e)

	inhalt = mail_rendern(angebot, e)
	queue = None
	mail = e.nachfass_mail_aktiv if mail in (None, "") else cint(mail)
	if mail and angebot.get("contact_email"):
		queue = in_email_queue(angebot, inhalt)
	elif mail:
		warnungen.append(_("Das Angebot hat keine Kontakt-E-Mail - bitte den Text manuell übermitteln."))
	return {
		"angebot": angebot.name,
		"todos": offen,
		"zyklus": zyklus,
		"email_queue": queue,
		"warnungen": warnungen,
		**inhalt,
	}
