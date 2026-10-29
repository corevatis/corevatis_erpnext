"""Aufgabe 4.1: Angebot aus dem Aufmaß (``angebot.generator``) auf einer ERPNext-v16-Site.

Abnahme „Quotation mit 5+ Positionen aus Testaufmaß“ (Adapter-Seite). Die CI-Bench dieses
Repos hat ``corevatis`` nicht: Aufmaß, Objekt und die Kern-Fassade sind hier Stellvertreter
(gleiche Daten wie ``corevatis/tests/angebot_testdaten.py``, Sollwerte von Hand in
``docs/4.1-angebotsengine.md``). Die echte Fassade prüft die Kern-CI gegen den Quellstand
dieses Repos (``corevatis/tests/test_erpnext_contract.py``). Katalog, Stücklisten, Preisliste,
Steuervorlage und das Angebot selbst sind echt (ERPNext).

Der Katalog wird - wenn ``test_katalog`` noch nicht lief - ohne Commit importiert und am Ende
der Klasse zurückgerollt (``test_katalog`` braucht eine Site ohne Katalog für den Data Import).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, getdate, nowdate

from corevatis_erpnext.angebot import generator
from corevatis_erpnext.katalog.importer import importieren
from corevatis_erpnext.tests._helpers import make_lead, make_user

FIRMA = "Hülshoff Sanierungstechnik"
BUERO = "cv-angebot-buero@example.com"
OHNE_ROLLE = "cv-angebot-sales@example.com"

# Herstellkosten je Einheit aus den Stücklisten (Katalog 2.3): Material, Minuten
KOSTEN = {
	"AL-01": (25.0, 180.0),
	"AA-01": (5.06, 25.0),
	"AA-09": (25.55, 28.0),
	"AA-13": (42.2, 120.0),
	"HS-02": (6.85, 20.0),
	"IA-05": (7.2, 22.0),
	"IA-08": (6.4, 15.0),
}
# Ergebnis der Kern-Kalkulation für das Testaufmaß (Schema „Hülshoff 2026“):
# Menge, Einzelpreis, Gesamtpreis, Räume mit Menge
SOLL = {
	"AL-01": ("1.00", "212.83", "212.83", []),
	"AA-01": ("15.36", "31.81", "488.60", []),
	"AA-09": ("21.83", "63.80", "1392.75", []),
	"AA-13": ("2", "178.06", "356.12", []),
	"HS-02": ("13.26", "29.42", "390.11", ["R1", "R2"]),
	"IA-05": ("16.61", "31.88", "529.53", ["R1", "R2"]),
	"IA-08": ("26.80", "23.85", "639.18", ["R1", "R2"]),
}
NETTO, UST, BRUTTO = 4009.12, 761.73, 4770.85  # 19 % von 4.009,12 = 761,7328


def testaufmass(name="AUF-TEST-4.1", **werte) -> frappe._dict:
	zeilen = [
		frappe._dict(
			name=f"row-{code}",
			idx=i + 1,
			item_code=code,
			raum="R1" if code.startswith(("IA", "HS")) else None,
			verfahren=None,
		)
		for i, code in enumerate(SOLL)
	]
	daten = {"name": name, "objekt": "OBJ-TEST-1", "referenz_doctype": None, "positionen": zeilen}
	daten.update(werte)
	return frappe._dict(daten)


class KernStellvertreter:
	"""Antwort wie ``corevatis.angebot.kalkulation.kalkuliere`` (Schlüssel und Werte)."""

	def __init__(self, ok=True):
		self.ok = ok
		self.aufrufe = []

	def __call__(self, aufmass, leistungen, schema=None, erschwernis_prozent=None):
		self.aufrufe.append({"aufmass": aufmass, "leistungen": leistungen, "schema": schema})
		if not self.ok:
			return {
				"ok": False,
				"fehler": [{"pfad": "positionen[0].einheit", "code": "einheit", "meldung": "passt nicht"}],
			}
		positionen = []
		for leistung in leistungen:
			menge, ep, gp, raeume = SOLL[leistung["item_code"]]
			hk = flt(leistung["material_je_einheit"]) + flt(leistung["montage_min_je_einheit"]) / 60 * 48
			positionen.append(
				{
					"id": leistung["item_code"],
					"item_code": leistung["item_code"],
					"bezeichnung": leistung["bezeichnung"],
					"einheit": leistung["einheit"],
					"menge": menge,
					"menge_quelle": "regel",
					"mengen_herkunft": f"Regel {leistung['leistungsregel']}: … Summe {menge}",
					"mengen": {
						"je_raum": [{"raum": r, "menge": "1.00"} for r in raeume],
						"beitraege": [{"bauteil": "Keller außen", "menge": "1.00"}] if raeume else [],
					},
					"kalkulation": {"herstellkosten": f"{hk:.2f}"},
					"einzelpreis": ep,
					"gesamtpreis": gp,
					"herkunft": leistung["herkunft"],
				}
			)
		return {
			"ok": True,
			"fehler": [],
			"warnungen": [],
			"positionen": positionen,
			"summen": {"netto": f"{NETTO:.2f}"},
			"aufmass": aufmass,
			"kalkulationsschema": schema or "Hülshoff 2026",
			"hinweise": [
				{
					"pfad": "aufmass.positionen[9]",
					"code": "nicht_verwendet",
					"meldung": "Zeile 10 ohne Kategorie",
				}
			],
		}


class TestAngebotGenerator(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Company", FIRMA):
			raise AssertionError(f"Firma {FIRMA} fehlt - Site-Template huelshoff vor den Tests anwenden (CI)")
		if not frappe.db.exists("Item", "IA-05"):
			importieren(firma=FIRMA, commit=False)  # wird mit der Klasse zurückgerollt

			def cache_leeren():
				from frappe.model.document import clear_document_cache

				for doctype in ("Item", "BOM", "Item Price", "Price List", "UOM"):
					clear_document_cache(doctype)

			cls.addClassCleanup(cache_leeren)
		make_user(BUERO, "Büro Angebot", ["Büro", "Sales User"])
		make_user(OHNE_ROLLE, "Vertrieb", ["Sales User"])

	def setUp(self):
		# jeder Test für sich: Angebote, Kunden, Textbaustein zurückrollen
		frappe.db.savepoint("angebot_test")
		self.addCleanup(self._zuruecksetzen)
		self.kern = KernStellvertreter()
		self.aufmass = testaufmass()
		for ziel, wert in (
			("kern_vorhanden", lambda: True),
			("_aufmass_lesen", lambda name: self.aufmass),
			(
				"_objekt_werte",
				lambda objekt: frappe._dict(titel="Testweg 4, 48143 Münster") if objekt else frappe._dict(),
			),
			("_kern_kalkulation", lambda: self.kern),
		):
			p = patch.object(generator, ziel, wert)
			p.start()
			self.addCleanup(p.stop)

	def _zuruecksetzen(self):
		frappe.set_user("Administrator")
		frappe.db.rollback(save_point="angebot_test")
		from frappe.model.document import clear_document_cache

		for doctype in ("Quotation", "Customer", "Lead", "Terms and Conditions"):
			clear_document_cache(doctype)

	def erzeugen(self, **kwargs):
		return generator.erzeuge_angebot(self.aufmass.name, **kwargs)

	def test_angebot_aus_testaufmass(self):
		frappe.set_user(BUERO)
		r = self.erzeugen()
		self.assertTrue(r["neu"])
		self.assertFalse(r["ersetzt"])
		self.assertEqual(r["positionen"], 7)
		self.assertIn("Zeile 10 ohne Kategorie", r["warnungen"])

		# Kern bekam je Leistung Regel und Herstellkosten aus der Standard-Stückliste
		(aufruf,) = self.kern.aufrufe
		self.assertEqual(aufruf["aufmass"], "AUF-TEST-4.1")
		for leistung in aufruf["leistungen"]:
			self.assertLessEqual(set(leistung), set(generator.LEISTUNG_SCHLUESSEL))
			material, minuten = KOSTEN[leistung["item_code"]]
			self.assertEqual(flt(leistung["material_je_einheit"]), material, leistung["item_code"])
			self.assertEqual(flt(leistung["montage_min_je_einheit"]), minuten, leistung["item_code"])
			self.assertTrue(leistung["leistungsregel"])  # Item.cv_leistungsregel
		self.assertTrue(aufruf["leistungen"][0]["herkunft"]["stueckliste"].startswith("BOM-AL-01-"))

		q = frappe.get_doc("Quotation", r["angebot"])
		self.assertEqual((q.quotation_to, q.party_name), ("Customer", generator.PLATZHALTER_KUNDE))
		self.assertEqual((q.company, q.selling_price_list), (FIRMA, generator.PREISLISTE))
		self.assertEqual(getdate(q.valid_till), getdate(add_days(nowdate(), 30)))
		self.assertEqual(
			{z.item_code: (flt(z.qty), flt(z.rate), flt(z.amount)) for z in q.items},
			{c: (flt(m), flt(ep), flt(gp)) for c, (m, ep, gp, _) in SOLL.items()},
		)
		self.assertEqual(flt(q.net_total), NETTO)
		# Befund mvp-test Nr. 4: Steuervorlage und genau eine Steuerzeile auch ohne Desk
		self.assertEqual(
			frappe.db.get_value("Sales Taxes and Charges Template", q.taxes_and_charges, "title"),
			generator.STEUERVORLAGE,
		)
		self.assertEqual(len(q.taxes), 1)
		self.assertEqual((flt(q.total_taxes_and_charges), flt(q.grand_total)), (UST, BRUTTO))
		self.assertEqual((r["netto"], r["steuer"], r["brutto"]), (NETTO, UST, BRUTTO))
		# Kopf-Felder aus angebot.felder
		self.assertEqual(
			(q.cv_aufmass, q.cv_objekt, q.cv_kalkulationsschema),
			("AUF-TEST-4.1", "OBJ-TEST-1", "Hülshoff 2026"),
		)
		self.assertEqual(q.cv_erzeugt_aus_aufmass, 1)
		self.assertIn("Testweg 4, 48143 Münster", q.cv_titel)
		self.assertIn("Innenabdichtung", q.cv_titel)
		self.assertIn("Aufmaßes vor Ort", q.cv_vorbemerkung)
		self.assertEqual(frappe.db.get_value("Terms and Conditions", generator.TEXTBAUSTEIN, "selling"), 1)
		self.assertEqual(q.cv_garantiehinweis, generator.GARANTIEHINWEIS)
		self.assertEqual(json.loads(q.cv_kalkulation_json)["summen"]["netto"], "4009.12")
		# Positions-Felder
		ia05 = next(z for z in q.items if z.item_code == "IA-05")
		self.assertEqual(
			(ia05.cv_raum, ia05.cv_bauteil, ia05.cv_verfahren), ("R1, R2", "Keller außen", "Innenabdichtung")
		)
		self.assertEqual((ia05.cv_aufmass_position, flt(ia05.cv_einzelkosten)), ("row-IA-05", 24.8))
		self.assertTrue(ia05.cv_mengen_herkunft.startswith("Regel "))
		self.assertEqual(next(z for z in q.items if z.item_code == "AL-01").cv_raum, None)
		self.assertEqual(flt(next(z for z in q.items if z.item_code == "AA-09").cv_einzelkosten), 47.95)

	def test_idempotent_und_entwurf_ersetzen(self):
		erstes = self.erzeugen()["angebot"]
		zweites = self.erzeugen()
		self.assertEqual((zweites["angebot"], zweites["neu"]), (erstes, False))
		self.assertEqual(frappe.db.count("Quotation", {"cv_aufmass": "AUF-TEST-4.1"}), 1)
		ersetzt = self.erzeugen(neu_erzeugen=1, schema="Variante")
		self.assertEqual((ersetzt["angebot"], ersetzt["neu"], ersetzt["ersetzt"]), (erstes, True, True))
		self.assertEqual(frappe.db.get_value("Quotation", erstes, "cv_kalkulationsschema"), "Variante")
		self.assertEqual(len(frappe.get_doc("Quotation", erstes).items), 7)
		frappe.get_doc("Quotation", erstes).submit()
		with self.assertRaises(generator.AngebotFehler):
			self.erzeugen(neu_erzeugen=1)
		self.assertEqual(self.erzeugen()["angebot"], erstes)

	def test_lead_als_partei(self):
		lead = make_lead(company=FIRMA)
		r = self.erzeugen(lead=lead.name)
		q = frappe.get_doc("Quotation", r["angebot"])
		self.assertEqual((q.quotation_to, q.party_name, q.company), ("Lead", lead.name, FIRMA))
		# Referenz am Aufmaß genügt ebenso
		self.aufmass = testaufmass("AUF-TEST-4.1-LEAD", referenz_doctype="Lead", referenz_name=lead.name)
		q = frappe.get_doc("Quotation", self.erzeugen()["angebot"])
		self.assertEqual((q.quotation_to, q.party_name), ("Lead", lead.name))
		self.aufmass = testaufmass("AUF-TEST-4.1-X")
		with self.assertRaises(generator.AngebotFehler):
			generator.erzeuge_angebot("AUF-TEST-4.1-X", lead="CRM-LEAD-GIBT-ES-NICHT")

	def test_rechte_und_ohne_kern(self):
		frappe.set_user(OHNE_ROLLE)
		with self.assertRaises(frappe.PermissionError):
			self.erzeugen()
		frappe.set_user("Administrator")
		with (
			patch.object(generator, "kern_vorhanden", lambda: False),
			self.assertRaises(generator.AngebotFehler),
		):
			self.erzeugen()

	def test_verfahren_ohne_item_code(self):
		"""Zeilen mit nur ``verfahren``: Leistungsregeln des Verfahrens mit Leistungsposition."""
		self.aufmass = testaufmass("AUF-TEST-4.1-VERF")
		self.aufmass.positionen = [
			frappe._dict(name="v1", idx=1, raum="R1", bauteil="Keller innen", verfahren="Innenabdichtung"),
			frappe._dict(name="v2", idx=2, verfahren="Spezialabdichtung"),
		]
		treffer = {"Innenabdichtung": [("REGEL-IA-150", "IA-05"), ("REGEL-BODEN", "IA-08")]}
		with patch.object(generator, "_regeln_je_verfahren", lambda v: treffer.get(v, [])):
			r = self.erzeugen()
		leistungen = self.kern.aufrufe[0]["leistungen"]
		self.assertEqual(
			[(l["item_code"], l["leistungsregel"]) for l in leistungen],
			[("IA-05", "REGEL-IA-150"), ("IA-08", "REGEL-BODEN")],
		)
		self.assertEqual(leistungen[0]["herkunft"]["aufmass_positionen"], ["v1"])
		self.assertTrue(any("Spezialabdichtung" in w for w in r["warnungen"]))
		self.assertEqual(r["positionen"], 2)

	def test_kalkulationsfehler_und_ohne_leistungen(self):
		self.kern.ok = False
		with self.assertRaises(generator.AngebotFehler):
			self.erzeugen()
		self.assertFalse(frappe.db.exists("Quotation", {"cv_aufmass": "AUF-TEST-4.1"}))
		self.aufmass.positionen = [frappe._dict(name="x", idx=1, item_code="GIBT-ES-NICHT")]
		with self.assertRaises(generator.AngebotFehler):
			self.erzeugen()

	def test_dashboards(self):
		data = generator.objekt_dashboard({"fieldname": "objekt", "transactions": [{"items": ["Aufmass"]}]})
		self.assertEqual(data["non_standard_fieldnames"]["Quotation"], "cv_objekt")
		self.assertEqual(data["transactions"][-1]["items"], ["Quotation"])
		self.assertEqual(
			generator.aufmass_dashboard({})["non_standard_fieldnames"], {"Quotation": "cv_aufmass"}
		)
		hooks = frappe.get_hooks("override_doctype_dashboards", app_name="corevatis_erpnext")
		self.assertEqual(hooks["Objekt"], ["corevatis_erpnext.angebot.generator.objekt_dashboard"])
		self.assertIn(
			"public/js/aufmass_angebot.js",
			frappe.get_hooks("doctype_js", app_name="corevatis_erpnext")["Aufmass"],
		)
