"""Kontoauszug lesen (7.5, ohne Frappe): CSV der Sparkassen und VR-Banken (Trennzeichen und Spalten
werden erkannt, eigenes Mapping möglich) und CAMT.053 (XML). Ergebnis sind ``Buchung``-Zeilen mit
Datum, Betrag (Gutschrift positiv, Abgang negativ), Name und IBAN des Zahlers, Verwendungszweck,
Buchungstext, Bankreferenz und einem Dublettenschlüssel.

Sicherheit: Die Datei ist Fremddaten. Größe und Typ prüft der Aufrufer (``zahlungseingang``); hier
werden Texte nur gelesen, nie ausgeführt. XML: kein DOCTYPE/ENTITY (XML-Bomben, externe Entitäten
abgewiesen), nur Elementtexte. CSV: Zellen mit führendem ``=``, ``+``, ``-``, ``@`` sind für einen
Export zu entschärfen (``export_sicher``), damit Tabellenprogramme keine Formeln ausführen.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from xml.etree import ElementTree

TRENNZEICHEN = (";", ",", "\t")
# Spaltenüberschriften (kleingeschrieben) je Bedeutung: Sparkasse (CSV-CAMT), VR-Bank/GLS, Postbank, allgemein
SPALTEN: dict[str, tuple[str, ...]] = {
	"datum": ("buchungstag", "buchungsdatum", "buchung", "datum", "date"),
	"valuta": ("valutadatum", "valuta", "wertstellung"),
	"betrag": ("betrag", "umsatz", "betrag (eur)", "betrag in eur", "amount", "umsatz in eur"),
	"waehrung": ("waehrung", "währung", "currency"),
	"verwendungszweck": ("verwendungszweck", "purpose", "zweck"),
	"buchungstext": ("buchungstext", "umsatzart", "vorgang", "transaction type", "text"),
	"name": (
		"beguenstigter/zahlungspflichtiger",
		"begünstigter/zahlungspflichtiger",
		"name zahlungsbeteiligter",
		"auftraggeber/empfaenger",
		"auftraggeber/empfänger",
		"auftraggeber / begünstigter",
		"zahlungspflichtiger",
		"beguenstigter",
		"begünstigter",
		"name",
	),
	"iban": ("kontonummer/iban", "iban zahlungsbeteiligter", "iban", "kontonummer"),
	"referenz": (
		"kundenreferenz (end-to-end)",
		"kundenreferenz",
		"end-to-end-referenz",
		"endtoend",
		"referenz",
	),
	"konto": ("auftragskonto", "iban auftragskonto", "kontonummer auftragskonto"),
}
PFLICHT = ("datum", "betrag")
DATUMSFORMATE = ("%d.%m.%y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y %H:%M:%S")
CAMT_VERBOTEN = (b"<!DOCTYPE", b"<!ENTITY")


class KontoauszugFehler(ValueError):
	pass


@dataclass
class Buchung:
	datum: date | None
	betrag: float
	waehrung: str = "EUR"
	name: str = ""
	iban: str = ""
	verwendungszweck: str = ""
	buchungstext: str = ""
	referenz: str = ""
	konto_iban: str = ""
	zeile: int = 0
	valuta: date | None = None
	hinweise: list[str] = field(default_factory=list)

	@property
	def gutschrift(self) -> bool:
		return self.betrag > 0

	def schluessel(self) -> str:
		"""Dublettenschlüssel: Bankreferenz (CAMT ``AcctSvcrRef``), sonst Datum, Betrag, IBAN, Name
		und normalisierter Verwendungszweck - gleiche Buchung zweimal eingelesen = gleicher Schlüssel."""
		if self.referenz and len(self.referenz) >= 8:
			basis = f"ref|{self.referenz.strip()}"
		else:
			basis = "|".join(
				(
					self.datum.isoformat() if self.datum else "",
					f"{self.betrag:.2f}",
					normalisiere(self.iban),
					normalisiere(self.name),
					normalisiere(self.verwendungszweck),
				)
			)
		return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:32]


# --- Hilfen -------------------------------------------------------------------------------------


def text_dekodieren(daten: bytes | str) -> str:
	"""Bank-Exporte kommen als UTF-8 (mit BOM) oder Windows-1252 (Sparkasse)."""
	if isinstance(daten, str):
		return daten
	for codec in ("utf-8-sig", "cp1252", "latin-1"):
		try:
			return daten.decode(codec)
		except UnicodeDecodeError:
			continue
	return daten.decode("utf-8", errors="replace")


def betrag_parsen(text) -> float:
	"""„1.234,56“, „-12,30“, „1234.56“, „1,234.56“, „12,5 EUR“ → float; leer → 0."""
	t = re.sub(r"[\s\u00a0]", "", str(text or ""))
	t = re.sub(r"[A-Za-z€$]", "", t)
	if not t:
		return 0.0
	negativ = t.startswith("-") or t.endswith("-") or (t.startswith("(") and t.endswith(")"))
	t = t.strip("-()+")
	if "," in t and "." in t:
		# das letzte Trennzeichen ist das Dezimaltrennzeichen
		if t.rfind(",") > t.rfind("."):
			t = t.replace(".", "").replace(",", ".")
		else:
			t = t.replace(",", "")
	elif "," in t:
		t = t.replace(",", ".")
	elif t.count(".") > 1:
		t = t.replace(".", "")
	try:
		wert = float(t)
	except ValueError:
		raise KontoauszugFehler(f"Betrag nicht lesbar: {text!r}")
	return -wert if negativ else wert


def datum_parsen(text) -> date | None:
	t = str(text or "").strip()
	if not t:
		return None
	for fmt in DATUMSFORMATE:
		try:
			return datetime.strptime(t[: len(datetime(2000, 1, 1).strftime(fmt))], fmt).date()
		except ValueError:
			continue
	try:
		return date.fromisoformat(t[:10])
	except ValueError:
		return None


def normalisiere(text) -> str:
	"""Großbuchstaben und Ziffern ohne Umlaute, Leer- und Satzzeichen (Vergleich von Nummern, Namen)."""
	t = str(text or "").upper()
	t = t.replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE").replace("ß", "SS")
	t = unicodedata.normalize("NFKD", t)
	return "".join(c for c in t if c.isalnum())


def rechnungsnummern_im_text(text, kandidaten) -> list[str]:
	"""Rechnungsnummern aus ``kandidaten``, die im Text vorkommen - unabhängig von Leerzeichen,
	Bindestrichen und Schreibweise („RE 2026 00003“ = „RE-2026-00003“)."""
	normal = normalisiere(text)
	if not normal:
		return []
	treffer = []
	for nummer in kandidaten:
		n = normalisiere(nummer)
		if len(n) >= 6 and n in normal:
			treffer.append(nummer)
	return treffer


_STOPP = frozenset(
	{
		"UND",
		"GMBH",
		"KG",
		"OHG",
		"GBR",
		"FAMILIE",
		"FAM",
		"HERR",
		"FRAU",
		"DR",
		"EHEL",
		"EHELEUTE",
		"UG",
		"AG",
	}
)


def _tokens(text) -> set[str]:
	t = str(text or "").upper().replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE").replace("ß", "SS")
	t = unicodedata.normalize("NFKD", t)
	woerter = re.findall(r"[A-Z0-9]{3,}", t)
	return {w for w in woerter if w not in _STOPP}


def namensaehnlichkeit(a, b) -> float:
	"""0 … 1: Anteil gemeinsamer Namensbestandteile (Wörter ab 3 Zeichen, ohne Floskeln) am kürzeren Namen."""
	ta, tb = _tokens(a), _tokens(b)
	if not ta or not tb:
		return 0.0
	return len(ta & tb) / min(len(ta), len(tb))


def export_sicher(wert) -> str:
	"""Zelle für einen CSV-Export: führende Formelzeichen entschärfen (keine Formel-Injection)."""
	t = str(wert if wert is not None else "")
	if t[:1] in ("=", "+", "-", "@", "\t", "\r"):
		return "'" + t
	return t


# --- CSV ----------------------------------------------------------------------------------------


def erkenne_trennzeichen(text: str) -> str:
	"""Trennzeichen aus den ersten Zeilen: das Zeichen, das in den meisten Zeilen gleich oft vorkommt."""
	zeilen = [z for z in text.splitlines()[:30] if z.strip()]
	if not zeilen:
		return ";"
	bestes, beste_zahl = ";", -1
	for zeichen in TRENNZEICHEN:
		zahlen = [z.count(zeichen) for z in zeilen]
		haeufig = max(set(zahlen), key=zahlen.count) if zahlen else 0
		if haeufig == 0:
			continue
		treffer = sum(1 for z in zahlen if z == haeufig)
		if treffer > beste_zahl or (treffer == beste_zahl and haeufig > 1):
			bestes, beste_zahl = zeichen, treffer
	return bestes


def _spalte(kopf: list[str], bedeutung: str, mapping: dict | None) -> int | None:
	"""Index der Spalte: eigenes Mapping (Überschrift exakt, Groß-/Kleinschreibung egal), sonst Synonyme."""
	klein = [k.strip().lower() for k in kopf]
	eigene = (mapping or {}).get(bedeutung)
	if eigene:
		e = str(eigene).strip().lower()
		if e in klein:
			return klein.index(e)
		if e.isdigit() and 0 < int(e) <= len(kopf):
			return int(e) - 1
		return None
	for name in SPALTEN.get(bedeutung, ()):
		if name in klein:
			return klein.index(name)
	return None


def _kopfzeile_finden(zeilen: list[list[str]], mapping: dict | None) -> int | None:
	"""Erste Zeile, die Datum und Betrag als Spalten nennt (Exporte haben teils Vorspann)."""
	for i, zeile in enumerate(zeilen[:30]):
		if all(_spalte(zeile, b, mapping) is not None for b in PFLICHT):
			return i
	return None


def lese_csv(
	daten: bytes | str, mapping: dict | None = None, trennzeichen: str | None = None
) -> tuple[list[Buchung], list[str]]:
	"""CSV lesen. Rückgabe ``(buchungen, hinweise)``; ohne erkennbare Kopfzeile ``KontoauszugFehler``."""
	text = text_dekodieren(daten)
	if not text.strip():
		raise KontoauszugFehler("Die Datei ist leer.")
	tz = trennzeichen or erkenne_trennzeichen(text)
	if tz == "Tab":
		tz = "\t"
	zeilen = list(csv.reader(io.StringIO(text), delimiter=tz, quotechar='"'))
	kopf_index = _kopfzeile_finden(zeilen, mapping)
	if kopf_index is None:
		raise KontoauszugFehler(
			"Keine Kopfzeile mit Datum und Betrag gefunden. Bitte die Spalten in den Abrechnungs-Einstellungen "
			"(Zahlungseingang) angeben."
		)
	kopf = zeilen[kopf_index]
	idx = {b: _spalte(kopf, b, mapping) for b in SPALTEN}
	hinweise: list[str] = []
	if idx["verwendungszweck"] is None:
		hinweise.append("Keine Spalte „Verwendungszweck“ erkannt - Zuordnung nur über Betrag und Name.")
	buchungen: list[Buchung] = []
	for nr, zeile in enumerate(zeilen[kopf_index + 1 :], start=kopf_index + 2):
		if not any(z.strip() for z in zeile):
			continue

		def wert(bedeutung: str, _zeile=zeile) -> str:
			i = idx.get(bedeutung)
			return _zeile[i].strip() if i is not None and i < len(_zeile) else ""

		datum = datum_parsen(wert("datum")) or datum_parsen(wert("valuta"))
		try:
			betrag = betrag_parsen(wert("betrag"))
		except KontoauszugFehler as fehler:
			hinweise.append(f"Zeile {nr}: {fehler}")
			continue
		if datum is None:
			hinweise.append(f"Zeile {nr}: kein Datum ({wert('datum')!r}) - übersprungen.")
			continue
		buchungen.append(
			Buchung(
				datum=datum,
				betrag=betrag,
				waehrung=(wert("waehrung") or "EUR").upper(),
				name=wert("name"),
				iban=normalisiere(wert("iban")) if wert("iban") else "",
				verwendungszweck=re.sub(r"\s+", " ", wert("verwendungszweck")),
				buchungstext=wert("buchungstext"),
				referenz=wert("referenz") if wert("referenz").upper() not in ("", "NOTPROVIDED") else "",
				konto_iban=normalisiere(wert("konto")) if wert("konto") else "",
				zeile=nr,
				valuta=datum_parsen(wert("valuta")),
			)
		)
	if not buchungen:
		hinweise.append("Keine Buchungen in der Datei.")
	return buchungen, hinweise


# --- CAMT.053 -----------------------------------------------------------------------------------


def _t(element, pfad: str) -> str:
	"""Text des ersten Elements unter ``pfad`` (Namensraum egal), leer wenn nicht vorhanden."""
	if element is None:
		return ""
	teil = element.find(pfad)
	return (teil.text or "").strip() if teil is not None else ""


def ist_camt(daten: bytes | str) -> bool:
	anfang = daten[:400] if isinstance(daten, bytes) else daten[:400].encode("utf-8", "ignore")
	return b"<Document" in anfang or b"camt.05" in anfang or b"BkToCstmrStmt" in anfang


def lese_camt(daten: bytes | str) -> tuple[list[Buchung], list[str]]:
	"""CAMT.053 (camt.053.001.02 … .08): je ``Ntry`` eine Buchung; Gutschrift (CRDT) positiv."""
	roh = daten.encode("utf-8") if isinstance(daten, str) else daten
	kopf = roh[:4000].upper()
	if any(v.upper() in kopf for v in CAMT_VERBOTEN):
		raise KontoauszugFehler("XML mit DOCTYPE/ENTITY wird nicht gelesen.")
	try:
		wurzel = ElementTree.fromstring(roh)
	except ElementTree.ParseError as fehler:
		raise KontoauszugFehler(f"XML nicht lesbar: {fehler}")
	hinweise: list[str] = []
	buchungen: list[Buchung] = []
	konto = ""
	for stmt in wurzel.iter():
		if stmt.tag.endswith("}Stmt") or stmt.tag == "Stmt":
			konto = normalisiere(_t(stmt, "{*}Acct/{*}Id/{*}IBAN"))
			break
	for nr, ntry in enumerate(
		(e for e in wurzel.iter() if e.tag.endswith("}Ntry") or e.tag == "Ntry"), start=1
	):
		amt = ntry.find("{*}Amt")
		if amt is None or not (amt.text or "").strip():
			hinweise.append(f"Buchung {nr}: kein Betrag - übersprungen.")
			continue
		try:
			betrag = float((amt.text or "0").strip())
		except ValueError:
			hinweise.append(f"Buchung {nr}: Betrag nicht lesbar ({amt.text!r}).")
			continue
		if _t(ntry, "{*}CdtDbtInd").upper() == "DBIT":
			betrag = -abs(betrag)
		if _t(ntry, "{*}Sts") and _t(ntry, "{*}Sts").upper() not in ("BOOK", "BOOKED"):
			# nur gebuchte Umsätze (keine vorgemerkten)
			sts = ntry.find("{*}Sts")
			if sts is not None and (sts.text or "").strip().upper() not in ("BOOK",) and not _t(sts, "{*}Cd"):
				hinweise.append(f"Buchung {nr}: Status {sts.text} - übersprungen.")
				continue
		datum = datum_parsen(_t(ntry, "{*}BookgDt/{*}Dt") or _t(ntry, "{*}BookgDt/{*}DtTm"))
		valuta = datum_parsen(_t(ntry, "{*}ValDt/{*}Dt") or _t(ntry, "{*}ValDt/{*}DtTm"))
		tx = ntry.find("{*}NtryDtls/{*}TxDtls")
		gegenseite = "Dbtr" if betrag > 0 else "Cdtr"
		name = _t(tx, f"{{*}}RltdPties/{{*}}{gegenseite}/{{*}}Nm") or _t(
			tx, f"{{*}}RltdPties/{{*}}{gegenseite}/{{*}}Pty/{{*}}Nm"
		)
		iban = _t(tx, f"{{*}}RltdPties/{{*}}{gegenseite}Acct/{{*}}Id/{{*}}IBAN")
		zweck = " ".join(
			(u.text or "").strip()
			for u in (tx.findall("{*}RmtInf/{*}Ustrd") if tx is not None else [])
			if u.text
		)
		referenz = _t(ntry, "{*}AcctSvcrRef") or _t(tx, "{*}Refs/{*}AcctSvcrRef")
		e2e = _t(tx, "{*}Refs/{*}EndToEndId")
		buchungen.append(
			Buchung(
				datum=datum or valuta,
				betrag=betrag,
				waehrung=(amt.get("Ccy") or "EUR").upper(),
				name=name,
				iban=normalisiere(iban) if iban else "",
				verwendungszweck=re.sub(
					r"\s+",
					" ",
					zweck + (f" {e2e}" if e2e and e2e.upper() != "NOTPROVIDED" and e2e not in zweck else ""),
				),
				buchungstext=_t(ntry, "{*}AddtlNtryInf") or _t(ntry, "{*}BkTxCd/{*}Prtry/{*}Cd"),
				referenz=referenz,
				konto_iban=konto,
				zeile=nr,
				valuta=valuta,
			)
		)
	if not buchungen:
		hinweise.append("Keine Buchungen (Ntry) in der Datei.")
	return buchungen, hinweise


def lese(
	daten: bytes | str, dateiname: str = "", mapping: dict | None = None, trennzeichen: str | None = None
) -> tuple[list[Buchung], list[str], str]:
	"""Dateityp erkennen (Endung, Inhalt) und lesen. Rückgabe ``(buchungen, hinweise, format)``."""
	endung = dateiname.rsplit(".", 1)[-1].lower() if "." in dateiname else ""
	if endung == "xml" or (endung not in ("csv", "txt") and ist_camt(daten)):
		buchungen, hinweise = lese_camt(daten)
		return buchungen, hinweise, "camt"
	buchungen, hinweise = lese_csv(daten, mapping=mapping, trennzeichen=trennzeichen)
	return buchungen, hinweise, "csv"
