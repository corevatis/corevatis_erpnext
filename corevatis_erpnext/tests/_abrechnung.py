"""Gemeinsame Hilfen der Abrechnungstests 7.3/7.5 (Rechnungsdruck, Mahnwesen, Zahlungseingang).

Die CI-Bench hat ``corevatis`` nicht: ``KernStellvertreter`` aus ``test_abrechnung`` ersetzt die Engine
(nur die Namen werden importiert, die Testklasse dort nicht). Angebot, Annahme, Auftrag, Projekt,
Rechnung, Zahlung und Mahnung sind echt (ERPNext).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import frappe
from frappe.utils import add_days, today

from corevatis_erpnext.abrechnung import kern
from corevatis_erpnext.angebot import annahme
from corevatis_erpnext.tests import _angebot as td
from corevatis_erpnext.tests._helpers import make_user
from corevatis_erpnext.tests.test_abrechnung import BUERO, MEISTER, MONTEUR, KernStellvertreter, _als

__all__ = [
	"BUERO",
	"MEISTER",
	"MONTEUR",
	"KernStellvertreter",
	"_als",
	"abnehmen",
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


def freigeben(rechnung: str, user: str = BUERO):
	si = frappe.get_doc("Sales Invoice", rechnung)
	_als(user, si.submit)
	si.reload()
	return si
