"""DATEV-Monatsexport 7.4 (Handwerker#51): Formatprüfung ohne Site (``TestPruefung``); mit Site:
Einstellungen aus dem Template, Export eines Monats mit Abschlag, Schlussrechnung, Gutschrift und
Zahlung (Zeilen, Konten, Belegfelder, Summen, Dateien, Debitoren, Belegbilder), Markierung,
Wiederholung, nur neue Belege, eingefrorener Export, Rechte, fehlgeschlagene Formatprüfung.

Die CI-Bench hat ``corevatis`` nicht: der ``KernStellvertreter`` aus ``test_abrechnung`` ersetzt die
Engine; Rechnungen, Zahlungen und Hauptbuch sind echt (ERPNext). erpnext_datev ist in der CI installiert;
ohne die App werden die Site-Tests übersprungen.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, get_first_day, get_last_day, getdate, today

from corevatis_erpnext.datev import pruefung

# --- Formatprüfung ohne Site ---------------------------------------------------------------------


def _spalten() -> list[str]:
	try:
		from erpnext_datev.utils.datev_constants import TRANSACTION_COLUMNS

		return list(TRANSACTION_COLUMNS)
	except ImportError:
		namen = [f"Spalte {i}" for i in range(120)]
		for name, pos in pruefung.FESTE_POSITIONEN.items():
			namen[pos] = name
		for i in range(1, 9):
			namen[18 + 2 * i], namen[19 + 2 * i] = f"Beleginfo - Art {i}", f"Beleginfo - Inhalt {i}"
		namen[94], namen[113], namen[116] = "Auftragsnummer", "Festschreibung", "Fälligkeit"
		return namen


SPALTEN = _spalten()


def _kopf(**aenderungen) -> list[str]:
	felder = [
		'"EXTF"',
		"700",
		"21",
		'"Buchungsstapel"',
		"9",
		"20261101120000000",
		"",
		'"EN"',
		'"test"',
		"",
		"1234567",
		"12345",
		"20260101",
		"4",
		"20261001",
		"20261031",
		'"Buchungsstapel"',
		"",
		"1",
		"0",
		"0",
		'"EUR"',
		"",
		"",
		"",
		"",
		'"03"',
		"",
		"",
		"",
		"",
	]
	for idx, wert in aenderungen.items():
		felder[int(idx[1:])] = wert  # f10="…" → Feld 10
	return felder


def _zeile(**werte) -> str:
	basis = {
		"Umsatz (ohne Soll/Haben-Kz)": "119,00",
		"Soll/Haben-Kennzeichen": "S",
		"Konto": "10000",
		"Gegenkonto (ohne BU-Schlüssel)": "9090",
		"Belegdatum": "1510",
		"Belegfeld 1": "RE-2026-00003",
		"Belegfeld 2": "AB202600008",
		"Buchungstext": "Abschlag 1 Erika Musterfrau AB-2026-00008",
		"Beleginfo - Art 1": "Sales Invoice",
		"Beleginfo - Inhalt 1": "RE-2026-00003",
		"Auftragsnummer": "AB-2026-00008",
		"Fälligkeit": "29102026",
	}
	basis.update(werte)
	felder = []
	for s in SPALTEN:
		w = basis.get(s, "")
		felder.append(w if s == "Umsatz (ohne Soll/Haben-Kz)" and w else f'"{w}"')
	return ";".join(felder)


def _datei(zeilen: list[str] | None = None, kopf: list[str] | None = None, spalten=None) -> bytes:
	spalten = spalten if spalten is not None else SPALTEN
	zeilen = (
		zeilen
		if zeilen is not None
		else [
			_zeile(),
			_zeile(
				**{"Soll/Haben-Kennzeichen": "H", "Konto": "8400", "Umsatz (ohne Soll/Haben-Kz)": "100,00"}
			),
		]
	)
	text = "\r\n".join([";".join(kopf or _kopf()), ";".join(f'"{s}"' for s in spalten), *zeilen]) + "\r\n"
	return text.encode("cp1252")


class TestPruefung(unittest.TestCase):
	def test_gueltiger_stapel(self):
		e = pruefung.pruefe_buchungsstapel(_datei())
		self.assertTrue(e.ok, e.text())
		self.assertEqual((e.zeilen, e.spalten), (2, 120))
		self.assertEqual((str(e.summe_soll), str(e.summe_haben)), ("119.00", "100.00"))
		self.assertEqual(
			(e.kopf["beraternummer"], e.kopf["mandantennummer"], e.kopf["festschreibung"], e.kopf["skr"]),
			("1234567", "12345", "0", "03"),
		)
		self.assertEqual(e.hinweise, [])
		self.assertIn("Formatprüfung: OK", e.text())

	def test_kopfzeile(self):
		def fehler(**aenderungen):
			e = pruefung.pruefe_buchungsstapel(_datei(kopf=_kopf(**aenderungen)))
			return [f.feld for f in e.fehler], e

		self.assertEqual(fehler(f1="510")[0], ["Versionsnummer"])
		self.assertEqual(fehler(f2="16")[0], ["Datenkategorie"])
		self.assertEqual(fehler(f3='"Debitoren"')[0], ["Formatname"])
		self.assertEqual(fehler(f4="7")[0], ["Formatversion"])
		self.assertEqual(fehler(f10="123")[0], ["Beraternummer"])
		self.assertEqual(fehler(f11="123456")[0], ["Mandantennummer"])
		self.assertEqual(fehler(f13="3")[0], ["Sachkontenlänge"])
		self.assertEqual(fehler(f14="20261101")[0], ["Zeitraum"])
		self.assertEqual(fehler(f18="2")[0], ["Buchungstyp"])
		self.assertEqual(fehler(f20="2")[0], ["Festschreibung"])
		self.assertEqual(fehler(f21='"Euro"')[0], ["Währung"])
		# Platzhalter sind Hinweise, keine Fehler
		felder, e = fehler(f10="0000000", f11="00000")
		self.assertEqual(felder, [])
		self.assertEqual([h.feld for h in e.hinweise], ["Beraternummer", "Mandantennummer"])
		self.assertIn("Platzhalter", e.text())
		# zu wenige Kopffelder, falsche Spaltenzahl
		kopf = _kopf()[:-1]
		e = pruefung.pruefe_buchungsstapel(_datei(kopf=kopf))
		self.assertEqual([f.feld for f in e.fehler], ["Kopfzeile"])
		e = pruefung.pruefe_buchungsstapel(_datei(spalten=SPALTEN[:-1], zeilen=[]))
		self.assertTrue(any(f.feld == "Spalten" and "119 statt 120" in f.meldung for f in e.fehler), e.text())

	def test_buchungszeilen(self):
		def fehler(**werte):
			e = pruefung.pruefe_buchungsstapel(_datei(zeilen=[_zeile(**werte)]))
			return [(f.feld, f.zeile) for f in e.fehler]

		self.assertEqual(fehler(**{"Umsatz (ohne Soll/Haben-Kz)": "119.00"}), [("Umsatz", 3)])
		self.assertEqual(fehler(**{"Umsatz (ohne Soll/Haben-Kz)": "0,00"}), [("Umsatz", 3)])
		self.assertEqual(fehler(**{"Soll/Haben-Kennzeichen": "X"}), [("Soll/Haben-Kennzeichen", 3)])
		self.assertEqual(fehler(Konto="14AB"), [("Konto", 3)])
		self.assertEqual(fehler(Konto="140000"), [("Konto", 3)])
		self.assertEqual(
			fehler(**{"Gegenkonto (ohne BU-Schlüssel)": "10000"}), [("Gegenkonto (ohne BU-Schlüssel)", 3)]
		)
		self.assertEqual(fehler(**{"BU-Schlüssel": "4A"}), [("BU-Schlüssel", 3)])
		self.assertEqual(fehler(Belegdatum="15.10."), [("Belegdatum", 3)])
		self.assertEqual(fehler(Belegdatum="3109"), [("Belegdatum", 3)])  # kein gültiger Tag
		self.assertEqual(fehler(Belegdatum="0111"), [("Belegdatum", 3)])  # außerhalb 01.10.-31.10.
		self.assertEqual(fehler(**{"Belegfeld 1": ""}), [("Belegfeld 1", 3)])
		self.assertEqual(fehler(**{"Belegfeld 1": "RE 2026 00003"}), [("Belegfeld 1", 3)])
		self.assertEqual(fehler(**{"Belegfeld 1": "R" * 37}), [("Belegfeld 1", 3)])
		self.assertEqual(fehler(**{"Belegfeld 2": "AB-2026-00008"}), [("Belegfeld 2", 3)])
		self.assertEqual(fehler(Buchungstext="x" * 61), [("Buchungstext", 3)])
		self.assertEqual(fehler(Auftragsnummer="x" * 31), [("Auftragsnummer", 3)])
		self.assertEqual(
			fehler(**{"Beleginfo - Art 2": "", "Beleginfo - Inhalt 2": "AB-1"}), [("Beleginfo - Art 2", 3)]
		)
		self.assertEqual(fehler(**{"Fälligkeit": "2026-10-29"}), [("Fälligkeit", 3)])
		self.assertEqual(fehler(Festschreibung="2"), [("Festschreibung", 3)])
		self.assertEqual(fehler(), [])
		# Feldzahl, Zeichensatz, Zeilenende
		e = pruefung.pruefe_buchungsstapel(_datei(zeilen=[_zeile() + ";x"]))
		self.assertEqual([(f.feld, f.zeile) for f in e.fehler], [("Zeile", 3)])
		e = pruefung.pruefe_buchungsstapel(_datei() + b"\x81")
		self.assertEqual([f.feld for f in e.fehler], ["Datei"])
		e = pruefung.pruefe_buchungsstapel(_datei().replace(b"\r\n", b"\n"))
		self.assertEqual([f.feld for f in e.fehler], ["Datei"])

	def test_debitoren(self):
		try:
			from erpnext_datev.utils.datev_constants import DEBTOR_CREDITOR_COLUMNS as spalten
		except ImportError:
			self.skipTest("erpnext_datev nicht installiert")

		def datei(**werte) -> bytes:
			basis = {
				"Konto": "10000",
				"Adressatentyp": "1",
				"Name (Adressatentyp natürl. Person)": "Musterfrau",
				"Vorname (Adressatentyp natürl. Person)": "Erika",
				"Straße": "Kellerweg 7",
				"Postleitzahl": "48143",
				"Ort": "Münster",
				"Land": "DE",
			}
			basis.update(werte)
			kopf = _kopf(
				f2="16", f3='"Debitoren/Kreditoren"', f4="5", f14="", f15="", f16="", f18="", f19="", f21=""
			)
			zeile = ";".join(f'"{basis.get(s, "")}"' for s in spalten)
			return (
				"\r\n".join([";".join(kopf), ";".join(f'"{s}"' for s in spalten), zeile]) + "\r\n"
			).encode("cp1252")

		e = pruefung.pruefe_debitoren(datei())
		self.assertTrue(e.ok, e.text())
		self.assertEqual((e.zeilen, e.spalten), (1, 254))
		self.assertEqual([f.feld for f in pruefung.pruefe_debitoren(datei(Konto="1400")).fehler], ["Konto"])
		self.assertEqual(
			[
				f.feld
				for f in pruefung.pruefe_debitoren(
					datei(**{"Name (Adressatentyp natürl. Person)": ""})
				).fehler
			],
			["Name (Adressatentyp natürl. Person)"],
		)
		self.assertEqual(
			[f.feld for f in pruefung.pruefe_debitoren(datei(Land="Deutschland")).fehler], ["Land"]
		)
		self.assertEqual(
			[f.feld for f in pruefung.pruefe_debitoren(datei(Adressatentyp="3")).fehler], ["Adressatentyp"]
		)


# --- Export mit Site -------------------------------------------------------------------------------


def _wkhtmltopdf() -> bool:
	return bool(shutil.which("wkhtmltopdf"))


class TestDatevExport(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from corevatis_erpnext.abrechnung import kern
		from corevatis_erpnext.datev import ensure_datev_setup
		from corevatis_erpnext.datev.export import app_installiert
		from corevatis_erpnext.setup import apply_site_template
		from corevatis_erpnext.tests import _abrechnung as H
		from corevatis_erpnext.tests import _angebot as td
		from corevatis_erpnext.tests.test_abrechnung import NACHTRAG_ARTIKEL

		if kern.KERN_APP in frappe.get_installed_apps():
			raise unittest.SkipTest(
				"corevatis installiert - Stellvertreter der Engine würde die echte überdecken"
			)
		if not app_installiert():
			raise unittest.SkipTest("erpnext_datev nicht installiert")
		frappe.set_user("Administrator")
		# DATEV Settings (Platzhalter), Konten, Druckformate wie auf der Zielsite
		apply_site_template(template="huelshoff")
		H.einrichten()
		ensure_datev_setup()
		td.artikel(NACHTRAG_ARTIKEL, "Sonstiges")
		cls.firma = td.firma()
		cls.H, cls.td = H, td
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Singles", {"doctype": "Corevatis Abrechnung Einstellungen"})
		frappe.db.delete("Singles", {"doctype": "Corevatis Abnahme Einstellungen"})
		self.H.kern_patch(self)

	# --- Hilfen ------------------------------------------------------------------------------

	def export_anlegen(self, user=None, **werte):
		H = self.H
		heute = getdate(today())
		doc = frappe.get_doc(
			{
				"doctype": "DATEV Export",
				"firma": self.firma,
				"von": get_first_day(heute),
				"bis": get_last_day(heute),
				**werte,
			}
		)
		H._als(user or H.BUERO, doc.insert)
		return doc

	def export(self, user=None, **werte):
		from corevatis_erpnext.datev import export_erstellen

		H = self.H
		doc = self.export_anlegen(user, **werte)
		r = H._als(user or H.BUERO, export_erstellen, doc.name)
		doc.reload()
		return doc, r

	def datei(self, url: str) -> bytes:
		f = frappe.get_doc("File", {"file_url": url})
		return Path(f.get_full_path()).read_bytes()

	@staticmethod
	def zeilen(daten: bytes) -> tuple[list[str], list[str], list[dict]]:
		text = daten.decode("cp1252")
		roh = [z for z in text.split("\r\n") if z != ""]
		kopf = next(csv.reader(io.StringIO(roh[0]), delimiter=";"))
		spalten = next(csv.reader(io.StringIO(roh[1]), delimiter=";"))
		zeilen = [
			dict(zip(spalten, next(csv.reader(io.StringIO(z), delimiter=";")), strict=True)) for z in roh[2:]
		]
		return kopf, spalten, zeilen

	@staticmethod
	def saldo(zeilen: list[dict]) -> dict[str, float]:
		"""Soll minus Haben je Konto (ERPNext darf gleiche Konten je Position getrennt buchen)."""
		salden: dict[str, float] = {}
		for z in zeilen:
			betrag = flt(z["Umsatz (ohne Soll/Haben-Kz)"].replace(",", "."))
			salden[z["Konto"]] = flt(
				salden.get(z["Konto"], 0.0) + (betrag if z["Soll/Haben-Kennzeichen"] == "S" else -betrag), 2
			)
		return salden

	def abschlag(self, projekt: str) -> str:
		from corevatis_erpnext.abrechnung import abschlag

		r = self.H._als(self.H.BUERO, abschlag.abschlag_stellen, project=projekt)
		return self.H.freigeben(r["rechnung"]).name

	def zahlung(self, rechnung: str, betrag=None) -> str:
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		pe = get_payment_entry("Sales Invoice", rechnung, party_amount=betrag)
		pe.reference_no = f"Test {rechnung}"
		pe.reference_date = today()
		pe.insert()
		pe.submit()
		return pe.name

	def vorgang_komplett(self) -> dict:
		"""Auftrag 10 x 50 €: Abschlag 1 (30 %), Abschlag 2 (40 %), Abnahme, Schlussrechnung (Ist 12,
		Nachtrag 80 €), Zahlung zu Abschlag 1, Gutschrift zu Abschlag 2."""
		from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return

		from corevatis_erpnext.abrechnung import schluss
		from corevatis_erpnext.tests.test_abrechnung import NACHTRAG_ARTIKEL

		H = self.H
		auftrag, projekt = H.vorgang()
		re1 = self.abschlag(projekt)
		re2 = self.abschlag(projekt)
		H.abnehmen(projekt)
		v = H._als(H.BUERO, schluss.schlussrechnung_vorschlag, project=projekt)
		so_detail = v["positionen"][0]["so_detail"]
		r3 = H._als(
			H.BUERO,
			schluss.schlussrechnung_erstellen,
			project=projekt,
			positionen=json.dumps({so_detail: {"menge_ist": 12}}),
			nachtraege=json.dumps(
				[
					{
						"item_code": NACHTRAG_ARTIKEL,
						"bezeichnung": "Durchbruch",
						"menge": 1,
						"einzelpreis": 80,
						"lohnanteil": 60,
					}
				]
			),
		)
		re3 = H.freigeben(r3["rechnung"]).name
		pe = self.zahlung(re1)
		gs = make_sales_return(re2)
		gs.insert()
		gs.submit()
		kunde = frappe.db.get_value("Sales Order", auftrag, "customer")
		return {
			"auftrag": auftrag,
			"projekt": projekt,
			"re1": re1,
			"re2": re2,
			"re3": re3,
			"pe": pe,
			"gs": gs.name,
			"kunde": kunde,
			"kunde_name": frappe.db.get_value("Customer", kunde, "customer_name"),
		}

	# --- Tests ---------------------------------------------------------------------------------

	def test_einrichtung_und_rechte(self):
		from frappe.translate import get_translations_from_csv

		from corevatis_erpnext.datev import debitoren, export_erstellen
		from corevatis_erpnext.setup import verify_site_template

		H = self.H
		# DATEV Settings aus dem Template: Platzhalter-Nummern, SKR03, Kontenlänge 4, 9090, Debitoren ab 10000
		e = debitoren.einstellungen(self.firma)
		self.assertIsNotNone(e)
		self.assertEqual((e.client_number, e.consultant_number), ("00000", "0000000"))
		self.assertEqual(
			(e.account_number_length, e.temporary_against_account_number, e.opening_against_account_number),
			(4, "9090", "9000"),
		)
		self.assertEqual(e.cv_debitor_start, 10000)
		if frappe.db.exists("Print Format", "Rechnung Corevatis"):
			self.assertEqual(e.cv_druckformat, "Rechnung Corevatis")
		# Konten der Abrechnung im SKR03 der Firma
		for nummer in ("1400", "1200", "1000", "2650", "8400", "1776"):
			self.assertTrue(
				frappe.db.exists("Account", {"company": self.firma, "account_number": nummer, "is_group": 0}),
				nummer,
			)
		# Prüfpunkte des Templates (inkl. DATEV) grün
		result = verify_site_template(template="huelshoff")
		self.assertTrue(result["ok"], "\n".join(result["failed"]))
		self.assertTrue(any(c["check"].startswith("DATEV:") for c in result["checks"]))
		# Felder, Sidebar, Übersetzungen
		for doctype in ("Sales Invoice", "Payment Entry"):
			meta = frappe.get_meta(doctype)
			self.assertTrue(
				meta.has_field("cv_datev_export") and meta.has_field("cv_datev_exportiert_am"), doctype
			)
		sidebar = frappe.get_doc("Sidebar", "Betrieb")
		self.assertIn(
			("DocType", "DATEV Export"), {(i.link_type, i.link_to) for i in sidebar.items if i.type == "Link"}
		)
		de = get_translations_from_csv("de", "corevatis_erpnext")
		for df in frappe.get_meta("DATEV Export").fields:
			if df.label and df.fieldtype not in ("Section Break", "Column Break"):
				self.assertIn(df.label, de, df.label)
		for doctype in ("Sales Invoice", "Payment Entry", "DATEV Settings"):
			for df in frappe.get_meta(doctype).fields:
				if df.fieldname.startswith("cv_d") and df.label and df.fieldtype != "Column Break":
					self.assertIn(df.label, de, f"{doctype}: {df.label}")
		# Rechte: Büro legt an und erstellt, löscht nicht; Meister und Monteur nichts
		self.assertTrue(frappe.has_permission("DATEV Export", "create", user=H.BUERO))
		self.assertFalse(frappe.has_permission("DATEV Export", "delete", user=H.BUERO))
		for user in (H.MEISTER, H.MONTEUR):
			self.assertFalse(frappe.has_permission("DATEV Export", "read", user=user), user)
		doc = self.export_anlegen()
		for user in (H.MEISTER, H.MONTEUR):
			with self.assertRaises(frappe.PermissionError):
				H._als(user, export_erstellen, doc.name)
		self.assertEqual(frappe.db.get_value("DATEV Export", doc.name, "status"), "Entwurf")
		# Debitorennummer beim Anlegen eines Kunden (Hook)
		kunde = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"DATEV Test {frappe.generate_hash(length=6)}",
				"customer_type": "Individual",
				"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}),
				"territory": frappe.db.get_value("Territory", {"is_group": 0}),
			}
		).insert(ignore_permissions=True)
		zeile = next(r for r in kunde.accounts if r.company == self.firma)
		self.assertGreaterEqual(int(zeile.debtor_creditor_number), 10000)
		self.assertEqual(debitoren.nummer(kunde.name, self.firma), zeile.debtor_creditor_number)
		# zweiter Kunde bekommt die nächste Nummer
		zweiter = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"DATEV Test GmbH {frappe.generate_hash(length=6)}",
				"customer_type": "Company",
				"customer_group": kunde.customer_group,
				"territory": kunde.territory,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(
			int(debitoren.nummer(zweiter.name, self.firma)), int(zeile.debtor_creditor_number) + 1
		)
		self.assertEqual(debitoren.name_teilen("Erika Musterfrau"), ("Erika", "Musterfrau"))
		self.assertEqual(debitoren.text('=SUMME(A1) "x"  y', 60), "SUMME(A1) 'x' y")

	def test_monatsexport(self):
		from corevatis_erpnext.datev import debitoren
		from corevatis_erpnext.datev.export import belegfeld2

		v = self.vorgang_komplett()
		doc, r = self.export()
		self.assertTrue(r["ok"], r.get("zusammenfassung"))
		self.assertEqual((doc.status, doc.pruefung, doc.wiederholung), ("Erstellt", "OK", 0))
		self.assertEqual(
			(doc.anzahl_rechnungen, doc.anzahl_gutschriften, doc.anzahl_zahlungen, doc.anzahl_sonstige),
			(3, 1, 1, 0),
		)
		belege = [v["re1"], v["re2"], v["re3"], v["gs"], v["pe"]]
		erwartet = frappe.db.count("GL Entry", {"voucher_no": ["in", belege], "is_cancelled": 0})
		self.assertEqual(doc.anzahl_buchungen, erwartet)
		self.assertGreaterEqual(doc.anzahl_buchungen, 12)
		self.assertEqual(flt(doc.summe_soll, 2), flt(doc.summe_haben, 2))
		# Abschlag 1 178,50 + Abschlag 2 238,00 + Schlussrechnung 392,70 - Gutschrift 238,00
		self.assertEqual((flt(doc.rechnungen_brutto, 2), flt(doc.zahlungen_brutto, 2)), (571.2, 178.5))
		self.assertEqual(doc.anzahl_debitoren, 1)
		self.assertEqual(doc.erstellt_von, self.H.BUERO)
		self.assertEqual(doc.titel, f"DATEV HST {getdate(today()):%m/%Y}")

		# Buchungsstapel: Kopf, Debitorenkonto, Belegfelder, Buchungstext, Zahlung, Gutschrift
		kopf, spalten, zeilen = self.zeilen(self.datei(doc.datei_buchungsstapel))
		self.assertEqual(
			(kopf[0], kopf[1], kopf[2], kopf[3], kopf[20], kopf[26]),
			("EXTF", "700", "21", "Buchungsstapel", "0", "03"),
		)
		self.assertEqual((kopf[10], kopf[11], kopf[13]), ("0000000", "00000", "4"))
		self.assertEqual((kopf[14], kopf[15]), (f"{getdate(doc.von):%Y%m%d}", f"{getdate(doc.bis):%Y%m%d}"))
		self.assertEqual(len(spalten), 120)
		self.assertEqual(len(zeilen), doc.anzahl_buchungen)
		pruef = pruefung.pruefe_buchungsstapel(self.datei(doc.datei_buchungsstapel))
		self.assertTrue(pruef.ok, pruef.text())
		debitor = debitoren.nummer(v["kunde"], self.firma)
		self.assertTrue(debitor and debitor.isdigit() and len(debitor) == 5, debitor)
		ddmm = f"{getdate(today()):%d%m}"
		z1 = [z for z in zeilen if z["Belegfeld 1"] == v["re1"]]
		self.assertEqual({z["Konto"] for z in z1}, {debitor, "8400", "1776"})
		d1 = next(z for z in z1 if z["Konto"] == debitor)
		self.assertEqual(
			(
				d1["Umsatz (ohne Soll/Haben-Kz)"],
				d1["Soll/Haben-Kennzeichen"],
				d1["Gegenkonto (ohne BU-Schlüssel)"],
				d1["Belegdatum"],
			),
			("178,5", "S", "9090", ddmm),
		)
		self.assertEqual(d1["Belegfeld 2"], belegfeld2(v["auftrag"]))
		self.assertEqual(len(d1["Belegfeld 2"]), 11)
		self.assertEqual(d1["Auftragsnummer"], v["auftrag"])
		self.assertTrue(d1["Buchungstext"].startswith("Abschlag 1 "), d1["Buchungstext"])
		self.assertIn(v["auftrag"], d1["Buchungstext"])
		self.assertLessEqual(len(d1["Buchungstext"]), 60)
		self.assertEqual((d1["Beleginfo - Art 3"], d1["Beleginfo - Inhalt 3"]), ("Customer", v["kunde"]))
		self.assertEqual((d1["Beleginfo - Art 4"], d1["Beleginfo - Inhalt 4"]), ("Debitorennummer", debitor))
		self.assertEqual((d1["Beleginfo - Art 7"], d1["Beleginfo - Inhalt 7"]), ("Auftrag", v["auftrag"]))
		self.assertEqual((d1["Beleginfo - Art 8"], d1["Beleginfo - Inhalt 8"]), ("Vorgang", v["projekt"]))
		self.assertEqual(
			d1["Fälligkeit"], f"{getdate(frappe.db.get_value('Sales Invoice', v['re1'], 'due_date')):%d%m%Y}"
		)
		e1 = next(z for z in z1 if z["Konto"] == "8400")
		self.assertEqual((e1["Umsatz (ohne Soll/Haben-Kz)"], e1["Soll/Haben-Kennzeichen"]), ("150,0", "H"))
		s1 = next(z for z in z1 if z["Konto"] == "1776")
		self.assertEqual((s1["Umsatz (ohne Soll/Haben-Kz)"], s1["Soll/Haben-Kennzeichen"]), ("28,5", "H"))
		# Schlussrechnung: Rest 330 netto, Abzugszeilen netto gegen 8400
		z3 = [z for z in zeilen if z["Belegfeld 1"] == v["re3"]]
		self.assertEqual(self.saldo(z3), {debitor: 392.7, "8400": -330.0, "1776": -62.7})
		self.assertEqual({z["Gegenkonto (ohne BU-Schlüssel)"] for z in z3}, {"9090"})
		self.assertTrue(
			next(z for z in z3 if z["Konto"] == debitor)["Buchungstext"].startswith("Schlussrechnung ")
		)
		# Zahlung: beide Zeilen tragen die bezahlte Rechnung in Belegfeld 1, Bank 1200 gegen Debitor
		zp = [z for z in zeilen if z["Beleginfo - Inhalt 1"] == v["pe"]]
		self.assertEqual(len(zp), 2)
		self.assertEqual({z["Belegfeld 1"] for z in zp}, {v["re1"]})
		self.assertEqual(
			{(z["Konto"], z["Soll/Haben-Kennzeichen"], z["Umsatz (ohne Soll/Haben-Kz)"]) for z in zp},
			{("1200", "S", "178,5"), (debitor, "H", "178,5")},
		)
		self.assertTrue(all(z["Buchungstext"].startswith(f"Zahlung {v['re1']} ") for z in zp))
		self.assertEqual({z["Beleginfo - Art 1"] for z in zp}, {"Payment Entry"})
		self.assertEqual({z["Beleginfo - Inhalt 6"] for z in zp}, {f"{getdate(today()):%d%m%Y}"})
		# Gutschrift: Erlös und Steuer im Soll, Debitor im Haben
		zg = [z for z in zeilen if z["Belegfeld 1"] == v["gs"]]
		self.assertEqual(self.saldo(zg), {"8400": 200.0, "1776": 38.0, debitor: -238.0})
		self.assertTrue(zg[0]["Buchungstext"].startswith(f"Gutschrift zu {v['re2']} "))
		self.assertTrue(v["gs"].startswith("GS-"), v["gs"])
		# keine Zeile ohne Debitor auf dem Sammelkonto 1400
		self.assertEqual([z for z in zeilen if z["Konto"] == "1400"], [])

		# Debitoren-Stammdaten des Kunden
		dkopf, dspalten, dzeilen = self.zeilen(self.datei(doc.datei_debitoren))
		self.assertEqual(
			(dkopf[0], dkopf[2], dkopf[3], dkopf[4]), ("EXTF", "16", "Debitoren/Kreditoren", "5")
		)
		self.assertEqual(len(dspalten), 254)
		self.assertEqual(len(dzeilen), 1)
		self.assertEqual((dzeilen[0]["Konto"], dzeilen[0]["Adressatentyp"]), (debitor, "1"))
		vorname, name = debitoren.name_teilen(v["kunde_name"])
		self.assertEqual(
			(
				dzeilen[0]["Vorname (Adressatentyp natürl. Person)"],
				dzeilen[0]["Name (Adressatentyp natürl. Person)"],
			),
			(vorname[:30], name[:30]),
		)
		self.assertTrue(pruefung.pruefe_debitoren(self.datei(doc.datei_debitoren)).ok)

		# Belegbilder: ein PDF je Rechnung und Gutschrift, Dateiname = Belegnummer
		if _wkhtmltopdf():
			self.assertEqual(doc.anzahl_belegbilder, 4)
			with zipfile.ZipFile(io.BytesIO(self.datei(doc.datei_belegbilder))) as zf:
				namen = set(zf.namelist())
				self.assertEqual(
					namen, {f"{n}.pdf" for n in (v["re1"], v["re2"], v["re3"], v["gs"])} | {"Belegliste.csv"}
				)
				self.assertTrue(zf.read(f"{v['re1']}.pdf").startswith(b"%PDF"))
				liste = zf.read("Belegliste.csv").decode("cp1252")
				self.assertIn(f"{v['re1']};", liste)
		# Dateien privat am Export
		for url in (doc.datei_buchungsstapel, doc.datei_debitoren):
			f = frappe.get_doc("File", {"file_url": url})
			self.assertEqual(
				(f.is_private, f.attached_to_doctype, f.attached_to_name), (1, "DATEV Export", doc.name)
			)
		self.assertTrue(
			doc.datei_buchungsstapel.endswith(
				f"EXTF_Buchungsstapel_HST_{getdate(today()):%Y-%m}_{doc.name}.csv"
			),
			doc.datei_buchungsstapel,
		)
		self.assertTrue(
			doc.datei_debitoren.endswith(f"EXTF_Debitoren_HST_{getdate(today()):%Y-%m}_{doc.name}.csv")
		)

		# Markierung an Rechnungen, Gutschrift und Zahlung; Belegliste am Export
		for name in (v["re1"], v["re2"], v["re3"], v["gs"]):
			self.assertEqual(frappe.db.get_value("Sales Invoice", name, "cv_datev_export"), doc.name, name)
			self.assertTrue(frappe.db.get_value("Sales Invoice", name, "cv_datev_exportiert_am"))
		self.assertEqual(frappe.db.get_value("Payment Entry", v["pe"], "cv_datev_export"), doc.name)
		self.assertEqual(
			{(b.belegart, b.beleg) for b in doc.belege}
			>= {("Sales Invoice", v["re1"]), ("Payment Entry", v["pe"])},
			True,
		)
		b_gs = next(b for b in doc.belege if b.beleg == v["gs"])
		self.assertEqual((flt(b_gs.brutto, 2), b_gs.zeilen, b_gs.zuvor_exportiert_in), (-238.0, 3, None))
		# Protokoll
		for text in (
			"Beraternummer 0000000",
			"Platzhalter",
			"Formatprüfung: OK",
			"Gegenkonto 9090",
			"EXTF_Buchungsstapel_HST",
		):
			self.assertIn(text, doc.protokoll)

	def test_wiederholung_nur_neue_festschreibung_eingefroren(self):
		from corevatis_erpnext.datev import export_erstellen
		from corevatis_erpnext.datev.export import DatevFehler

		H = self.H
		_auftrag, projekt = H.vorgang()
		re1 = self.abschlag(projekt)
		a, _ = self.export()
		self.assertEqual(frappe.db.get_value("Sales Invoice", re1, "cv_datev_export"), a.name)
		# Wiederholung: derselbe Monat noch einmal, mit Festschreibung
		b, rb = self.export(festschreibung=1)
		self.assertTrue(rb["ok"])
		self.assertGreaterEqual(b.wiederholung, 1)
		zeile = next(z for z in b.belege if z.beleg == re1)
		self.assertEqual(zeile.zuvor_exportiert_in, a.name)
		self.assertIn(f"{re1} (zuvor in {a.name}", b.protokoll)
		self.assertEqual(frappe.db.get_value("Sales Invoice", re1, "cv_datev_export"), b.name)
		kopf, _spalten, _zeilen = self.zeilen(self.datei(b.datei_buchungsstapel))
		self.assertEqual(kopf[20], "1")
		self.assertIn("Festschreibung: ja", b.protokoll)
		# nur neue Belege: alles schon exportiert → nichts zu tun
		c = self.export_anlegen(nur_neue=1)
		with self.assertRaises(DatevFehler):
			H._als(H.BUERO, export_erstellen, c.name)
		# neuer Abschlag → nur er landet im Export „nur neue“
		re2 = self.abschlag(projekt)
		d, rd = self.export(nur_neue=1)
		self.assertEqual((rd["ok"], d.anzahl_rechnungen, d.wiederholung), (True, 1, 0))
		self.assertEqual([z.beleg for z in d.belege], [re2])
		self.assertIn("schon exportierte Belege ausgelassen", d.protokoll)
		# erstellter Export: Zeitraum eingefroren, kein zweites Erstellen, Büro löscht nicht
		a.reload()
		a.von = add_days(a.von, 1)
		with self.assertRaises(frappe.ValidationError):
			H._als(H.BUERO, a.save)
		a.reload()
		with self.assertRaises(DatevFehler):
			H._als(H.BUERO, export_erstellen, a.name)
		with self.assertRaises(frappe.PermissionError):
			H._als(H.BUERO, frappe.delete_doc, "DATEV Export", a.name)
		# ohne Buchungen im Zeitraum (zwei Tage in der Zukunft, gleiches Geschäftsjahr)
		heute = getdate(today())
		if (heute.month, heute.day) < (12, 29):
			leer = self.export_anlegen(von=f"{heute.year}-12-30", bis=f"{heute.year}-12-31")
			with self.assertRaises(DatevFehler):
				H._als(H.BUERO, export_erstellen, leer.name)

	def test_formatfehler_speichert_nichts(self):
		from corevatis_erpnext.datev import export_erstellen

		H = self.H
		_auftrag, projekt = H.vorgang()
		re1 = self.abschlag(projekt)
		doc = self.export_anlegen()

		def kaputt(daten):
			e = pruefung.Pruefergebnis()
			e.fehler_(3, "Umsatz", "Test")
			return e

		with patch.object(pruefung, "pruefe_buchungsstapel", kaputt):
			r = H._als(H.BUERO, export_erstellen, doc.name)
		doc.reload()
		self.assertFalse(r["ok"])
		self.assertEqual((doc.status, doc.pruefung), ("Fehlgeschlagen", "Fehler"))
		self.assertFalse(doc.datei_buchungsstapel or doc.datei_debitoren or doc.datei_belegbilder)
		self.assertIsNone(frappe.db.get_value("Sales Invoice", re1, "cv_datev_export"))
		self.assertIn("Zeile 3, Umsatz: Test", doc.protokoll)
		# danach erstellbar
		r2 = H._als(H.BUERO, export_erstellen, doc.name)
		self.assertTrue(r2["ok"])
		self.assertEqual(frappe.db.get_value("DATEV Export", doc.name, "status"), "Erstellt")
