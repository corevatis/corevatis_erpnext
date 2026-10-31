"""DATEV-Monatsexport (7.4): Buchungsstapel über erpnext_datev, ergänzt um das, was fehlt.

Ablauf ``erstellen(doc)`` für einen ``DATEV Export`` (Firma, Zeitraum, Optionen):

1. Voraussetzungen: erpnext_datev installiert, DATEV Settings der Firma, Zeitraum in einem Geschäftsjahr.
2. Buchungszeilen holen - mit den Abfragen von erpnext_datev (``run_query`` je Belegart), nur nicht
   stornierte Hauptbucheinträge (``is_cancelled = 0``; der offene Upstream-PR #30 fehlt in 16.0.2).
   Standard „Rechnungen und Zahlungen“ (Sales Invoice, Payment Entry), wahlweise alle Buchungen.
3. Ergänzen je Zeile: Konto = Debitorennummer bei Kundenzeilen (statt Sammelkonto 1400), Belegfeld 1 =
   Rechnungsnummer (bei Zahlungen die bezahlte Rechnung, für den OPOS-Ausgleich), Belegfeld 2 und
   Auftragsnummer = Auftrag, Buchungstext „Abschlag 1 Kunde Auftrag“ / „Zahlung RE-… Kunde“, Beleginfo
   Auftrag und Vorgang. Gegenkonto bleibt das Übergangskonto 9090 (je Zeile eine Buchung, Steuerautomatik
   aus - Ansatz von erpnext_datev).
4. CSV über ``erpnext_datev.utils.datev_csv.get_datev_csv`` (Kopfzeile, 120 Spalten, cp1252, CRLF),
   Festschreibung 0/1 aus dem Export; Formatprüfung (``pruefung``) - bei Fehlern wird nichts gespeichert.
5. Debitoren-Stammdaten der betroffenen Kunden (``debitoren``), Belegbilder als ZIP (Rechnungs-PDFs,
   Dateiname = Rechnungsnummer), Dateien privat am Export.
6. Markierung ``cv_datev_export``/``cv_datev_exportiert_am`` an Rechnung und Zahlung; Protokoll mit
   Zeitraum, Anzahl, Summen, Dateien, Hinweisen (Platzhalter, Wiederholung, Storno nach Export).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field

import frappe
from frappe import _
from frappe.utils import (
	add_days,
	cint,
	flt,
	fmt_money,
	format_date,
	format_datetime,
	get_first_day,
	get_last_day,
	getdate,
	now_datetime,
)

from corevatis_erpnext.datev import debitoren, pruefung
from corevatis_erpnext.datev.felder import FELD_EXPORT, FELD_EXPORTIERT_AM

APP = "erpnext_datev"
BELEGARTEN_STANDARD = "Rechnungen und Zahlungen"
BELEGARTEN_ALLE = "Alle Buchungen"
STATUS_ENTWURF, STATUS_ERSTELLT, STATUS_FEHLER = "Entwurf", "Erstellt", "Fehlgeschlagen"
ART_RECHNUNG, ART_GUTSCHRIFT, ART_ZAHLUNG, ART_SONSTIGE = "Rechnung", "Gutschrift", "Zahlung", "Sonstige"
UEBERGANGSKONTO_STANDARD = "9090"
MAX_BUCHUNGSTEXT = 60
MAX_BELEGFELD1 = 36
MAX_BELEGFELD2 = 12
MAX_AUFTRAGSNUMMER = 30
MAX_BELEGINFO_INHALT = 210
RE_BELEGFELD = re.compile(r"[^A-Za-z0-9$&%*+\-/]")
RE_DATEINAME = re.compile(r"[^A-Za-z0-9_-]+")
# Spaltennamen des Buchungsstapels (erpnext_datev.utils.datev_constants.TRANSACTION_COLUMNS)
SP_UMSATZ = "Umsatz (ohne Soll/Haben-Kz)"
SP_SH = "Soll/Haben-Kennzeichen"
SP_KONTO = "Konto"
SP_BELEGDATUM = "Belegdatum"
SP_BELEGFELD1 = "Belegfeld 1"
SP_BELEGFELD2 = "Belegfeld 2"
SP_BUCHUNGSTEXT = "Buchungstext"
SP_AUFTRAGSNUMMER = "Auftragsnummer"
SP_ART = {i: f"Beleginfo - Art {i}" for i in range(1, 9)}
SP_INHALT = {i: f"Beleginfo - Inhalt {i}" for i in range(1, 9)}
# Zellen mit festem Format (Zahl, Kennzeichen, Konto, Datum), die nicht als Text behandelt werden
SPALTEN_FEST = frozenset(
	{
		SP_UMSATZ,
		SP_SH,
		SP_KONTO,
		"Gegenkonto (ohne BU-Schlüssel)",
		"BU-Schlüssel",
		SP_BELEGDATUM,
		SP_BELEGFELD1,
		SP_BELEGFELD2,
		"Fälligkeit",
		"Festschreibung",
		SP_INHALT[6],  # Datum (Buchungstag bzw. Lieferanten-Rechnungsdatum)
	}
)
SPALTEN_LAENGE = {SP_BUCHUNGSTEXT: MAX_BUCHUNGSTEXT, SP_AUFTRAGSNUMMER: MAX_AUFTRAGSNUMMER}
SPALTEN_LAENGE.update({SP_ART[i]: 20 for i in range(1, 9)})


class DatevFehler(frappe.ValidationError):
	pass


@dataclass
class Beleg:
	doctype: str
	name: str
	art: str
	datum: object = None
	kunde: str | None = None
	kunde_name: str = ""
	brutto: float = 0.0
	auftrag: str | None = None
	projekt: str | None = None
	zuvor: str | None = None
	zuvor_am: object = None
	rechnungen: list[str] = field(default_factory=list)
	buchungstext: str = ""
	zeilen: int = 0
	belegbild: int = 0

	def belegnummer(self, zeile: dict) -> str:
		"""Belegfeld 1: Rechnungsnummer; bei Zahlungen die bezahlte Rechnung (OPOS-Ausgleich)."""
		if self.doctype == "Payment Entry":
			if zeile.get(SP_ART[2]) == "Sales Invoice" and zeile.get(SP_INHALT[2]):
				return zeile[SP_INHALT[2]]
			if len(self.rechnungen) == 1:
				return self.rechnungen[0]
		return self.name


# --- Voraussetzungen ---------------------------------------------------------------------------


def app_installiert() -> bool:
	return APP in frappe.get_installed_apps()


def einstellungen_pruefen(company: str) -> frappe._dict:
	if not app_installiert():
		frappe.throw(
			_(
				"Die App erpnext_datev ist auf dieser Site nicht installiert - ohne sie gibt es keinen DATEV-Export."
			),
			DatevFehler,
		)
	e = debitoren.einstellungen(company)
	if not e:
		frappe.throw(
			_(
				"Für die Firma {0} gibt es noch keine DATEV-Einstellungen (Beraternummer, Mandantennummer). "
				"Site-Template anwenden oder „DATEV Settings“ anlegen."
			).format(frappe.bold(company)),
			DatevFehler,
		)
	return e


def filter_bauen(doc, e: frappe._dict) -> dict:
	"""Filter wie der Report „DATEV“ von erpnext_datev (Zeitraum, Gegenkonten, Kontenlänge, SKR, GJ)."""
	from erpnext.accounts.utils import get_fiscal_year
	from erpnext_datev.erpnext_datev.report.datev.datev import validate_fiscal_year

	von, bis = getdate(doc.von), getdate(doc.bis)
	if von > bis:
		frappe.throw(_("Der Zeitraum beginnt nach seinem Ende."), DatevFehler)
	validate_fiscal_year(von, bis, doc.firma)
	fy = get_fiscal_year(von, company=doc.firma)
	coa = frappe.db.get_value("Company", doc.firma, "chart_of_accounts") or ""
	gegenkonto = e.temporary_against_account_number or UEBERGANGSKONTO_STANDARD
	return {
		"company": doc.firma,
		"from_date": str(von),
		"to_date": str(bis),
		"fiscal_year_start": fy[1],
		"skr": "04" if "SKR04" in coa else ("03" if "SKR03" in coa else ""),
		"account_number_length": cint(e.account_number_length) or 4,
		"against_account": gegenkonto,
		"opening_account": e.opening_against_account_number or gegenkonto,
	}


# --- Buchungszeilen ----------------------------------------------------------------------------


def buchungszeilen(filters: dict, alle: bool) -> list[dict]:
	"""Hauptbucheinträge des Zeitraums über die Abfragen von erpnext_datev, ohne stornierte."""
	from erpnext_datev.erpnext_datev.report.datev import datev as upstream

	nur_gueltig = " AND gl.is_cancelled = 0"
	methoden = [upstream.get_sales_invoice_params, upstream.get_payment_entry_params]
	if alle:
		methoden.append(upstream.get_purchase_invoice_params)
	zeilen: list[dict] = []
	for methode in methoden:
		felder, joins, where = methode(filters)
		zeilen += upstream.run_query(filters, felder, joins, where + nur_gueltig, as_dict=1)
	if alle:
		f = dict(filters, exclude_voucher_types=["Sales Invoice", "Payment Entry", "Purchase Invoice"])
		felder, joins, where = upstream.get_generic_params(f)
		zeilen += upstream.run_query(f, felder, joins, where + nur_gueltig, as_dict=1)
	return zeilen


def _schluessel(zeile: dict) -> tuple[str, str]:
	return zeile.get(SP_ART[1]) or "", zeile.get(SP_INHALT[1]) or ""


def belege_laden(zeilen: list[dict]) -> dict[tuple[str, str], Beleg]:
	"""Belege zu den Zeilen: Art, Kunde, Brutto, Auftrag, Vorgang, frühere Exporte, Buchungstext."""
	belege: dict[tuple[str, str], Beleg] = {}
	je_typ: dict[str, set[str]] = {}
	for z in zeilen:
		typ, name = _schluessel(z)
		je_typ.setdefault(typ, set()).add(name)

	for name in je_typ.get("Sales Invoice", ()):
		belege[("Sales Invoice", name)] = Beleg("Sales Invoice", name, ART_RECHNUNG)
	rechnungen = (
		frappe.get_all(
			"Sales Invoice",
			filters={"name": ["in", list(je_typ.get("Sales Invoice", ()))]},
			fields=[
				"name",
				"customer",
				"customer_name",
				"is_return",
				"return_against",
				"cv_abschlag",
				"cv_abschlag_nr",
				"cv_schlussrechnung",
				"cv_auftrag",
				"project",
				"posting_date",
				"grand_total",
				FELD_EXPORT,
				FELD_EXPORTIERT_AM,
			],
		)
		if je_typ.get("Sales Invoice")
		else []
	)
	for r in rechnungen:
		b = belege[("Sales Invoice", r.name)]
		b.art = ART_GUTSCHRIFT if cint(r.is_return) else ART_RECHNUNG
		b.datum, b.kunde, b.kunde_name = r.posting_date, r.customer, r.customer_name or r.customer or ""
		b.brutto, b.auftrag, b.projekt = flt(r.grand_total), r.cv_auftrag, r.project
		b.zuvor, b.zuvor_am = r.get(FELD_EXPORT), r.get(FELD_EXPORTIERT_AM)
		if cint(r.is_return):
			art = _("Gutschrift zu {0}").format(r.return_against) if r.return_against else _("Gutschrift")
		elif cint(r.cv_schlussrechnung):
			art = _("Schlussrechnung")
		elif cint(r.cv_abschlag):
			art = _("Abschlag {0}").format(r.cv_abschlag_nr) if r.cv_abschlag_nr else _("Abschlag")
		else:
			art = _("Rechnung")
		b.buchungstext = " ".join(t for t in (art, b.kunde_name, b.auftrag) if t)

	pe_namen = list(je_typ.get("Payment Entry", ()))
	for name in pe_namen:
		belege[("Payment Entry", name)] = Beleg("Payment Entry", name, ART_ZAHLUNG)
	if pe_namen:
		referenzen: dict[str, list[str]] = {}
		for ref in frappe.get_all(
			"Payment Entry Reference",
			filters={"parent": ["in", pe_namen], "reference_doctype": "Sales Invoice"},
			fields=["parent", "reference_name"],
			order_by="idx asc",
		):
			referenzen.setdefault(ref.parent, []).append(ref.reference_name)
		auftraege: dict[str, frappe._dict] = {}
		alle_rechnungen = sorted({r for liste in referenzen.values() for r in liste})
		if alle_rechnungen:
			for r in frappe.get_all(
				"Sales Invoice",
				filters={"name": ["in", alle_rechnungen]},
				fields=["name", "cv_auftrag", "project"],
			):
				auftraege[r.name] = r
		for p in frappe.get_all(
			"Payment Entry",
			filters={"name": ["in", pe_namen]},
			fields=[
				"name",
				"payment_type",
				"party_type",
				"party",
				"party_name",
				"paid_amount",
				"posting_date",
				FELD_EXPORT,
				FELD_EXPORTIERT_AM,
			],
		):
			b = belege[("Payment Entry", p.name)]
			b.datum, b.kunde_name, b.brutto = (
				p.posting_date,
				p.party_name or p.party or "",
				flt(p.paid_amount),
			)
			b.kunde = p.party if p.party_type == "Customer" else None
			b.zuvor, b.zuvor_am = p.get(FELD_EXPORT), p.get(FELD_EXPORTIERT_AM)
			b.rechnungen = sorted(set(referenzen.get(p.name, [])))
			erste = auftraege.get(b.rechnungen[0]) if b.rechnungen else None
			if erste:
				b.auftrag, b.projekt = erste.cv_auftrag, erste.project
			art = _("Zahlung an") if p.payment_type == "Pay" else _("Zahlung")
			b.buchungstext = " ".join(t for t in (art, ", ".join(b.rechnungen), b.kunde_name) if t)

	for typ, namen in je_typ.items():
		if typ in ("Sales Invoice", "Payment Entry"):
			continue
		for name in namen:
			belege[(typ, name)] = Beleg(typ, name, ART_SONSTIGE)
	return belege


# --- Ergänzen je Zeile -------------------------------------------------------------------------


def belegfeld(wert: str | None, max_len: int = MAX_BELEGFELD1) -> str:
	"""Nur die in Belegfeld 1/2 erlaubten Zeichen (Buchstaben, Ziffern, ``$&%*+-/``), ohne führendes
	``+``/``-`` (Formelzeichen), gekürzt."""
	return RE_BELEGFELD.sub("", wert or "").lstrip("+-")[:max_len]


def belegfeld2(auftrag: str | None) -> str:
	"""Auftragsnummer für Belegfeld 2 (12 Zeichen): passt sie nicht, fallen zuerst die Bindestriche weg
	(``AB-2026-00008`` → ``AB202600008``); die volle Nummer steht in „Auftragsnummer“ und Beleginfo 7."""
	s = belegfeld(auftrag, MAX_BELEGFELD1)
	if len(s) > MAX_BELEGFELD2:
		s = s.replace("-", "")
	return s[:MAX_BELEGFELD2]


def zeile_ergaenzen(z: dict, beleg: Beleg | None, nummern: dict[str, str]) -> None:
	if z.get(SP_ART[3]) == "Customer" and z.get(SP_INHALT[3]) in nummern:
		nr = nummern[z[SP_INHALT[3]]]
		z[SP_KONTO] = nr
		z[SP_ART[4]], z[SP_INHALT[4]] = "Debitorennummer", nr
	if beleg:
		beleg.zeilen += 1
		z[SP_BELEGFELD1] = belegfeld(beleg.belegnummer(z))
		if beleg.auftrag:
			z[SP_BELEGFELD2] = belegfeld2(beleg.auftrag)
			z[SP_AUFTRAGSNUMMER] = debitoren.text(beleg.auftrag, MAX_AUFTRAGSNUMMER)
			z[SP_ART[7]], z[SP_INHALT[7]] = "Auftrag", debitoren.text(beleg.auftrag, 210)
		if beleg.projekt:
			z[SP_ART[8]], z[SP_INHALT[8]] = "Vorgang", debitoren.text(beleg.projekt, 210)
		if beleg.buchungstext:
			z[SP_BUCHUNGSTEXT] = debitoren.text(beleg.buchungstext, MAX_BUCHUNGSTEXT)
		else:
			z[SP_BUCHUNGSTEXT] = debitoren.text(z.get(SP_BUCHUNGSTEXT), MAX_BUCHUNGSTEXT)
	else:
		z[SP_BELEGFELD1] = belegfeld(z.get(SP_BELEGFELD1))
		z[SP_BUCHUNGSTEXT] = debitoren.text(z.get(SP_BUCHUNGSTEXT), MAX_BUCHUNGSTEXT)
	# Jede Textzelle (auch von erpnext_datev gelieferte wie Zahlungsreferenz oder Lieferanten-
	# Rechnungsnummer) ohne führende Formelzeichen, Anführungszeichen und Semikolon
	for spalte, wert in z.items():
		if isinstance(wert, str) and wert and spalte not in SPALTEN_FEST:
			z[spalte] = debitoren.text(wert, SPALTEN_LAENGE.get(spalte, MAX_BELEGINFO_INHALT))


# --- Dateien -----------------------------------------------------------------------------------


def buchungsstapel_csv(zeilen: list[dict], filters: dict, festschreibung: int) -> bytes:
	"""EXTF-Buchungsstapel über erpnext_datev; Kopffeld U (Festschreibung) aus dem Export."""
	from erpnext_datev.utils.datev_constants import Transactions
	from erpnext_datev.utils.datev_csv import get_datev_csv

	daten = get_datev_csv(zeilen, filters, csv_class=Transactions)
	if festschreibung:
		kopf, rest = daten.split(b"\r\n", 1)
		felder = kopf.split(b";")
		felder[20] = b"1"
		daten = b";".join(felder) + b"\r\n" + rest
	return daten


def debitoren_csv(kunden: list[str], company: str, filters: dict) -> tuple[bytes, int, list[str]]:
	from erpnext_datev.utils.datev_constants import DebtorsCreditors
	from erpnext_datev.utils.datev_csv import get_datev_csv

	zeilen, hinweise = debitoren.stammdaten_zeilen(kunden, company)
	return get_datev_csv(zeilen, filters, csv_class=DebtorsCreditors), len(zeilen), hinweise


def dateiname(beleg: str) -> str:
	return RE_DATEINAME.sub("_", beleg).strip("_") or "Beleg"


def druckformat_waehlen(doc, e: frappe._dict) -> str | None:
	for kandidat in (doc.get("druckformat"), e.get("cv_druckformat")):
		if kandidat and frappe.db.exists("Print Format", kandidat):
			return kandidat
	return frappe.get_meta("Sales Invoice").default_print_format or None


def belegbilder_zip(rechnungen: list[Beleg], druckformat: str | None) -> tuple[bytes, int, list[str]]:
	"""Rechnungs-PDFs (Belegbilder) als ZIP; Dateiname = Rechnungsnummer, dazu ``Belegliste.csv``."""
	puffer = io.BytesIO()
	fehler: list[str] = []
	anzahl = 0
	liste = io.StringIO()
	index = csv.writer(liste, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
	index.writerow(["Belegnummer", "Datum", "Kunde", "Brutto", "Datei"])
	with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as zf:
		for b in rechnungen:
			try:
				pdf = frappe.get_print("Sales Invoice", b.name, print_format=druckformat, as_pdf=True)
			except Exception as exc:
				frappe.log_error(
					title=f"DATEV-Export: Belegbild {b.name} nicht erzeugt",
					reference_doctype="Sales Invoice",
					reference_name=b.name,
				)
				fehler.append(
					_("{0}: Belegbild nicht erzeugt ({1})").format(b.name, debitoren.text(exc, 120))
				)
				continue
			datei = f"{dateiname(b.name)}.pdf"
			zf.writestr(datei, pdf)
			b.belegbild = 1
			anzahl += 1
			index.writerow(
				[
					debitoren.text(b.name, 36),
					format_date(b.datum) if b.datum else "",
					debitoren.text(b.kunde_name, 60),
					f"{b.brutto:.2f}".replace(".", ","),
					datei,
				]
			)
		zf.writestr("Belegliste.csv", liste.getvalue().encode("cp1252", "replace"))
	return puffer.getvalue(), anzahl, fehler


def datei_speichern(doc, feld: str, name: str, inhalt: bytes) -> str:
	f = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": name,
			"is_private": 1,
			"content": inhalt,
			"attached_to_doctype": doc.doctype,
			"attached_to_name": doc.name,
			"attached_to_field": feld,
		}
	)
	f.flags.ignore_duplicate_entry_error = True
	f.insert(ignore_permissions=True)
	return f.file_url


def zeitraum_kennung(von, bis) -> str:
	von, bis = getdate(von), getdate(bis)
	if von == get_first_day(von) and bis == get_last_day(von):
		return f"{von:%Y-%m}"
	return f"{von:%Y-%m-%d}_{bis:%Y-%m-%d}"


def titel(doc) -> str:
	abbr = frappe.db.get_value("Company", doc.firma, "abbr") or doc.firma
	von, bis = getdate(doc.von), getdate(doc.bis)
	if von == get_first_day(von) and bis == get_last_day(von):
		return f"DATEV {abbr} {von:%m/%Y}"
	return f"DATEV {abbr} {format_date(von)} - {format_date(bis)}"


# --- Export --------------------------------------------------------------------------------------


def _betrag(wert) -> str:
	return fmt_money(flt(wert), precision=2, currency="EUR")


def storno_hinweise(doc) -> list[str]:
	"""Belege, die nach einem früheren Export storniert wurden (Stornobuchung beim Steuerberater nötig)."""
	hinweise = []
	for doctype in ("Sales Invoice", "Payment Entry"):
		for b in frappe.get_all(
			doctype,
			filters={
				"company": doc.firma,
				"docstatus": 2,
				FELD_EXPORT: ["is", "set"],
				"modified": ["between", [str(getdate(doc.von)), str(add_days(getdate(doc.bis), 1))]],
			},
			fields=["name", FELD_EXPORT, "modified"],
		):
			hinweise.append(
				_(
					"{0} wurde am {1} storniert, war aber schon in {2} exportiert - Stornobuchung dem Steuerberater melden."
				).format(b.name, format_date(b.modified), b.get(FELD_EXPORT))
			)
	return hinweise


def erstellen(doc) -> dict:
	"""Export erzeugen (siehe Modul-Doku). Rückgabe: ``ok``, ``zusammenfassung`` (Protokoll), Kennzahlen."""
	if doc.status == STATUS_ERSTELLT:
		frappe.throw(
			_(
				"Der Export {0} ist schon erstellt. Für eine Wiederholung bitte einen neuen Export anlegen."
			).format(doc.name),
			DatevFehler,
		)
	frappe.has_permission("Company", doc=doc.firma, throw=True)
	e = einstellungen_pruefen(doc.firma)
	filters = filter_bauen(doc, e)
	alle = doc.belegarten == BELEGARTEN_ALLE
	zeilen = buchungszeilen(filters, alle)
	belege = belege_laden(zeilen)
	hinweise: list[str] = []

	if cint(doc.nur_neue):
		schon = {k for k, b in belege.items() if b.zuvor}
		if schon:
			zeilen = [z for z in zeilen if _schluessel(z) not in schon]
			for k in schon:
				belege.pop(k)
			hinweise.append(
				_("{0} schon exportierte Belege ausgelassen (nur neue Belege).").format(len(schon))
			)
	if not zeilen:
		frappe.throw(
			_("Keine Buchungen für {0} im Zeitraum {1} bis {2}{3}.").format(
				doc.firma,
				format_date(doc.von),
				format_date(doc.bis),
				_(" (alle Belege schon exportiert)") if cint(doc.nur_neue) else "",
			),
			DatevFehler,
		)

	# Debitorennummern der betroffenen Kunden (vergeben, falls noch keine)
	kunden = sorted(
		{z[SP_INHALT[3]] for z in zeilen if z.get(SP_ART[3]) == "Customer" and z.get(SP_INHALT[3])}
	)
	nummern: dict[str, str] = {}
	neu_vergeben: list[str] = []
	for kunde in kunden:
		nr, neu = debitoren.vergeben(kunde, doc.firma, e)
		if nr:
			nummern[kunde] = nr
			if neu:
				neu_vergeben.append(f"{kunde} -> {nr}")
	for z in zeilen:
		zeile_ergaenzen(z, belege.get(_schluessel(z)), nummern)
	zeilen.sort(
		key=lambda z: (str(z.get(SP_BELEGDATUM) or ""), z.get(SP_INHALT[1]) or "", str(z.get(SP_KONTO) or ""))
	)

	stapel = buchungsstapel_csv(zeilen, filters, cint(doc.festschreibung))
	ergebnis = pruefung.pruefe_buchungsstapel(stapel)
	# Dateiname: Kürzel, Zeitraum und Exportnummer - eindeutig auch bei Wiederholungen im selben Monat
	abbr = frappe.db.get_value("Company", doc.firma, "abbr") or "FIRMA"
	kennung = f"{zeitraum_kennung(doc.von, doc.bis)}_{doc.name}"
	jetzt = now_datetime()

	doc.flags.cv_server = True
	doc.titel = titel(doc)
	doc.erstellt_am, doc.erstellt_von = jetzt, frappe.session.user
	doc.anzahl_buchungen = len(zeilen)
	doc.summe_soll, doc.summe_haben = flt(ergebnis.summe_soll), flt(ergebnis.summe_haben)
	doc.pruefung = "OK" if ergebnis.ok else "Fehler"

	if not ergebnis.ok:
		doc.status = STATUS_FEHLER
		doc.protokoll = "\n".join(
			[
				_("DATEV-Export {0} fehlgeschlagen - Formatprüfung des Buchungsstapels:").format(doc.name),
				ergebnis.text(max_zeilen=50),
				_("Es wurde nichts gespeichert und kein Beleg markiert."),
			]
		)
		doc.save()
		return {"ok": False, "zusammenfassung": doc.protokoll, "fehler": [str(f) for f in ergebnis.fehler]}

	# Dateien
	doc.datei_buchungsstapel = datei_speichern(
		doc, "datei_buchungsstapel", f"EXTF_Buchungsstapel_{abbr}_{kennung}.csv", stapel
	)
	deb_csv, doc.anzahl_debitoren, deb_hinweise = debitoren_csv(kunden, doc.firma, filters)
	hinweise += deb_hinweise
	deb_pruefung = pruefung.pruefe_debitoren(deb_csv)
	if not deb_pruefung.ok:
		hinweise += [_("Debitoren-Stammdaten: {0}").format(f) for f in deb_pruefung.fehler[:10]]
	doc.datei_debitoren = datei_speichern(
		doc, "datei_debitoren", f"EXTF_Debitoren_{abbr}_{kennung}.csv", deb_csv
	)

	rechnungen = sorted(
		(b for b in belege.values() if b.doctype == "Sales Invoice"), key=lambda b: (str(b.datum), b.name)
	)
	druckformat = None
	doc.anzahl_belegbilder = 0
	doc.datei_belegbilder = None
	if cint(doc.mit_belegbildern) and rechnungen:
		druckformat = druckformat_waehlen(doc, e)
		inhalt, doc.anzahl_belegbilder, bild_fehler = belegbilder_zip(rechnungen, druckformat)
		hinweise += bild_fehler
		doc.datei_belegbilder = datei_speichern(
			doc, "datei_belegbilder", f"Belegbilder_{abbr}_{kennung}.zip", inhalt
		)

	# Markierung und Belegliste
	wiederholt: list[str] = []
	doc.set("belege", [])
	for b in sorted(belege.values(), key=lambda b: (str(b.datum or ""), b.doctype, b.name)):
		if b.doctype in ("Sales Invoice", "Payment Entry"):
			if b.zuvor and b.zuvor != doc.name:
				wiederholt.append(
					_("{0} (zuvor in {1} am {2})").format(
						b.name, b.zuvor, format_datetime(b.zuvor_am) if b.zuvor_am else "?"
					)
				)
			frappe.db.set_value(
				b.doctype, b.name, {FELD_EXPORT: doc.name, FELD_EXPORTIERT_AM: jetzt}, update_modified=False
			)
		doc.append(
			"belege",
			{
				"belegart": b.doctype,
				"beleg": b.name,
				"datum": b.datum,
				"kunde": debitoren.text(b.kunde_name, 140),
				"brutto": b.brutto if b.art != ART_GUTSCHRIFT else -abs(b.brutto),
				"zeilen": b.zeilen,
				"zuvor_exportiert_in": b.zuvor if b.zuvor != doc.name else None,
				"belegbild": b.belegbild,
			},
		)
	doc.wiederholung = len(wiederholt)
	doc.anzahl_rechnungen = sum(1 for b in belege.values() if b.art == ART_RECHNUNG)
	doc.anzahl_gutschriften = sum(1 for b in belege.values() if b.art == ART_GUTSCHRIFT)
	doc.anzahl_zahlungen = sum(1 for b in belege.values() if b.art == ART_ZAHLUNG)
	doc.anzahl_sonstige = sum(1 for b in belege.values() if b.art == ART_SONSTIGE)
	doc.rechnungen_brutto = sum(b.brutto for b in belege.values() if b.art == ART_RECHNUNG) - sum(
		abs(b.brutto) for b in belege.values() if b.art == ART_GUTSCHRIFT
	)
	doc.zahlungen_brutto = sum(b.brutto for b in belege.values() if b.art == ART_ZAHLUNG)
	hinweise += storno_hinweise(doc)
	hinweise += [_("Kopfzeile: {0}").format(h) for h in ergebnis.hinweise]
	for pfad in ("client_number", "consultant_number"):
		if not e.get(pfad) or set(str(e.get(pfad))) == {"0"}:
			hinweise.append(
				_(
					"{0} ist noch ein Platzhalter - Wert vom Steuerberater in den DATEV-Einstellungen eintragen."
				).format(_("Mandantennummer") if pfad == "client_number" else _("Beraternummer"))
			)

	doc.status = STATUS_ERSTELLT
	doc.protokoll = protokoll(doc, e, filters, ergebnis, druckformat, neu_vergeben, wiederholt, hinweise)
	doc.save()
	return {
		"ok": True,
		"name": doc.name,
		"zusammenfassung": doc.protokoll,
		"anzahl_buchungen": doc.anzahl_buchungen,
		"anzahl_rechnungen": doc.anzahl_rechnungen,
		"anzahl_gutschriften": doc.anzahl_gutschriften,
		"anzahl_zahlungen": doc.anzahl_zahlungen,
		"wiederholung": doc.wiederholung,
		"dateien": [d for d in (doc.datei_buchungsstapel, doc.datei_debitoren, doc.datei_belegbilder) if d],
	}


def protokoll(doc, e, filters, ergebnis, druckformat, neu_vergeben, wiederholt, hinweise) -> str:
	zeilen = [
		_("DATEV-Export {0} | {1} | Zeitraum {2} bis {3}").format(
			doc.name, doc.firma, format_date(doc.von), format_date(doc.bis)
		),
		_("Erstellt am {0} von {1} | Belegarten: {2} | Festschreibung: {3}").format(
			format_datetime(doc.erstellt_am),
			doc.erstellt_von,
			doc.belegarten,
			_("ja") if cint(doc.festschreibung) else _("nein"),
		),
		_(
			"Beraternummer {0} | Mandantennummer {1} | SKR{2} | Sachkontenlänge {3} | Gegenkonto {4} (Übergangskonto, je Zeile eine Buchung)"
		).format(
			e.consultant_number,
			e.client_number,
			filters["skr"] or "?",
			filters["account_number_length"],
			filters["against_account"],
		),
		_("Buchungszeilen: {0} (Soll {1}, Haben {2})").format(
			doc.anzahl_buchungen, _betrag(doc.summe_soll), _betrag(doc.summe_haben)
		),
		_(
			"Belege: {0} Rechnungen, {1} Gutschriften (Rechnungen brutto {2}), {3} Zahlungen ({4}), {5} sonstige"
		).format(
			doc.anzahl_rechnungen,
			doc.anzahl_gutschriften,
			_betrag(doc.rechnungen_brutto),
			doc.anzahl_zahlungen,
			_betrag(doc.zahlungen_brutto),
			doc.anzahl_sonstige,
		),
		_("Debitoren: {0} in den Stammdaten{1}").format(
			doc.anzahl_debitoren,
			_(" (neu vergeben: {0})").format(", ".join(neu_vergeben)) if neu_vergeben else "",
		),
		_("Belegbilder: {0} PDFs{1}").format(
			doc.anzahl_belegbilder, _(" (Druckformat {0})").format(druckformat) if druckformat else ""
		),
		_("Dateien: {0}").format(
			", ".join(
				d.rsplit("/", 1)[-1]
				for d in (doc.datei_buchungsstapel, doc.datei_debitoren, doc.datei_belegbilder)
				if d
			)
		),
		ergebnis.text(),
	]
	if wiederholt:
		zeilen.append(
			_("Wiederholung: {0} Belege waren schon exportiert: {1}").format(
				len(wiederholt), "; ".join(wiederholt)
			)
		)
	if hinweise:
		zeilen.append(_("Hinweise:"))
		zeilen += [f"  - {h}" for h in hinweise]
	return "\n".join(zeilen)
