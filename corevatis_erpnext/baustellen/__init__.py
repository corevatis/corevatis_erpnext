"""Büro-Dashboard „Baustellen“ (Handwerker#43, Aufgabe 5.9): je laufendem Projekt eine Zeile mit Soll/Ist
Stunden, Material, Fortschritt, nächstem Termin, offenen Meldungen/Mängeln und Ampel.

Laufend = Projekt im Status „Open“ mit eingereichtem Auftrag (Angebot angenommen, 4.4). Datenquellen, keine
eigene Rechnung von Soll und Ist:

- **Soll-/Ist-Stunden, Material Soll/Ist** aus der Nachkalkulation (P2.1, ``nachkalkulation.projekt_auswerten``):
  Soll aus der Kalkulation des Auftrags, Ist aus den Timesheets (Arbeits- und Fahrzeit, Entwurf und
  eingereicht), Material Ist aus den Materialentnahmen (3.11/P2.2).
- **Ist im Zeitraum** (Filter von/bis, im Bericht standardmäßig die laufende Woche) über
  ``nachkalkulation.ist_stunden`` (Pause zählt nicht).
- **Fortschritt** aus den Arbeitspaketen (5.1, ``Task``): erledigte Pakete gewichtet nach Sollstunden
  (``cv_sollstunden``, sonst ``expected_time``), ein offenes Paket mit ``progress`` anteilig; ohne
  Sollstunden zählt die Anzahl. Mangel-Aufgaben aus der Abnahme (6.1, ``cv_abnahmeprotokoll``) zählen nicht.
- **Abweichung** = Ist ÷ erwartete Stunden - 1, erwartet = Soll x Fortschritt (Fortschritt 0, 100 % oder
  unbekannt: volles Soll, wie die Nachkalkulation).
- **Ampel**: grün bis 5 % über den erwarteten Stunden, gelb bis 15 %, darüber rot; rot auch bei einem
  überfälligen Termin (offenes Arbeitspaket oder Mangelfrist mit Ende vor heute, geplantes Projektende vor
  heute). Ist ohne Soll gelb.
- **Nächster Termin**: frühester künftiger Beginn bzw. Ende eines offenen Arbeitspakets, Mangelfrist, Termin
  (Event mit dem Projekt als Teilnehmer) oder geplantes Projektende.
- **Meldungen/Mängel offen**: Baustellenmeldungen (3.12, Kern-Doctype per Name) im Status „Offen“ und
  offene Mangel-Aufgaben; eine in die Abnahme übernommene Meldung mit Mangel-Aufgabe zählt einmal.
- **Meister**: Benutzer mit Rolle Meister unter den Zugewiesenen der Arbeitspakete (5.2), sonst der Meister
  des Besichtigungstermins (1.3).

Rechte wie die Nachkalkulation: Büro, Meister, System Manager (``planung.auftrag.darf_planen``), Projekte nur
mit Leserecht (``frappe.get_list``). Kennzahlen (Number Cards vom Typ Custom) im Arbeitsbereich
„Baustellen“; angelegt von ``ensure_setup`` bei ``after_install``/``after_migrate``.
"""

from __future__ import annotations

import datetime as dt

import frappe
from frappe import _
from frappe.utils import cint, flt, format_date, get_datetime, get_fullname, getdate, now_datetime, today

from corevatis_erpnext import nachkalkulation as nk
from corevatis_erpnext.planung.auftrag import darf_planen
from corevatis_erpnext.planung.projekt import zuweisungen
from corevatis_erpnext.prozess import kette

GRUEN, GELB, ROT = nk.GRUEN, nk.GELB, nk.ROT
AMPEL_REIHENFOLGE = {ROT: 0, GELB: 1, GRUEN: 2}
# Abweichung in Prozent über den erwarteten Stunden
GELB_UEBER = 5.0
ROT_UEBER = 15.0
STATUS_OFFEN = "offen"
STATUS_IN_ARBEIT = "in Arbeit"
FILTER_ROT = "rot"
FILTER_ROT_GELB = "rot oder gelb"
PROJEKTE_MAX = 100
TASK_ERLEDIGT = "Completed"
TASK_AUS = ("Cancelled", "Template")
MELDUNG = "Baustellenmeldung"
MELDUNG_OFFEN = "Offen"
ABNAHME_MANGEL = "Abnahme Mangel"
REPORT = "Baustellen"
CACHE_SEKUNDEN = 60


def pruefe_rolle() -> None:
	if not darf_planen():
		frappe.throw(
			_("Das Dashboard „Baustellen“ sehen nur die Rollen Büro und Meister."), frappe.PermissionError
		)


def _hat(doctype: str, feld: str) -> bool:
	return kette.spalte_da(doctype, feld)


# --- reine Regeln -------------------------------------------------------------------------------------


def _soll_task(t) -> float:
	return flt(t.get("cv_sollstunden") or t.get("expected_time"))


def fortschritt(tasks: list) -> float | None:
	"""Fortschritt in Prozent: erledigte Arbeitspakete gewichtet nach Sollstunden (offene mit ``progress``
	anteilig), ohne Sollstunden nach Anzahl; ``None`` ohne Arbeitspakete."""
	if not tasks:
		return None
	anteile = []
	for t in tasks:
		a = 1.0 if t.get("status") == TASK_ERLEDIGT else min(max(flt(t.get("progress")) / 100, 0.0), 1.0)
		anteile.append((_soll_task(t), a))
	gewicht = sum(g for g, _a in anteile)
	if gewicht > 0:
		return flt(sum(g * a for g, a in anteile) / gewicht * 100, 1)
	return flt(sum(a for _g, a in anteile) / len(anteile) * 100, 1)


def abweichung(soll_h: float, ist_h: float, fortschritt_prozent: float | None) -> float | None:
	"""Ist über (+) bzw. unter (-) den erwarteten Stunden in Prozent; ``None`` ohne Soll."""
	if soll_h <= 0:
		return None
	f = flt(fortschritt_prozent)
	erwartet = soll_h * (f / 100 if 0 < f < 100 else 1.0)
	return flt((ist_h / erwartet - 1) * 100, 1)


def ampel(soll_h: float, ist_h: float, fortschritt_prozent: float | None, ueberfaellig: bool) -> str:
	if ueberfaellig:
		return ROT
	abw = abweichung(soll_h, ist_h, fortschritt_prozent)
	if abw is None:
		return GELB if ist_h > 0 else GRUEN
	if abw <= GELB_UEBER:
		return GRUEN
	if abw <= ROT_UEBER:
		return GELB
	return ROT


# --- Daten je Projekt (gesammelt) -----------------------------------------------------------------------


def _laufende_projekte() -> list[frappe._dict]:
	felder = ["name", "project_name", "customer", "status", "sales_order", "expected_end_date"]
	felder += [x for x in ("cv_objekt", "cv_lead") if _hat("Project", x)]
	projekte = frappe.get_list(
		"Project",
		filters={"status": "Open", "sales_order": ["is", "set"]},
		fields=felder,
		order_by="modified desc",
		limit_page_length=PROJEKTE_MAX,
	)
	auftraege = {p.sales_order for p in projekte}
	eingereicht = set(
		frappe.get_all("Sales Order", filters={"name": ["in", list(auftraege)], "docstatus": 1}, pluck="name")
		if auftraege
		else []
	)
	return [p for p in projekte if p.sales_order in eingereicht]


def _tasks(projekte: list[str]) -> dict[str, list[frappe._dict]]:
	felder = [
		"name",
		"project",
		"subject",
		"status",
		"progress",
		"expected_time",
		"exp_start_date",
		"exp_end_date",
	]
	felder += [x for x in ("cv_sollstunden", "cv_abnahmeprotokoll") if _hat("Task", x)]
	je: dict[str, list[frappe._dict]] = {p: [] for p in projekte}
	for t in frappe.get_all(
		"Task",
		filters={"project": ["in", projekte], "status": ["not in", list(TASK_AUS)]},
		fields=felder,
		order_by="exp_start_date asc, creation asc",
	):
		je[t.project].append(t)
	return je


def _ist_mangel(t) -> bool:
	return bool(t.get("cv_abnahmeprotokoll"))


def _meldungen(projekte: list[str], mangel_tasks: set[str]) -> dict[str, int]:
	"""Offene Baustellenmeldungen je Projekt; übernommene Meldungen mit Mangel-Aufgabe zählen dort."""
	if not kette.doctype_da(MELDUNG):
		return {}
	offen = frappe.get_all(
		MELDUNG,
		filters={"projekt": ["in", projekte], "status": MELDUNG_OFFEN},
		fields=["name", "projekt"],
	)
	uebernommen: set[str] = set()
	if offen and mangel_tasks and _hat(ABNAHME_MANGEL, "meldung"):
		uebernommen = set(
			frappe.get_all(
				ABNAHME_MANGEL,
				filters={"meldung": ["in", [m.name for m in offen]], "task": ["in", list(mangel_tasks)]},
				pluck="meldung",
			)
		)
	ergebnis: dict[str, int] = {}
	for m in offen:
		if m.name not in uebernommen:
			ergebnis[m.projekt] = ergebnis.get(m.projekt, 0) + 1
	return ergebnis


def _termine(projekte: list[str]) -> dict[str, list[frappe._dict]]:
	"""Künftige offene Termine (Event mit dem Projekt als Teilnehmer). Den Betreff gibt es nur für Events,
	die der Benutzer lesen darf (private Events anderer Benutzer erscheinen als „Termin“ ohne Betreff)."""
	teilnehmer = frappe.get_all(
		"Event Participants",
		filters={
			"parenttype": "Event",
			"reference_doctype": "Project",
			"reference_docname": ["in", projekte],
		},
		fields=["parent", "reference_docname"],
	)
	if not teilnehmer:
		return {}
	events = {}
	for e in frappe.get_all(
		"Event",
		filters={
			"name": ["in", list({t.parent for t in teilnehmer})],
			"status": "Open",
			"starts_on": [">=", now_datetime()],
		},
		fields=["name", "starts_on"],
	):
		e.subject = frappe.db.get_value("Event", e.name, "subject") if kette.lesbar("Event", e.name) else None
		events[e.name] = e
	je: dict[str, list[frappe._dict]] = {}
	for t in teilnehmer:
		if t.parent in events:
			je.setdefault(t.reference_docname, []).append(events[t.parent])
	return je


def _meister(projekte: list[frappe._dict], tasks: dict[str, list[frappe._dict]]) -> dict[str, str]:
	"""Meister je Projekt: zugewiesener Benutzer mit Rolle Meister, sonst Meister der Besichtigung."""
	meister_rolle = set(
		frappe.get_all("Has Role", filters={"role": "Meister", "parenttype": "User"}, pluck="parent")
	)
	alle = [t.name for liste in tasks.values() for t in liste if not _ist_mangel(t)]
	je_task = zuweisungen(alle)
	ergebnis: dict[str, str] = {}
	for p in projekte:
		for t in tasks.get(p.name, []):
			user = next((z["user"] for z in je_task.get(t.name, []) if z["user"] in meister_rolle), None)
			if user:
				ergebnis[p.name] = user
				break
		if p.name in ergebnis or not p.get("cv_lead") or not _hat("Lead", "cv_termin"):
			continue
		lead = frappe.db.get_value("Lead", p.cv_lead, ["name", "cv_termin"], as_dict=True)
		termin = kette._termin(lead) if lead else None
		user = kette._meister_des_termins(termin.name) if termin else None
		if user:
			ergebnis[p.name] = user
	return ergebnis


def _datum(wert) -> dt.date | None:
	return getdate(wert) if wert else None


def _ueberfaellig(p: frappe._dict, tasks: list[frappe._dict], heute: dt.date) -> str | None:
	for t in tasks:
		ende = _datum(t.exp_end_date)
		if t.status == TASK_ERLEDIGT or not ende or ende >= heute:
			continue
		if _ist_mangel(t):
			return _("Mangelfrist {0} überschritten ({1})").format(format_date(ende), t.subject)
		return _("Arbeitspaket {0} seit {1} überfällig").format(t.subject, format_date(ende))
	ende = _datum(p.expected_end_date)
	if ende and ende < heute:
		return _("Geplantes Ende {0} überschritten").format(format_date(ende))
	return None


def _naechster_termin(
	p: frappe._dict, tasks: list[frappe._dict], termine: list[frappe._dict], heute: dt.date
) -> tuple[dt.date | None, str]:
	kandidaten: list[tuple[dt.datetime, str]] = []
	for t in tasks:
		if t.status == TASK_ERLEDIGT:
			continue
		if _ist_mangel(t):
			if _datum(t.exp_end_date) and _datum(t.exp_end_date) >= heute:
				kandidaten.append((get_datetime(t.exp_end_date), _("Mangelfrist: {0}").format(t.subject)))
			continue
		if _datum(t.exp_start_date) and _datum(t.exp_start_date) >= heute:
			kandidaten.append((get_datetime(t.exp_start_date), _("Beginn: {0}").format(t.subject)))
		elif _datum(t.exp_end_date) and _datum(t.exp_end_date) >= heute:
			kandidaten.append((get_datetime(t.exp_end_date), _("Ende: {0}").format(t.subject)))
	for e in termine:
		text = _("Termin: {0}").format(e.subject) if e.subject else _("Termin")
		kandidaten.append((get_datetime(e.starts_on), text))
	if _datum(p.expected_end_date) and _datum(p.expected_end_date) >= heute:
		kandidaten.append((get_datetime(p.expected_end_date), _("Geplantes Ende")))
	if not kandidaten:
		return None, ""
	zeit, text = min(kandidaten, key=lambda k: k[0])
	return zeit.date(), text


def _ist_im_zeitraum(projekt: str, von, bis) -> float:
	return flt(
		sum(flt(z.stunden) for z in nk.ist_stunden(projekt, von, bis) if z.activity_type != nk.PAUSE), 2
	)


def _kunde_objekt(projekte: list[frappe._dict]) -> dict[str, str]:
	kunden = {p.customer for p in projekte if p.customer}
	namen = (
		dict(
			frappe.get_all(
				"Customer",
				filters={"name": ["in", list(kunden)]},
				fields=["name", "customer_name"],
				as_list=True,
			)
		)
		if kunden
		else {}
	)
	objekte: dict[str, str] = {}
	codes = {p.get("cv_objekt") for p in projekte if p.get("cv_objekt")}
	if codes and kette.spalte_da("Objekt", "titel"):
		objekte = dict(
			frappe.get_all(
				"Objekt", filters={"name": ["in", list(codes)]}, fields=["name", "titel"], as_list=True
			)
		)
	ergebnis = {}
	for p in projekte:
		teile = [namen.get(p.customer) or p.customer or "", objekte.get(p.get("cv_objekt")) or ""]
		ergebnis[p.name] = ", ".join(t for t in teile if t)
	return ergebnis


# --- Übersicht ------------------------------------------------------------------------------------------


def _filter(filters) -> frappe._dict:
	if isinstance(filters, str):
		filters = frappe.parse_json(filters) if filters else {}
	return frappe._dict(filters or {})


def uebersicht(filters=None) -> dict:
	"""Zeilen des Berichts „Baustellen“ (je laufendem Projekt eine), sortiert rot, gelb, grün und nach
	nächstem Termin."""
	pruefe_rolle()
	kette.neuer_aufruf()
	f = _filter(filters)
	heute = getdate(today())
	von, bis = f.get("von") or None, f.get("bis") or None
	mit_zeitraum = bool(von or bis)
	projekte = _laufende_projekte()
	namen = [p.name for p in projekte]
	if not namen:
		return {"zeilen": [], "hinweise": [], "zeitraum": mit_zeitraum}
	tasks = _tasks(namen)
	mangel_tasks = {t.name for liste in tasks.values() for t in liste if _ist_mangel(t)}
	meldungen = _meldungen(namen, mangel_tasks)
	termine = _termine(namen)
	meister = _meister(projekte, tasks)
	kunde_objekt = _kunde_objekt(projekte)
	waehrung = None
	zeilen = []
	hinweise: list[str] = []
	for p in projekte:
		a = nk.projekt_auswerten(p.name)
		s = a["summe"]
		waehrung = waehrung or s.get("currency")
		pakete = [t for t in tasks[p.name] if not _ist_mangel(t)]
		maengel = sum(1 for t in tasks[p.name] if _ist_mangel(t) and t.status != TASK_ERLEDIGT)
		fp = fortschritt(pakete)
		ueberfaellig = _ueberfaellig(p, tasks[p.name], heute)
		farbe = ampel(s["soll_h"], s["ist_h"], fp, bool(ueberfaellig))
		in_arbeit = (
			s["ist_h"] > 0
			or s["ist_material"] > 0
			or flt(fp) > 0
			or any(t.status == "Working" for t in pakete)
		)
		status = STATUS_IN_ARBEIT if in_arbeit else STATUS_OFFEN
		user = meister.get(p.name)
		termin, termin_text = _naechster_termin(p, tasks[p.name], termine.get(p.name, []), heute)
		grund = ueberfaellig or (_("Ist ohne Sollstunden") if not s["soll_h"] and s["ist_h"] else "")
		zeile = {
			"projekt": p.name,
			"projekt_name": p.project_name or "",
			"kunde_objekt": kunde_objekt.get(p.name, ""),
			"meister": get_fullname(user) if user else "",
			"meister_user": user or "",
			"status": status,
			"soll_h": flt(s["soll_h"], 2),
			"ist_h": flt(s["ist_h"], 2),
			"ist_zeitraum": _ist_im_zeitraum(p.name, von, bis) if mit_zeitraum else None,
			"abweichung": abweichung(s["soll_h"], s["ist_h"], fp),
			"soll_material": flt(s["soll_material"], 2),
			"ist_material": flt(s["ist_material"], 2),
			"fortschritt": fp,
			"naechster_termin": termin,
			"termin_text": termin_text,
			"meldungen": meldungen.get(p.name, 0) + maengel,
			"ampel": farbe,
			"ueberfaellig": bool(ueberfaellig),
			"hinweis": grund,
			"currency": s.get("currency"),
		}
		if f.get("meister") and zeile["meister_user"] != f.meister:
			continue
		if f.get("status") and zeile["status"] != f.status:
			continue
		if f.get("ampel") == FILTER_ROT and farbe != ROT:
			continue
		if f.get("ampel") == FILTER_ROT_GELB and farbe == GRUEN:
			continue
		zeilen.append(zeile)
	if len(projekte) >= PROJEKTE_MAX:
		hinweise.append(_("Es werden höchstens {0} laufende Projekte ausgewertet.").format(PROJEKTE_MAX))
	zeilen.sort(
		key=lambda z: (
			AMPEL_REIHENFOLGE.get(z["ampel"], 9),
			z["naechster_termin"] or dt.date.max,
			z["projekt"],
		)
	)
	return {"zeilen": zeilen, "hinweise": hinweise, "zeitraum": mit_zeitraum, "currency": waehrung}


# --- Kennzahlen (Number Cards vom Typ Custom) -----------------------------------------------------------


def diese_woche(tag=None) -> tuple[dt.date, dt.date]:
	t = getdate(tag or today())
	montag = t - dt.timedelta(days=t.weekday())
	return montag, montag + dt.timedelta(days=6)


def _zwischengespeichert() -> list[dict]:
	"""Übersicht der laufenden Woche, 60 s je Benutzer zwischengespeichert (vier Karten je Aufruf des
	Arbeitsbereichs); der Bericht rechnet immer frisch."""
	von, bis = diese_woche()
	schluessel = f"cv_baustellen|{frappe.session.user}|{von}"
	zeilen = frappe.cache.get_value(schluessel)
	if zeilen is None:
		zeilen = uebersicht({"von": von, "bis": bis})["zeilen"] if darf_planen() else []
		frappe.cache.set_value(schluessel, zeilen, expires_in_sec=CACHE_SEKUNDEN)
	return zeilen


def _route(**optionen) -> dict:
	von, bis = diese_woche()
	return {
		"route": ["query-report", REPORT],
		"route_options": {"von": str(von), "bis": str(bis), **optionen},
	}


@frappe.whitelist()
def karte_laufend(filters=None) -> dict:
	return {"value": len(_zwischengespeichert()), "fieldtype": "Int", **_route()}


@frappe.whitelist()
def karte_rot(filters=None) -> dict:
	zeilen = _zwischengespeichert()
	return {
		"value": sum(1 for z in zeilen if z["ampel"] == ROT),
		"fieldtype": "Int",
		**_route(ampel=FILTER_ROT),
	}


@frappe.whitelist()
def karte_stunden_woche(filters=None) -> dict:
	zeilen = _zwischengespeichert()
	return {"value": flt(sum(flt(z["ist_zeitraum"]) for z in zeilen), 1), "fieldtype": "Float", **_route()}


@frappe.whitelist()
def karte_maengel(filters=None) -> dict:
	zeilen = _zwischengespeichert()
	return {"value": sum(cint(z["meldungen"]) for z in zeilen), "fieldtype": "Int", **_route()}


KENNZAHLEN = (
	{
		"name": "Baustellen laufend",
		"method": "corevatis_erpnext.baustellen.karte_laufend",
		"color": "#2490EF",
	},
	{"name": "Baustellen rot", "method": "corevatis_erpnext.baustellen.karte_rot", "color": "#E24C4C"},
	{
		"name": "Baustellen: Stunden diese Woche",
		"method": "corevatis_erpnext.baustellen.karte_stunden_woche",
		"color": "#29CD42",
	},
	{
		"name": "Baustellen: Meldungen und Mängel offen",
		"method": "corevatis_erpnext.baustellen.karte_maengel",
		"color": "#ECAD4B",
	},
)


def ensure_setup() -> None:
	"""Kennzahlen des Arbeitsbereichs „Baustellen“ (Frappe synchronisiert Number Cards nicht aus Modulen)."""
	from corevatis_erpnext.prozess.setup import ensure_kennzahlen

	ensure_kennzahlen(KENNZAHLEN, document_type="Project")
