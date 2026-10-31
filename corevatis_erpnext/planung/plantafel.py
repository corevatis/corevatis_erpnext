"""Plantafel (Phase 1 P1.4, Issue corevatis/Handwerker#79): Mitarbeiter x Tage mit Besichtigungsterminen,
Arbeitspaketen, Abwesenheiten, Feiertagen und Fahrzeugen - verschieben per Ziehen, drucken.

Baut den Wochenplan 5.2 aus (dieselben Mitarbeiter: ``zuweisung.mitarbeiter_liste``, dieselbe
Zuweisung = ToDo) und nutzt die Belegungen der Terminplanung D14 (``terminplanung.belegungen``:
Termine, Arbeitspakete, Abwesenheiten, Feiertage mit Orten und Fahrzeiten).

- ``tafel(von, ansicht, wochenende, projekt, mitarbeiter)``: Zeilen = planbare Mitarbeiter (Reihenfolge
  aus den Planungseinstellungen, sonst Name), Spalten = Tage (Woche Mo-Fr, Sa/So einblendbar; Monat),
  je Zelle die Einsätze mit Uhrzeiten, Ort (nur PLZ/Ort für fremde Vorgänge), Fahrzeug, Konflikten
  (Überschneidung, Fahrzeug doppelt) und Fahrzeit-Hinweisen; dazu „Nicht zugewiesen“. Büro und Meister
  sehen alles, Monteure nur ihre eigene Zeile (schreibgeschützt).
- ``arbeitspaket_verschieben``: anderer Tag (Zeitraum mit Uhrzeiten verschoben) und/oder anderer
  Mitarbeiter (Zuweisung 5.2 umgehängt); Konflikte (Abwesenheit, Termin, Feiertag am Zieltag) mit
  Grund und ``trotzdem``. Termine verschiebt ``crm.termin.termin_verschieben``.
- ``fahrzeug_zuordnen``: Fahrzeug je Einsatz (``cv_fahrzeug`` an Task/Event); gleichzeitig in zwei
  Einsätzen = Konflikt.
- ``reihenfolge_speichern``: Zeilenreihenfolge der Mitarbeiter.
- ``drucken``: Wochenplan als PDF (Querformat) über ``druck_html``.

Rechte: ``tafel`` Büro/Meister/System Manager voll, Monteur eigene Zeile; alle anderen Methoden nur
Büro/Meister/System Manager (``pruefe_planer``). Jede Verschiebung und Fahrzeugzuordnung schreibt einen
Kommentar an den Beleg. Konflikte werden als ``crm.termin.TerminKonflikt`` (417) gemeldet; die
Konfliktliste steht in ``frappe.local.response["konflikte"]`` und in der Ausnahme.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json

import frappe
from frappe import _
from frappe.utils import cint, escape_html, flt, get_datetime, get_fullname, getdate, now_datetime, today

from corevatis_erpnext.crm import termin as crm_termin
from corevatis_erpnext.planung import abwesenheit, geokodierung, slotrechner, terminplanung, zuweisung
from corevatis_erpnext.planung.auftrag import PlanungFehler, darf_planen
from corevatis_erpnext.planung.einstellungen import EINSTELLUNGEN, einstellungen
from corevatis_erpnext.planung.slotrechner import (
	ART_ABWESENHEIT,
	ART_ARBEITSPAKET,
	ART_FEIERTAG,
	ART_TERMIN,
	EIN_TAG,
	Belegung,
)
from corevatis_erpnext.planung.terminplanung import TASK_OFFEN, tag_text
from corevatis_erpnext.planung.wochenplan import _firma, stunden_je_tag, wochenbeginn
from corevatis_erpnext.planung.zuweisung import TODO_ZUGEWIESEN, mitarbeiter_liste

ANSICHT_WOCHE = "woche"
ANSICHT_MONAT = "monat"
ANSICHTEN = (ANSICHT_WOCHE, ANSICHT_MONAT)
EINSAETZE = (ART_TERMIN, ART_ARBEITSPAKET)
# neue Nominatim-Anfragen je Aufruf der Tafel (Zwischenspeicher füllt sich nach und nach, kurze Ladezeit)
GEOKODIERUNG_BUDGET = 3
TerminKonflikt = crm_termin.TerminKonflikt


# --- Rechte ---------------------------------------------------------------------------------


def pruefe_planer() -> None:
	if not darf_planen():
		frappe.throw(
			_(
				"Verschieben, Fahrzeuge, Reihenfolge und Druck der Plantafel sind den Rollen Büro und Meister vorbehalten."
			),
			frappe.PermissionError,
		)


def _eigener_mitarbeiter() -> str | None:
	"""Employee des angemeldeten Monteurs/Meisters (für die eigene Zeile)."""
	if not set(zuweisung.ROLLEN_MITARBEITER) & set(frappe.get_roles()):
		return None
	return frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, "name")


def _rolle() -> tuple[bool, str | None]:
	"""``(planer, eigener Employee)``: Büro/Meister planen alles, Monteure sehen ihre Zeile."""
	if darf_planen():
		return True, None
	eigener = _eigener_mitarbeiter()
	if eigener:
		return False, eigener
	frappe.throw(
		_("Die Plantafel sehen Büro und Meister; Monteure sehen ihre eigene Zeile."), frappe.PermissionError
	)
	return False, None


# --- Zeitraum -------------------------------------------------------------------------------


def zeitraum(von, ansicht: str) -> tuple[dt.date, dt.date]:
	"""Woche: Montag bis Sonntag der Woche von ``von``; Monat: erster bis letzter Tag des Monats."""
	tag = getdate(von or today())
	if ansicht == ANSICHT_MONAT:
		erster = tag.replace(day=1)
		letzter = (erster + dt.timedelta(days=32)).replace(day=1) - EIN_TAG
		return erster, letzter
	erster = wochenbeginn(tag)
	return erster, erster + dt.timedelta(days=6)


def tage_liste(von: dt.date, bis: dt.date, wochenende: bool, feiertage: dict[dt.date, str]) -> list[dict]:
	heute = getdate(today())
	tage = []
	tag = von
	while tag <= bis:
		am_wochenende = tag.weekday() >= 5
		if wochenende or not am_wochenende:
			tage.append(
				{
					"datum": str(tag),
					"wochentag": terminplanung.WOCHENTAGE_KURZ[tag.weekday()],
					"wochenende": am_wochenende,
					"feiertag": feiertage.get(tag),
					"heute": tag == heute,
				}
			)
		tag += EIN_TAG
	return tage


def _minuten(zeit: dt.datetime, tag: dt.date) -> int:
	"""Minuten seit Mitternacht des Tages (Ende um Mitternacht des Folgetags = 1440)."""
	return int((zeit - dt.datetime.combine(tag, dt.time.min)).total_seconds()) // 60


def segmente(
	von: dt.datetime, bis: dt.datetime, tage: set[dt.date], arbeitszeit: tuple[int, int] | None = None
) -> list[dict]:
	"""Anteil einer Belegung je Kalendertag im Zeitraum: ``datum``, ``von``/``bis`` („HH:MM“),
	``von_min``/``bis_min`` (Minuten des Tages) für das Zeitraster der Zelle, ``ganztags``.
	Mit ``arbeitszeit`` (Minuten Beginn, Ende) wird ein mehrtägiger Einsatz je Tag auf die Arbeitszeit
	beschnitten (Arbeitspaket Di 07:00 bis Mi 11:00 → Di 07:00-15:00, Mi 07:00-11:00)."""
	ergebnis = []
	tag = von.date()
	letzter = (bis - dt.timedelta(microseconds=1)).date()
	while tag <= letzter:
		if tag in tage:
			anfang = dt.datetime.combine(tag, dt.time.min)
			ende_tag = anfang + EIN_TAG
			a, e = max(von, anfang), min(bis, ende_tag)
			ganztags = a == anfang and e == ende_tag
			von_min, bis_min = _minuten(a, tag), _minuten(e, tag)
			if arbeitszeit and not ganztags:
				beginn, ende = arbeitszeit
				v2, b2 = max(von_min, beginn), min(bis_min, ende)
				if b2 > v2:
					von_min, bis_min = v2, b2
			ergebnis.append(
				{
					"datum": str(tag),
					"von": "" if ganztags else f"{von_min // 60:02d}:{von_min % 60:02d}",
					"bis": "" if ganztags else f"{bis_min // 60:02d}:{bis_min % 60:02d}",
					"von_min": von_min,
					"bis_min": bis_min,
					"ganztags": ganztags,
				}
			)
		tag += EIN_TAG
	return ergebnis


def _arbeitszeit_minuten(e) -> tuple[int, int]:
	beginn = terminplanung.arbeitsbeginn(e)
	start = beginn.hour * 60 + beginn.minute
	return start, min(start + round(flt(e.tagesarbeitszeit or 8) * 60), 24 * 60)


# --- Details der Einsätze -------------------------------------------------------------------


class Details:
	"""Zusätzliche Felder der Tasks und Events einer Tafel, gebündelt abgefragt."""

	def __init__(self) -> None:
		self.tasks: dict[str, frappe._dict] = {}
		self.events: dict[str, frappe._dict] = {}
		self.projekte: dict[str, str] = {}
		self.zugewiesen: dict[str, list[str]] = {}
		self.fahrzeuge: dict[str, str] = {}
		self._lesbar: dict[tuple[str, str], bool] = {}

	def laden(self, task_namen: set[str], event_namen: set[str]) -> None:
		if task_namen:
			felder = [
				"name",
				"subject",
				"project",
				"status",
				"exp_start_date",
				"exp_end_date",
				"expected_time",
			]
			felder += [
				f
				for f in ("cv_verfahren", "cv_sollstunden", "cv_fahrzeug")
				if frappe.db.has_column("Task", f)
			]
			for t in frappe.get_all("Task", filters={"name": ["in", list(task_namen)]}, fields=felder):
				self.tasks[t.name] = t
			projekte = {t.project for t in self.tasks.values() if t.project}
			if projekte:
				for p in frappe.get_all(
					"Project", filters={"name": ["in", list(projekte)]}, fields=["name", "project_name"]
				):
					self.projekte[p.name] = p.project_name or p.name
			for todo in frappe.get_all(
				"ToDo",
				filters={
					"reference_type": "Task",
					"reference_name": ["in", list(task_namen)],
					"status": ["in", TODO_ZUGEWIESEN],
				},
				fields=["reference_name", "allocated_to"],
				order_by="creation asc",
			):
				liste = self.zugewiesen.setdefault(todo.reference_name, [])
				if todo.allocated_to and todo.allocated_to not in liste:
					liste.append(todo.allocated_to)
		if event_namen:
			felder = ["name", "subject", "location", "status"]
			if frappe.db.has_column("Event", "cv_fahrzeug"):
				felder.append("cv_fahrzeug")
			for e in frappe.get_all("Event", filters={"name": ["in", list(event_namen)]}, fields=felder):
				self.events[e.name] = e
		self.fahrzeuge = {
			f.name: f.bezeichnung or f.name
			for f in frappe.get_all("Fahrzeug", fields=["name", "bezeichnung"])
		}

	def lesbar(self, doctype: str, name: str | None) -> bool:
		if not name:
			return False
		key = (doctype, name)
		if key not in self._lesbar:
			self._lesbar[key] = terminplanung._lesbar(doctype, name)
		return self._lesbar[key]


def _zeit(b: Belegung) -> str:
	if b.von.time() == dt.time.min and b.bis.time() == dt.time.min:
		# ganztägig (Abwesenheit, Feiertag): „ganztags“ bzw. „Mo 03.11. - Mi 05.11.“ statt „00:00-00:00“
		letzter = (b.bis - dt.timedelta(microseconds=1)).date()
		return _("ganztags") if letzter == b.von.date() else terminplanung._zeit_text_tage(b)
	return terminplanung._zeit_text(b)


def _eintrag(
	b: Belegung, d: Details, tage: set[dt.date], user: str | None, arbeitszeit: tuple[int, int] | None = None
) -> dict:
	"""Einsatz für die Zelle: Art, Zeiten, Titel, Ort (PLZ/Ort für fremde Vorgänge), Fahrzeug, Rechte."""
	e = {
		"art": b.art,
		"doctype": b.doctype,
		"name": b.name,
		"von": b.von.strftime("%Y-%m-%d %H:%M:%S"),
		"bis": b.bis.strftime("%Y-%m-%d %H:%M:%S"),
		"zeit": _zeit(b),
		"segmente": segmente(b.von, b.bis, tage, arbeitszeit if b.art == ART_ARBEITSPAKET else None),
		"titel": "",
		"ort": "",
		"lesbar": False,
		"fahrzeug": None,
		"fahrzeug_name": None,
		"verschiebbar": False,
		"konflikte": [],
		"hinweise": [],
	}
	if b.art == ART_ARBEITSPAKET:
		t = d.tasks.get(b.name) or frappe._dict()
		lesbar = d.lesbar("Task", b.name)
		projekt_lesbar = bool(t.project) and d.lesbar("Project", t.project)
		verfahren = t.get("cv_verfahren") or ""
		e.update(
			titel=verfahren or (t.subject if lesbar else _("Arbeitspaket")),
			untertitel=(t.subject if verfahren and lesbar else ""),
			projekt=t.project if projekt_lesbar else None,
			projekt_name=d.projekte.get(t.project, "") if projekt_lesbar else "",
			verfahren=verfahren,
			status=t.status,
			sollstunden=flt(t.get("expected_time") or t.get("cv_sollstunden") or 0, 2),
			zugewiesen=d.zugewiesen.get(b.name, []),
			lesbar=lesbar,
			ort=terminplanung.belegung_ort(b),
			fahrzeug=t.get("cv_fahrzeug") or None,
			verschiebbar=lesbar,
		)
	elif b.art == ART_TERMIN:
		ev = d.events.get(b.name) or frappe._dict()
		lesbar = d.lesbar("Event", b.name)
		e.update(
			titel=(ev.subject or _("Besichtigung")) if lesbar else _("Besichtigung"),
			lead=b.lead if lesbar else None,
			lesbar=lesbar,
			# fremder Termin (nicht freigegeben): wie der Kalender 1.3 nur PLZ/Ort, auch mit Leserecht
			# auf Anfragen
			ort=terminplanung.belegung_ort(b) if lesbar else (crm_termin._ort(b.ort.text or "") or ""),
			fahrzeug=ev.get("cv_fahrzeug") or None,
			verschiebbar=lesbar,
		)
	elif b.art == ART_ABWESENHEIT:
		# Art (Urlaub, Krank, …) nur für Büro/System Manager und den Betroffenen; sonst „Abwesend“
		sichtbar = abwesenheit.art_sichtbar(user)
		e.update(
			titel=b.titel if sichtbar and b.titel else _("Abwesend"),
			abwesenheit_art=b.titel if sichtbar else None,
			lesbar=sichtbar,
		)
	if e["fahrzeug"]:
		e["fahrzeug_name"] = d.fahrzeuge.get(e["fahrzeug"], e["fahrzeug"])
	return e


# --- Konflikte und Hinweise -----------------------------------------------------------------


def _ueberschneidungen(paare: list[tuple[Belegung, dict]]) -> None:
	"""Zwei Einsätze desselben Mitarbeiters zur gleichen Zeit (oder Einsatz während einer Abwesenheit)."""
	for i, (a, ea) in enumerate(paare):
		for b, eb in paare[i + 1 :]:
			if a.art == ART_ABWESENHEIT and b.art == ART_ABWESENHEIT:
				continue
			if not slotrechner.ueberschneidung(a.von, a.bis, b.von, b.bis):
				continue
			ea["konflikte"].append(_("Überschneidung mit {0} ({1})").format(_titel_kurz(eb), _zeit(b)))
			eb["konflikte"].append(_("Überschneidung mit {0} ({1})").format(_titel_kurz(ea), _zeit(a)))


def _titel_kurz(e: dict) -> str:
	if e["art"] == ART_ABWESENHEIT:
		art = e.get("abwesenheit_art")
		return _("Abwesenheit {0}").format(art) if art else _("Abwesenheit")
	text = f"{_(e['art'])} {e['name']}"
	if e.get("lesbar") and e.get("titel"):
		text += f" ({e['titel']})"
	return text


def _fahrzeit_hinweise(paare: list[tuple[Belegung, dict]], ctx: terminplanung.Kontext) -> None:
	"""Zu wenig Zeit für Fahrt und Puffer zwischen zwei Einsätzen desselben Tages (D14-Regeln)."""
	einsaetze = sorted(
		((b, e) for b, e in paare if b.art in EINSAETZE), key=lambda be: (be[0].von, be[0].bis)
	)
	puffer = max(ctx.p.puffer, 0)
	for (a, _ea), (b, eb) in itertools.pairwise(einsaetze):
		if a.bis.date() != b.von.date() or slotrechner.ueberschneidung(a.von, a.bis, b.von, b.bis):
			continue
		minuten, geschaetzt = slotrechner.fahrzeit_minuten(a.ort, b.ort, ctx.p)
		luecke = int((b.von - a.bis).total_seconds() // 60)
		if luecke < minuten + puffer:
			text = _("Nur {0} min bis zum vorigen Einsatz, Fahrt {1} min + {2} min Puffer").format(
				luecke, minuten, puffer
			)
			if geschaetzt:
				text += " " + _("(geschätzt)")
			eb["hinweise"].append(text + ".")


def _fahrzeug_konflikte(alle: list[tuple[Belegung, dict]], d: Details) -> int:
	"""Ein Fahrzeug in zwei Einsätzen zur gleichen Zeit (über alle Mitarbeiter)."""
	je_fahrzeug: dict[str, list[tuple[Belegung, dict]]] = {}
	for b, e in alle:
		if e.get("fahrzeug") and b.art in EINSAETZE:
			je_fahrzeug.setdefault(e["fahrzeug"], []).append((b, e))
	anzahl = 0
	for fahrzeug, paare in je_fahrzeug.items():
		gesehen: set[tuple[str, str]] = set()
		for i, (a, ea) in enumerate(paare):
			for b, eb in paare[i + 1 :]:
				if (a.doctype, a.name) == (b.doctype, b.name):
					continue
				if not slotrechner.ueberschneidung(a.von, a.bis, b.von, b.bis):
					continue
				schluessel = tuple(sorted([f"{a.doctype}:{a.name}", f"{b.doctype}:{b.name}"]))
				if schluessel in gesehen:
					continue
				gesehen.add(schluessel)
				anzahl += 1
				name = d.fahrzeuge.get(fahrzeug, fahrzeug)
				ea["konflikte"].append(
					_("Fahrzeug {0} gleichzeitig bei {1} ({2})").format(name, _titel_kurz(eb), _zeit(b))
				)
				eb["konflikte"].append(
					_("Fahrzeug {0} gleichzeitig bei {1} ({2})").format(name, _titel_kurz(ea), _zeit(a))
				)
				ea["fahrzeug_konflikt"] = eb["fahrzeug_konflikt"] = True
	return anzahl


# --- Tafel ----------------------------------------------------------------------------------


def reihenfolge(e=None) -> list[str]:
	e = e or einstellungen()
	return [z.strip() for z in str(e.plantafel_reihenfolge or "").splitlines() if z.strip()]


def _sortiert(leute: list[dict], e) -> list[dict]:
	ordnung = {name: i for i, name in enumerate(reihenfolge(e))}
	return sorted(
		leute, key=lambda m: (ordnung.get(m["employee"], len(ordnung)), (m["employee_name"] or "").lower())
	)


def _kontext(e) -> terminplanung.Kontext:
	ctx = terminplanung.Kontext(e)
	ctx.budget = geokodierung.Budget(abfragen=GEOKODIERUNG_BUDGET)
	return ctx


def _offene_tasks(
	von: dt.date, bis: dt.date, ctx: terminplanung.Kontext, projekt: str | None
) -> list[Belegung]:
	"""Arbeitspakete im Zeitraum ohne Zuweisung (Rechte des Benutzers)."""
	filters: dict = {
		"status": ["in", list(TASK_OFFEN)],
		"is_template": 0,
		"exp_start_date": [
			"between",
			[
				dt.datetime.combine(von - dt.timedelta(days=366), dt.time.min),
				dt.datetime.combine(bis, dt.time.max),
			],
		],
	}
	if projekt:
		filters["project"] = projekt
	tasks = frappe.get_list(
		"Task",
		filters=filters,
		fields=["name", "subject", "project", "exp_start_date", "exp_end_date"],
		order_by="exp_start_date asc, name asc",
		limit_page_length=0,
	)
	if not tasks:
		return []
	zugewiesen = set(
		frappe.get_all(
			"ToDo",
			filters={
				"reference_type": "Task",
				"reference_name": ["in", [t.name for t in tasks]],
				"status": ["in", TODO_ZUGEWIESEN],
			},
			pluck="reference_name",
		)
	)
	anfang = dt.datetime.combine(von, dt.time.min)
	ergebnis = []
	for t in tasks:
		if t.name in zugewiesen:
			continue
		b = terminplanung.task_belegung(t, ctx)
		if b is not None and b.bis >= anfang:
			ergebnis.append(b)
	return ergebnis


def _last(user: str, bel: list[Belegung], d: Details, e, freie: set, tage: list[dt.date]) -> dict:
	"""Tageslast aus den Arbeitspaketen (wie Wochenplan 5.2: Sollstunden ÷ Zugewiesene, anteilig je Tag)."""
	last = dict.fromkeys(tage, 0.0)
	for b in bel:
		if b.art != ART_ARBEITSPAKET:
			continue
		t = d.tasks.get(b.name)
		if not t:
			continue
		stunden = flt(t.get("expected_time")) or flt(t.get("cv_sollstunden"))
		anteil = max(len(d.zugewiesen.get(b.name, [])), 1)
		for tag, h in stunden_je_tag(
			t.exp_start_date, t.exp_end_date, stunden, e.arbeitsbeginn, e.tagesarbeitszeit, freie
		).items():
			if tag in last:
				last[tag] += h / anteil
	return last


def tafel_berechnen(
	von=None,
	ansicht: str = ANSICHT_WOCHE,
	wochenende=0,
	projekt: str | None = None,
	mitarbeiter: str | None = None,
) -> dict:
	"""Die Tafel für den angemeldeten Benutzer (Rechte siehe Modul)."""
	planer, eigener = _rolle()
	ansicht = ansicht if ansicht in ANSICHTEN else ANSICHT_WOCHE
	wochenende = bool(cint(wochenende))
	e = einstellungen()
	ctx = _kontext(e)
	firma = _firma()
	von_tag, bis_tag = zeitraum(von, ansicht)
	feiertage = terminplanung._feiertage(firma, von_tag, bis_tag)
	tage = tage_liste(von_tag, bis_tag, wochenende, feiertage)
	tage_set = {getdate(t["datum"]) for t in tage}
	tage_dates = sorted(tage_set)
	arbeitszeit = _arbeitszeit_minuten(e)

	leute = _sortiert(mitarbeiter_liste(), e)
	if not planer:
		leute = [m for m in leute if m["employee"] == eigener]
		if not leute:
			frappe.throw(_("Zu Ihrem Benutzer gibt es keinen planbaren Mitarbeiter."), frappe.PermissionError)
	elif mitarbeiter:
		leute = [m for m in leute if m["employee"] == mitarbeiter]

	belegt: dict[str, list[Belegung]] = {}
	for m in leute:
		bel = terminplanung.belegungen(m["user"], von_tag, bis_tag, ctx, firma)
		belegt[m["user"]] = [b for b in bel if b.art != ART_FEIERTAG]
	offen = _offene_tasks(von_tag, bis_tag, ctx, projekt) if planer and not mitarbeiter else []

	d = Details()
	task_namen = {b.name for liste in belegt.values() for b in liste if b.doctype == "Task"}
	task_namen |= {b.name for b in offen}
	event_namen = {b.name for liste in belegt.values() for b in liste if b.doctype == "Event"}
	d.laden(task_namen, event_namen)

	alle: list[tuple[Belegung, dict]] = []
	zeilen = []
	konflikte_gesamt = hinweise_gesamt = abwesenheiten_gesamt = wochenende_eintraege = 0
	for m in leute:
		paare = []
		for b in belegt[m["user"]]:
			if projekt and b.doctype == "Task" and (d.tasks.get(b.name) or {}).get("project") != projekt:
				continue
			eintrag = _eintrag(b, d, tage_set, m["user"], arbeitszeit)
			if not eintrag["segmente"]:
				if any(t.weekday() >= 5 for t in _tage_von(b)):
					wochenende_eintraege += 1
				continue
			paare.append((b, eintrag))
		_ueberschneidungen(paare)
		_fahrzeit_hinweise(paare, ctx)
		alle += paare
		last = _last(m["user"], belegt[m["user"]], d, e, set(feiertage), tage_dates)
		abwesend = {}
		for b, eintrag in paare:
			if b.art == ART_ABWESENHEIT:
				abwesenheiten_gesamt += 1
				for s in eintrag["segmente"]:
					if s["ganztags"]:
						abwesend[s["datum"]] = eintrag["titel"]
		zeilen.append(
			{
				"mitarbeiter": m["employee"],
				"name": m["employee_name"],
				"user": m["user"],
				"eigene": m["user"] == frappe.session.user,
				"eintraege": [eintrag for _b, eintrag in paare],
				"last": {str(tag): flt(h, 2) for tag, h in last.items()},
				"ueberlast": [str(tag) for tag, h in last.items() if flt(h, 2) > flt(e.tagesarbeitszeit)],
				"abwesend": abwesend,
			}
		)

	offene_eintraege = []
	for b in offen:
		eintrag = _eintrag(b, d, tage_set, None, arbeitszeit)
		if eintrag["segmente"]:
			offene_eintraege.append(eintrag)
			alle.append((b, eintrag))
	fahrzeug_konflikte = _fahrzeug_konflikte(alle, d)
	for _b, eintrag in alle:
		konflikte_gesamt += 1 if eintrag["konflikte"] else 0
		hinweise_gesamt += 1 if eintrag["hinweise"] else 0

	fahrzeuge = frappe.get_all(
		"Fahrzeug", filters={"aktiv": 1}, fields=["name", "bezeichnung"], order_by="name asc"
	)
	hinweise = []
	if wochenende_eintraege and not wochenende:
		hinweise.append(
			_("{0} Einsatz/Einsätze am Wochenende - Sa/So einblenden.").format(wochenende_eintraege)
		)
	if ctx.budget.fehlgeschlagen:
		hinweise.append(_("Fahrzeiten teilweise geschätzt (Geokodierung gerade nicht möglich)."))
	return {
		"von": str(von_tag),
		"bis": str(bis_tag),
		"ansicht": ansicht,
		"wochenende": wochenende,
		"heute": today(),
		"tage": tage,
		"arbeitsbeginn": str(e.arbeitsbeginn)[:5],
		"tagesarbeitszeit": flt(e.tagesarbeitszeit),
		"zeilen": zeilen,
		"nicht_zugewiesen": offene_eintraege,
		"fahrzeuge": [{"value": f.name, "label": f.bezeichnung or f.name, "name": f.name} for f in fahrzeuge],
		"schreibgeschuetzt": not planer,
		"darf_abwesenheit": bool(frappe.has_permission("Abwesenheit", "create")),
		"darf_fahrzeug": bool(frappe.has_permission("Fahrzeug", "create")),
		"projekt": projekt or None,
		"mitarbeiter": mitarbeiter or None,
		"reihenfolge": reihenfolge(e) if planer else [],
		"hinweise": hinweise,
		"zusammenfassung": {
			"mitarbeiter": len(zeilen),
			"einsaetze": sum(1 for _b, x in alle if x["art"] in EINSAETZE),
			"nicht_zugewiesen": len(offene_eintraege),
			"abwesenheiten": abwesenheiten_gesamt,
			"konflikte": konflikte_gesamt,
			"fahrzeug_konflikte": fahrzeug_konflikte,
			"fahrzeit_hinweise": hinweise_gesamt,
		},
	}


def _tage_von(b: Belegung) -> list[dt.date]:
	tage = []
	tag = b.von.date()
	letzter = (b.bis - dt.timedelta(microseconds=1)).date()
	while tag <= letzter:
		tage.append(tag)
		tag += EIN_TAG
	return tage


@frappe.whitelist()
def tafel(von=None, ansicht: str = ANSICHT_WOCHE, wochenende=0, projekt=None, mitarbeiter=None) -> dict:
	"""Plantafel als JSON (Desk-Seite ``plantafel``, REST). Büro/Meister/System Manager: alle Zeilen,
	Monteur: nur die eigene Zeile, schreibgeschützt."""
	return tafel_berechnen(von, ansicht, wochenende, projekt or None, mitarbeiter or None)


# --- Verschieben ----------------------------------------------------------------------------


def _konflikt_eintrag(name: str, b: Belegung, user: str) -> dict:
	return {
		"text": terminplanung.konflikt_text(name, b, None),
		"meister": user,
		"meister_name": name,
		"art": b.art,
		"doctype": b.doctype,
		"name": b.name,
		"von": b.von.strftime("%Y-%m-%d %H:%M:%S"),
		"bis": b.bis.strftime("%Y-%m-%d %H:%M:%S"),
		"ort": terminplanung.belegung_ort(b),
		"fahrzeit_min": None,
		"geschaetzt": False,
	}


def konflikte_arbeitspaket(
	task: str, user: str | None, start: dt.datetime, ende: dt.datetime, ctx: terminplanung.Kontext, firma
) -> list[dict]:
	"""Was dem Arbeitspaket ``start``-``ende`` beim Mitarbeiter im Weg steht: Termine und Abwesenheiten
	im Zeitraum, ein Feiertag am Zieltag. Andere Arbeitspakete sind Überlast (Hinweis), kein Konflikt."""
	name = get_fullname(user) if user else ""
	ergebnis = []
	if user:
		bel = terminplanung.belegungen(user, start.date() - EIN_TAG, ende.date() + EIN_TAG, ctx, firma)
	else:
		bel = []
		for tag, bezeichnung in terminplanung._feiertage(firma, start.date(), ende.date()).items():
			anfang = dt.datetime.combine(tag, dt.time.min)
			bel.append(Belegung(anfang, anfang + EIN_TAG, slotrechner.UNBEKANNT, ART_FEIERTAG, bezeichnung))
	for b in bel:
		if b.doctype == "Task" and b.name == task:
			continue
		if b.art == ART_FEIERTAG:
			if b.von.date() == start.date():
				ergebnis.append(_konflikt_eintrag(name, b, user or ""))
			continue
		if b.art not in (ART_TERMIN, ART_ABWESENHEIT):
			continue
		if slotrechner.ueberschneidung(start, ende, b.von, b.bis):
			ergebnis.append(_konflikt_eintrag(name, b, user or ""))
	return ergebnis


def konflikt_werfen(liste: list[dict], user: str | None) -> None:
	"""Wie ``terminplanung.konflikt_werfen``, Wortlaut für Einsätze der Plantafel."""
	frappe.local.response["konflikte"] = liste
	zeilen = "".join(f"<li>{escape_html(k['text'])}</li>" for k in liste)
	frappe.msgprint(
		escape_html(_("Der Einsatz überschneidet sich mit der Planung:"))
		+ f"<ul>{zeilen}</ul>"
		+ escape_html(_("Anderen Tag oder Mitarbeiter wählen oder mit „Trotzdem verschieben“ übersteuern.")),
		title=_("Überschneidung"),
		indicator="orange",
	)
	raise TerminKonflikt(terminplanung.konflikt_grund(liste), liste, user)


@frappe.whitelist(methods=["POST"])
def arbeitspaket_verschieben(
	task: str,
	datum: str,
	mitarbeiter: str | None = None,
	von_mitarbeiter: str | None = None,
	trotzdem=0,
	freigeben=0,
) -> dict:
	"""Arbeitspaket auf einen anderen Tag (Beginn und Ende um ganze Tage verschoben, Uhrzeiten bleiben)
	und/oder zu einem anderen Mitarbeiter (Zuweisung 5.2: ``von_mitarbeiter`` entfernen, ``mitarbeiter``
	zuweisen; ``freigeben=1`` ohne ``mitarbeiter`` = in „Nicht zugewiesen“ gezogen, Zuweisung entfernen).
	Konflikte (Abwesenheit, Termin des Mitarbeiters, Feiertag am Zieltag) → ``TerminKonflikt``;
	``trotzdem=1`` übersteuert mit Vermerk. Hinweise (Überlast, Vorgänger, Abwesenheit) wie beim Zuweisen."""
	pruefe_planer()
	mitarbeiter = (mitarbeiter or "").strip() or None
	von_mitarbeiter = (von_mitarbeiter or "").strip() or None
	freigeben = bool(cint(freigeben)) and bool(von_mitarbeiter) and not mitarbeiter
	t = zuweisung.task_sperren(task)
	frappe.has_permission("Task", "write", task, throw=True)
	if t.status in zuweisung.TASK_GESCHLOSSEN:
		frappe.throw(
			_("Arbeitspaket {0} ist {1} - nicht mehr verschiebbar.").format(task, _(t.status)), PlanungFehler
		)
	if not t.exp_start_date:
		frappe.throw(
			_("Arbeitspaket {0} hat keinen Beginn - bitte im Arbeitspaket einen Termin setzen.").format(task),
			PlanungFehler,
		)
	neu_tag = getdate(datum)
	start = get_datetime(t.exp_start_date)
	ende = get_datetime(t.exp_end_date) if t.exp_end_date else None
	delta = neu_tag - start.date()
	neu_start = start + delta
	neu_ende = ende + delta if ende else None

	e = einstellungen()
	ctx = _kontext(e)
	firma = _firma()
	wechsel = bool(mitarbeiter) and mitarbeiter != von_mitarbeiter
	if wechsel:
		ziel = zuweisung.mitarbeiter_pruefen(mitarbeiter)
		pruef_user = [ziel.user_id]
	else:
		pruef_user = [
			u
			for u in frappe.get_all(
				"ToDo",
				filters={"reference_type": "Task", "reference_name": task, "status": "Open"},
				pluck="allocated_to",
				distinct=True,
			)
			if u
		]
	zeitraum_b = terminplanung.task_belegung(
		frappe._dict(
			name=task, subject=t.subject, project=t.project, exp_start_date=neu_start, exp_end_date=neu_ende
		),
		ctx,
	)
	konflikte: list[dict] = []
	if delta.days != 0 or wechsel:
		for user in pruef_user or [None]:
			konflikte += konflikte_arbeitspaket(task, user, zeitraum_b.von, zeitraum_b.bis, ctx, firma)
	if freigeben:
		# in „Nicht zugewiesen“ gezogen: die Zeit zählt nicht mehr gegen den bisherigen Mitarbeiter
		konflikte = [k for k in konflikte if k["art"] == ART_FEIERTAG]
	if konflikte and not cint(trotzdem):
		konflikt_werfen(konflikte, pruef_user[0] if pruef_user else None)

	wer = escape_html(get_fullname(frappe.session.user) or frappe.session.user)
	hinweise: list[str] = []
	if delta.days != 0:
		doc = frappe.get_doc("Task", task)
		doc.exp_start_date = neu_start
		doc.exp_end_date = neu_ende
		if doc.project and neu_ende:
			projektende = frappe.db.get_value("Project", doc.project, "expected_end_date")
			if projektende and getdate(projektende) < neu_ende.date():
				frappe.db.set_value("Project", doc.project, "expected_end_date", neu_ende.date())
				hinweise.append(
					_("Projektende auf {0} verschoben.").format(
						frappe.format(neu_ende.date(), {"fieldtype": "Date"})
					)
				)
		doc.save()
		doc.add_comment(
			"Info",
			_("Verschoben von {0}: {1} → {2}").format(
				wer, _zeitraum_text(start, ende), _zeitraum_text(neu_start, neu_ende)
			),
		)
	if wechsel:
		if von_mitarbeiter:
			zuweisung.zuweisung_entfernen(task, von_mitarbeiter)
		r = zuweisung.zuweisen(task, mitarbeiter)
		hinweise += [h for h in r["hinweise"] if "bereits zugewiesen" not in h]
		frappe.get_doc("Task", task).add_comment(
			"Info",
			_("Umgeplant von {0}: {1} → {2}").format(
				wer,
				escape_html(frappe.db.get_value("Employee", von_mitarbeiter, "employee_name") or "-")
				if von_mitarbeiter
				else "-",
				escape_html(r["employee_name"]),
			),
		)
	if freigeben:
		r = zuweisung.zuweisung_entfernen(task, von_mitarbeiter)
		if r["entfernt"]:
			frappe.get_doc("Task", task).add_comment(
				"Info",
				_("Zuweisung entfernt von {0}: {1} (in „Nicht zugewiesen“ gezogen)").format(
					wer, escape_html(frappe.db.get_value("Employee", von_mitarbeiter, "employee_name") or "-")
				),
			)
	if konflikte:
		frappe.get_doc("Task", task).add_comment(
			"Info",
			_("Trotz Überschneidung verschoben von {0}: {1}").format(
				wer, " ".join(escape_html(k["text"]) for k in konflikte)
			),
		)
	if delta.days == 0 and not wechsel and not freigeben:
		hinweise.append(_("Keine Änderung: Das Arbeitspaket lag schon dort."))
	return {
		"task": task,
		"start": neu_start.strftime("%Y-%m-%d %H:%M:%S"),
		"ende": neu_ende.strftime("%Y-%m-%d %H:%M:%S") if neu_ende else None,
		"zugewiesen": zuweisung.stand(task)["zugewiesen"],
		"hinweise": hinweise,
		"konflikte": konflikte,
	}


def _zeitraum_text(start: dt.datetime, ende: dt.datetime | None) -> str:
	if not ende:
		return f"{start:%d.%m.%Y %H:%M}"
	if ende.date() == start.date():
		return f"{start:%d.%m.%Y %H:%M}\u2013{ende:%H:%M}"
	return f"{start:%d.%m.%Y %H:%M} \u2013 {ende:%d.%m.%Y %H:%M}"


# --- Fahrzeuge ------------------------------------------------------------------------------


def _einsatz_zeitraum(doctype: str, name: str, ctx: terminplanung.Kontext) -> tuple[dt.datetime, dt.datetime]:
	if doctype == "Task":
		t = frappe.db.get_value(
			"Task", name, ["name", "subject", "project", "exp_start_date", "exp_end_date"], as_dict=True
		)
		b = terminplanung.task_belegung(t, ctx) if t else None
		if b is None:
			frappe.throw(_("Arbeitspaket {0} hat keinen Beginn.").format(name), PlanungFehler)
		return b.von, b.bis
	ev = frappe.db.get_value("Event", name, ["starts_on", "ends_on", "all_day"], as_dict=True)
	start = get_datetime(ev.starts_on)
	ende = (
		get_datetime(ev.ends_on)
		if ev.ends_on
		else start + dt.timedelta(minutes=terminplanung.EVENT_STANDARD_MINUTEN)
	)
	if cint(ev.all_day):
		start = dt.datetime.combine(start.date(), dt.time.min)
		ende = dt.datetime.combine(max(ende, start).date(), dt.time.min) + EIN_TAG
	return start, max(ende, start + dt.timedelta(minutes=1))


def fahrzeug_konflikte(
	fahrzeug: str,
	start: dt.datetime,
	ende: dt.datetime,
	ausser: tuple[str, str] | None,
	ctx: terminplanung.Kontext,
) -> list[dict]:
	"""Einsätze mit demselben Fahrzeug im Zeitraum (ohne den eigenen)."""
	name = frappe.db.get_value("Fahrzeug", fahrzeug, "bezeichnung") or fahrzeug
	ergebnis = []
	for t in frappe.get_all(
		"Task",
		filters={"cv_fahrzeug": fahrzeug, "status": ["in", list(TASK_OFFEN)], "exp_start_date": ["<=", ende]},
		fields=["name", "subject", "project", "exp_start_date", "exp_end_date"],
	):
		if ausser == ("Task", t.name):
			continue
		b = terminplanung.task_belegung(t, ctx)
		if b is not None and slotrechner.ueberschneidung(start, ende, b.von, b.bis):
			ergebnis.append(_fahrzeug_konflikt(name, b))
	for ev in frappe.get_all(
		"Event",
		filters={"cv_fahrzeug": fahrzeug, "status": "Open", "starts_on": ["<=", ende]},
		fields=["name", "starts_on", "ends_on", "all_day"],
	):
		if ausser == ("Event", ev.name):
			continue
		von_e, bis_e = _einsatz_zeitraum("Event", ev.name, ctx)
		if slotrechner.ueberschneidung(start, ende, von_e, bis_e):
			ergebnis.append(
				_fahrzeug_konflikt(
					name, Belegung(von_e, bis_e, art=ART_TERMIN, doctype="Event", name=ev.name)
				)
			)
	return ergebnis


def _fahrzeug_konflikt(fahrzeug_name: str, b: Belegung) -> dict:
	return {
		"text": _("Fahrzeug {0} ist {1} schon eingeplant ({2} {3}).").format(
			fahrzeug_name, _zeit_mit_tag(b), _(b.art), b.name
		),
		"art": b.art,
		"doctype": b.doctype,
		"name": b.name,
		"von": b.von.strftime("%Y-%m-%d %H:%M:%S"),
		"bis": b.bis.strftime("%Y-%m-%d %H:%M:%S"),
		"fahrzeug": fahrzeug_name,
	}


def _zeit_mit_tag(b: Belegung) -> str:
	return (
		f"{tag_text(b.von.date())} {_zeit(b)}"
		if b.von.date() == (b.bis - dt.timedelta(microseconds=1)).date()
		else _zeit(b)
	)


@frappe.whitelist(methods=["POST"])
def fahrzeug_zuordnen(doctype: str, name: str, fahrzeug: str | None = None, trotzdem=0) -> dict:
	"""Fahrzeug einem Einsatz zuordnen (``cv_fahrzeug`` an Task oder Event; leer = entfernen). Ist das
	Fahrzeug zur selben Zeit schon eingeplant → ``TerminKonflikt``; ``trotzdem=1`` übersteuert."""
	pruefe_planer()
	if doctype not in ("Task", "Event"):
		frappe.throw(_("Fahrzeuge gibt es nur an Arbeitspaketen und Terminen."))
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} nicht gefunden.").format(_(doctype), name), frappe.DoesNotExistError)
	frappe.has_permission(doctype, "write", name, throw=True)
	fahrzeug = (fahrzeug or "").strip() or None
	konflikte: list[dict] = []
	bezeichnung = None
	if fahrzeug:
		f = frappe.db.get_value("Fahrzeug", fahrzeug, ["name", "bezeichnung", "aktiv"], as_dict=True)
		if not f:
			frappe.throw(_("Fahrzeug {0} nicht gefunden.").format(fahrzeug), frappe.DoesNotExistError)
		if not cint(f.aktiv):
			frappe.throw(_("Fahrzeug {0} ist nicht aktiv.").format(fahrzeug), PlanungFehler)
		bezeichnung = f.bezeichnung or f.name
		ctx = _kontext(einstellungen())
		start, ende = _einsatz_zeitraum(doctype, name, ctx)
		konflikte = fahrzeug_konflikte(fahrzeug, start, ende, (doctype, name), ctx)
		if konflikte and not cint(trotzdem):
			konflikt_werfen(konflikte, None)
	vorher = frappe.db.get_value(doctype, name, "cv_fahrzeug")
	if (vorher or None) != fahrzeug:
		doc = frappe.get_doc(doctype, name)
		doc.db_set("cv_fahrzeug", fahrzeug)
		wer = escape_html(get_fullname(frappe.session.user) or frappe.session.user)
		if fahrzeug:
			text = _("Fahrzeug {0} zugeordnet von {1}").format(escape_html(bezeichnung), wer)
			if konflikte:
				text += " " + _("(trotz Überschneidung: {0})").format(
					" ".join(escape_html(k["text"]) for k in konflikte)
				)
		else:
			text = _("Fahrzeug {0} entfernt von {1}").format(escape_html(vorher or "-"), wer)
		doc.add_comment("Info", text)
	return {
		"doctype": doctype,
		"name": name,
		"fahrzeug": fahrzeug,
		"fahrzeug_name": bezeichnung,
		"konflikte": konflikte,
	}


# --- Reihenfolge ----------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def reihenfolge_speichern(mitarbeiter) -> list[str]:
	"""Reihenfolge der Mitarbeiter-Zeilen (Employee-Namen) in den Planungseinstellungen speichern."""
	pruefe_planer()
	if isinstance(mitarbeiter, str):
		try:
			liste = (
				json.loads(mitarbeiter) if mitarbeiter.strip().startswith("[") else mitarbeiter.splitlines()
			)
		except ValueError:
			liste = mitarbeiter.splitlines()
	else:
		liste = list(mitarbeiter or [])
	liste = [str(m).strip() for m in liste if str(m).strip()]
	unbekannt = [m for m in liste if not frappe.db.exists("Employee", m)]
	if unbekannt:
		frappe.throw(
			_("Mitarbeiter nicht gefunden: {0}").format(", ".join(escape_html(u)[:60] for u in unbekannt))
		)
	doc = frappe.get_single(EINSTELLUNGEN)
	doc.plantafel_reihenfolge = "\n".join(dict.fromkeys(liste))
	doc.save()
	return reihenfolge(einstellungen())


# --- Druck ----------------------------------------------------------------------------------


def druck_html(daten: dict) -> str:
	"""Wochenplan als eigenständiges HTML (Querformat): Mitarbeiter x Tage mit Einsätzen, Fahrzeug,
	Adresse (wie in der Tafel: PLZ/Ort für fremde Vorgänge), Abwesenheiten, Feiertage."""
	h = escape_html
	tage = daten["tage"]
	titel = _("Wochenplan {0} bis {1}").format(
		frappe.format(getdate(tage[0]["datum"]), {"fieldtype": "Date"}) if tage else daten["von"],
		frappe.format(getdate(tage[-1]["datum"]), {"fieldtype": "Date"}) if tage else daten["bis"],
	)
	kopf = "".join(
		f'<th class="tag{" frei" if t["feiertag"] else ""}{" we" if t["wochenende"] else ""}">'
		f"{h(t['wochentag'])} {getdate(t['datum']):%d.%m.}"
		+ (f'<div class="feiertag">{h(_("Feiertag"))}: {h(t["feiertag"])}</div>' if t["feiertag"] else "")
		+ "</th>"
		for t in tage
	)
	zeilen_html = []
	for z in daten["zeilen"]:
		zeilen_html.append(_druck_zeile(h(z["name"]), z["eintraege"], tage, z.get("abwesend") or {}))
	if daten.get("nicht_zugewiesen"):
		zeilen_html.append(
			_druck_zeile(f"<i>{h(_('Nicht zugewiesen'))}</i>", daten["nicht_zugewiesen"], tage, {})
		)
	stand = _("Gedruckt am {0} von {1}").format(
		frappe.format(now_datetime(), {"fieldtype": "Datetime"}), h(get_fullname(frappe.session.user))
	)
	legende = " · ".join(
		f'<span class="e {klasse}">{h(text)}</span>'
		for klasse, text in (
			("termin", _("Termin")),
			("arbeitspaket", _("Arbeitspaket")),
			("abwesenheit", _("Abwesenheit")),
			("konflikt", _("Konflikt")),
		)
	)
	return f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8"><title>{h(titel)}</title>
<style>
@page {{ size: A4 landscape; margin: 10mm; }}
body {{ font-family: Arial, "Liberation Sans", Helvetica, sans-serif; font-size: 9pt; color: #111; }}
h1 {{ font-size: 14pt; margin: 0 0 6px; }}
.stand {{ color: #666; font-size: 8pt; margin-bottom: 6px; }}
table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
th, td {{ border: 1px solid #999; vertical-align: top; padding: 3px 4px; }}
th {{ background: #eee; text-align: left; font-size: 9pt; }}
th.frei, td.frei {{ background: #f3e6c8; }}
th.we, td.we {{ background: #f4f4f4; }}
th.name, td.name {{ width: 11%; font-weight: bold; }}
td.abwesend {{ background: #e9e9e9; }}
.feiertag {{ font-weight: normal; font-size: 8pt; color: #7a5a00; }}
.e {{ display: block; margin: 0 0 3px; padding: 2px 3px; border-left: 3px solid #999; background: #fafafa; page-break-inside: avoid; }}
.e.termin {{ border-color: #2a6fdb; }}
.e.arbeitspaket {{ border-color: #2e9e5b; }}
.e.abwesenheit {{ border-color: #777; background: #ededed; }}
.e.konflikt {{ border-color: #d33; background: #fde9e9; }}
.zeit {{ font-weight: bold; }}
.klein {{ color: #444; font-size: 8pt; }}
.legende {{ margin-top: 6px; font-size: 8pt; }}
.legende .e {{ display: inline-block; margin: 0 4px 0 0; padding: 1px 4px; }}
</style></head><body>
<h1>{h(titel)}</h1>
<div class="stand">{stand}</div>
<table><thead><tr><th class="name">{h(_("Mitarbeiter"))}</th>{kopf}</tr></thead>
<tbody>{"".join(zeilen_html)}</tbody></table>
<div class="legende">{legende}</div>
</body></html>"""


def _druck_zeile(name_html: str, eintraege: list[dict], tage: list[dict], abwesend: dict) -> str:
	h = escape_html
	zellen = []
	for t in tage:
		klassen = []
		if t["feiertag"]:
			klassen.append("frei")
		if t["wochenende"]:
			klassen.append("we")
		if t["datum"] in abwesend:
			klassen.append("abwesend")
		teile = []
		for e in sorted(eintraege, key=lambda x: (x["von"], x["bis"])):
			for s in e["segmente"]:
				if s["datum"] != t["datum"]:
					continue
				klasse = {
					ART_TERMIN: "termin",
					ART_ARBEITSPAKET: "arbeitspaket",
					ART_ABWESENHEIT: "abwesenheit",
				}.get(e["art"], "")
				if e["konflikte"]:
					klasse += " konflikt"
				zeit = h(_("ganztags")) if s["ganztags"] else f"{h(s['von'])}\u2013{h(s['bis'])}"
				zeilen = [f'<span class="zeit">{zeit}</span> {h(e["titel"])}']
				klein = [x for x in (e.get("projekt_name"), e.get("ort"), e.get("fahrzeug_name")) if x]
				if klein:
					zeilen.append(f'<div class="klein">{" · ".join(h(x) for x in klein)}</div>')
				if e["konflikte"]:
					zeilen.append(f'<div class="klein">{h(e["konflikte"][0])}</div>')
				teile.append(f'<div class="e {klasse}">{"".join(zeilen)}</div>')
		zellen.append(f'<td class="{" ".join(klassen)}">{"".join(teile)}</td>')
	return f'<tr><td class="name">{name_html}</td>{"".join(zellen)}</tr>'


@frappe.whitelist()
def drucken(von=None, wochenende=0, projekt=None):
	"""Wochenplan als PDF (Querformat) herunterladen - Büro/Meister/System Manager."""
	pruefe_planer()
	from frappe.utils.pdf import get_pdf

	daten = tafel_berechnen(von, ANSICHT_WOCHE, wochenende, projekt or None, None)
	html = druck_html(daten)
	frappe.local.response.filename = f"Wochenplan_{daten['von']}.pdf"
	frappe.local.response.filecontent = get_pdf(
		html,
		{
			"orientation": "Landscape",
			"page-size": "A4",
			"margin-top": "10mm",
			"margin-bottom": "10mm",
			"margin-left": "10mm",
			"margin-right": "10mm",
		},
	)
	frappe.local.response.type = "pdf"
