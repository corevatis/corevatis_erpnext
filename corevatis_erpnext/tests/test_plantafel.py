"""Plantafel (Phase 1 P1.4, Issue corevatis/Handwerker#79) - gegen echtes ERPNext v16.

Eine Woche ab dem nächsten Montag M (Feiertag Do gemockt, Nominatim gemockt, Firmenadresse gemockt):

| Zeile | Mo | Di | Mi | Fr |
|---|---|---|---|---|
| Anton (Monteur) | A1 07-15 (Fahrzeug F1), A6 13-16 (Überschneidung) | A2 07:00 - | Mi 11:00 | |
| Berta (Monteurin) | A7 08-12 (Fahrzeug F1 → Fahrzeugkonflikt mit A1) | | Urlaub ganztags, A3 07-11 (Konflikt) | |
| Martin (Meister 1) | | Termin 09-10 Mühlenweg, A5 10:05-12:00 Hamm (Fahrzeit-Hinweis) | | |
| Moritz (Meister 2) | | | | Schulung 12-15 |
| Nicht zugewiesen | | | | A4 07-11 |

Geprüft: Aufbau der Tafel (Woche/Monat, Sa/So, Feiertag, Segmente, Konflikte, Hinweise, Fahrzeug),
Verschieben eines Arbeitspakets (Tag, Mitarbeiter, Konflikt mit und ohne „trotzdem“, Freigeben),
Verschieben eines Termins (Zeit, Meister, Konflikt, Zuweisung, Freigabe, Bestätigungstext),
Fahrzeugzuordnung mit Konflikt, Rechte des Monteurs (eigene Zeile, nur lesen, eigene Abwesenheiten) und des
Meisters (alle Abwesenheiten ohne Art),
Reihenfolge und Filter, Druck (HTML, PDF), Felder, Seite, Leiste, Übersetzungen.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, get_fullname, getdate

from corevatis_erpnext.crm import termin as crm_termin
from corevatis_erpnext.planung import (
	abwesenheit,
	geokodierung,
	plantafel,
	terminplanung,
	wochenplan,
	zuweisung,
)
from corevatis_erpnext.planung.auftrag import PlanungFehler
from corevatis_erpnext.tests._angebot import firma
from corevatis_erpnext.tests._helpers import make_lead, make_user
from corevatis_erpnext.tests.test_terminplanung import (
	ADRESSE_FIRMA,
	ADRESSE_FREMD,
	ADRESSE_HAMM,
	KOORDINATEN,
)
from corevatis_erpnext.tests.test_wochenplan import als, employee, naechster_montag, um

BUERO = "cv-pt-buero@example.com"
MEISTER1 = "cv-pt-meister1@example.com"
MEISTER2 = "cv-pt-meister2@example.com"
MONTEUR = "cv-pt-anton@example.com"
MONTEUR2 = "cv-pt-berta@example.com"
OHNE_ROLLE = "cv-pt-ohne@example.com"
F1 = "MS-PT 1"
F2 = "MS-PT 2"


def _nominatim(adresse: str):
	return KOORDINATEN.get(adresse)


def fahrzeug(kennzeichen: str, bezeichnung: str, aktiv: int = 1) -> str:
	if frappe.db.exists("Fahrzeug", kennzeichen):
		frappe.db.set_value("Fahrzeug", kennzeichen, {"aktiv": aktiv, "bezeichnung": bezeichnung})
		return kennzeichen
	return (
		frappe.get_doc(
			{"doctype": "Fahrzeug", "kennzeichen": kennzeichen, "bezeichnung": bezeichnung, "aktiv": aktiv}
		)
		.insert(ignore_permissions=True)
		.name
	)


class TestPlantafel(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		from corevatis_erpnext.planung.felder import ensure_planung_felder

		ensure_planung_felder()
		make_user(BUERO, "Bea", ["Büro", "Sales User", "Projects User"], "Plantafel")
		make_user(MEISTER1, "Martin", ["Meister", "Sales User", "Projects Manager"], "Plantafel")
		make_user(MEISTER2, "Moritz", ["Meister", "Sales User", "Projects Manager"], "Plantafel")
		make_user(MONTEUR, "Anton", ["Monteur"], "Plantafel")
		make_user(MONTEUR2, "Berta", ["Monteur"], "Plantafel")
		make_user(OHNE_ROLLE, "Ohne", ["Sales User"], "Plantafel")
		cls.e_meister1 = employee(MEISTER1, "Martin")
		cls.e_meister2 = employee(MEISTER2, "Moritz")
		cls.e_anton = employee(MONTEUR, "Anton")
		cls.e_berta = employee(MONTEUR2, "Berta")
		cls.f1 = fahrzeug(F1, "Sprinter")
		cls.f2 = fahrzeug(F2, "Anhänger", aktiv=0)
		cls.m = naechster_montag()
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.savepoint("plantafel_test")
		self.addCleanup(self._zuruecksetzen)
		patch.object(geokodierung, "abfragen", side_effect=_nominatim).start()
		patch.object(terminplanung, "firmen_adresse", return_value=ADRESSE_FIRMA).start()
		patch.object(terminplanung, "_feiertage", side_effect=self._feiertage).start()
		patch("frappe.utils.jinja_globals.bundled_asset", return_value="/assets/fehlt/email.css").start()
		self.addCleanup(patch.stopall)
		frappe.cache.delete_value(geokodierung.CACHE_SPERRE)
		frappe.get_single(plantafel.EINSTELLUNGEN).db_set("plantafel_reihenfolge", "", update_modified=False)
		m = self.m
		self.mo, self.di, self.mi, self.do, self.fr = (m + dt.timedelta(days=n) for n in range(5))
		self.lead = make_lead(
			last_name=f"Plantafel {frappe.generate_hash(length=6)}",
			cv_objekt_adresse="Kellerweg 7\n48143 Münster",
		)
		self.fremd = make_lead(
			first_name="Fremde", last_name="Kundin", cv_objekt_adresse=ADRESSE_FREMD, mobile_no="0251 999999"
		)
		hamm = make_lead(first_name="Baustelle", last_name="Hamm", cv_objekt_adresse=ADRESSE_HAMM)
		self.projekt = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"Plantafel Hamm {frappe.generate_hash(length=6)}",
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
		self.a3 = self.task(
			"Horizontalsperre - Keller",
			um(self.mi, "07:00"),
			um(self.mi, "11:00"),
			4,
			"Horizontalsperre Injektion",
		)
		self.a4 = self.task(
			"Allgemeine Leistungen - AL-01", um(self.fr, "07:00"), um(self.fr, "11:00"), 4, "Sonstiges"
		)
		self.a5 = self.task(
			"Außenabdichtung - Sockel", um(self.di, "10:05"), um(self.di, "12:00"), 2, "Außenabdichtung"
		)
		self.a6 = self.task("Nacharbeit", um(self.mo, "13:00"), um(self.mo, "16:00"), 3, "Innenabdichtung")
		self.a7 = self.task(
			"Außenabdichtung - Hof", um(self.mo, "08:00"), um(self.mo, "12:00"), 4, "Außenabdichtung"
		)
		# Termin vor der Zuweisung von A5: danach wäre die Fahrzeit nach Hamm ein Konflikt (gewollt eng)
		self.termin = als(
			BUERO,
			crm_termin.termin_anlegen,
			self.fremd.name,
			f"{self.di} 09:00:00",
			MEISTER1,
			60,
			bestaetigung=0,
		)["event"]
		for task, emp in (
			(self.a1, self.e_anton),
			(self.a2, self.e_anton),
			(self.a6, self.e_anton),
			(self.a3, self.e_berta),
			(self.a7, self.e_berta),
			(self.a5, self.e_meister1),
		):
			als(BUERO, zuweisung.zuweisen, task, emp)
		frappe.db.set_value("Task", self.a1, "cv_fahrzeug", self.f1)
		frappe.db.set_value("Task", self.a7, "cv_fahrzeug", self.f1)
		self.abw_berta = self.abwesenheit(self.e_berta, "Urlaub", self.mi, self.mi)
		self.abw_moritz = self.abwesenheit(
			self.e_meister2,
			"Schulung",
			self.fr,
			self.fr,
			ganztags=0,
			von_uhrzeit="12:00:00",
			bis_uhrzeit="15:00:00",
		)

	def _zuruecksetzen(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="plantafel_test")
		from frappe.model.document import clear_document_cache

		for doctype in ("Project", "Task", "ToDo", "Event", "Abwesenheit", "Lead"):
			clear_document_cache(doctype)

	def _feiertage(self, firma_, von, bis):
		return {self.do: "Testfeiertag"} if getdate(von) <= self.do <= getdate(bis) else {}

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

	def abwesenheit(self, emp, art, von, bis, **werte) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Abwesenheit",
					"mitarbeiter": emp,
					"art": art,
					"von": von,
					"bis": bis,
					"ganztags": 1,
					**werte,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def zeile(self, daten: dict, user: str) -> dict:
		return next(z for z in daten["zeilen"] if z["user"] == user)

	def eintrag(self, zeile: dict, doctype: str, name: str) -> dict:
		return next(e for e in zeile["eintraege"] if e["doctype"] == doctype and e["name"] == name)

	def kommentare(self, doctype: str, name: str) -> list[str]:
		return frappe.get_all(
			"Comment",
			filters={"reference_doctype": doctype, "reference_name": name, "comment_type": "Info"},
			pluck="content",
			order_by="creation asc",
		)

	# --- Aufbau ----------------------------------------------------------------------------------

	def test_tafel_woche_und_monat(self):
		d = als(BUERO, plantafel.tafel, str(self.mi), "woche", 0)
		self.assertEqual(
			(d["von"], d["bis"], d["ansicht"]), (str(self.mo), str(self.mo + dt.timedelta(days=6)), "woche")
		)
		self.assertEqual(
			[t["datum"] for t in d["tage"]], [str(self.mo + dt.timedelta(days=n)) for n in range(5)]
		)
		self.assertEqual(d["tage"][3]["feiertag"], "Testfeiertag")
		self.assertEqual(
			(d["arbeitsbeginn"], d["tagesarbeitszeit"], d["schreibgeschuetzt"]), ("07:00", 8.0, False)
		)
		self.assertTrue(d["darf_abwesenheit"])
		self.assertIn({"value": self.f1, "label": "Sprinter", "name": self.f1}, d["fahrzeuge"])
		self.assertNotIn(self.f2, [f["name"] for f in d["fahrzeuge"]])
		# Anton: A1 mit Fahrzeug, A6 überschneidet A1, A2 über zwei Tage auf die Arbeitszeit beschnitten
		anton = self.zeile(d, MONTEUR)
		a1 = self.eintrag(anton, "Task", self.a1)
		self.assertEqual(
			(a1["art"], a1["titel"], a1["fahrzeug"], a1["fahrzeug_name"], a1["verschiebbar"], a1["lesbar"]),
			("Arbeitspaket", "Innenabdichtung", self.f1, "Sprinter", True, True),
		)
		self.assertEqual(
			a1["segmente"],
			[
				{
					"datum": str(self.mo),
					"von": "07:00",
					"bis": "15:00",
					"von_min": 420,
					"bis_min": 900,
					"ganztags": False,
				}
			],
		)
		self.assertEqual(a1["projekt_name"], self.projekt.project_name)
		self.assertEqual(a1["ort"], ADRESSE_HAMM)  # Büro liest die Anfrage des Projekts: volle Anschrift
		self.assertTrue(any("Überschneidung" in k and self.a6 in k for k in a1["konflikte"]), a1["konflikte"])
		self.assertTrue(
			any("Fahrzeug Sprinter" in k and self.a7 in k for k in a1["konflikte"]), a1["konflikte"]
		)
		self.assertTrue(a1.get("fahrzeug_konflikt"))
		a2 = self.eintrag(anton, "Task", self.a2)
		self.assertEqual(
			[(s["datum"], s["von"], s["bis"]) for s in a2["segmente"]],
			[(str(self.di), "07:00", "15:00"), (str(self.mi), "07:00", "11:00")],
		)
		self.assertEqual(anton["last"][str(self.mo)], 11.0)
		self.assertEqual(anton["ueberlast"], [str(self.mo)])
		# Berta: Urlaub am Mittwoch, A3 kollidiert damit
		berta = self.zeile(d, MONTEUR2)
		self.assertEqual(berta["abwesend"], {str(self.mi): "Urlaub"})
		urlaub = self.eintrag(berta, "Abwesenheit", self.abw_berta)
		self.assertEqual(
			(urlaub["art"], urlaub["titel"], urlaub["verschiebbar"], urlaub["segmente"][0]["ganztags"]),
			("Abwesenheit", "Urlaub", False, True),
		)
		a3 = self.eintrag(berta, "Task", self.a3)
		self.assertTrue(
			any("Abwesenheit Urlaub" in k and "ganztags" in k for k in a3["konflikte"]), a3["konflikte"]
		)
		# Martin: Termin mit Kunde (lesbar für Büro), danach zu wenig Zeit für die Fahrt nach Hamm
		martin = self.zeile(d, MEISTER1)
		termin = self.eintrag(martin, "Event", self.termin)
		self.assertEqual(
			(termin["art"], termin["zeit"], termin["ort"], termin["lesbar"], termin["verschiebbar"]),
			("Termin", "09:00\u201310:00", ADRESSE_FREMD, True, True),
		)
		self.assertIn("Besichtigung", termin["titel"])
		self.assertIn("Fremde", termin["titel"])
		a5 = self.eintrag(martin, "Task", self.a5)
		self.assertTrue(any("Fahrt 60 min" in h and "Nur 5 min" in h for h in a5["hinweise"]), a5["hinweise"])
		self.assertEqual(a5["konflikte"], [])
		# Moritz: Schulung mit Uhrzeiten (nicht ganztags)
		moritz = self.zeile(d, MEISTER2)
		schulung = self.eintrag(moritz, "Abwesenheit", self.abw_moritz)
		self.assertEqual(
			schulung["segmente"][0],
			{
				"datum": str(self.fr),
				"von": "12:00",
				"bis": "15:00",
				"von_min": 720,
				"bis_min": 900,
				"ganztags": False,
			},
		)
		self.assertEqual(moritz["abwesend"], {})
		# Nicht zugewiesen: A4
		self.assertEqual(
			[
				e["name"]
				for e in d["nicht_zugewiesen"]
				if e["doctype"] == "Task"
				and e["name"] in (self.a1, self.a2, self.a3, self.a4, self.a5, self.a6, self.a7)
			],
			[self.a4],
		)
		z = d["zusammenfassung"]
		self.assertGreaterEqual(z["einsaetze"], 8)
		self.assertGreaterEqual(z["konflikte"], 4)
		self.assertEqual(z["fahrzeug_konflikte"], 1)
		self.assertGreaterEqual(z["fahrzeit_hinweise"], 1)
		self.assertGreaterEqual(z["abwesenheiten"], 2)
		# Reihenfolge ohne Einstellung: alphabetisch
		namen = [zeile["name"] for zeile in d["zeilen"]]
		self.assertEqual(namen, sorted(namen, key=str.lower))
		# Wochenende einblenden: sieben Tage
		d7 = als(BUERO, plantafel.tafel, str(self.mo), "woche", 1)
		self.assertEqual(len(d7["tage"]), 7)
		self.assertTrue(d7["tage"][5]["wochenende"])
		# Monat: alle Tage des Monats
		dm = als(MEISTER1, plantafel.tafel, str(self.mo), "monat", 1)
		erster = self.mo.replace(day=1)
		letzter = (erster + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
		self.assertEqual((dm["von"], dm["bis"], len(dm["tage"])), (str(erster), str(letzter), letzter.day))
		self.assertFalse(dm["schreibgeschuetzt"])  # Meister planen

	def test_segmente_und_zeitraum(self):
		tage = {self.mo, self.di, self.mi}
		s = plantafel.segmente(um(self.mo, "22:00"), um(self.di, "02:00"), tage)
		self.assertEqual(
			[(x["datum"], x["von"], x["bis"], x["von_min"], x["bis_min"]) for x in s],
			[(str(self.mo), "22:00", "24:00", 1320, 1440), (str(self.di), "00:00", "02:00", 0, 120)],
		)
		s = plantafel.segmente(um(self.mo), um(self.mi), tage)
		self.assertEqual(
			[(x["datum"], x["ganztags"]) for x in s], [(str(self.mo), True), (str(self.di), True)]
		)
		# außerhalb des Zeitraums nichts; Arbeitszeit-Beschnitt nur innerhalb
		self.assertEqual(plantafel.segmente(um(self.fr, "07:00"), um(self.fr, "08:00"), tage), [])
		s = plantafel.segmente(um(self.mo, "18:00"), um(self.mo, "20:00"), tage, (420, 900))
		self.assertEqual((s[0]["von"], s[0]["bis"]), ("18:00", "20:00"))
		self.assertEqual(
			plantafel.zeitraum("2030-03-06", "woche"), (dt.date(2030, 3, 4), dt.date(2030, 3, 10))
		)
		self.assertEqual(
			plantafel.zeitraum("2030-02-10", "monat"), (dt.date(2030, 2, 1), dt.date(2030, 2, 28))
		)

	# --- Verschieben -----------------------------------------------------------------------------

	def test_arbeitspaket_verschieben(self):
		# A4 (nicht zugewiesen, Fr) auf Montag zu Anton: Zeitraum und Zuweisung
		r = als(BUERO, plantafel.arbeitspaket_verschieben, self.a4, str(self.mo), self.e_anton)
		self.assertEqual((r["start"], r["ende"]), (f"{self.mo} 07:00:00", f"{self.mo} 11:00:00"))
		self.assertEqual([z["employee"] for z in r["zugewiesen"]], [self.e_anton])
		self.assertTrue(any("Tageslast" in h for h in r["hinweise"]), r["hinweise"])  # Mo: 11 h + 4 h
		t = frappe.db.get_value("Task", self.a4, ["exp_start_date", "exp_end_date"], as_dict=True)
		self.assertEqual((t.exp_start_date, t.exp_end_date), (um(self.mo, "07:00"), um(self.mo, "11:00")))
		kommentare = self.kommentare("Task", self.a4)
		self.assertTrue(any("Verschoben von" in k for k in kommentare), kommentare)
		self.assertTrue(any("Umgeplant von" in k for k in kommentare), kommentare)
		# A1 (Anton, Mo) zu Berta auf Mittwoch: Berta ist im Urlaub → Konflikt, nichts geändert
		with self.assertRaises(plantafel.TerminKonflikt) as cm:
			als(BUERO, plantafel.arbeitspaket_verschieben, self.a1, str(self.mi), self.e_berta, self.e_anton)
		self.assertIn("abwesend (Urlaub)", str(cm.exception))
		self.assertEqual(cm.exception.konflikte[0]["art"], "Abwesenheit")
		self.assertEqual(frappe.local.response.get("konflikte"), cm.exception.konflikte)
		self.assertEqual(getdate(frappe.db.get_value("Task", self.a1, "exp_start_date")), self.mo)
		self.assertIn(MONTEUR, frappe.parse_json(frappe.db.get_value("Task", self.a1, "_assign") or "[]"))
		# trotzdem: verschoben, Berta zugewiesen, Anton nicht mehr, Vermerk
		r = als(
			BUERO,
			plantafel.arbeitspaket_verschieben,
			self.a1,
			str(self.mi),
			self.e_berta,
			self.e_anton,
			trotzdem=1,
		)
		self.assertEqual((r["start"], r["ende"]), (f"{self.mi} 07:00:00", f"{self.mi} 15:00:00"))
		self.assertEqual([z["employee"] for z in r["zugewiesen"] if z["status"] == "Open"], [self.e_berta])
		self.assertTrue(any("abwesend (Urlaub)" in h for h in r["hinweise"]), r["hinweise"])
		self.assertTrue(any("Trotz Überschneidung" in k for k in self.kommentare("Task", self.a1)))
		# Feiertag am Zieltag
		with self.assertRaises(plantafel.TerminKonflikt) as cm:
			als(MEISTER1, plantafel.arbeitspaket_verschieben, self.a6, str(self.do))
		self.assertIn("Testfeiertag", str(cm.exception))
		# Termin des Meisters im Weg: A4 (Mo 07-11) zu Martin auf Dienstag (Termin 09-10)
		with self.assertRaises(plantafel.TerminKonflikt) as cm:
			als(
				BUERO,
				plantafel.arbeitspaket_verschieben,
				self.a4,
				str(self.di),
				self.e_meister1,
				self.e_anton,
			)
		self.assertIn("09:00\u201310:00", str(cm.exception))
		# in „Nicht zugewiesen“ gezogen: Zuweisung entfernt, Tag gleich
		r = als(
			BUERO, plantafel.arbeitspaket_verschieben, self.a2, str(self.di), None, self.e_anton, freigeben=1
		)
		self.assertEqual([z for z in r["zugewiesen"] if z["status"] == "Open"], [])
		self.assertTrue(any("Zuweisung entfernt" in k for k in self.kommentare("Task", self.a2)))
		# keine Änderung
		r = als(BUERO, plantafel.arbeitspaket_verschieben, self.a7, str(self.mo), self.e_berta, self.e_berta)
		self.assertTrue(any("Keine Änderung" in h for h in r["hinweise"]))
		# Projektende wird verschoben, wenn das Paket dahinter liegt
		spaet = self.mo + dt.timedelta(days=21)
		r = als(BUERO, plantafel.arbeitspaket_verschieben, self.a7, str(spaet))
		self.assertTrue(any("Projektende" in h for h in r["hinweise"]), r["hinweise"])
		self.assertEqual(
			getdate(frappe.db.get_value("Project", self.projekt.name, "expected_end_date")), spaet
		)
		# erledigt: nicht mehr verschiebbar; ohne Beginn: Meldung
		frappe.db.set_value("Task", self.a6, "status", "Completed")
		with self.assertRaises(PlanungFehler):
			als(BUERO, plantafel.arbeitspaket_verschieben, self.a6, str(self.fr))
		frappe.db.set_value("Task", self.a7, {"exp_start_date": None, "exp_end_date": None})
		with self.assertRaises(PlanungFehler):
			als(BUERO, plantafel.arbeitspaket_verschieben, self.a7, str(self.fr))

	def test_termin_verschieben(self):
		# Martins Arbeitspaket A5 (Di 10:05-12:00) wegnehmen, sonst ist 10:30 ein echter Konflikt (geprüft
		# in test_arbeitspaket_verschieben); hier geht es um Termin gegen Termin, Abwesenheit und Meisterwechsel
		als(BUERO, zuweisung.zuweisung_entfernen, self.a5, self.e_meister1)
		frappe.set_user(BUERO)
		# gleicher Meister, neue Uhrzeit: Dauer bleibt, ToDo-Fälligkeit und Kommentar, Bestätigung neu
		r = crm_termin.termin_verschieben(self.termin, f"{self.di} 10:30:00")
		self.assertEqual(
			(r["beginn"], r["ende"], r["meister"], r["konflikte"]),
			(f"{self.di} 10:30:00", f"{self.di} 11:30:00", MEISTER1, []),
		)
		ev = frappe.get_doc("Event", self.termin)
		self.assertEqual((ev.starts_on, ev.ends_on), (um(self.di, "10:30"), um(self.di, "11:30")))
		self.assertIn("10:30", r["bestaetigung"]["text"])
		self.assertEqual(r["bestaetigung"]["werte"]["meister"], get_fullname(MEISTER1))
		self.assertTrue(any("Terminbestätigung" in h for h in r["hinweise"]), r["hinweise"])
		todo = frappe.get_all(
			"ToDo",
			filters={"reference_type": "Event", "reference_name": self.termin, "status": "Open"},
			fields=["allocated_to", "date"],
		)
		self.assertEqual([(t.allocated_to, getdate(t.date)) for t in todo], [(MEISTER1, self.di)])
		self.assertTrue(
			any("Verschoben von" in k and "10:30" in k for k in self.kommentare("Event", self.termin))
		)
		# Email Queue bleibt leer: nichts automatisch gesendet
		self.assertFalse(
			frappe.db.exists("Email Queue", {"reference_doctype": "Lead", "reference_name": self.fremd.name})
		)
		# zweiter Termin für Moritz Di 09:00 (andere Anfrage); Martins Termin dorthin → Konflikt
		zweiter = crm_termin.termin_anlegen(
			self.lead.name, f"{self.di} 09:00:00", MEISTER2, 60, bestaetigung=0
		)["event"]
		with self.assertRaises(crm_termin.TerminKonflikt) as cm:
			crm_termin.termin_verschieben(self.termin, f"{self.di} 09:30:00", MEISTER2)
		self.assertIn(get_fullname(MEISTER2), str(cm.exception))
		self.assertIn("09:00\u201310:00", str(cm.exception))
		self.assertEqual(frappe.db.get_value("Event", self.termin, "starts_on"), um(self.di, "10:30"))
		# trotzdem: Meister gewechselt, Zuweisung und Freigabe nachgezogen, Vermerk
		r = crm_termin.termin_verschieben(self.termin, f"{self.di} 09:30:00", MEISTER2, trotzdem=1)
		self.assertEqual((r["meister"], r["alter_meister"], len(r["konflikte"])), (MEISTER2, MEISTER1, 1))
		offen = frappe.get_all(
			"ToDo",
			filters={"reference_type": "Event", "reference_name": self.termin, "status": "Open"},
			pluck="allocated_to",
		)
		self.assertEqual(offen, [MEISTER2])
		self.assertTrue(
			frappe.db.exists(
				"ToDo",
				{
					"reference_type": "Event",
					"reference_name": self.termin,
					"allocated_to": MEISTER1,
					"status": "Cancelled",
				},
			)
		)
		self.assertTrue(frappe.has_permission("Event", "write", doc=self.termin, user=MEISTER2))
		self.assertFalse(frappe.has_permission("Event", "read", doc=self.termin, user=MEISTER1))
		self.assertTrue(frappe.has_permission("Event", "read", doc=self.termin, user=BUERO))
		kommentare = self.kommentare("Event", self.termin)
		self.assertTrue(any("Meister:" in k and get_fullname(MEISTER2) in k for k in kommentare), kommentare)
		self.assertTrue(any("Trotz Überschneidung verschoben" in k for k in kommentare), kommentare)
		# Abwesenheit mit Uhrzeiten (Moritz Fr 12-15): 13:00 geht nicht, 09:00 geht
		with self.assertRaises(crm_termin.TerminKonflikt) as cm:
			crm_termin.termin_verschieben(self.termin, f"{self.fr} 13:00:00")
		self.assertIn("abwesend (Schulung)", str(cm.exception))
		r = crm_termin.termin_verschieben(self.termin, f"{self.fr} 09:00:00")
		self.assertEqual(r["konflikte"], [])
		# unverändert → Hinweis, kein neuer Kommentar
		anzahl = len(self.kommentare("Event", self.termin))
		r = crm_termin.termin_verschieben(self.termin, f"{self.fr} 09:00:00", MEISTER2)
		self.assertTrue(any("Keine Änderung" in h for h in r["hinweise"]))
		self.assertEqual(len(self.kommentare("Event", self.termin)), anzahl)
		# abgesagter Termin, ungültiger Meister
		frappe.db.set_value("Event", zweiter, "status", "Cancelled")
		with self.assertRaises(frappe.ValidationError):
			crm_termin.termin_verschieben(zweiter, f"{self.fr} 09:00:00")
		with self.assertRaises(frappe.ValidationError):
			crm_termin.termin_verschieben(self.termin, f"{self.fr} 09:00:00", MONTEUR)
		# Monteur darf nicht
		frappe.set_user(MONTEUR)
		with self.assertRaises(frappe.PermissionError):
			crm_termin.termin_verschieben(self.termin, f"{self.fr} 10:00:00")

	# --- Fahrzeuge -------------------------------------------------------------------------------

	def test_fahrzeug_zuordnen(self):
		# A3 (Mi 07-11) bekommt den Sprinter: frei (A1/A7 sind am Montag)
		r = als(BUERO, plantafel.fahrzeug_zuordnen, "Task", self.a3, self.f1)
		self.assertEqual((r["fahrzeug"], r["fahrzeug_name"], r["konflikte"]), (self.f1, "Sprinter", []))
		self.assertEqual(frappe.db.get_value("Task", self.a3, "cv_fahrzeug"), self.f1)
		self.assertTrue(any("Fahrzeug Sprinter zugeordnet" in k for k in self.kommentare("Task", self.a3)))
		# A2 (Di 07 - Mi 11) überschneidet A3 → Konflikt; trotzdem → zugeordnet mit Vermerk
		with self.assertRaises(plantafel.TerminKonflikt) as cm:
			als(BUERO, plantafel.fahrzeug_zuordnen, "Task", self.a2, self.f1)
		self.assertIn("Fahrzeug Sprinter ist", str(cm.exception))
		self.assertIn(self.a3, str(cm.exception))
		self.assertIsNone(frappe.db.get_value("Task", self.a2, "cv_fahrzeug"))
		r = als(MEISTER1, plantafel.fahrzeug_zuordnen, "Task", self.a2, self.f1, trotzdem=1)
		self.assertEqual(len(r["konflikte"]), 1)
		self.assertTrue(any("trotz Überschneidung" in k for k in self.kommentare("Task", self.a2)))
		# Termin (Di 09-10) mit demselben Fahrzeug → Konflikt mit A2
		with self.assertRaises(plantafel.TerminKonflikt) as cm:
			als(BUERO, plantafel.fahrzeug_zuordnen, "Event", self.termin, self.f1)
		self.assertIn(self.a2, str(cm.exception))
		# Termin am Donnerstag ohne Überschneidung: frei
		frappe.db.set_value(
			"Event", self.termin, {"starts_on": um(self.fr, "09:00"), "ends_on": um(self.fr, "10:00")}
		)
		r = als(BUERO, plantafel.fahrzeug_zuordnen, "Event", self.termin, self.f1)
		self.assertEqual(
			(r["konflikte"], frappe.db.get_value("Event", self.termin, "cv_fahrzeug")), ([], self.f1)
		)
		# entfernen
		r = als(BUERO, plantafel.fahrzeug_zuordnen, "Event", self.termin, "")
		self.assertIsNone(frappe.db.get_value("Event", self.termin, "cv_fahrzeug"))
		self.assertTrue(any("entfernt" in k for k in self.kommentare("Event", self.termin)))
		# inaktiv, unbekannt, falscher Doctype
		with self.assertRaises(PlanungFehler):
			als(BUERO, plantafel.fahrzeug_zuordnen, "Task", self.a4, self.f2)
		with self.assertRaises(frappe.DoesNotExistError):
			als(BUERO, plantafel.fahrzeug_zuordnen, "Task", self.a4, "GIBTS-NICHT")
		with self.assertRaises(frappe.ValidationError):
			als(BUERO, plantafel.fahrzeug_zuordnen, "Project", self.projekt.name, self.f1)
		# Fahrzeugkonflikt in der Tafel: A2 und A3 am Mittwoch
		d = als(BUERO, plantafel.tafel, str(self.mo))
		a3 = self.eintrag(self.zeile(d, MONTEUR2), "Task", self.a3)
		self.assertTrue(
			any("Fahrzeug Sprinter" in k and self.a2 in k for k in a3["konflikte"]), a3["konflikte"]
		)

	# --- Rechte ----------------------------------------------------------------------------------

	def test_rechte_monteur_und_kundendaten(self):
		# Monteur: nur die eigene Zeile, schreibgeschützt, nichts zum Verschieben
		d = als(MONTEUR, plantafel.tafel, str(self.mo))
		self.assertEqual([z["user"] for z in d["zeilen"]], [MONTEUR])
		self.assertTrue(d["zeilen"][0]["eigene"])
		self.assertEqual(
			(d["schreibgeschuetzt"], d["nicht_zugewiesen"], d["darf_abwesenheit"]), (True, [], False)
		)
		self.assertEqual({e["name"] for e in d["zeilen"][0]["eintraege"]}, {self.a1, self.a2, self.a6})
		for funktion, args, kwargs in (
			(plantafel.arbeitspaket_verschieben, (self.a1, str(self.di)), {}),
			(plantafel.fahrzeug_zuordnen, ("Task", self.a1, self.f1), {}),
			(plantafel.reihenfolge_speichern, ([self.e_anton],), {}),
			(plantafel.drucken, (str(self.mo),), {}),
			(crm_termin.termin_verschieben, (self.termin, f"{self.di} 11:00:00"), {}),
		):
			with self.assertRaises(frappe.PermissionError, msg=funktion.__name__):
				als(MONTEUR, funktion, *args, **kwargs)
		self.assertEqual(getdate(frappe.db.get_value("Task", self.a1, "exp_start_date")), self.mo)
		# ohne Fachrolle und ohne Mitarbeiter: keine Tafel
		with self.assertRaises(frappe.PermissionError):
			als(OHNE_ROLLE, plantafel.tafel, str(self.mo))
		# Abwesenheiten: Monteur sieht nur die eigenen (Liste und Formular), legt keine an
		eigene = self.abwesenheit(self.e_anton, "Krank", self.fr, self.fr)
		frappe.set_user(MONTEUR)
		try:
			liste = frappe.get_list("Abwesenheit", fields=["name", "benutzer"], limit_page_length=0)
			self.assertEqual({a.name for a in liste}, {eigene})
			self.assertTrue(frappe.has_permission("Abwesenheit", "read", doc=eigene))
			self.assertFalse(frappe.has_permission("Abwesenheit", "read", doc=self.abw_berta))
			self.assertFalse(frappe.has_permission("Abwesenheit", "create"))
			self.assertTrue(frappe.has_permission("Fahrzeug", "read"))
			self.assertFalse(frappe.has_permission("Fahrzeug", "create"))
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(frappe.has_permission("Abwesenheit", "read", doc=self.abw_berta, user=BUERO))
		self.assertTrue(frappe.has_permission("Abwesenheit", "create", user=BUERO))
		self.assertEqual(abwesenheit.permission_query_conditions(BUERO), "")
		self.assertIn("benutzer", abwesenheit.permission_query_conditions(MONTEUR))
		# Meister sieht alle Abwesenheiten des Betriebs (Liste und Formular), aber Art und Notiz nur beim Büro
		# (Feldrecht Stufe 1, Krank ist ein Gesundheitsdatum); User Permission auf Employee filtert nicht
		frappe.set_user(MEISTER2)
		try:
			zeilen = frappe.get_list(
				"Abwesenheit", fields=["name", "mitarbeiter", "art", "notiz"], limit_page_length=0
			)
			self.assertTrue({eigene, self.abw_berta} <= {z.name for z in zeilen})
			self.assertTrue(all("art" not in z and "notiz" not in z for z in zeilen), zeilen)
			self.assertTrue(frappe.has_permission("Abwesenheit", "read", doc=self.abw_berta))
			self.assertFalse(frappe.has_permission("Abwesenheit", "write", doc=self.abw_berta))
			self.assertFalse(frappe.has_permission("Abwesenheit", "create"))
			fremd = frappe.get_doc("Abwesenheit", self.abw_berta)
			fremd.apply_fieldlevel_read_permissions()
			self.assertIsNone(fremd.get("art"))
			# Filtern, Sortieren oder Gruppieren nach Art/Notiz verrät, wer krank ist: nur mit Stufe-1-Recht
			for anfrage in (
				{"filters": json.dumps({"art": "Krank"})},
				{"filters": json.dumps([["Abwesenheit", "art", "=", "Krank"]])},
				{"or_filters": [["notiz", "like", "%Grippe%"]]},
				{"order_by": "`tabAbwesenheit`.`art` asc"},
				{"group_by": "art"},
				{"field": "art", "current_filters": "[]"},
				{"aggregate_on_field": "art"},
			):
				frappe.local.form_dict = frappe._dict(doctype="Abwesenheit", **anfrage)
				try:
					with self.assertRaises(frappe.PermissionError, msg=anfrage):
						frappe.get_list("Abwesenheit", fields=["name"])
				finally:
					frappe.local.form_dict = frappe._dict()
			frappe.local.form_dict = frappe._dict(
				doctype="Abwesenheit", filters=json.dumps({"mitarbeiter": self.e_berta}), order_by="von desc"
			)
			try:
				self.assertTrue(frappe.get_list("Abwesenheit", fields=["name"]))
			finally:
				frappe.local.form_dict = frappe._dict()
		finally:
			frappe.set_user("Administrator")
		# Rahmen des Meisters ist seine Firma, nicht „alle“
		bedingung = abwesenheit.permission_query_conditions(MEISTER2)
		self.assertIn("`tabEmployee`", bedingung)
		self.assertIn(firma(), bedingung)
		# Büro darf nach Art filtern
		frappe.local.form_dict = frappe._dict(doctype="Abwesenheit", filters=json.dumps({"art": "Urlaub"}))
		try:
			abwesenheit.geschuetzte_felder_pruefen(BUERO)
		finally:
			frappe.local.form_dict = frappe._dict()
		frappe.set_user(BUERO)
		try:
			zeilen = frappe.get_list("Abwesenheit", fields=["name", "art"], filters={"name": self.abw_berta})
			self.assertEqual([z.art for z in zeilen], ["Urlaub"])
		finally:
			frappe.set_user("Administrator")
		# Meister 2 sieht Martins Termin ohne Kundendaten: „Besichtigung“ und nur PLZ/Ort, nicht öffnen
		d = als(MEISTER2, plantafel.tafel, str(self.mo))
		termin = self.eintrag(self.zeile(d, MEISTER1), "Event", self.termin)
		self.assertEqual(
			(termin["titel"], termin["ort"], termin["lesbar"], termin["verschiebbar"], termin.get("lead")),
			("Besichtigung", "48155 Münster", False, False, None),
		)
		dump = frappe.as_json(d)
		for geheim in ("Fremde", "Kundin", "0251 999999", self.fremd.name):
			self.assertNotIn(geheim, dump)
		# Art der Abwesenheit anderer (Krank = Gesundheitsdatum) nur für Büro: Meister sieht „Abwesend“
		urlaub = self.eintrag(self.zeile(d, MONTEUR2), "Abwesenheit", self.abw_berta)
		self.assertEqual(
			(urlaub["titel"], urlaub["abwesenheit_art"], urlaub["lesbar"]), ("Abwesend", None, False)
		)
		self.assertEqual(self.zeile(d, MONTEUR2)["abwesend"], {str(self.mi): "Abwesend"})
		self.assertNotIn("Urlaub", dump)
		# der Betroffene selbst sieht seine Art
		d = als(MONTEUR, plantafel.tafel, str(self.mo))
		krank = self.eintrag(d["zeilen"][0], "Abwesenheit", eigene)
		self.assertEqual((krank["titel"], krank["lesbar"]), ("Krank", True))
		# Monteur ohne Leserecht an der Anfrage: Projektort als PLZ/Ort
		a1 = self.eintrag(d["zeilen"][0], "Task", self.a1)
		self.assertEqual(a1["ort"], "59067 Hamm")

	# --- Reihenfolge, Filter ----------------------------------------------------------------------

	def test_reihenfolge_und_filter(self):
		r = als(BUERO, plantafel.reihenfolge_speichern, [self.e_berta, self.e_anton])
		self.assertEqual(r[:2], [self.e_berta, self.e_anton])
		d = als(BUERO, plantafel.tafel, str(self.mo))
		users = [z["user"] for z in d["zeilen"]]
		self.assertEqual(users[:2], [MONTEUR2, MONTEUR])
		self.assertEqual(d["reihenfolge"][:2], [self.e_berta, self.e_anton])
		with self.assertRaises(frappe.ValidationError):
			als(BUERO, plantafel.reihenfolge_speichern, ["HR-EMP-GIBTSNICHT"])
		# Filter Mitarbeiter: eine Zeile, keine offenen; Filter Projekt: nur dessen Pakete
		d = als(BUERO, plantafel.tafel, str(self.mo), "woche", 0, None, self.e_anton)
		self.assertEqual(([z["user"] for z in d["zeilen"]], d["nicht_zugewiesen"]), ([MONTEUR], []))
		anderes = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"Anderes {frappe.generate_hash(length=6)}",
				"company": firma(),
			}
		).insert(ignore_permissions=True)
		fremd = frappe.get_doc(
			{
				"doctype": "Task",
				"subject": "Fremd",
				"project": anderes.name,
				"exp_start_date": um(self.fr, "07:00"),
				"exp_end_date": um(self.fr, "09:00"),
				"expected_time": 2,
			}
		).insert(ignore_permissions=True)
		als(BUERO, zuweisung.zuweisen, fremd.name, self.e_anton)
		d = als(BUERO, plantafel.tafel, str(self.mo), "woche", 0, self.projekt.name)
		anton = self.zeile(d, MONTEUR)
		self.assertNotIn(fremd.name, [e["name"] for e in anton["eintraege"]])
		self.assertEqual(anton["last"][str(self.fr)], 2.0)  # Kapazität über alle Projekte
		self.assertEqual([e["name"] for e in d["nicht_zugewiesen"]], [self.a4])

	# --- Zuweisung und Wochenplan kennen Abwesenheiten --------------------------------------------

	def test_zuweisung_und_wochenplan_mit_abwesenheit(self):
		r = als(BUERO, zuweisung.zuweisen, self.a4, self.e_berta)  # Freitag: frei
		self.assertFalse([h for h in r["hinweise"] if "abwesend" in h], r["hinweise"])
		frappe.db.set_value(
			"Task", self.a4, {"exp_start_date": um(self.mi, "11:00"), "exp_end_date": um(self.mi, "15:00")}
		)
		als(BUERO, zuweisung.zuweisung_entfernen, self.a4, self.e_berta)
		r = als(BUERO, zuweisung.zuweisen, self.a4, self.e_berta)
		self.assertTrue(
			any("abwesend (Urlaub)" in h and f"Mi {self.mi:%d.%m.}" in h for h in r["hinweise"]),
			r["hinweise"],
		)
		with patch.object(wochenplan, "freie_tage", lambda von: set()):
			plan = als(BUERO, wochenplan.wochenplan, str(self.mo))
		berta = next(z for z in plan["zeilen"] if z["zeile"] == "M:" + MONTEUR2)
		self.assertEqual(
			(berta["abwesend"], berta["abwesenheit"]),
			([wochenplan.spalte(self.mi)], {wochenplan.spalte(self.mi): "Urlaub"}),
		)
		self.assertEqual(next(z for z in plan["zeilen"] if z["zeile"] == "M:" + MONTEUR)["abwesend"], [])
		self.assertFalse(abwesenheit.hrms_installiert())

	def test_abwesenheit_und_fahrzeug_pruefung(self):
		doc = frappe.get_doc("Abwesenheit", self.abw_berta)
		self.assertEqual(
			(doc.benutzer, doc.mitarbeiter_name),
			(MONTEUR2, frappe.db.get_value("Employee", self.e_berta, "employee_name")),
		)
		self.assertTrue(doc.name.startswith("ABW-"))
		with self.assertRaises(frappe.ValidationError):
			self.abwesenheit(self.e_berta, "Urlaub", self.fr, self.mo)
		with self.assertRaises(frappe.MandatoryError):
			self.abwesenheit(self.e_berta, "Krank", self.fr, self.fr, ganztags=0)
		with self.assertRaises(frappe.ValidationError):
			self.abwesenheit(
				self.e_berta,
				"Krank",
				self.mo,
				self.di,
				ganztags=0,
				von_uhrzeit="08:00:00",
				bis_uhrzeit="10:00:00",
			)
		with self.assertRaises(frappe.ValidationError):
			self.abwesenheit(
				self.e_berta,
				"Krank",
				self.mo,
				self.mo,
				ganztags=0,
				von_uhrzeit="10:00:00",
				bis_uhrzeit="08:00:00",
			)
		with self.assertRaises(frappe.ValidationError):
			self.abwesenheit(self.e_berta, "Frei", self.mo, self.mo)
		f = frappe.get_doc(
			{"doctype": "Fahrzeug", "kennzeichen": " ms-pt  3 ", "bezeichnung": "Pritsche"}
		).insert(ignore_permissions=True)
		self.assertEqual((f.name, f.aktiv), ("MS-PT 3", 1))

	# --- Druck -----------------------------------------------------------------------------------

	def test_druck(self):
		frappe.set_user(MEISTER2)
		daten = plantafel.tafel(str(self.mo), "woche", 0)
		html = plantafel.druck_html(daten)
		frappe.set_user("Administrator")
		for text in (
			"Wochenplan",
			frappe.db.get_value("Employee", self.e_anton, "employee_name"),
			"Testfeiertag",
			"Abwesend",  # Meister 2 sieht die Art fremder Abwesenheiten nicht
			"Innenabdichtung",
			"Sprinter",
			"Nicht zugewiesen",
			"Besichtigung",
			"48155 Münster",  # fremder Termin: nur PLZ/Ort
			"Mo " + self.mo.strftime("%d.%m."),
		):
			self.assertIn(text, html, text)
		for geheim in ("Fremde", "Kundin", "0251 999999", "Urlaub"):
			self.assertNotIn(geheim, html)
		self.assertIn("Schulung", html)  # die eigene Abwesenheit von Meister 2 mit Art
		self.assertIn("landscape", html)
		# Büro druckt mit Art
		self.assertIn("Urlaub", plantafel.druck_html(als(BUERO, plantafel.tafel, str(self.mo), "woche", 0)))
		# PDF (wkhtmltopdf der Test-Bench)
		if not shutil.which("wkhtmltopdf"):
			self.skipTest("wkhtmltopdf fehlt")
		frappe.set_user(BUERO)
		try:
			plantafel.drucken(str(self.mo), 0)
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(frappe.local.response.type, "pdf")
		self.assertEqual(frappe.local.response.filename, f"Wochenplan_{self.mo}.pdf")
		self.assertTrue(frappe.local.response.filecontent[:4] == b"%PDF")
		frappe.local.response.pop("filecontent", None)
		frappe.local.response.pop("type", None)
		frappe.local.response.pop("filename", None)

	# --- Felder, Seite, Leiste, Übersetzungen ----------------------------------------------------

	def test_felder_seite_leiste_uebersetzungen(self):
		seite = frappe.get_doc("Page", "plantafel")
		self.assertEqual(
			(seite.title, {r.role for r in seite.roles}),
			("Plantafel", {"Büro", "Meister", "Monteur", "System Manager"}),
		)
		sidebar = frappe.get_doc("Sidebar", "Betrieb")
		links = {(i.link_type, i.link_to) for i in sidebar.items if i.type == "Link"}
		self.assertIn(("Page", "plantafel"), links)
		self.assertIn(("DocType", "Abwesenheit"), links)
		self.assertIn(("DocType", "Fahrzeug"), links)
		self.assertNotIn(("Report", "Wochenplan"), links)
		self.assertEqual(next(i.label for i in sidebar.items if i.link_to == "plantafel"), "Plantafel")
		for doctype in ("Task", "Event"):
			df = frappe.get_meta(doctype).get_field("cv_fahrzeug")
			self.assertEqual((df.fieldtype, df.options), ("Link", "Fahrzeug"), doctype)
		self.assertEqual(
			frappe.get_meta(plantafel.EINSTELLUNGEN).get_field("plantafel_reihenfolge").fieldtype,
			"Small Text",
		)
		hooks = frappe.get_hooks(app_name="corevatis_erpnext")
		self.assertEqual(
			hooks["permission_query_conditions"]["Abwesenheit"],
			["corevatis_erpnext.planung.abwesenheit.permission_query_conditions"],
		)
		self.assertEqual(
			hooks["has_permission"]["Abwesenheit"], ["corevatis_erpnext.planung.abwesenheit.has_permission"]
		)
		from frappe.translate import get_translations_from_csv

		de = get_translations_from_csv("de", "corevatis_erpnext")
		for doctype in ("Abwesenheit", "Fahrzeug"):
			for df in frappe.get_meta(doctype).fields:
				if df.label:
					self.assertIn(df.label, de, f"{doctype}: {df.label}")
		for label in ("Vehicle", "Planning Board", "Row Order of Employees"):
			self.assertIn(label, de)
		# Report Wochenplan bleibt als Datenquelle (leitet im Desk zur Plantafel um)
		self.assertTrue(frappe.db.exists("Report", "Wochenplan"))
