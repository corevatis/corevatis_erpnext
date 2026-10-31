"""Abrechnung 7.1/7.2: Abschlagsplan, „Abschlag stellen“, Schlussrechnung, abgerechneter Anteil am
Auftrag, Prozessleiste, Rechte.

Die CI-Bench dieses Repos hat ``corevatis`` nicht: die Engine ``corevatis.engine.invoice`` ersetzt
``KernStellvertreter`` (gleiche Antwortform, Decimal-Rechnung für die Testfälle; die Rechenregeln
selbst prüft ``corevatis/tests/test_engine_invoice.py``). Angebot, Annahme, Auftrag, Projekt und
Rechnung sind echt (4.4, ERPNext).
"""

from __future__ import annotations

import json
import unittest
from decimal import ROUND_HALF_EVEN, Decimal
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, today

from corevatis_erpnext.abnahme.rechnung import RechnungOhneAbnahme
from corevatis_erpnext.abrechnung import abschlag, kern, plan, schluss
from corevatis_erpnext.abrechnung import auftrag as A
from corevatis_erpnext.abrechnung.einstellungen import EINSTELLUNGEN, einstellungen
from corevatis_erpnext.abrechnung.kern import AbrechnungFehler
from corevatis_erpnext.angebot import annahme
from corevatis_erpnext.prozess.kette import status
from corevatis_erpnext.tests import _angebot as td
from corevatis_erpnext.tests._helpers import make_user

BUERO = "cv-abrechnung-buero@example.com"
BUCHHALTER = "cv-abrechnung-buchhalter@example.com"
MEISTER = "cv-abrechnung-meister@example.com"
MONTEUR = "cv-abrechnung-monteur@example.com"
NACHTRAG_ARTIKEL = "_TEST-CV-NACHTRAG"
CENT = Decimal("0.01")


def _d(w) -> Decimal:
	return Decimal(str(w))


def _c(w: Decimal) -> Decimal:
	return w.quantize(CENT, rounding=ROUND_HALF_EVEN)


def _s(w: Decimal) -> str:
	return format(_c(w), "f")


class KernStellvertreter:
	"""Antworten wie ``corevatis.engine.invoice`` (Schlüssel und Rechenweg der Testfälle)."""

	def __init__(self):
		self.aufrufe = []

	def aufloesen(self, pfad):
		return {
			kern.KERN_ABSCHLAGSPLAN: self.abschlagsplan,
			kern.KERN_ABSCHLAG: self.abschlag,
			kern.KERN_SCHLUSSRECHNUNG: self.schlussrechnung,
		}.get(pfad)

	@staticmethod
	def _betraege(netto, satz):
		steuer = _c(netto * satz / 100)
		return {"netto": _s(netto), "steuer": _s(steuer), "brutto": _s(netto + steuer)}

	def abschlagsplan(self, plan_, auftrag, optionen=None):
		self.aufrufe.append(("abschlagsplan", plan_, auftrag, optionen))
		summe, satz = _d(auftrag["netto"]), _d(auftrag["steuersatz"])
		kum = Decimal(0)
		zeilen, warnungen = [], []
		s = {
			"anzahl": 0,
			"mit_rechnung": 0,
			"freigegeben": 0,
			"bezahlt": 0,
			"offen": 0,
			"gestellt_netto": Decimal(0),
			"offen_netto": Decimal(0),
		}
		for i, z in enumerate(sorted(plan_["zeilen"], key=lambda z: z.get("nr") or 0), start=1):
			status_ = z.get("status") or "offen"
			if status_ != "offen" and z.get("netto") is not None:
				netto = _c(_d(z["netto"]))
			elif z.get("betrag") is not None:
				netto = _c(_d(z["betrag"]))
			else:
				netto = _c(summe * _d(z["prozent"]) / 100)
			kum += netto
			s["anzahl"] += 1
			if status_ != "offen":
				s["mit_rechnung"] += 1
			if status_ in ("gestellt", "bezahlt"):
				s["freigegeben"] += 1
				s["gestellt_netto"] += netto
			if status_ == "bezahlt":
				s["bezahlt"] += 1
			if status_ == "offen":
				s["offen"] += 1
				s["offen_netto"] += netto
			zeilen.append(
				{
					"nr": z.get("nr") or i,
					"bezeichnung": z.get("bezeichnung"),
					"prozent": _s(_d(z["prozent"]) if z.get("prozent") is not None else netto / summe * 100),
					"art": "prozent" if z.get("prozent") is not None else "betrag",
					**self._betraege(netto, satz),
					"kumuliert_netto": _s(kum),
					"kumuliert_prozent": _s(kum / summe * 100),
					"faellig_bei": z.get("faellig_bei"),
					"datum": z.get("datum"),
					"status": status_,
					"rechnung": z.get("rechnung"),
				}
			)
		if kum > summe:
			warnungen.append(
				{
					"pfad": "plan.zeilen",
					"code": "ueber_auftragssumme",
					"meldung": f"Abschläge zusammen {kum} über der Auftragssumme {summe}",
				}
			)
		return {
			"ok": True,
			"invoice_version": "test",
			"fehler": [],
			"warnungen": warnungen,
			"auftrag": {"name": auftrag.get("name"), **self._betraege(summe, satz), "steuersatz": str(satz)},
			"zeilen": zeilen,
			"summen": {
				**{k: v for k, v in s.items() if not isinstance(v, Decimal)},
				"gestellt_netto": _s(s["gestellt_netto"]),
				"gestellt_steuer": _s(_c(s["gestellt_netto"] * satz / 100)),
				"gestellt_brutto": _s(s["gestellt_netto"] + _c(s["gestellt_netto"] * satz / 100)),
				"gestellt_prozent": _s(s["gestellt_netto"] / summe * 100),
				"offen_netto": _s(s["offen_netto"]),
				"offen_brutto": _s(s["offen_netto"] + _c(s["offen_netto"] * satz / 100)),
				"plan_netto": _s(kum),
				"plan_prozent": _s(kum / summe * 100),
				"rest_netto": _s(summe - kum),
			},
			"naechste": next((z for z in zeilen if z["status"] == "offen"), None),
		}

	def abschlag(
		self, auftrag, nr, prozent=None, betrag=None, bereits_gestellt=None, bezeichnung=None, optionen=None
	):
		self.aufrufe.append(("abschlag", auftrag, nr, prozent, betrag, bereits_gestellt))
		summe, satz = _d(auftrag["netto"]), _d(auftrag["steuersatz"])
		vorher = sum(
			(_d(g["netto"] if isinstance(g, dict) else g) for g in bereits_gestellt or []), Decimal(0)
		)
		netto = _c(_d(betrag)) if betrag is not None else _c(summe * _d(prozent) / 100)
		kum = vorher + netto
		warnungen = []
		if kum > summe:
			warnungen.append(
				{
					"pfad": "prozent",
					"code": "ueber_auftragssumme",
					"meldung": f"Mit diesem Abschlag sind {kum} netto gestellt - mehr als die Auftragssumme {summe}",
				}
			)
		p = _d(prozent) if prozent is not None else netto / summe * 100
		lohn = None
		if auftrag.get("lohnanteil_netto") is not None:
			la = _c(_d(auftrag["lohnanteil_netto"]) * netto / summe)
			lohn = self._betraege(la, satz)
		return {
			"ok": True,
			"invoice_version": "test",
			"fehler": [],
			"warnungen": warnungen,
			"nr": nr,
			"bezeichnung": bezeichnung,
			"text": f"Abschlagsrechnung Nr. {nr} zum Auftrag {auftrag['name']} ({format(p.normalize(), 'f')} % der Auftragssumme)",
			"prozent": _s(p),
			"art": "prozent" if prozent is not None else "betrag",
			**self._betraege(netto, satz),
			"kumuliert": {**self._betraege(kum, satz), "prozent": _s(kum / summe * 100)},
			"bisher": {"netto": _s(vorher), "prozent": _s(vorher / summe * 100)},
			"rest": {"netto": _s(summe - kum)},
			"lohnanteil": lohn,
			"auftrag": {"name": auftrag["name"], **self._betraege(summe, satz), "steuersatz": str(satz)},
		}

	def schlussrechnung(self, auftrag, positionen, nachtraege=None, abschlaege=None, optionen=None):
		self.aufrufe.append(("schlussrechnung", auftrag, positionen, nachtraege, abschlaege, optionen))
		satz = _d((optionen or {}).get("steuersatz") or auftrag["steuersatz"])
		standard = _d((optionen or {}).get("lohnanteil_prozent_standard") or 0)
		aus, leistung, lohn_summe, warnungen = [], Decimal(0), Decimal(0), []
		for p in list(positionen) + [{**n, "nachtrag": True} for n in nachtraege or []]:
			nachtrag = bool(p.get("nachtrag"))
			menge_auftrag = Decimal(0) if nachtrag else _d(p["menge_auftrag"])
			menge_ist = _d(p["menge"]) if nachtrag else _d(p.get("menge_ist", p["menge_auftrag"]))
			ep = _d(p["einzelpreis"])
			gp = _c(ep * menge_ist)
			if p.get("lohnanteil") is not None:
				quelle, lohn = "fest", _c(_d(p["lohnanteil"]))
			elif p.get("lohn_je_einheit") is not None:
				quelle, lohn = "lohn_je_einheit", _c(_d(p["lohn_je_einheit"]) * menge_ist)
			elif standard > 0:
				quelle, lohn = "prozent", _c(gp * standard / 100)
			else:
				quelle, lohn = "keine", Decimal(0)
			if lohn > gp:
				lohn = gp
				warnungen.append({"pfad": p["id"], "code": "lohnanteil_gekappt", "meldung": "gekappt"})
			if menge_ist == 0:
				warnungen.append(
					{"pfad": p["id"], "code": "menge_null", "meldung": f"Position {p['id']}: Ist-Menge 0"}
				)
			elif not nachtrag and menge_ist > menge_auftrag:
				warnungen.append(
					{"pfad": p["id"], "code": "mehrmenge", "meldung": f"Position {p['id']}: Mehrmenge"}
				)
			elif not nachtrag and menge_ist < menge_auftrag:
				warnungen.append(
					{"pfad": p["id"], "code": "mindermenge", "meldung": f"Position {p['id']}: Mindermenge"}
				)
			leistung += gp
			lohn_summe += lohn
			aus.append(
				{
					"id": p["id"],
					"item_code": p.get("item_code"),
					"bezeichnung": p.get("bezeichnung"),
					"einheit": p.get("einheit"),
					"nachtrag": nachtrag,
					"menge_auftrag": str(menge_auftrag),
					"menge_ist": str(menge_ist),
					"abweichung": str(menge_ist - menge_auftrag),
					"mehrmenge": str(max(menge_ist - menge_auftrag, Decimal(0))),
					"einzelpreis": str(ep),
					"gesamtpreis": _s(gp),
					"gesamtpreis_auftrag": _s(ep * menge_auftrag),
					"lohnanteil": {
						"quelle": quelle,
						"je_einheit": None,
						"prozent": _s(lohn / gp * 100) if gp else None,
						"netto": _s(lohn),
					},
					"herkunft": {},
				}
			)
		ab_aus, ab_netto, ab_steuer, ab_brutto = [], Decimal(0), Decimal(0), Decimal(0)
		for z in abschlaege or []:
			netto = _c(_d(z["netto"]))
			steuer = _c(_d(z["steuer"])) if z.get("steuer") is not None else _c(netto * satz / 100)
			brutto = _c(_d(z["brutto"])) if z.get("brutto") is not None else netto + steuer
			ab_netto, ab_steuer, ab_brutto = ab_netto + netto, ab_steuer + steuer, ab_brutto + brutto
			ab_aus.append(
				{
					"nr": z["nr"],
					"rechnung": z.get("rechnung"),
					"datum": z.get("datum"),
					"bezeichnung": z.get("bezeichnung"),
					"text": f"abzüglich Abschlagsrechnung Nr. {z['nr']} vom {z.get('datum')} ({z.get('rechnung')}) (netto)",
					"netto": _s(netto),
					"steuer": _s(steuer),
					"brutto": _s(brutto),
				}
			)
		rest = leistung - ab_netto
		if rest < 0:
			warnungen.append(
				{
					"pfad": "abschlaege",
					"code": "rest_negativ",
					"meldung": "Die Abschläge übersteigen die Leistung - Gutschrift nötig",
				}
			)
		return {
			"ok": True,
			"invoice_version": "test",
			"fehler": [],
			"warnungen": warnungen,
			"auftrag": {"name": auftrag.get("name"), "netto": auftrag.get("netto"), "steuersatz": str(satz)},
			"positionen": aus,
			"abschlaege": ab_aus,
			"summen": {
				"leistung": {
					"netto_auftragspositionen": _s(
						sum((_d(p["gesamtpreis"]) for p in aus if not p["nachtrag"]), Decimal(0))
					),
					"netto_nachtraege": _s(
						sum((_d(p["gesamtpreis"]) for p in aus if p["nachtrag"]), Decimal(0))
					),
					"netto_auftrag": _s(sum((_d(p["gesamtpreis_auftrag"]) for p in aus), Decimal(0))),
					**self._betraege(leistung, satz),
				},
				"abschlaege": {
					"anzahl": len(ab_aus),
					"netto": _s(ab_netto),
					"steuer": _s(ab_steuer),
					"brutto": _s(ab_brutto),
				},
				"rest": self._betraege(rest, satz),
				"lohnanteil": {
					**self._betraege(lohn_summe, satz),
					"prozent": _s(lohn_summe / leistung * 100) if leistung else "0.00",
				},
				"abweichung_netto": _s(leistung - _d(auftrag["netto"])) if auftrag.get("netto") else "0.00",
				"rundungsdifferenz_steuer": "0.00",
				"anzahl_positionen": len([p for p in aus if not p["nachtrag"]]),
				"anzahl_nachtraege": len([p for p in aus if p["nachtrag"]]),
			},
		}


def _als(user: str, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def _schritt(stand: dict, key: str) -> dict:
	return next(s for s in stand["schritte"] if s["key"] == key)


class TestAbrechnung(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if kern.KERN_APP in frappe.get_installed_apps():
			raise unittest.SkipTest(
				"corevatis installiert - Stellvertreter der Engine würde die echte überdecken"
			)
		from corevatis_erpnext.abnahme.felder import ensure_abnahme_felder
		from corevatis_erpnext.abrechnung import ensure_abrechnung_setup

		td.ensure_felder_41()
		td.ensure_artikel()
		td.artikel(NACHTRAG_ARTIKEL, "Sonstiges")
		ensure_abnahme_felder()
		ensure_abrechnung_setup()
		make_user(
			BUERO,
			"Berta",
			["Büro", "Sales User", "Accounts User", "Projects User", "Projects Manager"],
			"Büro",
		)
		make_user(
			MEISTER, "Martin", ["Meister", "Sales User", "Projects User", "Projects Manager"], "Meister"
		)
		make_user(MONTEUR, "Mona", ["Monteur"], "Monteur")
		# Buchhaltung ohne Rolle Büro: darf Rechnungen buchen, aber keine Abschläge/Schlussrechnungen freigeben
		make_user(BUCHHALTER, "Bodo", ["Accounts User", "Sales User"], "Buchhalter")
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Singles", {"doctype": EINSTELLUNGEN})
		frappe.db.delete("Singles", {"doctype": "Corevatis Abnahme Einstellungen"})
		self.kern = KernStellvertreter()
		p = patch.object(kern, "_aufloesen", self.kern.aufloesen)
		p.start()
		self.addCleanup(p.stop)

	# --- Hilfen ------------------------------------------------------------------------------

	def vorgang(self, kalkulation: bool = True):
		"""Angebot (10 x 50 € = 500 netto, 19 %) annehmen → Auftrag + Projekt (+ Abschlagsplan per Hook)."""
		lead = td.lead_mit_angebot_status()
		angebot = td.angebot(lead.name, zusatz=False)
		if kalkulation:
			# Kalkulation 4.1: 22 min je Einheit x 48 €/h = 17,60 € Lohn je Einheit (Spiegel nach Sales Order)
			frappe.db.set_value(
				"Quotation",
				angebot.name,
				"cv_kalkulation_json",
				json.dumps(
					{
						"ok": True,
						"schema": {"stundensatz": "48.00"},
						"positionen": [
							{
								"id": td.ARTIKEL,
								"item_code": td.ARTIKEL,
								"kalkulation": {
									"minuten": "22.00",
									"lohn": "17.60",
									"erschwernis": "0.00",
									"einzelpreis": "50.00",
									"einzelkosten": "25.88",
								},
							}
						],
					}
				),
				update_modified=False,
			)
		r = _als(BUERO, annahme.angebot_annehmen, angebot.name, einreichen=1)
		return r["auftrag"], r["projekt"]

	def abnehmen(self, projekt: str):
		frappe.db.set_value(
			"Project",
			projekt,
			{"cv_abgenommen_am": today(), "cv_abnahme_ergebnis": "Abgenommen"},
			update_modified=False,
		)

	def freigeben(self, rechnung: str, user: str = BUERO):
		si = frappe.get_doc("Sales Invoice", rechnung)
		_als(user, si.submit)
		si.reload()
		return si

	# --- Einrichtung ------------------------------------------------------------------------

	def test_einrichtung(self):
		e = einstellungen()
		self.assertEqual(
			(e.abschlagsplan_automatisch, e.abschlagsplan_vorlage, e.lohnanteil_prozent_standard),
			(1, "30/40/30", 0.0),
		)
		self.assertEqual(e.abschlag_mindestsumme, 0.0)
		for doctype, feld in (
			("Sales Invoice", "cv_schlussrechnung"),
			("Sales Invoice", "cv_auftrag"),
			("Sales Invoice", "cv_abschlaege"),
			("Sales Invoice", "cv_lohnanteil_brutto"),
			("Sales Invoice", "cv_abrechnung_json"),
			("Sales Invoice Item", "cv_lohnanteil"),
			("Sales Invoice Item", "cv_positionsart"),
		):
			self.assertTrue(frappe.get_meta(doctype).has_field(feld), f"{doctype}.{feld}")
		self.assertTrue(frappe.get_meta("Sales Invoice").get_field("cv_auftrag").read_only)
		self.assertTrue(frappe.db.exists("Item", "ABSCHLAG"))
		self.assertEqual(frappe.db.get_value("Item", "ABSCHLAG", "is_stock_item"), 0)
		self.assertEqual(frappe.db.get_single_value("Selling Settings", "allow_negative_rates_for_items"), 1)
		events = frappe.get_hooks("doc_events")
		self.assertIn("corevatis_erpnext.abrechnung.plan.projekt_angelegt", events["Project"]["after_insert"])
		self.assertIn(
			"corevatis_erpnext.abrechnung.plan.auftrag_eingereicht", events["Sales Order"]["on_submit"]
		)
		self.assertIn(
			"corevatis_erpnext.abrechnung.auftrag.rechnung_eingereicht", events["Sales Invoice"]["on_submit"]
		)
		self.assertIn(
			"corevatis_erpnext.abrechnung.auftrag.rechnung_storniert", events["Sales Invoice"]["on_cancel"]
		)
		self.assertIn(
			"corevatis_erpnext.abrechnung.auftrag.rechnung_freigabe", events["Sales Invoice"]["before_submit"]
		)
		self.assertIn(
			"corevatis_erpnext.abrechnung.auftrag.rechnung_validieren", events["Sales Invoice"]["validate"]
		)
		self.assertIn(
			"corevatis_erpnext.abrechnung.auftrag.zahlung_gebucht", events["Payment Entry"]["on_submit"]
		)
		js = frappe.get_hooks("doctype_js", app_name="corevatis_erpnext")
		for doctype in ("Project", "Sales Order", "Sales Invoice", "Abschlagsplan"):
			self.assertIn("public/js/abrechnung.js", js[doctype], doctype)
		sidebar = frappe.get_doc("Sidebar", "Betrieb")
		links = {(i.link_type, i.link_to) for i in sidebar.items if i.type == "Link"}
		self.assertIn(("DocType", "Abschlagsplan"), links)
		self.assertIn(("DocType", EINSTELLUNGEN), links)
		# Übersetzungen der neuen Doctypes und Felder
		from frappe.translate import get_translations_from_csv

		de = get_translations_from_csv("de", "corevatis_erpnext")
		for doctype in (EINSTELLUNGEN, "Abschlagsplan", "Abschlagsplan Zeile", "Rechnung Abschlag"):
			for df in frappe.get_meta(doctype).fields:
				if df.label:
					self.assertIn(df.label, de, f"{doctype}: {df.label}")
		for df in frappe.get_meta("Sales Invoice").fields:
			if (
				df.fieldname.startswith("cv_")
				and df.label
				and df.fieldname
				in ("cv_schlussrechnung", "cv_auftrag", "cv_lohnanteil_brutto", "cv_abschlaege")
			):
				self.assertIn(df.label, de, df.label)

	# --- Abschlagsplan ----------------------------------------------------------------------

	def test_abschlagsplan_aus_der_annahme_und_pflege(self):
		auftrag, projekt = self.vorgang()
		name = plan.plan_zum_auftrag(auftrag)
		self.assertTrue(name, "Abschlagsplan fehlt nach der Annahme")
		doc = frappe.get_doc("Abschlagsplan", name)
		self.assertEqual(
			(doc.auftrag, doc.projekt, doc.vorlage, doc.status), (auftrag, projekt, "30/40/30", "Offen")
		)
		self.assertEqual(flt(doc.auftragssumme_netto), 500.0)
		self.assertEqual(flt(doc.steuersatz), 19.0)
		self.assertEqual(
			[(z.nr, flt(z.prozent), z.faellig_bei, z.status) for z in doc.zeilen],
			[
				(1, 30.0, "bei Baubeginn", "offen"),
				(2, 40.0, "bei Baufortschritt", "offen"),
				(3, 30.0, "bei Abnahme", "offen"),
			],
		)
		# Engine-Werte je Zeile: 150 / 200 / 150 netto, kumuliert 30/70/100 %
		self.assertEqual(
			[(flt(z.netto), flt(z.brutto), flt(z.kumuliert_prozent)) for z in doc.zeilen],
			[(150.0, 178.5, 30.0), (200.0, 238.0, 70.0), (150.0, 178.5, 100.0)],
		)
		self.assertIn(auftrag, doc.titel)
		# nur ein Plan je Auftrag
		with self.assertRaises(frappe.DuplicateEntryError):
			frappe.get_doc(
				{
					"doctype": "Abschlagsplan",
					"auftrag": auftrag,
					"zeilen": [{"prozent": 10, "faellig_bei": "bei Baubeginn"}],
				}
			).insert()
		# Pflege im Desk: Prozent xor Betrag, über 100 % abgelehnt, Status nicht setzbar
		doc = frappe.get_doc("Abschlagsplan", name)
		doc.zeilen[0].betrag = 10
		with self.assertRaises(frappe.ValidationError):
			_als(BUERO, doc.save)
		doc = frappe.get_doc("Abschlagsplan", name)
		doc.zeilen[2].prozent = 50
		with self.assertRaises(frappe.ValidationError):
			_als(BUERO, doc.save)
		doc = frappe.get_doc("Abschlagsplan", name)
		doc.zeilen[0].status = "bezahlt"
		doc.zeilen[0].rechnung = None
		_als(BUERO, doc.save)
		doc.reload()
		self.assertEqual(doc.zeilen[0].status, "offen")
		# Meister liest, legt aber nicht an
		self.assertTrue(frappe.has_permission("Abschlagsplan", "read", user=MEISTER))
		self.assertFalse(frappe.has_permission("Abschlagsplan", "create", user=MEISTER))
		self.assertFalse(frappe.has_permission("Abschlagsplan", "read", user=MONTEUR))
		# nicht automatisch, wenn abgeschaltet
		e = frappe.get_single(EINSTELLUNGEN)
		e.abschlagsplan_automatisch = 0
		e.save()
		auftrag2, _projekt2 = self.vorgang(kalkulation=False)
		self.assertIsNone(plan.plan_zum_auftrag(auftrag2))

	# --- Abschlag stellen ------------------------------------------------------------------

	def test_abschlag_stellen_und_abgerechneter_anteil(self):
		auftrag, projekt = self.vorgang()
		# Vorschlag: nächste Zeile 30 %
		v = _als(BUERO, abschlag.abschlag_vorschlag, project=projekt)
		self.assertEqual((v["auftrag"]["netto"], v["auftrag"]["steuersatz"]), (500.0, 19.0))
		self.assertEqual(
			(v["naechste"]["nr"], v["naechste"]["prozent"], v["naechste"]["netto"]), (1, 30.0, 150.0)
		)
		self.assertEqual(v["abschlaege"], [])
		# 1. Abschlag 30 % → Rechnung im Entwurf
		r = _als(BUERO, abschlag.abschlag_stellen, project=projekt)
		self.assertEqual(
			(r["nr"], r["prozent"], r["netto"], r["steuer"], r["brutto"]), (1, 30.0, 150.0, 28.5, 178.5)
		)
		self.assertEqual(r["text"], f"Abschlagsrechnung Nr. 1 zum Auftrag {auftrag} (30 % der Auftragssumme)")
		si = frappe.get_doc("Sales Invoice", r["rechnung"])
		self.assertEqual(si.docstatus, 0)
		self.assertEqual(
			(si.customer, si.project, si.cv_abschlag, si.cv_schlussrechnung, si.cv_auftrag),
			(frappe.db.get_value("Sales Order", auftrag, "customer"), projekt, 1, 0, auftrag),
		)
		self.assertEqual(
			(
				si.cv_abschlag_nr,
				flt(si.cv_abschlag_prozent),
				flt(si.cv_kumuliert_prozent),
				flt(si.cv_auftragssumme_netto),
			),
			(1, 30.0, 30.0, 500.0),
		)
		self.assertEqual(len(si.items), 1)
		z = si.items[0]
		self.assertEqual(
			(
				z.item_code,
				flt(z.qty),
				flt(z.rate),
				flt(z.amount),
				z.sales_order,
				z.so_detail,
				z.cv_positionsart,
			),
			("ABSCHLAG", 1.0, 150.0, 150.0, auftrag, None, "Abschlag"),
		)
		self.assertIn("Abschlagsrechnung Nr. 1", z.description)
		self.assertIn("fällig bei Baubeginn", z.description)
		self.assertEqual(
			(flt(si.net_total), flt(si.total_taxes_and_charges), flt(si.grand_total)), (150.0, 28.5, 178.5)
		)
		self.assertEqual(
			si.taxes_and_charges, frappe.db.get_value("Sales Order", auftrag, "taxes_and_charges")
		)
		self.assertEqual(si.payment_schedule[0].payment_amount, 178.5)
		# Lohnanteil anteilig: Auftrag 10 x 17,60 = 176 → 30 % = 52,80
		self.assertEqual((flt(si.cv_lohnanteil_netto), flt(si.cv_lohnanteil_brutto)), (52.8, 62.83))
		self.assertEqual(flt(z.cv_lohnanteil), 52.8)
		self.assertTrue(json.loads(si.cv_abrechnung_json)["ok"])
		# 6.2: Abschlag vor der Abnahme erlaubt (cv_abschlag) - Entwurf wurde gespeichert
		doc = frappe.get_doc("Abschlagsplan", plan.plan_zum_auftrag(auftrag))
		self.assertEqual(
			(doc.zeilen[0].status, doc.zeilen[0].rechnung, doc.status),
			("Entwurf", si.name, "Teilweise gestellt"),
		)
		# Prozessleiste: Entwurf
		stand = _als(BUERO, status, "Project", projekt)
		schritt = _schritt(stand, "rechnung")
		self.assertEqual(schritt["status"], "aktiv")
		self.assertIn("Abschlag 1 im Entwurf (30 %)", schritt["text"])
		self.assertEqual(
			(schritt["aktion"]["label"], schritt["aktion"]["doctype"], schritt["aktion"]["name"]),
			("Rechnung freigeben", "Sales Invoice", si.name),
		)
		# noch kein Anteil am Auftrag
		self.assertEqual(flt(frappe.db.get_value("Sales Order", auftrag, "per_billed")), 0.0)
		# freigeben → 30 % abgerechnet, Zeile „gestellt“
		self.freigeben(si.name)
		self.assertEqual(flt(frappe.db.get_value("Sales Order", auftrag, "per_billed")), 30.0)
		self.assertEqual(frappe.db.get_value("Sales Order", auftrag, "billing_status"), "Partly Billed")
		doc.reload()
		self.assertEqual(
			(doc.zeilen[0].status, flt(doc.gestellt_prozent), flt(doc.gestellt_netto)),
			("gestellt", 30.0, 150.0),
		)
		stand = _als(BUERO, status, "Sales Order", auftrag)
		schritt = _schritt(stand, "rechnung")
		self.assertIn("Abschlag 1 von 3 gestellt (30 %", schritt["text"])
		self.assertIn("2 offen", schritt["text"])
		self.assertEqual(schritt["aktion"]["label"], "Abschlag stellen")
		self.assertEqual(schritt["aktion"]["doctype"], "Project")
		self.assertEqual(len(schritt["belege"]), 1)
		# 2. Abschlag: Zeile 2 (40 %) mit abweichendem Prozent 20 → 100 € netto, kumuliert 50 %
		r2 = _als(BUERO, abschlag.abschlag_stellen, sales_order=auftrag, prozent=20)
		self.assertEqual((r2["nr"], r2["netto"], r2["kumuliert"]["prozent"]), (2, 100.0, "50.00"))
		si2 = frappe.get_doc("Sales Invoice", r2["rechnung"])
		self.assertEqual(len(si2.cv_abschlaege), 1)
		self.assertEqual(
			(si2.cv_abschlaege[0].rechnung, flt(si2.cv_abschlaege[0].brutto), si2.cv_abschlaege[0].bezahlt),
			(si.name, 178.5, 0),
		)
		self.freigeben(si2.name)
		self.assertEqual(flt(frappe.db.get_value("Sales Order", auftrag, "per_billed")), 50.0)
		doc.reload()
		self.assertEqual(
			[(z.status, flt(z.prozent)) for z in doc.zeilen],
			[("gestellt", 30.0), ("gestellt", 20.0), ("offen", 30.0)],
		)
		# 3. Abschlag 60 % → über der Auftragssumme: ohne Bestätigung abgelehnt, mit Bestätigung Hinweis
		with self.assertRaises(AbrechnungFehler):
			_als(BUERO, abschlag.abschlag_stellen, sales_order=auftrag, prozent=60)
		self.assertEqual(len(frappe.get_all("Sales Invoice", filters={"cv_auftrag": auftrag})), 2)
		r3 = _als(BUERO, abschlag.abschlag_stellen, sales_order=auftrag, prozent=60, bestaetigen=1)
		self.assertTrue(any("Auftragssumme" in w for w in r3["warnungen"]))
		self.assertEqual(r3["netto"], 300.0)
		# Entwurf löschen → Zeile wieder offen
		frappe.delete_doc("Sales Invoice", r3["rechnung"])
		doc.reload()
		self.assertEqual((doc.zeilen[2].status, doc.zeilen[2].rechnung), ("offen", None))
		# Stornieren des 2. Abschlags → Anteil 30 %, Zeile offen
		si2.reload()
		si2.cancel()
		self.assertEqual(flt(frappe.db.get_value("Sales Order", auftrag, "per_billed")), 30.0)
		doc.reload()
		self.assertEqual((doc.zeilen[1].status, doc.zeilen[1].rechnung), ("offen", None))

	def test_rechte_und_eingaben(self):
		auftrag, projekt = self.vorgang(kalkulation=False)
		for user in (MEISTER, MONTEUR):
			with self.assertRaises(frappe.PermissionError):
				_als(user, abschlag.abschlag_stellen, project=projekt)
			with self.assertRaises(frappe.PermissionError):
				_als(user, schluss.schlussrechnung_erstellen, project=projekt)
			with self.assertRaises(frappe.PermissionError):
				_als(user, abschlag.abschlag_vorschlag, project=projekt)
		self.assertEqual(frappe.get_all("Sales Invoice", filters={"cv_auftrag": auftrag}), [])
		# Beträge nur aus Serverdaten: Prozent und Betrag zugleich, negative Werte
		with self.assertRaises(frappe.MandatoryError):
			_als(BUERO, abschlag.abschlag_stellen, project=projekt, prozent=10, betrag=50)
		with self.assertRaises(frappe.ValidationError):
			_als(BUERO, abschlag.abschlag_stellen, project=projekt, prozent=-5)
		with self.assertRaises(frappe.ValidationError):
			_als(BUERO, abschlag.abschlag_stellen, project=projekt, prozent=150)
		# Projekt ohne Auftrag; fremdes Projekt zum Auftrag (Security-Review: Originalargument prüfen)
		leer = frappe.get_doc(
			{
				"doctype": "Project",
				"project_name": f"Ohne Auftrag {frappe.generate_hash(length=5)}",
				"status": "Open",
			}
		).insert(ignore_permissions=True)
		with self.assertRaises(AbrechnungFehler):
			_als(BUERO, abschlag.abschlag_stellen, project=leer.name)
		with self.assertRaises(AbrechnungFehler):
			_als(BUERO, abschlag.abschlag_stellen, sales_order=auftrag, project=leer.name)
		with self.assertRaises(AbrechnungFehler):
			_als(BUERO, abschlag.abschlag_vorschlag, sales_order=auftrag, project=leer.name)
		with self.assertRaises(AbrechnungFehler):
			_als(BUERO, schluss.schlussrechnung_vorschlag, sales_order=auftrag, project=leer.name)
		self.assertEqual(
			_als(BUERO, abschlag.abschlag_vorschlag, sales_order=auftrag, project=projekt)["projekt"], projekt
		)
		# Betrag statt Prozent: 125 € = 25 %
		r = _als(
			BUERO, abschlag.abschlag_stellen, project=projekt, betrag=125, bezeichnung="Anzahlung Material"
		)
		self.assertEqual((r["netto"], r["prozent"]), (125.0, 25.0))
		self.assertIn("Teilbetrag", r["text"]) if "Teilbetrag" in r["text"] else self.assertIn(
			"25 %", r["text"]
		)
		doc = frappe.get_doc("Abschlagsplan", plan.plan_zum_auftrag(auftrag))
		self.assertEqual(
			(flt(doc.zeilen[0].betrag), flt(doc.zeilen[0].prozent), doc.zeilen[0].bezeichnung),
			(125.0, 0.0, "Anzahlung Material"),
		)

	def test_server_felder_und_freigabe_geschuetzt(self):
		"""Security-Review: Abrechnungsfelder setzt nur der Server; Freigabe prüft Rolle, Kunde, Positionen."""
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

		auftrag, projekt = self.vorgang(kalkulation=False)
		plan_name = plan.plan_zum_auftrag(auftrag)
		# 1. Eine gewöhnliche Rechnung lässt sich nicht als Schlussrechnung/Abschlag des Auftrags ausgeben
		si = make_sales_invoice(auftrag)
		si.update(
			{
				"cv_abschlag": 1,  # 6.2: darf das Büro setzen (Abnahmesperre)
				"cv_schlussrechnung": 1,
				"cv_auftrag": auftrag,
				"cv_abschlagsplan": plan_name,
				"cv_abschlag_nr": 5,
				"cv_abschlag_prozent": 50,
				"cv_lohnanteil_brutto": 999,
			}
		)
		si.items[0].cv_positionsart = "Nachtrag"
		si.items[0].cv_lohnanteil = 123
		_als(BUERO, si.insert)
		si.reload()
		self.assertEqual(si.cv_abschlag, 1)
		self.assertEqual(
			(si.cv_schlussrechnung, si.cv_auftrag, si.cv_abschlagsplan, si.cv_abschlag_nr), (0, None, None, 0)
		)
		self.assertEqual((flt(si.cv_abschlag_prozent), flt(si.cv_lohnanteil_brutto)), (0.0, 0.0))
		self.assertFalse(si.items[0].cv_positionsart)
		self.assertEqual(flt(si.items[0].cv_lohnanteil), 0.0)
		self.assertEqual(A.rechnungen_zum_auftrag(auftrag), [])
		si.delete()
		# 2. Felder einer echten Abschlagsrechnung bleiben beim Speichern durch den Benutzer, wie sie waren
		r = _als(BUERO, abschlag.abschlag_stellen, project=projekt)
		si = frappe.get_doc("Sales Invoice", r["rechnung"])
		si.cv_auftrag = None
		si.cv_abschlag_nr = 9
		si.cv_schlussrechnung = 1
		si.items[0].cv_positionsart = "Nachtrag"
		si.remarks = "geprüft"
		_als(BUERO, si.save)
		si.reload()
		self.assertEqual(
			(si.cv_auftrag, si.cv_abschlag_nr, si.cv_schlussrechnung, si.remarks), (auftrag, 1, 0, "geprüft")
		)
		self.assertEqual(si.items[0].cv_positionsart, "Abschlag")
		# 3. Freigabe nur durch das Büro: Buchhaltung ohne Rolle Büro darf buchen, aber keinen Abschlag freigeben
		self.assertTrue(frappe.has_permission("Sales Invoice", "submit", si.name, user=BUCHHALTER))
		with self.assertRaises(frappe.PermissionError):
			_als(BUCHHALTER, frappe.get_doc("Sales Invoice", si.name).submit)
		self.assertEqual(frappe.db.get_value("Sales Invoice", si.name, "docstatus"), 0)
		# 4. Positionen müssen zum Auftrag gehören
		si = frappe.get_doc("Sales Invoice", si.name)
		si.append("items", {"item_code": "ABSCHLAG", "qty": 1, "rate": 10})
		_als(BUERO, si.save)
		with self.assertRaises(AbrechnungFehler):
			_als(BUERO, frappe.get_doc("Sales Invoice", si.name).submit)
		si = frappe.get_doc("Sales Invoice", si.name)
		si.remove(si.items[1])
		_als(BUERO, si.save)
		self.freigeben(si.name)
		self.assertEqual(flt(frappe.db.get_value("Sales Order", auftrag, "per_billed")), 30.0)

	# --- Schlussrechnung -------------------------------------------------------------------

	def test_schlussrechnung_mit_abschlaegen_nachtrag_mehr_und_mindermenge(self):
		auftrag, projekt = self.vorgang()
		r1 = _als(BUERO, abschlag.abschlag_stellen, project=projekt)  # 30 % = 150
		self.freigeben(r1["rechnung"])
		r2 = _als(BUERO, abschlag.abschlag_stellen, project=projekt)  # 40 % = 200
		# Entwurf blockiert die Schlussrechnung
		self.abnehmen(projekt)
		with self.assertRaises(AbrechnungFehler):
			_als(BUERO, schluss.schlussrechnung_erstellen, project=projekt)
		self.freigeben(r2["rechnung"])
		self.assertEqual(flt(frappe.db.get_value("Sales Order", auftrag, "per_billed")), 70.0)
		# Vorschlag
		v = _als(BUERO, schluss.schlussrechnung_vorschlag, project=projekt)
		self.assertEqual(len(v["positionen"]), 1)
		p = v["positionen"][0]
		self.assertEqual(
			(
				p["item_code"],
				p["menge_auftrag"],
				p["menge_ist"],
				p["einzelpreis"],
				p["lohn_je_einheit"],
				p["lohnanteil"],
				p["lohn_quelle"],
			),
			(td.ARTIKEL, 10.0, 10.0, 50.0, 17.6, 176.0, "kalkulation"),
		)
		self.assertEqual(
			[(a["nr"], a["netto"], a["brutto"]) for a in v["abschlaege"]],
			[(1, 150.0, 178.5), (2, 200.0, 238.0)],
		)
		self.assertEqual(v["entwuerfe"], [])
		self.assertTrue(v["abnahme"]["abgenommen"])
		so_detail = p["so_detail"]
		# Schlussrechnung: Ist 12 statt 10 (Mehrmenge 2), Nachtrag 1 x 80 € mit Lohnanteil 60
		r = _als(
			BUERO,
			schluss.schlussrechnung_erstellen,
			project=projekt,
			positionen=json.dumps({so_detail: {"menge_ist": 12}}),
			nachtraege=json.dumps(
				[
					{
						"item_code": NACHTRAG_ARTIKEL,
						"bezeichnung": "Durchbruch verschließen",
						"menge": 1,
						"einzelpreis": 80,
						"lohnanteil": 60,
					}
				]
			),
		)
		self.assertFalse(r["bereits_vorhanden"])
		# Leistung 12 x 50 + 80 = 680; Abschläge 350; Rest 330 netto, 62,70 USt, 392,70 brutto
		s = r["summen"]
		self.assertEqual(
			(
				s["leistung"]["netto"],
				s["abschlaege"]["netto"],
				s["rest"]["netto"],
				s["rest"]["steuer"],
				s["rest"]["brutto"],
			),
			("680.00", "350.00", "330.00", "62.70", "392.70"),
		)
		# Lohnanteil: 12 x 17,60 = 211,20 + 60 = 271,20
		self.assertEqual(s["lohnanteil"]["netto"], "271.20")
		self.assertTrue(any("Mehrmenge" in w for w in r["warnungen"]))
		si = frappe.get_doc("Sales Invoice", r["rechnung"])
		self.assertEqual(
			(si.docstatus, si.cv_schlussrechnung, si.cv_abschlag, si.cv_auftrag, si.project),
			(0, 1, 0, auftrag, projekt),
		)
		arten = [
			(z.cv_positionsart, z.item_code, flt(z.qty), flt(z.rate), z.so_detail is not None)
			for z in si.items
		]
		self.assertEqual(
			arten,
			[
				("Auftragsposition", td.ARTIKEL, 10.0, 50.0, True),
				("Mehrmenge", td.ARTIKEL, 2.0, 50.0, False),
				("Nachtrag", NACHTRAG_ARTIKEL, 1.0, 80.0, False),
				("Abzug Abschlag", "ABSCHLAG", 1.0, -150.0, False),
				("Abzug Abschlag", "ABSCHLAG", 1.0, -200.0, False),
			],
		)
		self.assertEqual([flt(z.cv_lohnanteil) for z in si.items], [176.0, 35.2, 60.0, 0.0, 0.0])
		self.assertEqual(flt(si.items[0].cv_menge_auftrag), 10.0)
		self.assertEqual(si.items[1].cv_bezug, so_detail)
		self.assertIn("abzüglich Abschlagsrechnung Nr. 1", si.items[3].description)
		self.assertEqual(si.items[3].cv_bezug, r1["rechnung"])
		# ERPNext rechnet dieselben Summen wie die Engine
		self.assertEqual(
			(flt(si.net_total), flt(si.total_taxes_and_charges), flt(si.grand_total)), (330.0, 62.7, 392.7)
		)
		self.assertEqual(
			(flt(si.cv_leistung_netto), flt(si.cv_leistung_steuer), flt(si.cv_leistung_brutto)),
			(680.0, 129.2, 809.2),
		)
		self.assertEqual((flt(si.cv_abschlaege_netto), flt(si.cv_abschlaege_brutto)), (350.0, 416.5))
		self.assertEqual(
			(flt(si.cv_lohnanteil_netto), flt(si.cv_lohnanteil_steuer), flt(si.cv_lohnanteil_brutto)),
			(271.2, 51.53, 322.73),
		)
		self.assertEqual(
			[(a.nr, a.rechnung, flt(a.netto), flt(a.brutto)) for a in si.cv_abschlaege],
			[(1, r1["rechnung"], 150.0, 178.5), (2, r2["rechnung"], 200.0, 238.0)],
		)
		self.assertEqual(json.loads(si.cv_abrechnung_json)["summen"]["rest"]["brutto"], "392.70")
		# Plan kennt die Schlussrechnung; zweiter Aufruf legt nichts an
		doc = frappe.get_doc("Abschlagsplan", plan.plan_zum_auftrag(auftrag))
		self.assertEqual(doc.schlussrechnung, si.name)
		r_wieder = _als(BUERO, schluss.schlussrechnung_erstellen, project=projekt)
		self.assertEqual((r_wieder["rechnung"], r_wieder["bereits_vorhanden"]), (si.name, True))
		with self.assertRaises(AbrechnungFehler):
			_als(BUERO, abschlag.abschlag_stellen, project=projekt, prozent=10)
		# Prozessleiste: Entwurf, danach erledigt
		schritt = _schritt(_als(BUERO, status, "Project", projekt), "rechnung")
		self.assertEqual(
			(schritt["status"], schritt["text"]),
			("aktiv", "Schlussrechnung im Entwurf: prüfen und freigeben"),
		)
		self.assertEqual(schritt["aktion"]["label"], "Rechnung freigeben")
		self.freigeben(si.name)
		self.assertEqual(flt(frappe.db.get_value("Sales Order", auftrag, "per_billed")), 100.0)
		doc.reload()
		self.assertEqual(doc.status, "Schlussrechnung")
		schritt = _schritt(_als(BUERO, status, "Project", projekt), "rechnung")
		self.assertEqual(schritt["status"], "erledigt")
		self.assertIn(f"Schlussrechnung {si.name}", schritt["text"])
		self.assertIn("offen", schritt["text"])
		self.assertIsNone(schritt.get("aktion"))
		# Rechnungsformular: Zustandstext
		from corevatis_erpnext.abrechnung.rechnung import zustand

		z = _als(BUERO, zustand, si.name)
		self.assertEqual(z["art"], "schlussrechnung")
		self.assertIn("abzüglich 2 Abschlagsrechnung(en)", z["text"])
		self.assertIn("freigegeben am", z["text"])
		z1 = _als(BUERO, zustand, r1["rechnung"])
		self.assertIn("Abschlagsrechnung Nr. 1 (30 %)", z1["text"])
		self.assertIn(f"Schlussrechnung {si.name} liegt vor", z1["text"])
		self.assertEqual(z1["auftrag"], auftrag)

	def test_schlussrechnung_vor_der_abnahme_und_mindermenge(self):
		auftrag, projekt = self.vorgang(kalkulation=False)
		# 6.2: ohne Abnahme blockiert (Büro), System Manager nur mit Begründung
		with self.assertRaises(AbrechnungFehler):
			_als(BUERO, schluss.schlussrechnung_erstellen, project=projekt)
		with self.assertRaises(frappe.PermissionError):
			_als(
				BUERO,
				schluss.schlussrechnung_erstellen,
				project=projekt,
				uebersteuern=1,
				grund="Kunde zahlt vorab, Abnahme folgt",
			)
		with self.assertRaises(frappe.MandatoryError):
			schluss.schlussrechnung_erstellen(project=projekt, uebersteuern=1, grund="kurz")
		self.assertEqual(frappe.get_all("Sales Invoice", filters={"cv_auftrag": auftrag}), [])
		# Mindermenge 8 statt 10, Lohnanteil-Rückfall 50 %
		e = frappe.get_single(EINSTELLUNGEN)
		e.lohnanteil_prozent_standard = 50
		e.save()
		so_detail = frappe.get_doc("Sales Order", auftrag).items[0].name
		r = schluss.schlussrechnung_erstellen(
			project=projekt,
			positionen=json.dumps({so_detail: {"menge_ist": 8}}),
			uebersteuern=1,
			grund="Kunde zahlt vorab, Abnahme folgt",
		)
		self.assertTrue(r["uebersteuert"])
		si = frappe.get_doc("Sales Invoice", r["rechnung"])
		self.assertEqual((si.cv_abnahme_uebersteuert, si.cv_uebersteuert_von), (1, "Administrator"))
		self.assertEqual(
			[(z.cv_positionsart, flt(z.qty), flt(z.cv_lohnanteil)) for z in si.items],
			[("Auftragsposition", 8.0, 200.0)],
		)
		self.assertEqual(
			(flt(si.net_total), flt(si.grand_total), flt(si.cv_lohnanteil_prozent)), (400.0, 476.0, 50.0)
		)
		self.assertTrue(any("Mindermenge" in w for w in r["warnungen"]))
		# nach dem Freigeben gilt der Auftrag als voll abgerechnet (Schlussrechnung schließt ab)
		si.submit()
		self.assertEqual(flt(frappe.db.get_value("Sales Order", auftrag, "per_billed")), 100.0)
		# Abschläge über der Leistung → Gutschrift nötig, keine Schlussrechnung
		auftrag2, projekt2 = self.vorgang(kalkulation=False)
		self.abnehmen(projekt2)
		rr = _als(BUERO, abschlag.abschlag_stellen, project=projekt2, prozent=90)
		self.freigeben(rr["rechnung"])
		so_detail2 = frappe.get_doc("Sales Order", auftrag2).items[0].name
		with self.assertRaises(AbrechnungFehler):
			_als(
				BUERO,
				schluss.schlussrechnung_erstellen,
				project=projekt2,
				positionen=json.dumps({so_detail2: {"menge_ist": 5}}),
			)
		# unbekannte Position, ungültiger Nachtrag
		with self.assertRaises(AbrechnungFehler):
			_als(
				BUERO,
				schluss.schlussrechnung_erstellen,
				project=projekt2,
				positionen=json.dumps({"xyz": {"menge_ist": 1}}),
			)
		with self.assertRaises(frappe.MandatoryError):
			_als(
				BUERO,
				schluss.schlussrechnung_erstellen,
				project=projekt2,
				nachtraege=json.dumps([{"menge": 1, "einzelpreis": 5}]),
			)
		with self.assertRaises(frappe.ValidationError):
			_als(
				BUERO,
				schluss.schlussrechnung_erstellen,
				project=projekt2,
				nachtraege=json.dumps([{"item_code": NACHTRAG_ARTIKEL, "menge": 1, "einzelpreis": -5}]),
			)
		# 6.2 greift auch direkt: eine normale Rechnung zum Projekt ohne Abnahme bleibt gesperrt
		auftrag3, _projekt3 = self.vorgang(kalkulation=False)
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

		with self.assertRaises(RechnungOhneAbnahme):
			_als(BUERO, make_sales_invoice(auftrag3).insert)
