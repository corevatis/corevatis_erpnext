"""Terminplanung der Besichtigung (Durchstich D14): Wunschtermin des Kunden, Verfügbarkeit der
Meister, Fahrzeit, Vorschläge und Doppelbuchungsschutz.

- **Ressourcen** sind die aktiven Systembenutzer mit der Rolle Meister (``crm.termin._meister``);
  die Anzahl ergibt sich aus den Stammdaten, nicht aus einer Einstellung.
- **Belegt** ist ein Meister durch Termine (Event: ihm zugewiesen, von ihm angelegt oder mit seinem
  Mitarbeiter/Benutzer als Teilnehmer), Arbeitspakete (Task mit Zuweisung, Zeitraum wie im
  Wochenplan 5.2), Abwesenheiten (Doctype ``Abwesenheit``: Urlaub, Krank, Schulung, Sonstiges -
  Plantafel P1.4) und die Feiertage der Firma; frei ist er innerhalb der Arbeitszeit (Arbeitsbeginn,
  Tagesarbeitszeit, Mo-Fr) aus „Corevatis Planung Einstellungen“.
- **Fahrzeit** von der vorigen Belegung (bzw. der Firma am Tagesbeginn) zum Objekt und weiter zur
  nächsten (bzw. zurück zur Firma): Luftlinie mal Umwegfaktor durch Geschwindigkeit, Koordinaten über
  OpenStreetMap mit Zwischenspeicher (``planung.geokodierung``); ohne Koordinaten Standardfahrzeit
  („Fahrzeit geschätzt“). Die Rechenregeln stehen in ``planung.slotrechner`` (ohne Frappe).
- **Vorschläge** (``vorschlaege``): freie Slots je Meister in den Wunschfenstern des Kunden und in den
  nächsten Arbeitstagen, bewertet nach Wunsch getroffen → früh → wenig Fahrt.
- **Doppelbuchungsschutz** (``konflikte``): ``crm.termin.termin_anlegen`` lehnt einen Termin ab, der
  sich mit Belegungen des Meisters (plus Fahrzeit und Puffer) überschneidet - ``TerminKonflikt`` mit
  dem Grund in Klartext; das Büro kann mit „Trotzdem anlegen“ übersteuern (Vermerk am Termin).

Rechte: Rollen Büro/Meister/System Manager und Leserecht auf die Anfrage. Orte fremder Anfragen
erscheinen als PLZ/Ort (wie der Kalender 1.3); die Straße nur, wenn der Benutzer die Anfrage lesen
darf. Kundennamen fremder Anfragen nie.
"""

from __future__ import annotations

import datetime as dt
import json

import frappe
from frappe import _
from frappe.utils import (
	add_days,
	cint,
	escape_html,
	flt,
	get_datetime,
	get_fullname,
	getdate,
	now_datetime,
	today,
)

from corevatis_erpnext.crm import termin as crm_termin
from corevatis_erpnext.planung import abwesenheit, geokodierung, slotrechner
from corevatis_erpnext.planung.einstellungen import einstellungen
from corevatis_erpnext.planung.slotrechner import (
	ART_ABWESENHEIT,
	ART_ARBEITSPAKET,
	ART_FEIERTAG,
	ART_FIRMA,
	ART_TERMIN,
	EIN_TAG,
	UNBEKANNT,
	Belegung,
	Fahrparameter,
	Fahrt,
	Ort,
	Slot,
)

WUNSCH_FELDER = (("cv_wunsch1_datum", "cv_wunsch1_fenster"), ("cv_wunsch2_datum", "cv_wunsch2_fenster"))
FELD_SCHNELL = "cv_wunsch_schnell"
WOCHENTAGE_KURZ = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
ARBEITSTAGE_VORAUS = 10
RASTER_MINUTEN = 30
MAX_VORSCHLAEGE = 10
MAX_JE_MEISTER_UND_TAG = 2
VORLAUF_HEUTE_MINUTEN = 60
TASK_OFFEN = ("Open", "Working", "Pending Review", "Overdue")
EVENT_STANDARD_MINUTEN = 60


# Exception beim Anlegen (Definition in crm.termin, damit Sync-Handler sie ohne Umweg importieren)
TerminKonflikt = crm_termin.TerminKonflikt


class Kontext:
	"""Einstellungen und Geokodierungs-Budget eines Aufrufs."""

	def __init__(self, e=None) -> None:
		self.e = e or einstellungen()
		self.p = parameter(self.e)
		self.aktiv = not cint(self.e.geokodierung_aus)
		self.budget = geokodierung.Budget()
		self.adressen: dict[tuple[str, str], tuple[str, str | None]] = {}

	def ort(self, adresse) -> Ort:
		return geokodierung.koordinaten(adresse, self.budget, self.aktiv)


# --- Einstellungen --------------------------------------------------------------------------


def parameter(e=None) -> Fahrparameter:
	e = e or einstellungen()
	return Fahrparameter(
		umwegfaktor=flt(e.fahrzeit_umwegfaktor),
		kmh=flt(e.fahrzeit_kmh),
		minimum=cint(e.fahrzeit_minimum),
		standard=cint(e.fahrzeit_standard),
		puffer=cint(e.fahrzeit_puffer),
	)


def arbeitsbeginn(e) -> dt.time:
	from frappe.utils import to_timedelta

	try:
		return (dt.datetime.min + to_timedelta(e.arbeitsbeginn)).time()
	except (TypeError, ValueError):
		return dt.time(7)


def _dauer(dauer, e) -> int:
	if dauer is None or (isinstance(dauer, str) and not dauer.strip()):
		return cint(e.besichtigung_dauer) or 60
	minuten = crm_termin._dauer(dauer)
	if not crm_termin.DAUER_MIN <= minuten <= crm_termin.DAUER_MAX:
		frappe.throw(
			_("Die Dauer muss zwischen {0} und {1} Minuten liegen.").format(
				crm_termin.DAUER_MIN, crm_termin.DAUER_MAX
			)
		)
	return minuten


# --- Wunschtermin ---------------------------------------------------------------------------


def wunsch(lead) -> dict:
	"""Wunsch des Kunden aus den Lead-Feldern (Document oder dict): ``termine`` [(Datum, Fenster)],
	``schnell``."""
	termine = []
	for datum_feld, fenster_feld in WUNSCH_FELDER:
		datum = lead.get(datum_feld)
		if not datum:
			continue
		try:
			tag = getdate(datum)
		except Exception:
			continue
		fenster = lead.get(fenster_feld) or "ganztags"
		if fenster not in slotrechner.FENSTER:
			fenster = "ganztags"
		termine.append((tag, fenster))
	return {"termine": termine, "schnell": bool(cint(lead.get(FELD_SCHNELL)))}


def tag_text(tag: dt.date) -> str:
	return f"{WOCHENTAGE_KURZ[tag.weekday()]} {tag:%d.%m.}"


def wunsch_text(lead) -> str:
	"""„Di 04.11. vormittags, sonst Mi 05.11. ganztags“, „schnellstmöglich“ oder leer."""
	w = wunsch(lead)
	teile = [f"{tag_text(tag)} {_(fenster)}" for tag, fenster in w["termine"]]
	text = _(", sonst ").join(teile)
	if w["schnell"]:
		text = text + _(", sonst schnellstmöglich") if text else _("schnellstmöglich")
	return text


# --- Orte -----------------------------------------------------------------------------------


def firmen_adresse(firma: str | None) -> str:
	"""Anschrift der Firma (erste mit ihr verknüpfte Adresse) als Text."""
	if not firma:
		return ""
	namen = frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": "Company", "link_name": firma, "parenttype": "Address"},
		pluck="parent",
		order_by="creation asc",
	)
	for name in namen:
		text = crm_termin._adresse_text(name)
		if text:
			return text
	return ""


def lead_adresse(doc) -> str:
	return crm_termin.objekt_adresse(doc)


def _lead_adresse_text(name: str, ctx: Kontext) -> str:
	key = ("Lead", name)
	if key not in ctx.adressen:
		try:
			ctx.adressen[key] = (lead_adresse(frappe.get_doc("Lead", name)), name)
		except frappe.DoesNotExistError:
			ctx.adressen[key] = ("", name)
	return ctx.adressen[key][0]


def _projekt_adresse(projekt: str | None, ctx: Kontext) -> tuple[str, str | None]:
	"""(Adresse, Lead) des Objekts eines Projekts: Anfrage des Projekts, sonst Kundenanschrift des
	Auftrags, sonst der Projektname („<Objektadresse> - <Verfahren>“, 4.4)."""
	if not projekt:
		return "", None
	key = ("Project", projekt)
	if key in ctx.adressen:
		return ctx.adressen[key]
	felder = [
		f for f in ("cv_lead", "sales_order", "project_name") if frappe.get_meta("Project").has_field(f)
	]
	p = frappe.db.get_value("Project", projekt, felder, as_dict=True) or frappe._dict()
	adresse, lead = "", None
	if p.get("cv_lead") and frappe.db.exists("Lead", p.cv_lead):
		lead = p.cv_lead
		adresse = _lead_adresse_text(lead, ctx)
	if not adresse and p.get("sales_order"):
		anschrift = frappe.db.get_value("Sales Order", p.sales_order, "customer_address")
		if anschrift:
			adresse = crm_termin._adresse_text(anschrift)
	if not adresse and p.get("project_name"):
		kopf = p.project_name.split(" \u2013 ")[0].strip()
		adresse = kopf if crm_termin._PLZ_ORT.search(kopf) else ""
	ctx.adressen[key] = (adresse, lead)
	return adresse, lead


# --- Belegungen -----------------------------------------------------------------------------


def _feiertage(firma: str | None, von: dt.date, bis: dt.date) -> dict[dt.date, str]:
	"""Feiertage der Firma (``Company.default_holiday_list``) im Zeitraum mit Bezeichnung."""
	if not firma:
		return {}
	liste = frappe.db.get_value("Company", firma, "default_holiday_list")
	if not liste:
		return {}
	return {
		getdate(h.holiday_date): h.description or _("Feiertag")
		for h in frappe.get_all(
			"Holiday",
			filters={"parent": liste, "holiday_date": ["between", [von, bis]]},
			fields=["holiday_date", "description"],
		)
	}


def _event_namen(user: str, von: dt.datetime, bis: dt.datetime) -> set[str]:
	namen = set(
		frappe.get_all(
			"ToDo",
			filters={"reference_type": "Event", "allocated_to": user, "status": "Open"},
			pluck="reference_name",
		)
	)
	namen |= set(
		frappe.get_all(
			"Event",
			filters={"owner": user, "status": "Open", "starts_on": ["<=", bis], "ends_on": [">=", von]},
			pluck="name",
		)
	)
	namen |= set(
		frappe.get_all(
			"Event",
			filters={"owner": user, "status": "Open", "starts_on": ["between", [von - EIN_TAG, bis]]},
			pluck="name",
		)
	)
	teilnehmer = [("User", user)]
	employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if employee:
		teilnehmer.append(("Employee", employee))
	for doctype, name in teilnehmer:
		namen |= set(
			frappe.get_all(
				"Event Participants",
				filters={"parenttype": "Event", "reference_doctype": doctype, "reference_docname": name},
				pluck="parent",
			)
		)
	return {n for n in namen if n}


def _event_belegungen(user: str, von: dt.datetime, bis: dt.datetime, ctx: Kontext) -> list[Belegung]:
	namen = _event_namen(user, von, bis)
	if not namen:
		return []
	events = frappe.get_all(
		"Event",
		filters={"name": ["in", list(namen)], "status": "Open", "starts_on": ["<=", bis]},
		fields=["name", "subject", "starts_on", "ends_on", "all_day", "location"],
		order_by="starts_on asc",
	)
	if not events:
		return []
	leads = {
		p.parent: p.reference_docname
		for p in frappe.get_all(
			"Event Participants",
			filters={"parent": ["in", [e.name for e in events]], "reference_doctype": "Lead"},
			fields=["parent", "reference_docname"],
			order_by="idx asc",
		)
	}
	ergebnis = []
	for e in events:
		start = get_datetime(e.starts_on)
		ende = get_datetime(e.ends_on) if e.ends_on else start + dt.timedelta(minutes=EVENT_STANDARD_MINUTEN)
		if cint(e.all_day):
			start = dt.datetime.combine(start.date(), dt.time.min)
			ende = dt.datetime.combine(max(ende, start).date(), dt.time.min) + EIN_TAG
		ende = max(ende, start + dt.timedelta(minutes=1))
		if ende < von:
			continue
		lead = leads.get(e.name)
		adresse = _lead_adresse_text(lead, ctx) if lead else ""
		ort = ctx.ort(adresse or e.location or "")
		ergebnis.append(Belegung(start, ende, ort, ART_TERMIN, e.subject or "", "Event", e.name, lead))
	return ergebnis


def _task_belegungen(user: str, von: dt.datetime, bis: dt.datetime, ctx: Kontext) -> list[Belegung]:
	namen = frappe.get_all(
		"ToDo",
		filters={"reference_type": "Task", "allocated_to": user, "status": "Open"},
		pluck="reference_name",
	)
	namen = [n for n in set(namen) if n]
	if not namen:
		return []
	tasks = frappe.get_all(
		"Task",
		filters={"name": ["in", namen], "status": ["in", TASK_OFFEN], "exp_start_date": ["<=", bis]},
		fields=["name", "subject", "project", "exp_start_date", "exp_end_date"],
		order_by="exp_start_date asc",
	)
	ergebnis = []
	for t in tasks:
		b = task_belegung(t, ctx)
		if b is not None and b.bis >= von:
			ergebnis.append(b)
	return ergebnis


def task_belegung(t, ctx: Kontext) -> Belegung | None:
	"""Arbeitspaket (Zeile mit ``name``, ``subject``, ``project``, ``exp_start_date``, ``exp_end_date``)
	als Belegung. Beginn nur als Datum → Arbeitsbeginn; Ende nur als Datum → Ende des Arbeitstags (der
	ganze Tag gehört dazu, wie im Wochenplan 5.2). Ohne Beginn ``None``. Auch für die Plantafel (P1.4)."""
	if not t.exp_start_date:
		return None
	beginn = arbeitsbeginn(ctx.e)
	tagesstunden = flt(ctx.e.tagesarbeitszeit) or 8.0
	start = get_datetime(t.exp_start_date)
	ende = get_datetime(t.exp_end_date) if t.exp_end_date else start
	if start.time() == dt.time.min:
		start = dt.datetime.combine(start.date(), beginn)
	if ende.time() == dt.time.min:
		ende = slotrechner.arbeitszeit(ende.date(), beginn, tagesstunden)[1]
	ende = max(ende, start + dt.timedelta(minutes=1))
	adresse, lead = _projekt_adresse(t.project, ctx)
	return Belegung(start, ende, ctx.ort(adresse), ART_ARBEITSPAKET, t.subject or "", "Task", t.name, lead)


def belegungen(
	user: str, von: dt.date, bis: dt.date, ctx: Kontext, firma: str | None = None
) -> list[Belegung]:
	"""Alle Belegungen des Meisters im Zeitraum (Termine, Arbeitspakete, Abwesenheiten, Feiertage),
	sortiert."""
	von_dt = dt.datetime.combine(getdate(von), dt.time.min)
	bis_dt = dt.datetime.combine(getdate(bis), dt.time.min) + EIN_TAG
	liste = _event_belegungen(user, von_dt, bis_dt, ctx) + _task_belegungen(user, von_dt, bis_dt, ctx)
	liste += abwesenheit.belegungen(user, getdate(von), getdate(bis))
	for tag, bezeichnung in _feiertage(firma, getdate(von), getdate(bis)).items():
		anfang = dt.datetime.combine(tag, dt.time.min)
		liste.append(Belegung(anfang, anfang + EIN_TAG, UNBEKANNT, ART_FEIERTAG, bezeichnung))
	liste.sort(key=lambda b: (b.von, b.bis))
	return liste


# --- Anzeige (Rechte) -----------------------------------------------------------------------


def _lesbar(doctype: str, name: str | None) -> bool:
	if not name:
		return False
	try:
		return bool(frappe.has_permission(doctype, "read", doc=name))
	except (frappe.DoesNotExistError, frappe.PermissionError):
		return False


def belegung_ort(b: Belegung) -> str:
	"""Ort einer Belegung für die Anzeige: volle Anschrift nur mit Leserecht auf die Anfrage bzw. den
	Beleg, sonst PLZ/Ort (wie ``Event.location`` im Kalender 1.3)."""
	text = b.ort.text or ""
	if not text:
		return ""
	lesbar = _lesbar("Lead", b.lead) if b.lead else _lesbar(b.doctype, b.name) if b.doctype else False
	return text if lesbar else (crm_termin._ort(text) or "")


def fahrt_ort(f: Fahrt) -> str:
	if f.art == ART_FIRMA:
		return _("Firma")
	if f.belegung is not None:
		return belegung_ort(f.belegung)
	return crm_termin._ort(f.ort.text or "")


def _fahrt_dict(f: Fahrt | None) -> dict | None:
	if f is None:
		return None
	return {"minuten": f.minuten, "geschaetzt": f.geschaetzt, "ort": fahrt_ort(f), "art": f.art}


def _vorschlag_text(s: Slot, meister_name: str) -> str:
	teile = [f"{tag_text(s.beginn.date())} {s.beginn:%H:%M}\u2013{s.ende:%H:%M}", meister_name]
	if s.hin:
		fahrt = _("{0} min Fahrt von {1}").format(s.hin.minuten, fahrt_ort(s.hin) or _("unbekannt"))
		if s.hin.geschaetzt:
			fahrt += " " + _("(geschätzt)")
		if s.weiter:
			fahrt += ", " + _("danach {0} min nach {1}").format(
				s.weiter.minuten, fahrt_ort(s.weiter) or _("unbekannt")
			)
			if s.weiter.geschaetzt:
				fahrt += " " + _("(geschätzt)")
		teile.append(fahrt)
	return " · ".join(teile)


def _vorschlag(s: Slot, m: dict) -> dict:
	name = m.get("full_name") or m["name"]
	return {
		"beginn": s.beginn.strftime("%Y-%m-%d %H:%M:%S"),
		"ende": s.ende.strftime("%Y-%m-%d %H:%M:%S"),
		"datum": str(s.beginn.date()),
		"meister": m["name"],
		"meister_name": name,
		"wunsch": s.wunsch,
		"fahrt_hin": _fahrt_dict(s.hin),
		"fahrt_weiter": _fahrt_dict(s.weiter),
		"geschaetzt": s.geschaetzt,
		"text": _vorschlag_text(s, name),
	}


# --- Vorschläge -----------------------------------------------------------------------------


def arbeitstage(ab: dt.date, anzahl: int, frei: set) -> list[dt.date]:
	tage = []
	tag = ab
	for _i in range(anzahl * 5 + 30):
		if len(tage) >= anzahl:
			break
		if tag.weekday() < 5 and tag not in frei:
			tage.append(tag)
		tag += EIN_TAG
	return tage


def _meister_auswahl(meister) -> list[dict]:
	alle = crm_termin._meister()
	if not meister:
		return alle
	if isinstance(meister, str):
		try:
			gewuenscht = json.loads(meister) if meister.strip().startswith("[") else [meister]
		except ValueError:
			gewuenscht = [meister]
	else:
		gewuenscht = list(meister)
	gewuenscht = {str(m).strip() for m in gewuenscht if m}
	auswahl = [m for m in alle if m["name"] in gewuenscht]
	if gewuenscht and not auswahl:
		frappe.throw(_("Kein aktiver Benutzer mit der Rolle Meister ausgewählt."))
	return auswahl


def _ab_zeit(jetzt: dt.datetime) -> dt.datetime:
	"""Frühester Beginn heute: in einer Stunde, auf das Raster aufgerundet."""
	ab = jetzt + dt.timedelta(minutes=VORLAUF_HEUTE_MINUTEN)
	rest = (ab.minute % RASTER_MINUTEN, ab.second, ab.microsecond)
	if any(rest):
		ab = ab.replace(second=0, microsecond=0) + dt.timedelta(
			minutes=RASTER_MINUTEN - ab.minute % RASTER_MINUTEN
		)
	return ab


@frappe.whitelist()
def vorschlaege(lead: str, dauer=None, ab=None, bis=None, meister=None) -> dict:
	"""Freie Termine für die Besichtigung der Anfrage (Dialog „Termin anlegen“, REST): nur
	Büro/Meister/System Manager mit Leserecht auf die Anfrage; Berechnung in ``berechne_vorschlaege``."""
	crm_termin._pruefe_rolle()
	doc = frappe.get_doc("Lead", lead)
	doc.check_permission("read")
	return berechne_vorschlaege(doc, dauer, ab, bis, meister)


def berechne_vorschlaege(lead, dauer=None, ab=None, bis=None, meister=None) -> dict:
	"""Freie Termine für die Besichtigung der Anfrage ``lead`` (Name oder Document): in den
	Wunschfenstern des Kunden und in den nächsten Arbeitstagen, je Meister, mit Fahrzeiten.

	Frei aufrufbare Python-Funktion ohne Rechteprüfung (für Sync-Handler der App, ``bench execute``):
	der Aufrufer prüft Rolle und Leserecht. ``dauer`` in Minuten (leer = Einstellung), ``ab``/``bis``
	Datum, ``meister`` Benutzer, Liste oder JSON-Liste schränkt die Auswahl ein. Rückgabe wie
	``vorschlaege``: ``dauer``, ``wunsch``, ``vorschlaege`` (Beginn, Ende, Meister, Wunsch, Fahrt hin/weiter,
	Text), ``hinweise``, ``fahrzeit_geschaetzt``, ``zeitraum``.
	"""
	doc = lead if hasattr(lead, "get") and hasattr(lead, "name") else frappe.get_doc("Lead", lead)

	ctx = Kontext()
	minuten = _dauer(dauer, ctx.e)
	ziel = ctx.ort(lead_adresse(doc))
	firma_name = crm_termin.firma(doc)
	firma = ctx.ort(firmen_adresse(firma_name))

	heute = getdate(today())
	jetzt = now_datetime()
	start_tag = max(getdate(ab), heute) if ab else heute
	ende_tag = getdate(bis) if bis else None
	frei = _feiertage(firma_name, start_tag, add_days(start_tag, 120))
	tage = arbeitstage(start_tag, ARBEITSTAGE_VORAUS, set(frei))
	if ende_tag:
		tage = [t for t in tage if t <= ende_tag]

	hinweise: list[str] = []
	w = wunsch(doc)
	wunsch_tage: list[tuple[dt.date, str]] = []
	for tag, fenster in w["termine"]:
		if tag < heute:
			hinweise.append(_("Der Wunschtermin {0} liegt in der Vergangenheit.").format(tag_text(tag)))
		elif tag.weekday() >= 5:
			hinweise.append(
				_("Der Wunschtermin {0} fällt auf ein Wochenende (keine Arbeitszeit).").format(tag_text(tag))
			)
		elif tag in frei:
			hinweise.append(
				_("Der Wunschtermin {0} ist ein Feiertag ({1}).").format(tag_text(tag), frei[tag])
			)
		elif ende_tag and tag > ende_tag:
			continue
		else:
			wunsch_tage.append((tag, fenster))
	alle_tage = sorted({t for t, _f in wunsch_tage} | set(tage))
	meister_liste = _meister_auswahl(meister)
	if not alle_tage or not meister_liste:
		if not meister_liste:
			hinweise.append(_("Es gibt keinen aktiven Benutzer mit der Rolle Meister."))
		return _antwort(doc, minuten, w, [], hinweise, ziel, ctx, None)

	ab_zeit = _ab_zeit(jetzt)
	beginn = arbeitsbeginn(ctx.e)
	tagesstunden = flt(ctx.e.tagesarbeitszeit) or 8.0
	gefunden: list[tuple[Slot, dict]] = []
	for m in meister_liste:
		bel = belegungen(m["name"], alle_tage[0], alle_tage[-1], ctx, firma_name)
		for tag in alle_tage:
			fenster_liste = [f for t, f in wunsch_tage if t == tag] or [None]
			for fenster in fenster_liste:
				for s in slotrechner.freie_slots(
					tag,
					fenster,
					minuten,
					ziel,
					bel,
					ctx.p,
					firma,
					beginn,
					tagesstunden,
					RASTER_MINUTEN,
					wunsch=fenster is not None,
					ab=ab_zeit,
				):
					gefunden.append((s, m))

	gefunden.sort(key=lambda sm: (sm[0].bewertung(), sm[1]["name"]))
	auswahl: list[dict] = []
	zaehler: dict[tuple[str, dt.date], int] = {}
	gesehen: set[tuple[str, dt.datetime]] = set()
	for s, m in gefunden:
		key = (m["name"], s.beginn.date())
		if (m["name"], s.beginn) in gesehen or zaehler.get(key, 0) >= MAX_JE_MEISTER_UND_TAG:
			continue
		gesehen.add((m["name"], s.beginn))
		zaehler[key] = zaehler.get(key, 0) + 1
		auswahl.append(_vorschlag(s, m))
		if len(auswahl) >= MAX_VORSCHLAEGE:
			break

	if wunsch_tage and not any(v["wunsch"] for v in auswahl):
		hinweise.append(_("Im Wunschfenster des Kunden ist kein Meister frei."))
	if not auswahl:
		hinweise.append(
			_("Keine freien Zeiten in den nächsten {0} Arbeitstagen gefunden.").format(ARBEITSTAGE_VORAUS)
		)
	return _antwort(doc, minuten, w, auswahl, hinweise, ziel, ctx, (alle_tage[0], alle_tage[-1]))


def _antwort(doc, minuten, w, auswahl, hinweise, ziel, ctx, zeitraum) -> dict:
	if not ziel.bekannt:
		hinweise.append(
			_("Fahrzeit geschätzt: Die Objektadresse „{0}“ wurde auf der Karte nicht gefunden.").format(
				ziel.text or _("(leer)")
			)
			if ctx.aktiv and ziel.text
			else _("Fahrzeit geschätzt (Standardfahrzeit): {0}.").format(
				_("Geokodierung ist ausgeschaltet") if not ctx.aktiv else _("keine Objektadresse")
			)
		)
	if ctx.budget.fehlgeschlagen:
		hinweise.append(
			_(
				"Fahrzeit geschätzt: Die Geokodierung ist gerade nicht möglich (OpenStreetMap oder Zwischenspeicher)."
			)
		)
	return {
		"lead": doc.name,
		"dauer": minuten,
		"wunsch": {
			"text": wunsch_text(doc),
			"schnell": w["schnell"],
			"termine": [{"datum": str(t), "fenster": f} for t, f in w["termine"]],
		},
		"vorschlaege": auswahl,
		"hinweise": list(dict.fromkeys(hinweise)),
		"fahrzeit_geschaetzt": (not ziel.bekannt) or any(v["geschaetzt"] for v in auswahl),
		"zeitraum": {"von": str(zeitraum[0]), "bis": str(zeitraum[1])} if zeitraum else None,
	}


# --- Doppelbuchungsschutz -------------------------------------------------------------------


def _zeit_text(b: Belegung) -> str:
	if b.von.date() == (b.bis - dt.timedelta(microseconds=1)).date():
		return f"{b.von:%H:%M}\u2013{b.bis:%H:%M}"
	return f"{tag_text(b.von.date())} {b.von:%H:%M} \u2013 {tag_text(b.bis.date())} {b.bis:%H:%M}"


def _zeit_text_tage(b: Belegung) -> str:
	"""Zeitraum einer ganztägigen Belegung als Tage, sonst Tag mit Uhrzeiten."""
	letzter = (b.bis - dt.timedelta(microseconds=1)).date()
	if b.von.time() == dt.time.min and b.bis.time() == dt.time.min:
		if b.von.date() == letzter:
			return tag_text(b.von.date())
		return f"{tag_text(b.von.date())} \u2013 {tag_text(letzter)}"
	return f"{tag_text(b.von.date())} {b.von:%H:%M}\u2013{b.bis:%H:%M}"


def konflikt_text(meister_name: str, b: Belegung, fahrt: int | None) -> str:
	"""„Martin Meister ist 09:00-10:30 bei Mühlenweg 5, 25 min Fahrt.“"""
	if b.art == ART_FEIERTAG:
		return _("{0} ist Feiertag ({1}).").format(f"{tag_text(b.von.date())}{b.von:%Y}", b.titel)
	if b.art == ART_ABWESENHEIT:
		# Abwesenheit (Urlaub, Krank, …): ganztags „Di 04.11.“, sonst mit Uhrzeiten; die Art nur, wenn
		# der Aufrufer sie sehen darf (abwesenheit.belegungen setzt sonst keinen Titel)
		if b.titel:
			return _("{0} ist {1} abwesend ({2}).").format(meister_name, _zeit_text_tage(b), b.titel)
		return _("{0} ist {1} abwesend.").format(meister_name, _zeit_text_tage(b))
	ort = belegung_ort(b)
	if ort:
		text = _("{0} ist {1} bei {2}").format(meister_name, _zeit_text(b), ort)
	else:
		text = _("{0} ist {1} belegt ({2})").format(meister_name, _zeit_text(b), _(b.art))
	if fahrt:
		text += _(", {0} min Fahrt").format(fahrt)
	return text + "."


def konflikte(doc, user: str, start: dt.datetime, ende: dt.datetime, e=None) -> list[dict]:
	"""Belegungen des Meisters, die dem Termin ``start``-``ende`` am Objekt der Anfrage im Weg stehen
	(Überschneidung oder zu wenig Zeit für Fahrt und Puffer). Tagesgrenzen werden hier nicht geprüft -
	das Büro darf bewusst außerhalb der Arbeitszeit planen."""
	ctx = Kontext(e)
	ziel = ctx.ort(lead_adresse(doc))
	firma_name = crm_termin.firma(doc)
	bel = belegungen(user, start.date() - EIN_TAG, ende.date() + EIN_TAG, ctx, firma_name)
	# der bisherige Termin derselben Anfrage wird beim Anlegen ersetzt („Termin verschieben“, 1.3)
	bel = [b for b in bel if not (b.doctype == "Event" and b.lead == doc.name)]
	tag_von = dt.datetime.combine(start.date(), dt.time.min)
	tag_bis = dt.datetime.combine(ende.date(), dt.time.min) + EIN_TAG
	des_tages = [b for b in bel if slotrechner.ueberschneidung(b.von, b.bis, tag_von, tag_bis)]
	pr = slotrechner.slot_pruefen(start, ende, ziel, des_tages, ctx.p)
	name = get_fullname(user) or user
	ergebnis = []
	for b in pr.konflikte:
		fahrt = None
		if pr.hin and pr.hin.belegung is b:
			fahrt = pr.hin.minuten
		elif pr.weiter and pr.weiter.belegung is b:
			fahrt = pr.weiter.minuten
		ergebnis.append(
			{
				"text": konflikt_text(name, b, fahrt),
				"meister": user,
				"meister_name": name,
				"art": b.art,
				"doctype": b.doctype,
				"name": b.name,
				"von": b.von.strftime("%Y-%m-%d %H:%M:%S"),
				"bis": b.bis.strftime("%Y-%m-%d %H:%M:%S"),
				"ort": belegung_ort(b),
				"fahrzeit_min": fahrt,
				"geschaetzt": bool(
					(pr.hin and pr.hin.belegung is b and pr.hin.geschaetzt)
					or (pr.weiter and pr.weiter.belegung is b and pr.weiter.geschaetzt)
				),
			}
		)
	return ergebnis


def konflikt_meldung(liste: list[dict]) -> str:
	"""HTML für den Desk (Werte escaped)."""
	zeilen = "".join(f"<li>{escape_html(k['text'])}</li>" for k in liste)
	return (
		escape_html(_("Der Termin überschneidet sich mit der Planung des Meisters:"))
		+ f"<ul>{zeilen}</ul>"
		+ escape_html(_("Anderen Vorschlag wählen oder mit „Trotzdem anlegen“ übersteuern."))
	)


def konflikt_grund(liste: list[dict]) -> str:
	"""Klartext (eine Zeile je Konflikt) für Exception, Sync-Antwort und Kommentar."""
	return _("Terminüberschneidung") + ": " + " ".join(k["text"] for k in liste)


def konflikt_werfen(liste: list[dict], meister: str) -> None:
	"""Konflikt melden: Klartext als Desk-Meldung, strukturierte Details in der Antwort
	(``konflikte``) und ``TerminKonflikt`` mit ``grund``, ``konflikte``, ``meister`` (417)."""
	frappe.local.response["konflikte"] = liste
	frappe.msgprint(konflikt_meldung(liste), title=_("Terminüberschneidung"), indicator="orange")
	raise TerminKonflikt(konflikt_grund(liste), liste, meister)


@frappe.whitelist()
def pruefen(lead: str, beginn: str, meister: str, dauer=None) -> dict:
	"""Konfliktprüfung für den Dialog „Termin anlegen“ (ohne Anlegen): Belegungen des Meisters, die
	dem Termin im Weg stehen, in Klartext. Rechte wie ``vorschlaege``."""
	crm_termin._pruefe_rolle()
	doc = frappe.get_doc("Lead", lead)
	doc.check_permission("read")
	start = crm_termin._beginn(beginn)
	e = einstellungen()
	minuten = _dauer(dauer, e)
	if meister not in {m["name"] for m in crm_termin._meister()}:
		frappe.throw(
			_("{0} ist kein aktiver Benutzer mit der Rolle Meister.").format(escape_html(str(meister)[:140]))
		)
	liste = konflikte(doc, meister, start, start + dt.timedelta(minutes=minuten), e)
	return {
		"konflikte": liste,
		"frei": not liste,
		"meister": meister,
		"meister_name": get_fullname(meister) or meister,
		"beginn": start.strftime("%Y-%m-%d %H:%M:%S"),
		"dauer": minuten,
	}
