"""Terminplanung der Besichtigung (Durchstich D14): Wunschtermin, Verfügbarkeit, Fahrzeit, Vorschläge,
Doppelbuchungsschutz.

1. ``TestSlotrechner``: Rechenregeln ohne Frappe (``planung.slotrechner``) - Luftlinie, Fahrzeit,
   Zeitfenster, freie Slots, Bewertung.
2. ``TestTerminplanung`` gegen echtes ERPNext v16: Geokodierung mit Zwischenspeicher (Nominatim ist
   gemockt - die Test-Bench hat kein Netz), Vorschläge für zwei Meister mit Termin, Arbeitspaket und
   Feiertag, Konflikte beim Anlegen, Rechte, Prozessleiste, Einstellungen und Felder.

Sollwerte (Umwegfaktor 1,3; 45 km/h; Mindestfahrzeit 10; Puffer 10; Standard 30; Raster 30;
Arbeitszeit 07:00 + 8 h): Firma -> Kellerweg 0,2 km = 10 min; Mühlenweg -> Kellerweg etwa 3,2 km =
10 min; Hamm -> Kellerweg etwa 34 km -> 34 * 1,3 / 45 * 60 = 59 -> 60 min.

Testwoche ab dem nächsten Montag M: Di 09:00-10:00 Termin von Meister 1 am Mühlenweg, Mi 07:00-15:00
Arbeitspaket von Meister 1 in Hamm, Do Feiertag (gemockt). Wunsch der Anfrage: Di vormittags, Mi ganztags.
Jeder Test räumt per Rollback auf, damit die Belegungen der Meister nicht zwischen den Tests wandern.
"""

from __future__ import annotations

import datetime as dt
import json
from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_fullname, getdate

from corevatis_erpnext.crm import termin as crm_termin
from corevatis_erpnext.planung import geokodierung, slotrechner, terminplanung
from corevatis_erpnext.planung.einstellungen import STANDARD
from corevatis_erpnext.planung.slotrechner import Belegung, Fahrparameter, Ort, Slot
from corevatis_erpnext.tests._angebot import firma
from corevatis_erpnext.tests._helpers import make_lead, make_user
from corevatis_erpnext.tests.test_wochenplan import naechster_montag, um

BUERO = "cv-tp-buero@example.com"
MEISTER1 = "cv-tp-meister1@example.com"
MEISTER2 = "cv-tp-meister2@example.com"
MONTEUR = "cv-tp-monteur@example.com"

ADRESSE_OBJEKT = "Kellerweg 7, 48143 Münster"
ADRESSE_FREMD = "Mühlenweg 5, 48155 Münster"
ADRESSE_HAMM = "Hafenweg 1, 59067 Hamm"
ADRESSE_FIRMA = "Werkstraße 2, 48143 Münster"
KOORDINATEN = {
	ADRESSE_OBJEKT: (51.9607, 7.6261, "Kellerweg, Münster"),
	ADRESSE_FREMD: (51.95, 7.67, "Mühlenweg, Münster"),
	ADRESSE_HAMM: (51.68, 7.82, "Hafenweg, Hamm"),
	ADRESSE_FIRMA: (51.9625, 7.625, "Werkstraße, Münster"),
}
KELLERWEG = Ort(ADRESSE_OBJEKT, *KOORDINATEN[ADRESSE_OBJEKT][:2])
MUEHLENWEG = Ort(ADRESSE_FREMD, *KOORDINATEN[ADRESSE_FREMD][:2])
HAMM = Ort(ADRESSE_HAMM, *KOORDINATEN[ADRESSE_HAMM][:2])
FIRMA = Ort(ADRESSE_FIRMA, *KOORDINATEN[ADRESSE_FIRMA][:2])
P = Fahrparameter()
TAG = dt.date(2030, 3, 4)  # ein Montag
EINSTELLUNGEN = frappe._dict(STANDARD)


def _nominatim(adresse: str):
	return KOORDINATEN.get(adresse)


# --- 1. Rechenregeln ohne Frappe ----------------------------------------------------------------


class TestSlotrechner(TestCase):
	def test_haversine(self):
		km = slotrechner.haversine_km(51.9607, 7.6261, 51.5136, 7.4653)  # Münster -> Dortmund
		self.assertTrue(49 < km < 53, km)
		self.assertAlmostEqual(slotrechner.haversine_km(51.96, 7.62, 51.96, 7.62), 0.0)

	def test_fahrzeit(self):
		self.assertEqual(slotrechner.fahrzeit_minuten(KELLERWEG, KELLERWEG, P), (10, False))
		self.assertEqual(slotrechner.fahrzeit_minuten(KELLERWEG, MUEHLENWEG, P), (10, False))
		self.assertEqual(slotrechner.fahrzeit_minuten(KELLERWEG, HAMM, P), (60, False))
		self.assertEqual(slotrechner.fahrzeit_minuten(KELLERWEG, Ort("ohne Koordinaten"), P), (30, True))
		# 20 km Luftlinie: 20 * 1,3 / 45 * 60 = 34,67 -> 35
		nord = Ort("20 km nördlich", KELLERWEG.lat + 20 / 111.195, KELLERWEG.lon)
		self.assertEqual(slotrechner.fahrzeit_minuten(KELLERWEG, nord, P), (35, False))
		# andere Parameter: Faktor 1,0 bei 60 km/h = 20 min, die Mindestfahrzeit 25 greift
		self.assertEqual(
			slotrechner.fahrzeit_minuten(KELLERWEG, nord, Fahrparameter(1.0, 60, 25, 30, 10)), (25, False)
		)

	def test_aufrunden(self):
		for wert, erwartet in ((31, 35), (30, 30), (0, 0), (34.67, 35), (1, 5)):
			self.assertEqual(slotrechner.aufrunden(wert, 5), erwartet, wert)

	def test_fenster_grenzen(self):
		def grenzen(fenster, beginn=dt.time(7)):
			return slotrechner.fenster_grenzen(TAG, fenster, beginn, 8.0)

		self.assertEqual(grenzen("vormittags"), (um(TAG, "08:00"), um(TAG, "12:00")))
		self.assertEqual(grenzen("nachmittags"), (um(TAG, "12:00"), um(TAG, "15:00")))
		self.assertEqual(grenzen("ganztags"), (um(TAG, "07:00"), um(TAG, "15:00")))
		self.assertEqual(grenzen(None), (um(TAG, "07:00"), um(TAG, "15:00")))
		self.assertIsNone(grenzen("vormittags", dt.time(13)))

	def test_kandidaten(self):
		starts = slotrechner.kandidaten(um(TAG, "08:00"), um(TAG, "12:00"), 60, 30)
		self.assertEqual(len(starts), 7)
		self.assertEqual((starts[0], starts[-1]), (um(TAG, "08:00"), um(TAG, "11:00")))
		self.assertEqual(slotrechner.kandidaten(um(TAG, "08:00"), um(TAG, "12:00"), 300, 30), [])

	def test_slot_pruefen_vorige_und_naechste(self):
		termin = Belegung(
			um(TAG, "09:00"), um(TAG, "10:00"), MUEHLENWEG, "Termin", "Besichtigung", "Event", "EV1", "L1"
		)
		spaeter = Belegung(um(TAG, "13:00"), um(TAG, "14:00"), HAMM, "Arbeitspaket", "Keller", "Task", "T1")
		bel = [termin, spaeter]
		pr = slotrechner.slot_pruefen(um(TAG, "09:30"), um(TAG, "10:30"), KELLERWEG, bel, P)
		self.assertFalse(pr.frei)
		self.assertEqual(pr.konflikte, [termin])
		# Fahrt 10 + Puffer 10: frühestens 10:20
		pr = slotrechner.slot_pruefen(um(TAG, "10:15"), um(TAG, "11:15"), KELLERWEG, bel, P)
		self.assertFalse(pr.frei)
		self.assertEqual(pr.konflikte, [termin])
		self.assertEqual(pr.hin.minuten, 10)
		pr = slotrechner.slot_pruefen(um(TAG, "10:30"), um(TAG, "11:30"), KELLERWEG, bel, P)
		self.assertTrue(pr.frei)
		self.assertIs(pr.hin.belegung, termin)
		self.assertEqual((pr.hin.minuten, pr.hin.geschaetzt), (10, False))
		# weiter nach Hamm 60 min + Puffer 10: Ende spätestens 11:50
		self.assertEqual(pr.weiter.minuten, 60)
		self.assertIs(pr.weiter.belegung, spaeter)
		pr = slotrechner.slot_pruefen(um(TAG, "11:30"), um(TAG, "12:30"), KELLERWEG, bel, P)
		self.assertFalse(pr.frei)
		self.assertEqual(pr.konflikte, [spaeter])
		# ohne Koordinaten: Standardfahrzeit 30, geschätzt
		fremd = Belegung(um(TAG, "09:00"), um(TAG, "10:00"), Ort("irgendwo"), "Termin")
		pr = slotrechner.slot_pruefen(um(TAG, "10:30"), um(TAG, "11:30"), KELLERWEG, [fremd], P)
		self.assertFalse(pr.frei)  # 10:00 + 30 + 10 = 10:40
		pr = slotrechner.slot_pruefen(um(TAG, "10:45"), um(TAG, "11:45"), KELLERWEG, [fremd], P)
		self.assertTrue(pr.frei)
		self.assertTrue(pr.hin.geschaetzt)

	def test_slot_pruefen_tagesgrenzen_ab_firma(self):
		tb, te = slotrechner.arbeitszeit(TAG, dt.time(7), 8.0)
		pr = slotrechner.slot_pruefen(um(TAG, "07:00"), um(TAG, "08:00"), KELLERWEG, [], P, FIRMA, tb, te)
		self.assertFalse(pr.frei)  # 10 min Fahrt ab Tagesbeginn
		self.assertEqual((pr.hin.art, pr.hin.minuten), ("Firma", 10))
		pr = slotrechner.slot_pruefen(um(TAG, "07:30"), um(TAG, "08:30"), KELLERWEG, [], P, FIRMA, tb, te)
		self.assertTrue(pr.frei)
		pr = slotrechner.slot_pruefen(um(TAG, "14:00"), um(TAG, "15:00"), KELLERWEG, [], P, FIRMA, tb, te)
		self.assertFalse(pr.frei)  # Rückfahrt 10 min über das Tagesende
		pr = slotrechner.slot_pruefen(um(TAG, "13:30"), um(TAG, "14:30"), KELLERWEG, [], P, FIRMA, tb, te)
		self.assertTrue(pr.frei)
		self.assertEqual(pr.weiter.art, "Firma")
		# ohne Firma keine Tagesgrenzen (Konfliktprüfung beim Anlegen: nur echte Belegungen)
		self.assertTrue(slotrechner.slot_pruefen(um(TAG, "06:00"), um(TAG, "07:00"), KELLERWEG, [], P).frei)

	def test_feiertag_und_vortag(self):
		feiertag = Belegung(
			um(TAG, "00:00"),
			um(TAG, "00:00") + dt.timedelta(days=1),
			slotrechner.UNBEKANNT,
			"Feiertag",
			"Test",
		)
		self.assertEqual(
			slotrechner.freie_slots(TAG, None, 60, KELLERWEG, [feiertag], P, FIRMA, dt.time(7), 8.0), []
		)
		# eine Belegung vom Vortag ist keine „vorige“ - der Tag beginnt in der Firma
		gestern = TAG - dt.timedelta(days=1)
		vortag = Belegung(um(gestern, "14:00"), um(gestern, "16:00"), HAMM, "Termin")
		slots = slotrechner.freie_slots(TAG, None, 60, KELLERWEG, [vortag], P, FIRMA, dt.time(7), 8.0)
		self.assertEqual(slots[0].beginn, um(TAG, "07:30"))
		self.assertEqual(slots[0].hin.art, "Firma")

	def test_freie_slots_und_bewertung(self):
		termin = Belegung(um(TAG, "09:00"), um(TAG, "10:00"), MUEHLENWEG, "Termin")
		slots = slotrechner.freie_slots(
			TAG, "vormittags", 60, KELLERWEG, [termin], P, FIRMA, dt.time(7), 8.0, wunsch=True
		)
		# 08:00 passt nicht (bis 09:00 plus 10 Fahrt plus 10 Puffer), 10:00 nicht (10:00 + 10 + 10)
		self.assertEqual([s.beginn for s in slots], [um(TAG, "10:30"), um(TAG, "11:00")])
		self.assertTrue(all(s.wunsch for s in slots))
		self.assertEqual(slots[0].fahrt_gesamt, 10 + 10)  # von Mühlenweg 10, zurück zur Firma 10
		# Bewertung: Wunsch zuerst, dann früh, dann wenig Fahrt
		frueh = Slot(um(TAG, "08:00"), um(TAG, "09:00"), None, None, wunsch=False)
		self.assertLess(slots[0].bewertung(), frueh.bewertung())
		ab = slotrechner.freie_slots(
			TAG, "vormittags", 60, KELLERWEG, [termin], P, FIRMA, dt.time(7), 8.0, ab=um(TAG, "10:45")
		)
		self.assertEqual([s.beginn for s in ab], [um(TAG, "11:00")])


# --- 2. Gegen ERPNext ---------------------------------------------------------------------------


class TestTerminplanung(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(BUERO, "Bea", ["Büro", "Sales User"], "Terminplanung")
		make_user(MEISTER1, "Martin", ["Meister", "Sales User"], "Meister")
		make_user(MEISTER2, "Moritz", ["Meister", "Sales User"], "Meisterling")
		make_user(MONTEUR, "Max", ["Monteur"], "Monteur")
		cls.montag = naechster_montag()
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		self.di = self.montag + dt.timedelta(days=1)
		self.mi = self.montag + dt.timedelta(days=2)
		self.do = self.montag + dt.timedelta(days=3)
		self.nominatim = patch.object(geokodierung, "abfragen", side_effect=_nominatim).start()
		self.addCleanup(patch.stopall)
		patch.object(terminplanung, "einstellungen", return_value=frappe._dict(EINSTELLUNGEN)).start()
		patch.object(terminplanung, "firmen_adresse", return_value=ADRESSE_FIRMA).start()
		patch.object(terminplanung, "_feiertage", side_effect=self._feiertage).start()
		# CI-Bench ohne gebaute Assets: E-Mail-CSS für frappe.sendmail fehlt (wie test_termin)
		patch("frappe.utils.jinja_globals.bundled_asset", return_value="/assets/fehlt/email.css").start()
		frappe.cache.delete_value(geokodierung.CACHE_SPERRE)
		self.lead = make_lead(
			last_name=f"Terminplanung {frappe.generate_hash(length=6)}",
			cv_objekt_adresse="Kellerweg 7\n48143 Münster",
			cv_wunsch1_datum=self.di,
			cv_wunsch1_fenster="vormittags",
			cv_wunsch2_datum=self.mi,
			cv_wunsch2_fenster="ganztags",
		)
		self.fremd = make_lead(
			first_name="Fremde", last_name="Kundin", cv_objekt_adresse=ADRESSE_FREMD, mobile_no="0251 999999"
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _feiertage(self, firma, von, bis):
		return {self.do: "Testfeiertag"} if getdate(von) <= self.do <= getdate(bis) else {}

	def _termin_fremd(self, uhrzeit: str = "09:00", meister: str = MEISTER1) -> str:
		frappe.set_user(BUERO)
		try:
			return crm_termin.termin_anlegen(
				self.fremd.name, f"{self.di} {uhrzeit}:00", meister, 60, bestaetigung=0
			)["event"]
		finally:
			frappe.set_user("Administrator")

	def _arbeitspaket(self, user: str, tag: dt.date, von: str = "07:00", bis: str = "15:00") -> str:
		lead = make_lead(first_name="Baustelle", last_name="Hamm", cv_objekt_adresse=ADRESSE_HAMM)
		projekt = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"Terminplanung {frappe.generate_hash(length=6)}",
				"company": firma(),
				"expected_start_date": tag,
				"expected_end_date": tag + dt.timedelta(days=7),
				"cv_lead": lead.name,
			}
		).insert(ignore_permissions=True)
		task = frappe.get_doc(
			{
				"doctype": "Task",
				"subject": "Innenabdichtung Keller",
				"project": projekt.name,
				"company": firma(),
				"status": "Open",
				"exp_start_date": um(tag, von),
				"exp_end_date": um(tag, bis),
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "ToDo",
				"allocated_to": user,
				"description": "Arbeitspaket",
				"reference_type": "Task",
				"reference_name": task.name,
			}
		).insert(ignore_permissions=True)
		return task.name

	def _vorschlaege(self, user: str = BUERO, **kwargs) -> dict:
		frappe.set_user(user)
		try:
			return terminplanung.vorschlaege(
				self.lead.name, meister=json.dumps([MEISTER1, MEISTER2]), **kwargs
			)
		finally:
			frappe.set_user("Administrator")

	# --- Wunsch ---------------------------------------------------------------------------

	def test_wunsch_text(self):
		self.assertEqual(
			terminplanung.wunsch_text(self.lead),
			f"Di {self.di:%d.%m.} vormittags, sonst Mi {self.mi:%d.%m.} ganztags",
		)
		self.assertEqual(terminplanung.wunsch_text(frappe._dict(cv_wunsch_schnell=1)), "schnellstmöglich")
		self.assertEqual(
			terminplanung.wunsch_text(frappe._dict(cv_wunsch1_datum=str(self.di), cv_wunsch_schnell=1)),
			f"Di {self.di:%d.%m.} ganztags, sonst schnellstmöglich",
		)
		self.assertEqual(terminplanung.wunsch_text(frappe._dict()), "")

	# --- Vorschläge -----------------------------------------------------------------------

	def test_vorschlaege_wunsch_fahrzeit_und_belegung(self):
		self.addCleanup(frappe.db.rollback)
		self._termin_fremd("09:00")
		self._arbeitspaket(MEISTER1, self.mi)
		r = self._vorschlaege()
		self.assertEqual(r["dauer"], 60)
		self.assertEqual(
			r["wunsch"]["text"], f"Di {self.di:%d.%m.} vormittags, sonst Mi {self.mi:%d.%m.} ganztags"
		)
		vs = r["vorschlaege"]
		self.assertTrue(vs, r["hinweise"])
		self.assertFalse(r["fahrzeit_geschaetzt"], r["hinweise"])
		# bester Vorschlag: Meister 2 ist frei, Wunschfenster Dienstag vormittags, 10 min ab Firma
		erster = vs[0]
		self.assertEqual(
			(erster["meister"], erster["beginn"], erster["wunsch"]), (MEISTER2, f"{self.di} 08:00:00", True)
		)
		self.assertEqual(
			erster["fahrt_hin"], {"minuten": 10, "geschaetzt": False, "ort": "Firma", "art": "Firma"}
		)
		self.assertTrue(
			erster["text"].startswith(
				f"Di {self.di:%d.%m.} 08:00\u201309:00 · {get_fullname(MEISTER2)} · 10 min Fahrt von Firma"
			),
			erster["text"],
		)
		# Meister 1: Termin 09:00-10:00 am Mühlenweg, danach 10 min Fahrt + 10 min Puffer -> ab 10:30
		m1_di = [v for v in vs if v["meister"] == MEISTER1 and v["datum"] == str(self.di)]
		self.assertTrue(m1_di, vs)
		self.assertEqual(m1_di[0]["beginn"], f"{self.di} 10:30:00")
		self.assertEqual(
			m1_di[0]["fahrt_hin"], {"minuten": 10, "geschaetzt": False, "ort": ADRESSE_FREMD, "art": "Termin"}
		)
		self.assertTrue(all(v["wunsch"] for v in m1_di))
		# Mittwoch: Meister 1 ganztags auf der Baustelle in Hamm, Meister 2 frei
		self.assertFalse([v for v in vs if v["meister"] == MEISTER1 and v["datum"] == str(self.mi)])
		m2_mi = [v for v in vs if v["meister"] == MEISTER2 and v["datum"] == str(self.mi)]
		self.assertTrue(m2_mi, vs)
		self.assertTrue(all(v["wunsch"] for v in m2_mi))
		# Donnerstag ist Feiertag; höchstens zwei Vorschläge je Meister und Tag; Wunsch zuerst
		self.assertFalse([v for v in vs if v["datum"] == str(self.do)])
		zaehler: dict[tuple[str, str], int] = {}
		for v in vs:
			zaehler[(v["meister"], v["datum"])] = zaehler.get((v["meister"], v["datum"]), 0) + 1
		self.assertTrue(all(n <= terminplanung.MAX_JE_MEISTER_UND_TAG for n in zaehler.values()), zaehler)
		flags = [v["wunsch"] for v in vs]
		self.assertEqual(flags, sorted(flags, reverse=True))
		self.assertLessEqual(len(vs), terminplanung.MAX_VORSCHLAEGE)
		# keine Kundendaten fremder Anfragen in der Antwort
		dump = json.dumps(r, ensure_ascii=False)
		for geheim in ("Fremde", "Kundin", "0251 999999", self.fremd.name):
			self.assertNotIn(geheim, dump)

	def test_vorschlaege_ohne_koordinaten(self):
		self.addCleanup(frappe.db.rollback)
		frappe.db.set_value("Lead", self.lead.name, "cv_objekt_adresse", "Nirgendwo 1, 99999 Nirgends")
		r = self._vorschlaege()
		self.assertTrue(r["fahrzeit_geschaetzt"])
		self.assertTrue(
			any("Fahrzeit geschätzt" in h and "Nirgendwo 1" in h for h in r["hinweise"]), r["hinweise"]
		)
		self.assertTrue(r["vorschlaege"])
		hin = r["vorschlaege"][0]["fahrt_hin"]
		self.assertEqual((hin["minuten"], hin["geschaetzt"]), (30, True))
		eintrag = frappe.db.get_value(
			"Geokoordinate",
			geokodierung.adresse_hash("Nirgendwo 1, 99999 Nirgends"),
			["quelle", "lat"],
			as_dict=True,
		)
		self.assertEqual(eintrag.quelle, geokodierung.QUELLE_KEIN_TREFFER)
		self.assertFalse(eintrag.lat)

	def test_geokodierung_aus(self):
		self.addCleanup(frappe.db.rollback)
		frappe.db.set_value("Lead", self.lead.name, "cv_objekt_adresse", "Neue Straße 9, 48143 Münster")
		with patch.object(
			terminplanung, "einstellungen", return_value=frappe._dict(EINSTELLUNGEN, geokodierung_aus=1)
		):
			r = self._vorschlaege()
		self.assertEqual(self.nominatim.call_count, 0)
		self.assertTrue(r["fahrzeit_geschaetzt"])
		self.assertTrue(any("ausgeschaltet" in h for h in r["hinweise"]), r["hinweise"])
		self.assertTrue(r["vorschlaege"])

	def test_geokodierung_zwischenspeicher_budget_und_sperre(self):
		self.addCleanup(frappe.db.rollback)
		ort = geokodierung.koordinaten("Kellerweg 7\n48143 Münster", aktiv=True)
		self.assertEqual((ort.text, ort.lat, ort.lon), (ADRESSE_OBJEKT, 51.9607, 7.6261))
		# gleiche Adresse, anders geschrieben: aus dem Zwischenspeicher
		self.assertTrue(geokodierung.koordinaten("  Kellerweg 7 , 48143  Münster ", aktiv=True).bekannt)
		self.assertEqual(self.nominatim.call_count, 1)
		doc = frappe.get_doc("Geokoordinate", geokodierung.adresse_hash(ADRESSE_OBJEKT))
		self.assertEqual(
			(doc.adresse, doc.quelle, doc.anzeige), (ADRESSE_OBJEKT, "Nominatim", "Kellerweg, Münster")
		)
		# kein Treffer wird gemerkt
		self.assertFalse(geokodierung.koordinaten("Nirgendwo 1", aktiv=True).bekannt)
		self.assertFalse(geokodierung.koordinaten("Nirgendwo 1", aktiv=True).bekannt)
		self.assertEqual(self.nominatim.call_count, 2)
		zeile = frappe.db.get_value(
			"Geokoordinate",
			geokodierung.adresse_hash("Nirgendwo 1"),
			["quelle", "gefunden", "lat", "lon"],
			as_dict=True,
		)
		# nie NULL (Spalten NOT NULL, Befund mvp-test bench-39)
		self.assertEqual(
			(zeile.quelle, zeile.gefunden, zeile.lat, zeile.lon),
			(geokodierung.QUELLE_KEIN_TREFFER, 0, 0.0, 0.0),
		)
		# Budget je Aufruf
		budget = geokodierung.Budget(abfragen=1)
		self.assertTrue(geokodierung.koordinaten(ADRESSE_FREMD, budget, aktiv=True).bekannt)
		self.assertFalse(geokodierung.koordinaten(ADRESSE_HAMM, budget, aktiv=True).bekannt)
		self.assertEqual((budget.rest, budget.verbraucht, self.nominatim.call_count), (0, 1, 3))
		# Netzfehler: keine Ausnahme, danach Sperre
		self.nominatim.side_effect = OSError(
			"kein Netz: https://nominatim.openstreetmap.org/search?q=Hafenweg"
		)
		self.assertFalse(geokodierung.koordinaten(ADRESSE_HAMM, aktiv=True).bekannt)
		self.assertTrue(frappe.cache.get_value(geokodierung.CACHE_SPERRE))
		# ein Netzfehler wird nicht gespeichert
		self.assertFalse(frappe.db.exists("Geokoordinate", geokodierung.adresse_hash(ADRESSE_HAMM)))
		# Error Log ohne Adresse/URL (Security-Review)
		protokoll = frappe.get_all(
			"Error Log",
			filters={"method": "Geokodierung: Nominatim nicht erreichbar"},
			fields=["error"],
			order_by="creation desc",
			limit=1,
		)
		self.assertTrue(protokoll)
		self.assertIn("OSError", protokoll[0].error)
		self.assertNotIn("Hafenweg", protokoll[0].error)
		self.nominatim.side_effect = _nominatim
		self.assertFalse(geokodierung.koordinaten(ADRESSE_HAMM, aktiv=True).bekannt)
		self.assertEqual(self.nominatim.call_count, 4)
		frappe.cache.delete_value(geokodierung.CACHE_SPERRE)
		self.assertTrue(geokodierung.koordinaten(ADRESSE_HAMM, aktiv=True).bekannt)
		# Schalter aus: keine Anfrage
		self.assertFalse(geokodierung.koordinaten("Noch eine Straße 1", aktiv=False).bekannt)
		self.assertEqual(self.nominatim.call_count, 5)
		# Nominatim-Antwort auswerten (ohne Netz): Liste, Grenzen, leer
		antwort = frappe._dict(status_code=200, raise_for_status=lambda: None)
		with patch("requests.get", return_value=antwort), patch.object(geokodierung, "_warten"):
			self.nominatim.side_effect = None
			patch.stopall()
			antwort.json = lambda: [{"lat": "51.9607", "lon": "7.6261", "display_name": "Kellerweg, Münster"}]
			self.assertEqual(geokodierung.abfragen("x"), (51.9607, 7.6261, "Kellerweg, Münster"))
			antwort.json = lambda: []
			self.assertIsNone(geokodierung.abfragen("x"))
			antwort.json = lambda: [{"lat": "0", "lon": "0"}]
			self.assertIsNone(geokodierung.abfragen("x"))

	def test_geokoordinate_manuell(self):
		self.addCleanup(frappe.db.rollback)
		doc = frappe.get_doc(
			{"doctype": "Geokoordinate", "adresse": "Handweg 1\n48143 Münster", "lat": 51.96, "lon": 7.62}
		).insert()
		self.assertEqual(
			(doc.name, doc.adresse, doc.quelle),
			(geokodierung.adresse_hash("Handweg 1, 48143 Münster"), "Handweg 1, 48143 Münster", "Manuell"),
		)
		self.assertEqual(doc.gefunden, 1)
		ort = geokodierung.koordinaten("Handweg 1, 48143 Münster", aktiv=True)
		self.assertEqual((ort.lat, ort.lon), (51.96, 7.62))
		self.assertEqual(self.nominatim.call_count, 0)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Geokoordinate", "adresse": "Falsch 1", "lat": 123, "lon": 7}).insert()

	def test_geokodierung_fehler_brechen_vorschlaege_nicht_ab(self):
		# Befund mvp-test (bench-39): ein Datenbankfehler beim Speichern riss die Vorschläge mit
		self.addCleanup(frappe.db.rollback)
		with patch.object(geokodierung, "speichern", side_effect=Exception("Column 'lat' cannot be null")):
			budget = geokodierung.Budget()
			ort = geokodierung.koordinaten(ADRESSE_OBJEKT, budget, aktiv=True)
			self.assertEqual((ort.text, ort.bekannt), (ADRESSE_OBJEKT, False))
			self.assertEqual(budget.fehlgeschlagen, [ADRESSE_OBJEKT])
			r = self._vorschlaege()
		self.assertTrue(r["vorschlaege"], r["hinweise"])
		self.assertTrue(r["fahrzeit_geschaetzt"])
		self.assertTrue(any("Fahrzeit geschätzt" in h for h in r["hinweise"]), r["hinweise"])
		self.assertTrue(frappe.db.exists("Error Log", {"method": "Geokodierung: Fehler"}))
		# Duplikat: ein paralleler Aufruf hat den Eintrag schon angelegt, der eigene Snapshot sieht ihn nicht
		self.assertTrue(geokodierung.koordinaten(ADRESSE_FREMD, aktiv=True).bekannt)
		name = geokodierung.adresse_hash(ADRESSE_FREMD)
		stand_vorher = frappe.db.get_value("Geokoordinate", name, "stand")
		meldungen = len(frappe.local.message_log)
		with (
			patch.object(geokodierung, "gespeichert", return_value=None),
			patch.object(geokodierung, "_vorhanden", return_value=False),
		):
			ort = geokodierung.koordinaten(ADRESSE_FREMD, aktiv=True)
		self.assertTrue(ort.bekannt)
		self.assertEqual(len(frappe.local.message_log), meldungen)  # keine Meldung „existiert bereits“
		self.assertEqual(frappe.db.count("Geokoordinate", {"adresse": ADRESSE_FREMD}), 1)
		self.assertGreaterEqual(frappe.db.get_value("Geokoordinate", name, "stand"), stand_vorher)
		# kein Treffer, zweiter Aufruf derselben Adresse: gemerkt, keine zweite Anfrage, nie NULL
		anzahl = self.nominatim.call_count
		self.assertFalse(geokodierung.koordinaten("Irgendwo 7, 00000 Nirgends", aktiv=True).bekannt)
		self.assertFalse(geokodierung.koordinaten("Irgendwo 7, 00000 Nirgends", aktiv=True).bekannt)
		self.assertEqual(self.nominatim.call_count, anzahl + 1)
		zeile = frappe.db.get_value(
			"Geokoordinate",
			geokodierung.adresse_hash("Irgendwo 7, 00000 Nirgends"),
			["gefunden", "lat", "lon", "quelle"],
			as_dict=True,
		)
		self.assertEqual((zeile.gefunden, zeile.lat, zeile.lon, zeile.quelle), (0, 0.0, 0.0, "Kein Treffer"))

	# --- Doppelbuchungsschutz -------------------------------------------------------------

	def test_konflikt_beim_anlegen(self):
		self.addCleanup(frappe.db.rollback)
		self._termin_fremd("09:00")
		name1 = get_fullname(MEISTER1)
		frappe.set_user(BUERO)
		with self.assertRaises(terminplanung.TerminKonflikt) as cm:
			crm_termin.termin_anlegen(self.lead.name, f"{self.di} 09:30:00", MEISTER1, 60, bestaetigung=0)
		for teil in (name1, "09:00\u201310:00", "48155 Münster"):
			self.assertIn(teil, str(cm.exception))
		# maschinenlesbare Details für Sync-Handler (App): meister, von, bis, ort, fahrzeit_min
		self.assertIs(type(cm.exception), crm_termin.TerminKonflikt)
		self.assertEqual((cm.exception.meister, cm.exception.grund), (MEISTER1, str(cm.exception)))
		[k] = cm.exception.konflikte
		self.assertEqual(
			(k["meister"], k["meister_name"], k["von"], k["bis"], k["ort"], k["fahrzeit_min"], k["art"]),
			(MEISTER1, name1, f"{self.di} 09:00:00", f"{self.di} 10:00:00", ADRESSE_FREMD, None, "Termin"),
		)
		self.assertEqual(frappe.local.response.get("konflikte"), cm.exception.konflikte)
		# frei aufrufbare Berechnung ohne Rechteprüfung (App)
		frappe.set_user(MONTEUR)
		self.assertTrue(terminplanung.berechne_vorschlaege(self.lead.name, meister=MEISTER2)["vorschlaege"])
		frappe.set_user(BUERO)
		# zu wenig Zeit für Fahrt (10) und Puffer (10): 10:15 geht nicht, 10:30 geht
		with self.assertRaises(terminplanung.TerminKonflikt) as cm:
			crm_termin.termin_anlegen(self.lead.name, f"{self.di} 10:15:00", MEISTER1, 60, bestaetigung=0)
		self.assertIn("10 min Fahrt", str(cm.exception))
		self.assertEqual(cm.exception.konflikte[0]["fahrzeit_min"], 10)
		# Feiertag
		with self.assertRaises(terminplanung.TerminKonflikt) as cm:
			crm_termin.termin_anlegen(self.lead.name, f"{self.do} 09:00:00", MEISTER1, 60, bestaetigung=0)
		self.assertIn("Testfeiertag", str(cm.exception))
		self.assertFalse(frappe.db.get_value("Lead", self.lead.name, "cv_termin"))
		r = crm_termin.termin_anlegen(self.lead.name, f"{self.di} 10:30:00", MEISTER1, 60, bestaetigung=0)
		self.assertEqual(r["konflikte"], [])
		# verschieben auf eine Zeit, die sich mit dem eigenen Termin überschneidet: kein Konflikt
		r2 = crm_termin.termin_anlegen(self.lead.name, f"{self.di} 10:45:00", MEISTER1, 60, bestaetigung=0)
		self.assertEqual((r2["konflikte"], r2["abgesagt"]), ([], r["event"]))
		# der andere Meister ist um 09:30 frei
		r3 = crm_termin.termin_anlegen(self.lead.name, f"{self.di} 09:30:00", MEISTER2, 60, bestaetigung=0)
		self.assertEqual(r3["konflikte"], [])
		# Büro übersteuert: trotzdem anlegen, Vermerk am Termin
		r4 = crm_termin.termin_anlegen(
			self.lead.name, f"{self.di} 09:30:00", MEISTER1, 60, bestaetigung=0, trotzdem=1
		)
		self.assertEqual(len(r4["konflikte"]), 1)
		self.assertIn(name1, r4["konflikte"][0]["text"])
		vermerke = frappe.get_all(
			"Comment",
			filters={"reference_doctype": "Event", "reference_name": r4["event"], "comment_type": "Info"},
			pluck="content",
		)
		self.assertTrue(
			any("Trotz Überschneidung" in v and "09:00\u201310:00" in v for v in vermerke), vermerke
		)

	def test_abwesenheit_sperrt_meister(self):
		"""Plantafel P1.4: eine Abwesenheit (Urlaub, Krank, …) zählt wie ein belegter Tag - keine
		Vorschläge, Konflikt beim Anlegen mit Klartext; mit Uhrzeiten nur dieser Teil des Tages."""
		self.addCleanup(frappe.db.rollback)
		from corevatis_erpnext.tests.test_wochenplan import employee

		employee(MEISTER1, "Martin")
		frappe.get_doc(
			{
				"doctype": "Abwesenheit",
				"mitarbeiter": frappe.db.get_value("Employee", {"user_id": MEISTER1}),
				"art": "Urlaub",
				"von": self.di,
				"bis": self.di,
				"ganztags": 1,
			}
		).insert(ignore_permissions=True)
		r = self._vorschlaege()
		self.assertFalse(
			[v for v in r["vorschlaege"] if v["meister"] == MEISTER1 and v["datum"] == str(self.di)]
		)
		self.assertTrue(
			[v for v in r["vorschlaege"] if v["meister"] == MEISTER2 and v["datum"] == str(self.di)]
		)
		frappe.set_user(BUERO)
		with self.assertRaises(terminplanung.TerminKonflikt) as cm:
			crm_termin.termin_anlegen(self.lead.name, f"{self.di} 09:30:00", MEISTER1, 60, bestaetigung=0)
		self.assertIn(f"ist Di {self.di:%d.%m.} abwesend (Urlaub)", str(cm.exception))
		self.assertEqual(cm.exception.konflikte[0]["art"], "Abwesenheit")
		# Abwesenheit mit Uhrzeiten: nur der Nachmittag ist gesperrt
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "Abwesenheit",
				"mitarbeiter": frappe.db.get_value("Employee", {"user_id": MEISTER2})
				or employee(MEISTER2, "Moritz"),
				"art": "Schulung",
				"von": self.mi,
				"bis": self.mi,
				"ganztags": 0,
				"von_uhrzeit": "12:00:00",
				"bis_uhrzeit": "15:00:00",
			}
		).insert(ignore_permissions=True)
		frappe.set_user(BUERO)
		frei = terminplanung.pruefen(self.lead.name, f"{self.mi} 09:00:00", MEISTER2, 60)
		self.assertTrue(frei["frei"], frei)
		belegt = terminplanung.pruefen(self.lead.name, f"{self.mi} 13:00:00", MEISTER2, 60)
		self.assertFalse(belegt["frei"])
		self.assertIn("12:00\u201315:00", belegt["konflikte"][0]["text"])
		self.assertIn("Schulung", belegt["konflikte"][0]["text"])

	def test_pruefen_und_rechte(self):
		self.addCleanup(frappe.db.rollback)
		self._termin_fremd("09:00")
		frappe.set_user(BUERO)
		r = terminplanung.pruefen(self.lead.name, f"{self.di} 09:30:00", MEISTER1, 60)
		self.assertFalse(r["frei"])
		self.assertEqual([k["doctype"] for k in r["konflikte"]], ["Event"])
		r = terminplanung.pruefen(self.lead.name, f"{self.di} 10:30:00", MEISTER1, None)
		self.assertEqual((r["frei"], r["dauer"], r["konflikte"]), (True, 60, []))
		with self.assertRaises(frappe.ValidationError):
			terminplanung.pruefen(self.lead.name, f"{self.di} 10:30:00", MONTEUR, 60)
		with self.assertRaises(frappe.ValidationError):
			terminplanung.vorschlaege(self.lead.name, meister="niemand@example.com")
		frappe.set_user(MEISTER2)
		self.assertTrue(terminplanung.vorschlaege(self.lead.name, meister=MEISTER2)["vorschlaege"])
		frappe.set_user(MONTEUR)
		with self.assertRaises(frappe.PermissionError):
			terminplanung.vorschlaege(self.lead.name)
		with self.assertRaises(frappe.PermissionError):
			terminplanung.pruefen(self.lead.name, f"{self.di} 10:30:00", MEISTER1)

	def test_ort_fremder_anfrage_nur_plz_ort_ohne_leserecht(self):
		b = Belegung(
			um(self.di, "09:00"),
			um(self.di, "10:00"),
			MUEHLENWEG,
			"Termin",
			"Besichtigung",
			"Event",
			"EV-X",
			self.fremd.name,
		)
		frappe.set_user(BUERO)
		self.assertEqual(terminplanung.belegung_ort(b), ADRESSE_FREMD)
		with patch.object(terminplanung, "_lesbar", return_value=False):
			self.assertEqual(terminplanung.belegung_ort(b), "48155 Münster")
			text = terminplanung.konflikt_text("Martin Meister", b, 25)
		self.assertEqual(text, "Martin Meister ist 09:00\u201310:00 bei 48155 Münster, 25 min Fahrt.")

	# --- Prozessleiste, Einstellungen, Felder -----------------------------------------------

	def test_prozessleiste_zeigt_wunsch_und_termin(self):
		self.addCleanup(frappe.db.rollback)
		from corevatis_erpnext.prozess.kette import status

		frappe.set_user(BUERO)
		schritt = next(s for s in status("Lead", self.lead.name)["schritte"] if s["key"] == "termin")
		self.assertEqual(
			schritt["text"],
			f"Noch kein Termin · Wunsch: Di {self.di:%d.%m.} vormittags, sonst Mi {self.mi:%d.%m.} ganztags",
		)
		crm_termin.termin_anlegen(self.lead.name, f"{self.di} 10:30:00", MEISTER1, 60, bestaetigung=0)
		schritt = next(s for s in status("Lead", self.lead.name)["schritte"] if s["key"] == "termin")
		self.assertEqual(schritt["text"], f"Termin am {self.di:%d.%m.%Y} 10:30 mit {get_fullname(MEISTER1)}")

	def test_einstellungen_felder_uebersetzungen(self):
		from frappe.translate import get_translations_from_csv

		from corevatis_erpnext.planung.einstellungen import EINSTELLUNGEN as DOCTYPE
		from corevatis_erpnext.planung.einstellungen import einstellungen

		e = einstellungen()
		self.assertEqual(
			(
				e.besichtigung_dauer,
				e.fahrzeit_umwegfaktor,
				e.fahrzeit_kmh,
				e.fahrzeit_minimum,
				e.fahrzeit_standard,
				e.fahrzeit_puffer,
				e.geokodierung_aus,
			),
			(60, 1.3, 45.0, 10, 30, 10, 0),
		)
		self.assertEqual(crm_termin._dauer(""), 60)
		self.assertEqual(crm_termin._dauer("90"), 90)
		doc = frappe.get_doc(DOCTYPE)
		doc.besichtigung_dauer = 5
		with self.assertRaises(frappe.ValidationError):
			doc.save()
		meta = frappe.get_meta("Lead")
		for feld, typ in (
			("cv_section_wunschtermin", "Section Break"),
			("cv_wunsch1_datum", "Date"),
			("cv_wunsch1_fenster", "Select"),
			("cv_wunsch2_datum", "Date"),
			("cv_wunsch2_fenster", "Select"),
			("cv_wunsch_schnell", "Check"),
		):
			self.assertEqual(meta.get_field(feld).fieldtype, typ, feld)
		self.assertEqual(
			meta.get_field("cv_wunsch1_fenster").options.split("\n"),
			["", "vormittags", "nachmittags", "ganztags"],
		)
		reihenfolge = [df.fieldname for df in meta.fields]
		self.assertLess(reihenfolge.index("cv_quelle"), reihenfolge.index("cv_section_wunschtermin"))
		self.assertLess(reihenfolge.index("cv_wunsch_schnell"), reihenfolge.index("cv_section_gewerke"))
		de = get_translations_from_csv("de", "corevatis_erpnext")
		for feld in (
			"cv_section_wunschtermin",
			"cv_wunsch1_datum",
			"cv_wunsch1_fenster",
			"cv_wunsch_schnell",
		):
			self.assertIn(meta.get_field(feld).label, de)
		for doctype in (DOCTYPE, "Geokoordinate"):
			for df in frappe.get_meta(doctype).fields:
				if df.label:
					self.assertIn(df.label, de, f"{doctype}: {df.label}")
		sidebar = frappe.get_doc("Sidebar", "Betrieb")
		self.assertIn(
			("DocType", "Geokoordinate"),
			{(i.link_type, i.link_to) for i in sidebar.items if i.type == "Link"},
		)
		wf = frappe.get_doc("Web Form", "kostenlose-einschaetzung")
		felder = {f.fieldname: f for f in wf.web_form_fields}
		for feld in (
			"cv_wunsch1_datum",
			"cv_wunsch1_fenster",
			"cv_wunsch2_datum",
			"cv_wunsch2_fenster",
			"cv_wunsch_schnell",
		):
			self.assertIn(feld, felder)
		self.assertEqual(felder["cv_wunsch1_datum"].fieldtype, "Date")
