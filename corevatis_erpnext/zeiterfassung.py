"""Zeiterfassung der Monteure (Aufgabe 3.9): ERPNext-Timesheets für die Mobile-API.

Die Kern-App ``corevatis`` (Sync-Entitäten ``zeit_start``/``zeit_stopp``/``zeit_eintrag``,
Lese-Endpunkt ``meine_zeiten``) prüft Rechte, Payload und den Ablauf Start → Wechsel →
Stopp und ruft diese Funktionen per ``frappe.get_attr("corevatis_erpnext.zeiterfassung.…")``
auf (kein Import, Lizenzgrenze). Hier steckt nur das ERPNext-Wissen:

- **Activity Types** „Arbeitszeit“, „Fahrzeit“, „Pause“ (``activity_types_sicherstellen``,
  aus ``after_install``/``after_migrate``). ERPNext kennt am Activity Type kein
  „abrechenbar“; das steht an der Zeile (``Timesheet Detail.is_billable``): Pause nie,
  Arbeits- und Fahrzeit ja (``ABRECHENBAR``).
- **Buchung** (``zeit_buchen``): je Mitarbeiter und Tag ein Timesheet im Entwurf; jede
  Buchung wird eine Zeile ``Timesheet Detail`` (``from_time``/``to_time``/``hours``,
  ``activity_type``, ``project``, ``task``, ``description`` = Notiz). Über Mitternacht wird
  geteilt (je Tag eine Zeile im Timesheet des Tages). Ist das Timesheet des Tages schon
  eingereicht, entsteht ein neuer Entwurf. Überlappungen prüft ERPNext
  (``Timesheet.validate_overlap``, abschaltbar in den Projects Settings) - der Adapter
  meldet sie als ``ZeitKonflikt`` (``grund = "ueberlappung"``).
- **Idempotenz:** Der ``idempotency_key`` der Mobile-Mutation steht an der Zeile
  (Custom Field ``Timesheet Detail.cv_sync_key``). Ein zweiter Aufruf mit demselben Key
  liefert die vorhandenen Zeilen (``duplikat: True``) und schreibt nichts - zusätzlich zum
  ``Sync Journal`` des Kerns.
- **Laufende Buchung:** eigener kleiner Doctype ``Zeitbuchung Lauf`` (Name = Employee,
  also höchstens eine je Mitarbeiter). Entscheidung gegen eine Timesheet-Zeile ohne
  ``to_time``: ERPNext rechnet ``to_time`` leer als „jetzt“ (Überlappungsprüfung,
  ``set_dates``), Berichte und Abrechnung sähen eine halbe Zeile mit 0 Stunden. Der Lauf
  wird beim Stopp zur Zeile und gelöscht.
- **Sollstunden** (``sollstunden``): Holiday List des Mitarbeiters bzw. der Firma
  (Feiertag/Wochenende → 0 h, halber Tag → 4 h), sonst 8 h als Annahme (Mo-Fr; Sa/So 0 h).

Zeitpunkte sind naive ``datetime`` in der Zeitzone der Site (wie in Frappe gespeichert);
die Umrechnung aus der App (ISO 8601 mit Offset) macht der Kern.
"""

from __future__ import annotations

import datetime as _dt

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import flt, get_datetime, getdate, now_datetime

MODULE = "Corevatis ERPNext"

ARBEITSZEIT = "Arbeitszeit"
FAHRZEIT = "Fahrzeit"
PAUSE = "Pause"
# Vertrag mit corevatis.api.zeiten.AKTIVITAETEN (Vertragstest im Kern)
ACTIVITY_TYPES = (ARBEITSZEIT, FAHRZEIT, PAUSE)
ABRECHENBAR = {ARBEITSZEIT: 1, FAHRZEIT: 1, PAUSE: 0}

LAUF_DOCTYPE = "Zeitbuchung Lauf"
SYNC_KEY_FELD = "cv_sync_key"

STANDARD_SOLLSTUNDEN = 8.0
# Grenzen je Buchung (Security-Review 3.9): Dauer, Abstand zu heute
MAX_DAUER_STUNDEN = 24
MAX_TAGE_ZURUECK = 31
MAX_TAGE_VORAUS = 1
# Annahme ohne Holiday List: Montag (0) bis Freitag (4)
ARBEITSTAGE = (0, 1, 2, 3, 4)


class ZeitKonflikt(frappe.ValidationError):
	"""Fachlicher Konflikt einer Buchung; ``grund`` ist maschinenlesbar.

	``ueberlappung`` (ERPNext-Überlappung mit einer anderen Zeile), ``laeuft_bereits``
	(Start, obwohl schon eine andere Buchung läuft), ``ungueltig`` (Ende nicht nach Beginn),
	``zu_lang`` (mehr als 24 h), ``ausserhalb_zeitraum`` (mehr als 31 Tage zurück oder mehr
	als einen Tag voraus, in der Vorführung das weitere Fenster des Kerns), ``schluessel_fremd`` (Idempotenz-Key gehört zu Buchungen eines
	anderen Mitarbeiters).
	"""

	def __init__(self, grund: str, message: str, details: dict | None = None):
		super().__init__(message)
		self.grund = grund
		self.details = details or {}


# --------------------------------------------------------------------------- Einrichtung


def activity_types_sicherstellen() -> list[str]:
	"""Activity Types „Arbeitszeit“, „Fahrzeit“, „Pause“ anlegen, falls sie fehlen (idempotent).

	Vorhandene bleiben unverändert (Kostensätze pflegt das Büro im Desk); ein deaktivierter
	Typ bleibt deaktiviert - ERPNext lehnt Buchungen darauf ab.
	"""
	angelegt = []
	for name in ACTIVITY_TYPES:
		if not frappe.db.exists("Activity Type", name):
			frappe.get_doc({"doctype": "Activity Type", "activity_type": name}).insert(
				ignore_permissions=True
			)
			angelegt.append(name)
	return angelegt


def timesheet_felder() -> dict[str, list[dict]]:
	return {
		"Timesheet Detail": [
			{
				"fieldname": SYNC_KEY_FELD,
				"fieldtype": "Data",
				"label": "Mobile Sync Key",
				"insert_after": "description",
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"print_hide": 1,
				"search_index": 1,
				"module": MODULE,
				"description": "Idempotency key of the mobile booking (Corevatis app).",
			}
		]
	}


def ensure_setup() -> None:
	"""Aus ``after_install``/``after_migrate``: Custom Field und Activity Types."""
	create_custom_fields(timesheet_felder(), update=True)
	activity_types_sicherstellen()


# --------------------------------------------------------------------------- Mitarbeiter


def employee_fuer_user(user: str) -> dict | None:
	"""Aktiver Employee mit ``user_id`` = Benutzer (``name``, ``employee_name``, ``company``)."""
	if not user:
		return None
	rows = frappe.get_all(
		"Employee",
		filters={"user_id": user, "status": "Active"},
		fields=["name", "employee_name", "company", "user_id"],
		order_by="creation asc",
		limit=1,
	)
	return dict(rows[0]) if rows else None


def mitarbeiter(employee: str) -> dict | None:
	"""Employee lesend (``name``, ``employee_name``, ``company``, ``user_id``, ``status``) oder None."""
	if not employee:
		return None
	row = frappe.db.get_value(
		"Employee", employee, ["name", "employee_name", "company", "user_id", "status"], as_dict=True
	)
	return dict(row) if row else None


# --------------------------------------------------------------------------- Buchen


def _zeit(value) -> _dt.datetime:
	return get_datetime(value).replace(microsecond=0, tzinfo=None)


def _iso(value) -> str | None:
	return get_datetime(value).replace(microsecond=0).isoformat() if value else None


def _tagesteile(von: _dt.datetime, bis: _dt.datetime) -> list[tuple[_dt.datetime, _dt.datetime]]:
	"""Zeitraum an Mitternacht teilen (je Kalendertag ein Teil)."""
	teile = []
	start = von
	while start < bis:
		mitternacht = _dt.datetime.combine(start.date() + _dt.timedelta(days=1), _dt.time())
		ende = min(bis, mitternacht)
		teile.append((start, ende))
		start = ende
	return teile


def _tages_timesheet(employee: str, company: str, tag: _dt.date):
	"""Entwurf des Mitarbeiters für den Tag (ohne festes Projekt) oder ein neues Timesheet."""
	namen = frappe.get_all(
		"Timesheet",
		filters={
			"employee": employee,
			"docstatus": 0,
			"start_date": tag,
			"end_date": tag,
			"parent_project": ["is", "not set"],
		},
		order_by="creation asc",
		limit=1,
		pluck="name",
	)
	if namen:
		return frappe.get_doc("Timesheet", namen[0])
	ts = frappe.new_doc("Timesheet")
	ts.employee = employee
	ts.company = company
	ts.currency = frappe.get_cached_value("Company", company, "default_currency")
	return ts


def _zeile(row, ts) -> dict:
	return {
		"name": row.name,
		"timesheet": ts.name,
		"timesheet_status": ts.status,
		"docstatus": ts.docstatus,
		"von": _iso(row.from_time),
		"bis": _iso(row.to_time),
		"stunden": flt(row.hours, 4),
		"activity_type": row.activity_type,
		"project": row.project,
		"task": row.task,
		"notiz": row.description,
		"abrechenbar": bool(row.is_billable),
		"sync_key": row.get(SYNC_KEY_FELD),
	}


def _vorhandene(employee: str, idempotency_key: str) -> list[dict]:
	"""Zeilen mit diesem Key - nur aus Timesheets des Mitarbeiters (Security-Review 3.9).

	Gehört der Key zu Zeilen eines anderen Mitarbeiters, wird nichts zurückgegeben, sondern
	``ZeitKonflikt("schluessel_fremd")`` - sonst ließen sich fremde Buchungen über einen
	erratenen Key lesen.
	"""
	ts_t = frappe.qb.DocType("Timesheet")
	td_t = frappe.qb.DocType("Timesheet Detail")
	treffer = (
		frappe.qb.from_(td_t)
		.join(ts_t)
		.on(td_t.parent == ts_t.name)
		.select(td_t.name, td_t.parent, ts_t.employee)
		.where(
			(getattr(td_t, SYNC_KEY_FELD) == idempotency_key)
			& (td_t.parenttype == "Timesheet")
			& (ts_t.docstatus < 2)
		)
		.orderby(td_t.from_time)
	).run(as_dict=True)
	if any(t.employee != employee for t in treffer):
		raise ZeitKonflikt("schluessel_fremd", _("This booking key is already used."), {})
	zeilen = []
	for eintrag in treffer:
		ts = frappe.get_doc("Timesheet", eintrag.parent)
		row = next(r for r in ts.time_logs if r.name == eintrag.name)
		zeilen.append(_zeile(row, ts))
	return zeilen


def _tage(fenster: _dt.timedelta) -> int:
	return int(fenster.total_seconds() // 86400)


def zeitraum_pruefen(
	von: _dt.datetime,
	bis: _dt.datetime | None = None,
	zurueck: _dt.timedelta | None = None,
	voraus: _dt.timedelta | None = None,
) -> None:
	"""Grenzen gegen beliebig lange bzw. weit entfernte Zeiträume (Security-Review 3.9).

	Höchstens ``MAX_DAUER_STUNDEN`` je Buchung, Zeitpunkte höchstens ``MAX_TAGE_ZURUECK``
	Tage in der Vergangenheit und ``MAX_TAGE_VORAUS`` in der Zukunft (Uhrabweichung des
	Geräts). ``zurueck``/``voraus`` (Handwerker#108) übergibt der Kern aus seinen Grenzen
	(``corevatis.api.zeiten.grenzen``, in der Vorführung 365 Tage für eigene Buchungen). Sie
	**erweitern** das Standardfenster, verengen es aber nicht: Die engere Prüfung der
	App-Zeitstempel (7 Tage/5 Minuten) macht der Kern selbst, hier wird auch der Beginn einer
	laufenden Buchung geprüft, der beim Stopp älter sein darf als das Fenster des Kerns. Ohne
	Angabe (Desk) gilt das Standardfenster. Verstoß → ``ZeitKonflikt("ausserhalb_zeitraum")``
	(Text und ``details.richtung`` je Richtung) bzw. ``("zu_lang")``.
	"""
	zurueck = max(zurueck or _dt.timedelta(0), _dt.timedelta(days=MAX_TAGE_ZURUECK))
	voraus = max(voraus or _dt.timedelta(0), _dt.timedelta(days=MAX_TAGE_VORAUS))
	jetzt = now_datetime()
	for wert in (von, bis):
		if wert is None:
			continue
		if jetzt - zurueck <= wert <= jetzt + voraus:
			continue
		zukunft = wert > jetzt
		# angefangene Tage: 3,2 Tage voraus bei erlaubtem 1 Tag → „4 Tage“
		abstand = -(-abs(wert - jetzt).total_seconds() // 86400)
		erlaubt = _tage(voraus if zukunft else zurueck)
		text = (
			_("The time lies {0} days in the future; at most {1} are allowed.")
			if zukunft
			else _("The time lies {0} days in the past; at most {1} are allowed.")
		)
		raise ZeitKonflikt(
			"ausserhalb_zeitraum",
			text.format(int(abstand), erlaubt),
			{
				"zeitpunkt": _iso(wert),
				"richtung": "zukunft" if zukunft else "vergangenheit",
				"abstand_tage": int(abstand),
				"erlaubt_zurueck_tage": _tage(zurueck),
				"erlaubt_voraus_tage": _tage(voraus),
				# bis #108 das einzige Detail, für ältere Apps beibehalten
				"max_tage_zurueck": _tage(zurueck),
			},
		)
	if bis is not None and (bis - von) > _dt.timedelta(hours=MAX_DAUER_STUNDEN):
		raise ZeitKonflikt(
			"zu_lang",
			_("A booking may last at most {0} hours.").format(MAX_DAUER_STUNDEN),
			{"max_stunden": MAX_DAUER_STUNDEN},
		)


def zeit_buchen(
	employee: str,
	project: str | None,
	task: str | None,
	activity_type: str,
	von,
	bis,
	idempotency_key: str | None = None,
	notiz: str | None = None,
	*,
	zurueck: _dt.timedelta | None = None,
	voraus: _dt.timedelta | None = None,
) -> dict:
	"""Buchung als Zeile(n) im Timesheet des Tages; Rückgabe ``timesheet``, ``zeilen``, ``duplikat``.

	``von``/``bis``: ``datetime`` oder ISO-Text in Site-Zeit, ``bis`` > ``von``. Über Mitternacht
	eine Zeile je Tag. ERPNext prüft Überlappung, Task ↔ Projekt, abgebrochene Tasks und
	deaktivierte Activity Types; Überlappung → ``ZeitKonflikt("ueberlappung")``, sonst
	``frappe.ValidationError``. ``zurueck``/``voraus``: Zeitfenster des Kerns (``zeitraum_pruefen``).
	Gespeichert wird mit ``ignore_permissions`` - die Rechte
	(eigener Employee bzw. Meister/Büro) prüft der Kern vor dem Aufruf.
	"""
	from erpnext.projects.doctype.timesheet.timesheet import OverlapError

	if idempotency_key:
		vorhanden = _vorhandene(employee, idempotency_key)
		if vorhanden:
			return {"timesheet": vorhanden[0]["timesheet"], "zeilen": vorhanden, "duplikat": True}

	von, bis = _zeit(von), _zeit(bis)
	if bis <= von:
		raise ZeitKonflikt("ungueltig", _("End time must be after start time."), {"von": _iso(von)})
	# vor jeder Schleife: höchstens 24 h, also höchstens zwei Tagesteile
	zeitraum_pruefen(von, bis, zurueck, voraus)
	if activity_type not in ACTIVITY_TYPES:
		raise frappe.ValidationError(_("Unknown activity type {0}.").format(activity_type))
	if not frappe.db.exists("Activity Type", activity_type):
		activity_types_sicherstellen()
	ma = mitarbeiter(employee)
	if not ma:
		raise frappe.DoesNotExistError(_("Employee {0} not found.").format(employee))

	zeilen = []
	for start, ende in _tagesteile(von, bis):
		ts = _tages_timesheet(employee, ma["company"], start.date())
		row = ts.append(
			"time_logs",
			{
				"activity_type": activity_type,
				"from_time": start,
				"to_time": ende,
				"hours": (ende - start).total_seconds() / 3600,
				"project": project or None,
				"task": task or None,
				"is_billable": ABRECHENBAR.get(activity_type, 0),
				"description": notiz or None,
				SYNC_KEY_FELD: idempotency_key or None,
			},
		)
		try:
			# ignore_permissions bewusst: Die Funktion ist nicht whitelisted (kein REST-Aufruf);
			# der Kern hat vorher geprüft, dass der Employee dem angemeldeten Benutzer gehört
			# (Monteur) bzw. der Benutzer Meister/Büro ist. Die ERPNext-Rolle des Monteurs darf
			# Timesheets nur „erfassen“, das Timesheet des Tages kann aber ein anderer (Meister)
			# angelegt haben.
			ts.save(ignore_permissions=True)
		except OverlapError as exc:
			raise ZeitKonflikt(
				"ueberlappung",
				frappe.utils.strip_html_tags(str(exc)),
				{"von": _iso(start), "bis": _iso(ende)},
			)
		row = next(r for r in ts.time_logs if r.idx == row.idx)
		zeilen.append(_zeile(row, ts))
	return {"timesheet": zeilen[0]["timesheet"], "zeilen": zeilen, "duplikat": False}


# --------------------------------------------------------------------------- Laufende Buchung


def _lauf(doc) -> dict:
	return {
		"employee": doc.employee,
		"project": doc.project,
		"task": doc.task,
		"activity_type": doc.activity_type,
		"von": _iso(doc.von),
		"notiz": doc.notiz,
		"sync_key": doc.sync_key,
	}


def offene_buchung(employee: str) -> dict | None:
	"""Laufende Buchung (Start ohne Stopp) des Mitarbeiters oder None."""
	if not employee or not frappe.db.exists(LAUF_DOCTYPE, employee):
		return None
	return _lauf(frappe.get_doc(LAUF_DOCTYPE, employee))


def lauf_starten(
	employee: str,
	project: str | None,
	task: str | None,
	activity_type: str,
	von,
	idempotency_key: str | None = None,
	notiz: str | None = None,
	*,
	zurueck: _dt.timedelta | None = None,
	voraus: _dt.timedelta | None = None,
) -> dict:
	"""Laufende Buchung anlegen. Läuft schon eine (anderer Key) → ``ZeitKonflikt("laeuft_bereits")``.

	Den Wechsel (laufende stoppen, neue starten) steuert der Kern; ``zurueck``/``voraus`` wie bei
	``zeit_buchen``.
	"""
	if activity_type not in ACTIVITY_TYPES:
		raise frappe.ValidationError(_("Unknown activity type {0}.").format(activity_type))
	vorhanden = offene_buchung(employee)
	if vorhanden:
		if idempotency_key and vorhanden["sync_key"] == idempotency_key:
			return vorhanden
		raise ZeitKonflikt("laeuft_bereits", _("A booking is already running."), {"laufend": vorhanden})
	if not mitarbeiter(employee):
		raise frappe.DoesNotExistError(_("Employee {0} not found.").format(employee))
	zeitraum_pruefen(_zeit(von), zurueck=zurueck, voraus=voraus)
	doc = frappe.get_doc(
		{
			"doctype": LAUF_DOCTYPE,
			"employee": employee,
			"project": project or None,
			"task": task or None,
			"activity_type": activity_type,
			"von": _zeit(von),
			"notiz": notiz or None,
			"sync_key": idempotency_key or None,
		}
	).insert(ignore_permissions=True)
	return _lauf(doc)


def lauf_beenden(employee: str) -> dict | None:
	"""Laufende Buchung entfernen (ohne zu buchen) und zurückgeben; None, wenn keine läuft."""
	vorhanden = offene_buchung(employee)
	if vorhanden:
		frappe.delete_doc(LAUF_DOCTYPE, employee, ignore_permissions=True, force=True)
	return vorhanden


# --------------------------------------------------------------------------- Lesen


def tagesbuchungen(employee: str, datum) -> list[dict]:
	"""Zeilen aller nicht stornierten Timesheets des Mitarbeiters, die am Tag beginnen."""
	tag = getdate(datum)
	ts = frappe.qb.DocType("Timesheet")
	td = frappe.qb.DocType("Timesheet Detail")
	beginn = _dt.datetime.combine(tag, _dt.time())
	rows = (
		frappe.qb.from_(td)
		.join(ts)
		.on(td.parent == ts.name)
		.select(
			td.name,
			td.parent.as_("timesheet"),
			ts.status.as_("timesheet_status"),
			ts.docstatus,
			td.from_time,
			td.to_time,
			td.hours,
			td.activity_type,
			td.project,
			td.task,
			td.description,
			td.is_billable,
			getattr(td, SYNC_KEY_FELD).as_("sync_key"),
		)
		.where(
			(ts.employee == employee)
			& (ts.docstatus < 2)
			& (td.parenttype == "Timesheet")
			& (td.from_time >= beginn)
			& (td.from_time < beginn + _dt.timedelta(days=1))
		)
		.orderby(td.from_time)
	).run(as_dict=True)
	tasks = {r.task for r in rows if r.task}
	titel = (
		dict(
			frappe.get_all(
				"Task", filters={"name": ["in", list(tasks)]}, fields=["name", "subject"], as_list=True
			)
		)
		if tasks
		else {}
	)
	projekte = {r.project for r in rows if r.project}
	projekt_namen = (
		dict(
			frappe.get_all(
				"Project",
				filters={"name": ["in", list(projekte)]},
				fields=["name", "project_name"],
				as_list=True,
			)
		)
		if projekte
		else {}
	)
	return [
		{
			"name": r.name,
			"timesheet": r.timesheet,
			"timesheet_status": r.timesheet_status,
			"docstatus": r.docstatus,
			"von": _iso(r.from_time),
			"bis": _iso(r.to_time),
			"stunden": flt(r.hours, 4),
			"activity_type": r.activity_type,
			"project": r.project,
			"project_name": projekt_namen.get(r.project),
			"task": r.task,
			"task_titel": titel.get(r.task),
			"notiz": r.description,
			"abrechenbar": bool(r.is_billable),
			"sync_key": r.sync_key,
		}
		for r in rows
	]


def sollstunden(employee: str, datum) -> dict:
	"""Sollstunden des Tages: ``stunden``, ``quelle`` (``holiday_list``/``annahme``), ``feiertag``.

	Holiday List des Employees, sonst die Standard-Holiday-List der Firma (ERPNext
	``get_holiday_list_for_employee``). Liegt der Tag in ihrem Zeitraum: Feiertag bzw.
	Wochenende (auch „weekly off“) → 0 h, halber Tag → 4 h, sonst 8 h. Ohne passende Liste
	gilt die Annahme 8 h Mo-Fr, 0 h Sa/So (ERPNext ohne HRMS kennt keine Tagesarbeitszeit).
	"""
	from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee

	tag = getdate(datum)
	liste = get_holiday_list_for_employee(employee, raise_exception=False) if employee else None
	if liste:
		von, bis = frappe.db.get_value("Holiday List", liste, ["from_date", "to_date"])
		if von and bis and getdate(von) <= tag <= getdate(bis):
			frei = frappe.db.get_value(
				"Holiday",
				{"parent": liste, "parenttype": "Holiday List", "holiday_date": tag},
				["description", "is_half_day"],
				as_dict=True,
			)
			if frei and not frei.is_half_day:
				stunden = 0.0
			elif frei:
				stunden = STANDARD_SOLLSTUNDEN / 2
			else:
				stunden = STANDARD_SOLLSTUNDEN
			return {
				"stunden": stunden,
				"quelle": "holiday_list",
				"holiday_list": liste,
				"feiertag": bool(frei),
				"beschreibung": frappe.utils.strip_html_tags(frei.description or "") if frei else None,
			}
	arbeitstag = tag.weekday() in ARBEITSTAGE
	return {
		"stunden": STANDARD_SOLLSTUNDEN if arbeitstag else 0.0,
		"quelle": "annahme",
		"holiday_list": liste or None,
		"feiertag": False,
		"beschreibung": None,
	}
