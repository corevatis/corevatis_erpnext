"""Vorgangsübersicht „Vorgänge“ und Startseite „Meine Baustellen heute“.

- ``vorgaenge(filters)``: offene Vorgänge (je Lead eine Zeile) mit aktuellem und nächstem Schritt,
  Verantwortlichem, Alter und Ampel. Quelle der Script Report „Vorgangsliste“
  (``corevatis_erpnext/corevatis_erpnext/report/vorgangsliste``), die Kennzahlen und der Block im
  Arbeitsbereich „Vorgänge“. Die Leads kommen über ``frappe.get_list`` (Rechte des Benutzers),
  der Stand je Vorgang aus ``prozess.kette`` (Namen nur lesbarer Datensätze).
- ``meine_baustellen()``: Arbeitspakete (Task) und Aufgaben (ToDo) des angemeldeten Benutzers für
  heute - Startseite der Monteure im Desk (die App bleibt ihr Hauptwerkzeug).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, date_diff, getdate, today

from corevatis_erpnext.prozess import kette
from corevatis_erpnext.prozess.einstellungen import einstellungen

AMPEL_REIHENFOLGE = {"rot": 0, "gelb": 1, "grün": 2, "grau": 3}
AMPEL_WERTE = ("rot", "gelb", "grün", "grau")
ROLLEN_UEBERSICHT = ("Büro", "Meister", "System Manager")
CACHE_SEKUNDEN = 60


def darf_uebersicht() -> bool:
	return frappe.session.user == "Administrator" or bool(set(ROLLEN_UEBERSICHT) & set(frappe.get_roles()))


def _filter(filters) -> frappe._dict:
	if isinstance(filters, str):
		filters = frappe.parse_json(filters) if filters else {}
	return frappe._dict(filters or {})


def vorgaenge(filters=None) -> list[dict]:
	"""Zeilen der Vorgangsübersicht, sortiert nach Ampel (rot zuerst) und Alter."""
	f = _filter(filters)
	kette.neuer_aufruf()
	if not frappe.has_permission("Lead", "read"):
		return []
	einst = einstellungen()
	nur_offene = cint(f.get("nur_offene", 1))
	lead_filter = {}
	if nur_offene and kette.spalte_da("Lead", "cv_status"):
		lead_filter["cv_status"] = ["!=", kette.STATUS_ANFRAGE_VERLOREN]
	leads = frappe.get_list(
		"Lead",
		filters=lead_filter,
		fields=["name", "lead_name", "company_name", "cv_objekt_adresse", "creation"],
		order_by="creation desc",
		limit_page_length=einst.uebersicht_max_vorgaenge,
	)
	zeilen = []
	for lead in leads:
		k = kette.sammeln(lead.name, "Lead", lead.name)
		stand = kette.auswerten(k, einst, mit_aktionen=True)
		if nur_offene and stand["vorgang"]["status"] != "offen":
			continue
		zeile = _zeile(lead, k, stand)
		if f.get("schritt") and zeile["schritt"] != f.schritt:
			continue
		if f.get("verantwortlich") and zeile["verantwortlich"] != f.verantwortlich:
			continue
		if f.get("ampel") and zeile["ampel"] != f.ampel:
			continue
		zeilen.append(zeile)
	zeilen.sort(key=lambda z: (AMPEL_REIHENFOLGE.get(z["ampel"], 9), -z["alter_tage"]))
	return zeilen


def _zeile(lead, k, stand) -> dict:
	aktuell = stand["aktuell"]
	schritt = next((s for s in stand["schritte"] if s["key"] == aktuell), None)
	beleg = (schritt["belege"][-1] if schritt and schritt["belege"] else None) or {}
	aktion = stand["naechste_aktion"] or {}
	objekt = next((o for o in k.objekte if kette.lesbar(kette.OBJEKT, o.name)), None)
	adresse = (objekt.get("titel") if objekt else None) or (lead.cv_objekt_adresse or "").replace("\n", ", ")
	if schritt:
		naechster = aktion.get("label") or schritt["text"]
	else:
		naechster = _("abgeschlossen")
	return {
		"vorgang": lead.name,
		"kunde": lead.lead_name or lead.company_name or lead.name,
		"objekt_adresse": adresse,
		"schritt": schritt["label"] if schritt else _("abgeschlossen"),
		"schritt_key": aktuell or "",
		"naechster_schritt": naechster,
		"beleg_typ": beleg.get("doctype") or "",
		"beleg": beleg.get("name") or "",
		"verantwortlich": kette.verantwortlich(k, aktuell),
		"alter_tage": max(date_diff(today(), getdate(lead.creation)), 0),
		"ampel": stand["ampel"]["farbe"],
		"ampel_text": stand["ampel"]["text"],
	}


def _zwischengespeichert() -> list[dict]:
	"""Übersicht ohne Filter, 60 s je Benutzer zwischengespeichert (Kennzahlen und Block laden sie
	beim Öffnen des Arbeitsbereichs dreimal); der Bericht rechnet immer frisch."""
	schluessel = f"cv_prozess_vorgaenge|{frappe.session.user}"
	zeilen = frappe.cache.get_value(schluessel)
	if zeilen is None:
		zeilen = vorgaenge()
		frappe.cache.set_value(schluessel, zeilen, expires_in_sec=CACHE_SEKUNDEN)
	return zeilen


# --- Arbeitsbereich „Vorgänge“: Kennzahlen (Number Cards vom Typ Custom) und Block ----------


@frappe.whitelist()
def karte_offen(filters=None) -> dict:
	zeilen = _zwischengespeichert() if darf_uebersicht() else []
	return {"value": len(zeilen), "fieldtype": "Int", "route": ["query-report", "Vorgangsliste"]}


@frappe.whitelist()
def karte_ueberfaellig(filters=None) -> dict:
	zeilen = _zwischengespeichert() if darf_uebersicht() else []
	return {
		"value": sum(1 for z in zeilen if z["ampel"] == "rot"),
		"fieldtype": "Int",
		"route": ["query-report", "Vorgangsliste"],
		"route_options": {"ampel": "rot"},
	}


@frappe.whitelist()
def karte_am_zug(filters=None) -> dict:
	zeilen = _zwischengespeichert() if darf_uebersicht() else []
	return {
		"value": sum(1 for z in zeilen if z["ampel"] == "gelb"),
		"fieldtype": "Int",
		"route": ["query-report", "Vorgangsliste"],
		"route_options": {"ampel": "gelb"},
	}


@frappe.whitelist()
def block_vorgaenge(limit: int | str = 12) -> dict:
	"""Daten für den Block „Vorgänge“ oben im Arbeitsbereich: dringendste Vorgänge und Zähler je Schritt."""
	if not darf_uebersicht():
		return {"zeilen": [], "schritte": [], "gesamt": 0}
	zeilen = _zwischengespeichert()
	je_schritt = {key: 0 for key in kette.SCHRITT_KEYS}
	for zeile in zeilen:
		if zeile["schritt_key"] in je_schritt:
			je_schritt[zeile["schritt_key"]] += 1
	return {
		"zeilen": zeilen[: max(cint(limit), 1)],
		"schritte": [
			{"key": key, "label": kette.SCHRITT_LABEL[key], "anzahl": je_schritt[key]}
			for key in kette.SCHRITT_KEYS
		],
		"gesamt": len(zeilen),
	}


# --- Startseite Monteur ----------------------------------------------------------------------


@frappe.whitelist()
def meine_baustellen() -> dict:
	"""Arbeitspakete und Aufgaben des Benutzers für heute (Rechte des Benutzers)."""
	kette.neuer_aufruf()
	user = frappe.session.user
	heute = today()
	tasks = []
	if frappe.has_permission("Task", "read"):
		kandidaten = frappe.get_list(
			"Task",
			filters={
				"status": ["not in", ["Completed", "Cancelled", "Template"]],
				"_assign": ["like", f'%"{user}"%'],
			},
			fields=["name", "subject", "project", "status", "exp_start_date", "exp_end_date", "progress"],
			order_by="exp_start_date asc",
			limit_page_length=200,
		)
		for task in kandidaten:
			# heute oder früher begonnen (auch überfällige); ohne Datum gilt es immer
			if task.exp_start_date and getdate(task.exp_start_date) > getdate(heute):
				continue
			tasks.append(task)
	projekte = {}
	for task in tasks:
		if task.project and task.project not in projekte:
			projekte[task.project] = (
				frappe.db.get_value("Project", task.project, "project_name")
				if kette.lesbar("Project", task.project)
				else ""
			)
		task["project_name"] = projekte.get(task.project) or ""
	todos = frappe.get_list(
		"ToDo",
		filters={"allocated_to": user, "status": "Open", "date": ["<=", heute]},
		fields=["name", "description", "reference_type", "reference_name", "date", "priority"],
		order_by="date asc",
		limit_page_length=50,
	)
	ohne_datum = frappe.get_list(
		"ToDo",
		filters={"allocated_to": user, "status": "Open", "date": ["is", "not set"]},
		fields=["name", "description", "reference_type", "reference_name", "date", "priority"],
		order_by="creation asc",
		limit_page_length=50,
	)
	for todo in todos + ohne_datum:
		todo["description"] = frappe.utils.strip_html(todo.description or "")[:200]
		if (
			todo.reference_type
			and todo.reference_name
			and not kette.lesbar(todo.reference_type, todo.reference_name)
		):
			todo["reference_type"] = todo["reference_name"] = None
	return {"datum": heute, "tasks": tasks, "todos": todos + ohne_datum}
