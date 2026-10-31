"""Nachkalkulation (Phase 2, P2.1): Soll aus der Kalkulation gegen Ist aus Zeiten und Material, Ampel,
Bericht, Kennzahl der Prozessleiste, Lernschleife über die Schlussrechnung.

Die CI-Bench hat ``corevatis`` nicht: ``KernStellvertreter`` ersetzt die Rechnungs-Engine (wie
``test_abrechnung``). Angebot mit Kalkulation (22 min je Einheit, 48 €/h), Annahme, Auftrag, Projekt,
Arbeitspaket, Timesheets, Materialentnahme und Schlussrechnung sind echt (ERPNext).
"""

from __future__ import annotations

import json
import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, getdate, today

from corevatis_erpnext import material as m
from corevatis_erpnext import nachkalkulation as nk
from corevatis_erpnext import zeiterfassung as z
from corevatis_erpnext.abrechnung import kern, schluss
from corevatis_erpnext.prozess.kette import status
from corevatis_erpnext.tests import _abrechnung as H
from corevatis_erpnext.tests import _angebot as td
from corevatis_erpnext.tests._abrechnung import BUERO, MEISTER, MONTEUR, _als
from corevatis_erpnext.tests.material_testdaten import fahrzeug_auffuellen
from corevatis_erpnext.tests.test_material import ensure_item, ensure_warehouse
from corevatis_erpnext.tests.test_zeiterfassung import ensure_employee, ts

MATERIAL = "_TEST-CV-NK-DICHTSCHLAEMME"
MATERIAL_EAN = "2099990000501"
LAGER = "_Test Nachkalkulation"
TAG = getdate(today())


class TestNachkalkulation(IntegrationTestCase):
	_lauf = 0  # je Test ein eigener Tag, sonst überschneiden sich die Zeiten desselben Monteurs

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if kern.KERN_APP in frappe.get_installed_apps():
			raise unittest.SkipTest(
				"corevatis installiert - Stellvertreter der Engine würde die echte überdecken"
			)
		frappe.set_user("Administrator")
		H.einrichten()
		z.ensure_setup()
		m.ensure_setup()
		cls.employee = ensure_employee(MONTEUR)
		cls.lager = ensure_warehouse(LAGER)
		ensure_item(MATERIAL, "Kg", MATERIAL_EAN)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		H.kern_patch(self)
		self.auftrag, self.projekt = H.vorgang(kalkulation=True)
		self.task = self.arbeitspaket()
		TestNachkalkulation._lauf += 1
		self.tag = add_days(TAG, -TestNachkalkulation._lauf)

	# --- Hilfen ---------------------------------------------------------------------------------

	def arbeitspaket(self) -> str:
		"""Arbeitspaket des Verfahrens wie 5.1 (``Task.cv_verfahren``), falls der Hook keins angelegt hat."""
		vorhanden = frappe.get_all("Task", filters={"project": self.projekt}, pluck="name")
		if vorhanden:
			return vorhanden[0]
		return (
			frappe.get_doc(
				{
					"doctype": "Task",
					"subject": "Innenabdichtung",
					"project": self.projekt,
					"cv_auftrag": self.auftrag,
					"cv_verfahren": "Innenabdichtung",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def zeit(self, art: str, von: str, bis: str) -> None:
		z.zeit_buchen(
			self.employee,
			self.projekt,
			self.task,
			art,
			ts(self.tag, von),
			ts(self.tag, bis),
			frappe.generate_hash(),
		)

	def material(self, menge: float) -> float:
		"""Entnahme buchen; Rückgabe des gebuchten Betrags (Bewertung des Stock Entry)."""
		fahrzeug_auffuellen(self.lager, {MATERIAL: 20}, commit=False)
		r = m.entnahme_buchen(
			self.employee,
			self.lager,
			self.projekt,
			self.task,
			[{"item_code": MATERIAL, "menge": menge}],
			frappe.generate_hash(length=32),
		)
		zeilen = frappe.get_all(
			"Stock Entry Detail", filters={"parent": r["stock_entry"]}, fields=["basic_amount"]
		)
		return flt(sum(flt(x.basic_amount) for x in zeilen), 2)

	def zeile(self, bericht: dict, verfahren: str) -> dict:
		return next(r for r in bericht["zeilen"] if r["verfahren"] == verfahren)

	# --- Ampel (reine Funktion) -----------------------------------------------------------------

	def test_ampel_grenzen(self):
		self.assertEqual(nk.ampel(10, 0, 0), (nk.GRAU, None))
		self.assertEqual(nk.ampel(0, 2, 0), (nk.GELB, None))
		self.assertEqual(nk.ampel(10, 10, 0)[0], nk.GRUEN)
		self.assertEqual(nk.ampel(10, 11, 0)[0], nk.GELB)
		self.assertEqual(nk.ampel(10, 11.5, 0)[0], nk.GELB)
		self.assertEqual(nk.ampel(10, 11.6, 0)[0], nk.ROT)
		# Fortschritt 40 %: erwartet 4 h, 5 h Ist = 125 % → rot; 3 h = 75 % → grün
		self.assertEqual(nk.ampel(10, 5, 40)[0], nk.ROT)
		self.assertEqual(nk.ampel(10, 3, 40)[0], nk.GRUEN)

	# --- Soll und Ist am Projekt ----------------------------------------------------------------

	def test_soll_aus_kalkulation_ist_aus_zeiten_und_material(self):
		a = nk.projekt_auswerten(self.projekt)
		self.assertEqual(a["auftrag"], self.auftrag)
		self.assertEqual(a["stundensatz"], 48.0)
		s = self.zeile(a, "Innenabdichtung")
		# 10 Einheiten x 22 min = 220 min = 3,67 h; Lohn Soll 3,67 h x 48 € = 176 €; Erlös 10 x 50 €
		self.assertEqual(
			(s["soll_h"], s["soll_lohn"], s["erloes"], s["soll_material"]), (3.67, 176.0, 500.0, 0.0)
		)
		self.assertEqual((s["ist_h"], s["ampel"], s["verbrauch"]), (0.0, nk.GRAU, 0.0))
		self.assertEqual(s["marge_soll"], 324.0)
		self.assertEqual(s["tasks"], [self.task])

		# Ist: 2 h Arbeit, 30 min Fahrt, 30 min Pause (zählt nicht) → 2,5 h, 68 % → grün
		self.zeit(z.ARBEITSZEIT, "08:00", "10:00")
		self.zeit(z.FAHRZEIT, "07:30", "08:00")
		self.zeit(z.PAUSE, "10:00", "10:30")
		betrag = self.material(4)
		self.assertGreater(betrag, 0)
		a = nk.projekt_auswerten(self.projekt)
		s = self.zeile(a, "Innenabdichtung")
		self.assertEqual((s["ist_h"], s["fahrt_h"], s["verbrauch"], s["ampel"]), (2.5, 0.5, 68.0, nk.GRUEN))
		self.assertEqual(s["ist_lohn"], 120.0)
		self.assertEqual(s["ist_material"], flt(betrag, 2))
		self.assertEqual(s["marge_ist"], flt(500 - 120 - betrag, 2))
		self.assertEqual(a["summe"]["ampel"], nk.GRUEN)

		# Zeitraumfilter: morgen nichts
		a = nk.projekt_auswerten(self.projekt, von=add_days(self.tag, 1))
		self.assertEqual(self.zeile(a, "Innenabdichtung")["ist_h"], 0.0)

		# 2 h mehr: 4,5 h = 123 % → rot
		self.zeit(z.ARBEITSZEIT, "11:00", "13:00")
		a = nk.projekt_auswerten(self.projekt)
		s = self.zeile(a, "Innenabdichtung")
		self.assertEqual((s["ist_h"], s["verbrauch"], s["ampel"]), (4.5, 123.0, nk.ROT))

	def test_buchung_ohne_arbeitspaket(self):
		z.zeit_buchen(
			self.employee,
			self.projekt,
			None,
			z.ARBEITSZEIT,
			ts(self.tag, "08:00"),
			ts(self.tag, "09:00"),
			frappe.generate_hash(),
		)
		a = nk.projekt_auswerten(self.projekt)
		s = self.zeile(a, nk.OHNE_PAKET)
		self.assertEqual((s["ist_h"], s["soll_h"], s["ampel"]), (1.0, 0.0, nk.GELB))
		# Verfahren zuerst, Sammelzeile zuletzt
		self.assertEqual([r["verfahren"] for r in a["zeilen"]], ["Innenabdichtung", nk.OHNE_PAKET])

	# --- Bericht, Rechte, Kennzahl ---------------------------------------------------------------

	def test_bericht_und_rechte(self):
		self.zeit(z.ARBEITSZEIT, "08:00", "10:00")
		with self.assertRaises(frappe.PermissionError):
			_als(MONTEUR, nk.uebersicht, {"projekt": self.projekt})
		from corevatis_erpnext.corevatis_erpnext.report.nachkalkulation.nachkalkulation import execute

		spalten, zeilen, _nachricht, _chart, kennzahlen = _als(BUERO, execute, {"projekt": self.projekt})
		self.assertEqual([r["indent"] for r in zeilen], [0, 1])
		self.assertEqual(zeilen[0]["projekt"], self.projekt)
		self.assertIn(zeilen[0]["status"], ("Open", "Offen"))
		self.assertEqual(
			(zeilen[1]["bezeichnung"], zeilen[1]["arbeitspaket"]), ("Innenabdichtung", self.task)
		)
		self.assertEqual((zeilen[1]["soll_h"], zeilen[1]["ist_h"], zeilen[1]["ampel"]), (3.67, 2.0, nk.GRUEN))
		self.assertTrue({c["fieldname"] for c in spalten} >= {"soll_h", "ist_h", "marge_ist", "ampel"})
		self.assertEqual(kennzahlen[0]["value"], 1)
		# Verfahrensfilter ohne Treffer: Projekt fällt weg
		_s, zeilen, *_rest = _als(MEISTER, execute, {"projekt": self.projekt, "verfahren": "Außenabdichtung"})
		self.assertEqual(zeilen, [])
		# ohne Projektfilter: Liste der Projekte mit Auftrag (Leserecht)
		daten = _als(MEISTER, nk.uebersicht, {"nur_offene": 1})
		self.assertIn(self.projekt, [r["projekt"] for r in daten["zeilen"] if r["indent"] == 0])

	def test_kennzahl_prozessleiste(self):
		# ohne Ist, aber mit Soll: Kennzahl grau
		k = _als(MEISTER, nk.kennzahl, self.projekt)
		self.assertEqual((k["ampel"], k["verbrauch"]), (nk.GRAU, 0.0))
		self.zeit(z.ARBEITSZEIT, "08:00", "10:00")
		self.zeit(z.FAHRZEIT, "07:30", "08:00")
		k = _als(MEISTER, nk.kennzahl, self.projekt)
		self.assertEqual((k["ampel"], k["verbrauch"], k["ist_h"]), (nk.GRUEN, 68.0, 2.5))
		self.assertIn("68 % der Sollstunden", k["text"])
		self.assertIn("Marge", k["text"])
		# Monteur: keine Kennzahl
		self.assertIsNone(_als(MONTEUR, nk.kennzahl, self.projekt))
		# Prozessleiste am Projekt trägt die Kennzahl, am Auftrag nicht (Rechenaufwand)
		leiste = _als(MEISTER, status, "Project", self.projekt)
		self.assertEqual(leiste["nachkalkulation"]["ampel"], nk.GRUEN)
		self.assertEqual(leiste["nachkalkulation"]["projekt"], self.projekt)
		self.assertIsNone(_als(MEISTER, status, "Sales Order", self.auftrag)["nachkalkulation"])

	# --- Schlussrechnung und Lernschleife --------------------------------------------------------

	def test_erloes_aus_schlussrechnung_und_regelvorschlag(self):
		self.zeit(z.ARBEITSZEIT, "08:00", "12:00")  # 4 h = 109 % der 3,67 h
		H.abnehmen(self.projekt)
		so_detail = frappe.db.get_value(
			"Sales Order Item", {"parent": self.auftrag, "item_code": td.ARTIKEL}, "name"
		)
		r = _als(
			BUERO,
			schluss.schlussrechnung_erstellen,
			project=self.projekt,
			positionen=json.dumps({so_detail: {"menge_ist": 12}}),
		)
		H.freigeben(r["rechnung"])
		a = _als(BUERO, nk.projekt_auswerten, self.projekt)
		s = self.zeile(a, "Innenabdichtung")
		# Erlös 12 x 50 € aus der Schlussrechnung (Auftragszeile 10 + Mehrmenge 2)
		self.assertEqual(
			(s["erloes"], s["erloes_quelle"], a["schlussrechnung"]), (600.0, "Schlussrechnung", r["rechnung"])
		)
		self.assertEqual((s["ist_h"], s["ampel"]), (4.0, nk.GELB))
		self.assertEqual(s["marge_ist"], flt(600 - 4 * 48, 2))

		with self.assertRaises(frappe.PermissionError):
			_als(MONTEUR, nk.regeln, {})
		daten = _als(BUERO, nk.regeln, {})
		zeile = next(
			x for x in daten["zeilen"] if x["item_code"] == td.ARTIKEL and x["verfahren"] == "Innenabdichtung"
		)
		self.assertGreaterEqual(zeile["projekte"], 1)
		# dieser Auftrag: 10 → 12; weitere Testaufträge desselben Artikels ändern nur die Summen
		self.assertGreaterEqual(zeile["ist_menge"], zeile["soll_menge"])
		self.assertIsNotNone(zeile["mengenfaktor"])
		self.assertEqual(zeile["soll_min_je_einheit"], 22.0)
		# Zeitfaktor dieses Projekts 4 h / 3,67 h = 1,09 fließt in den Mittelwert ein
		self.assertIsNotNone(zeile["zeitfaktor"])
		self.assertNotEqual(zeile["vorschlag"], "")
		# Vorschlagstext: Mengenfaktor 1,2 → „120 %“, Zeitfaktor → neue Minuten je Einheit; passt bei < 5 %
		self.assertIn("120 %", nk.vorschlag(1.2, None, "", 22))
		text = nk.vorschlag(None, 1.09, "", 22)
		self.assertIn("Montageminuten je Einheit", text)
		self.assertIn("statt", text)
		self.assertEqual(nk.vorschlag(1.02, 0.97, "R1", 22), "passt")
		from corevatis_erpnext.corevatis_erpnext.report.mengenregeln_nachjustieren.mengenregeln_nachjustieren import (
			execute,
		)

		_spalten, zeilen, nachricht, _chart, kennzahlen = _als(
			MEISTER, execute, {"verfahren": "Innenabdichtung"}
		)
		self.assertTrue(zeilen)
		self.assertIn("Schlussrechnungen", nachricht)
		self.assertEqual(kennzahlen[0]["label"], "Leistungen")
