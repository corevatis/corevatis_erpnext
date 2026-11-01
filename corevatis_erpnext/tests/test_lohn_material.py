"""Lohn und Material getrennt ausweisen (Handwerker#105): Einstellungen, Angebot → Auftrag → Rechnung
(Spiegel, Teilmenge anteilig, § 35a = Lohn), Abschlags- und Schlussrechnung mit Regiestunde.

Die CI-Bench hat ``corevatis`` nicht: ``AnteileStellvertreter`` ersetzt ``corevatis.engine.anteile``,
``KernStellvertreter`` ``corevatis.engine.invoice`` (Rechenregeln: Kern-Tests ``test_engine_anteile``).
Sollwerte von Hand: Position 10 x 50 € mit Lohn 17,60 € je Einheit und Einzelkosten 25,88 €,
mit Zuschlägen Lohn = 500 x 17,60 ÷ 25,88 = 340,03 €, Material 159,97 €.
"""

from __future__ import annotations

import json
import unittest
import unittest.mock

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint, flt

from corevatis_erpnext.abrechnung import abschlag, kern, lohn_material, schluss
from corevatis_erpnext.abrechnung.einstellungen import EINSTELLUNGEN, einstellungen
from corevatis_erpnext.angebot import annahme
from corevatis_erpnext.tests import _abrechnung as H
from corevatis_erpnext.tests import _angebot as td

REGIE = "_TEST-CV-REGIE-H"
KALKULATION = json.dumps(
	{
		"ok": True,
		"positionen": [
			{
				"id": td.ARTIKEL,
				"item_code": td.ARTIKEL,
				"kalkulation": {
					"material": "7.20",
					"materialzuschlag": "1.08",
					"minuten": "22.00",
					"lohn": "17.60",
					"erschwernis": "0.00",
					"einzelkosten": "25.88",
					"einzelpreis": "50.00",
				},
			}
		],
	}
)


def anteile(zeilen) -> list[tuple]:
	return [(flt(z.cv_materialanteil), flt(z.cv_lohnanteil)) for z in zeilen]


class TestLohnMaterial(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if kern.KERN_APP in frappe.get_installed_apps():
			raise unittest.SkipTest(
				"corevatis installiert - Stellvertreter würden die echte Engine überdecken"
			)
		frappe.set_user("Administrator")
		from corevatis_erpnext.katalog.item_felder import ensure_item_fields

		ensure_item_fields()  # Item.cv_verfahren (Regiestunde: „Sonstiges“)
		H.einrichten()
		if not frappe.db.exists("UOM", "h"):
			frappe.get_doc({"doctype": "UOM", "uom_name": "h"}).insert(ignore_permissions=True)
		if not frappe.db.exists("Item", REGIE):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": REGIE,
					"item_name": "Regiestunde Monteur",
					"item_group": frappe.db.get_value("Item Group", {"is_group": 0}),
					"stock_uom": "h",
					"is_stock_item": 0,
					"cv_verfahren": "Sonstiges",
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Singles", {"doctype": EINSTELLUNGEN})
		frappe.db.delete("Singles", {"doctype": "Corevatis Abnahme Einstellungen"})
		self.anteile = H.anteile_patch(self)
		H.kern_patch(self)

	def test_einstellungen_standard_und_schutz(self):
		e = einstellungen()
		self.assertEqual((e.lohnanteil_mit_zuschlaegen, e.lohn_material_je_position), (1, 1))
		# Handwerker#99 auch hier: nie gespeicherte Checks laden mit ihrem Default, Speichern schreibt ihn
		doc = frappe.get_single(EINSTELLUNGEN)
		self.assertEqual((doc.lohnanteil_mit_zuschlaegen, doc.lohn_material_je_position), (1, 1))
		doc.lohnanteil_prozent_standard = 10
		doc.save()
		gespeichert = frappe.db.get_singles_dict(EINSTELLUNGEN)
		self.assertEqual(cint(gespeichert.get("lohn_material_je_position")), 1)
		self.assertEqual(cint(gespeichert.get("lohnanteil_mit_zuschlaegen")), 1)
		# bewusst ausgeschaltet bleibt aus
		frappe.db.set_single_value(EINSTELLUNGEN, "lohn_material_je_position", 0)
		self.assertEqual(frappe.get_single(EINSTELLUNGEN).lohn_material_je_position, 0)
		self.assertEqual(einstellungen().lohn_material_je_position, 0)

	def test_angebot_auftrag_rechnung(self):
		frappe.db.set_single_value(EINSTELLUNGEN, "lohnanteil_prozent_standard", 20)
		lead = td.lead_mit_angebot_status()
		angebot = td.angebot(lead.name, zusatz=True, cv_kalkulation_json=KALKULATION)
		# Kalkulation (mit Zuschlägen), Eventual 1 x 100 und Alternative 2 x 30 ohne Kalkulation: 20 %
		self.assertEqual(anteile(angebot.items), [(159.97, 340.03), (80.0, 20.0), (48.0, 12.0)])
		# Summen nur über Positionen in der Angebotssumme; brutto mit 19 %: Lohn 64,61, Material 30,39 USt
		self.assertEqual(
			(
				flt(angebot.cv_material_netto),
				flt(angebot.cv_lohn_netto),
				flt(angebot.cv_material_brutto),
				flt(angebot.cv_lohn_brutto),
			),
			(159.97, 340.03, 190.36, 404.64),
		)
		self.assertEqual(flt(angebot.cv_material_netto + angebot.cv_lohn_netto, 2), flt(angebot.net_total))
		self.assertEqual(
			flt(angebot.cv_material_brutto + angebot.cv_lohn_brutto, 2), flt(angebot.grand_total)
		)
		# Auftrag: Spiegel der Angebotszeile (anteilig), auch wenn sich die Einstellung inzwischen ändert
		frappe.db.set_single_value(EINSTELLUNGEN, "lohnanteil_mit_zuschlaegen", 0)
		r = H._als(H.BUERO, annahme.angebot_annehmen, angebot.name, einreichen=1)
		auftrag = frappe.get_doc("Sales Order", r["auftrag"])
		haupt = next(z for z in auftrag.items if z.item_code == td.ARTIKEL)
		self.assertEqual(anteile([haupt]), [(159.97, 340.03)])
		bezuege = [p.get("bezug") for positionen, _o in self.anteile.aufrufe for p in positionen]
		self.assertIn({"betrag": "500.0", "lohn": "340.03"}, bezuege)
		self.assertEqual(flt(auftrag.cv_material_netto + auftrag.cv_lohn_netto, 2), flt(auftrag.net_total))
		# Rechnung über 4 von 10: Lohn 340,03 x 200 ÷ 500 = 136,01; § 35a = derselbe Lohn
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

		H.abnehmen(r["projekt"])
		si = make_sales_invoice(auftrag.name)
		si.set("items", [z for z in si.items if z.so_detail == haupt.name])
		si.items[0].qty = 4
		si.insert()
		self.assertEqual(anteile(si.items), [(63.99, 136.01)])
		self.assertEqual(
			(
				flt(si.cv_lohn_netto),
				flt(si.cv_lohn_brutto),
				flt(si.cv_material_netto),
				flt(si.cv_material_brutto),
			),
			(136.01, 161.85, 63.99, 76.15),
		)
		self.assertEqual(
			(flt(si.cv_lohnanteil_netto), flt(si.cv_lohnanteil_steuer), flt(si.cv_lohnanteil_brutto)),
			(136.01, 25.84, 161.85),
		)
		# Formularwerte zählen nicht: der Server rechnet beim Speichern neu
		si.items[0].cv_lohnanteil = 1
		si.cv_lohn_netto = 1
		si.save()
		self.assertEqual((flt(si.items[0].cv_lohnanteil), flt(si.cv_lohn_netto)), (136.01, 136.01))

	def test_abschlag_und_schlussrechnung_mit_regie(self):
		auftrag, projekt = H.vorgang()  # Kalkulation per Generator-Weg: Lohn 340,03
		self.assertEqual(flt(frappe.db.get_value("Sales Order", auftrag, "cv_lohn_netto")), 340.03)
		# Abschlag 30 %: § 35a anteilig aus dem Auftrag 340,03 x 30 % = 102,01; Material = Rest
		r1 = H._als(H.BUERO, abschlag.abschlag_stellen, project=projekt)
		si1 = frappe.get_doc("Sales Invoice", r1["rechnung"])
		self.assertEqual(anteile(si1.items), [(47.99, 102.01)])
		self.assertEqual(
			(flt(si1.cv_lohnanteil_netto), flt(si1.cv_lohn_netto), flt(si1.cv_lohn_brutto)),
			(102.01, 102.01, 121.39),
		)
		self.assertEqual((flt(si1.cv_material_netto), flt(si1.cv_material_brutto)), (47.99, 57.11))
		H.freigeben(r1["rechnung"])
		H.abnehmen(projekt)
		# Schlussrechnung: Auftragsposition 340,03 (aus der Auftragszeile), Regie 2 h x 69,60 = 100 % Lohn
		r = H._als(
			H.BUERO,
			schluss.schlussrechnung_erstellen,
			project=projekt,
			nachtraege=json.dumps([{"item_code": REGIE, "menge": 2, "einzelpreis": 69.60}]),
		)
		si = frappe.get_doc("Sales Invoice", r["rechnung"])
		self.assertEqual(
			[(z.cv_positionsart, flt(z.cv_materialanteil), flt(z.cv_lohnanteil)) for z in si.items],
			[("Auftragsposition", 159.97, 340.03), ("Nachtrag", 0.0, 139.2), ("Abzug Abschlag", 0.0, 0.0)],
		)
		# Summen der Leistung 639,20 (vor dem Abzug): Lohn 479,23 = § 35a, Material 159,97
		self.assertEqual(
			(flt(si.cv_lohnanteil_netto), flt(si.cv_lohn_netto), flt(si.cv_material_netto)),
			(479.23, 479.23, 159.97),
		)
		self.assertEqual(flt(si.cv_lohn_brutto), flt(si.cv_lohnanteil_brutto))
		self.assertEqual(flt(si.cv_lohn_brutto + si.cv_material_brutto, 2), flt(si.cv_leistung_brutto))

	def test_nachtragen_ohne_engine(self):
		"""Ohne Kern (``corevatis``) bleiben die Felder unverändert, Speichern geht."""
		with unittest.mock.patch.object(lohn_material, "_engine", lambda: None):
			lead = td.lead_mit_angebot_status()
			angebot = td.angebot(lead.name, zusatz=False, cv_kalkulation_json=KALKULATION)
			self.assertEqual(anteile(angebot.items), [(0.0, 0.0)])
			self.assertFalse(lohn_material.nachtragen("Quotation", angebot.name))
		self.assertTrue(lohn_material.nachtragen("Quotation", angebot.name))
		self.assertEqual(flt(frappe.db.get_value("Quotation", angebot.name, "cv_lohn_netto")), 340.03)
