"""Aufgabe 5.1: Arbeitspakete (Task) und Materialbedarf (Material Request) aus dem Auftrag.

Abnahme „Tasks und Bestellvorschlag nach SO-Submit“ (Adapter-Seite). Der Auftrag entsteht wie
auf der Site: Testaufmaß → Angebot (Generator 4.1, Kern-Stellvertreter wie in
``test_angebot_generator``) → einreichen → „Angebot annehmen“ (4.4, Auftrag eingereicht) → Hook.
Katalog, Stücklisten und alle Belege sind echt (ERPNext). Sollwerte von Hand aus den
Stücklisten des Katalogs (docs/2.3-leistungskatalog.md) und den Mengen des Testaufmaßes
(docs/4.1-angebotsengine.md), Rechenweg in docs/5.1-tasks-materialbedarf.md.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, get_datetime, getdate, today

from corevatis_erpnext import planung
from corevatis_erpnext.angebot import annahme, generator
from corevatis_erpnext.katalog.importer import importieren
from corevatis_erpnext.planung import arbeitspakete, bauablauf, materialbedarf
from corevatis_erpnext.planung.einstellungen import EINSTELLUNGEN, einstellungen
from corevatis_erpnext.planung.positionen import positionen as positionen_lesen
from corevatis_erpnext.tests import _angebot
from corevatis_erpnext.tests._helpers import make_user
from corevatis_erpnext.tests.test_angebot_generator import KernStellvertreter, testaufmass

FIRMA = "Hülshoff Sanierungstechnik"
BUERO = "cv-planung-buero@example.com"
MEISTER = "cv-planung-meister@example.com"
MONTEUR = "cv-planung-monteur@example.com"
BUERO_NUR = "cv-planung-buero-nur@example.com"
TRENNER = " \u2013 "

# Montageminuten je Einheit (Stückliste 2.3) x Menge (Testaufmaß 4.1)
MINUTEN = {
	"AL-01": 180 * 1,  # 180
	"AA-01": 25 * 15.36,  # 384
	"AA-09": 28 * 21.83,  # 611,24
	"AA-13": 120 * 2,  # 240
	"HS-02": 20 * 13.26,  # 265,2
	"IA-05": 22 * 16.61,  # 365,42
	"IA-08": 15 * 26.80,  # 402
}
# Arbeitspakete in Bauablauf-Reihenfolge: Titel, Sollstunden (Σ Minuten ÷ 60, 2 Stellen)
PAKETE = [
	("Sonstiges", "Allgemeine Leistungen" + TRENNER + "AL-01", 3.00),  # 180 min
	("Außenabdichtung", "Außenabdichtung" + TRENNER + "AA-01, AA-09, AA-13", 20.59),  # 1235,24 min
	("Horizontalsperre Injektion", "Horizontalsperre Injektion" + TRENNER + "R1, R2, Keller außen", 4.42),
	("Innenabdichtung", "Innenabdichtung" + TRENNER + "R1, R2, Keller außen", 12.79),  # 767,42 min
]
# Material je Einheit (Stückliste 2.3) x Menge, summiert; x 1,05 Verschnitt, auf 2 Stellen aufgerundet
MATERIAL = {
	# MAT-KLEIN: AL-01 25 + AA-01 0,5 x 15,36 + AA-13 5 x 2 + HS-02 1 x 13,26 = 55,94 → 58,737
	"MAT-KLEIN": (55.94, 58.74),
	"MAT-BAGGER": (1.8432, 1.94),  # AA-01 0,12 h x 15,36 → 1,93536
	"MAT-PMBC": (142.98, 150.13),  # AA-09 6 l x 21,83 + AA-13 6 l x 2 → 150,129
	"MAT-GEWEBE": (24.013, 25.22),  # AA-09 1,1 m² x 21,83 → 25,21365
	"MAT-DICHTBAND": (6.0, 6.3),  # AA-13 3 lfm x 2
	"MAT-INJ-CREME": (3.978, 4.18),  # HS-02 0,3 l x 13,26 → 4,1769
	"MAT-DICHTSCHL": (181.945, 191.05),  # IA-05 4,5 kg x 16,61 + IA-08 4 kg x 26,80 → 191,04225
}


def naechster_montag(tag=None) -> dt.date:
	tag = getdate(tag or today())
	return getdate(add_days(tag, (7 - tag.weekday()) % 7))


class TestPlanung(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Company", FIRMA):
			raise AssertionError(f"Firma {FIRMA} fehlt - Site-Template huelshoff vor den Tests anwenden (CI)")
		if not frappe.db.exists("Item", "IA-05"):
			importieren(firma=FIRMA, commit=False)  # wird mit der Klasse zurückgerollt

			def cache_leeren():
				from frappe.cache_manager import clear_defaults_cache
				from frappe.model.document import clear_document_cache

				for doctype in ("Item", "BOM", "Item Price", "Price List", "UOM"):
					clear_document_cache(doctype)
				# Der Import setzt auch die Site-Defaults selling/buying_price_list; der
				# Rollback nimmt sie aus der DB, der Defaults-Cache behielte sie (dann setzt
				# ein späterer Import sie nicht und die Site bleibt auf „Standard Selling“).
				clear_defaults_cache("__default")

			cls.addClassCleanup(cache_leeren)
		_angebot.ensure_felder_41()
		from corevatis_erpnext.planung.felder import ensure_planung_felder

		ensure_planung_felder()
		make_user(BUERO, "Büro Planung", ["Büro", "Sales User", "Projects User", "Stock User"])
		make_user(BUERO_NUR, "Büro ohne Zusatzrollen", ["Büro"])
		make_user(MEISTER, "Meister Planung", ["Meister", "Projects Manager"])
		make_user(MONTEUR, "Monteur Planung", ["Monteur"])

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.savepoint("planung_test")
		self.addCleanup(self._zuruecksetzen)
		self.kern = KernStellvertreter()
		self.aufmass = testaufmass()
		for ziel, objekt, wert in (
			(generator, "kern_vorhanden", lambda: True),
			(generator, "_aufmass_lesen", lambda name: self.aufmass),
			(
				generator,
				"_objekt_werte",
				lambda objekt: frappe._dict(titel="Testweg 4, 48143 Münster") if objekt else frappe._dict(),
			),
			(generator, "_kern_kalkulation", lambda: self.kern),
			# Feiertagsliste der Firma bleibt außen vor (eigener Test)
			(arbeitspakete, "freie_tage", lambda firma, start: set()),
		):
			p = patch.object(ziel, objekt, wert)
			p.start()
			self.addCleanup(p.stop)
		self.start = naechster_montag()

	def _zuruecksetzen(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="planung_test")
		from frappe.model.document import clear_document_cache

		for doctype in ("Quotation", "Sales Order", "Project", "Task", "Material Request", "Customer"):
			clear_document_cache(doctype)
		frappe.clear_cache(doctype=EINSTELLUNGEN)

	def auftrag(self, liefertage: int = 14) -> dict:
		"""Testaufmaß → Angebot (eingereicht) → annehmen: Auftrag eingereicht, Projekt, Hook gelaufen."""
		frappe.set_user("Administrator")
		name = generator.erzeuge_angebot(self.aufmass.name)["angebot"]
		frappe.get_doc("Quotation", name).submit()
		frappe.set_user(BUERO)
		r = annahme.angebot_annehmen(
			name,
			auftragsdatum=str(self.start),
			lieferdatum=str(add_days(self.start, liefertage)),
			einreichen=1,
		)
		frappe.set_user("Administrator")
		self.assertEqual(r["auftrag_docstatus"], 1)
		self.assertTrue(r["projekt"])
		return r

	def tasks(self, auftrag: str) -> list:
		return [
			frappe.get_doc("Task", n)
			for n in frappe.get_all(
				"Task", filters={"cv_auftrag": auftrag}, order_by="exp_start_date asc", pluck="name"
			)
		]

	def zeitpunkt(self, tage: int, uhrzeit: str) -> dt.datetime:
		return get_datetime(f"{add_days(self.start, tage)} {uhrzeit}")

	# --- Felder, Einstellungen, Desk ---------------------------------------------------------

	def test_felder_hooks_und_desk(self):
		for doctype, felder in (
			("Task", ("cv_auftrag", "cv_verfahren", "cv_sollstunden", "cv_positionen")),
			("Material Request", ("cv_auftrag",)),
		):
			meta = frappe.get_meta(doctype)
			for feld in felder:
				self.assertTrue(meta.has_field(feld), f"{doctype}.{feld}")
		self.assertEqual(frappe.get_meta("Task").get_field("cv_positionen").fieldtype, "JSON")
		self.assertTrue(frappe.db.exists("DocType", EINSTELLUNGEN))
		hooks = frappe.get_hooks("doc_events")["Sales Order"]["on_submit"]
		# Planung nach der Annahme (die legt das Projekt an)
		self.assertLess(
			hooks.index("corevatis_erpnext.angebot.annahme.auftrag_eingereicht"),
			hooks.index("corevatis_erpnext.planung.auftrag_eingereicht"),
		)
		js = frappe.get_hooks("doctype_js", app_name="corevatis_erpnext")
		self.assertIn("public/js/planung.js", js["Sales Order"])
		self.assertIn("public/js/planung.js", js["Project"])
		# Verbindungen: Projekt → Aufgaben/Materialanforderungen, Auftrag → Materialanforderung
		projekt = frappe.get_meta("Project").get_dashboard_data()
		elemente = {i for t in projekt.transactions for i in t["items"]}
		self.assertLessEqual({"Task", "Material Request"}, elemente)
		auftrag = frappe.get_meta("Sales Order").get_dashboard_data()
		self.assertLessEqual(
			{"Material Request", "Project"}, {i for t in auftrag.transactions for i in t["items"]}
		)
		# Workspace „Anfragen“ (Befund 4.4): Auftrag, Projekt, Aufgabe, Materialanforderung, Einstellungen
		ws = frappe.get_doc("Workspace", "Anfragen")
		ziele = {link.link_to for link in ws.links if link.type == "Link"}
		self.assertLessEqual(
			{
				"Sales Order",
				"Project",
				"Task",
				"Material Request",
				"Corevatis Vertrieb Einstellungen",
				EINSTELLUNGEN,
			},
			ziele,
		)
		karten = [b["data"]["card_name"] for b in frappe.parse_json(ws.content) if b["type"] == "card"]
		self.assertIn("Aufträge und Planung", karten)
		for karte in karten:
			self.assertTrue(any(l.type == "Card Break" and l.label == karte for l in ws.links), karte)

	def test_einstellungen_standard(self):
		e = einstellungen()
		self.assertEqual(
			(e.teamgroesse, e.tagesarbeitszeit, e.verschnitt_prozent, e.material_request_typ),
			(2, 8.0, 5.0, "Purchase"),
		)
		self.assertEqual(
			(e.arbeitspakete_automatisch, e.materialbedarf_automatisch, e.bestand_beruecksichtigen), (1, 1, 0)
		)
		doc = frappe.get_doc(EINSTELLUNGEN)
		doc.teamgroesse = 0
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	# --- Abnahme: Auftrag einreichen → Tasks und Bestellvorschlag ----------------------------

	def test_tasks_und_materialbedarf_nach_einreichen(self):
		r = self.auftrag()
		auftrag, projekt = r["auftrag"], r["projekt"]
		tasks = self.tasks(auftrag)
		self.assertEqual(len(tasks), 4)
		for task, (verfahren, titel, stunden) in zip(tasks, PAKETE, strict=True):
			with self.subTest(verfahren=verfahren):
				self.assertEqual(task.project, projekt)
				self.assertEqual((task.cv_verfahren, task.subject), (verfahren, titel))
				self.assertEqual(flt(task.cv_sollstunden), stunden)
				self.assertEqual(flt(task.expected_time), stunden)
				self.assertEqual(task.status, "Open")
				self.assertEqual(task.company, FIRMA)
				self.assertFalse(
					frappe.get_all("ToDo", filters={"reference_type": "Task", "reference_name": task.name})
				)
		# Sollstunden nachgerechnet gegen die Stücklisten-Minuten
		je_verfahren = {
			"Sonstiges": ["AL-01"],
			"Außenabdichtung": ["AA-01", "AA-09", "AA-13"],
			"Horizontalsperre Injektion": ["HS-02"],
			"Innenabdichtung": ["IA-05", "IA-08"],
		}
		for task in tasks:
			soll = sum(MINUTEN[c] for c in je_verfahren[task.cv_verfahren]) / 60
			self.assertAlmostEqual(flt(task.cv_sollstunden), soll, places=2)
		self.assertAlmostEqual(
			sum(flt(t.cv_sollstunden) for t in tasks), sum(MINUTEN.values()) / 60, places=1
		)

		# Abhängigkeiten nach Bauablauf: jedes Paket hängt am vorigen
		self.assertEqual(tasks[0].depends_on, [])
		for vorher, task in itertools.pairwise(tasks):
			self.assertEqual([d.task for d in task.depends_on], [vorher.name])

		# Termine: Team 2, 8 h ab 07:00, Mo-Fr, fortlaufend ab Auftragsdatum (Montag)
		termine = [
			(self.zeitpunkt(0, "07:00:00"), self.zeitpunkt(0, "08:30:00")),  # 3,00 h ÷ 2 = 1,5 h
			(self.zeitpunkt(0, "08:30:00"), self.zeitpunkt(1, "10:47:42")),  # 10,295 h: 6,5 h + 3,795 h
			(self.zeitpunkt(1, "10:47:42"), self.zeitpunkt(1, "13:00:18")),  # 2,21 h
			(self.zeitpunkt(1, "13:00:18"), self.zeitpunkt(2, "11:24:00")),  # 6,395 h: 1,995 h + 4,4 h
		]
		self.assertEqual(
			[(get_datetime(t.exp_start_date), get_datetime(t.exp_end_date)) for t in tasks], termine
		)
		ende = frappe.db.get_value("Project", projekt, "expected_end_date")
		self.assertEqual(getdate(ende), getdate(add_days(self.start, 14)))  # reicht, unverändert

		# Positionen des Arbeitspakets (JSON) und Beschreibung
		innen = json.loads(tasks[3].cv_positionen)
		self.assertEqual([p["item_code"] for p in innen], ["IA-05", "IA-08"])
		ia05 = innen[0]
		self.assertEqual((ia05["menge"], ia05["einheit"], ia05["minuten_je_einheit"]), (16.61, "m²", 22.0))
		self.assertEqual(
			(ia05["raum"], ia05["bauteil"], ia05["phase"]), ("R1, R2", "Keller außen", "Dichtschlämme")
		)
		self.assertTrue(ia05["bom"].startswith("BOM-IA-05-"))
		self.assertAlmostEqual(ia05["sollstunden"], 365.42 / 60, places=3)
		self.assertIn("IA-05", tasks[3].description)
		self.assertIn(auftrag, tasks[3].description)
		aussen = [p["item_code"] for p in json.loads(tasks[1].cv_positionen)]
		self.assertEqual(aussen, ["AA-01", "AA-09", "AA-13"])  # Erdarbeiten vor Abdichtung

		# Materialanforderung: Entwurf, Einkauf ins Hauptlager, aggregiert, mit Projekt und Auftrag
		namen = frappe.get_all("Material Request", filters={"cv_auftrag": auftrag}, pluck="name")
		self.assertEqual(len(namen), 1)
		mr = frappe.get_doc("Material Request", namen[0])
		self.assertEqual((mr.docstatus, mr.material_request_type, mr.company), (0, "Purchase", FIRMA))
		self.assertEqual(mr.set_warehouse, "Hauptlager - HST")
		self.assertEqual(getdate(mr.schedule_date), max(self.start, getdate(today())))
		self.assertEqual(
			{z.item_code: flt(z.qty) for z in mr.items}, {c: b for c, (_n, b) in MATERIAL.items()}
		)
		for z in mr.items:
			self.assertEqual((z.project, z.sales_order, z.warehouse), (projekt, auftrag, "Hauptlager - HST"))
			self.assertEqual(flt(z.conversion_factor), 1)
			self.assertEqual(z.uom, frappe.db.get_value("Item", z.item_code, "stock_uom"))
		# Verbindungen (Dashboard): Projekt -> Aufgaben (Task.project), Materialanforderung
		# (Material Request Item.project), Auftrag -> Materialanforderung (Material Request Item.sales_order)
		self.assertEqual(frappe.db.count("Task", {"project": projekt}), 4)
		for feld, wert in (("project", projekt), ("sales_order", auftrag)):
			self.assertEqual(
				frappe.get_all(
					"Material Request",
					filters=[["Material Request Item", feld, "=", wert]],
					pluck="name",
					distinct=True,
				),
				namen,
			)

	def test_materialbedarf_nachgerechnet(self):
		r = self.auftrag()
		auftrag = frappe.get_doc("Sales Order", r["auftrag"])
		liste, hinweise = positionen_lesen(auftrag)
		self.assertEqual(hinweise, [])
		zeilen = {z["item_code"]: z for z in materialbedarf.bedarf(liste, 5)}
		self.assertEqual(set(zeilen), set(MATERIAL))
		for code, (netto, mit_verschnitt) in MATERIAL.items():
			with self.subTest(material=code):
				self.assertAlmostEqual(zeilen[code]["netto"], netto, places=4)
				self.assertEqual(zeilen[code]["bedarf"], mit_verschnitt)
		self.assertEqual(zeilen["MAT-PMBC"]["positionen"], sorted(zeilen["MAT-PMBC"]["positionen"]))
		# ganzzahlige Einheit (Stk) wird auf ganze Zahlen aufgerundet
		self.assertEqual(materialbedarf.aufrunden(2.1, True), 3.0)
		self.assertEqual(materialbedarf.aufrunden(6.3, False), 6.3)
		self.assertEqual(materialbedarf.aufrunden(1.93536, False), 1.94)

	# --- Idempotenz, Rechte, ohne Projekt -----------------------------------------------------

	def test_idempotent_auftrag_und_projekt(self):
		r = self.auftrag()
		tasks = [t.name for t in self.tasks(r["auftrag"])]
		mr = frappe.get_all("Material Request", filters={"cv_auftrag": r["auftrag"]}, pluck="name")
		for benutzer in (BUERO, MEISTER):
			frappe.set_user(benutzer)
			for args in ({"sales_order": r["auftrag"]}, {"project": r["projekt"]}):
				with self.subTest(benutzer=benutzer, args=args):
					t = planung.tasks_erzeugen(**args)
					self.assertFalse(t["neu"])
					self.assertEqual([a["name"] for a in t["arbeitspakete"]], tasks)
					self.assertEqual(t["sollstunden"], round(sum(p[2] for p in PAKETE), 2))
					self.assertTrue(any("bereits" in h for h in t["hinweise"]))
					m = planung.materialbedarf_erzeugen(**args)
					self.assertEqual((m["neu"], m["material_request"]), (False, mr[0]))
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Task", {"cv_auftrag": r["auftrag"]}), 4)
		self.assertEqual(frappe.db.count("Material Request", {"cv_auftrag": r["auftrag"]}), 1)

	def test_manuell_nach_loeschen_und_abgeschalteter_automatik(self):
		frappe.db.set_single_value(
			EINSTELLUNGEN, {"arbeitspakete_automatisch": 0, "materialbedarf_automatisch": 0}
		)
		r = self.auftrag()
		self.assertEqual(frappe.db.count("Task", {"cv_auftrag": r["auftrag"]}), 0)
		self.assertEqual(frappe.db.count("Material Request", {"cv_auftrag": r["auftrag"]}), 0)
		frappe.set_user(MEISTER)
		t = planung.tasks_erzeugen(project=r["projekt"])
		self.assertTrue(t["neu"])
		self.assertEqual([a["titel"] for a in t["arbeitspakete"]], [p[1] for p in PAKETE])
		m = planung.materialbedarf_erzeugen(sales_order=r["auftrag"])
		self.assertTrue(m["neu"])
		self.assertEqual((m["typ"], m["lager"]), ("Purchase", "Hauptlager - HST"))
		self.assertEqual(
			{z["item_code"]: z["menge"] for z in m["materialien"]}, {c: b for c, (_n, b) in MATERIAL.items()}
		)

	def test_rechte_monteur(self):
		r = self.auftrag()
		frappe.set_user(MONTEUR)
		for methode in (planung.tasks_erzeugen, planung.materialbedarf_erzeugen):
			with self.subTest(methode=methode.__name__), self.assertRaises(frappe.PermissionError):
				methode(sales_order=r["auftrag"])
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Task", {"cv_auftrag": r["auftrag"]}), 4)

	def test_schreibrechte_fuer_buttons(self):
		"""Security-Review: Buttons/Whitelist legen mit den Rechten des Benutzers an - Rolle Büro ohne
		Task-Anlegerecht → 403, ohne Material-Request-Recht (kein Stock User) → 403."""
		frappe.db.set_single_value(
			EINSTELLUNGEN, {"arbeitspakete_automatisch": 0, "materialbedarf_automatisch": 0}
		)
		r = self.auftrag()
		rechte = frappe.get_all(
			"Custom DocPerm", filters={"parent": "Task", "role": "Büro", "permlevel": 0}, pluck="name"
		)
		self.assertTrue(rechte, "Site-Template: Büro hat Rechte an Task")
		for name in rechte:
			frappe.db.set_value("Custom DocPerm", name, "create", 0)
		frappe.clear_cache(doctype="Task")
		self.addCleanup(frappe.clear_cache, doctype="Task")
		frappe.set_user(BUERO_NUR)
		for methode in (planung.tasks_erzeugen, planung.materialbedarf_erzeugen):
			with self.subTest(methode=methode.__name__), self.assertRaises(frappe.PermissionError):
				methode(sales_order=r["auftrag"])
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Task", {"cv_auftrag": r["auftrag"]}), 0)
		self.assertEqual(frappe.db.count("Material Request", {"cv_auftrag": r["auftrag"]}), 0)
		# mit Rechten (Projects User, Stock User): angelegt, Besitzer ist der Benutzer
		frappe.set_user(BUERO)
		t = planung.tasks_erzeugen(sales_order=r["auftrag"])
		m = planung.materialbedarf_erzeugen(sales_order=r["auftrag"])
		self.assertTrue(t["neu"] and m["neu"])
		self.assertEqual(frappe.db.get_value("Material Request", m["material_request"], "owner"), BUERO)

	def test_fremdes_projekt_zum_auftrag(self):
		r = self.auftrag()
		fremd = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"Fremd {frappe.generate_hash(length=6)}",
				"company": FIRMA,
			}
		).insert()
		frappe.set_user(BUERO)
		for methode in (planung.tasks_erzeugen, planung.materialbedarf_erzeugen):
			with self.subTest(methode=methode.__name__), self.assertRaises(planung.auftrag.PlanungFehler):
				methode(sales_order=r["auftrag"], project=fremd.name)
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Task", {"project": fremd.name}), 0)
		# passendes Projekt zusammen mit dem Auftrag ist erlaubt
		frappe.set_user(BUERO)
		self.assertFalse(planung.tasks_erzeugen(sales_order=r["auftrag"], project=r["projekt"])["neu"])

	def test_ohne_projekt_nur_hinweis_und_ohne_stueckliste(self):
		_angebot.ensure_artikel()
		kunde = generator._platzhalterkunde()
		q = _angebot.angebot(kunde, quotation_to="Customer", zusatz=False, corevatis=False)
		r = annahme.angebot_annehmen(q.name, einreichen=1)
		self.assertIsNone(r["projekt"])
		self.assertEqual(frappe.db.count("Task", {"cv_auftrag": r["auftrag"]}), 0)
		frappe.set_user(BUERO)
		t = planung.tasks_erzeugen(sales_order=r["auftrag"])
		m = planung.materialbedarf_erzeugen(sales_order=r["auftrag"])
		for ergebnis in (t, m):
			self.assertFalse(ergebnis["neu"])
			self.assertTrue(any("kein Projekt" in h for h in ergebnis["hinweise"]), ergebnis["hinweise"])
		self.assertEqual(t["arbeitspakete"], [])
		self.assertIsNone(m["material_request"])
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.count("Task", {"cv_auftrag": r["auftrag"]}), 0)

		# Projekt nachträglich (Annahme mit projekt_anlegen): Artikel ohne Stückliste → 0 Sollstunden,
		# kein Material - mit Hinweis, ohne Fehler
		r = annahme.angebot_annehmen(q.name, projekt_anlegen=1)
		self.assertTrue(r["projekt"])
		frappe.set_user(BUERO)
		t = planung.tasks_erzeugen(sales_order=r["auftrag"])
		self.assertTrue(t["neu"])
		self.assertEqual(
			[(a["verfahren"], a["sollstunden"]) for a in t["arbeitspakete"]], [("Innenabdichtung", 0.0)]
		)
		self.assertTrue(any("keine Stückliste" in h for h in t["hinweise"]), t["hinweise"])
		m = planung.materialbedarf_erzeugen(project=r["projekt"])
		self.assertIsNone(m["material_request"])
		self.assertTrue(any("kein Material" in h for h in m["hinweise"]), m["hinweise"])

	def test_entwurf_und_projekt_ohne_auftrag(self):
		_angebot.ensure_artikel()
		q = _angebot.angebot(generator._platzhalterkunde(), quotation_to="Customer", zusatz=False)
		r = annahme.angebot_annehmen(q.name, einreichen=0)
		frappe.set_user(BUERO)
		with self.assertRaises(planung.auftrag.PlanungFehler):
			planung.tasks_erzeugen(sales_order=r["auftrag"])
		frappe.set_user("Administrator")
		projekt = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"Ohne Auftrag {frappe.generate_hash(length=6)}",
				"company": FIRMA,
			}
		).insert()
		frappe.set_user(BUERO)
		t = planung.tasks_erzeugen(project=projekt.name)
		self.assertEqual((t["neu"], t["arbeitspakete"]), (False, []))
		self.assertTrue(any("keinen Auftrag" in h for h in t["hinweise"]))

	# --- Einstellungen: Umlagerung, Bestand, Teamgröße, Projektende ---------------------------

	def test_umlagerung_bestand_teamgroesse(self):
		frappe.db.set_single_value(
			EINSTELLUNGEN,
			{
				"material_request_typ": "Material Transfer",
				"bestand_beruecksichtigen": 1,
				"verschnitt_prozent": 10,
				"teamgroesse": 3,
			},
		)
		with patch.object(
			materialbedarf, "bestand", lambda item, lager: 100.0 if item == "MAT-PMBC" else -5.0
		):
			r = self.auftrag()
		mr = frappe.get_doc("Material Request", {"cv_auftrag": r["auftrag"]})
		self.assertEqual(mr.material_request_type, "Material Transfer")
		self.assertEqual((mr.set_from_warehouse, mr.set_warehouse), ("Hauptlager - HST", "Fahrzeug - HST"))
		mengen = {z.item_code: flt(z.qty) for z in mr.items}
		# 142,98 x 1,10 = 157,278 → 157,28 - 100 Bestand im Fahrzeug = 57,28; negativer Bestand zählt nicht
		self.assertEqual(mengen["MAT-PMBC"], 57.28)
		self.assertEqual(mengen["MAT-DICHTSCHL"], 200.14)  # 181,945 x 1,1 = 200,1395
		for z in mr.items:
			self.assertEqual((z.from_warehouse, z.warehouse), ("Hauptlager - HST", "Fahrzeug - HST"))
		tasks = self.tasks(r["auftrag"])
		# Team 3: 3,00 h ÷ 3 = 1 h
		self.assertEqual(get_datetime(tasks[0].exp_end_date), self.zeitpunkt(0, "08:00:00"))

	def test_bestand_deckt_bedarf(self):
		frappe.db.set_single_value(
			EINSTELLUNGEN, {"bestand_beruecksichtigen": 1, "materialbedarf_automatisch": 0}
		)
		r = self.auftrag()
		frappe.set_user(BUERO)
		with patch.object(materialbedarf, "bestand", lambda item, lager: 1000.0):
			m = planung.materialbedarf_erzeugen(sales_order=r["auftrag"])
		self.assertIsNone(m["material_request"])
		self.assertTrue(any("deckt den Bedarf" in h for h in m["hinweise"]))
		self.assertTrue(all(z["menge"] == 0 for z in m["materialien"]))

	def test_projektende_wird_verschoben(self):
		r = self.auftrag(liefertage=0)
		ende = frappe.db.get_value("Project", r["projekt"], "expected_end_date")
		self.assertEqual(getdate(ende), getdate(add_days(self.start, 2)))  # letztes Paket endet Mittwoch

	def test_fehler_im_hook_haelt_einreichen_nicht_auf(self):
		def kaputt(*args, **kwargs):
			raise frappe.ValidationError("Testfehler Materialbedarf")

		with patch.object(planung, "materialbedarf_anlegen", kaputt):
			r = self.auftrag()
		self.assertEqual(frappe.db.count("Task", {"cv_auftrag": r["auftrag"]}), 4)
		self.assertEqual(frappe.db.count("Material Request", {"cv_auftrag": r["auftrag"]}), 0)
		self.assertTrue(frappe.db.exists("Error Log", {"reference_name": r["auftrag"]}))
		self.assertIn("Testfehler Materialbedarf", json.dumps(frappe.local.message_log, default=str))
		frappe.set_user(BUERO)
		self.assertTrue(planung.materialbedarf_erzeugen(sales_order=r["auftrag"])["neu"])


class TestBauablauf(IntegrationTestCase):
	"""Reihenfolge und Zeitplan ohne Belege."""

	def test_zeitplan_wochenende_feiertag(self):
		freitag = getdate("2026-10-30")
		plan = bauablauf.zeitplan(freitag, [10, 0, 2], tagesstunden=8, arbeitsbeginn="07:00")
		self.assertEqual(
			plan,
			[
				(get_datetime("2026-10-30 07:00:00"), get_datetime("2026-11-02 09:00:00")),
				(get_datetime("2026-11-02 09:00:00"), get_datetime("2026-11-02 09:00:00")),
				(get_datetime("2026-11-02 09:00:00"), get_datetime("2026-11-02 11:00:00")),
			],
		)
		# Montag Feiertag, Start am Samstag → Dienstag
		plan = bauablauf.zeitplan(
			getdate("2026-10-31"), [8, 1], tagesstunden=8, freie_tage={getdate("2026-11-02")}
		)
		self.assertEqual(
			plan,
			[
				(get_datetime("2026-11-03 07:00:00"), get_datetime("2026-11-03 15:00:00")),
				(get_datetime("2026-11-04 07:00:00"), get_datetime("2026-11-04 08:00:00")),
			],
		)

	def test_freie_tage_aus_feiertagsliste(self):
		liste = frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": f"Test Planung {frappe.generate_hash(length=6)}",
				"from_date": "2026-01-01",
				"to_date": "2027-12-31",
				"holidays": [
					{"holiday_date": "2026-12-25", "description": "1. Weihnachtstag"},
					{"holiday_date": "2026-01-01", "description": "Neujahr"},
				],
			}
		).insert()
		vorher = frappe.db.get_value("Company", FIRMA, "default_holiday_list")
		self.addCleanup(frappe.db.set_value, "Company", FIRMA, "default_holiday_list", vorher)
		frappe.db.set_value("Company", FIRMA, "default_holiday_list", liste.name)
		self.assertEqual(arbeitspakete.freie_tage(FIRMA, "2026-12-01"), {getdate("2026-12-25")})
		frappe.db.set_value("Company", FIRMA, "default_holiday_list", None)
		self.assertEqual(arbeitspakete.freie_tage(FIRMA, "2026-12-01"), set())

	def test_reihenfolge_und_phasen(self):
		liste = [
			{
				"verfahren": v,
				"item_code": c,
				"pos": i,
				"phasen_rang": bauablauf.phasen_rang(c),
				"raum": None,
				"bauteil": None,
				"sollminuten": 60,
			}
			for i, (v, c) in enumerate(
				[
					("Sanierputz", "SP-06"),
					("Innenabdichtung", "IA-05"),
					("Innenabdichtung", "IA-01"),
					("Fliesen", "FL-01"),
					("Außenabdichtung", "AA-12"),
					("Außenabdichtung", "AA-01"),
					("Innenabdichtung", "IA-04"),
					("Sonstiges", "AL-01"),
				]
			)
		]
		pakete = arbeitspakete.pakete_bilden(liste)
		self.assertEqual(
			[p["verfahren"] for p in pakete],
			["Sonstiges", "Außenabdichtung", "Innenabdichtung", "Sanierputz", "Fliesen"],
		)
		innen = next(p for p in pakete if p["verfahren"] == "Innenabdichtung")
		self.assertEqual([p["item_code"] for p in innen["positionen"]], ["IA-01", "IA-04", "IA-05"])
		aussen = next(p for p in pakete if p["verfahren"] == "Außenabdichtung")
		self.assertEqual([p["item_code"] for p in aussen["positionen"]], ["AA-01", "AA-12"])
		self.assertEqual(innen["sollstunden"], 3.0)
