"""Formatprüfung der DATEV-Dateien (EXTF-CSV) - bewusst ohne Frappe, damit sie ohne Site testbar ist.

Geprüft wird, was der DATEV-Import (Unternehmen online, Kanzlei-Rechnungswesen) am Buchungsstapel
verlangt (DATEV-Format 7.0, Buchungsstapel Formatversion 9 oder 12):

- Kopfzeile: ``"EXTF"``, Versionsnummer 700, Datenkategorie 21, Formatname „Buchungsstapel“,
  Formatversion, erzeugt am (17 Ziffern), Beraternummer (4-7 Ziffern), Mandantennummer (1-5 Ziffern),
  Wirtschaftsjahresbeginn (JJJJMMTT), Sachkontenlänge (4-9), Zeitraum von/bis (JJJJMMTT), Buchungstyp 1,
  Festschreibung 0/1, Währung, SKR - insgesamt 31 Felder.
- Spaltenüberschrift: 120 Spalten (Version 9) bzw. 125 (Version 12), Pflichtspalten an ihrem Platz.
- Je Buchungszeile: Umsatz (Dezimalkomma, > 0), Soll/Haben-Kennzeichen S oder H, Konto und Gegenkonto
  (Ziffern; Sachkonto mit Sachkontenlänge, Personenkonto eine Stelle länger), BU-Schlüssel (leer oder
  Ziffern), Belegdatum TTMM innerhalb des Zeitraums, Belegfeld 1 (Pflicht, höchstens 36 Zeichen, nur
  Buchstaben, Ziffern und ``$ & % * + - /``), Belegfeld 2 (höchstens 12), Buchungstext (höchstens 60),
  Beleginfo-Art (20) und -Inhalt (210), Auftragsnummer (30), Fälligkeit (TTMMJJJJ), Festschreibung je
  Zeile (leer, 0 oder 1), Zeichensatz Windows-1252, Zeilenende CRLF.

Die Debitoren-Stammdaten (Datenkategorie 16, „Debitoren/Kreditoren“ Version 5, 254 Spalten) werden mit
``pruefe_debitoren`` geprüft (Kontonummer, Adressatentyp, Name, Feldlängen).

Rückgabe ist ein ``Pruefergebnis`` mit ``fehler`` (Import würde scheitern oder Buchungen wären falsch)
und ``hinweise`` (z. B. Platzhalter-Beraternummer ``0000000``).

``textzelle`` bereinigt Textzellen vor dem Schreiben (Leerraum, Anführungszeichen, Semikolon, führende
Formelzeichen); die Prüfung lehnt Zellen ab, die trotzdem mit einem Formelzeichen beginnen.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

KOPF_FELDER = 31
KENNUNG = "EXTF"
VERSION = "700"
KATEGORIE_BUCHUNGSSTAPEL = "21"
KATEGORIE_DEBITOREN = "16"
FORMAT_BUCHUNGSSTAPEL = "Buchungsstapel"
FORMAT_DEBITOREN = "Debitoren/Kreditoren"
# Buchungsstapel: Formatversion → Spaltenzahl der Nutzdaten (DATEV-Format 7.0)
SPALTEN_JE_VERSION = {"9": 120, "12": 125}
SPALTEN_DEBITOREN = 254
VERSION_DEBITOREN = "5"
PLATZHALTER_BERATER = "0000000"
PLATZHALTER_MANDANT = "00000"

RE_UMSATZ = re.compile(r"^\d{1,10},\d{1,2}$")
RE_ZIFFERN = re.compile(r"^\d+$")
RE_BELEGFELD = re.compile(r"^[A-Za-z0-9$&%*+\-/]*$")
RE_WAEHRUNG = re.compile(r"^[A-Z]{3}$")

PFLICHTSPALTEN = (
	"Umsatz (ohne Soll/Haben-Kz)",
	"Soll/Haben-Kennzeichen",
	"Konto",
	"Gegenkonto (ohne BU-Schlüssel)",
	"BU-Schlüssel",
	"Belegdatum",
	"Belegfeld 1",
	"Belegfeld 2",
	"Buchungstext",
	"Auftragsnummer",
	"Festschreibung",
	"Fälligkeit",
)
# Spalten, die an einer festen Stelle stehen müssen (0-basiert)
FESTE_POSITIONEN = {
	"Umsatz (ohne Soll/Haben-Kz)": 0,
	"Soll/Haben-Kennzeichen": 1,
	"Konto": 6,
	"Gegenkonto (ohne BU-Schlüssel)": 7,
	"BU-Schlüssel": 8,
	"Belegdatum": 9,
	"Belegfeld 1": 10,
	"Belegfeld 2": 11,
	"Buchungstext": 13,
}
MAX_LAENGEN = {
	"Belegfeld 1": 36,
	"Belegfeld 2": 12,
	"Buchungstext": 60,
	"Auftragsnummer": 30,
	"KOST1 - Kostenstelle": 36,
	"KOST2 - Kostenstelle": 36,
}
MAX_BELEGINFO_ART = 20
MAX_BELEGINFO_INHALT = 210
# Textzellen dürfen nicht mit Formelzeichen beginnen (Tabellenkalkulation beim Steuerberater)
FORMELZEICHEN = ("=", "+", "-", "@")
KEINE_TEXTSPALTEN = frozenset(
	{
		"Umsatz (ohne Soll/Haben-Kz)",
		"Soll/Haben-Kennzeichen",
		"Konto",
		"Gegenkonto (ohne BU-Schlüssel)",
		"BU-Schlüssel",
		"Belegdatum",
		"Fälligkeit",
		"Festschreibung",
	}
)


def textzelle(wert, max_len: int) -> str:
	"""Textzelle für eine CSV: Leerraum gebündelt (auch Tab, CR, LF), ``"`` → ``'``, Semikolon (Trennzeichen)
	→ Komma, führende Formelzeichen ``= + - @`` wiederholt entfernt, bis keines mehr vorn steht
	(``"=\t=cmd"`` → ``"cmd"``), auf ``max_len`` gekürzt. Ein führendes ``+`` von Rufnummern fällt
	ebenfalls weg - Rufnummern vorher über ``rufnummer`` (``+49`` → ``0049``)."""
	if wert is None:
		return ""
	s = " ".join(str(wert).replace('"', "'").replace(";", ",").split())
	while s and s[0] in FORMELZEICHEN:
		s = s[1:].lstrip()
	return s[:max_len]


def rufnummer(wert) -> str:
	"""``+49 251 ...`` → ``0049 251 ...``, damit ``textzelle`` das Plus nicht als Formelzeichen entfernt."""
	s = str(wert or "").strip()
	return "00" + s[1:] if s.startswith("+") else s


@dataclass
class Befund:
	"""Eine Beanstandung: ``zeile`` 1 = Kopfzeile, 2 = Spaltenüberschrift, ab 3 Buchungszeilen."""

	zeile: int
	feld: str
	meldung: str

	def __str__(self) -> str:
		return f"Zeile {self.zeile}, {self.feld}: {self.meldung}"


@dataclass
class Pruefergebnis:
	fehler: list[Befund] = field(default_factory=list)
	hinweise: list[Befund] = field(default_factory=list)
	zeilen: int = 0
	spalten: int = 0
	summe_soll: Decimal = Decimal("0")
	summe_haben: Decimal = Decimal("0")
	kopf: dict = field(default_factory=dict)

	@property
	def ok(self) -> bool:
		return not self.fehler

	def fehler_(self, zeile: int, feld: str, meldung: str) -> None:
		self.fehler.append(Befund(zeile, feld, meldung))

	def hinweis(self, zeile: int, feld: str, meldung: str) -> None:
		self.hinweise.append(Befund(zeile, feld, meldung))

	def text(self, max_zeilen: int = 20) -> str:
		"""Kurzprotokoll für Menschen (Fehler zuerst, höchstens ``max_zeilen`` je Art)."""
		teile = [
			f"Formatprüfung: {'OK' if self.ok else f'{len(self.fehler)} Fehler'}, "
			f"{self.zeilen} Buchungszeilen, {self.spalten} Spalten, "
			f"Soll {_betrag(self.summe_soll)}, Haben {_betrag(self.summe_haben)}"
		]
		for art, liste in (("Fehler", self.fehler), ("Hinweis", self.hinweise)):
			for b in liste[:max_zeilen]:
				teile.append(f"  {art}: {b}")
			if len(liste) > max_zeilen:
				teile.append(f"  ... {len(liste) - max_zeilen} weitere {art}")
		return "\n".join(teile)


def _betrag(wert: Decimal) -> str:
	return f"{wert:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# --- gemeinsam -------------------------------------------------------------------------------


def _dekodieren(daten: bytes | str, ergebnis: Pruefergebnis) -> str | None:
	if isinstance(daten, str):
		return daten
	try:
		return daten.decode("cp1252")
	except UnicodeDecodeError as exc:
		ergebnis.fehler_(0, "Datei", f"nicht als Windows-1252 lesbar ({exc.reason} an Byte {exc.start})")
		return None


def _zeilen(text: str, ergebnis: Pruefergebnis) -> list[str]:
	if "\r\n" not in text:
		ergebnis.fehler_(0, "Datei", "Zeilenende muss CRLF (Windows) sein")
	zeilen = text.replace("\r\n", "\n").split("\n")
	while zeilen and zeilen[-1] == "":
		zeilen.pop()
	if len(zeilen) < 2:
		ergebnis.fehler_(0, "Datei", "Kopfzeile und Spaltenüberschrift fehlen")
	return zeilen


def _felder(zeile: str) -> list[str]:
	return next(csv.reader(io.StringIO(zeile), delimiter=";", quotechar='"'))


def _datum_jjjjmmtt(wert: str) -> date | None:
	try:
		return datetime.strptime(wert, "%Y%m%d").date()
	except ValueError:
		return None


def _kopf_pruefen(felder: list[str], kategorie: str, formatname: str, ergebnis: Pruefergebnis) -> dict:
	"""Gemeinsame Kopfprüfung. Rückgabe: gelesene Kopfwerte."""
	kopf: dict = {}
	if len(felder) != KOPF_FELDER:
		ergebnis.fehler_(1, "Kopfzeile", f"{len(felder)} Felder statt {KOPF_FELDER}")
		return kopf
	if felder[0] != KENNUNG:
		ergebnis.fehler_(1, "Kennzeichen", f"{felder[0]!r} statt {KENNUNG!r}")
	if felder[1] != VERSION:
		ergebnis.fehler_(1, "Versionsnummer", f"{felder[1]!r} statt {VERSION}")
	if felder[2] != kategorie:
		ergebnis.fehler_(1, "Datenkategorie", f"{felder[2]!r} statt {kategorie}")
	if felder[3] != formatname:
		ergebnis.fehler_(1, "Formatname", f"{felder[3]!r} statt {formatname!r}")
	kopf["formatversion"] = felder[4]
	if not (len(felder[5]) == 17 and RE_ZIFFERN.match(felder[5])):
		ergebnis.fehler_(1, "Erzeugt am", f"{felder[5]!r} ist nicht JJJJMMTTHHMMSSFFF")
	kopf["beraternummer"] = felder[10]
	if not (RE_ZIFFERN.match(felder[10]) and 4 <= len(felder[10]) <= 7):
		ergebnis.fehler_(1, "Beraternummer", f"{felder[10]!r} muss 4 bis 7 Ziffern haben")
	elif felder[10] == PLATZHALTER_BERATER or int(felder[10]) < 1001:
		ergebnis.hinweis(1, "Beraternummer", f"{felder[10]} ist ein Platzhalter - vom Steuerberater erfragen")
	kopf["mandantennummer"] = felder[11]
	if not (RE_ZIFFERN.match(felder[11]) and 1 <= len(felder[11]) <= 5):
		ergebnis.fehler_(1, "Mandantennummer", f"{felder[11]!r} muss 1 bis 5 Ziffern haben")
	elif int(felder[11]) == 0:
		ergebnis.hinweis(
			1, "Mandantennummer", f"{felder[11]} ist ein Platzhalter - vom Steuerberater erfragen"
		)
	kopf["wj_beginn"] = _datum_jjjjmmtt(felder[12])
	if not kopf["wj_beginn"]:
		ergebnis.fehler_(1, "Wirtschaftsjahresbeginn", f"{felder[12]!r} ist nicht JJJJMMTT")
	try:
		kopf["sachkontenlaenge"] = int(felder[13])
	except ValueError:
		kopf["sachkontenlaenge"] = 0
	if not 4 <= kopf["sachkontenlaenge"] <= 9:
		ergebnis.fehler_(1, "Sachkontenlänge", f"{felder[13]!r} muss zwischen 4 und 9 liegen")
	kopf["skr"] = felder[26]
	return kopf


# --- Buchungsstapel ----------------------------------------------------------------------------


def pruefe_buchungsstapel(daten: bytes | str) -> Pruefergebnis:
	"""Buchungsstapel prüfen (Kopfzeile, Spalten, jede Buchungszeile). Siehe Modul-Doku."""
	ergebnis = Pruefergebnis()
	text = _dekodieren(daten, ergebnis)
	if text is None:
		return ergebnis
	zeilen = _zeilen(text, ergebnis)
	if len(zeilen) < 2:
		return ergebnis

	kopf = _kopf_pruefen(_felder(zeilen[0]), KATEGORIE_BUCHUNGSSTAPEL, FORMAT_BUCHUNGSSTAPEL, ergebnis)
	ergebnis.kopf = kopf
	if not kopf:
		return ergebnis
	felder = _felder(zeilen[0])
	kopf["von"] = _datum_jjjjmmtt(felder[14])
	kopf["bis"] = _datum_jjjjmmtt(felder[15])
	if not kopf["von"] or not kopf["bis"]:
		ergebnis.fehler_(1, "Zeitraum", f"von {felder[14]!r} / bis {felder[15]!r} nicht JJJJMMTT")
	elif kopf["von"] > kopf["bis"]:
		ergebnis.fehler_(1, "Zeitraum", "Beginn liegt nach dem Ende")
	kopf["bezeichnung"] = felder[16]
	if len(felder[16]) > 30:
		ergebnis.fehler_(1, "Bezeichnung", "länger als 30 Zeichen")
	if felder[18] != "1":
		ergebnis.fehler_(1, "Buchungstyp", f"{felder[18]!r} statt 1 (Finanzbuchführung)")
	if felder[19] not in ("", "0", "30", "40", "50", "64"):
		ergebnis.fehler_(1, "Rechnungslegungszweck", f"{felder[19]!r} unbekannt")
	kopf["festschreibung"] = felder[20]
	if felder[20] not in ("0", "1"):
		ergebnis.fehler_(1, "Festschreibung", f"{felder[20]!r} statt 0 oder 1")
	kopf["waehrung"] = felder[21]
	if not RE_WAEHRUNG.match(felder[21]):
		ergebnis.fehler_(1, "Währung", f"{felder[21]!r} ist kein ISO-Code")
	if kopf["skr"] not in ("03", "04"):
		ergebnis.hinweis(1, "SKR", f"{kopf['skr']!r} - erwartet 03 oder 04")

	spalten = _felder(zeilen[1])
	ergebnis.spalten = len(spalten)
	erwartet = SPALTEN_JE_VERSION.get(kopf["formatversion"])
	if erwartet is None:
		ergebnis.fehler_(
			1,
			"Formatversion",
			f"{kopf['formatversion']!r} unbekannt (erwartet {', '.join(SPALTEN_JE_VERSION)})",
		)
	elif len(spalten) != erwartet:
		ergebnis.fehler_(
			2, "Spalten", f"{len(spalten)} statt {erwartet} (Formatversion {kopf['formatversion']})"
		)
	index = {name: i for i, name in enumerate(spalten)}
	for name in PFLICHTSPALTEN:
		if name not in index:
			ergebnis.fehler_(2, "Spalten", f"Spalte {name!r} fehlt")
	for name, pos in FESTE_POSITIONEN.items():
		if name in index and index[name] != pos:
			ergebnis.fehler_(2, "Spalten", f"{name!r} steht an Position {index[name] + 1} statt {pos + 1}")
	if ergebnis.fehler:
		return ergebnis

	n = kopf["sachkontenlaenge"]
	for nr, zeile in enumerate(zeilen[2:], start=3):
		if zeile == "":
			ergebnis.fehler_(nr, "Zeile", "leer")
			continue
		werte = _felder(zeile)
		if len(werte) != len(spalten):
			ergebnis.fehler_(nr, "Zeile", f"{len(werte)} Felder statt {len(spalten)}")
			continue
		ergebnis.zeilen += 1
		w = dict(zip(spalten, werte, strict=True))
		_buchungszeile_pruefen(nr, w, n, kopf, ergebnis)
	if ergebnis.zeilen == 0:
		ergebnis.hinweis(3, "Zeilen", "keine Buchungszeile")
	return ergebnis


def _buchungszeile_pruefen(nr: int, w: dict, n: int, kopf: dict, ergebnis: Pruefergebnis) -> None:
	umsatz = w["Umsatz (ohne Soll/Haben-Kz)"]
	betrag = None
	if not RE_UMSATZ.match(umsatz):
		ergebnis.fehler_(nr, "Umsatz", f"{umsatz!r} - erwartet Ziffern mit Dezimalkomma, z. B. 119,00")
	else:
		try:
			betrag = Decimal(umsatz.replace(",", "."))
		except InvalidOperation:
			betrag = None
		if not betrag or betrag <= 0:
			ergebnis.fehler_(nr, "Umsatz", f"{umsatz!r} muss größer 0 sein")
			betrag = None
	sh = w["Soll/Haben-Kennzeichen"]
	if sh not in ("S", "H"):
		ergebnis.fehler_(nr, "Soll/Haben-Kennzeichen", f"{sh!r} statt S oder H")
	elif betrag is not None:
		if sh == "S":
			ergebnis.summe_soll += betrag
		else:
			ergebnis.summe_haben += betrag

	for feld in ("Konto", "Gegenkonto (ohne BU-Schlüssel)"):
		konto = w[feld]
		if not konto:
			ergebnis.fehler_(nr, feld, "leer")
		elif not RE_ZIFFERN.match(konto):
			ergebnis.fehler_(nr, feld, f"{konto!r} ist keine Kontonummer")
		elif len(konto) not in (n, n + 1):
			ergebnis.fehler_(
				nr, feld, f"{konto!r} hat {len(konto)} Stellen (Sachkonto {n}, Personenkonto {n + 1})"
			)
	if w["Konto"] and w["Konto"] == w["Gegenkonto (ohne BU-Schlüssel)"]:
		ergebnis.fehler_(nr, "Gegenkonto (ohne BU-Schlüssel)", "gleich dem Konto")

	bu = w["BU-Schlüssel"]
	if bu and not (RE_ZIFFERN.match(bu) and len(bu) <= 4):
		ergebnis.fehler_(nr, "BU-Schlüssel", f"{bu!r} - leer oder bis zu 4 Ziffern")

	_belegdatum_pruefen(nr, w["Belegdatum"], kopf, ergebnis)

	if not w["Belegfeld 1"]:
		ergebnis.fehler_(nr, "Belegfeld 1", "leer (Rechnungs- bzw. Belegnummer ist Pflicht)")
	for feld in ("Belegfeld 1", "Belegfeld 2"):
		if w[feld] and not RE_BELEGFELD.match(w[feld]):
			ergebnis.fehler_(
				nr, feld, f"{w[feld]!r} enthält unzulässige Zeichen (erlaubt: Buchstaben, Ziffern, $&%*+-/)"
			)
	for feld, max_len in MAX_LAENGEN.items():
		if feld in w and len(w[feld]) > max_len:
			ergebnis.fehler_(nr, feld, f"{len(w[feld])} Zeichen, höchstens {max_len}")
	for i in range(1, 9):
		art, inhalt = w.get(f"Beleginfo - Art {i}", ""), w.get(f"Beleginfo - Inhalt {i}", "")
		if len(art) > MAX_BELEGINFO_ART:
			ergebnis.fehler_(nr, f"Beleginfo - Art {i}", f"{len(art)} Zeichen, höchstens {MAX_BELEGINFO_ART}")
		if len(inhalt) > MAX_BELEGINFO_INHALT:
			ergebnis.fehler_(
				nr, f"Beleginfo - Inhalt {i}", f"{len(inhalt)} Zeichen, höchstens {MAX_BELEGINFO_INHALT}"
			)
		if inhalt and not art:
			ergebnis.fehler_(nr, f"Beleginfo - Art {i}", "leer, obwohl ein Inhalt steht")

	faellig = w["Fälligkeit"]
	if faellig:
		try:
			datetime.strptime(faellig, "%d%m%Y")
		except ValueError:
			ergebnis.fehler_(nr, "Fälligkeit", f"{faellig!r} ist nicht TTMMJJJJ")
	if w["Festschreibung"] not in ("", "0", "1"):
		ergebnis.fehler_(nr, "Festschreibung", f"{w['Festschreibung']!r} statt leer, 0 oder 1")
	_formelzeichen_pruefen(nr, w, KEINE_TEXTSPALTEN, ergebnis)


def _formelzeichen_pruefen(nr: int, w: dict, ausnahmen: frozenset, ergebnis: Pruefergebnis) -> None:
	for feld, wert in w.items():
		if wert and feld not in ausnahmen and wert.lstrip()[:1] in FORMELZEICHEN:
			ergebnis.fehler_(nr, feld, f"{wert[:20]!r} beginnt mit einem Formelzeichen")


def _belegdatum_pruefen(nr: int, wert: str, kopf: dict, ergebnis: Pruefergebnis) -> None:
	if not (len(wert) == 4 and RE_ZIFFERN.match(wert)):
		ergebnis.fehler_(nr, "Belegdatum", f"{wert!r} ist nicht TTMM")
		return
	tag, monat = int(wert[:2]), int(wert[2:])
	wj = kopf.get("wj_beginn")
	von, bis = kopf.get("von"), kopf.get("bis")
	if not (wj and von and bis):
		return
	# Jahr aus dem Wirtschaftsjahr: liegt TTMM vor dem WJ-Beginn, gehört der Tag ins Folgejahr
	jahr = wj.year if (monat, tag) >= (wj.month, wj.day) else wj.year + 1
	try:
		d = date(jahr, monat, tag)
	except ValueError:
		ergebnis.fehler_(nr, "Belegdatum", f"{wert!r} ist kein gültiger Tag")
		return
	if not von <= d <= bis:
		ergebnis.fehler_(
			nr, "Belegdatum", f"{d:%d.%m.%Y} liegt außerhalb des Zeitraums {von:%d.%m.%Y}-{bis:%d.%m.%Y}"
		)


# --- Debitoren-Stammdaten ------------------------------------------------------------------------


def pruefe_debitoren(daten: bytes | str) -> Pruefergebnis:
	"""Debitoren-/Kreditoren-Stammdaten prüfen (Kopf, 254 Spalten, Kontonummer, Adressatentyp, Name)."""
	ergebnis = Pruefergebnis()
	text = _dekodieren(daten, ergebnis)
	if text is None:
		return ergebnis
	zeilen = _zeilen(text, ergebnis)
	if len(zeilen) < 2:
		return ergebnis
	kopf = _kopf_pruefen(_felder(zeilen[0]), KATEGORIE_DEBITOREN, FORMAT_DEBITOREN, ergebnis)
	ergebnis.kopf = kopf
	if not kopf:
		return ergebnis
	if kopf["formatversion"] != VERSION_DEBITOREN:
		ergebnis.fehler_(1, "Formatversion", f"{kopf['formatversion']!r} statt {VERSION_DEBITOREN}")
	spalten = _felder(zeilen[1])
	ergebnis.spalten = len(spalten)
	if len(spalten) != SPALTEN_DEBITOREN:
		ergebnis.fehler_(2, "Spalten", f"{len(spalten)} statt {SPALTEN_DEBITOREN}")
	index = {name: i for i, name in enumerate(spalten)}
	for name in (
		"Konto",
		"Name (Adressatentyp Unternehmen)",
		"Name (Adressatentyp natürl. Person)",
		"Vorname (Adressatentyp natürl. Person)",
		"Adressatentyp",
		"Straße",
		"Postleitzahl",
		"Ort",
		"Land",
	):
		if name not in index:
			ergebnis.fehler_(2, "Spalten", f"Spalte {name!r} fehlt")
	if ergebnis.fehler:
		return ergebnis
	n = kopf["sachkontenlaenge"]
	gesehen: set[str] = set()
	for nr, zeile in enumerate(zeilen[2:], start=3):
		werte = _felder(zeile)
		if len(werte) != len(spalten):
			ergebnis.fehler_(nr, "Zeile", f"{len(werte)} Felder statt {len(spalten)}")
			continue
		ergebnis.zeilen += 1
		w = dict(zip(spalten, werte, strict=True))
		konto = w["Konto"]
		if not (RE_ZIFFERN.match(konto) and len(konto) == n + 1):
			ergebnis.fehler_(nr, "Konto", f"{konto!r} - Personenkonto mit {n + 1} Ziffern erwartet")
		elif konto in gesehen:
			ergebnis.fehler_(nr, "Konto", f"{konto} doppelt")
		gesehen.add(konto)
		typ = w["Adressatentyp"]
		if typ not in ("0", "1", "2"):
			ergebnis.fehler_(nr, "Adressatentyp", f"{typ!r} statt 0, 1 oder 2")
		elif typ == "1" and not w["Name (Adressatentyp natürl. Person)"]:
			ergebnis.fehler_(nr, "Name (Adressatentyp natürl. Person)", "leer")
		elif typ == "2" and not w["Name (Adressatentyp Unternehmen)"]:
			ergebnis.fehler_(nr, "Name (Adressatentyp Unternehmen)", "leer")
		for feld, max_len in (
			("Name (Adressatentyp Unternehmen)", 50),
			("Name (Adressatentyp natürl. Person)", 30),
			("Vorname (Adressatentyp natürl. Person)", 30),
			("Straße", 36),
			("Postleitzahl", 10),
			("Ort", 30),
			("E-Mail", 60),
			("Telefon", 60),
		):
			if len(w.get(feld, "")) > max_len:
				ergebnis.fehler_(nr, feld, f"{len(w[feld])} Zeichen, höchstens {max_len}")
		land = w["Land"]
		if land and not re.match(r"^[A-Z]{2}$", land):
			ergebnis.fehler_(nr, "Land", f"{land!r} ist kein zweistelliger Ländercode")
		_formelzeichen_pruefen(nr, w, frozenset({"Konto", "Adressatentyp"}), ergebnis)
	return ergebnis
