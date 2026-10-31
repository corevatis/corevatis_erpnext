"""Mitarbeiter (Phase 2 P2.5, Handwerker#88): Urlaub, Krank, Stundenkonto, Überstunden - ohne HRMS.

- **Einrichtung** (``ensure_setup``, aus ``after_install``/``after_migrate``): Felder am Employee
  ``cv_urlaubsanspruch`` (Tage je Kalenderjahr, 0/leer = 30), ``cv_urlaub_uebertrag`` (Resturlaub aus dem
  Vorjahr in Tagen), ``cv_saldo_uebertrag`` (Stundenkonto zum 1. Januar, Stunden mit Vorzeichen).
- **Rechnen**: ``stundenkonto`` (Soll aus Tagesarbeitszeit, Feiertagen und Abwesenheiten, Ist aus
  Timesheets, Urlaubskonto).
- **Desk**: Script Report „Stundenkonto“ (``report/stundenkonto``) mit Ansicht „Mitarbeiter“ (eine Zeile
  je Mitarbeiter) und „Tage“ (Monatsübersicht eines Mitarbeiters); Hinweis zum Resturlaub im Formular
  „Abwesenheit“ (``urlaub_hinweis``).
- **App**: Hook ``corevatis_stundenkonto_adapter`` → ``fuer_benutzer(user, jahr, monat)`` liefert dem
  Kern-Endpunkt ``corevatis.api.v1.mein_stundenkonto`` Monat, Saldo Jahr und Urlaubskonto des Benutzers.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, flt, getdate, today

from corevatis_erpnext.mitarbeiter import stundenkonto as sk
from corevatis_erpnext.planung.einstellungen import einstellungen
from corevatis_erpnext.zeiterfassung import employee_fuer_user

MODULE = "Corevatis ERPNext"


def custom_fields() -> dict[str, list[dict]]:
	return {
		"Employee": [
			{
				"fieldname": "cv_konto_section",
				"fieldtype": "Section Break",
				"label": "Urlaub und Stundenkonto",
				"insert_after": "cv_fahrzeuglager",
				"collapsible": 0,
				"module": MODULE,
			},
			{
				"fieldname": sk.FELD_ANSPRUCH,
				"fieldtype": "Float",
				"label": "Urlaubsanspruch (Tage je Jahr)",
				"insert_after": "cv_konto_section",
				"default": "30",
				"precision": "1",
				"module": MODULE,
				"description": "Jahresurlaub in Arbeitstagen; leer oder 0 = 30.",
			},
			{
				"fieldname": sk.FELD_UEBERTRAG,
				"fieldtype": "Float",
				"label": "Resturlaub Vorjahr (Tage)",
				"insert_after": sk.FELD_ANSPRUCH,
				"precision": "1",
				"module": MODULE,
				"description": "Übertrag ins laufende Jahr; das Büro pflegt ihn zum Jahreswechsel.",
			},
			{
				"fieldname": "cv_konto_column",
				"fieldtype": "Column Break",
				"insert_after": sk.FELD_UEBERTRAG,
				"module": MODULE,
			},
			{
				"fieldname": sk.FELD_SALDO,
				"fieldtype": "Float",
				"label": "Stundenkonto zum 1. Januar (Stunden)",
				"insert_after": "cv_konto_column",
				"precision": "2",
				"module": MODULE,
				"description": "Übertrag des Stundenkontos ins laufende Jahr (Überstunden positiv, "
				"Minusstunden negativ).",
			},
		]
	}


def ensure_setup() -> None:
	"""Felder am Employee anlegen (idempotent)."""
	create_custom_fields(custom_fields(), update=True)


# --- Desk ----------------------------------------------------------------------------------------


@frappe.whitelist()
def urlaub_hinweis(
	mitarbeiter: str,
	von=None,
	bis=None,
	ganztags=1,
	von_uhrzeit=None,
	bis_uhrzeit=None,
	name: str | None = None,
) -> dict:
	"""Resturlaub des Jahres (aus ``von``) vor und nach dem Eintrag im Formular „Abwesenheit“. Wer die
	Abwesenheit lesen darf (Büro, System Manager, der Mitarbeiter selbst), bekommt den Hinweis."""
	if not mitarbeiter:
		return {}
	user = frappe.session.user
	eigener = employee_fuer_user(user)
	if not (
		sk.arten_sichtbar(frappe.db.get_value("Employee", mitarbeiter, "user_id"), user)
		or (eigener and eigener["name"] == mitarbeiter)
	):
		frappe.throw(
			_("Kein Zugriff auf das Urlaubskonto von {0}.").format(mitarbeiter), frappe.PermissionError
		)
	jahr = getdate(von or today()).year
	e = einstellungen()
	konto = sk.urlaubskonto(mitarbeiter, jahr, e, ausser=name or None)
	tage = (
		sk.urlaubstage(mitarbeiter, von, bis or von, cint(ganztags), von_uhrzeit, bis_uhrzeit, e)
		if von
		else 0.0
	)
	konto["tage_eintrag"] = tage
	konto["rest_nachher"] = flt(konto["rest"] - tage, 2)
	return konto


@frappe.whitelist()
def monatsuebersicht(mitarbeiter: str, jahr: int, monat: int) -> dict:
	"""Tage eines Mitarbeiters im Monat (für Desk-Skripte; Rechte wie der Report)."""
	return sk.monatsuebersicht(mitarbeiter, cint(jahr), cint(monat))


# --- App (Hook corevatis_stundenkonto_adapter) -----------------------------------------------------


def fuer_benutzer(user: str, jahr: int, monat: int) -> dict | None:
	"""Stundenkonto des Benutzers für den Kern-Endpunkt ``mein_stundenkonto``; ``None`` ohne Employee.
	Nur der Benutzer selbst (oder Büro/System Manager im Namen des Benutzers)."""
	if user != frappe.session.user and not sk.darf_alle():
		frappe.throw(_("Sie sehen nur Ihr eigenes Stundenkonto."), frappe.PermissionError)
	eigener = employee_fuer_user(user)
	if not eigener:
		return None
	e = einstellungen()
	heute = getdate(today())
	m = sk.monat(eigener["name"], cint(jahr), cint(monat), e, arten=True, heute=heute)
	werte = (
		frappe.db.get_value(
			"Employee", eigener["name"], [sk.FELD_ANSPRUCH, sk.FELD_UEBERTRAG, sk.FELD_SALDO], as_dict=True
		)
		or {}
	)
	u = sk.urlaubskonto(eigener["name"], cint(jahr), e, heute=heute, werte=werte)
	return {
		"mitarbeiter": eigener["name"],
		"mitarbeiter_name": eigener.get("employee_name"),
		"jahr": m["jahr"],
		"monat": m["monat"],
		"von": m["von"],
		"bis": m["bis"],
		"tagesarbeitszeit": m["tagesarbeitszeit"],
		"solltage": m["solltage"],
		"soll": m["soll"],
		"ist": m["ist"],
		"saldo": m["saldo"],
		"ueberstunden": m["ueberstunden"],
		"minusstunden": m["minusstunden"],
		"saldo_jahr": sk.saldo_jahr(
			eigener["name"], cint(jahr), cint(monat), flt(werte.get(sk.FELD_SALDO)), e, heute
		),
		"urlaub": {k: u[k] for k in ("anspruch", "uebertrag", "genommen", "geplant", "rest")},
		"kranktage_jahr": u["kranktage"],
		"tage": [
			{
				k: z[k]
				for k in (
					"datum",
					"wochentag",
					"soll",
					"ist",
					"saldo",
					"abwesenheit",
					"feiertag",
					"wochenende",
					"zaehlt",
				)
			}
			for z in m["tage"]
		],
	}
