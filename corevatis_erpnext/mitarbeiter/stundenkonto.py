"""Stundenkonto und Urlaubskonto je Mitarbeiter (Phase 2 P2.5, Handwerker#88) - ohne HRMS.

Rechenweg (Monat):

- **Soll je Tag:** Tagesarbeitszeit der Planung (``Corevatis Planung Einstellungen``, Standard 8 h) an
  Montag bis Freitag; 0 h am Wochenende, an Feiertagen und freien Tagen der Holiday List des
  Mitarbeiters bzw. der Firma (``get_holiday_list_for_employee``), halber Feiertag = halbe Tagesarbeitszeit.
- **Abwesenheit** (Doctype ``Abwesenheit``: Urlaub, Krank, Schulung, Sonstiges) senkt das Soll des Tages:
  ganztags auf 0, mit Uhrzeiten um die Dauer (höchstens auf 0). Alle Arten gelten als bezahlt (Annahme,
  siehe Doku); ein Urlaubstag zählt im Urlaubskonto als Anteil Dauer/Tagesarbeitszeit (ganztags 1,0).
- **Ist je Tag:** Stunden der Timesheet-Zeilen des Mitarbeiters (Entwurf und eingereicht) ohne Pause
  (Activity Type ``Pause``); Arbeitszeit und Fahrzeit zählen.
- **Saldo** = Ist - Soll; Tage nach heute zählen nicht ins Soll (sonst stünde jeder Monat im Minus, bevor
  er gearbeitet ist). **Saldo Jahr** = Übertrag zum 1. Januar (``Employee.cv_saldo_uebertrag``) + Saldi der
  Monate bis zum gewählten Monat.
- **Urlaubskonto Jahr:** Anspruch (``Employee.cv_urlaubsanspruch``, 0/leer = 30) + Übertrag Vorjahr
  (``cv_urlaub_uebertrag``) - genommen (Urlaubstage bis heute) - geplant (nach heute) = Rest.

Rechte: Büro, Meister und System Manager sehen alle Mitarbeiter; alle anderen nur den eigenen
Employee (``user_id``). Die **Art** einer Abwesenheit (Krank ist ein Gesundheitsdatum) sehen nur Büro,
System Manager und der Betroffene selbst (wie ``planung.abwesenheit.art_anzeige``); andere sehen
„Abwesend“ und keine Urlaubs-/Krankheitsspalten.
"""

from __future__ import annotations

import calendar
import datetime as dt

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, strip_html_tags, to_timedelta, today

from corevatis_erpnext.planung import abwesenheit as abw
from corevatis_erpnext.planung.einstellungen import einstellungen
from corevatis_erpnext.zeiterfassung import PAUSE, employee_fuer_user

ARBEITSTAGE = (0, 1, 2, 3, 4)
ART_URLAUB = "Urlaub"
ART_KRANK = "Krank"
FELD_ANSPRUCH = "cv_urlaubsanspruch"
FELD_UEBERTRAG = "cv_urlaub_uebertrag"
FELD_SALDO = "cv_saldo_uebertrag"
STANDARD_ANSPRUCH = 30.0
ROLLEN_ALLE = ("Büro", "Meister", "System Manager")
WOCHENTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


class StundenkontoFehler(frappe.ValidationError):
	pass


# --- Rechte ------------------------------------------------------------------------------------


def darf_alle(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(set(ROLLEN_ALLE) & set(frappe.get_roles(user)))


def arten_sichtbar(employee_user: str | None, user: str | None = None) -> bool:
	"""Art der Abwesenheit (Urlaub, Krank …) nur für Büro/System Manager und den Betroffenen."""
	user = user or frappe.session.user
	return abw.darf_alle(user) or bool(employee_user and employee_user == user)


def mitarbeiter_liste(
	user: str | None = None, mitarbeiter: str | None = None, firma: str | None = None
) -> list[dict]:
	"""Mitarbeiter, die der Benutzer sehen darf: alle aktiven (Büro, Meister, System Manager) oder nur
	der eigene. ``mitarbeiter`` schränkt ein; ein fremder Name ohne Recht → ``PermissionError``."""
	user = user or frappe.session.user
	felder = ["name", "employee_name", "user_id", "company", FELD_ANSPRUCH, FELD_UEBERTRAG, FELD_SALDO]
	if darf_alle(user):
		filters: dict = {"status": "Active"}
		if mitarbeiter:
			filters["name"] = mitarbeiter
		if firma:
			filters["company"] = firma
		return [
			dict(r)
			for r in frappe.get_all("Employee", filters=filters, fields=felder, order_by="employee_name asc")
		]
	eigener = employee_fuer_user(user)
	if not eigener:
		return []
	if mitarbeiter and mitarbeiter != eigener["name"]:
		frappe.throw(_("Sie sehen nur Ihr eigenes Stundenkonto."), frappe.PermissionError)
	row = frappe.db.get_value("Employee", eigener["name"], felder, as_dict=True)
	return [dict(row)] if row else []


# --- Soll ---------------------------------------------------------------------------------------


def feiertage(employee: str, von: dt.date, bis: dt.date) -> dict[dt.date, dict]:
	"""Freie Tage der Holiday List des Mitarbeiters (sonst der Firma) im Zeitraum: Feiertage, halbe Tage,
	freie Wochentage (``weekly_off``)."""
	from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee

	liste = get_holiday_list_for_employee(employee, raise_exception=False) if employee else None
	if not liste:
		return {}
	rows = frappe.get_all(
		"Holiday",
		filters={"parent": liste, "parenttype": "Holiday List", "holiday_date": ["between", [von, bis]]},
		fields=["holiday_date", "description", "is_half_day", "weekly_off"],
	)
	return {
		getdate(r.holiday_date): {
			"halb": bool(r.is_half_day),
			"wochenende": bool(r.weekly_off),
			"bezeichnung": strip_html_tags(r.description or "").strip() or _("Feiertag"),
		}
		for r in rows
	}


def soll_tag(tag: dt.date, tagesarbeitszeit: float, frei: dict | None) -> float:
	if tag.weekday() not in ARBEITSTAGE:
		return 0.0
	if frei:
		return tagesarbeitszeit / 2 if frei["halb"] else 0.0
	return tagesarbeitszeit


def _tage(von: dt.date, bis: dt.date) -> list[dt.date]:
	return [von + dt.timedelta(days=n) for n in range((bis - von).days + 1)]


# --- Abwesenheit und Ist -------------------------------------------------------------------------


def abwesenheit_je_tag(employee: str, von: dt.date, bis: dt.date) -> dict[dt.date, list[dict]]:
	"""Je Tag die Abwesenheiten des Mitarbeiters: ``art``, ``ganztags``, ``stunden`` (nur mit Uhrzeiten)."""
	ergebnis: dict[dt.date, list[dict]] = {}
	for a in abw.abwesenheiten(von, bis, employees=[employee]):
		a_von, a_bis = max(getdate(a.von), von), min(getdate(a.bis), bis)
		stunden = None
		if not cint(a.ganztags) and a.von_uhrzeit and a.bis_uhrzeit:
			stunden = max(
				(to_timedelta(a.bis_uhrzeit) - to_timedelta(a.von_uhrzeit)).total_seconds() / 3600, 0.0
			)
		for tag in _tage(a_von, a_bis):
			ergebnis.setdefault(tag, []).append(
				{"name": a.name, "art": a.art, "ganztags": stunden is None, "stunden": stunden}
			)
	return ergebnis


def ist_je_tag(employee: str, von: dt.date, bis: dt.date) -> dict[dt.date, float]:
	"""Stunden der Timesheet-Zeilen je Tag (Entwurf und eingereicht), ohne Pause."""
	rows = frappe.db.sql(
		"""
		SELECT DATE(td.from_time) AS tag, SUM(td.hours) AS stunden
		FROM `tabTimesheet Detail` td
		JOIN `tabTimesheet` ts ON ts.name = td.parent
		WHERE ts.employee = %(employee)s AND ts.docstatus < 2
			AND td.from_time >= %(von)s AND td.from_time < %(bis)s
			AND (td.activity_type IS NULL OR td.activity_type != %(pause)s)
		GROUP BY DATE(td.from_time)
		""",
		{
			"employee": employee,
			"von": dt.datetime.combine(von, dt.time.min),
			"bis": dt.datetime.combine(bis + dt.timedelta(days=1), dt.time.min),
			"pause": PAUSE,
		},
		as_dict=True,
	)
	return {getdate(r.tag): flt(r.stunden, 2) for r in rows}


# --- Monat ---------------------------------------------------------------------------------------


def monatsgrenzen(jahr: int, monat: int) -> tuple[dt.date, dt.date]:
	if not 1 <= monat <= 12:
		frappe.throw(_("Monat {0} gibt es nicht.").format(monat), StundenkontoFehler)
	return dt.date(jahr, monat, 1), dt.date(jahr, monat, calendar.monthrange(jahr, monat)[1])


def monat(
	employee: str,
	jahr: int,
	monat_nr: int,
	e: frappe._dict | None = None,
	arten: bool = True,
	heute: dt.date | None = None,
) -> dict:
	"""Stundenkonto eines Monats: ``tage`` (je Kalendertag) und Summen. Tage nach ``heute`` zählen nicht
	ins Soll; ``arten=False`` ersetzt die Art jeder Abwesenheit durch „Abwesend“."""
	e = e or einstellungen()
	tz = flt(e.tagesarbeitszeit)
	heute = heute or getdate(today())
	von, bis = monatsgrenzen(jahr, monat_nr)
	frei = feiertage(employee, von, bis)
	ist = ist_je_tag(employee, von, bis)
	abwesend = abwesenheit_je_tag(employee, von, bis)
	tage = []
	summe = frappe._dict(
		solltage=0.0,
		soll_brutto=0.0,
		abwesend=0.0,
		soll=0.0,
		ist=0.0,
		urlaubstage=0.0,
		kranktage=0.0,
		sonstige_tage=0.0,
	)
	for tag in _tage(von, bis):
		f = frei.get(tag)
		brutto = soll_tag(tag, tz, f)
		ab_stunden = 0.0
		ab_arten: list[str] = []
		for a in abwesend.get(tag, []):
			anteil_stunden = brutto if a["ganztags"] else min(flt(a["stunden"]), brutto)
			if brutto > 0 and tz > 0:
				anteil = anteil_stunden / tz
				if a["art"] == ART_URLAUB:
					summe.urlaubstage += anteil
				elif a["art"] == ART_KRANK:
					summe.kranktage += anteil
				else:
					summe.sonstige_tage += anteil
			ab_stunden += anteil_stunden
			ab_arten.append(_(a["art"]) if arten else _("Abwesend"))
		ab_stunden = min(ab_stunden, brutto)
		zaehlt = tag <= heute
		soll = (brutto - ab_stunden) if zaehlt else 0.0
		ist_tag = flt(ist.get(tag, 0.0), 2)
		zeile = {
			"datum": str(tag),
			"wochentag": WOCHENTAGE[tag.weekday()],
			"wochenende": tag.weekday() not in ARBEITSTAGE,
			"feiertag": f["bezeichnung"] if f and not f["wochenende"] else None,
			"frei": bool(f and f["wochenende"]),
			"abwesenheit": ", ".join(dict.fromkeys(ab_arten)) if ab_arten else None,
			"soll_brutto": flt(brutto, 2),
			"abwesend": flt(ab_stunden, 2),
			"soll": flt(soll, 2),
			"ist": ist_tag,
			"saldo": flt(ist_tag - soll, 2),
			"zaehlt": zaehlt,
		}
		tage.append(zeile)
		if brutto > 0:
			summe.solltage += 1
		summe.soll_brutto += brutto
		summe.abwesend += ab_stunden
		summe.soll += soll
		summe.ist += ist_tag
	saldo = flt(summe.ist - summe.soll, 2)
	laufend = 0.0
	for z in tage:
		laufend += z["saldo"]
		z["laufend"] = flt(laufend, 2)
	return {
		"mitarbeiter": employee,
		"jahr": jahr,
		"monat": monat_nr,
		"von": str(von),
		"bis": str(bis),
		"tagesarbeitszeit": tz,
		"tage": tage,
		"solltage": int(summe.solltage),
		"soll_brutto": flt(summe.soll_brutto, 2),
		"abwesend": flt(summe.abwesend, 2),
		"soll": flt(summe.soll, 2),
		"ist": flt(summe.ist, 2),
		"saldo": saldo,
		"ueberstunden": max(saldo, 0.0),
		"minusstunden": max(-saldo, 0.0),
		"urlaubstage": flt(summe.urlaubstage, 2),
		"kranktage": flt(summe.kranktage, 2),
		"sonstige_tage": flt(summe.sonstige_tage, 2),
		"abgeschlossen": bis <= heute,
	}


def saldo_jahr(employee: str, jahr: int, bis_monat: int, uebertrag: float, e=None, heute=None) -> float:
	"""Übertrag zum 1. Januar plus Saldi der Monate 1..``bis_monat``."""
	e = e or einstellungen()
	summe = flt(uebertrag)
	for m in range(1, bis_monat + 1):
		summe += monat(employee, jahr, m, e, heute=heute)["saldo"]
	return flt(summe, 2)


# --- Urlaubskonto ---------------------------------------------------------------------------------


def urlaubskonto(
	employee: str,
	jahr: int,
	e: frappe._dict | None = None,
	heute: dt.date | None = None,
	ausser: str | None = None,
	werte: dict | None = None,
) -> dict:
	"""Urlaubskonto des Jahres: Anspruch, Übertrag, genommen (bis heute), geplant (nach heute), Rest,
	dazu Krankheits- und sonstige Abwesenheitstage. ``ausser`` lässt eine Abwesenheit aus (beim Bearbeiten).
	``werte``: Employee-Felder, falls schon gelesen."""
	e = e or einstellungen()
	tz = flt(e.tagesarbeitszeit)
	heute = heute or getdate(today())
	von, bis = dt.date(jahr, 1, 1), dt.date(jahr, 12, 31)
	werte = (
		werte
		or frappe.db.get_value("Employee", employee, [FELD_ANSPRUCH, FELD_UEBERTRAG], as_dict=True)
		or {}
	)
	anspruch = flt(werte.get(FELD_ANSPRUCH)) or STANDARD_ANSPRUCH
	uebertrag = flt(werte.get(FELD_UEBERTRAG))
	frei = feiertage(employee, von, bis)
	genommen = geplant = kranktage = sonstige = 0.0
	for tag, liste in abwesenheit_je_tag(employee, von, bis).items():
		brutto = soll_tag(tag, tz, frei.get(tag))
		if brutto <= 0 or tz <= 0:
			continue
		for a in liste:
			if ausser and a["name"] == ausser:
				continue
			anteil = (brutto if a["ganztags"] else min(flt(a["stunden"]), brutto)) / tz
			if a["art"] == ART_URLAUB:
				if tag <= heute:
					genommen += anteil
				else:
					geplant += anteil
			elif a["art"] == ART_KRANK:
				kranktage += anteil
			else:
				sonstige += anteil
	return {
		"mitarbeiter": employee,
		"jahr": jahr,
		"anspruch": flt(anspruch, 2),
		"uebertrag": flt(uebertrag, 2),
		"genommen": flt(genommen, 2),
		"geplant": flt(geplant, 2),
		"rest": flt(anspruch + uebertrag - genommen - geplant, 2),
		"kranktage": flt(kranktage, 2),
		"sonstige_tage": flt(sonstige, 2),
	}


def urlaubstage(
	employee: str, von, bis, ganztags: int = 1, von_uhrzeit=None, bis_uhrzeit=None, e=None
) -> float:
	"""Urlaubstage (Arbeitstage als Anteil) eines Zeitraums - für den Hinweis im Formular."""
	e = e or einstellungen()
	tz = flt(e.tagesarbeitszeit)
	von_tag, bis_tag = getdate(von), getdate(bis)
	if bis_tag < von_tag or tz <= 0:
		return 0.0
	frei = feiertage(employee, von_tag, bis_tag)
	summe = 0.0
	for tag in _tage(von_tag, bis_tag):
		brutto = soll_tag(tag, tz, frei.get(tag))
		if brutto <= 0:
			continue
		if cint(ganztags) or not (von_uhrzeit and bis_uhrzeit):
			summe += brutto / tz
		else:
			stunden = max((to_timedelta(bis_uhrzeit) - to_timedelta(von_uhrzeit)).total_seconds() / 3600, 0.0)
			summe += min(stunden, brutto) / tz
	return flt(summe, 2)


# --- Übersicht (Report) ---------------------------------------------------------------------------


def uebersicht(jahr: int, monat_nr: int, mitarbeiter: str | None = None, user: str | None = None) -> dict:
	"""Eine Zeile je sichtbarem Mitarbeiter: Monat, Saldo Jahr, Urlaubskonto. Spalten mit Art
	(Urlaub, Krank) nur, wenn der Benutzer die Arten des Mitarbeiters sehen darf."""
	user = user or frappe.session.user
	e = einstellungen()
	heute = getdate(today())
	zeilen = []
	for emp in mitarbeiter_liste(user, mitarbeiter):
		sichtbar = arten_sichtbar(emp.get("user_id"), user)
		m = monat(emp["name"], jahr, monat_nr, e, arten=sichtbar, heute=heute)
		u = urlaubskonto(emp["name"], jahr, e, heute=heute, werte=emp)
		zeile = {
			"mitarbeiter": emp["name"],
			"mitarbeiter_name": emp.get("employee_name"),
			"benutzer": emp.get("user_id"),
			"arten_sichtbar": sichtbar,
			"solltage": m["solltage"],
			"soll_brutto": m["soll_brutto"],
			"abwesend": m["abwesend"],
			"abwesend_tage": flt(m["urlaubstage"] + m["kranktage"] + m["sonstige_tage"], 2),
			"soll": m["soll"],
			"ist": m["ist"],
			"saldo": m["saldo"],
			"saldo_jahr": saldo_jahr(emp["name"], jahr, monat_nr, flt(emp.get(FELD_SALDO)), e, heute),
			"urlaub_anspruch": u["anspruch"] + u["uebertrag"] if sichtbar else None,
			"urlaub_genommen": u["genommen"] if sichtbar else None,
			"urlaub_geplant": u["geplant"] if sichtbar else None,
			"urlaub_rest": u["rest"] if sichtbar else None,
			"kranktage_jahr": u["kranktage"] if sichtbar else None,
		}
		zeilen.append(zeile)
	return {
		"jahr": jahr,
		"monat": monat_nr,
		"tagesarbeitszeit": flt(e.tagesarbeitszeit),
		"zeilen": zeilen,
		"alle": darf_alle(user),
	}


def monatsuebersicht(employee: str, jahr: int, monat_nr: int, user: str | None = None) -> dict:
	"""Tage eines Mitarbeiters im Monat (Rechte wie ``mitarbeiter_liste``)."""
	user = user or frappe.session.user
	emps = mitarbeiter_liste(user, employee)
	if not emps:
		frappe.throw(
			_("Mitarbeiter {0} nicht gefunden oder kein Zugriff.").format(employee), frappe.PermissionError
		)
	emp = emps[0]
	e = einstellungen()
	heute = getdate(today())
	sichtbar = arten_sichtbar(emp.get("user_id"), user)
	m = monat(emp["name"], jahr, monat_nr, e, arten=sichtbar, heute=heute)
	m["mitarbeiter_name"] = emp.get("employee_name")
	m["saldo_jahr"] = saldo_jahr(emp["name"], jahr, monat_nr, flt(emp.get(FELD_SALDO)), e, heute)
	m["urlaub"] = urlaubskonto(emp["name"], jahr, e, heute=heute, werte=emp) if sichtbar else None
	m["arten_sichtbar"] = sichtbar
	m["abwesend_tage"] = flt(m["urlaubstage"] + m["kranktage"] + m["sonstige_tage"], 2)
	if not sichtbar:
		# wie uebersicht(): ohne Recht auf die Art keine Einzelwerte (Kranktage eines Kollegen)
		m["urlaubstage"] = m["kranktage"] = m["sonstige_tage"] = None
	return m
