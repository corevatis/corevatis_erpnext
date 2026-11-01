"""Bautagebuch (Phase 3 P3.2, Handwerker#94) im ERPNext-Adapter.

Das Bautagebuch selbst (Doctype ``Bautagebuch Tag``, Berechnung, Wetter, Druck, Mappe) liegt im Kern
``corevatis`` (``corevatis.bautagebuch``). Hier steckt nur das ERPNext-Wissen, das der Kern über zwei
Hooks abruft (kein Import über die Lizenzgrenze; Vertragstest
``corevatis/tests/test_erpnext_contract.py::test_bautagebuch``):

- ``corevatis_anwesenheit_adapter`` → ``anwesenheit(projekt, von, bis)``: Zeilen ``Timesheet Detail``
  des Projekts (nicht stornierte Timesheets, Beginn im Zeitraum) je Mitarbeiter mit Benutzer
  (``Employee.user_id``); ``projekte_mit_zeiten(von, bis)``: Projekte mit Zeitbuchung (nächtlicher Job).
  Welche Tätigkeiten als Anwesenheit zählen (ohne Pause, Fahrzeit), entscheidet der Kern.
- ``corevatis_koordinaten_adapter`` → ``koordinaten(projekt)``: Geokoordinate des Objekts (Anschrift
  aus ``Project.cv_objekt``) bzw. der Projektadresse (wie die Plantafel, ``terminplanung``) über die
  Geokodierung (``planung.geokodierung``: Zwischenspeicher „Geokoordinate“, höchstens eine neue
  Nominatim-Anfrage je Aufruf, Schalter „Geokodierung aus“ gilt). Nur gefragt, wenn der Kern das
  automatische Wetter eingeschaltet hat; der Kern rundet auf zwei Nachkommastellen.

Dazu der Stand für die Prozessleiste (``stand``, Pille „Bautagebuch: n Tage“) aus dem Kern per
``frappe.get_attr``; ohne Kern ``None``.
"""

from __future__ import annotations

import datetime as _dt

import frappe
from frappe.utils import flt, getdate

KERN_APP = "corevatis"
KERN_DOCTYPE = "Bautagebuch Tag"
KERN_STAND = "corevatis.bautagebuch.stand"
OBJEKT = "Objekt"
MAX_TAGE = 366


def kern_da() -> bool:
	return KERN_APP in frappe.get_installed_apps() and bool(frappe.db.exists("DocType", KERN_DOCTYPE))


def _kern(pfad: str):
	return frappe.get_attr(pfad)


def _grenzen(von, bis) -> tuple[_dt.datetime, _dt.datetime]:
	von, bis = getdate(von), getdate(bis)
	if bis < von or (bis - von).days + 1 > MAX_TAGE:
		raise ValueError("Zeitraum ungültig")
	return _dt.datetime.combine(von, _dt.time()), _dt.datetime.combine(bis, _dt.time()) + _dt.timedelta(
		days=1
	)


def _zeilen(von, bis, projekt: str | None = None):
	beginn, ende = _grenzen(von, bis)
	ts = frappe.qb.DocType("Timesheet")
	td = frappe.qb.DocType("Timesheet Detail")
	bedingung = (
		(ts.docstatus < 2)
		& (td.parenttype == "Timesheet")
		& (td.from_time >= beginn)
		& (td.from_time < ende)
		& td.project.isnotnull()
		& (td.project != "")
	)
	if projekt is not None:
		bedingung &= td.project == projekt
	return frappe.qb.from_(td).join(ts).on(td.parent == ts.name).where(bedingung), ts, td


def anwesenheit(projekt: str, von: _dt.date, bis: _dt.date) -> list[dict]:
	"""Zeitzeilen des Projekts im Zeitraum (Vertrag mit ``corevatis.bautagebuch.berechnung``):
	``datum``, ``mitarbeiter``, ``mitarbeiter_name``, ``benutzer``, ``von``, ``bis``, ``stunden``,
	``aktivitaet``. Rechte prüft der Kern (Zugang zum Projekt)."""
	if not projekt or not isinstance(projekt, str):
		return []
	abfrage, ts, td = _zeilen(von, bis, projekt)
	rows = (
		abfrage.select(
			ts.employee,
			ts.employee_name,
			td.from_time,
			td.to_time,
			td.hours,
			td.activity_type,
		)
		.orderby(td.from_time)
		.run(as_dict=True)
	)
	mitarbeiter = {r.employee for r in rows if r.employee}
	benutzer = (
		dict(
			frappe.get_all(
				"Employee",
				filters={"name": ["in", list(mitarbeiter)]},
				fields=["name", "user_id"],
				as_list=True,
			)
		)
		if mitarbeiter
		else {}
	)
	return [
		{
			"datum": getdate(r.from_time).isoformat(),
			"mitarbeiter": r.employee,
			"mitarbeiter_name": r.employee_name or r.employee,
			"benutzer": benutzer.get(r.employee) or None,
			"von": str(r.from_time) if r.from_time else None,
			"bis": str(r.to_time) if r.to_time else None,
			"stunden": round(flt(r.hours), 2),
			"aktivitaet": r.activity_type,
		}
		for r in rows
	]


def projekte_mit_zeiten(von: _dt.date, bis: _dt.date) -> list[str]:
	"""Projekte mit mindestens einer Zeitbuchung im Zeitraum (nächtlicher Job des Kerns)."""
	abfrage, _ts, td = _zeilen(von, bis)
	return sorted({r[0] for r in abfrage.select(td.project).distinct().run() if r[0]})


def _objekt_adresse(projekt: str) -> str:
	meta = frappe.get_meta("Project")
	if not meta.has_field("cv_objekt") or not frappe.db.exists("DocType", OBJEKT):
		return ""
	objekt = frappe.db.get_value("Project", projekt, "cv_objekt")
	if not objekt:
		return ""
	werte = frappe.db.get_value(OBJEKT, objekt, ["strasse", "plz", "ort"], as_dict=True) or {}
	strasse = (werte.get("strasse") or "").strip()
	ort = " ".join(x for x in ((werte.get("plz") or "").strip(), (werte.get("ort") or "").strip()) if x)
	if not ort:
		return ""
	return ", ".join(x for x in (strasse, ort) if x)


def koordinaten(projekt: str) -> tuple[float, float] | None:
	"""(lat, lon) des Objekts bzw. der Projektadresse oder ``None`` (keine Adresse, kein Treffer,
	Geokodierung aus oder Fehler - nie eine Ausnahme)."""
	from corevatis_erpnext.planung import geokodierung

	try:
		if not projekt or not frappe.db.exists("Project", projekt):
			return None
		adresse = _objekt_adresse(projekt)
		if not adresse:
			from corevatis_erpnext.planung import terminplanung

			adresse, _lead = terminplanung._projekt_adresse(projekt, terminplanung.Kontext())
		if not adresse:
			return None
		ort = geokodierung.koordinaten(adresse, geokodierung.Budget(1))
	except Exception:
		frappe.log_error(title="Bautagebuch: Koordinaten des Projekts")
		return None
	if not ort.bekannt:
		return None
	return flt(ort.lat), flt(ort.lon)


def stand(projekt: str) -> dict | None:
	"""Pille „Bautagebuch: n Tage“ der Prozessleiste (``prozess.kette``): ``tage``, ``text``,
	``erster``, ``letzter``, ``darf_erzeugen``. ``None`` ohne Kern oder ohne Leserecht."""
	if not kern_da():
		return None
	s = _kern(KERN_STAND)(projekt)
	if not s or (not s.get("tage") and not s.get("darf_erzeugen")):
		return None
	return {
		"projekt": s.get("projekt"),
		"tage": s.get("tage") or 0,
		"text": s.get("text"),
		"erster": s.get("erster"),
		"letzter": s.get("letzter"),
		"darf_erzeugen": bool(s.get("darf_erzeugen")),
	}
