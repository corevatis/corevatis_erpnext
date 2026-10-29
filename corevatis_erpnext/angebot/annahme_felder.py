"""Custom Fields für Angebotsannahme (4.4) und Nachfassen (4.5), idempotent aus
``setup.install.after_install``/``after_migrate`` - **nach** allen anderen Feldern.

- **Sales Order / Sales Order Item:** Spiegel aller ``cv_*``-Custom-Fields von Quotation /
  Quotation Item (gleicher Name, gleicher Typ, gleiche Optionen). Die Felder am Angebot legt
  die Angebotsengine an (4.1, ``angebot.felder``: z. B. ``cv_aufmass``, ``cv_objekt``,
  ``cv_titel``, ``cv_verfahren``, ``cv_eventualposition``); der Spiegel folgt ihnen bei jedem ``migrate``, ohne dass hier eine
  Liste gepflegt wird. ``get_mapped_doc`` übernimmt gleichnamige Felder, ``annahme`` kopiert
  sie zusätzlich ausdrücklich (auch Felder mit ``no_copy``).
- **Project:** ``cv_objekt``, ``cv_aufmass`` (Data - der Adapter läuft auch ohne Kern-App,
  ein Link auf ``Objekt``/``Aufmass`` verlangte deren Doctype), ``cv_angebot`` (Link
  Quotation), ``cv_lead`` (Link Lead).
- **Lead:** ``cv_projekt`` (Link Project, schreibgeschützt) - gesetzt von der Annahme.
- **Quotation:** ``cv_lead`` (Link Lead, schreibgeschützt) - Anfrage des Angebots, gemerkt,
  wenn die Annahme das Angebot auf den Kunden umstellt (auch auf einen übernommenen
  Bestandskunden mit anderem ``lead_name``, Review Epic 4 A-3); wird gespiegelt.
- **ToDo:** ``cv_nachfass_zyklus`` (Int, schreibgeschützt): 0 = keine Nachfass-Aufgabe,
  1, 2, ... = Nachfass-Zyklus (Idempotenz des Scheduler-Jobs, 4.5).

Labels sind englische Quelltexte, die deutsche Oberfläche kommt aus ``translations/de.csv``.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "Corevatis ERPNext"
PRAEFIX = "cv_"

# Quelle -> Spiegel
SPIEGEL = (("Quotation", "Sales Order"), ("Quotation Item", "Sales Order Item"))

# Eigenschaften, die der Spiegel übernimmt (kein reqd: der Auftrag entsteht per Mapping;
# kein no_copy: der Wert soll vom Angebot in den Auftrag)
SPIEGEL_EIGENSCHAFTEN = (
	"fieldtype",
	"label",
	"options",
	"insert_after",
	"read_only",
	"hidden",
	"description",
	"depends_on",
	"collapsible",
	"collapsible_depends_on",
	"in_list_view",
	"in_standard_filter",
	"bold",
	"precision",
	"length",
	"columns",
	"allow_on_submit",
	"print_hide",
	"print_hide_if_no_value",
	"translatable",
	"default",
	"non_negative",
	# Feldrechte (Kalkulationsdaten, Review Epic 4 G-1/A-7) gelten auch am Auftrag
	"permlevel",
)


def projekt_felder() -> list[dict]:
	return [
		{
			"fieldname": "cv_section_corevatis",
			"fieldtype": "Section Break",
			"label": "Quotation and Property",
			"insert_after": "sales_order",
			"collapsible": 0,
		},
		{
			"fieldname": "cv_angebot",
			"fieldtype": "Link",
			"options": "Quotation",
			"label": "Quotation",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "cv_section_corevatis",
		},
		{
			"fieldname": "cv_lead",
			"fieldtype": "Link",
			"options": "Lead",
			"label": "Inquiry",
			"read_only": 1,
			"no_copy": 1,
			"search_index": 1,
			"insert_after": "cv_angebot",
		},
		{
			"fieldname": "cv_column_corevatis",
			"fieldtype": "Column Break",
			"insert_after": "cv_lead",
		},
		{
			"fieldname": "cv_objekt",
			"fieldtype": "Data",
			"label": "Property Record",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "cv_column_corevatis",
		},
		{
			"fieldname": "cv_aufmass",
			"fieldtype": "Data",
			"label": "Measurement",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "cv_objekt",
		},
	]


def lead_felder() -> list[dict]:
	return [
		{
			"fieldname": "cv_projekt",
			"fieldtype": "Link",
			"options": "Project",
			"label": "Project",
			"read_only": 1,
			"no_copy": 1,
			# im Kopf nach dem Besichtigungstermin (1.3)
			"insert_after": "cv_termin",
			"depends_on": "eval:doc.cv_projekt",
		}
	]


def angebot_felder() -> list[dict]:
	return [
		{
			"fieldname": "cv_lead",
			"fieldtype": "Link",
			"options": "Lead",
			"label": "Inquiry",
			"read_only": 1,
			"no_copy": 1,
			"search_index": 1,
			"insert_after": "cv_aufmass",
			"depends_on": "eval:doc.cv_lead",
		}
	]


def todo_felder() -> list[dict]:
	return [
		{
			"fieldname": "cv_nachfass_zyklus",
			"fieldtype": "Int",
			"label": "Follow-up Cycle",
			"read_only": 1,
			"no_copy": 1,
			"default": "0",
			"insert_after": "reference_name",
			"depends_on": "eval:doc.cv_nachfass_zyklus",
			"description": "Follow-up task for a quotation (Corevatis), cycle number",
		}
	]


def _mit_modul(felder: list[dict]) -> list[dict]:
	for feld in felder:
		feld.setdefault("module", MODULE)
	return felder


def spiegel_felder(quelle: str) -> list[dict]:
	"""``cv_*``-Custom-Fields eines Doctypes als Definition für den Spiegel (Reihenfolge = idx)."""
	felder = frappe.get_all(
		"Custom Field",
		filters={"dt": quelle, "fieldname": ["like", f"{PRAEFIX}%"]},
		fields=["fieldname", "idx", "is_virtual", *SPIEGEL_EIGENSCHAFTEN],
		order_by="idx asc, creation asc",
	)
	ergebnis = []
	for feld in felder:
		# virtuelle Felder haben keinen Wert, Tabellen kopiert das Mapping nicht
		if feld.get("is_virtual") or feld.fieldtype in ("Table", "Table MultiSelect"):
			continue
		definition = {"fieldname": feld.fieldname, "module": MODULE}
		for eigenschaft in SPIEGEL_EIGENSCHAFTEN:
			wert = feld.get(eigenschaft)
			if wert not in (None, ""):
				definition[eigenschaft] = wert
		ergebnis.append(definition)
	return ergebnis


def ensure_spiegel() -> dict[str, list[str]]:
	"""Spiegel Quotation -> Sales Order anlegen/aktualisieren. Rückgabe: Feldnamen je Ziel."""
	bericht: dict[str, list[str]] = {}
	for quelle, ziel in SPIEGEL:
		felder = spiegel_felder(quelle)
		vorhanden = {df.fieldname for df in frappe.get_meta(ziel).fields if not df.get("is_custom_field")}
		# Standardfeld gleichen Namens am Ziel (gibt es heute nicht) nie überschreiben
		felder = [f for f in felder if f["fieldname"] not in vorhanden]
		if felder:
			create_custom_fields({ziel: felder}, update=True)
		bericht[ziel] = [f["fieldname"] for f in felder]
	return bericht


def ensure_felder() -> None:
	create_custom_fields(
		{
			"Project": _mit_modul(projekt_felder()),
			"Lead": _mit_modul(lead_felder()),
			"Quotation": _mit_modul(angebot_felder()),
			"ToDo": _mit_modul(todo_felder()),
		},
		update=True,
	)
	ensure_spiegel()
