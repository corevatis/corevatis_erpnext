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

from corevatis_erpnext.angebot import generator, textbausteine
from corevatis_erpnext.katalog.importer import importieren
from corevatis_erpnext.tests._helpers import make_lead, make_user

FIRMA = "Hülshoff Sanierungstechnik"
BUERO = "cv-angebot-buero@example.com"
OHNE_ROLLE = "cv-angebot-sales@example.com"
MONTEUR = "cv-angebot-monteur@example.com"

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
				from frappe.cache_manager import clear_defaults_cache
				from frappe.model.document import clear_document_cache

				for doctype in ("Item", "BOM", "Item Price", "Price List", "UOM"):
					clear_document_cache(doctype)
				# Der Import setzt auch die Site-Defaults selling/buying_price_list; der
				# Rollback nimmt sie aus der DB, der Defaults-Cache behielte sie (dann setzt
				# ein späterer Import sie nicht und die Site bleibt auf „Standard Selling“).
				clear_defaults_cache("__default")

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
		# Textbausteine aus 4.3: Vorbemerkung „Abdichtung Standard“, Garantie „Gewährleistung Abdichtung“
		self.assertEqual(
			frappe.db.get_value("Terms and Conditions", textbausteine.VORBEMERKUNG_STANDARD, "selling"), 1
		)
		self.assertIn("§ 634a BGB", q.cv_garantiehinweis)
		self.assertEqual(q.tc_name, textbausteine.ANGEBOT_STANDARD)
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

	def test_entwurf_ersetzen_warnt_bei_manuellen_positionen(self):
		"""Review Epic 4 G-2: von Hand ergänzte Positionen gehen nur nach Bestätigung verloren."""
		erstes = self.erzeugen()["angebot"]
		self.assertEqual(generator.manuelle_positionen(erstes), [])
		q = frappe.get_doc("Quotation", erstes)
		code = "AZ-01" if frappe.db.exists("Item", "AZ-01") else next(iter(SOLL))
		q.append("items", {"item_code": code, "qty": 2, "rate": 55})
		q.save()
		r = self.erzeugen(neu_erzeugen=1)
		self.assertTrue(r["bestaetigung_noetig"])
		self.assertEqual((r["angebot"], r["neu"], r["ersetzt"]), (erstes, False, False))
		self.assertEqual(len(r["manuelle_positionen"]), 1)
		self.assertTrue(r["manuelle_positionen"][0].startswith(f"8. {code}"), r["manuelle_positionen"])
		self.assertEqual(len(frappe.get_doc("Quotation", erstes).items), 8)  # unverändert
		r = self.erzeugen(neu_erzeugen=1, bestaetigen=1)
		self.assertEqual((r["angebot"], r["neu"], r["ersetzt"]), (erstes, True, True))
		self.assertEqual(len(frappe.get_doc("Quotation", erstes).items), 7)

	def test_kalkulationsfelder_nur_buero_und_meister(self):
		"""Review Epic 4 G-1: EK, Minuten, Zuschläge (JSON) und Herstellkosten auf Feldebene 2."""
		from corevatis_erpnext.angebot.felder import PERMLEVEL_KALKULATION, feldrechte_fehlend

		for doctype, feld in (
			("Quotation", "cv_kalkulation_json"),
			("Quotation Item", "cv_einzelkosten"),
			("Sales Order", "cv_kalkulation_json"),
			("Sales Order Item", "cv_einzelkosten"),
		):
			with self.subTest(doctype=doctype, feld=feld):
				self.assertEqual(frappe.get_meta(doctype).get_field(feld).permlevel, PERMLEVEL_KALKULATION)
		self.assertEqual(feldrechte_fehlend(), [])
		frappe.set_user(BUERO)
		name = self.erzeugen()["angebot"]
		q = frappe.get_doc("Quotation", name)
		self.assertIn(PERMLEVEL_KALKULATION, q.get_permlevel_access("read"))
		# Generator mit den Rechten des Büros: Werte bleiben erhalten (Schreibrecht Ebene 2)
		self.assertTrue(q.cv_kalkulation_json)
		self.assertTrue(flt(q.items[0].cv_einzelkosten))
		frappe.set_user(OHNE_ROLLE)
		q = frappe.get_doc("Quotation", name)
		self.assertTrue(q.has_permission("read"))
		self.assertNotIn(PERMLEVEL_KALKULATION, q.get_permlevel_access("read"))
		q.apply_fieldlevel_read_permissions()
		self.assertFalse(q.get("cv_kalkulation_json"))
		self.assertFalse(flt(q.items[0].get("cv_einzelkosten")))
		self.assertTrue(q.items[0].get("cv_mengen_herkunft"))  # Mengenherkunft bleibt sichtbar

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

	def test_firma_vorrang_objekt_lead_standard(self):
		"""Abnahme mvp-test: Firma des Aufmaßes/Objekts vor der des Leads vor der Standardfirma;
		Firmen, die es nicht gibt, zählen nicht."""
		lead = make_lead()
		frappe.db.set_value("Lead", lead.name, "company", "_Test Gibt Es Nicht", update_modified=False)
		self.assertEqual(generator._firmen(self.aufmass, "Lead", lead.name)[0], generator._standardfirma())
		frappe.db.set_value("Lead", lead.name, "company", FIRMA, update_modified=False)
		self.assertEqual(generator._firmen(self.aufmass, "Lead", lead.name)[0], FIRMA)
		with patch.object(generator, "_firma_des_objekts", return_value=FIRMA):
			self.assertEqual(generator._firmen(self.aufmass, "Customer", "egal")[0], FIRMA)
		self.aufmass.company = FIRMA
		self.assertEqual(generator._firma_des_objekts(self.aufmass), FIRMA)

	def test_firma_ohne_leistungen_naechste_firma(self):
		"""Abnahme mvp-test: alter Lead mit einer Firma ohne Stücklisten (CVT) - das Angebot
		entsteht über die nächste Firma mit Leistungen, mit Hinweis statt „Keine Leistungen“."""
		original = generator.leistungen_aus_aufmass

		def je_firma(am, firma):
			return ([], ["keine Stückliste"]) if firma == "_Test Ohne Katalog" else original(am, firma)

		with (
			patch.object(generator, "_firmen", return_value=["_Test Ohne Katalog", FIRMA]),
			patch.object(generator, "leistungen_aus_aufmass", side_effect=je_firma),
		):
			r = self.erzeugen()
		self.assertEqual(frappe.db.get_value("Quotation", r["angebot"], "company"), FIRMA)
		self.assertTrue(any("_Test Ohne Katalog" in w for w in r["warnungen"]), r["warnungen"])

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
		# Desk-Zähler brauchen ``fieldname`` (mvp-test: Aufmaß zeigte „Angebot“ ohne Zahl)
		self.assertEqual(generator.aufmass_dashboard({})["fieldname"], "cv_aufmass")
		self.assertEqual(data["fieldname"], "objekt")
		hooks = frappe.get_hooks("override_doctype_dashboards", app_name="corevatis_erpnext")
		self.assertEqual(hooks["Objekt"], ["corevatis_erpnext.angebot.generator.objekt_dashboard"])
		self.assertIn(
			"public/js/aufmass_angebot.js",
			frappe.get_hooks("doctype_js", app_name="corevatis_erpnext")["Aufmass"],
		)

	def test_feste_mengen_der_app(self):
		"""Durchstich A1: Zeilen mit ``menge_fest`` (vom Meister in der App bestätigt) geben die Menge
		je Item vor (Summe), ohne Mengenregel; ihr Hinweis „nicht verwendet“ entfällt."""
		self.aufmass = testaufmass("AUF-TEST-APP")
		self.aufmass.positionen = [
			frappe._dict(
				name="a1",
				idx=1,
				item_code="IA-05",
				raum="Keller 1",
				bauteil="Keller innen",
				menge=20.5,
				menge_fest=1,
			),
			frappe._dict(name="a2", idx=2, item_code="IA-05", raum="Keller 2", menge=5, menge_fest=1),
			frappe._dict(name="a3", idx=10, item_code="AL-01", menge=1, menge_fest=1),
		]
		r = self.erzeugen()
		leistungen = self.kern.aufrufe[0]["leistungen"]
		self.assertEqual(
			[(l["item_code"], l["menge"], l["leistungsregel"]) for l in leistungen],
			[("IA-05", "25.5", None), ("AL-01", "1.0", None)],
		)
		self.assertEqual(leistungen[0]["herkunft"]["raeume"], ["Keller 1", "Keller 2"])
		self.assertNotIn("Zeile 10 ohne Kategorie", r["warnungen"])
		for leistung in leistungen:
			self.assertLessEqual(set(leistung), set(generator.LEISTUNG_SCHLUESSEL))

	def test_mobil_katalog_angebote_pdf(self):
		"""Durchstich A1: Funktionen für die App (Hook ``corevatis_angebot_adapter``)."""
		from corevatis_erpnext.angebot import mobil

		self.assertEqual(frappe.get_hooks("corevatis_angebot_adapter"), ["corevatis_erpnext.angebot.mobil"])
		katalog = {k["item_code"]: k for k in mobil.katalog()}
		self.assertEqual(katalog["IA-05"]["verfahren"], "Innenabdichtung")
		self.assertEqual(katalog["IA-05"]["einheit"], "m²")
		self.assertNotIn("AZ-01", katalog)  # Regiestunde ohne Stückliste: der Generator überspränge sie
		a = mobil.angebot_erzeugen("AUF-TEST-4.1")
		self.assertEqual((a["netto"], a["brutto"], a["docstatus"]), (NETTO, BRUTTO, 0))
		self.assertIn(a["angebot"], [x["name"] for x in mobil.angebote(objekt="OBJ-TEST-1")])
		self.assertEqual(mobil.angebot_info(a["angebot"])["objekt"], "OBJ-TEST-1")
		self.assertIsNone(mobil.angebot_info("ANG-GIBT-ES-NICHT"))
		dateiname, pdf = mobil.angebot_pdf(a["angebot"])
		self.assertEqual(dateiname, f"{a['angebot']}.pdf")
		self.assertTrue(pdf.startswith(b"%PDF"))

	def test_mobil_rechte(self):
		"""Security-Review: Katalog nur für Büro/Meister; Summen nur mit Leserecht auf das Angebot."""
		from corevatis_erpnext.angebot import mobil

		make_user(MONTEUR, "Monteur Angebot", ["Monteur"])
		frappe.set_user(MONTEUR)
		with self.assertRaises(frappe.PermissionError):
			mobil.katalog()
		frappe.set_user("Administrator")
		name = self.erzeugen()["angebot"]
		with (
			patch.object(generator, "erzeuge_angebot", return_value={"angebot": name, "neu": False}),
			patch.object(frappe, "has_permission", return_value=False),
		):
			r = mobil.angebot_erzeugen("AUF-TEST-4.1")
		self.assertEqual(r, {"angebot": name, "neu": False})  # ohne Leserecht keine Summen
		self.assertIn("netto", mobil.angebot_erzeugen("AUF-TEST-4.1"))
