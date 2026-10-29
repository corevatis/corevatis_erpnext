"""Materialentnahme (Aufgabe 3.11): Fahrzeuglager, Bestand mit EAN, Stock Entry „Material Issue“,
Idempotenz, Bestandsprüfung und Fehler - gegen echtes ERPNext v16 (Firma aus dem Site-Template)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from corevatis_erpnext import material as m
from corevatis_erpnext.tests._angebot import firma
from corevatis_erpnext.tests._helpers import make_user
from corevatis_erpnext.tests.material_testdaten import fahrzeug_auffuellen

USER = "cv-test-material-monteur@example.com"
USER_EIGEN = "cv-test-material-eigen@example.com"
ART_KG = "_TEST-CV-MAT-KG"
ART_STK = "_TEST-CV-MAT-STK"
ART_LEER = "_TEST-CV-MAT-LEER"
EAN_KG = "2099990000012"
EAN_STK = "2099990000029"
EAN_LEER = "2099990000036"
EAN_UNBEKANNT = "2099990000999"


def ensure_gender() -> str:
	if not frappe.db.exists("Gender", "Male"):
		frappe.get_doc({"doctype": "Gender", "gender": "Male"}).insert(ignore_permissions=True)
	return "Male"


def ensure_employee(user: str, lager: str | None = None) -> str:
	name = frappe.db.get_value("Employee", {"user_id": user})
	if not name:
		name = (
			frappe.get_doc(
				{
					"doctype": "Employee",
					"first_name": "Material",
					"last_name": user.split("@")[0],
					"company": firma(),
					"gender": ensure_gender(),
					"date_of_birth": "1990-01-01",
					"date_of_joining": "2020-01-01",
					"status": "Active",
					"user_id": user,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
	frappe.db.set_value("Employee", name, m.EMPLOYEE_FELD, lager)
	return name


def ensure_item(code: str, uom: str, ean: str) -> None:
	if frappe.db.exists("Item", code):
		return
	if not frappe.db.exists("UOM", uom):
		frappe.get_doc({"doctype": "UOM", "uom_name": uom}).insert(ignore_permissions=True)
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": code,
			"item_name": f"Testmaterial {code}",
			"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
			"stock_uom": uom,
			"is_stock_item": 1,
			"barcodes": [{"barcode": ean, "barcode_type": "EAN"}],
		}
	).insert(ignore_permissions=True)


def ensure_warehouse(name: str) -> str:
	company = firma()
	abbr = frappe.db.get_value("Company", company, "abbr")
	full = f"{name} - {abbr}"
	if not frappe.db.exists("Warehouse", full):
		frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": name,
				"company": company,
				"parent_warehouse": frappe.db.get_value(
					"Warehouse", {"company": company, "is_group": 1}, "name", order_by="lft asc"
				),
			}
		).insert(ignore_permissions=True)
	return full


def bin_menge(item: str, lager: str) -> float:
	return flt(frappe.db.get_value("Bin", {"item_code": item, "warehouse": lager}, "actual_qty"))


def key() -> str:
	return frappe.generate_hash(length=32)


class TestMaterial(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		m.ensure_setup()
		make_user(USER, "Material", ["Monteur"])
		make_user(USER_EIGEN, "Eigen", ["Monteur"])
		cls.fahrzeug = frappe.db.get_value(
			"Warehouse", {"warehouse_name": m.FAHRZEUG_LAGER, "company": firma(), "is_group": 0}, "name"
		) or ensure_warehouse(m.FAHRZEUG_LAGER)
		cls.fahrzeug2 = ensure_warehouse("_Test Fahrzeug 2")
		cls.employee = ensure_employee(USER)
		cls.employee_eigen = ensure_employee(USER_EIGEN, cls.fahrzeug2)
		ensure_item(ART_KG, "Kg", EAN_KG)
		ensure_item(ART_STK, "Nos", EAN_STK)
		ensure_item(ART_LEER, "Kg", EAN_LEER)
		frappe.db.set_value("UOM", "Nos", "must_be_whole_number", 1)
		cls.projekt = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"_TEST-CV-MAT {frappe.generate_hash(length=6)}",
				"company": firma(),
			}
		).insert(ignore_permissions=True)
		cls.task = frappe.get_doc(
			{"doctype": "Task", "subject": "Innenabdichtung", "project": cls.projekt.name}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		# je Test definierter Bestand: 20 Kg, 10 Stk im Fahrzeug, 5 Kg im zweiten Fahrzeug
		self.auffuellen(self.fahrzeug, {ART_KG: 20, ART_STK: 10})
		self.auffuellen(self.fahrzeug2, {ART_KG: 5})

	def auffuellen(self, lager: str, mengen: dict) -> None:
		fahrzeug_auffuellen(lager, mengen, commit=False)
		for item, ziel in mengen.items():
			zuviel = bin_menge(item, lager) - ziel
			if zuviel > 0:
				m.entnahme_buchen(None, lager, None, None, [{"item_code": item, "menge": zuviel}], key())
			self.assertEqual(bin_menge(item, lager), ziel)

	# ------------------------------------------------------------------ Einrichtung, Lager

	def test_custom_fields(self):
		self.assertEqual(frappe.get_meta("Employee").get_field(m.EMPLOYEE_FELD).options, "Warehouse")
		meta = frappe.get_meta("Stock Entry")
		for feld in (m.SYNC_KEY_FELD, m.TASK_FELD, m.MITARBEITER_FELD):
			self.assertIsNotNone(meta.get_field(feld), feld)
		self.assertTrue(meta.get_field("project"))

	def test_fahrzeuglager_fallback_und_employee(self):
		lager = m.fahrzeuglager(self.employee)
		self.assertEqual((lager["warehouse"], lager["quelle"]), (self.fahrzeug, "fallback"))
		lager = m.fahrzeuglager(self.employee_eigen)
		self.assertEqual((lager["warehouse"], lager["quelle"]), (self.fahrzeug2, "employee"))
		self.assertEqual(lager["company"], firma())
		# ohne Employee: Fahrzeug der Standardfirma; unbekannter Employee: None
		self.assertIsNotNone(m.fahrzeuglager(None))
		self.assertIsNone(m.fahrzeuglager("EMP-GIBT-ES-NICHT"))
		self.assertEqual(m.employee_fuer_user(USER)["name"], self.employee)
		self.assertIsNone(m.employee_fuer_user("niemand@example.com"))
		# Gruppe ist kein buchbares Lager
		gruppe = frappe.db.get_value("Warehouse", {"company": firma(), "is_group": 1}, "name")
		self.assertIsNone(m.lager(gruppe))
		namen = {lg["warehouse"] for lg in m.fahrzeuglager_liste(firma())}
		self.assertLessEqual({self.fahrzeug, self.fahrzeug2}, namen)

	def test_mitarbeiter_und_rechte(self):
		ma = m.mitarbeiter(self.employee)
		self.assertEqual((ma["name"], ma["status"], ma["user_id"]), (self.employee, "Active", USER))
		self.assertIsNone(m.mitarbeiter("EMP-GIBT-ES-NICHT"))
		self.assertEqual(m.lager_rechte(self.fahrzeug), {"lesen": True, "buchen": True})
		frappe.set_user(USER)  # Monteur ohne Stock-Rollen (Site-Template: Warehouse lesen, kein Submit)
		try:
			rechte = m.lager_rechte(self.fahrzeug)
		finally:
			frappe.set_user("Administrator")
		self.assertFalse(rechte["buchen"])

	def test_bestand_mit_ean(self):
		zeilen = {z["item_code"]: z for z in m.bestand(self.fahrzeug)}
		self.assertEqual(zeilen[ART_KG]["menge"], 20)
		self.assertEqual(zeilen[ART_KG]["eans"], [EAN_KG])
		self.assertEqual(zeilen[ART_KG]["uom"], "Kg")
		self.assertFalse(zeilen[ART_KG]["ganzzahlig"])
		self.assertTrue(zeilen[ART_STK]["ganzzahlig"])
		# ohne Suche nur Artikel mit Bestand im Lager
		self.assertNotIn(ART_LEER, zeilen)
		# EAN exakt (auch Artikel ohne Bestand), unbekannte EAN leer
		treffer = m.bestand(self.fahrzeug, ean=EAN_LEER)
		self.assertEqual([(t["item_code"], t["menge"]) for t in treffer], [(ART_LEER, 0.0)])
		self.assertEqual(m.bestand(self.fahrzeug, ean=EAN_UNBEKANNT), [])
		self.assertEqual(m.artikel_zu_ean(EAN_STK), ART_STK)
		# Suche im Code/Namen über alle Lagerartikel
		codes = {t["item_code"] for t in m.bestand(self.fahrzeug, suche="_TEST-CV-MAT")}
		self.assertEqual(codes, {ART_KG, ART_STK, ART_LEER})

	# ------------------------------------------------------------------ Buchung

	def test_entnahme_reduziert_bestand(self):
		ergebnis = m.entnahme_buchen(
			self.employee,
			self.fahrzeug,
			None,
			self.task.name,
			[{"item_code": ART_KG, "menge": 4.5}, {"ean": EAN_STK, "menge": 3}],
			key(),
			notiz="Nordwand",
		)
		self.assertFalse(ergebnis["duplikat"])
		self.assertEqual(ergebnis["docstatus"], 1)
		se = frappe.get_doc("Stock Entry", ergebnis["stock_entry"])
		self.assertEqual(se.purpose, "Material Issue")
		self.assertEqual(se.project, self.projekt.name)  # aus der Task übernommen
		self.assertEqual(se.get(m.TASK_FELD), self.task.name)
		self.assertEqual(se.get(m.MITARBEITER_FELD), self.employee)
		self.assertIn("Nordwand", se.remarks)
		self.assertEqual({z.project for z in se.items}, {self.projekt.name})
		self.assertEqual(bin_menge(ART_KG, self.fahrzeug), 15.5)
		self.assertEqual(bin_menge(ART_STK, self.fahrzeug), 7)
		nachher = {p["item_code"]: p["bestand_nachher"] for p in ergebnis["positionen"]}
		self.assertEqual(nachher, {ART_KG: 15.5, ART_STK: 7})
		# Verlauf
		verlauf = m.entnahmen(warehouse=self.fahrzeug, employee=self.employee, datum=str(se.posting_date))
		self.assertIn(se.name, [v["stock_entry"] for v in verlauf])

	def test_idempotenz_dreifach(self):
		k = key()
		ergebnisse = [
			m.entnahme_buchen(
				self.employee, self.fahrzeug, self.projekt.name, None, [{"item_code": ART_KG, "menge": 2}], k
			)
			for _ in range(3)
		]
		self.assertEqual(len({e["stock_entry"] for e in ergebnisse}), 1)
		self.assertEqual([e["duplikat"] for e in ergebnisse], [False, True, True])
		self.assertEqual(frappe.db.count("Stock Entry", {m.SYNC_KEY_FELD: k}), 1)
		self.assertEqual(bin_menge(ART_KG, self.fahrzeug), 18)
		# derselbe Key für einen anderen Mitarbeiter: abgelehnt
		with self.assertRaises(m.MaterialFehler) as ctx:
			m.entnahme_buchen(
				self.employee_eigen, self.fahrzeug2, None, None, [{"item_code": ART_KG, "menge": 1}], k
			)
		self.assertEqual(ctx.exception.grund, "schluessel_fremd")

	def test_fehler(self):
		def fehler(positionen, lager=None, **kw):
			with self.assertRaises(m.MaterialFehler) as ctx:
				m.entnahme_buchen(
					self.employee,
					lager or self.fahrzeug,
					kw.get("project"),
					kw.get("task"),
					positionen,
					key(),
				)
			return ctx.exception

		e = fehler([{"ean": EAN_UNBEKANNT, "menge": 1}])
		self.assertEqual((e.grund, e.details["ean"]), ("ean_unbekannt", EAN_UNBEKANNT))
		self.assertEqual(fehler([{"item_code": "GIBT-ES-NICHT", "menge": 1}]).grund, "artikel_unbekannt")
		self.assertEqual(fehler([{"item_code": ART_KG, "menge": 0}]).grund, "menge_ungueltig")
		self.assertEqual(fehler([{"item_code": ART_KG, "menge": "abc"}]).grund, "menge_ungueltig")
		self.assertEqual(fehler([]).grund, "menge_ungueltig")
		self.assertEqual(fehler([{"item_code": ART_STK, "menge": 1.5}]).grund, "menge_ganzzahlig")
		leistung = frappe.db.get_value("Item", {"is_stock_item": 0, "disabled": 0}, "name")
		if leistung:
			self.assertEqual(fehler([{"item_code": leistung, "menge": 1}]).grund, "kein_lagerartikel")
		gruppe = frappe.db.get_value("Warehouse", {"company": firma(), "is_group": 1}, "name")
		self.assertEqual(fehler([{"item_code": ART_KG, "menge": 1}], lager=gruppe).grund, "lager_unbekannt")
		self.assertEqual(
			fehler([{"item_code": ART_KG, "menge": 1}], project="PROJ-GIBT-ES-NICHT").grund,
			"projekt_unbekannt",
		)
		self.assertEqual(
			fehler([{"item_code": ART_KG, "menge": 1}], task="TASK-GIBT-ES-NICHT").grund, "task_unbekannt"
		)
		anderes = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"_TEST-CV-MAT-B {frappe.generate_hash(length=6)}",
				"company": firma(),
			}
		).insert(ignore_permissions=True)
		self.assertEqual(
			fehler([{"item_code": ART_KG, "menge": 1}], project=anderes.name, task=self.task.name).grund,
			"task_projekt",
		)
		self.assertEqual(
			fehler([{"item_code": ART_KG, "menge": 1}] * (m.MAX_POSITIONEN + 1)).grund, "zu_viele_positionen"
		)
		# nichts gebucht
		self.assertEqual(bin_menge(ART_KG, self.fahrzeug), 20)

	def test_menge_ueber_bestand(self):
		# zwei Zeilen desselben Artikels zählen zusammen: 15 + 6 > 20
		with self.assertRaises(m.MaterialFehler) as ctx:
			m.entnahme_buchen(
				self.employee,
				self.fahrzeug,
				None,
				None,
				[{"item_code": ART_KG, "menge": 15}, {"item_code": ART_KG, "menge": 6}],
				key(),
			)
		self.assertEqual(ctx.exception.grund, "bestand_unzureichend")
		self.assertEqual(
			ctx.exception.details["positionen"],
			[{"item_code": ART_KG, "verfuegbar": 20.0, "angefordert": 21.0}],
		)
		self.assertEqual(bin_menge(ART_KG, self.fahrzeug), 20)

	def test_negativer_bestand_wenn_erlaubt(self):
		frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
		try:
			ergebnis = m.entnahme_buchen(
				self.employee, self.fahrzeug2, None, None, [{"item_code": ART_KG, "menge": 7}], key()
			)
		finally:
			frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 0)
		self.assertEqual(len(ergebnis["hinweise"]), 1, ergebnis)
		self.assertIn("negativer Bestand erlaubt", ergebnis["hinweise"][0])
		self.assertEqual(bin_menge(ART_KG, self.fahrzeug2), -2)
		# zurück auf den Ausgangsbestand für die übrigen Tests
		fahrzeug_auffuellen(self.fahrzeug2, {ART_KG: 5}, commit=False)
