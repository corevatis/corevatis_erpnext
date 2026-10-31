"""Meine Woche in der App (Phase 1 P1.4b, Issue Handwerker#84) - Adapter gegen echtes ERPNext v16.

Eine Woche ab dem nächsten Montag M (Feiertag Fr gemockt, Geokodierung aus, Firmenadresse gemockt):

| Benutzer | Mo | Di | Mi | Do | Fr |
|---|---|---|---|---|---|
| Anton (Monteur) | A1 07-15 (Fahrzeug F1), A3 13-16 (Überschneidung) | A2 07:00 - | Mi 11:00 | Urlaub | Feiertag |
| Martin (Meister) | | Termin 09-10 Kellerweg | | | Feiertag |

Geprüft: Einträge je Tag (Segmente, Arbeitszeit-Beschnitt), Fahrzeug, Konflikte, Adresse (eigenes
Projekt voll, sonst PLZ/Ort), Abwesenheit (Art nur für Betroffene und Büro), Feiertag, ``geaendert``,
Rechte (fremde Woche nur Planer), Zeitraumgrenze, Hook und - mit installiertem Kern - der Endpunkt.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate

from corevatis_erpnext.crm import termin as crm_termin
from corevatis_erpnext.planung import geokodierung, terminplanung, woche, zuweisung
from corevatis_erpnext.tests._angebot import firma
from corevatis_erpnext.tests._helpers import make_lead, make_user
from corevatis_erpnext.tests.test_terminplanung import ADRESSE_FIRMA, ADRESSE_HAMM
from corevatis_erpnext.tests.test_wochenplan import als, employee, naechster_montag, um

BUERO = "cv-wo-buero@example.com"
MEISTER = "cv-wo-meister@example.com"
MONTEUR = "cv-wo-anton@example.com"
F1 = "MS-WO 1"


def fahrzeug(kennzeichen: str, bezeichnung: str) -> str:
	if frappe.db.exists("Fahrzeug", kennzeichen):
		return kennzeichen
	return (
		frappe.get_doc(
			{"doctype": "Fahrzeug", "kennzeichen": kennzeichen, "bezeichnung": bezeichnung, "aktiv": 1}
		)
		.insert(ignore_permissions=True)
		.name
	)


class TestWoche(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		from corevatis_erpnext.planung.felder import ensure_planung_felder

		ensure_planung_felder()
		make_user(BUERO, "Bea", ["Büro", "Sales User", "Projects User"], "Woche")
		make_user(MEISTER, "Martin", ["Meister", "Sales User", "Projects Manager"], "Woche")
		make_user(MONTEUR, "Anton", ["Monteur"], "Woche")
		cls.e_meister = employee(MEISTER, "Martin")
		cls.e_monteur = employee(MONTEUR, "Anton")
		cls.f1 = fahrzeug(F1, "Sprinter")
		cls.m = naechster_montag()
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.savepoint("woche_test")
		self.addCleanup(self._zuruecksetzen)
		patch.object(geokodierung, "abfragen", return_value=None).start()
		patch.object(terminplanung, "firmen_adresse", return_value=ADRESSE_FIRMA).start()
		patch.object(terminplanung, "_feiertage", side_effect=self._feiertage).start()
		self.addCleanup(patch.stopall)
		m = self.m
		self.mo, self.di, self.mi, self.do, self.fr, self.sa, self.so = (
			m + dt.timedelta(days=n) for n in range(7)
		)
		self.lead = make_lead(
			last_name=f"Woche {frappe.generate_hash(length=6)}",
			cv_objekt_adresse="Kellerweg 7\n48143 Münster",
		)
		hamm = make_lead(first_name="Baustelle", last_name="Hamm", cv_objekt_adresse=ADRESSE_HAMM)
		self.projekt = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"Woche Hamm {frappe.generate_hash(length=6)}",
				"company": firma(),
				"expected_start_date": add_days(m, -7),
				"expected_end_date": add_days(m, 10),
				"cv_lead": hamm.name,
			}
		).insert(ignore_permissions=True)
		self.a1 = self.task(
			"Innenabdichtung - Keller", um(self.mo, "07:00"), um(self.mo, "15:00"), 8, "Innenabdichtung"
		)
		self.a2 = self.task(
			"Sanierputz - Keller", um(self.di, "07:00"), um(self.mi, "11:00"), 12, "Sanierputz"
		)
		self.a3 = self.task("Nacharbeit", um(self.mo, "13:00"), um(self.mo, "16:00"), 3, "Innenabdichtung")
		self.termin = als(
			BUERO,
			crm_termin.termin_anlegen,
			self.lead.name,
			f"{self.di} 09:00:00",
			MEISTER,
			60,
			bestaetigung=0,
		)["event"]
		for task in (self.a1, self.a2, self.a3):
			als(BUERO, zuweisung.zuweisen, task, self.e_monteur)
		frappe.db.set_value("Task", self.a1, "cv_fahrzeug", self.f1)
		self.abw = (
			frappe.get_doc(
				{
					"doctype": "Abwesenheit",
					"mitarbeiter": self.e_monteur,
					"art": "Urlaub",
					"von": self.do,
					"bis": self.do,
					"ganztags": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _zuruecksetzen(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="woche_test")
		from frappe.model.document import clear_document_cache

		for doctype in ("Project", "Task", "ToDo", "Event", "Abwesenheit", "Lead"):
			clear_document_cache(doctype)

	def _feiertage(self, firma_, von, bis):
		return {self.fr: "Testfeiertag"} if getdate(von) <= self.fr <= getdate(bis) else {}

	def task(self, titel, start, ende, stunden, verfahren) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Task",
					"subject": titel,
					"project": self.projekt.name,
					"company": firma(),
					"status": "Open",
					"exp_start_date": start,
					"exp_end_date": ende,
					"expected_time": stunden,
					"cv_verfahren": verfahren,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	@staticmethod
	def eintrag(daten: dict, doctype: str, name: str) -> dict:
		return next(e for e in daten["eintraege"] if e.get("doctype") == doctype and e.get("name") == name)

	def test_monteur_woche(self):
		d = als(MONTEUR, woche.woche, MONTEUR, self.mo, self.so)
		self.assertEqual(d["mitarbeiter"], self.e_monteur)
		self.assertEqual((d["arbeitsbeginn"], d["tagesarbeitszeit"]), ("07:00", 8.0))
		self.assertEqual(d["feiertage"], {str(self.fr): "Testfeiertag"})
		a1 = self.eintrag(d, "Task", self.a1)
		self.assertEqual(set(a1), set(woche.FELDER))
		self.assertEqual(
			(a1["art"], a1["key"], a1["datum"], a1["von"], a1["bis"], a1["ganztags"]),
			(
				"arbeitspaket",
				f"Task:{self.a1}",
				str(self.mo),
				f"{self.mo} 07:00:00",
				f"{self.mo} 15:00:00",
				False,
			),
		)
		self.assertEqual(
			(a1["titel"], a1["fahrzeug"], a1["fahrzeug_name"], a1["sollstunden"]),
			("Innenabdichtung", self.f1, "Sprinter", 8.0),
		)
		self.assertEqual(a1["geaendert"], str(frappe.db.get_value("Task", self.a1, "modified")))
		self.assertTrue(any("Überschneidung" in k and self.a3 in k for k in a1["konflikte"]), a1["konflikte"])
		# Adresse: Projekt lesbar → volle Anschrift für die Navigation, sonst PLZ/Ort wie die Tafel
		if frappe.has_permission("Project", "read", doc=self.projekt.name, user=MONTEUR):
			self.assertEqual(
				(a1["adresse"], a1["projekt"], a1["projekt_name"]),
				(ADRESSE_HAMM, self.projekt.name, self.projekt.project_name),
			)
		else:
			self.assertEqual((a1["adresse"], a1["projekt"]), ("59067 Hamm", None))
		# mehrtägig: eine Zeile je Tag, auf die Arbeitszeit beschnitten
		a2 = [e for e in d["eintraege"] if e.get("name") == self.a2]
		self.assertEqual(
			[(e["datum"], e["von"][-8:], e["bis"][-8:]) for e in a2],
			[(str(self.di), "07:00:00", "15:00:00"), (str(self.mi), "07:00:00", "11:00:00")],
		)
		self.assertEqual({e["key"] for e in a2}, {f"Task:{self.a2}"})
		# eigene Abwesenheit mit Art, Feiertag ganztags
		abw = self.eintrag(d, "Abwesenheit", self.abw)
		self.assertEqual(
			(abw["art"], abw["datum"], abw["ganztags"], abw["titel"], abw["lesbar"], abw["von"]),
			("abwesenheit", str(self.do), True, "Urlaub", True, None),
		)
		self.assertTrue(abw["geaendert"])
		feiertag = next(e for e in d["eintraege"] if e["art"] == "feiertag")
		self.assertEqual(
			(feiertag["datum"], feiertag["titel"], feiertag["ganztags"]), (str(self.fr), "Testfeiertag", True)
		)
		self.assertFalse([e for e in d["eintraege"] if e["art"] == "termin"])

	def test_meister_termin(self):
		d = als(MEISTER, woche.woche, MEISTER, self.mo, self.so)
		t = self.eintrag(d, "Event", self.termin)
		self.assertEqual(
			(t["art"], t["datum"], t["von"], t["bis"], t["ganztags"]),
			("termin", str(self.di), f"{self.di} 09:00:00", f"{self.di} 10:00:00", False),
		)
		self.assertEqual(
			(t["lead"], t["kunde"], t["adresse"], t["lesbar"], t["status"]),
			(
				self.lead.name,
				frappe.db.get_value("Lead", self.lead.name, "lead_name"),
				"Kellerweg 7, 48143 Münster",
				True,
				"Open",
			),
		)
		self.assertTrue(t["titel"] and t["geaendert"])
		self.assertEqual(d["mitarbeiter"], self.e_meister)
		self.assertFalse([e for e in d["eintraege"] if e["art"] == "arbeitspaket"])

	def test_rechte_zeitraum_hook(self):
		with self.assertRaises(frappe.PermissionError):
			als(MONTEUR, woche.woche, MEISTER, self.mo, self.so)
		# Planer dürfen die Woche anderer sehen - die Art einer fremden Abwesenheit nur das Büro
		d = als(BUERO, woche.woche, MONTEUR, self.mo, self.so)
		self.assertEqual(self.eintrag(d, "Abwesenheit", self.abw)["titel"], "Urlaub")
		d = als(MEISTER, woche.woche, MONTEUR, self.mo, self.so)
		abw = self.eintrag(d, "Abwesenheit", self.abw)
		self.assertEqual((abw["titel"], abw["lesbar"]), ("Abwesend", False))
		self.assertIn(self.a1, [e.get("name") for e in d["eintraege"]])
		with self.assertRaises(frappe.ValidationError):
			als(MONTEUR, woche.woche, MONTEUR, self.mo, add_days(self.mo, 40))
		self.assertEqual(frappe.get_hooks("corevatis_woche_adapter"), ["corevatis_erpnext.planung.woche"])

	def test_kern_endpunkt(self):
		"""Mit installiertem Kern liest ``corevatis.api.woche.meine_woche`` die Woche über den Hook."""
		if "corevatis" not in frappe.get_installed_apps():
			self.skipTest("Kern corevatis nicht installiert")
		# Lizenzgrenze: kein Python-Import aus dem Kern, Aufruf über den Punktpfad wie in der App-Grenze üblich
		meine_woche = frappe.get_attr("corevatis.api.woche.meine_woche")
		d = als(MONTEUR, meine_woche, str(self.mo), str(self.so))
		self.assertEqual(
			(d["quelle"], d["von"], d["bis"], len(d["tage"])), ("adapter", str(self.mo), str(self.so), 7)
		)
		self.assertEqual(d["tage"][4]["feiertag"], "Testfeiertag")
		self.assertEqual(
			[e["art"] for e in d["eintraege"]],
			["arbeitspaket", "arbeitspaket", "arbeitspaket", "arbeitspaket", "abwesenheit", "feiertag"],
		)
		self.assertEqual(d["einsaetze"], 4)
		self.assertTrue(d["stand"])
		a1 = next(e for e in d["eintraege"] if e["name"] == self.a1)
		self.assertEqual((a1["fahrzeug_name"], a1["key"]), ("Sprinter", f"Task:{self.a1}"))
