"""Materialwirtschaft (Phase 2, P2.2): Bestellung aus dem Materialbedarf, Lieferung mit Teilmengen,
Fahrzeug beladen, Mindestbestand und Nachbestellen, Pille der Prozessleiste, Rechte.

Die CI-Bench hat ``corevatis`` nicht: Angebot, Annahme, Auftrag und Projekt sind echt (``_abrechnung.vorgang``),
die Materialanforderung wird mit eigenen Lagerartikeln angelegt (Katalog-Stücklisten spielen hier keine Rolle).
"""

from __future__ import annotations

import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, today

from corevatis_erpnext import material as m
from corevatis_erpnext import materialwirtschaft as mw
from corevatis_erpnext import nachkalkulation as nk
from corevatis_erpnext.abrechnung import kern
from corevatis_erpnext.prozess.kette import status
from corevatis_erpnext.tests import _abrechnung as H
from corevatis_erpnext.tests._abrechnung import BUERO, MEISTER, MONTEUR, _als
from corevatis_erpnext.tests.test_material import bin_menge, ensure_employee, ensure_item, ensure_warehouse

ART_A = "_TEST-CV-MW-DICHTSCHLAEMME"
ART_B = "_TEST-CV-MW-DICHTBAND"
EAN_A = "2099990000600"
EAN_B = "2099990000617"
HAUPT = "_Test MW Hauptlager"
FAHRZEUG = "_Test MW Fahrzeug"
LIEFERANT = "_Test MW Baustoffhandel"


def ensure_lieferant(name: str) -> str:
	if not frappe.db.exists("Supplier", name):
		frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": name,
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
				or "All Supplier Groups",
				"supplier_type": "Company",
			}
		).insert(ignore_permissions=True)
	return name


def ensure_standardlieferant(item_code: str, firma: str, lieferant: str) -> None:
	item = frappe.get_doc("Item", item_code)
	zeile = next((d for d in item.item_defaults if d.company == firma), None)
	if zeile is None:
		zeile = item.append("item_defaults", {"company": firma})
	zeile.default_supplier = lieferant
	item.flags.ignore_permissions = True
	item.save()


def einkaufspreisliste() -> str:
	"""Einkaufspreisliste der Site (Buying Settings; die Materialwirtschaft liest dieselbe), sonst „Standard-Einkauf“."""
	name = frappe.db.get_single_value("Buying Settings", "buying_price_list") or "Standard-Einkauf"
	if not frappe.db.exists("Price List", name):
		frappe.get_doc(
			{"doctype": "Price List", "price_list_name": name, "buying": 1, "currency": "EUR", "enabled": 1}
		).insert(ignore_permissions=True)
	return name


def ensure_einkaufspreis(item_code: str, preis: float) -> None:
	liste = einkaufspreisliste()
	if not frappe.db.exists("Item Price", {"item_code": item_code, "price_list": liste}):
		frappe.get_doc(
			{"doctype": "Item Price", "item_code": item_code, "price_list": liste, "price_list_rate": preis}
		).insert(ignore_permissions=True)


def ensure_konto(firma: str, feld: str, name: str, account_type: str, root_type: str) -> None:
	"""Firmenkonto für die Lieferbuchung (Wareneingang: „Stock Received But Not Billed“), falls der
	Kontenrahmen der Test-Site keins liefert."""
	if frappe.db.get_value("Company", firma, feld):
		return
	konto = frappe.db.get_value("Account", {"company": firma, "account_type": account_type, "is_group": 0})
	if not konto:
		eltern = frappe.db.get_value(
			"Account", {"company": firma, "root_type": root_type, "is_group": 1}, "name", order_by="lft asc"
		)
		konto = (
			frappe.get_doc(
				{
					"doctype": "Account",
					"account_name": name,
					"company": firma,
					"parent_account": eltern,
					"account_type": account_type,
					"is_group": 0,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
	frappe.db.set_value("Company", firma, feld, konto, update_modified=False)
	frappe.clear_cache(doctype="Company")


class TestMaterialwirtschaft(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if kern.KERN_APP in frappe.get_installed_apps():
			raise unittest.SkipTest(
				"corevatis installiert - Stellvertreter der Engine würde die echte überdecken"
			)
		frappe.set_user("Administrator")
		H.einrichten()
		m.ensure_setup()
		mw.ensure_setup()
		cls.firma = frappe.db.get_value("Warehouse", ensure_warehouse(HAUPT), "company")
		cls.haupt = ensure_warehouse(HAUPT)
		cls.fahrzeug = ensure_warehouse(FAHRZEUG)
		cls.employee = ensure_employee(MONTEUR, cls.fahrzeug)
		ensure_item(ART_A, "Kg", EAN_A)
		ensure_item(ART_B, "Meter", EAN_B)
		cls.lieferant = ensure_lieferant(LIEFERANT)
		ensure_standardlieferant(ART_A, cls.firma, cls.lieferant)
		ensure_einkaufspreis(ART_A, 12.5)
		ensure_einkaufspreis(ART_B, 3.0)
		ensure_konto(
			cls.firma,
			"stock_received_but_not_billed",
			"Wareneingang nicht fakturiert",
			"Stock Received But Not Billed",
			"Liability",
		)
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		H.kern_patch(self)
		# Hauptlager der Planung = unser Testlager (Einstellung wird mit dem Test zurückgerollt)
		frappe.db.set_single_value("Corevatis Planung Einstellungen", "lager_haupt", self.haupt)
		frappe.clear_cache(doctype="Corevatis Planung Einstellungen")
		self.auftrag, self.projekt = H.vorgang(kalkulation=False)

	# --- Hilfen ---------------------------------------------------------------------------------

	def materialbedarf(self, mengen: dict[str, float] | None = None) -> str:
		"""Materialanforderung (Entwurf, Einkauf) zum Projekt wie 5.1 sie anlegt."""
		mengen = mengen or {ART_A: 10, ART_B: 4}
		heute = today()
		mr = frappe.get_doc(
			{
				"doctype": "Material Request",
				"material_request_type": "Purchase",
				"company": self.firma,
				"transaction_date": heute,
				"schedule_date": heute,
				"set_warehouse": self.haupt,
				"cv_auftrag": self.auftrag,
				"items": [
					{
						"item_code": code,
						"qty": menge,
						"uom": frappe.db.get_value("Item", code, "stock_uom"),
						"stock_uom": frappe.db.get_value("Item", code, "stock_uom"),
						"conversion_factor": 1,
						"schedule_date": heute,
						"warehouse": self.haupt,
						"project": self.projekt,
						"sales_order": self.auftrag,
					}
					for code, menge in mengen.items()
				],
			}
		)
		mr.insert(ignore_permissions=True)
		return mr.name

	def bestellung(self, mr: str, einreichen: int = 1) -> dict:
		v = _als(BUERO, mw.bestellung_vorbereiten, mr)
		zeilen = [
			{
				"material_request_item": p["material_request_item"],
				"lieferant": p["lieferant"] or self.lieferant,
				"menge": p["menge"],
			}
			for p in v["positionen"]
		]
		return _als(BUERO, mw.bestellen, mr, zeilen, self.haupt, einreichen)

	def arbeitspaket_fuer_monteur(self) -> str:
		task = frappe.get_doc(
			{
				"doctype": "Task",
				"subject": "Innenabdichtung Keller",
				"project": self.projekt,
				"exp_start_date": today(),
				"exp_end_date": add_days(today(), 2),
				"status": "Open",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "ToDo",
				"reference_type": "Task",
				"reference_name": task.name,
				"allocated_to": MONTEUR,
				"description": "Test",
				"status": "Open",
			}
		).insert(ignore_permissions=True)
		return task.name

	# --- Tests ----------------------------------------------------------------------------------

	def test_einrichtung_und_rechte(self):
		mw.ensure_setup()
		mw.ensure_setup()  # idempotent
		for doctype, recht in (
			("Purchase Order", "create"),
			("Purchase Receipt", "submit"),
			("Supplier", "write"),
			("Material Request", "submit"),
			("Stock Entry", "submit"),
		):
			self.assertTrue(frappe.has_permission(doctype, recht, user=BUERO), f"Büro {recht} {doctype}")
		self.assertTrue(frappe.has_permission("Purchase Order", "read", user=MEISTER))
		self.assertFalse(frappe.has_permission("Purchase Order", "create", user=MEISTER))
		self.assertFalse(frappe.has_permission("Purchase Order", "read", user=MONTEUR))
		einst = frappe.db.get_singles_dict("Stock Settings")
		self.assertEqual(
			(int(einst.get("auto_indent") or 0), int(einst.get("reorder_email_notify") or 0)), (0, 0)
		)
		# Rolle Monteur darf nichts aus der Materialwirtschaft
		mr = self.materialbedarf()
		for fn, args in (
			(mw.bestellung_vorbereiten, (mr,)),
			(mw.bestellen, (mr, [])),
			(mw.fahrzeug_vorschlag, (self.employee,)),
			(mw.nachbestellen, ({},)),
			(mw.mindestbestand_setzen, (ART_A, self.fahrzeug, 5)),
		):
			with self.assertRaises(frappe.PermissionError, msg=fn.__name__):
				_als(MONTEUR, fn, *args)

	def test_bestellen_aus_materialbedarf(self):
		mr = self.materialbedarf()
		v = _als(BUERO, mw.bestellung_vorbereiten, mr)
		self.assertEqual(v["docstatus"], 0)
		self.assertEqual(v["projekt"], self.projekt)
		self.assertEqual([p["item_code"] for p in v["positionen"]], [ART_A, ART_B])
		self.assertEqual(
			v["positionen"][0]["lieferant"], self.lieferant
		)  # Standardlieferant aus Item Default
		self.assertIsNone(v["positionen"][1]["lieferant"])  # ohne Standard: Wahl im Dialog
		self.assertEqual(v["positionen"][0]["preis"], 12.5)
		self.assertIn(self.haupt, [o["warehouse"] for o in v["lager"]])
		self.assertTrue(v["darf_bestellen"])
		# ohne Lieferant für B: Fehler, nichts angelegt
		with self.assertRaises(frappe.MandatoryError):
			_als(
				BUERO,
				mw.bestellen,
				mr,
				[
					{
						"material_request_item": p["material_request_item"],
						"lieferant": p["lieferant"],
						"menge": p["menge"],
					}
					for p in v["positionen"]
				],
			)
		self.assertEqual(frappe.db.get_value("Material Request", mr, "docstatus"), 0)
		# Menge über dem offenen Bedarf
		with self.assertRaises(mw.MaterialwirtschaftFehler):
			_als(
				BUERO,
				mw.bestellen,
				mr,
				[
					{
						"material_request_item": v["positionen"][0]["material_request_item"],
						"lieferant": self.lieferant,
						"menge": 99,
					}
				],
			)
		r = self.bestellung(mr, einreichen=0)
		self.assertEqual(len(r["bestellungen"]), 1)  # beide Zeilen beim selben Lieferanten → eine Bestellung
		b = r["bestellungen"][0]
		self.assertEqual(
			(b["lieferant"], b["positionen"], b["docstatus"], b["lager"]), (self.lieferant, 2, 0, self.haupt)
		)
		self.assertEqual(b["netto"], 10 * 12.5 + 4 * 3.0)
		self.assertIn(f"Materialanforderung {mr} eingereicht.", r["hinweise"])
		po = frappe.get_doc("Purchase Order", b["name"])
		self.assertEqual((po.project, po.set_warehouse, po.docstatus), (self.projekt, self.haupt, 0))
		self.assertTrue(
			all(
				z.project == self.projekt and z.warehouse == self.haupt and z.material_request == mr
				for z in po.items
			)
		)
		self.assertEqual({z.item_code: flt(z.qty) for z in po.items}, {ART_A: 10, ART_B: 4})
		# Materialanforderung ist eingereicht; ERPNext zählt „bestellt“ erst mit der Freigabe der Bestellung -
		# bis dahin zieht der Dialog die Entwurfsmengen ab (kein doppeltes Bestellen)
		mr_doc = frappe.get_doc("Material Request", mr)
		self.assertEqual((mr_doc.docstatus, flt(mr_doc.per_ordered)), (1, 0))
		v_entwurf = _als(BUERO, mw.bestellung_vorbereiten, mr)
		self.assertEqual((v_entwurf["positionen"], v_entwurf["entwuerfe"]), ([], [po.name]))
		z = _als(BUERO, mw.anforderung_stand, mr)
		self.assertEqual((z["offen"], z["entwuerfe"]), (0, [po.name]))
		self.assertEqual(
			_als(BUERO, mw.stand, self.projekt)["text"],
			"Material: 0 offen, 2 im Bestellentwurf, 0 bestellt, 0 geliefert",
		)
		# Freigabe durch das Büro
		_als(BUERO, lambda: frappe.get_doc("Purchase Order", po.name).submit())
		self.assertEqual(frappe.db.get_value("Purchase Order", po.name, "status"), "To Receive and Bill")
		self.assertEqual(flt(frappe.db.get_value("Material Request", mr, "per_ordered")), 100)
		self.assertEqual(_als(BUERO, mw.bestellung_vorbereiten, mr)["positionen"], [])
		# zweiter Lieferant: zwei Bestellungen
		mr2 = self.materialbedarf({ART_A: 2, ART_B: 1})
		v2 = _als(BUERO, mw.bestellung_vorbereiten, mr2)
		zweiter = ensure_lieferant("_Test MW Zweithandel")
		zeilen = [
			{
				"material_request_item": p["material_request_item"],
				"lieferant": self.lieferant if p["item_code"] == ART_A else zweiter,
				"menge": p["menge"],
			}
			for p in v2["positionen"]
		]
		r2 = _als(BUERO, mw.bestellen, mr2, zeilen, self.fahrzeug, 1)
		self.assertEqual(
			sorted(b["lieferant"] for b in r2["bestellungen"]), sorted([self.lieferant, zweiter])
		)
		self.assertTrue(all(b["docstatus"] == 1 and b["lager"] == self.fahrzeug for b in r2["bestellungen"]))

	def test_lieferung_teilmengen_und_bestand(self):
		mr = self.materialbedarf()
		po = self.bestellung(mr)["bestellungen"][0]["name"]
		vor_a, vor_b = bin_menge(ART_A, self.haupt), bin_menge(ART_B, self.haupt)
		fz_a, fz_b = bin_menge(ART_A, self.fahrzeug), bin_menge(ART_B, self.fahrzeug)
		v = _als(BUERO, mw.lieferung_vorbereiten, po)
		self.assertEqual(
			[(p["item_code"], p["bestellt"], p["geliefert"], p["offen"]) for p in v["positionen"]],
			[(ART_A, 10, 0, 10), (ART_B, 4, 0, 4)],
		)
		self.assertEqual(v["positionen"][0]["eans"], [EAN_A])
		self.assertTrue(v["darf_buchen"])
		zeile_a = v["positionen"][0]["purchase_order_item"]
		zeile_b = v["positionen"][1]["purchase_order_item"]
		# zu viel geliefert → Fehler
		with self.assertRaises(mw.MaterialwirtschaftFehler):
			_als(BUERO, mw.lieferung_buchen, po, [{"purchase_order_item": zeile_a, "menge": 11}])
		# Teillieferung: 6 von 10 Kg, Dichtband noch nicht
		r = _als(
			BUERO,
			mw.lieferung_buchen,
			po,
			[{"purchase_order_item": zeile_a, "menge": 6}, {"purchase_order_item": zeile_b, "menge": 0}],
			None,
			1,
			"LS-4711",
		)
		self.assertEqual((r["docstatus"], r["vollstaendig"], r["offen"]), (1, False, 2))
		self.assertEqual(
			[(p["item_code"], p["menge"], p["lager"]) for p in r["positionen"]], [(ART_A, 6, self.haupt)]
		)
		self.assertEqual(bin_menge(ART_A, self.haupt), vor_a + 6)
		self.assertEqual(bin_menge(ART_B, self.haupt), vor_b)
		pr = frappe.get_doc("Purchase Receipt", r["lieferung"])
		self.assertEqual(
			(pr.supplier_delivery_note, pr.project, pr.items[0].project),
			("LS-4711", self.projekt, self.projekt),
		)
		self.assertAlmostEqual(
			flt(frappe.db.get_value("Purchase Order", po, "per_received")), 6 / 14 * 100, places=1
		)
		# Rest ohne Zeilenangabe (alles Offene) ins Fahrzeuglager
		v2 = _als(BUERO, mw.lieferung_vorbereiten, po)
		self.assertEqual([(p["item_code"], p["offen"]) for p in v2["positionen"]], [(ART_A, 4), (ART_B, 4)])
		r2 = _als(BUERO, mw.lieferung_buchen, po, None, self.fahrzeug, 1)
		self.assertEqual((r2["vollstaendig"], r2["offen"], r2["status"]), (True, 0, "To Bill"))
		self.assertEqual(bin_menge(ART_A, self.fahrzeug), fz_a + 4)
		self.assertEqual(bin_menge(ART_B, self.fahrzeug), fz_b + 4)
		self.assertEqual(_als(BUERO, mw.lieferung_vorbereiten, po)["positionen"], [])  # vollständig geliefert
		# Scan: EAN → Artikel
		self.assertEqual(_als(BUERO, mw.ean_suchen, EAN_B)["item_code"], ART_B)
		self.assertIsNone(_als(BUERO, mw.ean_suchen, "0000000000000"))
		# Nachkalkulation: Wareneingang und Umlagerung sind kein Verbrauch
		self.assertEqual(nk.ist_material(self.projekt), [])
		umlagerung = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Transfer",
				"purpose": "Material Transfer",
				"company": self.firma,
				"project": self.projekt,
				"items": [
					{
						"item_code": ART_A,
						"qty": 2,
						"s_warehouse": self.haupt,
						"t_warehouse": self.fahrzeug,
						"conversion_factor": 1,
					}
				],
			}
		)
		umlagerung.insert(ignore_permissions=True)
		umlagerung.submit()
		self.assertEqual(nk.ist_material(self.projekt), [])
		entnahme = m.entnahme_buchen(
			idempotency_key=frappe.generate_hash(length=32),
			warehouse=self.fahrzeug,
			employee=self.employee,
			project=self.projekt,
			task=None,
			positionen=[{"item_code": ART_A, "menge": 1}],
		)
		self.assertEqual(entnahme["docstatus"], 1)
		ist = nk.ist_material(self.projekt)
		self.assertEqual([(z.item_code, flt(z.menge)) for z in ist], [(ART_A, 1)])

	def test_prozessleiste_stand(self):
		self.assertIsNone(_als(BUERO, mw.stand, self.projekt))  # ohne Materialanforderung keine Pille
		mr = self.materialbedarf()
		s = _als(BUERO, mw.stand, self.projekt)
		self.assertEqual(
			(s["offen"], s["bestellt"], s["geliefert"], s["ampel"], s["material_request"], s["bestellbar"]),
			(2, 0, 0, "gelb", mr, mr),
		)
		self.assertEqual(s["text"], "Material: 2 offen, 0 bestellt, 0 geliefert")
		leiste = _als(BUERO, status, "Project", self.projekt)
		self.assertEqual(leiste["material"]["offen"], 2)
		self.assertEqual(_als(BUERO, status, "Sales Order", self.auftrag)["material"]["offen"], 2)
		# Monteur: keine Pille (Rolle), Meister: Pille ohne „Bestellen“ (kein Recht auf Bestellungen)
		self.assertIsNone(_als(MONTEUR, status, "Project", self.projekt)["material"])
		s_meister = _als(MEISTER, mw.stand, self.projekt)
		self.assertEqual((s_meister["offen"], s_meister["bestellbar"]), (2, None))
		po = self.bestellung(mr)["bestellungen"][0]["name"]
		s = _als(BUERO, mw.stand, self.projekt)
		self.assertEqual(
			(s["offen"], s["bestellt"], s["geliefert"], s["ampel"], s["bestellbar"]), (0, 2, 0, "grau", None)
		)
		_als(BUERO, mw.lieferung_buchen, po, None, None, 1)
		s = _als(BUERO, mw.stand, self.projekt)
		self.assertEqual((s["offen"], s["bestellt"], s["geliefert"], s["ampel"]), (0, 0, 2, "grün"))
		pm = _als(BUERO, mw.projekt_material, self.projekt)
		self.assertEqual(
			[(a["name"], a["offen"], a["positionen"]) for a in pm["anforderungen"]], [(mr, 0, 2)]
		)

	def test_fahrzeug_beladen(self):
		mr = self.materialbedarf({ART_A: 10, ART_B: 4})
		po = self.bestellung(mr)["bestellungen"][0]["name"]
		_als(BUERO, mw.lieferung_buchen, po, None, self.haupt, 1)  # 10 Kg und 4 m im Hauptlager
		task = self.arbeitspaket_fuer_monteur()
		fz_a_vor = bin_menge(ART_A, self.fahrzeug)
		v = _als(MEISTER, mw.fahrzeug_vorschlag, self.employee)
		self.assertEqual(
			(v["fahrzeuglager"], v["hauptlager"], v["projekte"]), (self.fahrzeug, self.haupt, [self.projekt])
		)
		je = {z["item_code"]: z for z in v["zeilen"]}
		self.assertEqual(
			(je[ART_A]["bedarf"], je[ART_A]["fahrzeug"], je[ART_A]["hauptlager"]),
			(10, fz_a_vor, bin_menge(ART_A, self.haupt)),
		)
		self.assertEqual(je[ART_A]["vorschlag"], min(max(10 - fz_a_vor, 0), bin_menge(ART_A, self.haupt)))
		self.assertEqual(je[ART_B]["bedarf"], 4)
		self.assertTrue(v["darf_beladen"])
		# Entnahme senkt den Bedarf: 3 Kg schon auf der Baustelle verbraucht → Bedarf 7
		fahrzeug_vorher = bin_menge(ART_A, self.fahrzeug)
		if fahrzeug_vorher < 3:
			_als(
				MEISTER,
				mw.fahrzeug_beladen,
				self.employee,
				[{"item_code": ART_A, "menge": 3 - fahrzeug_vorher}],
			)
		m.entnahme_buchen(
			idempotency_key=frappe.generate_hash(length=32),
			warehouse=self.fahrzeug,
			employee=self.employee,
			project=self.projekt,
			task=task,
			positionen=[{"item_code": ART_A, "menge": 3}],
		)
		v = _als(MEISTER, mw.fahrzeug_vorschlag, self.employee)
		self.assertEqual({z["item_code"]: z["bedarf"] for z in v["zeilen"]}[ART_A], 7)
		# Beladen bucht die Umlagerung
		haupt_vor, fz_vor = bin_menge(ART_B, self.haupt), bin_menge(ART_B, self.fahrzeug)
		r = _als(MEISTER, mw.fahrzeug_beladen, self.employee, [{"item_code": ART_B, "menge": 4}])
		self.assertEqual(
			(r["docstatus"], r["hauptlager"], r["fahrzeuglager"]), (1, self.haupt, self.fahrzeug)
		)
		self.assertEqual(r["positionen"][0]["fahrzeug_nachher"], fz_vor + 4)
		self.assertEqual(bin_menge(ART_B, self.haupt), haupt_vor - 4)
		se = frappe.get_doc("Stock Entry", r["stock_entry"])
		self.assertEqual((se.purpose, se.get(m.MITARBEITER_FELD)), ("Material Transfer", self.employee))
		self.assertEqual(
			nk.ist_material(self.projekt), [z for z in nk.ist_material(self.projekt) if z.item_code == ART_A]
		)
		# Außerhalb des Zeitraums: keine Baustelle, kein Bedarf
		v = _als(MEISTER, mw.fahrzeug_vorschlag, self.employee, add_days(today(), 30), add_days(today(), 37))
		self.assertEqual(v["projekte"], [])
		self.assertEqual({z["item_code"]: z["bedarf"] for z in v["zeilen"]}.get(ART_B), 0)
		# Mehr als im Hauptlager → ERPNext lehnt ab
		with self.assertRaises(frappe.ValidationError):
			_als(MEISTER, mw.fahrzeug_beladen, self.employee, [{"item_code": ART_B, "menge": 9999}])

	def test_mindestbestand_und_nachbestellen(self):
		mr = self.materialbedarf({ART_A: 10})
		po = self.bestellung(mr)["bestellungen"][0]["name"]
		_als(BUERO, mw.lieferung_buchen, po, None, self.haupt, 1)
		_als(MEISTER, mw.fahrzeug_beladen, self.employee, [{"item_code": ART_A, "menge": 2}])
		# Mindestbestand 20 auf dem Fahrzeug (Art automatisch: Umlagerung), Nachbestellmenge 30
		r = _als(BUERO, mw.mindestbestand_setzen, ART_A, self.fahrzeug, 20, 30)
		self.assertEqual((r["art"], r["mindestbestand"], r["nachbestellmenge"]), ("Transfer", 20, 30))
		zeile = frappe.get_all(
			"Item Reorder",
			filters={"parent": ART_A, "warehouse": self.fahrzeug},
			fields=["warehouse_reorder_level", "material_request_type"],
		)
		self.assertEqual(
			[(flt(z.warehouse_reorder_level), z.material_request_type) for z in zeile], [(20, "Transfer")]
		)
		# Hauptlager: Bestellung beim Lieferanten
		r = _als(BUERO, mw.mindestbestand_setzen, ART_B, self.haupt, 50, 0)
		self.assertEqual(r["art"], "Purchase")
		daten = _als(BUERO, mw.nachbestellen, {"lager": self.fahrzeug})
		self.assertEqual(len(daten["zeilen"]), 1)
		z = daten["zeilen"][0]
		self.assertEqual(
			(z["item_code"], z["lager"], z["bestand"], z["mindestbestand"], z["art"], z["unter"]),
			(ART_A, self.fahrzeug, bin_menge(ART_A, self.fahrzeug), 20, "Transfer", True),
		)
		self.assertEqual(z["nachbestellmenge"], 30)  # verfügbar + 30 > 20 → Nachbestellmenge
		self.assertIsNone(z["lieferant"])
		self.assertEqual(
			r["nachbestellmenge"], 50
		)  # ohne Angabe: Mindestbestand (ERPNext verlangt eine Menge)
		haupt_zeile = next(x for x in _als(BUERO, mw.nachbestellen, {})["zeilen"] if x["item_code"] == ART_B)
		verf = haupt_zeile["verfuegbar"]
		erwartet = 50 + (50 - verf if verf + 50 <= 50 else 0)  # Rechenweg des ERPNext-Jobs
		self.assertEqual((haupt_zeile["art"], haupt_zeile["nachbestellmenge"]), ("Purchase", erwartet))
		# alles anzeigen (auch über Mindestbestand) listet beide
		self.assertEqual(
			sorted(
				x["item_code"]
				for x in _als(BUERO, mw.nachbestellen, {"nur_unter": 0})["zeilen"]
				if x["item_code"] in (ART_A, ART_B)
			),
			sorted([ART_A, ART_B]),
		)
		# Bedarf anlegen: Umlagerung Hauptlager → Fahrzeug und Bestellung ins Hauptlager
		r = _als(
			BUERO,
			mw.nachbestellung_anlegen,
			[
				{"item_code": ART_A, "lager": self.fahrzeug, "menge": 30, "art": "Transfer"},
				{"item_code": ART_B, "lager": self.haupt, "menge": 50, "art": "Purchase"},
			],
		)
		self.assertEqual(
			sorted((x["typ"], x["lager"], x["quelle"], x["positionen"]) for x in r["material_requests"]),
			sorted([("Material Transfer", self.fahrzeug, self.haupt, 1), ("Purchase", self.haupt, None, 1)]),
		)
		umlagerung = next(x for x in r["material_requests"] if x["typ"] == "Material Transfer")
		doc = frappe.get_doc("Material Request", umlagerung["name"])
		self.assertEqual(
			(doc.docstatus, doc.set_from_warehouse, doc.items[0].from_warehouse, flt(doc.items[0].qty)),
			(0, self.haupt, self.haupt, 30),
		)
		# Eintrag löschen
		r = _als(BUERO, mw.mindestbestand_setzen, ART_A, self.fahrzeug, 0, 0)
		self.assertIsNone(r["art"])
		self.assertFalse(frappe.db.exists("Item Reorder", {"parent": ART_A, "warehouse": self.fahrzeug}))
		# Automatik bleibt aus
		self.assertEqual(int(frappe.db.get_single_value("Stock Settings", "auto_indent") or 0), 0)
