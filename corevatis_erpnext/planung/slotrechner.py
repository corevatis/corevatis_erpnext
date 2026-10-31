"""Reine Rechenregeln der Terminplanung (ohne Frappe): Fahrzeit aus Koordinaten, freie Zeitfenster
aus einer Belegungsliste (Durchstich D14, ``docs/terminplanung.md`` im Repo Handwerker).

- **Fahrzeit** = Luftlinie (Haversine) mal Umwegfaktor durch Durchschnittsgeschwindigkeit, mindestens die
  Mindestfahrzeit, aufgerundet auf 5 Minuten. Fehlt eine Koordinate, gilt die Standardfahrzeit, und
  die Fahrt ist „geschätzt“.
- **Belegung** = Zeitraum mit Ort (Termin, Arbeitspaket, Feiertag). Ein Slot ist frei, wenn er sich
  mit keiner Belegung überschneidet, die Fahrt von der vorigen Belegung desselben Tages (sonst von der
  Firma ab Tagesbeginn) plus Puffer vor dem Slot Platz hat und die Fahrt zur nächsten Belegung
  (sonst zurück zur Firma bis Tagesende) nach dem Slot.
- **Kandidaten** sind Startzeiten im Raster (30 Minuten) innerhalb eines Zeitfensters; die Fenster
  „vormittags“ (8-12), „nachmittags“ (12-16) und „ganztags“ werden mit der Arbeitszeit geschnitten.

Keine Frappe-Abhängigkeit, damit die Regeln ohne Site prüfbar bleiben (``tests/test_terminplanung``).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

ERDRADIUS_KM = 6371.0
EIN_TAG = dt.timedelta(days=1)

# Zeitfenster des Kunden (Werte wie ``anfrage.custom_fields.WUNSCH_FENSTER``); None = Arbeitszeit
FENSTER: dict[str, tuple[dt.time, dt.time] | None] = {
	"vormittags": (dt.time(8), dt.time(12)),
	"nachmittags": (dt.time(12), dt.time(16)),
	"ganztags": None,
}

ART_TERMIN = "Termin"
ART_ARBEITSPAKET = "Arbeitspaket"
ART_FEIERTAG = "Feiertag"
ART_ABWESENHEIT = "Abwesenheit"
ART_FIRMA = "Firma"


@dataclass(frozen=True)
class Ort:
	"""Ein Ort mit Anzeigetext und optionalen Koordinaten."""

	text: str = ""
	lat: float | None = None
	lon: float | None = None

	@property
	def bekannt(self) -> bool:
		return self.lat is not None and self.lon is not None


UNBEKANNT = Ort()


@dataclass(frozen=True)
class Fahrparameter:
	umwegfaktor: float = 1.3
	kmh: float = 45.0
	minimum: int = 10
	standard: int = 30
	puffer: int = 10
	rundung: int = 5


@dataclass
class Belegung:
	von: dt.datetime
	bis: dt.datetime
	ort: Ort = UNBEKANNT
	art: str = ART_TERMIN
	titel: str = ""
	doctype: str | None = None
	name: str | None = None
	# Anfrage, zu der der Ort gehört (die Anzeige prüft das Leserecht darauf)
	lead: str | None = None


@dataclass(frozen=True)
class Fahrt:
	minuten: int
	geschaetzt: bool
	ort: Ort
	art: str
	titel: str = ""
	belegung: Belegung | None = None


@dataclass
class Pruefung:
	frei: bool
	hin: Fahrt | None
	weiter: Fahrt | None
	konflikte: list[Belegung] = field(default_factory=list)


@dataclass
class Slot:
	beginn: dt.datetime
	ende: dt.datetime
	hin: Fahrt | None
	weiter: Fahrt | None
	wunsch: bool = False

	@property
	def fahrt_gesamt(self) -> int:
		return (self.hin.minuten if self.hin else 0) + (self.weiter.minuten if self.weiter else 0)

	@property
	def geschaetzt(self) -> bool:
		return bool((self.hin and self.hin.geschaetzt) or (self.weiter and self.weiter.geschaetzt))

	def bewertung(self) -> tuple:
		"""Sortierschlüssel: Wunsch getroffen → früh → wenig Fahrt."""
		return (0 if self.wunsch else 1, self.beginn, self.fahrt_gesamt)


# --- Fahrzeit -------------------------------------------------------------------------------


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
	"""Luftlinie in Kilometern (Großkreis)."""
	phi1, phi2 = math.radians(lat1), math.radians(lat2)
	dphi = phi2 - phi1
	dlambda = math.radians(lon2 - lon1)
	a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
	return 2 * ERDRADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def aufrunden(minuten: float, rundung: int) -> int:
	rundung = max(int(rundung), 1)
	return int(math.ceil(minuten / rundung - 1e-9) * rundung)


def fahrzeit_minuten(von: Ort, nach: Ort, p: Fahrparameter) -> tuple[int, bool]:
	"""``(Minuten, geschätzt)``: ohne Koordinaten auf einer Seite die Standardfahrzeit."""
	if not von.bekannt or not nach.bekannt:
		return aufrunden(max(p.standard, 0), p.rundung), True
	km = haversine_km(von.lat, von.lon, nach.lat, nach.lon)
	kmh = p.kmh if p.kmh > 0 else 45.0
	faktor = p.umwegfaktor if p.umwegfaktor > 0 else 1.0
	minuten = km * faktor / kmh * 60
	return aufrunden(max(minuten, p.minimum, 0), p.rundung), False


# --- Zeitfenster ----------------------------------------------------------------------------


def arbeitszeit(tag: dt.date, arbeitsbeginn: dt.time, tagesstunden: float) -> tuple[dt.datetime, dt.datetime]:
	"""Tagesbeginn und Tagesende der Arbeitszeit (Ende höchstens Mitternacht)."""
	beginn = dt.datetime.combine(tag, arbeitsbeginn)
	ende = beginn + dt.timedelta(hours=tagesstunden if tagesstunden > 0 else 8.0)
	mitternacht = dt.datetime.combine(tag, dt.time.min) + EIN_TAG
	return beginn, min(ende, mitternacht)


def fenster_grenzen(
	tag: dt.date, fenster: str | None, arbeitsbeginn: dt.time, tagesstunden: float
) -> tuple[dt.datetime, dt.datetime] | None:
	"""Wunschfenster mit der Arbeitszeit geschnitten; ``None``, wenn nichts übrig bleibt."""
	von, bis = arbeitszeit(tag, arbeitsbeginn, tagesstunden)
	zeiten = FENSTER.get(fenster or "ganztags")
	if zeiten:
		von = max(von, dt.datetime.combine(tag, zeiten[0]))
		bis = min(bis, dt.datetime.combine(tag, zeiten[1]))
	return (von, bis) if von < bis else None


def kandidaten(von: dt.datetime, bis: dt.datetime, dauer: int, raster: int = 30) -> list[dt.datetime]:
	"""Startzeiten im Raster, deren Ende noch im Fenster liegt."""
	ergebnis = []
	start = von
	schritt = dt.timedelta(minutes=max(int(raster), 5))
	while start + dt.timedelta(minutes=dauer) <= bis:
		ergebnis.append(start)
		start += schritt
	return ergebnis


# --- Slots ----------------------------------------------------------------------------------


def ueberschneidung(a_von, a_bis, b_von, b_bis) -> bool:
	return a_von < b_bis and b_von < a_bis


def _mit_ort(b: Belegung) -> bool:
	# Feiertage, Abwesenheiten und ganztägige Sperren sind keine Orte, von denen man anreist
	return b.art not in (ART_FEIERTAG, ART_ABWESENHEIT)


def _vorige(belegungen: list[Belegung], start: dt.datetime) -> Belegung | None:
	tagesanfang = dt.datetime.combine(start.date(), dt.time.min)
	passend = [b for b in belegungen if _mit_ort(b) and tagesanfang < b.bis <= start]
	return max(passend, key=lambda b: b.bis) if passend else None


def _naechste(belegungen: list[Belegung], ende: dt.datetime) -> Belegung | None:
	tagesende = dt.datetime.combine(ende.date(), dt.time.min) + EIN_TAG
	passend = [b for b in belegungen if _mit_ort(b) and ende <= b.von < tagesende]
	return min(passend, key=lambda b: b.von) if passend else None


def slot_pruefen(
	start: dt.datetime,
	ende: dt.datetime,
	ziel: Ort,
	belegungen: list[Belegung],
	p: Fahrparameter,
	firma: Ort | None = None,
	tagesbeginn: dt.datetime | None = None,
	tagesende: dt.datetime | None = None,
) -> Pruefung:
	"""Passt der Slot ``start``-``ende`` am Ort ``ziel`` zwischen die Belegungen?

	Konflikte sind Überschneidungen sowie die vorige/nächste Belegung desselben Tages, wenn Fahrt
	plus Puffer nicht hineinpassen. Mit ``firma`` und ``tagesbeginn``/``tagesende`` beginnt der Tag
	ohne vorige Belegung in der Firma (Fahrt ab Tagesbeginn, ohne Puffer) und endet dort; ohne
	``firma`` bleiben die Tagesgrenzen ungeprüft (Konfliktprüfung beim Anlegen: nur echte Belegungen).
	"""
	konflikte = [b for b in belegungen if ueberschneidung(start, ende, b.von, b.bis)]
	frei = not konflikte
	puffer = dt.timedelta(minutes=max(p.puffer, 0))
	hin = weiter = None

	vorige = _vorige(belegungen, start)
	if vorige is not None:
		minuten, geschaetzt = fahrzeit_minuten(vorige.ort, ziel, p)
		hin = Fahrt(minuten, geschaetzt, vorige.ort, vorige.art, vorige.titel, vorige)
		if vorige.bis + dt.timedelta(minutes=minuten) + puffer > start:
			frei = False
			if vorige not in konflikte:
				konflikte.append(vorige)
	elif firma is not None and tagesbeginn is not None:
		minuten, geschaetzt = fahrzeit_minuten(firma, ziel, p)
		hin = Fahrt(minuten, geschaetzt, firma, ART_FIRMA)
		if tagesbeginn + dt.timedelta(minutes=minuten) > start:
			frei = False

	naechste = _naechste(belegungen, ende)
	if naechste is not None:
		minuten, geschaetzt = fahrzeit_minuten(ziel, naechste.ort, p)
		weiter = Fahrt(minuten, geschaetzt, naechste.ort, naechste.art, naechste.titel, naechste)
		if ende + dt.timedelta(minutes=minuten) + puffer > naechste.von:
			frei = False
			if naechste not in konflikte:
				konflikte.append(naechste)
	elif firma is not None and tagesende is not None:
		minuten, geschaetzt = fahrzeit_minuten(ziel, firma, p)
		weiter = Fahrt(minuten, geschaetzt, firma, ART_FIRMA)
		if ende + dt.timedelta(minutes=minuten) > tagesende:
			frei = False

	return Pruefung(frei, hin, weiter, konflikte)


def freie_slots(
	tag: dt.date,
	fenster: str | None,
	dauer: int,
	ziel: Ort,
	belegungen: list[Belegung],
	p: Fahrparameter,
	firma: Ort,
	arbeitsbeginn: dt.time,
	tagesstunden: float,
	raster: int = 30,
	wunsch: bool = False,
	ab: dt.datetime | None = None,
) -> list[Slot]:
	"""Freie Slots eines Tages im Fenster (``ab``: frühester Beginn, z. B. heute ab jetzt)."""
	grenzen = fenster_grenzen(tag, fenster, arbeitsbeginn, tagesstunden)
	if not grenzen:
		return []
	tagesbeginn, tagesende = arbeitszeit(tag, arbeitsbeginn, tagesstunden)
	tag_von = dt.datetime.combine(tag, dt.time.min)
	des_tages = [b for b in belegungen if ueberschneidung(b.von, b.bis, tag_von, tag_von + EIN_TAG)]
	slots = []
	for start in kandidaten(grenzen[0], grenzen[1], dauer, raster):
		if ab is not None and start < ab:
			continue
		ende = start + dt.timedelta(minutes=dauer)
		pr = slot_pruefen(start, ende, ziel, des_tages, p, firma, tagesbeginn, tagesende)
		if pr.frei:
			slots.append(Slot(start, ende, pr.hin, pr.weiter, wunsch))
	return slots
