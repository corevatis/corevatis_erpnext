"""Gemeinsame Hilfen der Abrechnungstests 7.3/7.5 (Rechnungsdruck, Mahnwesen, Zahlungseingang).

Die CI-Bench hat ``corevatis`` nicht: ``KernStellvertreter`` aus ``test_abrechnung`` ersetzt die Engine
(nur die Namen werden importiert, die Testklasse dort nicht), ``AnteileStellvertreter`` die Engine
``corevatis.engine.anteile`` (Lohn und Material, #105). Angebot, Annahme, Auftrag, Projekt,
Rechnung, Zahlung und Mahnung sind echt (ERPNext).
"""

from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

import frappe
from frappe.utils import add_days, today

from corevatis_erpnext.abrechnung import kern, lohn_material
from corevatis_erpnext.angebot import annahme
from corevatis_erpnext.tests import _angebot as td
from corevatis_erpnext.tests._helpers import make_user
from corevatis_erpnext.tests.test_abrechnung import (
	BUERO,
	MEISTER,
	MONTEUR,
	KernStellvertreter,
	_als,
	_c,
	_d,
	_s,
)

__all__ = [
	"BUERO",
	"MEISTER",
	"MONTEUR",
	"AnteileStellvertreter",
	"KernStellvertreter",
	"_als",
	"abnehmen",
	"anteile_patch",
	"datieren",
	"einrichten",
	"freigeben",
	"kern_patch",
	"vorgang",
]


def einrichten() -> None:
	"""Felder, Artikel, Einstellungen, Mahntypen, Benutzer (wie nach ``migrate``)."""
	from corevatis_erpnext.abnahme.felder import ensure_abnahme_felder
	from corevatis_erpnext.abrechnung import ensure_abrechnung_setup

	td.ensure_felder_41()
	td.ensure_artikel()
	ensure_abnahme_felder()
	ensure_abrechnung_setup()
	make_user(
		BUERO, "Berta", ["Büro", "Sales User", "Accounts User", "Projects User", "Projects Manager"], "Büro"
	)
	make_user(MEISTER, "Martin", ["Meister", "Sales User", "Projects User", "Projects Manager"], "Meister")
	make_user(MONTEUR, "Mona", ["Monteur"], "Monteur")
	frappe.db.commit()


def kern_patch(testcase) -> KernStellvertreter:
	stellvertreter = KernStellvertreter()
	p = patch.object(kern, "_aufloesen", stellvertreter.aufloesen)
	p.start()
	testcase.addCleanup(p.stop)
	return stellvertreter


class AnteileStellvertreter:
	"""Antworten wie ``corevatis.engine.anteile`` für die Testfälle (Rechenregeln und Rundung prüft
	``corevatis/tests/test_engine_anteile.py``); prüft die Eingabeschlüssel gegen den Vertrag."""

	def __init__(self):
		self.aufrufe = []

	def aufteilen(self, positionen, optionen=None):
		o = optionen or {}
		self.aufrufe.append((positionen, o))
		assert set(o) <= set(lohn_material.OPTIONEN_SCHLUESSEL), o
		aus, summe, lohn_summe, anzahl = [], Decimal(0), Decimal(0), 0
		for p in positionen:
			assert set(p) <= set(lohn_material.POSITION_SCHLUESSEL), p
			b, k = _d(p["betrag"]), p.get("kalkulation")
			if "lohnanteil" in p:
				quelle, lohn = "fest", _c(_d(p["lohnanteil"]))
			elif p.get("bezug"):
				quelle, lohn = "anteilig", _c(_d(p["bezug"]["lohn"]) * b / _d(p["bezug"]["betrag"]))
			elif p.get("regie"):
				quelle, lohn = "regie", b
			elif k:
				assert set(k) <= set(lohn_material.KALKULATION_SCHLUESSEL), k
				lk = _d(k["lohn"]) + _d(k.get("erschwernis") or 0)
				ek = _d(k.get("einzelkosten") or 0) or (
					_d(k.get("material") or 0) + _d(k.get("materialzuschlag") or 0) + lk
				)
				quelle = "kalkulation"
				lohn = _c(b * lk / ek) if o.get("mit_zuschlaegen") else min(_c(lk * _d(p["menge"])), b)
			else:
				quelle, lohn = "rueckfall", _c(b * _d(o.get("lohnanteil_prozent_standard") or 0) / 100)
			aus.append(
				{"id": p["id"], "betrag": _s(b), "material": _s(b - lohn), "lohn": _s(lohn), "quelle": quelle}
			)
			if p.get("in_summe", True):
				summe, lohn_summe, anzahl = summe + b, lohn_summe + lohn, anzahl + 1
		netto = _d(o["netto"]) if o.get("netto") not in (None, "") else summe
		if summe and netto != summe:
			lohn_summe = _c(lohn_summe * netto / summe)
		satz = _d(o.get("steuersatz") or 0)
		steuer, steuer_lohn = _c(netto * satz / 100), _c(lohn_summe * satz / 100)
		material = netto - lohn_summe
		return {
			"ok": True,
			"fehler": [],
			"warnungen": [],
			"positionen": aus,
			"summen": {
				"netto": _s(netto),
				"steuer": _s(steuer),
				"brutto": _s(netto + steuer),
				"material": {
					"netto": _s(material),
					"steuer": _s(steuer - steuer_lohn),
					"brutto": _s(material + steuer - steuer_lohn),
				},
				"lohn": {
					"netto": _s(lohn_summe),
					"steuer": _s(steuer_lohn),
					"brutto": _s(lohn_summe + steuer_lohn),
				},
				"lohn_prozent": _s(lohn_summe / netto * 100) if netto else "0.00",
				"anzahl_positionen": anzahl,
			},
		}


def anteile_patch(testcase) -> AnteileStellvertreter:
	"""``lohn_material`` rechnet mit dem Stellvertreter (die Bench hat den Kern nicht)."""
	stellvertreter = AnteileStellvertreter()
	p = patch.object(lohn_material, "_engine", lambda: stellvertreter.aufteilen)
	p.start()
	testcase.addCleanup(p.stop)
	return stellvertreter


def vorgang(kalkulation: bool = True):
	"""Angebot (10 x 50 € = 500 netto, 19 %) annehmen → Auftrag + Projekt (+ Abschlagsplan per Hook)."""
	lead = td.lead_mit_angebot_status()
	angebot = td.angebot(lead.name, zusatz=False)
	if kalkulation:
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
		# wie der Generator: Material/Lohn aus der Kalkulation (#105; ohne Engine passiert nichts)
		lohn_material.nachtragen("Quotation", angebot.name)
	r = _als(BUERO, annahme.angebot_annehmen, angebot.name, einreichen=1)
	return r["auftrag"], r["projekt"]


def abnehmen(projekt: str) -> None:
	frappe.db.set_value(
		"Project",
		projekt,
		{"cv_abgenommen_am": today(), "cv_abnahme_ergebnis": "Abgenommen"},
		update_modified=False,
	)


def datieren(rechnung: str, tage_alt: int, zahlungsziel: int = 14) -> None:
	"""Entwurf einer Rechnung zurückdatieren: Buchungsdatum ``tage_alt`` Tage her, fällig ``zahlungsziel``
	Tage danach (Zahlungsplan wird neu aufgebaut)."""
	doc = frappe.get_doc("Sales Invoice", rechnung)
	doc.set_posting_time = 1
	doc.posting_date = add_days(today(), -tage_alt)
	doc.due_date = add_days(doc.posting_date, zahlungsziel)
	doc.set("payment_schedule", [])
	doc.save()
	# Fälligkeit unabhängig von Zahlungsbedingungen der Site fest auf posting + zahlungsziel
	frappe.db.set_value("Sales Invoice", doc.name, "due_date", doc.due_date, update_modified=False)
	frappe.db.set_value(
		"Payment Schedule",
		{"parent": doc.name, "parenttype": "Sales Invoice"},
		"due_date",
		doc.due_date,
		update_modified=False,
	)


def freigeben(rechnung: str, user: str = BUERO):
	si = frappe.get_doc("Sales Invoice", rechnung)
	_als(user, si.submit)
	si.reload()
	return si
