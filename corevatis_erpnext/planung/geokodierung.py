"""Geokodierung von Adressen über Nominatim (OpenStreetMap) mit dauerhaftem Zwischenspeicher
(Doctype „Geokoordinate“, Schlüssel = Hash der normalisierten Adresse).

Regeln (Nominatim Usage Policy, Security-Review):

- feste URL ``NOMINATIM_URL``, eigener User-Agent, höchstens **eine Anfrage je Sekunde** (Zeitstempel
  im Cache, Wartezeit vor der nächsten Anfrage), Timeout 5 s, keine Weiterleitungen, höchstens
  ``MAX_ABFRAGEN_JE_AUFRUF`` neue Adressen je Desk-Aufruf (der Rest gilt als „Fahrzeit geschätzt“
  und wird beim nächsten Aufruf nachgeholt);
- jedes Ergebnis wird dauerhaft gespeichert, auch „Kein Treffer“ (erneuter Versuch nach 30 Tagen);
- nach einem Netzfehler bleibt die Geokodierung 10 Minuten aus (Cache-Sperre), kein 500 im Desk;
- Schalter „Geokodierung aus“ in „Corevatis Planung Einstellungen“: keine Anfragen, alle Fahrzeiten
  mit der Standardfahrzeit.

Keine API-Schlüssel, keine Kosten. Es werden nur Adressen übertragen, keine Namen.
"""

from __future__ import annotations

import hashlib
import re
import time

import frappe
from frappe.utils import add_days, cint, flt, getdate, now_datetime, today

from corevatis_erpnext.planung.slotrechner import UNBEKANNT, Ort

DOCTYPE = "Geokoordinate"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "Corevatis Handwerker (corevatis.de)"
TIMEOUT = 5
MINDESTABSTAND = 1.0  # Sekunden zwischen zwei Anfragen
MAX_ABFRAGEN_JE_AUFRUF = 8
SPERRE_NACH_FEHLER = 600  # Sekunden
OHNE_TREFFER_TAGE = 30
QUELLE_NOMINATIM = "Nominatim"
QUELLE_MANUELL = "Manuell"
QUELLE_KEIN_TREFFER = "Kein Treffer"
CACHE_LETZTE = "cv:geo:letzte_abfrage"
CACHE_SPERRE = "cv:geo:gesperrt"
MAX_ADRESSE = 500

_WS = re.compile(r"\s+")
_KOMMA = re.compile(r"\s*,\s*")


class Budget:
	"""Zähler der neuen Anfragen je Aufruf (Vorschläge rechnen für mehrere Orte)."""

	def __init__(self, abfragen: int = MAX_ABFRAGEN_JE_AUFRUF) -> None:
		self.rest = abfragen
		self.verbraucht = 0
		self.fehlgeschlagen: list[str] = []
		self.ohne_treffer: list[str] = []

	def nehmen(self) -> bool:
		if self.rest <= 0:
			return False
		self.rest -= 1
		self.verbraucht += 1
		return True


def normalisieren(adresse) -> str:
	"""Zeilen zu „Straße 1, 48143 Münster“ (Leerraum und Kommas vereinheitlicht): gleiche Adresse,
	gleicher Schlüssel."""
	zeilen = [_KOMMA.sub(", ", _WS.sub(" ", z)).strip(" ,;") for z in str(adresse or "").splitlines()]
	return ", ".join(z for z in zeilen if z)[:MAX_ADRESSE]


def adresse_hash(adresse) -> str:
	return hashlib.sha1(normalisieren(adresse).casefold().encode("utf-8"), usedforsecurity=False).hexdigest()


def geokodierung_aktiv() -> bool:
	from corevatis_erpnext.planung.einstellungen import einstellungen

	return not cint(einstellungen().geokodierung_aus)


def gespeichert(adresse: str) -> frappe._dict | None:
	if not frappe.db.exists("DocType", DOCTYPE):
		return None
	return frappe.db.get_value(
		DOCTYPE, adresse_hash(adresse), ["name", "lat", "lon", "quelle", "stand"], as_dict=True
	)


def koordinaten(adresse, budget: Budget | None = None, aktiv: bool | None = None) -> Ort:
	"""Ort zur Adresse: aus dem Zwischenspeicher, sonst (Budget, Schalter, Sperre erlaubend) von
	Nominatim. Ohne Ergebnis ein Ort ohne Koordinaten - nie eine Ausnahme."""
	text = normalisieren(adresse)
	if not text:
		return UNBEKANNT
	eintrag = gespeichert(text)
	if (
		eintrag
		and eintrag.quelle != QUELLE_KEIN_TREFFER
		and eintrag.lat is not None
		and eintrag.lon is not None
	):
		return Ort(text, flt(eintrag.lat), flt(eintrag.lon))
	if eintrag and eintrag.quelle == QUELLE_KEIN_TREFFER:
		stand = getdate(eintrag.stand) if eintrag.stand else None
		if stand and stand > getdate(add_days(today(), -OHNE_TREFFER_TAGE)):
			if budget is not None:
				budget.ohne_treffer.append(text)
			return Ort(text)
	if aktiv is None:
		aktiv = geokodierung_aktiv()
	if not aktiv or frappe.cache.get_value(CACHE_SPERRE):
		return Ort(text)
	if budget is not None and not budget.nehmen():
		return Ort(text)
	try:
		treffer = abfragen(text)
	except Exception:
		frappe.cache.set_value(CACHE_SPERRE, 1, expires_in_sec=SPERRE_NACH_FEHLER)
		frappe.log_error(title="Geokodierung: Nominatim nicht erreichbar", message=frappe.get_traceback())
		if budget is not None:
			budget.fehlgeschlagen.append(text)
		return Ort(text)
	speichern(text, treffer, eintrag)
	if not treffer:
		if budget is not None:
			budget.ohne_treffer.append(text)
		return Ort(text)
	return Ort(text, treffer[0], treffer[1])


def speichern(text: str, treffer: tuple[float, float, str] | None, eintrag=None) -> None:
	if not frappe.db.exists("DocType", DOCTYPE):
		return
	werte = {
		"adresse": text,
		"stand": now_datetime(),
		"quelle": QUELLE_NOMINATIM if treffer else QUELLE_KEIN_TREFFER,
		"lat": treffer[0] if treffer else None,
		"lon": treffer[1] if treffer else None,
		"anzeige": (treffer[2] if treffer else "")[:140],
	}
	if eintrag:
		frappe.db.set_value(DOCTYPE, eintrag.name, werte)
	else:
		doc = frappe.get_doc({"doctype": DOCTYPE, "adresse_hash": adresse_hash(text), **werte})
		doc.flags.ignore_permissions = True
		try:
			doc.insert()
		except frappe.DuplicateEntryError:
			frappe.db.set_value(DOCTYPE, adresse_hash(text), werte)
	# auch bei einem lesenden Aufruf (GET) behalten
	frappe.flags.commit = True


def _warten() -> None:
	letzte = frappe.cache.get_value(CACHE_LETZTE)
	if letzte:
		rest = MINDESTABSTAND - (time.time() - flt(letzte))
		if rest > 0:
			time.sleep(min(rest, MINDESTABSTAND))


def abfragen(adresse: str) -> tuple[float, float, str] | None:
	"""Eine Nominatim-Anfrage (feste URL, Timeout, keine Weiterleitungen, höchstens eine je Sekunde).

	Rückgabe ``(lat, lon, Anzeigename)`` oder ``None`` ohne Treffer; Netz-/HTTP-Fehler als Ausnahme.
	In Tests wird diese Funktion gemockt (kein Netz in der Test-Bench).
	"""
	import requests

	_warten()
	try:
		antwort = requests.get(
			NOMINATIM_URL,
			params={"q": adresse, "format": "jsonv2", "limit": 1, "addressdetails": 0},
			headers={"User-Agent": USER_AGENT, "Accept-Language": "de"},
			timeout=TIMEOUT,
			allow_redirects=False,
		)
	finally:
		frappe.cache.set_value(CACHE_LETZTE, time.time(), expires_in_sec=10)
	antwort.raise_for_status()
	if antwort.status_code != 200:
		raise ValueError(f"Nominatim: unerwarteter Status {antwort.status_code}")
	daten = antwort.json()
	if not isinstance(daten, list) or not daten or not isinstance(daten[0], dict):
		return None
	erster = daten[0]
	lat, lon = flt(erster.get("lat")), flt(erster.get("lon"))
	if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
		return None
	return lat, lon, str(erster.get("display_name") or "")
