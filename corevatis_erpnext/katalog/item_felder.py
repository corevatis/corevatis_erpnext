"""Aufgabe 2.2: Custom Fields am ERPNext-Doctype ``Item`` (Gewerk, Verfahren, Leistungsregel,
Maschinen- und Fahrtkosten).

- ``cv_gewerk``, ``cv_verfahren``: Select. Die Werte sind deutsche Daten und müssen mit
  ``Leistungsregel.gewerk``/``Leistungsregel.verfahren`` in ``corevatis`` übereinstimmen
  (Vertragstest in der CI von ``corevatis``: ``tests/test_erpnext_contract.py``).
- ``cv_leistungsregel``: **Data** mit dem Namen der Leistungsregel aus ``corevatis``, kein
  Link. Ein Link verlangt den Doctype ``Leistungsregel`` beim Anlegen des Feldes; das Item
  hätte dann je nach Installationsreihenfolge ein anderes Schema, und auf Sites ohne Kern
  (CI dieses Repos) fehlte das Feld. Die Existenz prüft ``katalog.item.validate`` am Item,
  sobald ``corevatis`` installiert ist (nur per Doctype-Name, kein Import).
- ``cv_maschinenkosten`` (Handwerker#105): Check. Die Kosten des Artikels sind Maschinen-, Geräte-
  oder Fahrtkosten (z. B. Minibagger-Miete). Sie zählen nach § 35a Abs. 3 EStG zu den Arbeitskosten
  (BMF-Schreiben vom 9.11.2016), nicht zum Material: in einer Stückliste als ``maschinen_je_einheit``
  an die Kalkulation, als eigene Position 100 % Lohn (``abrechnung.lohn_material``).

Labels sind englische Quelltexte, die deutsche Oberfläche kommt aus ``translations/de.csv``.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "Corevatis ERPNext"
LEISTUNGSREGEL_DOCTYPE = "Leistungsregel"
# Handwerker#105: Maschinen-, Geräte- und Fahrtkosten (§ 35a: Arbeitskosten)
MASCHINENKOSTEN = "cv_maschinenkosten"

GEWERKE = ("Bauwerksabdichtung", "Sanierputz", "Injektion", "Drainage", "Sonstiges")
VERFAHREN = (
	"Außenabdichtung",
	"Innenabdichtung",
	"Horizontalsperre Injektion",
	"Sanierputz",
	"Spezialabdichtung",
	"Sonstiges",
)


def _select(*values: str) -> str:
	return "\n".join(("", *values))


def item_fields() -> list[dict]:
	fields = [
		{
			"fieldname": "cv_section_leistung",
			"fieldtype": "Section Break",
			"label": "Corevatis Service",
			"insert_after": "over_billing_allowance",
		},
		{
			"fieldname": "cv_gewerk",
			"fieldtype": "Select",
			"label": "Craft Trade",
			"options": _select(*GEWERKE),
			"in_standard_filter": 1,
			"insert_after": "cv_section_leistung",
		},
		{
			"fieldname": "cv_verfahren",
			"fieldtype": "Select",
			"label": "Remediation Method",
			"options": _select(*VERFAHREN),
			"in_standard_filter": 1,
			"insert_after": "cv_gewerk",
		},
		{
			"fieldname": "cv_column_leistung",
			"fieldtype": "Column Break",
			"insert_after": "cv_verfahren",
		},
		{
			"fieldname": "cv_leistungsregel",
			"fieldtype": "Data",
			"label": "Quantity Rule",
			"description": "Name of the Leistungsregel (Corevatis) that calculates the quantity from the measurement",
			"insert_after": "cv_column_leistung",
		},
		{
			"fieldname": MASCHINENKOSTEN,
			"fieldtype": "Check",
			"label": "Machine or Travel Costs (§ 35a EStG)",
			"description": "Machine, equipment or travel costs (e.g. excavator hire): count as labour costs under § 35a EStG, not as material - in a BOM and as a position of its own",
			"insert_after": "cv_leistungsregel",
		},
	]
	for field in fields:
		field.setdefault("module", MODULE)
	return fields


def ensure_item_fields() -> None:
	create_custom_fields({"Item": item_fields()}, update=True)
