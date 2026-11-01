"""Leistungskatalog Abdichtung (Aufgabe 2.3): Vorlagen, Frappe Data Import, Importer (idempotent,
kein Überschreiben ohne force), Pflichtfelder, BOM-Kosten, Preisliste, Angebot aus drei Positionen.

Reihenfolge in der CI: Die Klasse spielt den Katalog zuerst über Frappe **Data Import** ein
(wie im Desk, Datei für Datei), danach muss der Importer auf demselben Stand ``changes: []``
melden - beide Wege erzeugen also denselben Katalog. Data Import committet zeilenweise; der
Katalog bleibt auf der Testsite stehen (der CI-Schritt nach den Tests prüft ihn per
``bench execute`` erneut). Tests, die Daten ändern, rollen auf einen Savepoint zurück.
"""

from __future__ import annotations

import contextlib
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import flt, today
from frappe.utils.nestedset import get_root_of

from corevatis_erpnext.katalog import importieren, list_pakete, uebersicht
from corevatis_erpnext.katalog.importer import (
	BARCODE_DATEI,
	DATEIEN,
	POSITIONSREGEL_DATEI,
	Bericht,
	_abweichungen,
	_barcodes,
	lese_barcodes,
	lese_csv,
	nachziehen,
	paket_dir,
	standard_preislisten,
)

PAKET = "abdichtung"
FIRMA = "Hülshoff Sanierungstechnik"
VERKAUF = "Standard-Verkauf"
EINKAUF = "Standard-Einkauf"
STUNDENSATZ = Decimal("48")  # Workstation „Montage“ (Annahme)

# Select-Werte der Item-Felder aus Aufgabe 2.2 (Schnittstelle, siehe docs/2.3-leistungskatalog.md)
GEWERKE = ("Bauwerksabdichtung", "Sanierputz", "Injektion", "Drainage", "Sonstiges")
VERFAHREN = (
	"Außenabdichtung",
	"Innenabdichtung",
	"Horizontalsperre Injektion",
	"Sanierputz",
	"Spezialabdichtung",
	"Sonstiges",
)
REGELN = ("wandflaeche_netto", "laufmeter", "grabvolumen", "stueck", "pauschale", "bodenflaeche")
CV_FELDER = ("cv_gewerk", "cv_verfahren", "cv_leistungsregel")

# Drei Beispiel-Stücklisten, von Hand gerechnet (Rechenweg in docs/2.3-leistungskatalog.md):
# Material = Σ Verbrauch * Einkaufspreis, Montage = Minuten * 48 €/h / 60, HK = Material + Montage
BEISPIELE = {
	# Innenabdichtung: 4,5 kg Dichtschlämme * 1,60 = 7,20; 22 min * 0,80 = 17,60
	"IA-05": {"material": 7.20, "montage": 17.60, "hk": 24.80, "vk": 35.96},
	# Außenabdichtung: 6 l PMBC * 3,80 = 22,80 + 1,1 m² Gewebe * 2,50 = 2,75; 28 min * 0,80 = 22,40
	"AA-09": {"material": 25.55, "montage": 22.40, "hk": 47.95, "vk": 69.53},
	# Horizontalsperre: 0,3 l Creme * 19,50 = 5,85 + 1 psch Kleinmaterial * 1,00; 20 min * 0,80 = 16,00
	"HS-02": {"material": 6.85, "montage": 16.00, "hk": 22.85, "vk": 33.13},
}
# Angebot: 18 m² IA-05, 24 m² AA-09, 12 lfm HS-02
ANGEBOT = (("IA-05", 18), ("AA-09", 24), ("HS-02", 12))
ANGEBOT_NETTO = 2713.56  # 647,28 + 1.668,72 + 397,56
ANGEBOT_HK = 1871.40  # 446,40 + 1.150,80 + 274,20
ANGEBOT_UST = 515.58  # 19 % von 2.713,56 (515,5764)


def zeilen(datei: str) -> list[dict]:
	header, rows = lese_csv(paket_dir(PAKET) / datei)
	return [dict(zip(header, row, strict=True)) for row in rows]


def boms_aus_vorlage() -> dict[str, dict]:
	"""BOM-CSV zu {item: {"minuten": .., "material": [(code, menge, uom)]}}."""
	out: dict[str, dict] = {}
	aktuell = None
	for z in zeilen("08_bom.csv"):
		if z["item"]:
			aktuell = out.setdefault(z["item"], {"minuten": None, "material": [], "kopf": z})
		if z["items.item_code"]:
			aktuell["material"].append((z["items.item_code"], z["items.qty"], z["items.uom"]))
		if z["operations.time_in_mins"]:
			aktuell["minuten"] = z["operations.time_in_mins"]
	return out


def preise(liste: str) -> dict[str, Decimal]:
	return {
		z["item_code"]: Decimal(z["price_list_rate"])
		for z in zeilen("07_item_price.csv")
		if z["price_list"] == liste
	}


def herstellkosten(bom: dict, ek: dict[str, Decimal]) -> tuple[Decimal, Decimal, Decimal]:
	"""Erwartete Kosten je Einheit wie ERPNext: Materialzeile auf Cent gerundet, Lohn aus Minuten."""
	cent = Decimal("0.01")
	material = sum(
		(
			(ek[code] * Decimal(menge)).quantize(cent, rounding=ROUND_HALF_UP)
			for code, menge, _uom in bom["material"]
		),
		Decimal(0),
	)
	montage = (STUNDENSATZ * Decimal(bom["minuten"]) / 60).quantize(cent, rounding=ROUND_HALF_UP)
	return material, montage, material + montage


class TestKatalogVorlagen(UnitTestCase):
	"""Vorlagen ohne Datenbank: Umfang, Konsistenz, Schnittstellenwerte, Zahlenformat."""

	def setUp(self):
		self.items = {z["item_code"]: z for z in zeilen("06_item.csv")}
		self.boms = boms_aus_vorlage()
		self.vk = preise(VERKAUF)
		self.ek = preise(EINKAUF)

	def test_paket_vorhanden(self):
		self.assertEqual(list_pakete(), [PAKET])
		for _doctype, datei in DATEIEN:
			self.assertTrue((paket_dir(PAKET) / datei).is_file(), datei)

	def test_umfang(self):
		leistungen = [c for c, z in self.items.items() if z["is_sales_item"] == "1"]
		material = [c for c, z in self.items.items() if z["is_stock_item"] == "1"]
		# Plan 2.3: ca. 30-50 Leistungen; seit Handwerker#71 51 (IA-10 Innenwand)
		self.assertGreaterEqual(len(self.boms), 30)
		self.assertEqual(len(leistungen), 51)
		self.assertEqual(len(self.boms), 49)
		self.assertEqual(len(material), 32)
		# jedes Verfahren aus 2.2 hat Leistungspositionen mit Stückliste („Sonstiges“: Allgemeines)
		verfahren = {self.items[c]["cv_verfahren"] for c in self.boms}
		self.assertEqual(verfahren, set(VERFAHREN))

	def test_leistungen_vollstaendig(self):
		gruppen = {z["item_group_name"] for z in zeilen("02_item_group.csv")}
		einheiten = {z["uom_name"] for z in zeilen("01_uom.csv")} | {"Kg"}  # Kg aus dem ERPNext-Stamm
		for code, z in self.items.items():
			self.assertIn(z["item_group"], gruppen, code)
			self.assertIn(z["stock_uom"], einheiten, code)
			if z["is_sales_item"] == "1":
				self.assertEqual(z["is_stock_item"], "0", code)
				self.assertIn(z["cv_gewerk"], GEWERKE, code)
				self.assertIn(z["cv_verfahren"], VERFAHREN, code)
				self.assertIn(z["cv_leistungsregel"], REGELN, code)
				self.assertIn(code, self.vk, f"{code}: Verkaufspreis fehlt")
				if z["item_group"] != "Arbeitszeit":
					self.assertIn(code, self.boms, f"{code}: Stückliste fehlt")
			else:
				self.assertEqual(
					z["is_stock_item"], "1", f"{code}: Material muss Lagerartikel sein (BOM-Kosten)"
				)
				self.assertIn(code, self.ek, f"{code}: Einkaufspreis fehlt")
		for z in zeilen("07_item_price.csv"):
			self.assertEqual(z["uom"], self.items[z["item_code"]]["stock_uom"], z["item_code"])
			self.assertEqual(z["reference"], "ANNAHME 2.3", z["item_code"])

	def test_leistungsregeln(self):
		"""Jede an einer Leistung genannte Regel steht in der Regel-Vorlage (für Sites mit corevatis)."""
		regeln = {z["regel_name"]: z for z in zeilen("00_leistungsregel.csv")}
		self.assertEqual(set(regeln), set(REGELN))
		genutzt = {z["cv_leistungsregel"] for z in self.items.values() if z["cv_leistungsregel"]}
		self.assertLessEqual(genutzt, set(regeln))
		regelarten = {
			"wandflaeche_netto": "Wandfläche netto bis Höhe",
			"laufmeter": "Laufmeter",
			"grabvolumen": "m³ Grabvolumen",
			"stueck": "Stück",
			"pauschale": "Pauschale",
			"bodenflaeche": "Bodenfläche",
		}
		for name, z in regeln.items():
			self.assertEqual(z["regelart"], regelarten[name])
		# Bodenabdichtung (Review Epic 2, D-1): Fläche aus der Regelart bodenflaeche
		boden = sorted(c for c, z in self.items.items() if z["cv_leistungsregel"] == "bodenflaeche")
		self.assertEqual(boden, ["IA-08", "IA-09"])
		for code in boden:
			self.assertEqual(self.items[code]["stock_uom"], "m²", code)
			self.assertIn(code, self.boms, code)

	def test_positionsgenaue_regeln(self):
		"""Handwerker#71: Regeln der Innenabdichtung je Wandtyp - eine Leistung je Wand, keine Lücke für
		Innenwände, keine doppelte Dichtschlämme; jede Regel nennt eine Katalogleistung mit Stückliste."""
		allgemein = {z["regel_name"] for z in zeilen("00_leistungsregel.csv")}
		regeln = zeilen(POSITIONSREGEL_DATEI)
		self.assertEqual(
			list(regeln[0]),
			[
				"regel_name",
				"aktiv",
				"gewerk",
				"verfahren",
				"regelart",
				"item_code",
				"parameter",
				"beschreibung",
			],
		)
		erlaubt = {  # Parameter der Engine (corevatis.engine.regeln.PARAMETER), die der Katalog nutzt
			"Wandfläche netto bis Höhe": {"wandtyp", "max_hoehe_m"},
			"Bodenfläche": set(),
		}
		je_item = {}
		for z in regeln:
			code = z["item_code"]
			self.assertNotIn(z["regel_name"], allgemein)
			self.assertEqual(z["aktiv"], "1")
			self.assertIn(code, self.boms, f"{code}: Stückliste fehlt")
			self.assertEqual(z["verfahren"], self.items[code]["cv_verfahren"], code)
			self.assertEqual(z["gewerk"], self.items[code]["cv_gewerk"], code)
			self.assertEqual(self.items[code]["stock_uom"], "m²", code)
			params = json.loads(z["parameter"]) if z["parameter"] else {}
			self.assertLessEqual(set(params), erlaubt[z["regelart"]], z["regel_name"])
			self.assertNotIn(code, je_item, f"{code}: zwei Regeln - der Generator nimmt nur eine")
			je_item[code] = params
		self.assertEqual(sorted(je_item), ["IA-05", "IA-08", "IA-10"])
		self.assertEqual(je_item["IA-05"], {"wandtyp": "aussen", "max_hoehe_m": 1.5})
		self.assertEqual(je_item["IA-10"], {"wandtyp": "innen", "max_hoehe_m": 1.5})
		self.assertEqual(je_item["IA-08"], {})
		self.assertEqual(len({z["regel_name"] for z in regeln}), len(regeln))

	def test_abweichungen_json_inhaltlich(self):
		"""Der Kern speichert ``parameter`` eingerückt und sortiert: kein „weicht ab“ bei gleichem Inhalt."""
		doc = frappe._dict(parameter='{\n "max_hoehe_m": 1.5,\n "wandtyp": "innen"\n}', item_code="IA-10")
		soll = {"parameter": '{"wandtyp": "innen", "max_hoehe_m": 1.5}', "item_code": "IA-10"}
		self.assertEqual(_abweichungen(doc, soll, json_felder={"parameter"}), [])
		self.assertEqual(_abweichungen(doc, soll), ["parameter"])
		soll["parameter"] = '{"wandtyp": "aussen", "max_hoehe_m": 1.5}'
		self.assertEqual(_abweichungen(doc, soll, json_felder={"parameter"}), ["parameter"])
		leer = frappe._dict(parameter=None)
		self.assertEqual(_abweichungen(leer, {"parameter": None}, json_felder={"parameter"}), [])
		self.assertEqual(_abweichungen(leer, {"parameter": "{}"}, json_felder={"parameter"}), [])

	def test_stuecklisten_konsistent(self):
		for item, bom in self.boms.items():
			self.assertEqual(self.items[item]["is_sales_item"], "1", item)
			self.assertEqual(bom["kopf"]["company"], FIRMA)
			self.assertEqual(bom["kopf"]["rm_cost_as_per"], "Price List")
			self.assertEqual(bom["kopf"]["buying_price_list"], EINKAUF)
			self.assertEqual(bom["kopf"]["operations.operation"], "Montage")
			self.assertGreater(float(bom["minuten"]), 0, item)
			self.assertTrue(bom["material"], item)
			for code, menge, uom in bom["material"]:
				self.assertEqual(self.items[code]["is_stock_item"], "1", f"{item}: {code}")
				self.assertEqual(uom, self.items[code]["stock_uom"], f"{item}: {code}")
				self.assertGreater(float(menge), 0)
			_mat, _lohn, hk = herstellkosten(bom, self.ek)
			self.assertGreater(self.vk[item], hk, f"{item}: Verkaufspreis unter Herstellkosten")

	def test_verkaufspreis_aus_herstellkosten_der_stueckliste(self):
		"""Jede Preisnotiz „Herstellkosten X EUR x Faktor 1.45“ muss zur Stückliste passen
		(Verifikation Epic 2: eine BOM-Folgezeile war in die falsche Stückliste gerutscht)."""
		muster = re.compile(r"Herstellkosten ([0-9.]+) EUR x Faktor ([0-9.]+)")
		geprueft = 0
		for z in zeilen("07_item_price.csv"):
			if z["price_list"] != VERKAUF or z["item_code"] not in self.boms:
				continue
			treffer = muster.search(z["note"])
			self.assertTrue(treffer, f"{z['item_code']}: Notiz ohne Herstellkosten")
			notiz_hk, faktor = Decimal(treffer.group(1)), Decimal(treffer.group(2))
			_mat, _lohn, hk = herstellkosten(self.boms[z["item_code"]], self.ek)
			self.assertEqual(hk, notiz_hk, z["item_code"])
			self.assertEqual(
				Decimal(z["price_list_rate"]),
				(hk * faktor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
				z["item_code"],
			)
			geprueft += 1
		self.assertEqual(geprueft, len(self.boms))

	def test_beispiele_von_hand(self):
		for item, soll in BEISPIELE.items():
			material, montage, hk = herstellkosten(self.boms[item], self.ek)
			self.assertEqual(float(material), soll["material"], item)
			self.assertEqual(float(montage), soll["montage"], item)
			self.assertEqual(float(hk), soll["hk"], item)
			self.assertEqual(float(self.vk[item]), soll["vk"], item)
		netto = sum(Decimal(str(BEISPIELE[c]["vk"])) * m for c, m in ANGEBOT)
		hk = sum(Decimal(str(BEISPIELE[c]["hk"])) * m for c, m in ANGEBOT)
		self.assertEqual(float(netto), ANGEBOT_NETTO)
		self.assertEqual(float(hk), ANGEBOT_HK)
		ust = (netto * Decimal("0.19")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
		self.assertEqual(float(ust), ANGEBOT_UST)

	def test_zahlen_eindeutig_in_deutschem_format(self):
		"""Data Import liest Zahlen im Format der Site (#.###,##); „1.250“ wäre mehrdeutig."""
		try:
			from frappe.core.doctype.data_import.importer import _parse_number
			from frappe.utils.number_format import NumberFormat
		except ImportError:
			self.skipTest("Frappe ohne _parse_number")
		formate = [NumberFormat.from_string(f) for f in ("#.###,##", "#,###.##")]
		spalten = {
			"07_item_price.csv": ("price_list_rate",),
			"08_bom.csv": ("items.qty", "operations.time_in_mins", "quantity"),
			"04_workstation.csv": ("workstation_costs.operating_cost",),
		}
		for datei, felder in spalten.items():
			for z in zeilen(datei):
				for feld in felder:
					if z[feld]:
						for fmt in formate:
							self.assertEqual(
								_parse_number(z[feld], fmt), float(z[feld]), f"{datei} {feld}={z[feld]}"
							)


class TestKatalogImport(IntegrationTestCase):
	data_imports: dict[str, frappe._dict] | None = None
	vorhandene_einheiten: set[str] = frozenset()

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Company", FIRMA):
			raise AssertionError(f"Firma {FIRMA} fehlt - Site-Template huelshoff vor den Tests anwenden (CI)")
		if not frappe.db.exists("Item", "IA-05"):
			# Einheiten, die andere Tests schon angelegt haben (test_item_felder: m²), auf den
			# Stand der Vorlage bringen - wie eine Site, deren Einheit zur Vorlage passt. Der
			# Data Import meldet für sie „existiert bereits“ (Insert New Records).
			cls.vorhandene_einheiten = set()
			for z in zeilen("01_uom.csv"):
				if frappe.db.exists("UOM", z["uom_name"]):
					cls.vorhandene_einheiten.add(z["uom_name"])
					frappe.db.set_value(
						"UOM",
						z["uom_name"],
						{
							"must_be_whole_number": int(z["must_be_whole_number"]),
							"common_code": z["common_code"],
							"description": z["description"],
						},
					)
			frappe.db.commit()
			cls.data_imports = {
				datei: cls._data_import(doctype, datei)
				for doctype, datei in DATEIEN
				if frappe.db.exists("DocType", doctype)  # Leistungsregel nur mit corevatis
			}
			# EANs (3.11, optionale 09_barcode.csv): Kindzeilen ohne eigenen Data Import - im Desk
			# am Artikel unter „Barcodes“, hier wie im Importer ergänzt
			_barcodes(PAKET, Bericht(PAKET, FIRMA, False))
			frappe.db.commit()
		# Katalog-Preislisten als Standard für neue Belege (Importer bzw. Patch v0_4); der
		# Desk-Weg über Data Import setzt sie nicht. Danach meldet der Importer keine Änderung.
		standard_preislisten()
		frappe.db.commit()

	@classmethod
	def _data_import(cls, doctype: str, datei: str) -> frappe._dict:
		"""Eine Vorlage wie im Desk über Data Import einspielen (Insert New Records)."""
		from frappe.core.doctype.data_import.data_import import start_import

		datei_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"katalog-{PAKET}-{datei}",
				"content": (paket_dir(PAKET) / datei).read_bytes(),
				"is_private": 1,
			}
		)
		datei_doc.save(ignore_permissions=True)
		data_import = frappe.new_doc("Data Import")
		data_import.reference_doctype = doctype
		data_import.import_type = "Insert New Records"
		data_import.import_file = datei_doc.file_url
		data_import.submit_after_import = 1 if doctype == "BOM" else 0
		data_import.mute_emails = 1
		data_import.insert(ignore_permissions=True)
		frappe.db.commit()  # wie im Desk: Data Import existiert vor dem Lauf

		# Artikelgruppen ohne übergeordnete Gruppe hängt ERPNext an die Wurzel - außer in Tests
		# (frappe.in_test). Für diesen Lauf Produktivverhalten wie im Desk.
		kontext = (
			patch.object(frappe, "in_test", False) if doctype == "Item Group" else contextlib.nullcontext()
		)
		with kontext:
			start_import(data_import.name)  # Funktion des Hintergrundjobs
		data_import.reload()
		fehler = frappe.get_all(
			"Data Import Log",
			filters={"data_import": data_import.name, "success": 0},
			fields=["row_indexes", "messages", "exception"],
		)
		return frappe._dict(
			status=data_import.status,
			warnings=data_import.template_warnings,
			payload_count=data_import.payload_count,
			fehler=[
				f"Zeilen {f.row_indexes}: {f.messages or ''} {(f.exception or '')[-500:]}" for f in fehler
			],
		)

	@contextlib.contextmanager
	def savepoint(self):
		frappe.db.savepoint("katalog_test")
		try:
			yield
		finally:
			frappe.db.rollback(save_point="katalog_test")
			# Dokument-Cache (Redis) ist nicht transaktional: Stand nach dem Rücksprung neu lesen
			from frappe.model.document import clear_document_cache

			for doctype in ("Item", "BOM", "Item Price"):
				clear_document_cache(doctype)

	# ------------------------------------------------------------------ Data Import

	def test_data_import_alle_vorlagen(self):
		if self.data_imports is None:
			self.skipTest("Katalog war vor dem Testlauf schon importiert")
		for datei, ergebnis in self.data_imports.items():
			if datei == "01_uom.csv" and self.vorhandene_einheiten:
				# nur die schon vorhandenen Einheiten scheitern (Dubletten), alle anderen kommen an
				self.assertEqual(ergebnis.status, "Partial Success", f"{datei}: {ergebnis}")
				self.assertEqual(len(ergebnis.fehler), len(self.vorhandene_einheiten), ergebnis.fehler)
				# Meldung in der Sprache des Laufs (Druck-Tests 4.3 rendern deutsch)
				self.assertTrue(
					all("already exists" in f or "existiert bereits" in f for f in ergebnis.fehler),
					ergebnis.fehler,
				)
				continue
			self.assertEqual(ergebnis.status, "Success", f"{datei}: {ergebnis}")
			self.assertFalse(ergebnis.fehler, datei)
			self.assertIn(ergebnis.warnings or "", ("", "[]"), datei)
		self.assertEqual(self.data_imports["08_bom.csv"].payload_count, 49)
		self.assertEqual(self.data_imports["06_item.csv"].payload_count, 83)

	def test_vorlagen_spalten_wie_template_download(self):
		"""Jede Spalte entspricht einer Spalte der Data-Import-Vorlage aus dem Desk (Exporter)."""
		from frappe.core.doctype.data_import.exporter import Exporter
		from frappe.core.doctype.data_import.importer import get_df_for_column_header

		def feld(doctype, spalte):
			df = get_df_for_column_header(doctype, spalte)
			self.assertIsNotNone(df, f"{doctype}: Spalte {spalte!r} passt zu keinem Feld")
			tabelle = df.child_table_df.fieldname if df.get("is_child_table_field") else ""
			return (tabelle, df.fieldname)

		for doctype, datei in DATEIEN:
			if not frappe.db.exists("DocType", doctype):
				continue
			header, _rows = lese_csv(paket_dir(PAKET) / datei)
			export_fields: dict[str, list[str]] = {}
			for spalte in header:
				tabelle, _, name = spalte.rpartition(".")
				export_fields.setdefault(tabelle or doctype, []).append(name)
			exporter = Exporter(doctype, export_fields=export_fields, export_data=False)
			vorlage = exporter.get_csv_array()[0]
			self.assertEqual(len(vorlage), len(header), f"{datei}: {vorlage}")
			# Felder der Vorlagen-Spalten (Beschriftungen sind übersetzt, die Felder nicht)
			vorlagen_felder = [
				(df.child_table_df.fieldname if df.is_child_table_field else "", df.fieldname)
				for df in exporter.fields
			]
			self.assertEqual(sorted(feld(doctype, s) for s in header), sorted(vorlagen_felder), datei)

	def test_pflichtfelder(self):
		meta = frappe.get_meta("Item")
		for feld in CV_FELDER:
			self.assertTrue(meta.has_field(feld), f"Item.{feld} fehlt (Aufgabe 2.2)")
		for feld, werte in (("cv_gewerk", GEWERKE), ("cv_verfahren", VERFAHREN)):
			optionen = [o for o in (meta.get_field(feld).options or "").split("\n") if o]
			self.assertEqual(set(werte) - set(optionen), set(), feld)
		for z in zeilen("06_item.csv"):
			item = frappe.db.get_value(
				"Item", z["item_code"], ["item_group", "stock_uom", "is_stock_item", *CV_FELDER], as_dict=True
			)
			self.assertIsNotNone(item, z["item_code"])
			self.assertEqual((item.item_group, item.stock_uom), (z["item_group"], z["stock_uom"]))
			if z["is_sales_item"] == "1":
				for feld in CV_FELDER:
					self.assertEqual(item[feld], z[feld], f"{z['item_code']}.{feld}")
		wurzel = get_root_of("Item Group")
		for gruppe in ("Leistungen Abdichtung", "Material Abdichtung", "Arbeitszeit"):
			self.assertEqual(frappe.db.get_value("Item Group", gruppe, "parent_item_group"), wurzel)
		self.assertEqual(
			frappe.db.get_value("Item Group", "Innenabdichtung", "parent_item_group"), "Leistungen Abdichtung"
		)
		self.assertEqual(flt(frappe.db.get_value("Workstation", "Montage", "hour_rate")), float(STUNDENSATZ))
		# Kostenkomponente legt die App an (Doctype ohne Data Import)
		self.assertTrue(frappe.db.exists("Workstation Operating Component", "Lohn Montage"))

	# ------------------------------------------------------------------ Importer

	def test_importer_ohne_aenderungen(self):
		"""Auf dem per Data Import eingespielten Stand: nichts anzulegen, nichts abweichend."""
		for _lauf in range(2):
			ergebnis = importieren(firma=FIRMA, commit=False)
			self.assertEqual(ergebnis["changes"], [])
			self.assertEqual(ergebnis["skipped"], [])
			self.assertEqual(ergebnis["warnings"], [])
			if not frappe.db.exists("DocType", "Leistungsregel"):
				# allgemeine (00_) und positionsgenaue Regeln (10_, Handwerker#71)
				self.assertEqual(len(ergebnis["optional_uebersprungen"]), 2)
			# 8 UOM + 9 Item Group + 2 Price List + 1 Workstation + 1 Operation
			# + 83 Item + 83 Item Price + 49 BOM + 29 Barcodes (3.11)
			self.assertEqual(ergebnis["unveraendert"], 265)

	def test_barcodes(self):
		"""3.11: EAN-13 je Material (interner GS1-Bereich 20…), gültige Prüfziffer, nur Lagerartikel."""
		zeilen_bc = lese_barcodes(PAKET)
		self.assertTrue((paket_dir(PAKET) / BARCODE_DATEI).is_file())
		self.assertEqual(len(zeilen_bc), 29)
		self.assertEqual(len({z["barcode"] for z in zeilen_bc}), 29)
		for z in zeilen_bc:
			code = z["barcode"]
			self.assertRegex(code, r"^20\d{11}$")
			summe = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(code[:12]))
			self.assertEqual(int(code[12]), (10 - summe % 10) % 10, code)
			self.assertEqual(frappe.db.get_value("Item Barcode", {"barcode": code}, "parent"), z["item_code"])
			self.assertEqual(frappe.db.get_value("Item", z["item_code"], "is_stock_item"), 1)
		with self.savepoint():
			# Barcode an einem anderen Artikel: nicht übernommen, kein Überschreiben
			erster = zeilen_bc[0]
			frappe.db.delete("Item Barcode", {"barcode": erster["barcode"]})
			item = frappe.get_doc("Item", zeilen_bc[1]["item_code"])
			item.append("barcodes", {"barcode": erster["barcode"], "barcode_type": "EAN"})
			item.save(ignore_permissions=True)
			ergebnis = importieren(firma=FIRMA, commit=False)
			self.assertEqual(ergebnis["changes"], [])
			self.assertEqual(len(ergebnis["skipped"]), 1, ergebnis["skipped"])
			self.assertIn(erster["barcode"], ergebnis["skipped"][0])
			# fehlt er ganz, ergänzt ihn der Importer
			frappe.db.delete("Item Barcode", {"barcode": erster["barcode"]})
			ergebnis = importieren(firma=FIRMA, commit=False)
			self.assertEqual(
				ergebnis["changes"], [f"Item {erster['item_code']}: Barcode {erster['barcode']} ergänzt"]
			)

	def test_importer_legt_fehlendes_an(self):
		with self.savepoint():
			preis = frappe.db.get_value("Item Price", {"price_list": VERKAUF, "item_code": "IA-05"}, "name")
			frappe.delete_doc("Item Price", preis, ignore_permissions=True)
			alte_bom = frappe.db.get_value("Item", "HS-02", "default_bom")
			frappe.get_doc("BOM", alte_bom).cancel()

			ergebnis = importieren(firma=FIRMA, commit=False)
			self.assertEqual(len(ergebnis["changes"]), 2, ergebnis["changes"])
			self.assertIn("Item Price IA-05 (Standard-Verkauf, m²) angelegt", ergebnis["changes"])
			self.assertEqual(
				flt(
					frappe.db.get_value(
						"Item Price", {"price_list": VERKAUF, "item_code": "IA-05"}, "price_list_rate"
					)
				),
				35.96,
			)
			neue_bom = frappe.db.get_value("Item", "HS-02", "default_bom")
			self.assertNotEqual(neue_bom, alte_bom)
			self.assertEqual(
				flt(frappe.db.get_value("BOM", neue_bom, "total_cost")), BEISPIELE["HS-02"]["hk"]
			)
			self.assertEqual(importieren(firma=FIRMA, commit=False)["changes"], [])

	def test_nachziehen_und_patch_innenwand(self):
		"""Handwerker#71: Patch ergänzt IA-10 auf einer Site mit älterem Katalog, sonst nichts."""
		from corevatis_erpnext.patches.v0_5 import katalog_innenwand

		with self.savepoint():
			self.assertEqual(nachziehen(FIRMA, ["IA-10"])["changes"], [])
			bom = frappe.db.get_value("Item", "IA-10", "default_bom")
			self.assertEqual(flt(frappe.db.get_value("BOM", bom, "total_cost")), 25.60)
			# Stand vor #71: IA-10 ohne Preis und Stückliste, IA-05 mit altem Langtext; dazu ein im
			# Desk gelöschter Preis eines anderen Artikels - den legt der Patch nicht neu an
			frappe.get_doc("BOM", bom).cancel()
			for code in ("IA-10", "IA-04"):
				name = frappe.db.get_value("Item Price", {"price_list": VERKAUF, "item_code": code}, "name")
				frappe.delete_doc("Item Price", name, ignore_permissions=True)
			frappe.db.set_value("Item", "IA-05", "description", katalog_innenwand.IA05_ALT)

			katalog_innenwand.execute()
			self.assertEqual(
				flt(
					frappe.db.get_value(
						"Item Price", {"price_list": VERKAUF, "item_code": "IA-10"}, "price_list_rate"
					)
				),
				37.12,
			)
			self.assertFalse(frappe.db.exists("Item Price", {"price_list": VERKAUF, "item_code": "IA-04"}))
			neu = frappe.db.get_value("Item", "IA-10", "default_bom")
			self.assertNotEqual(neu, bom)
			self.assertEqual(flt(frappe.db.get_value("BOM", neu, "total_cost")), 25.60)
			self.assertIn(
				"erdberührter Kelleraußenwände", frappe.db.get_value("Item", "IA-05", "description")
			)
			# zweiter Lauf: nichts mehr zu tun; im Desk geänderter Langtext bleibt
			frappe.db.set_value("Item", "IA-05", "description", "Eigener Text")
			katalog_innenwand.execute()
			self.assertEqual(nachziehen(FIRMA, ["IA-10"])["changes"], [])
			self.assertEqual(frappe.db.get_value("Item", "IA-05", "description"), "Eigener Text")

	def test_kein_ueberschreiben_ohne_force(self):
		with self.savepoint():
			preis = frappe.get_doc("Item Price", {"price_list": VERKAUF, "item_code": "IA-05"})
			preis.price_list_rate = 39.90  # im Desk geändert
			preis.save(ignore_permissions=True)

			ergebnis = importieren(firma=FIRMA, commit=False)
			self.assertEqual(ergebnis["changes"], [])
			self.assertEqual(len(ergebnis["skipped"]), 1, ergebnis["skipped"])
			self.assertIn("IA-05", ergebnis["skipped"][0])
			self.assertEqual(flt(frappe.db.get_value("Item Price", preis.name, "price_list_rate")), 39.90)

			ergebnis = importieren(firma=FIRMA, force=True, commit=False)
			self.assertEqual(len(ergebnis["changes"]), 1, ergebnis["changes"])
			self.assertIn("aktualisiert (price_list_rate)", ergebnis["changes"][0])
			self.assertEqual(flt(frappe.db.get_value("Item Price", preis.name, "price_list_rate")), 35.96)

	def test_force_neue_stuecklisten_version(self):
		with self.savepoint():
			alt = frappe.db.get_value("Item", "IA-05", "default_bom")
			eigene = frappe.copy_doc(frappe.get_doc("BOM", alt))
			eigene.items[0].qty = 5  # Hülshoff rechnet mit 5 kg/m²
			eigene.is_default = 1
			eigene.insert(ignore_permissions=True)
			eigene.submit()
			self.assertEqual(frappe.db.get_value("Item", "IA-05", "default_bom"), eigene.name)

			ergebnis = importieren(firma=FIRMA, commit=False)
			self.assertEqual(ergebnis["changes"], [])
			self.assertEqual(len(ergebnis["skipped"]), 1, ergebnis["skipped"])
			self.assertIn(eigene.name, ergebnis["skipped"][0])

			ergebnis = importieren(firma=FIRMA, force=True, commit=False)
			self.assertEqual(len(ergebnis["changes"]), 1, ergebnis["changes"])
			neu = frappe.db.get_value("Item", "IA-05", "default_bom")
			self.assertNotIn(neu, (alt, eigene.name))
			self.assertIn(f"ersetzt {eigene.name}", ergebnis["changes"][0])
			self.assertEqual(frappe.db.get_value("BOM", eigene.name, ["docstatus", "is_active"]), (1, 1))
			self.assertEqual(flt(frappe.db.get_value("BOM", neu, "total_cost")), BEISPIELE["IA-05"]["hk"])

	def test_standard_preislisten(self):
		# Befund mvp-test: neues Angebot zog „Standard Selling“, Positionen mit 0,00 €
		self.assertEqual(frappe.db.get_single_value("Selling Settings", "selling_price_list"), VERKAUF)
		self.assertEqual(frappe.db.get_single_value("Buying Settings", "buying_price_list"), EINKAUF)
		# Site-Default, den das Desk-Formular liest (frappe.defaults)
		self.assertEqual(frappe.db.get_default("selling_price_list"), VERKAUF)
		self.assertEqual(frappe.db.get_default("buying_price_list"), EINKAUF)
		self.assertEqual(standard_preislisten(), [])

	def test_unbekanntes_paket(self):
		with self.assertRaises(frappe.ValidationError):
			importieren(firma=FIRMA, paket="../setup", commit=False)
		with self.assertRaises(frappe.ValidationError):
			importieren(firma="Gibt es nicht GmbH", commit=False)

	# ------------------------------------------------------------------ Kalkulation

	def test_drei_beispiel_stuecklisten(self):
		for item, soll in BEISPIELE.items():
			name = frappe.db.get_value("Item", item, "default_bom")
			self.assertTrue(name, item)
			bom = frappe.get_doc("BOM", name)
			self.assertEqual((bom.company, bom.docstatus, bom.is_active, bom.is_default), (FIRMA, 1, 1, 1))
			self.assertEqual(bom.quantity, 1)
			self.assertEqual([(o.operation, o.workstation) for o in bom.operations], [("Montage", "Montage")])
			self.assertEqual(flt(bom.operations[0].hour_rate), float(STUNDENSATZ))
			self.assertEqual(flt(bom.raw_material_cost, 2), soll["material"], item)
			self.assertEqual(flt(bom.operating_cost, 2), soll["montage"], item)
			self.assertEqual(flt(bom.total_cost, 2), soll["hk"], item)

	def test_alle_stuecklisten_kosten(self):
		ek = preise(EINKAUF)
		for item, vorlage in boms_aus_vorlage().items():
			material, montage, hk = herstellkosten(vorlage, ek)
			name = frappe.db.get_value("Item", item, "default_bom")
			kosten = frappe.db.get_value("BOM", name, ["raw_material_cost", "operating_cost", "total_cost"])
			self.assertEqual(
				tuple(flt(k, 2) for k in kosten), (float(material), float(montage), float(hk)), item
			)

	def test_preislisten(self):
		self.assertEqual(frappe.db.get_value("Price List", VERKAUF, ["selling", "currency"]), (1, "EUR"))
		self.assertEqual(frappe.db.get_value("Price List", EINKAUF, ["buying", "currency"]), (1, "EUR"))
		for liste, soll in ((VERKAUF, preise(VERKAUF)), (EINKAUF, preise(EINKAUF))):
			ist = dict(
				frappe.get_all(
					"Item Price",
					filters={"price_list": liste},
					fields=["item_code", "price_list_rate"],
					as_list=True,
				)
			)
			for code, preis in soll.items():
				self.assertEqual(flt(ist.get(code)), float(preis), f"{liste} {code}")

	def test_angebot_aus_drei_positionen(self):
		kunde = "Katalog Testkunde"
		if not frappe.db.exists("Customer", kunde):
			frappe.get_doc(
				{"doctype": "Customer", "customer_name": kunde, "customer_type": "Company"}
			).insert(ignore_permissions=True)
		angebot = frappe.get_doc(
			{
				"doctype": "Quotation",
				"quotation_to": "Customer",
				"party_name": kunde,
				"company": FIRMA,
				"transaction_date": today(),
				"currency": "EUR",
				"conversion_rate": 1,
				"selling_price_list": VERKAUF,
				"items": [{"item_code": code, "qty": menge} for code, menge in ANGEBOT],
			}
		)
		# wie das Formular: Standard-Steuervorlage der Firma, Preise aus der Preisliste
		vorlage = frappe.db.get_value(
			"Sales Taxes and Charges Template", {"company": FIRMA, "is_default": 1, "disabled": 0}, "name"
		)
		self.assertTrue(vorlage, "Standard-Verkaufssteuervorlage HST fehlt (Site-Template)")
		angebot.taxes_and_charges = vorlage
		angebot.set_missing_values()
		angebot.insert(ignore_permissions=True)
		for zeile in angebot.items:
			self.assertEqual(flt(zeile.rate), BEISPIELE[zeile.item_code]["vk"], zeile.item_code)
		self.assertEqual(flt(angebot.net_total), ANGEBOT_NETTO)
		hk = sum(
			flt(
				frappe.db.get_value(
					"BOM", frappe.db.get_value("Item", z.item_code, "default_bom"), "total_cost"
				)
			)
			* z.qty
			for z in angebot.items
		)
		self.assertEqual(flt(hk, 2), ANGEBOT_HK)
		# Standard-Steuervorlage der Firma (Site-Template): USt 19 % auf 1776
		steuern = [(z.account_head, flt(z.tax_amount)) for z in angebot.taxes]
		self.assertEqual(angebot.taxes_and_charges, vorlage)
		self.assertEqual(flt(angebot.total_taxes_and_charges), ANGEBOT_UST, steuern)
		self.assertEqual([b for k, b in steuern if k.startswith("1776")], [ANGEBOT_UST], steuern)
		self.assertEqual(len(angebot.taxes), 1, steuern)  # genau eine Zeile (Befunde mvp-test)
		self.assertEqual(flt(angebot.grand_total), flt(ANGEBOT_NETTO + ANGEBOT_UST, 2))

	def test_uebersicht(self):
		zeilen_ = {z["item_code"]: z for z in uebersicht(firma=FIRMA)}
		self.assertEqual(len(zeilen_), 51)
		self.assertEqual(zeilen_["IA-10"]["herstellkosten"], 25.60)  # 4 kg * 1,60 + 24 min * 0,80
		self.assertEqual(zeilen_["IA-10"]["verkaufspreis"], 37.12)
		for item, soll in BEISPIELE.items():
			self.assertEqual(zeilen_[item]["herstellkosten"], soll["hk"])
			self.assertEqual(zeilen_[item]["verkaufspreis"], soll["vk"])
			self.assertEqual(zeilen_[item]["is_default"], 1)
		self.assertIsNone(zeilen_["AZ-01"]["bom"])
		self.assertEqual(zeilen_["AZ-01"]["verkaufspreis"], 69.60)
