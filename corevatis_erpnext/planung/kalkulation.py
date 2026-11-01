"""Kalkulation des angenommenen Angebots als Quelle der Sollminuten (Handwerker#104).

Arbeitspakete (5.1) und Nachkalkulation (P2.1) lesen die Minuten je Einheit aus derselben Stelle:
der im Angebot eingefrorenen Kalkulation (``Quotation.cv_kalkulation_json``, 4.1). Die Stückliste
ist nur Rückfall, wenn das Angebot keine Kalkulation trägt (Altbestand, Handposition).

Zuordnung (wie P2.1): Angebote über ``Sales Order Item.prevdoc_docname``; je Artikel die Positionen
der Kalkulation in ihrer Reihenfolge, die n-te Auftragsposition eines Artikels bekommt die n-te
Kalkulationsposition dieses Artikels (``zuordnen``).

Stundensatz aus der Kalkulation (``schema.stundensatz``), sonst aus dem Kalkulationsschema des
Angebots (Kern-Doctype nur per Name gelesen, Lizenzgrenze).
"""

from __future__ import annotations

import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt

SCHEMA_DOCTYPE = "Kalkulationsschema"


def kalkulation_laden(auftrag) -> tuple[dict[str, list[dict]], float, str | None, list[str]]:
	"""Positionen der Kalkulation je Artikel (Reihenfolge wie im Angebot), Stundensatz, Schema, Hinweise."""
	hinweise: list[str] = []
	angebote = []
	for z in auftrag.items:
		if z.get("prevdoc_docname") and z.prevdoc_docname not in angebote:
			angebote.append(z.prevdoc_docname)
	je_artikel: dict[str, list[dict]] = defaultdict(list)
	stundensatz = 0.0
	schema_name = None
	if not frappe.db.has_column("Quotation", "cv_kalkulation_json"):
		return je_artikel, stundensatz, schema_name, hinweise
	for angebot in angebote:
		roh, schema_name = frappe.db.get_value(
			"Quotation", angebot, ["cv_kalkulation_json", "cv_kalkulationsschema"]
		) or (None, None)
		if not roh:
			continue
		try:
			daten = json.loads(roh) if isinstance(roh, str) else roh
		except ValueError:
			hinweise.append(_("Kalkulation des Angebots {0} nicht lesbar.").format(angebot))
			continue
		schema = daten.get("schema") or {}
		stundensatz = stundensatz or flt(schema.get("stundensatz"))
		schema_name = schema_name or schema.get("name")
		for p in daten.get("positionen") or []:
			code = p.get("item_code") or p.get("id")
			if not code:
				continue
			k = p.get("kalkulation") or {}
			mengen = p.get("mengen") or {}
			je_artikel[code].append(
				{
					"minuten": flt(k.get("minuten")),
					"material": flt(k.get("material")),
					"menge": flt(p.get("menge")),
					"einzelpreis": flt(p.get("einzelpreis") or k.get("einzelpreis")),
					"regel": mengen.get("regel") or None,
					"regelart": mengen.get("kind") or None,
					"verfahren": (p.get("herkunft") or {}).get("verfahren"),
				}
			)
	if not stundensatz and schema_name and frappe.db.exists("DocType", SCHEMA_DOCTYPE):
		stundensatz = flt(frappe.db.get_value(SCHEMA_DOCTYPE, schema_name, "stundensatz"))
	if not stundensatz:
		hinweise.append(_("Kein Stundensatz aus der Kalkulation: Lohnkosten werden mit 0 € gerechnet."))
	return je_artikel, stundensatz, schema_name, hinweise


def zuordnen(item_codes: list[str], kalk: dict[str, list[dict]]) -> list[dict | None]:
	"""Kalkulationsposition je Auftragsposition (gleiche Reihenfolge wie ``item_codes``), sonst ``None``."""
	zaehler: dict[str, int] = defaultdict(int)
	ergebnis: list[dict | None] = []
	for code in item_codes:
		kandidaten = kalk.get(code) or []
		i = zaehler[code]
		ergebnis.append(kandidaten[i] if i < len(kandidaten) else None)
		zaehler[code] += 1
	return ergebnis
