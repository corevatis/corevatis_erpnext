"""Pläne mit Pins (Phase 3 P3.3) im Adapter: Seitenleiste „Pläne“ nach „Objekte“ und Pin-Nummern in der
Foto-Galerie am Befund (Ergänzung ``nachher`` der Galerie, Kern-Methode ``befund_pins``). Datei-basiert:
der Kern ``corevatis`` (Doctype ``Plan``) ist auf der CI-Site nicht installiert."""

from __future__ import annotations

import json
import pathlib

from frappe.tests import UnitTestCase

BASIS = pathlib.Path(__file__).parents[1]


class TestPlaeneDesk(UnitTestCase):
	def test_seitenleiste(self):
		daten = json.loads(
			(BASIS / "corevatis_erpnext/sidebar/betrieb/betrieb.json").read_text(encoding="utf-8")
		)
		labels = [i.get("label") for i in daten["items"]]
		eintrag = daten["items"][labels.index("Pläne")]
		self.assertEqual(
			(eintrag["type"], eintrag["link_type"], eintrag["link_to"]), ("Link", "DocType", "Plan")
		)
		self.assertEqual(labels.index("Pläne"), labels.index("Objekte") + 1)

	def test_galerie_pins(self):
		galerie = (BASIS / "public/js/galerie.js").read_text(encoding="utf-8")
		self.assertIn("opts.nachher(container, bilder)", galerie)
		befund = (BASIS / "public/js/befund_galerie.js").read_text(encoding="utf-8")
		# Kern über eine whitelisted Methode, kein Python-Import
		self.assertIn('method: "corevatis.plaene.desk.befund_pins"', befund)
		self.assertIn("nachher:", befund)
