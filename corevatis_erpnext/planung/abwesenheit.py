"""Abwesenheiten (Doctype ``Abwesenheit``) als Belegung: Terminplanung D14, Wochenplan 5.2 und
Plantafel werten Urlaub, Krankheit, Schulung und Sonstiges als „nicht verfügbar“.

``hrms`` ist nicht installiert (``frappe.get_installed_apps()``); der eigene Doctype ist bewusst klein:
Mitarbeiter, von, bis, Art, ganztags oder Uhrzeiten, Notiz. Rechte: Büro schreibt; der Meister liest alle
Abwesenheiten des Betriebs, aber Art und Notiz nur die eigenen (Feldrecht Stufe 1, Mike 01.11.: „Ja soll er“);
der Monteur liest nur die eigenen (``permission_query_conditions``/``has_permission``).
"""

from __future__ import annotations

import datetime as dt
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, getdate, to_timedelta

from corevatis_erpnext.planung.slotrechner import ART_ABWESENHEIT, EIN_TAG, UNBEKANNT, Belegung
from corevatis_erpnext.zeiterfassung import employee_fuer_user

DOCTYPE = "Abwesenheit"
ROLLEN_ALLE = ("Büro", "System Manager")
# sehen alle Abwesenheiten ihrer Firma, aber ohne Art und Notiz (Feldrecht Stufe 1 im Doctype)
ROLLEN_TEAM = ("Meister",)
# Felder auf Feldrechtstufe 1: Frappe streicht sie aus der Ausgabe, prüft Filter aber nur auf Existenz
GESCHUETZTE_FELDER = ("art", "notiz")
_MUSTER_GESCHUETZT = re.compile(r"\b(" + "|".join(GESCHUETZTE_FELDER) + r")\b")
# Parameter einer Listenanfrage, in denen Feldnamen stehen (reportview.get/get_count/export_query,
# frappe.client.get_list/get_count, get_group_by_count, get_sidebar_stats)
_ANFRAGE_PARAMETER = (
	"filters",
	"or_filters",
	"current_filters",
	"field",
	"aggregate_on_field",
	"order_by",
	"group_by",
	"stats",
)


def hrms_installiert() -> bool:
	return "hrms" in frappe.get_installed_apps()


def _felder() -> list[str]:
	return [
		"name",
		"mitarbeiter",
		"mitarbeiter_name",
		"benutzer",
		"art",
		"von",
		"bis",
		"ganztags",
		"von_uhrzeit",
		"bis_uhrzeit",
		"notiz",
	]


def abwesenheiten(
	von, bis, users: list[str] | None = None, employees: list[str] | None = None
) -> list[frappe._dict]:
	"""Abwesenheiten im Zeitraum (Tage ``von``-``bis`` einschließlich), optional je Benutzer/Mitarbeiter."""
	if not frappe.db.exists("DocType", DOCTYPE):
		return []
	filters: dict = {"von": ["<=", getdate(bis)], "bis": [">=", getdate(von)]}
	if users is not None:
		if not users:
			return []
		filters["benutzer"] = ["in", users]
	if employees is not None:
		if not employees:
			return []
		filters["mitarbeiter"] = ["in", employees]
	return frappe.get_all(DOCTYPE, filters=filters, fields=_felder(), order_by="von asc, name asc")


def zeitraum(a) -> tuple[dt.datetime, dt.datetime]:
	"""Beginn und Ende einer Abwesenheit als Zeitpunkte (ganztags: 00:00 bis 00:00 des Folgetags)."""
	von = dt.datetime.combine(getdate(a.von), dt.time.min)
	bis = dt.datetime.combine(getdate(a.bis), dt.time.min) + EIN_TAG
	if not cint(a.ganztags) and a.von_uhrzeit and a.bis_uhrzeit:
		von = dt.datetime.combine(getdate(a.von), dt.time.min) + to_timedelta(a.von_uhrzeit)
		bis = dt.datetime.combine(getdate(a.von), dt.time.min) + to_timedelta(a.bis_uhrzeit)
	return von, max(bis, von + dt.timedelta(minutes=1))


def art_anzeige(art: str, user: str | None) -> str:
	"""Art der Abwesenheit für die Anzeige: Büro/System Manager und der Betroffene sehen „Urlaub“, „Krank“ …;
	alle anderen nur „Abwesend“ (Security-Review: Krankheit ist ein Gesundheitsdatum, der Doctype gibt
	Meistern und Monteuren nur die eigenen Abwesenheiten frei)."""
	if darf_alle() or (user and user == frappe.session.user):
		return _(art)
	return _("Abwesend")


def art_sichtbar(user: str | None) -> bool:
	return darf_alle() or bool(user and user == frappe.session.user)


def belegungen(user: str, von, bis) -> list[Belegung]:
	"""Abwesenheiten des Benutzers als Belegungen (ohne Ort - von einer Abwesenheit fährt niemand an).
	``titel`` ist die Art, wenn der Aufrufer sie sehen darf (``art_anzeige``), sonst leer."""
	if not user:
		return []
	ergebnis = []
	sichtbar = art_sichtbar(user)
	for a in abwesenheiten(von, bis, users=[user]):
		anfang, ende = zeitraum(a)
		ergebnis.append(
			Belegung(anfang, ende, UNBEKANNT, ART_ABWESENHEIT, _(a.art) if sichtbar else "", DOCTYPE, a.name)
		)
	return ergebnis


def tage_text(a) -> str:
	"""„Di 04.11.“ oder „Di 04.11. - Fr 07.11.“ bzw. mit Uhrzeiten „Di 04.11. 12:00-15:00“."""
	from corevatis_erpnext.planung.terminplanung import tag_text

	von, bis = getdate(a.von), getdate(a.bis)
	if not cint(a.ganztags) and a.von_uhrzeit and a.bis_uhrzeit:
		return f"{tag_text(von)} {str(a.von_uhrzeit)[:5]}\u2013{str(a.bis_uhrzeit)[:5]}"
	if von == bis:
		return tag_text(von)
	return f"{tag_text(von)} \u2013 {tag_text(bis)}"


def hinweise(user: str, von, bis, name: str | None = None) -> list[str]:
	"""Hinweise „<Name> ist Di 04.11. abwesend (Urlaub).“ für den Zeitraum."""
	name = name or frappe.utils.get_fullname(user) or user
	sichtbar = art_sichtbar(user)
	return [
		_("{0} ist {1} abwesend ({2}).").format(name, tage_text(a), _(a.art))
		if sichtbar
		else _("{0} ist {1} abwesend.").format(name, tage_text(a))
		for a in abwesenheiten(von, bis, users=[user])
	]


# --- Rechte (hooks.permission_query_conditions / has_permission) -------------------------------


def darf_alle(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(set(ROLLEN_ALLE) & set(frappe.get_roles(user)))


def sieht_team(user: str | None = None) -> bool:
	"""Büro und System Manager sehen alle Abwesenheiten, der Meister die seiner Firma (``firma_des_benutzers``).
	Art und Notiz liegen auf Feldrechtstufe 1 (nur Büro und System Manager); der Meister sieht fremde Einträge
	als „Abwesend“. Der Link ``mitarbeiter`` ignoriert Benutzerberechtigungen, sonst filtert ERPNexts
	automatische User Permission auf den eigenen Employee die Liste wieder auf die eigene Zeile."""
	user = user or frappe.session.user
	return darf_alle(user) or bool(set(ROLLEN_TEAM) & set(frappe.get_roles(user)))


def firma_des_benutzers(user: str) -> str | None:
	"""Firma des aktiven Employee des Benutzers - der Rahmen, in dem ein Meister Abwesenheiten sieht."""
	eigener = employee_fuer_user(user)
	return (eigener or {}).get("company") or None


def _feldnamen(filters) -> set[str]:
	"""Feldnamen aus Frappe-Filtern: Dict, Liste von ``[feld, op, wert]`` oder ``[doctype, feld, op, wert]``, JSON."""
	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except ValueError:
			return set(_MUSTER_GESCHUETZT.findall(filters.lower()))
	namen: set[str] = set()
	if isinstance(filters, dict):
		namen.update(str(k) for k in filters)
	elif isinstance(filters, (list, tuple)):
		for bedingung in filters:
			if isinstance(bedingung, dict):
				namen.update(str(k) for k in bedingung)
			elif isinstance(bedingung, (list, tuple)) and bedingung:
				namen.add(str(bedingung[1] if len(bedingung) == 4 else bedingung[0]))
	return {n.rsplit(".", 1)[-1].strip("`").lower() for n in namen}


def _feldnamen_in_text(wert) -> set[str]:
	"""Geschützte Feldnamen in Sortierung, Gruppierung oder Feldlisten (String, Liste, Dict, JSON)."""
	if wert is None:
		return set()
	if isinstance(wert, str):
		try:
			wert = json.loads(wert)
		except ValueError:
			return set(_MUSTER_GESCHUETZT.findall(wert.lower()))
	if isinstance(wert, dict):
		texte = [str(v) for v in wert.values()]
	elif isinstance(wert, (list, tuple)):
		texte = [str(v) for e in wert for v in (e.values() if isinstance(e, dict) else [e])]
	else:
		texte = [str(wert)]
	return {t for text in texte for t in _MUSTER_GESCHUETZT.findall(text.lower())}


def geschuetzte_felder_pruefen(user: str) -> None:
	"""Wer Art und Notiz nicht lesen darf, darf auch nicht danach filtern, sortieren oder gruppieren.

	Frappe entfernt Felder der Stufe 1 nur aus der Ausgabe (``apply_fieldlevel_read_permissions``); Filter
	prüft ``reportview.validate_filters`` nur auf Existenz. Ein Meister könnte sonst mit ``art = Krank``
	erfragen, wer krank ist. Geprüft wird die laufende Web-Anfrage (``frappe.local.form_dict``); eigene
	Serverfunktionen lesen Abwesenheiten mit ``frappe.get_all`` und laufen hier nicht durch."""
	form = getattr(frappe.local, "form_dict", None)
	if not form or darf_alle(user):
		return
	gefunden: set[str] = set()
	for schluessel in _ANFRAGE_PARAMETER:
		wert = form.get(schluessel)
		if wert is None or wert == "":
			continue
		if schluessel in ("filters", "or_filters", "current_filters"):
			gefunden |= _feldnamen(wert) & set(GESCHUETZTE_FELDER)
		else:
			gefunden |= _feldnamen_in_text(wert)
	if gefunden:
		frappe.throw(
			_(
				"Nach Art oder Notiz der Abwesenheit dürfen nur Büro und System Manager filtern oder sortieren."
			),
			frappe.PermissionError,
		)


def permission_query_conditions(user: str | None = None) -> str:
	"""Liste/Report: Büro alle, Meister die Mitarbeiter seiner Firma, Monteure nur die eigenen Abwesenheiten."""
	user = user or frappe.session.user
	if darf_alle(user):
		return ""
	geschuetzte_felder_pruefen(user)
	if sieht_team(user):
		firma = firma_des_benutzers(user)
		if firma:
			return (
				"`tabAbwesenheit`.`mitarbeiter` in (select `name` from `tabEmployee` "
				f"where `company` = {frappe.db.escape(firma)})"
			)
	return f"`tabAbwesenheit`.`benutzer` = {frappe.db.escape(user)}"


def has_permission(doc, ptype: str = "read", user: str | None = None) -> bool:
	"""Formular/API: Meister lesen die Abwesenheiten der Mitarbeiter ihrer Firma (ohne Art), Monteure nur die
	eigene; schreiben darf laut Doctype nur Büro/System Manager."""
	user = user or frappe.session.user
	if darf_alle(user):
		return True
	if ptype == "read" and sieht_team(user) and doc.get("mitarbeiter"):
		firma = firma_des_benutzers(user)
		if firma and frappe.db.get_value("Employee", doc.get("mitarbeiter"), "company") == firma:
			return True
	return bool(doc.get("benutzer")) and doc.get("benutzer") == user
