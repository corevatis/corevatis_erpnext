"""Mahnwesen 7.5 (Handwerker#77): Einrichtung, Mahnvorschlag nach Fristen, Mahnung mit Gebühr auf
ERPNext Dunning, Offene Posten, Prozessleiste („fällig seit“, Mahnstufe, „bezahlt am“), Rechte."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, getdate, today

from corevatis_erpnext.abrechnung import kern, mahnung, offene_posten
from corevatis_erpnext.abrechnung.einstellungen import EINSTELLUNGEN, einstellungen
from corevatis_erpnext.abrechnung.mahnung import MahnungFehler
from corevatis_erpnext.prozess.kette import status
from corevatis_erpnext.tests import _abrechnung as H
from corevatis_erpnext.tests import _angebot as td


def _schritt(stand: dict, key: str) -> dict:
	return next(s for s in stand["schritte"] if s["key"] == key)


class TestMahnwesen(IntegrationTestCase):
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

	def rechnung(self, tage_alt: int = 40):
		"""Auftrag + abgenommenes Projekt, gewöhnliche Rechnung (500 netto / 595 brutto), zurückdatiert und gebucht."""
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

		auftrag, projekt = H.vorgang(kalkulation=False)
		H.abnehmen(projekt)
		si = make_sales_invoice(auftrag)
		H._als(H.BUERO, si.insert)
		H.datieren(si.name, tage_alt)
		H.freigeben(si.name)
		return si.name, auftrag, projekt

	# --- Einrichtung ------------------------------------------------------------------------

	def test_einrichtung(self):
		e = einstellungen()
		self.assertEqual(
			(e.mahnfrist_erinnerung, e.mahnfrist_mahnung1, e.mahnfrist_mahnung2, e.mahnung_zahlungsfrist),
			(7, 14, 14, 7),
		)
		self.assertEqual((e.mahngebuehr_mahnung1, e.mahngebuehr_mahnung2, e.nur_gutschriften), (0.0, 5.0, 1))
		self.assertEqual(e.csv_trennzeichen, "Automatisch")
		# Mahntypen je Firma mit Stufe, Gebühr und deutschem Brieftext
		for stufe, label in mahnung.STUFEN:
			name = mahnung.typ_name(stufe, self.firma)
			self.assertTrue(frappe.db.exists("Dunning Type", name), name)
			typ = frappe.get_doc("Dunning Type", name)
			# Bezeichnung mit Firmenkürzel (ERPNext: dunning_type firmenübergreifend eindeutig)
			self.assertTrue(typ.dunning_type.startswith(label + " ("), typ.dunning_type)
			self.assertEqual(
				(typ.dunning_type, typ.cv_mahnstufe, flt(typ.dunning_fee)),
				(mahnung.typ_label(stufe, self.firma), stufe, mahnung.gebuehr(stufe, e)),
			)
			self.assertEqual(len(typ.dunning_letter_text), 1)
			self.assertIn("<p>", typ.dunning_letter_text[0].body_text)
			self.assertTrue(typ.dunning_letter_text[0].is_default_language)
		self.assertEqual(
			frappe.db.get_value("Dunning Type", mahnung.typ_name(0, self.firma), "is_default"), 1
		)
		# zweiter Lauf legt nichts an, ändert nichts
		self.assertEqual(mahnung.ensure_mahntypen(self.firma), [])
		# Felder
		for doctype, feld in (
			("Dunning", "cv_mahnstufe"),
			("Dunning", "cv_zahlungsfrist"),
			("Dunning", "cv_projekt"),
			("Dunning Type", "cv_mahnstufe"),
			("Bank Transaction", "cv_ignoriert"),
			("Bank Transaction", "cv_notiz"),
			("Bank Transaction", "cv_import"),
		):
			self.assertTrue(frappe.get_meta(doctype).has_field(feld), f"{doctype}.{feld}")
		# Rechte: Büro bucht Mahnungen, liest Mahntypen; Meister nicht
		self.assertTrue(frappe.has_permission("Dunning", "submit", user=H.BUERO))
		self.assertTrue(frappe.has_permission("Dunning Type", "read", user=H.BUERO))
		self.assertFalse(frappe.has_permission("Dunning", "create", user=H.MEISTER))
		# Listen, Sidebar, Skripte
		for report in ("Offene Posten", "Zahlungseingang"):
			self.assertTrue(frappe.db.exists("Report", report), report)
		sidebar = frappe.get_doc("Sidebar", "Betrieb")
		links = {(i.link_type, i.link_to) for i in sidebar.items if i.type == "Link"}
		for link in (("Report", "Offene Posten"), ("Report", "Zahlungseingang"), ("DocType", "Dunning")):
			self.assertIn(link, links)
		js = frappe.get_hooks("doctype_js", app_name="corevatis_erpnext")
		for doctype in ("Sales Invoice", "Bank Transaction", "Dunning"):
			self.assertIn("public/js/mahnung_zahlung.js", js[doctype], doctype)
		# Übersetzungen der neuen Einstellungen und Felder
		from frappe.translate import get_translations_from_csv

		de = get_translations_from_csv("de", "corevatis_erpnext")
		for df in frappe.get_meta(EINSTELLUNGEN).fields:
			if df.label:
				self.assertIn(df.label, de, df.label)
		for doctype in ("Dunning", "Dunning Type", "Bank Transaction"):
			for df in frappe.get_meta(doctype).fields:
				if df.fieldname.startswith("cv_") and df.label:
					self.assertIn(df.label, de, f"{doctype}: {df.label}")
		# Site-Template: Druckformate der Rechnung und Mahnung
		ordner = Path(frappe.get_app_path("corevatis_erpnext", "setup", "templates"))
		huelshoff = json.loads((ordner / "huelshoff.json").read_text())
		self.assertEqual(huelshoff["print_formats"]["Sales Invoice"], "Rechnung Corevatis")
		self.assertEqual(huelshoff["print_formats"]["Dunning"], "Mahnung Corevatis")
		standard = json.loads((ordner / "_standard.json").read_text())
		self.assertIn("Ausgangsrechnung", standard["print_formats_replace"]["Sales Invoice"])
		self.assertIn("Mahnung", standard["print_formats_replace"]["Dunning"])

	def test_mahntypen_zwei_firmen(self):
		"""ERPNext verlangt ``dunning_type`` über alle Firmen eindeutig: mit zweiter Firma scheiterte die
		Migration auf mvp-test (Duplicate entry 'Zahlungserinnerung'). Bezeichnung trägt darum das Kürzel."""
		firma2 = "Zweite Firma Mahntest"
		if not frappe.db.exists("Company", firma2):
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": firma2,
					"abbr": "ZFM",
					"default_currency": "EUR",
					"country": "Germany",
					"create_chart_of_accounts_based_on": "Standard Template",
					"chart_of_accounts": "Standard",
				}
			).insert(ignore_permissions=True)
		mahnung.ensure_mahntypen()
		for firma in (self.firma, firma2):
			for stufe in mahnung.LABELS:
				name = mahnung.typ_name(stufe, firma)
				self.assertTrue(frappe.db.exists("Dunning Type", name), name)
				self.assertEqual(mahnung.mahntyp(stufe, firma), name)
				self.assertEqual(frappe.db.get_value("Dunning Type", name, "cv_mahnstufe"), stufe)
		self.assertEqual(
			frappe.db.get_value("Dunning Type", mahnung.typ_name(1, firma2), "dunning_type"),
			"1. Mahnung (ZFM)",
		)
		self.assertEqual(mahnung.ensure_mahntypen(), [])

	# --- Vorschlag ------------------------------------------------------------------------------

	def test_vorschlag_nach_fristen(self):
		e = einstellungen()
		heute = getdate("2026-11-30")
		r = frappe._dict(outstanding_amount=100, docstatus=1, due_date="2026-11-25")
		v = mahnung.vorschlag(r, None, heute, e)
		self.assertEqual((v["stufe"], v["moeglich"], v["tage_ueberfaellig"]), (0, False, 5))
		self.assertIn("ab 02.12.2026", v["grund"])
		r.due_date = "2026-11-20"
		v = mahnung.vorschlag(r, None, heute, e)
		self.assertEqual(
			(v["stufe"], v["label"], v["moeglich"], v["gebuehr"]), (0, "Zahlungserinnerung", True, 0.0)
		)
		self.assertIn("10 Tage überfällig", v["grund"])
		# nach der Erinnerung: 1. Mahnung erst 14 Tage später
		stand = frappe._dict(stufe=0, datum=getdate("2026-11-20"), mahnung="DUNN-1")
		v = mahnung.vorschlag(r, stand, heute, e)
		self.assertEqual((v["stufe"], v["moeglich"]), (1, False))
		self.assertEqual(str(v["ab"]), "2026-12-04")
		stand.datum = getdate("2026-11-10")
		v = mahnung.vorschlag(r, stand, heute, e)
		self.assertEqual((v["stufe"], v["label"], v["moeglich"], v["gebuehr"]), (1, "1. Mahnung", True, 0.0))
		self.assertIn("Zahlungserinnerung vom 10.11.2026", v["grund"])
		# nach der 1. Mahnung: 2. Mahnung mit Gebühr
		v = mahnung.vorschlag(r, frappe._dict(stufe=1, datum=getdate("2026-11-10")), heute, e)
		self.assertEqual((v["stufe"], v["label"], v["moeglich"], v["gebuehr"]), (2, "2. Mahnung", True, 5.0))
		# nach der 2. Mahnung: nichts mehr
		v = mahnung.vorschlag(r, frappe._dict(stufe=2, datum=getdate("2026-11-10")), heute, e)
		self.assertIsNone(v["stufe"])
		self.assertIn("Inkasso", v["grund"])
		# bezahlt / nicht fällig
		self.assertEqual(
			mahnung.vorschlag(
				frappe._dict(outstanding_amount=0, docstatus=1, due_date="2026-11-01"), None, heute, e
			)["grund"],
			"bezahlt",
		)
		v = mahnung.vorschlag(
			frappe._dict(outstanding_amount=10, docstatus=1, due_date="2026-12-10"), None, heute, e
		)
		self.assertEqual((v["stufe"], v["grund"]), (None, "fällig am 10.12.2026"))
		# Fristen aus den Einstellungen
		einst = frappe.get_single(EINSTELLUNGEN)
		einst.mahnfrist_erinnerung = 3
		einst.mahngebuehr_mahnung1 = 2.5
		einst.save()
		e = einstellungen()
		self.assertTrue(
			mahnung.vorschlag(
				frappe._dict(outstanding_amount=100, docstatus=1, due_date="2026-11-25"), None, heute, e
			)["moeglich"]
		)
		self.assertEqual(mahnung.gebuehr(1, e), 2.5)

	# --- Mahnen -----------------------------------------------------------------------------------

	def test_mahnen_mit_stufen_gebuehr_und_zahlung(self):
		si, _auftrag, projekt = self.rechnung(tage_alt=40)  # fällig vor 26 Tagen
		self.assertEqual(flt(frappe.db.get_value("Sales Invoice", si, "outstanding_amount")), 595.0)
		v = H._als(H.BUERO, mahnung.mahnvorschlag, si)
		self.assertEqual((v["vorschlag"]["stufe"], v["vorschlag"]["moeglich"], v["stand"]), (0, True, None))
		self.assertEqual([s["stufe"] for s in v["stufen"]], [0, 1, 2])
		self.assertEqual(str(v["zahlungsfrist"]), add_days(today(), 7))
		# Rechte
		for user in (H.MEISTER, H.MONTEUR):
			with self.assertRaises(frappe.PermissionError):
				H._als(user, mahnung.mahnvorschlag, si)
			with self.assertRaises(frappe.PermissionError):
				H._als(user, mahnung.mahnung_erstellen, si)
		# andere Stufe als vorgeschlagen nur mit Bestätigung
		with self.assertRaises(MahnungFehler):
			H._als(H.BUERO, mahnung.mahnung_erstellen, si, stufe=1)
		self.assertEqual(
			frappe.db.count(
				"Dunning", {"docstatus": 1, "customer": frappe.db.get_value("Sales Invoice", si, "customer")}
			),
			0,
		)
		# Zahlungserinnerung
		r0 = H._als(H.BUERO, mahnung.mahnung_erstellen, si)
		d0 = frappe.get_doc("Dunning", r0["mahnung"])
		self.assertEqual((r0["stufe"], r0["label"], r0["gebuehr"]), (0, "Zahlungserinnerung", 0.0))
		self.assertEqual(
			(d0.docstatus, d0.status, d0.dunning_type), (1, "Unresolved", mahnung.typ_name(0, self.firma))
		)
		self.assertEqual(
			(flt(d0.dunning_fee), flt(d0.total_outstanding), flt(d0.grand_total)), (0.0, 595.0, 595.0)
		)
		self.assertEqual(
			(d0.cv_mahnstufe, str(d0.cv_zahlungsfrist), d0.cv_projekt), (0, add_days(today(), 7), projekt)
		)
		self.assertEqual([z.sales_invoice for z in d0.overdue_payments], [si])
		self.assertIn("Aufmerksamkeit entgangen", d0.body_text)
		self.assertEqual(d0.language, "de")
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "Project",
					"reference_name": projekt,
					"comment_type": "Comment",
					"content": ["like", "%Zahlungserinnerung%"],
				},
			)
		)
		stand = offene_posten.mahnstand([si])[si]
		self.assertEqual((stand.stufe, stand.mahnung, stand.anzahl), (0, d0.name, 1))
		# gleich noch einmal: Frist der 1. Mahnung läuft noch - nur mit Bestätigung
		with self.assertRaises(MahnungFehler):
			H._als(H.BUERO, mahnung.mahnung_erstellen, si)
		v = H._als(H.BUERO, mahnung.mahnvorschlag, si)
		self.assertEqual(
			(v["vorschlag"]["stufe"], v["vorschlag"]["moeglich"], v["stand"]["stufe"]), (1, False, 0)
		)
		# Zeit vergeht: Erinnerung vor 15 Tagen → 1. Mahnung (ohne Gebühr)
		frappe.db.set_value("Dunning", d0.name, "posting_date", add_days(today(), -15), update_modified=False)
		r1 = H._als(H.BUERO, mahnung.mahnung_erstellen, si)
		d1 = frappe.get_doc("Dunning", r1["mahnung"])
		self.assertEqual(
			(r1["stufe"], flt(d1.dunning_fee), flt(d1.grand_total), d1.cv_mahnstufe), (1, 0.0, 595.0, 1)
		)
		self.assertEqual(d1.overdue_payments[0].dunning_level, 2)
		# 1. Mahnung vor 15 Tagen → 2. Mahnung mit 5 € Gebühr auf dem Ertragskonto
		frappe.db.set_value("Dunning", d1.name, "posting_date", add_days(today(), -15), update_modified=False)
		r2 = H._als(H.BUERO, mahnung.mahnung_erstellen, si)
		d2 = frappe.get_doc("Dunning", r2["mahnung"])
		self.assertEqual((r2["stufe"], r2["gebuehr"], r2["gesamt"]), (2, 5.0, 600.0))
		self.assertEqual(
			(flt(d2.dunning_fee), flt(d2.dunning_amount), flt(d2.grand_total), d2.cv_mahnstufe),
			(5.0, 5.0, 600.0, 2),
		)
		self.assertTrue(d2.income_account)
		self.assertEqual(frappe.db.get_value("Account", d2.income_account, "root_type"), "Income")
		self.assertIn("Mahngebühr", d2.body_text)
		# danach kein Vorschlag mehr
		v = H._als(H.BUERO, mahnung.mahnvorschlag, si)
		self.assertIsNone(v["vorschlag"]["stufe"])
		self.assertIn("Inkasso", v["vorschlag"]["grund"])
		with self.assertRaises(MahnungFehler):
			H._als(H.BUERO, mahnung.mahnung_erstellen, si)
		# Offene Posten und Prozessleiste
		zeilen = [z for z in H._als(H.BUERO, offene_posten.offene_posten) if z["rechnung"] == si]
		self.assertEqual(len(zeilen), 1)
		z = zeilen[0]
		self.assertEqual(
			(
				z["art"],
				z["offen"],
				z["tage_ueberfaellig"],
				z["mahnstufe"],
				z["mahnung"],
				z["vorschlag"],
				z["vorgang"],
			),
			("Rechnung", 595.0, 26, "2. Mahnung", d2.name, "", projekt),
		)
		self.assertIn("Inkasso", z["hinweis"])
		schritt = _schritt(H._als(H.BUERO, status, "Project", projekt), "rechnung")
		self.assertIn("fällig seit 26 Tagen", schritt["text"])
		self.assertIn("2. Mahnung vom", schritt["text"])
		# nur überfällige / nur mahnbare
		self.assertEqual(
			[
				x["rechnung"]
				for x in H._als(H.BUERO, offene_posten.offene_posten, {"nur_mahnbar": 1})
				if x["rechnung"] == si
			],
			[],
		)
		# Zahlung → bezahlt am heute, nicht mehr in den offenen Posten, Erinnerung erledigt
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		pe = get_payment_entry("Sales Invoice", si)
		pe.reference_no = "Test"
		pe.reference_date = today()
		pe.insert()
		pe.submit()
		self.assertEqual(flt(frappe.db.get_value("Sales Invoice", si, "outstanding_amount")), 0.0)
		self.assertEqual(frappe.db.get_value("Dunning", d0.name, "status"), "Resolved")
		self.assertEqual(
			[x["rechnung"] for x in H._als(H.BUERO, offene_posten.offene_posten) if x["rechnung"] == si], []
		)
		schritt = _schritt(H._als(H.BUERO, status, "Project", projekt), "rechnung")
		self.assertIn(f"bezahlt am {frappe.utils.format_date(today())}", schritt["text"])
		self.assertEqual(offene_posten.bezahlt_am([si])[si], getdate(today()))

	def test_offene_posten_rechte_und_nicht_faellig(self):
		si, _auftrag, _projekt = self.rechnung(tage_alt=3)  # fällig in 11 Tagen
		with self.assertRaises(frappe.PermissionError):
			H._als(H.MONTEUR, offene_posten.offene_posten)
		# Meister darf die Liste öffnen (Zeilen nur mit Leserecht an Rechnungen)
		self.assertIsInstance(H._als(H.MEISTER, offene_posten.offene_posten), list)
		zeilen = [z for z in H._als(H.BUERO, offene_posten.offene_posten) if z["rechnung"] == si]
		self.assertEqual(len(zeilen), 1)
		self.assertEqual(
			(zeilen[0]["tage_ueberfaellig"], zeilen[0]["mahnstufe"], zeilen[0]["vorschlag"]), (0, "", "")
		)
		self.assertIn("fällig am", zeilen[0]["hinweis"])
		self.assertEqual(
			[
				z["rechnung"]
				for z in H._als(H.BUERO, offene_posten.offene_posten, {"nur_ueberfaellig": 1})
				if z["rechnung"] == si
			],
			[],
		)
		# nicht überfällig: Mahnung nicht möglich, auch nicht mit Bestätigung
		with self.assertRaises(MahnungFehler):
			H._als(H.BUERO, mahnung.mahnung_erstellen, si, stufe=0, bestaetigen=1)
