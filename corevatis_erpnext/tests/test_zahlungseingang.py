"""Zahlungseingang 7.5 (Handwerker#77): Kontoauszug lesen (Sparkasse-/VR-Bank-CSV, CAMT.053), Import
als Bank Transaction mit Dublettenerkennung, Zuordnungsvorschlag (Rechnungsnummer, Betrag, Name),
Bestätigung als Payment Entry (Teilzahlung), Abschlagsplan „bezahlt“, Prozessleiste, Ignorieren, Rechte,
Dateiprüfung."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, today

from corevatis_erpnext.abrechnung import abschlag, kern, plan
from corevatis_erpnext.abrechnung import kontoauszug as K
from corevatis_erpnext.abrechnung import zahlungseingang as Z
from corevatis_erpnext.abrechnung.einstellungen import EINSTELLUNGEN
from corevatis_erpnext.abrechnung.zahlungseingang import ZahlungFehler
from corevatis_erpnext.prozess.kette import status
from corevatis_erpnext.tests import _abrechnung as H
from corevatis_erpnext.tests import _angebot as td

ORDNER = Path(__file__).resolve().parent / "kontoauszug"


def _schritt(stand: dict, key: str) -> dict:
	return next(s for s in stand["schritte"] if s["key"] == key)


class TestKontoauszugParser(unittest.TestCase):
	"""Lesen ohne Site (reine Funktionen)."""

	def test_sparkasse_csv_cp1252(self):
		buchungen, hinweise = K.lese_csv((ORDNER / "sparkasse.csv").read_bytes())
		self.assertEqual(hinweise, [])
		self.assertEqual(len(buchungen), 3)
		b = buchungen[0]
		self.assertEqual(
			(b.datum, b.betrag, b.waehrung, b.name), (date(2026, 11, 3), 1431.26, "EUR", "Erika Müller")
		)
		self.assertEqual((b.iban, b.konto_iban), ("DE02120300000000202051", "DE02100100100006820101"))
		self.assertEqual(b.verwendungszweck, "Rechnung RE-2026-00042 Kellerabdichtung")
		self.assertEqual(b.buchungstext, "GUTSCHR. UEBERWEISUNG")
		self.assertEqual(b.referenz, "")  # NOTPROVIDED zählt nicht
		self.assertTrue(b.gutschrift)
		self.assertEqual((buchungen[1].betrag, buchungen[1].gutschrift), (-89.9, False))
		self.assertEqual(buchungen[2].betrag, 1908.34)
		self.assertEqual(buchungen[0].zeile, 2)

	def test_vr_bank_csv(self):
		buchungen, _hinweise = K.lese_csv((ORDNER / "vr_bank.csv").read_bytes())
		self.assertEqual(len(buchungen), 2)
		self.assertEqual(
			(buchungen[0].datum, buchungen[0].betrag, buchungen[0].name),
			(date(2026, 11, 3), 1431.26, "Erika Müller"),
		)
		self.assertEqual(buchungen[0].verwendungszweck, "Rechnung RE 2026 00042 Keller")
		self.assertEqual(buchungen[0].buchungstext, "Überweisungsgutschrift")
		self.assertEqual(buchungen[1].betrag, -120.0)
		self.assertEqual(
			K.rechnungsnummern_im_text(buchungen[0].verwendungszweck, ["RE-2026-00042", "RE-2026-00043"]),
			["RE-2026-00042"],
		)

	def test_komma_csv_mit_mapping_und_vorspann(self):
		text = 'Kontoauszug vom 07.11.2026\n\n"Date","Amount","Text","Payer"\n"2026-11-03","1,431.26","RE-2026-00042","Erika Mueller"\n"2026-11-04","-12.30","Gebühr",""\n'
		buchungen, _h = K.lese_csv(
			text, mapping={"datum": "Date", "betrag": "Amount", "verwendungszweck": "Text", "name": "Payer"}
		)
		self.assertEqual(
			[(b.datum, b.betrag) for b in buchungen],
			[(date(2026, 11, 3), 1431.26), (date(2026, 11, 4), -12.3)],
		)
		self.assertEqual(buchungen[0].name, "Erika Mueller")
		self.assertEqual(K.erkenne_trennzeichen(text), ",")
		with self.assertRaises(K.KontoauszugFehler):
			K.lese_csv("a;b;c\n1;2;3\n")
		with self.assertRaises(K.KontoauszugFehler):
			K.lese_csv("")

	def test_betrag_datum_normalisierung(self):
		self.assertEqual(K.betrag_parsen("1.234,56"), 1234.56)
		self.assertEqual(K.betrag_parsen("-12,30"), -12.3)
		self.assertEqual(K.betrag_parsen("1,234.56"), 1234.56)
		self.assertEqual(K.betrag_parsen("1234.56"), 1234.56)
		self.assertEqual(K.betrag_parsen("12,5 EUR"), 12.5)
		self.assertEqual(K.betrag_parsen(""), 0.0)
		with self.assertRaises(K.KontoauszugFehler):
			K.betrag_parsen("abc")
		self.assertEqual(K.datum_parsen("03.11.26"), date(2026, 11, 3))
		self.assertEqual(K.datum_parsen("03.11.2026"), date(2026, 11, 3))
		self.assertEqual(K.datum_parsen("2026-11-03"), date(2026, 11, 3))
		self.assertIsNone(K.datum_parsen("kein Datum"))
		self.assertEqual(K.normalisiere(" re-2026 00042 Müller"), "RE202600042MUELLER")
		self.assertEqual(K.rechnungsnummern_im_text("RE 2026 00042", ["RE-2026-00042"]), ["RE-2026-00042"])
		self.assertEqual(K.rechnungsnummern_im_text("Kellerabdichtung", ["RE-2026-00042"]), [])
		self.assertEqual(
			K.rechnungsnummern_im_text("RE-1", ["RE-1"]), []
		)  # zu kurz für einen sicheren Treffer
		self.assertEqual(K.namensaehnlichkeit("Familie Feucht", "Feucht, Erika"), 1.0)
		self.assertEqual(K.namensaehnlichkeit("Erika Müller", "Mueller Erika"), 1.0)
		self.assertEqual(K.namensaehnlichkeit("Aral Willich", "Erika Müller"), 0.0)
		self.assertEqual(K.namensaehnlichkeit("", "x"), 0.0)
		self.assertEqual(K.export_sicher("=SUMME(A1)"), "'=SUMME(A1)")
		self.assertEqual(K.export_sicher("Text"), "Text")

	def test_dublettenschluessel(self):
		a = K.Buchung(
			date(2026, 11, 3),
			1431.26,
			name="Erika Müller",
			iban="DE02120300000000202051",
			verwendungszweck="Rechnung RE-2026-00042",
		)
		b = K.Buchung(
			date(2026, 11, 3),
			1431.26,
			name="Erika Müller",
			iban="DE02120300000000202051",
			verwendungszweck="Rechnung  RE-2026-00042 ",
		)
		c = K.Buchung(
			date(2026, 11, 4),
			1431.26,
			name="Erika Müller",
			iban="DE02120300000000202051",
			verwendungszweck="Rechnung RE-2026-00042",
		)
		self.assertEqual(a.schluessel(), b.schluessel())
		self.assertNotEqual(a.schluessel(), c.schluessel())
		mit_ref = K.Buchung(date(2026, 11, 3), 1.0, referenz="2026110312345678")
		self.assertEqual(
			mit_ref.schluessel(), K.Buchung(date(2026, 11, 9), 2.0, referenz="2026110312345678").schluessel()
		)
		self.assertEqual(len(a.schluessel()), 32)

	def test_camt053(self):
		roh = (ORDNER / "camt053.xml").read_bytes()
		self.assertTrue(K.ist_camt(roh))
		buchungen, hinweise = K.lese_camt(roh)
		self.assertEqual(hinweise, [])
		self.assertEqual(len(buchungen), 3)
		b = buchungen[0]
		self.assertEqual(
			(b.datum, b.betrag, b.waehrung, b.name, b.iban),
			(date(2026, 11, 3), 1431.26, "EUR", "Erika Müller", "DE02120300000000202051"),
		)
		self.assertEqual(b.verwendungszweck, "Rechnung RE-2026-00042 Kellerabdichtung")
		self.assertEqual(
			(b.referenz, b.buchungstext, b.konto_iban),
			("2026110312345678", "GUTSCHR. UEBERWEISUNG", "DE02100100100006820101"),
		)
		self.assertEqual((buchungen[1].betrag, buchungen[1].name), (-89.9, "Aral Willich"))
		self.assertEqual(buchungen[2].verwendungszweck, "Abschlag Keller E2E-77")
		buchungen2, _h, fmt = K.lese(roh, "auszug.xml")
		self.assertEqual((fmt, len(buchungen2)), ("camt", 3))
		# XML-Bomben und externe Entitäten werden nicht gelesen - auch hinter einem langen Kommentar
		# (jenseits der Schnellprüfung), in UTF-16 und als externe DTD (Security-Review)
		boese = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><Document><BkToCstmrStmt/></Document>'
		with self.assertRaises(K.KontoauszugFehler):
			K.lese_camt(boese)
		spaet = (
			b'<?xml version="1.0"?><!-- '
			+ b"x" * 5000
			+ b' --><!DOCTYPE Document [<!ENTITY a "aaaa">]><Document>&a;</Document>'
		)
		with self.assertRaises(K.KontoauszugFehler):
			K.lese_camt(spaet)
		utf16 = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE Document [<!ENTITY a "aaaa">]><Document>&a;</Document>'.encode(
			"utf-16"
		)
		with self.assertRaises(K.KontoauszugFehler):
			K.lese_camt(utf16)
		extern = (
			b'<?xml version="1.0"?><!-- '
			+ b"y" * 5000
			+ b' --><!DOCTYPE Document SYSTEM "http://example.invalid/x.dtd"><Document><BkToCstmrStmt/></Document>'
		)
		with self.assertRaises(K.KontoauszugFehler):
			K.lese_camt(extern)
		with self.assertRaises(K.KontoauszugFehler):
			K.lese_camt(b"<Document><kaputt>")
		# ohne DOCTYPE, aber mit Kommentar vor der Wurzel lesbar
		praefix = (
			(ORDNER / "camt053.xml")
			.read_text(encoding="utf-8")
			.replace("<Document xmlns=", "<!-- Kommentar --><Document xmlns=")
		)
		self.assertEqual(len(K.lese_camt(praefix.encode("utf-8"))[0]), 3)


class TestZahlungseingang(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if kern.KERN_APP in frappe.get_installed_apps():
			raise unittest.SkipTest(
				"corevatis installiert - Stellvertreter der Engine würde die echte überdecken"
			)
		H.einrichten()
		cls.firma = td.firma()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Singles", {"doctype": EINSTELLUNGEN})
		frappe.db.delete("Singles", {"doctype": "Corevatis Abnahme Einstellungen"})
		H.kern_patch(self)

	# --- Hilfen ------------------------------------------------------------------------------

	def datei(
		self, inhalt: bytes | str, name: str = "kontoauszug.csv", user: str = H.BUERO, privat: int = 1
	) -> str:
		"""Hochgeladene Datei (wie der Dialog): privat, Eigentümer = Benutzer. Rückgabe ``file_url``."""
		frappe.set_user(user)
		try:
			f = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": f"{frappe.generate_hash(length=6)}-{name}",
					"is_private": privat,
					"content": inhalt,
				}
			)
			f.insert()
			return f.file_url
		finally:
			frappe.set_user("Administrator")

	def abschlaege(self):
		"""Auftrag mit zwei freigegebenen Abschlägen: 30 % (150 netto / 178,50) und 40 % (200 / 238)."""
		auftrag, projekt = H.vorgang()
		r1 = H._als(H.BUERO, abschlag.abschlag_stellen, project=projekt)
		H.freigeben(r1["rechnung"])
		r2 = H._als(H.BUERO, abschlag.abschlag_stellen, project=projekt)
		H.freigeben(r2["rechnung"])
		return auftrag, projekt, r1["rechnung"], r2["rechnung"]

	@staticmethod
	def sparkasse_zeile(
		datum: str, text: str, name: str, betrag: str, iban: str = "DE02120300000000202051"
	) -> str:
		return f'"DE02100100100006820101";"{datum}";"{datum}";"GUTSCHR. UEBERWEISUNG";"{text}";"";"";"NOTPROVIDED";"";"";"";"{name}";"{iban}";"BYLADEM1001";"{betrag}";"EUR";"Umsatz gebucht"\n'

	KOPF = (
		"Auftragskonto;Buchungstag;Valutadatum;Buchungstext;Verwendungszweck;Glaeubiger ID;Mandatsreferenz;"
		"Kundenreferenz (End-to-End);Sammlerreferenz;Lastschrift Ursprungsbetrag;Auslagenersatz Ruecklastschrift;"
		"Beguenstigter/Zahlungspflichtiger;Kontonummer/IBAN;BIC (SWIFT-Code);Betrag;Waehrung;Info\n"
	)

	# --- Tests -------------------------------------------------------------------------------

	def test_import_vorschlag_zuordnung_teilzahlung_dublette(self):
		auftrag, projekt, r1, r2 = self.abschlaege()
		kunde = frappe.db.get_value("Sales Order", auftrag, "customer_name")
		heute = frappe.utils.formatdate(today(), "dd.MM.yy")
		csv = self.KOPF
		csv += self.sparkasse_zeile(heute, f"Rechnung {r1} Abschlag Keller", kunde, "178,50")
		csv += self.sparkasse_zeile(
			heute, "Anzahlung Kellerabdichtung", kunde, "100,00", "DE02500105170137075030"
		)
		csv += self.sparkasse_zeile(
			heute, "Tankstelle Willich", "Aral Willich", "-89,90", "DE02300209000106531065"
		)
		csv += self.sparkasse_zeile(
			heute, "Erstattung Schaden 4711", "Allianz Versicherung", "55,00", "DE02200505501015871393"
		)
		inhalt = csv.encode("cp1252")
		# Rechte: nur Büro
		url = self.datei(inhalt, user=H.MEISTER)
		with self.assertRaises(frappe.PermissionError):
			H._als(H.MEISTER, Z.kontoauszug_importieren, url)
		# Import
		url = self.datei(inhalt)
		file_name = frappe.db.get_value("File", {"file_url": url})
		res = H._als(H.BUERO, Z.kontoauszug_importieren, url)
		self.assertEqual(
			(
				res["format"],
				res["gelesen"],
				len(res["angelegt"]),
				res["dubletten"],
				res["abgaenge"],
				res["fehler"],
			),
			("csv", 4, 3, [], 1, []),
		)
		self.assertFalse(frappe.db.exists("File", file_name), "Datei nach dem Einlesen entfernt")
		# Bankkonto der Firma wurde angelegt (Sachkonto Bank)
		konto = frappe.get_doc("Bank Account", res["bankkonto"])
		self.assertEqual((konto.company, konto.is_company_account, konto.bank), (self.firma, 1, Z.BANK_NAME))
		self.assertEqual(frappe.db.get_value("Account", konto.account, "account_type"), "Bank")
		bts = {frappe.db.get_value("Bank Transaction", n, "description"): n for n in res["angelegt"]}
		bt_a = next(n for d, n in bts.items() if r1 in d)
		bt_b = next(n for d, n in bts.items() if "Anzahlung" in d)
		bt_d = next(n for d, n in bts.items() if "Erstattung" in d)
		a = frappe.get_doc("Bank Transaction", bt_a)
		self.assertEqual(
			(a.docstatus, a.status, flt(a.deposit), flt(a.unallocated_amount), a.bank_party_name),
			(1, "Unreconciled", 178.5, 178.5, kunde),
		)
		self.assertEqual(a.bank_party_iban, "DE02120300000000202051")
		self.assertEqual(str(a.date), today())
		self.assertTrue(a.transaction_id)
		self.assertIn("Zeile 2", a.cv_import)
		# Vorschläge: A sicher über die Rechnungsnummer (+ Betrag + Name), B über den Namen, D nichts
		vs = {v["name"]: v for v in res["vorschlaege"]}
		self.assertEqual((vs[bt_a]["bester"]["rechnung"], vs[bt_a]["bester"]["sicherheit"]), (r1, "sicher"))
		self.assertTrue(any("Rechnungsnummer" in g for g in vs[bt_a]["bester"]["gruende"]))
		self.assertTrue(any("offenen Betrag" in g for g in vs[bt_a]["bester"]["gruende"]))
		self.assertGreaterEqual(vs[bt_a]["bester"]["punkte"], 170)
		self.assertEqual(vs[bt_a]["bester"]["vorschlag_betrag"], 178.5)
		self.assertIn(vs[bt_b]["bester"]["rechnung"], (r1, r2))
		self.assertEqual(vs[bt_b]["bester"]["sicherheit"], "möglich")
		self.assertTrue(vs[bt_b]["bester"]["teilzahlung"])
		self.assertIsNone(vs[bt_d]["bester"])
		# Dublette: dieselbe Datei noch einmal
		res2 = H._als(H.BUERO, Z.kontoauszug_importieren, self.datei(inhalt))
		self.assertEqual((len(res2["angelegt"]), len(res2["dubletten"]), res2["abgaenge"]), (0, 3, 1))
		self.assertEqual(sorted(res2["dubletten"]), sorted(res["angelegt"]))
		# Zuordnung A → Rechnung r1 bezahlt, Abschlagszeile „bezahlt“, Buchung abgeglichen, Kommentar, Prozessleiste
		with self.assertRaises(frappe.PermissionError):
			H._als(H.MEISTER, Z.zuordnen, bt_a, r1)
		z = H._als(H.BUERO, Z.zuordnen, bt_a, r1)
		self.assertEqual((z["betrag"], z["offen_danach"], z["status"]), (178.5, 0.0, "bezahlt"))
		pe = frappe.get_doc("Payment Entry", z["payment_entry"])
		self.assertEqual(
			(pe.docstatus, flt(pe.paid_amount), pe.party, str(pe.posting_date), pe.paid_to),
			(1, 178.5, frappe.db.get_value("Sales Invoice", r1, "customer"), today(), konto.account),
		)
		self.assertEqual(
			[(ref.reference_name, flt(ref.allocated_amount)) for ref in pe.references], [(r1, 178.5)]
		)
		self.assertEqual(
			frappe.db.get_value("Sales Invoice", r1, ["outstanding_amount", "status"]), (0.0, "Paid")
		)
		a.reload()
		self.assertEqual(
			(a.status, flt(a.unallocated_amount), a.payment_entries[0].payment_entry),
			("Reconciled", 0.0, pe.name),
		)
		self.assertEqual(str(pe.clearance_date), today())
		doc = frappe.get_doc("Abschlagsplan", plan.plan_zum_auftrag(auftrag))
		self.assertEqual([x.status for x in doc.zeilen], ["bezahlt", "gestellt", "offen"])
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "Project",
					"reference_name": projekt,
					"comment_type": "Comment",
					"content": ["like", f"%{r1}%zugeordnet%"],
				},
			)
		)
		schritt = _schritt(H._als(H.BUERO, status, "Project", projekt), "rechnung")
		self.assertIn("Abschlag 2 von 3 gestellt", schritt["text"])
		self.assertTrue(any(b["name"] == r1 and "bezahlt" in b["info"] for b in schritt["belege"]))
		# noch einmal dieselbe Buchung → vollständig zugeordnet
		with self.assertRaises(ZahlungFehler):
			H._als(H.BUERO, Z.zuordnen, bt_a, r2)
		# Teilzahlung B (100 von 238) → teilbezahlt
		with self.assertRaises(ZahlungFehler):
			H._als(H.BUERO, Z.zuordnen, bt_b, r2, betrag=999)
		z2 = H._als(H.BUERO, Z.zuordnen, bt_b, r2, betrag=100)
		self.assertEqual((z2["betrag"], z2["offen_danach"], z2["status"]), (100.0, 138.0, "teilbezahlt"))
		self.assertEqual(frappe.db.get_value("Sales Invoice", r2, "status"), "Partly Paid")
		self.assertEqual(
			frappe.db.get_value("Abschlagsplan Zeile", {"parent": doc.name, "nr": 2}, "status"), "gestellt"
		)
		schritt = _schritt(H._als(H.BUERO, status, "Project", projekt), "rechnung")
		self.assertIn("teilbezahlt, offen", schritt["text"])
		# Rechnung r1 ist bezahlt → kein Vorschlag mehr dafür; D ignorieren
		offen = {v["name"] for v in H._als(H.BUERO, Z.vorschlaege)}
		self.assertIn(bt_d, offen)
		self.assertNotIn(bt_a, offen)
		H._als(H.BUERO, Z.ignorieren, bt_d, grund="Versicherung, keine Rechnung")
		self.assertEqual(
			frappe.db.get_value("Bank Transaction", bt_d, ["cv_ignoriert", "cv_notiz"]),
			(1, "Versicherung, keine Rechnung"),
		)
		self.assertNotIn(bt_d, {v["name"] for v in H._als(H.BUERO, Z.vorschlaege)})
		self.assertIn(bt_d, {v["name"] for v in H._als(H.BUERO, Z.vorschlaege, mit_ignorierten=1)})
		H._als(H.BUERO, Z.ignorieren, bt_d, aufheben=1)
		self.assertEqual(frappe.db.get_value("Bank Transaction", bt_d, "cv_ignoriert"), 0)
		# Zustandstext des Rechnungsformulars
		from corevatis_erpnext.abrechnung.rechnung import zustand

		self.assertIn("bezahlt am", H._als(H.BUERO, zustand, r1)["text"])
		self.assertIn("teilbezahlt, offen", H._als(H.BUERO, zustand, r2)["text"])

	def test_camt_import_und_dateipruefung(self):
		auftrag, projekt, r1, _r2 = self.abschlaege()
		kunde = frappe.db.get_value("Sales Order", auftrag, "customer_name")
		xml = (ORDNER / "camt053.xml").read_text(encoding="utf-8")
		xml = xml.replace("RE-2026-00042", r1).replace("Erika Müller", kunde).replace("1431.26", "178.50")
		for alt in ("2026-11-03", "2026-11-04", "2026-11-05"):
			xml = xml.replace(alt, today())
		res = H._als(H.BUERO, Z.kontoauszug_importieren, self.datei(xml.encode("utf-8"), name="camt053.xml"))
		self.assertEqual(
			(res["format"], res["gelesen"], len(res["angelegt"]), res["abgaenge"]), ("camt", 3, 2, 1)
		)
		bts = {frappe.db.get_value("Bank Transaction", n, "description"): n for n in res["angelegt"]}
		bt = next(n for d, n in bts.items() if r1 in d)
		self.assertEqual(
			frappe.db.get_value("Bank Transaction", bt, ["reference_number", "transaction_id"]),
			("2026110312345678", K.Buchung(None, 0, referenz="2026110312345678").schluessel()),
		)
		v = next(v for v in res["vorschlaege"] if v["name"] == bt)
		self.assertEqual((v["bester"]["rechnung"], v["bester"]["sicherheit"]), (r1, "sicher"))
		# Bankreferenz als Dublettenschlüssel: zweiter Import legt nichts an
		res2 = H._als(H.BUERO, Z.kontoauszug_importieren, self.datei(xml.encode("utf-8"), name="camt053.xml"))
		self.assertEqual((len(res2["angelegt"]), len(res2["dubletten"])), (0, 2))
		# Dateiprüfung: öffentlich, fremd, falscher Typ, zu groß, fremdes Konto
		with self.assertRaises(ZahlungFehler):
			H._als(
				H.BUERO,
				Z.kontoauszug_importieren,
				self.datei(xml.encode("utf-8"), name="camt053.xml", privat=0),
			)
		fremd = self.datei(xml.encode("utf-8"), name="camt053.xml", user="Administrator")
		with self.assertRaises(frappe.PermissionError):
			H._als(H.BUERO, Z.kontoauszug_importieren, fremd)
		with self.assertRaises(ZahlungFehler):
			H._als(H.BUERO, Z.kontoauszug_importieren, self.datei(b"%PDF-1.4", name="auszug.pdf"))
		with patch.object(Z, "MAX_BYTES", 100):
			with self.assertRaises(ZahlungFehler):
				H._als(
					H.BUERO, Z.kontoauszug_importieren, self.datei(xml.encode("utf-8"), name="camt053.xml")
				)
		with self.assertRaises(frappe.DoesNotExistError):
			H._als(H.BUERO, Z.kontoauszug_importieren, "/private/files/gibt-es-nicht.csv")
		# Kontoauszug eines anderen Kontos, wenn das Bankkonto eine IBAN hat
		konto = frappe.get_doc("Bank Account", res["bankkonto"])
		alte_iban = konto.iban
		frappe.db.set_value(
			"Bank Account", konto.name, "iban", "DE02100500000054540402", update_modified=False
		)
		try:
			with self.assertRaises(ZahlungFehler):
				H._als(
					H.BUERO, Z.kontoauszug_importieren, self.datei(xml.encode("utf-8"), name="camt053.xml")
				)
		finally:
			frappe.db.set_value("Bank Account", konto.name, "iban", alte_iban, update_modified=False)
		# Zuordnung an eine Gutschrift/fremde Rechnung
		with self.assertRaises(ZahlungFehler):
			H._als(H.BUERO, Z.zuordnen, bt, r1, betrag=0)
		z = H._als(H.BUERO, Z.zuordnen, bt, r1)
		self.assertEqual(
			(z["status"], flt(frappe.db.get_value("Sales Invoice", r1, "outstanding_amount"))),
			("bezahlt", 0.0),
		)
		schritt = _schritt(H._als(H.BUERO, status, "Project", projekt), "rechnung")
		self.assertTrue(any(b["name"] == r1 and "bezahlt" in b["info"] for b in schritt["belege"]))
