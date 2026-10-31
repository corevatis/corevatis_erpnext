"""Meine Woche für die App (Phase 1 P1.4b, Issue Handwerker#84) - Datenquelle des Kern-Endpunkts
``corevatis.api.v1.meine_woche`` (Hook ``corevatis_woche_adapter``).

``woche(user, von, bis)`` liefert die Einsätze **eines** Benutzers je Kalendertag - dieselben Daten wie
seine Zeile in der Plantafel (``plantafel.tafel_berechnen``): Termine (Event), Arbeitspakete (Task mit
Zuweisung; mehrtägige je Tag auf die Arbeitszeit beschnitten, ``plantafel.segmente``), Abwesenheiten
(Art nur für den Betroffenen und Büro, sonst „Abwesend“), Feiertage der Firma und das Fahrzeug je
Einsatz (``cv_fahrzeug``). Dazu Überschneidungen und Fahrzeit-Hinweise wie in der Tafel.

Unterschiede zur Tafel, weil die App navigieren will: die Anschrift eines **eigenen** Arbeitspakets ist
die volle Objektadresse, sobald der Benutzer das Projekt lesen darf (in der Tafel nur mit Leserecht auf
die Anfrage, sonst PLZ/Ort); fremde Vorgänge bleiben PLZ/Ort. Keine Geokodierung aus der App
(``Kontext.aktiv = False``): Fahrzeiten stammen aus dem Cache oder sind geschätzt - das Nominatim-Budget
bleibt dem Desk. ``geaendert`` je Eintrag ist ``modified`` des Belegs (Task, Event, Abwesenheit) für die
Markierung „geändert seit dem letzten Blick“ in der App.

Rechte: der Kern prüft Flag und Anmeldung; hier darf nur der Benutzer selbst oder ein Planer (Büro,
Meister, ``auftrag.darf_planen``) die Woche eines Benutzers abrufen.
"""

from __future__ import annotations

import datetime as dt

import frappe
from frappe import _
from frappe.utils import flt, getdate

from corevatis_erpnext.planung import abwesenheit, plantafel, terminplanung
from corevatis_erpnext.planung.auftrag import darf_planen
from corevatis_erpnext.planung.einstellungen import einstellungen
from corevatis_erpnext.planung.slotrechner import (
	ART_ABWESENHEIT,
	ART_ARBEITSPAKET,
	ART_FEIERTAG,
	ART_TERMIN,
	Belegung,
)
from corevatis_erpnext.planung.wochenplan import _firma

# Arten der Tafel → Arten der App (Vertrag mit corevatis.api.woche.ARTEN)
ART_APP = {
	ART_TERMIN: "termin",
	ART_ARBEITSPAKET: "arbeitspaket",
	ART_ABWESENHEIT: "abwesenheit",
	ART_FEIERTAG: "feiertag",
}
ARTEN = ("termin", "arbeitspaket", "abwesenheit", "feiertag")
# Felder eines Eintrags (Teilmenge von corevatis.api.woche.FELDER; test_erpnext_contract im Kern)
FELDER = (
	"key",
	"art",
	"doctype",
	"name",
	"datum",
	"von",
	"bis",
	"ganztags",
	"titel",
	"untertitel",
	"projekt",
	"projekt_name",
	"lead",
	"kunde",
	"adresse",
	"fahrzeug",
	"fahrzeug_name",
	"status",
	"sollstunden",
	"konflikte",
	"hinweise",
	"lesbar",
	"geaendert",
)
MAX_TAGE = 31


def _pruefe_rechte(user: str) -> None:
	if user != frappe.session.user and not darf_planen():
		frappe.throw(_("Die Woche anderer Mitarbeiter sehen nur Büro und Meister."), frappe.PermissionError)


def _einzeilig(adresse: str | None) -> str | None:
	zeilen = [z.strip() for z in (adresse or "").splitlines() if z.strip()]
	return ", ".join(zeilen) or None


def _zeitpunkt(datum: str, minuten: int) -> str:
	tag = getdate(datum)
	if minuten >= 24 * 60:
		return f"{tag + dt.timedelta(days=1)} 00:00:00"
	return f"{tag} {minuten // 60:02d}:{minuten % 60:02d}:00"


def _geaendert(bel: list[Belegung]) -> dict[tuple[str, str], str]:
	"""``modified`` je Beleg (Task, Event, Abwesenheit), gebündelt abgefragt."""
	ergebnis: dict[tuple[str, str], str] = {}
	for doctype in ("Task", "Event", abwesenheit.DOCTYPE):
		namen = {b.name for b in bel if b.doctype == doctype and b.name}
		if not namen or not frappe.db.exists("DocType", doctype):
			continue
		for r in frappe.get_all(doctype, filters={"name": ["in", list(namen)]}, fields=["name", "modified"]):
			ergebnis[(doctype, r.name)] = str(r.modified)
	return ergebnis


def _kunden(leads: set[str]) -> dict[str, str]:
	leads = {lead for lead in leads if lead}
	if not leads:
		return {}
	return {
		r.name: r.lead_name or r.company_name or r.name
		for r in frappe.get_all(
			"Lead", filters={"name": ["in", list(leads)]}, fields=["name", "lead_name", "company_name"]
		)
	}


def _eintraege(
	b: Belegung, e: dict, d: plantafel.Details, geaendert: dict, kunden: dict[str, str]
) -> list[dict]:
	"""Ein Einsatz der Tafel als Zeilen der App - eine je Kalendertag (Segment)."""
	art = ART_APP.get(b.art)
	if not art:
		return []
	adresse = e.get("ort") or None
	if b.art == ART_ARBEITSPAKET and e.get("projekt"):
		# eigenes Arbeitspaket, Projekt lesbar: volle Objektadresse für die Navigation
		adresse = b.ort.text or adresse
	status = e.get("status")
	if b.art == ART_TERMIN:
		status = (d.events.get(b.name) or frappe._dict()).get("status")
	lead = e.get("lead")
	zeilen = []
	for s in e["segmente"]:
		zeilen.append(
			{
				"key": f"{b.doctype}:{b.name}" if b.doctype and b.name else f"{art}:{s['datum']}",
				"art": art,
				"doctype": b.doctype,
				"name": b.name,
				"datum": s["datum"],
				"von": None if s["ganztags"] else _zeitpunkt(s["datum"], s["von_min"]),
				"bis": None if s["ganztags"] else _zeitpunkt(s["datum"], s["bis_min"]),
				"ganztags": bool(s["ganztags"]),
				"titel": e.get("titel") or "",
				"untertitel": e.get("untertitel") or e.get("projekt_name") or None,
				"projekt": e.get("projekt"),
				"projekt_name": e.get("projekt_name") or None,
				"lead": lead,
				"kunde": kunden.get(lead) if lead else None,
				"adresse": _einzeilig(adresse),
				"fahrzeug": e.get("fahrzeug"),
				"fahrzeug_name": e.get("fahrzeug_name"),
				"status": status,
				"sollstunden": flt(e.get("sollstunden")) or None,
				"konflikte": list(e.get("konflikte") or []),
				"hinweise": list(e.get("hinweise") or []),
				"lesbar": bool(e.get("lesbar")),
				"geaendert": geaendert.get((b.doctype, b.name)) if b.doctype and b.name else None,
			}
		)
	return zeilen


def woche(user: str, von, bis) -> dict:
	"""Einträge des Benutzers im Zeitraum (``von``/``bis`` einschließlich, höchstens ``MAX_TAGE`` Tage)."""
	_pruefe_rechte(user)
	von_tag, bis_tag = getdate(von), getdate(bis)
	if bis_tag < von_tag or (bis_tag - von_tag).days >= MAX_TAGE:
		frappe.throw(_("Der Zeitraum darf höchstens {0} Tage umfassen.").format(MAX_TAGE))
	e = einstellungen()
	ctx = terminplanung.Kontext(e)
	ctx.aktiv = False
	firma = _firma()
	feiertage = terminplanung._feiertage(firma, von_tag, bis_tag)
	tage = {von_tag + dt.timedelta(days=n) for n in range((bis_tag - von_tag).days + 1)}
	arbeitszeit = plantafel._arbeitszeit_minuten(e)

	bel = [b for b in terminplanung.belegungen(user, von_tag, bis_tag, ctx, firma) if b.art != ART_FEIERTAG]
	d = plantafel.Details()
	d.laden(
		{b.name for b in bel if b.doctype == "Task" and b.name},
		{b.name for b in bel if b.doctype == "Event" and b.name},
	)
	paare: list[tuple[Belegung, dict]] = []
	for b in bel:
		eintrag = plantafel._eintrag(b, d, tage, user, arbeitszeit)
		if eintrag["segmente"]:
			paare.append((b, eintrag))
	plantafel._ueberschneidungen(paare)
	plantafel._fahrzeit_hinweise(paare, ctx)

	geaendert = _geaendert(bel)
	kunden = _kunden({e_["lead"] for _b, e_ in paare if e_.get("lead")})
	eintraege: list[dict] = []
	for b, eintrag in paare:
		eintraege += _eintraege(b, eintrag, d, geaendert, kunden)
	for tag, bezeichnung in sorted(feiertage.items()):
		eintraege.append(
			{
				"key": f"feiertag:{tag}",
				"art": "feiertag",
				"datum": str(tag),
				"ganztags": True,
				"titel": bezeichnung,
				"konflikte": [],
				"hinweise": [],
				"lesbar": True,
			}
		)
	return {
		"eintraege": eintraege,
		"feiertage": {str(tag): bezeichnung for tag, bezeichnung in feiertage.items()},
		"arbeitsbeginn": str(e.arbeitsbeginn)[:5],
		"tagesarbeitszeit": flt(e.tagesarbeitszeit),
		"mitarbeiter": frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name"),
	}
