"""Dokumentenmappe (Epic D) im Adapter: Felder, Hooks, Leiste „Betrieb“, Verhalten ohne Kern und
mit nachgestelltem Kern (``frappe.get_attr`` ersetzt).

Die CI-Bench dieses Repos hat ``corevatis`` nicht. Ohne Kern müssen alle Hooks wirkungslos sein -
und eine gesetzte Übersteuerung der Mappenprüfung darf die Abnahmesperre 6.2 **nicht** umgehen.
Mit nachgestelltem Kern wird geprüft, dass der Adapter die richtigen Funktionen mit den richtigen
Argumenten ruft (Pflichtprüfung an Angebot und Rechnung, PDF bei Freigabe, Protokoll nur beim
Buchen oder bei neuer Übersteuerung) und dass ein Fehler beim PDF die Freigabe nicht aufhält.
"""

from __future__ import annotations

import json
import pathlib
import unittest
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from corevatis_erpnext import hooks, mappe
from corevatis_erpnext.abnahme.rechnung import RechnungOhneAbnahme
from corevatis_erpnext.angebot import annahme
from corevatis_erpnext.mappe import felder
from corevatis_erpnext.prozess.kette import status
from corevatis_erpnext.tests import _angebot as td
from corevatis_erpnext.tests._helpers import make_user

BUERO = "cv-mappe-buero@example.com"
ANKER = "ANF-TEST-MAPPE"


def _als(user: str, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


class KernAttrappe:
	"""Nachgestellter Kern: merkt sich Aufrufe, liefert feste Antworten."""

	def __init__(self, pdf_fehler: Exception | None = None, uebersteuert: bool = False):
		self.aufrufe: list[tuple[str, tuple]] = []
		self.pdf_fehler = pdf_fehler
		self.uebersteuert = uebersteuert

	def get_attr(self, pfad: str):
		def fn(*args):
			self.aufrufe.append((pfad, args))
			if pfad == mappe.KERN_ANKER:
				return ANKER
			if pfad == mappe.KERN_UEBERSTEUERUNG:
				doc = args[0]
				if self.uebersteuert:
					doc.flags.cv_mappe_uebersteuert_neu = True
				return self.uebersteuert
			if pfad == mappe.KERN_PRUEFEN:
				return {
					"ergebnis": "Unvollständig",
					"hinweis": "Mappe: Befundfoto fehlt noch (Hinweis).",
					"fehlend": [],
				}
			if pfad == mappe.KERN_BELEG_PDF:
				if self.pdf_fehler:
					raise self.pdf_fehler
				return {"name": "FILE-1", "ordner": "03 Angebot", "dokumentart": "Angebot", "version": 1}
			if pfad == mappe.KERN_STAND:
				return {
					"ampel": "gelb",
					"text": "Mappe: 1 Hinweis (Befundfoto) vor Schritt Angebot",
					"schritt": "Angebot",
				}
			raise AssertionError(pfad)

		return fn

	def mit(self, pfad: str) -> list[tuple]:
		return [a for p, a in self.aufrufe if p == pfad]


class TestMappe(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if mappe.KERN_APP in frappe.get_installed_apps():
			raise unittest.SkipTest("corevatis installiert - die Tests stellen den Kern nach")
		make_user(
			BUERO,
			"Berta",
			["Büro", "Sales User", "Accounts User", "Projects User", "Projects Manager"],
			"Büro",
		)
		td.ensure_felder_41()
		td.ensure_artikel()
		felder.ensure_mappe_felder()
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")

	def auftrag_mit_projekt(self) -> tuple[str, str]:
		lead = td.lead_mit_angebot_status()
		angebot = td.angebot(lead.name, zusatz=False)
		r = _als(BUERO, annahme.angebot_annehmen, angebot.name, einreichen=1)
		return r["auftrag"], r["projekt"]

	def rechnung(self, auftrag: str, **werte):
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

		si = make_sales_invoice(auftrag)
		si.update(werte)
		return si

	# --- Struktur -------------------------------------------------------------------------------

	def test_felder_hooks_sidebar(self):
		for doctype, feldnamen in (
			("Project", (felder.PROJEKT_AMPEL, felder.PROJEKT_HINWEIS)),
			("Quotation", (felder.UEBERSTEUERT, felder.UEBERSTEUERT_GRUND, felder.UEBERSTEUERT_VON)),
			("Sales Invoice", (felder.UEBERSTEUERT, felder.UEBERSTEUERT_GRUND, felder.UEBERSTEUERT_VON)),
		):
			meta = frappe.get_meta(doctype)
			for feld in feldnamen:
				self.assertIsNotNone(meta.get_field(feld), f"{doctype}.{feld}")
		ampel = frappe.get_meta("Project").get_field(felder.PROJEKT_AMPEL)
		self.assertEqual(ampel.read_only, 1)
		self.assertEqual([o for o in ampel.options.split("\n") if o], list(felder.AMPEL_WERTE))
		self.assertEqual(frappe.get_meta("Sales Invoice").get_field(felder.UEBERSTEUERT_VON).read_only, 1)
		# Hooks: Mappenprüfung vor der Abnahmesperre, PDF bei Freigabe
		si = hooks.doc_events["Sales Invoice"]
		self.assertEqual(
			si["validate"],
			["corevatis_erpnext.mappe.rechnung_pruefen", "corevatis_erpnext.abnahme.rechnung_pruefen"],
		)
		self.assertIn("corevatis_erpnext.mappe.rechnung_freigegeben", si["on_submit"])
		q = hooks.doc_events["Quotation"]
		self.assertIn("corevatis_erpnext.mappe.angebot_pruefen", q["before_submit"])
		self.assertEqual(q["on_submit"], "corevatis_erpnext.mappe.angebot_freigegeben")
		self.assertIn(
			"corevatis_erpnext.mappe.auftrag_freigegeben", hooks.doc_events["Sales Order"]["on_submit"]
		)
		self.assertIn("corevatis_erpnext.mappe.angebot_validieren", q["validate"])
		for doctype in ("Quotation", "Sales Invoice"):
			self.assertIn("public/js/mappe_uebersteuern.js", hooks.doctype_js[doctype])
		# Leiste „Betrieb“: Eintrag „Mappen“ auf die Kern-Page
		sidebar = json.loads(
			(
				pathlib.Path(hooks.__file__).parent
				/ "corevatis_erpnext"
				/ "sidebar"
				/ "betrieb"
				/ "betrieb.json"
			).read_text(encoding="utf-8")
		)
		eintraege = {
			(i.get("link_to"), i.get("link_type")) for i in sidebar["items"] if i.get("type") == "Link"
		}
		self.assertIn((mappe.PAGE, "Page"), eintraege)
		self.assertTrue(frappe.db.exists("Sidebar", "Betrieb"))
		self.assertIn(
			mappe.PAGE,
			[i.link_to for i in frappe.get_doc("Sidebar", "Betrieb").items if i.link_type == "Page"],
		)

	# --- ohne Kern ------------------------------------------------------------------------------

	def test_ohne_kern_wirkungslos_und_sperre_bleibt(self):
		self.assertFalse(mappe.kern_da())
		self.assertIsNone(mappe.stand("Lead", "x"))
		auftrag, projekt = self.auftrag_mit_projekt()
		lead = frappe.db.get_value("Project", projekt, "cv_lead")
		stand = _als(BUERO, status, "Lead", lead)
		self.assertIsNone(stand["mappe"])
		# Übersteuerung der Mappenprüfung ohne Kern: verworfen - die Abnahmesperre 6.2 greift weiter
		with self.assertRaises(RechnungOhneAbnahme):
			_als(
				BUERO,
				self.rechnung(auftrag, cv_mappe_uebersteuert=1, cv_mappe_grund="Kunde zahlt vorab").insert,
			)
		abschlag = _als(
			BUERO, self.rechnung(auftrag, cv_abschlag=1, cv_mappe_uebersteuert=1, cv_mappe_grund="x").insert
		)
		self.assertEqual((abschlag.cv_mappe_uebersteuert, abschlag.cv_mappe_grund), (0, None))
		# Freigaben laufen ohne Kern ohne Mappen-Dateien durch
		self.assertFalse(
			frappe.get_all(
				"File", filters={"attached_to_doctype": "Sales Order", "attached_to_name": auftrag}
			)
		)

	# --- mit nachgestelltem Kern ----------------------------------------------------------------

	def test_angebot_pruefung_und_pdf(self):
		kern = KernAttrappe()
		lead = td.lead_mit_angebot_status()
		with patch.object(mappe, "kern_da", return_value=True), patch.object(mappe, "_kern", kern.get_attr):
			angebot = td.angebot(lead.name, zusatz=False)
		pruefungen = kern.mit(mappe.KERN_PRUEFEN)
		self.assertEqual(len(pruefungen), 1)
		self.assertEqual(
			pruefungen[0], (mappe.SCHRITT_ANGEBOT, ANKER, "Quotation", angebot.name, False, None, True)
		)
		pdfs = kern.mit(mappe.KERN_BELEG_PDF)
		self.assertEqual(len(pdfs), 1)
		doctype, name, druckformat, ereignis = pdfs[0]
		self.assertEqual((doctype, name, ereignis), ("Quotation", angebot.name, mappe.EREIGNIS_FREIGABE))
		self.assertEqual(
			druckformat,
			mappe.DRUCKFORMAT_ANGEBOT
			if frappe.db.exists("Print Format", mappe.DRUCKFORMAT_ANGEBOT)
			else frappe.get_meta("Quotation").default_print_format or None,
		)

	def test_angebot_uebersteuerung_bei_jedem_speichern_und_anker_im_speicher(self):
		"""Security-Review: Übersteuerung am Angebotsentwurf wird schon beim Speichern geprüft; der
		Anker eines ungespeicherten Belegs kommt aus seinen Verweisen."""
		kern = KernAttrappe()
		lead = td.lead_mit_angebot_status()
		with patch.object(mappe, "kern_da", return_value=True), patch.object(mappe, "_kern", kern.get_attr):
			angebot = td.angebot(lead.name, zusatz=False)
		# validate beim Anlegen, validate beim Freigeben und before_submit: dreimal gesichert, einmal geprüft
		self.assertEqual(len(kern.mit(mappe.KERN_UEBERSTEUERUNG)), 3)
		self.assertEqual(len(kern.mit(mappe.KERN_PRUEFEN)), 1)
		# ungespeicherte Rechnung: Anker über das Projekt im Speicher
		auftrag, projekt = self.auftrag_mit_projekt()
		kern = KernAttrappe()
		neu = self.rechnung(auftrag)
		self.assertTrue(neu.is_new())
		with patch.object(mappe, "kern_da", return_value=True), patch.object(mappe, "_kern", kern.get_attr):
			self.assertEqual(mappe.anker_des_belegs(neu), ANKER)
		self.assertEqual(kern.mit(mappe.KERN_ANKER)[0][0], "Project")
		# ungespeichertes Angebot an eine Anfrage
		kern = KernAttrappe()
		q = frappe.get_doc({"doctype": "Quotation", "quotation_to": "Lead", "party_name": lead.name})
		with patch.object(mappe, "kern_da", return_value=True), patch.object(mappe, "_kern", kern.get_attr):
			self.assertEqual(mappe.anker_des_belegs(q), ANKER)
		self.assertEqual(kern.mit(mappe.KERN_ANKER)[0], ("Lead", lead.name))
		del angebot, projekt

	def test_pdf_fehler_haelt_freigabe_nicht_auf(self):
		kern = KernAttrappe(pdf_fehler=frappe.ValidationError("wkhtmltopdf fehlt"))
		lead = td.lead_mit_angebot_status()
		vorher = frappe.db.count("Error Log")
		with patch.object(mappe, "kern_da", return_value=True), patch.object(mappe, "_kern", kern.get_attr):
			angebot = td.angebot(lead.name, zusatz=False)
		self.assertEqual(frappe.db.get_value("Quotation", angebot.name, "docstatus"), 1)
		self.assertGreater(frappe.db.count("Error Log"), vorher)
		self.assertTrue(frappe.db.exists("Error Log", {"method": ["like", "Dokumentenmappe: Angebots-PDF%"]}))

	def test_rechnung_pruefung_protokoll_und_uebersteuerung(self):
		auftrag, _projekt = self.auftrag_mit_projekt()
		kern = KernAttrappe()
		with patch.object(mappe, "kern_da", return_value=True), patch.object(mappe, "_kern", kern.get_attr):
			# Abschlagsrechnung: keine Pflichtprüfung, aber die Übersteuerungsfelder werden gesichert
			abschlag = _als(BUERO, self.rechnung(auftrag, cv_abschlag=1).insert)
			self.assertEqual(kern.mit(mappe.KERN_PRUEFEN), [])
			self.assertEqual(len(kern.mit(mappe.KERN_UEBERSTEUERUNG)), 1)
			abschlag.delete()
			# Entwurf (6.2 blockiert ohne Abnahme): Prüfung ohne Protokoll
			kern.aufrufe.clear()
			with self.assertRaises(RechnungOhneAbnahme):
				_als(BUERO, self.rechnung(auftrag).insert)
			pruefung = kern.mit(mappe.KERN_PRUEFEN)
			self.assertEqual(len(pruefung), 1)
			schritt, anker, doctype, _name, uebersteuert, grund, protokoll = pruefung[0]
			self.assertEqual(
				(schritt, anker, doctype, uebersteuert, grund, protokoll),
				(mappe.SCHRITT_RECHNUNG, ANKER, "Sales Invoice", False, None, False),
			)
		# neue Übersteuerung (Kern bestätigt sie): protokolliert und die Abnahmesperre 6.2 akzeptiert sie
		kern = KernAttrappe(uebersteuert=True)
		with patch.object(mappe, "kern_da", return_value=True), patch.object(mappe, "_kern", kern.get_attr):
			frei = _als(
				BUERO,
				self.rechnung(auftrag, cv_mappe_uebersteuert=1, cv_mappe_grund="Kunde zahlt vorab").insert,
			)
		self.assertTrue(frei.name)
		pruefung = kern.mit(mappe.KERN_PRUEFEN)
		self.assertEqual(len(pruefung), 1)
		self.assertEqual((pruefung[0][4], pruefung[0][5], pruefung[0][6]), (True, "Kunde zahlt vorab", True))
		frei.delete()

	def test_prozessleiste_mappe(self):
		auftrag, projekt = self.auftrag_mit_projekt()
		lead = frappe.db.get_value("Project", projekt, "cv_lead")
		kern = KernAttrappe()
		with patch.object(mappe, "kern_da", return_value=True), patch.object(mappe, "_kern", kern.get_attr):
			stand = _als(BUERO, status, "Project", projekt)
		self.assertEqual(
			stand["mappe"],
			{
				"vorgang": ANKER,
				"ampel": "gelb",
				"text": "Mappe: 1 Hinweis (Befundfoto) vor Schritt Angebot",
				"schritt": "Angebot",
			},
		)
		self.assertEqual(kern.mit(mappe.KERN_ANKER)[0], ("Lead", lead))
		self.assertEqual(kern.mit(mappe.KERN_STAND)[0], (ANKER, False))
		del auftrag
