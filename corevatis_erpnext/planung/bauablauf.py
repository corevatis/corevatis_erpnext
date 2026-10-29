"""Bauablauf (Annahme 5.1): Reihenfolge der Arbeitspakete, Phasen der Positionen, Zeitplan.

**Annahme, nicht von Hülshoff bestätigt** (``docs/5.1-tasks-materialbedarf.md``). Ein Trupp
(Teamgröße aus den Einstellungen) arbeitet die Arbeitspakete nacheinander ab:

1. Allgemeine Leistungen (Verfahren „Sonstiges“: Baustelleneinrichtung, Abdecken, Feuchtemessung)
2. Außenabdichtung - Erdarbeiten → Untergrund → Abdichtung → Drainage/Dämmung → Verfüllung
3. Horizontalsperre Injektion - Bohren/Injizieren → Bohrlöcher schließen (vor der Innenabdichtung,
   die Sperre soll unter der Dichtschlämme liegen)
4. Spezialabdichtung - Wasser stoppen (Schnellzement) → Riss-/Fugenabdichtung
5. Innenabdichtung - Vorbereitung → Hohlkehle → Dichtschlämme
6. Sanierputz - Vorbereitung → Sanierputz → Oberfläche

Verfahren, die die Tabelle nicht kennt, folgen danach in der Reihenfolge ihrer ersten Position.
Die Phase einer Position kommt aus ``PHASE_JE_ITEM`` (Leistungskatalog Abdichtung, 2.3);
unbekannte Positionen stehen am Ende ihres Arbeitspakets in Auftragsreihenfolge.
"""

from __future__ import annotations

import datetime as dt

from frappe.utils import add_days, get_time, getdate

VERFAHREN_SONSTIGES = "Sonstiges"
# Verfahren in Ausführungsreihenfolge; Titel für Verfahren, deren Name im Task nichts sagt
VERFAHREN_REIHENFOLGE = (
	VERFAHREN_SONSTIGES,
	"Außenabdichtung",
	"Horizontalsperre Injektion",
	"Spezialabdichtung",
	"Innenabdichtung",
	"Sanierputz",
)
VERFAHREN_TITEL = {VERFAHREN_SONSTIGES: "Allgemeine Leistungen"}

# Phasen in Ausführungsreihenfolge (ein Arbeitspaket nutzt nur einen Teil davon)
PHASEN = (
	"Baustelleneinrichtung",
	"Vorbereitung",
	"Erdarbeiten",
	"Untergrund",
	"Wasser stoppen",
	"Bohren und Injizieren",
	"Bohrlöcher schließen",
	"Hohlkehle",
	"Abdichtung",
	"Dichtschlämme",
	"Drainage/Dämmung",
	"Verfüllung",
	"Sanierputz",
	"Oberfläche",
)


def _phasen(phase: str, *codes: str) -> dict[str, str]:
	return dict.fromkeys(codes, phase)


# Leistungskatalog Abdichtung (2.3). AA-01 enthält das Wiederverfüllen - die Phase „Verfüllung“
# bleibt für eigene Positionen frei.
PHASE_JE_ITEM = {
	**_phasen("Baustelleneinrichtung", "AL-01"),
	**_phasen(
		"Vorbereitung", "AL-02", "AL-03", "IA-01", "IA-02", "IA-03", "SP-01", "SP-02", "SP-03", "SP-09"
	),
	**_phasen("Erdarbeiten", "AA-01", "AA-02", "AA-03"),
	**_phasen("Untergrund", "AA-04", "AA-05"),
	**_phasen("Wasser stoppen", "SA-02"),
	**_phasen("Bohren und Injizieren", "HS-01", "HS-02", "HS-03", "HS-05", "HS-06"),
	**_phasen("Bohrlöcher schließen", "HS-04"),
	**_phasen("Hohlkehle", "IA-04", "IA-07"),
	**_phasen(
		"Abdichtung", "AA-06", "AA-07", "AA-08", "AA-09", "AA-10", "AA-13", "AA-14", "SA-01", "SA-03", "SA-04"
	),
	**_phasen("Dichtschlämme", "IA-05", "IA-06", "IA-08", "IA-09"),
	**_phasen("Drainage/Dämmung", "AA-11", "AA-12", "AA-15", "AA-16"),
	**_phasen("Sanierputz", "SP-04", "SP-05", "SP-06", "SP-08"),
	**_phasen("Oberfläche", "SP-07", "SP-10"),
}


def verfahren_rang(verfahren: str, erste_nennung: dict[str, int]) -> tuple[int, int]:
	if verfahren in VERFAHREN_REIHENFOLGE:
		return (VERFAHREN_REIHENFOLGE.index(verfahren), 0)
	return (len(VERFAHREN_REIHENFOLGE), erste_nennung.get(verfahren, 0))


def phase(item_code: str | None) -> str | None:
	return PHASE_JE_ITEM.get(item_code or "")


def phasen_rang(item_code: str | None) -> int:
	p = phase(item_code)
	return PHASEN.index(p) if p else len(PHASEN)


def titel(verfahren: str) -> str:
	return VERFAHREN_TITEL.get(verfahren, verfahren)


# --- Zeitplan -------------------------------------------------------------------------------

EPS = 1e-9


def ist_arbeitstag(tag: dt.date, freie_tage: set) -> bool:
	"""Montag bis Freitag, nicht in der Feiertagsliste der Firma."""
	return tag.weekday() < 5 and tag not in freie_tage


def naechster_arbeitstag(tag, freie_tage: set) -> dt.date:
	tag = getdate(tag)
	for _ in range(370):
		if ist_arbeitstag(tag, freie_tage):
			return tag
		tag = getdate(add_days(tag, 1))
	return tag


def zeitplan(
	start,
	dauern: list[float],
	tagesstunden: float = 8.0,
	arbeitsbeginn: str = "07:00:00",
	freie_tage: set | None = None,
) -> list[tuple[dt.datetime, dt.datetime]]:
	"""Fortlaufender Plan: je Dauer (Stunden auf der Uhr = Sollstunden ÷ Teamgröße) Beginn und Ende.

	Ein Arbeitstag hat ``tagesstunden`` ab ``arbeitsbeginn``; was nicht mehr in den Tag passt,
	geht am nächsten Arbeitstag weiter. Das nächste Paket beginnt, wo das vorige endet.
	"""
	freie_tage = freie_tage or set()
	beginn = get_time(arbeitsbeginn)
	tagesstunden = tagesstunden if tagesstunden > 0 else 8.0
	tag = naechster_arbeitstag(start, freie_tage)
	versatz = 0.0  # Stunden seit Arbeitsbeginn am Tag ``tag``

	def zeitpunkt(t: dt.date, stunden: float) -> dt.datetime:
		punkt = dt.datetime.combine(t, beginn) + dt.timedelta(hours=stunden)
		return punkt.replace(microsecond=0) + dt.timedelta(seconds=round(punkt.microsecond / 1e6))

	ergebnis = []
	for dauer in dauern:
		if versatz >= tagesstunden - EPS:
			tag, versatz = naechster_arbeitstag(add_days(tag, 1), freie_tage), 0.0
		von = zeitpunkt(tag, versatz)
		rest = max(float(dauer or 0), 0.0)
		while rest > EPS:
			frei = tagesstunden - versatz
			if rest <= frei + EPS:
				versatz += rest
				rest = 0.0
			else:
				rest -= frei
				tag, versatz = naechster_arbeitstag(add_days(tag, 1), freie_tage), 0.0
		ergebnis.append((von, zeitpunkt(tag, versatz)))
	return ergebnis
