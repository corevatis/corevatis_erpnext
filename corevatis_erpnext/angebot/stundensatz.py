"""Stundensatz an einer Stelle (Handwerker#104): das Standard-Kalkulationsschema.

Den Lohn im Angebot rechnet der Stundensatz des Kalkulationsschemas (4.2, Kern); er wird mit der
Kalkulation im Angebot eingefroren (``cv_kalkulation_json``, ``schema.stundensatz``) und von der
Nachkalkulation (P2.1) und der Abrechnung von dort gelesen. Die Workstation „Montage“ (2.3) trägt
einen zweiten Satz, den ERPNext nur für die Herstellkosten der Stückliste (``BOM.operating_cost``,
``katalog.uebersicht``) braucht. Sie wird **aus dem Schema gepflegt** und nirgends als Quelle gelesen:

- Speichert jemand das Standardschema (``doc_events`` „Kalkulationsschema“ ``on_update``, Kern-Doctype
  nur per Name), setzt ``schema_gespeichert`` die Kostenkomponente „Lohn Montage“ jeder Workstation, die
  sie führt, auf den Stundensatz und lässt ERPNext die Kosten der betroffenen Stücklisten neu rechnen
  (wie „Kosten aktualisieren“, im Hintergrund).
- ``after_migrate`` gleicht ebenso ab (z. B. nach einem Katalogimport mit 48 €/h aus der Vorlage).
- ``pruefen`` meldet Abweichungen (``bench execute corevatis_erpnext.angebot.stundensatz.pruefen``).

Ohne Kern (kein Doctype „Kalkulationsschema“) oder ohne Standardschema geschieht nichts.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

SCHEMA_DOCTYPE = "Kalkulationsschema"
# Kostenkomponente der Workstation-Vorlage (Katalog 2.3, ``katalog.importer.KOSTENKOMPONENTEN``)
KOMPONENTE = "Lohn Montage"


def standard_schema() -> frappe._dict | None:
	"""Aktives Standardschema mit Name und Stundensatz, ``None`` ohne Kern oder ohne Standard."""
	if not frappe.db.exists("DocType", SCHEMA_DOCTYPE):
		return None
	return frappe.db.get_value(
		SCHEMA_DOCTYPE, {"ist_standard": 1, "aktiv": 1}, ["name", "stundensatz"], as_dict=True
	)


def workstations() -> list[str]:
	"""Workstations mit der Kostenkomponente „Lohn Montage“."""
	return frappe.get_all(
		"Workstation Cost",
		filters={"parenttype": "Workstation", "operating_component": KOMPONENTE},
		pluck="parent",
		distinct=True,
		order_by="parent asc",
	)


def _satz(workstation: str) -> float:
	return flt(
		frappe.db.get_value(
			"Workstation Cost",
			{"parent": workstation, "parenttype": "Workstation", "operating_component": KOMPONENTE},
			"operating_cost",
		)
	)


def abgleichen(stundensatz: float | None = None, schema: str | None = None) -> dict:
	"""Workstation-Sätze auf den Stundensatz des (Standard-)Schemas setzen. Rückgabe
	``{"schema", "stundensatz", "geaendert": [Workstation], "stuecklisten": n}``."""
	if stundensatz is None:
		s = standard_schema()
		if not s:
			return {"schema": None, "stundensatz": None, "geaendert": [], "stuecklisten": 0}
		stundensatz, schema = flt(s.stundensatz), s.name
	stundensatz = flt(stundensatz)
	geaendert: list[str] = []
	if stundensatz > 0:
		for name in workstations():
			if flt(_satz(name), 2) == flt(stundensatz, 2):
				continue
			ws = frappe.get_doc("Workstation", name)
			for zeile in ws.workstation_costs:
				if zeile.operating_component == KOMPONENTE:
					zeile.operating_cost = stundensatz
			ws.flags.ignore_permissions = True
			ws.save()
			geaendert.append(name)
	stuecklisten = _stuecklisten(geaendert)
	if stuecklisten:
		frappe.enqueue(
			"corevatis_erpnext.angebot.stundensatz.stuecklisten_neu_rechnen",
			queue="short",
			boms=stuecklisten,
			enqueue_after_commit=True,
			now=bool(frappe.flags.in_test or frappe.flags.in_migrate),
		)
	return {
		"schema": schema,
		"stundensatz": stundensatz,
		"geaendert": geaendert,
		"stuecklisten": len(stuecklisten),
	}


def _stuecklisten(workstation_namen: list[str]) -> list[str]:
	"""Gebuchte, aktive Stücklisten mit einem Arbeitsgang an diesen Workstations."""
	if not workstation_namen:
		return []
	return frappe.get_all(
		"BOM",
		filters=[
			["BOM", "docstatus", "=", 1],
			["BOM", "is_active", "=", 1],
			["BOM Operation", "workstation", "in", workstation_namen],
		],
		pluck="name",
		distinct=True,
		order_by="name asc",
	)


def stuecklisten_neu_rechnen(boms: list[str]) -> None:
	"""Kosten der Stücklisten mit dem neuen Workstation-Satz neu ermitteln (wie ``katalog.importer``)."""
	for name in boms:
		bom = frappe.get_doc("BOM", name)
		bom.update_cost(update_parent=False, from_child_bom=True, update_hour_rate=True, save=True)


def schema_gespeichert(doc, method=None) -> None:
	"""``doc_events`` „Kalkulationsschema“ ``on_update``: nur das aktive Standardschema zählt."""
	if not (doc.get("ist_standard") and doc.get("aktiv")):
		return
	ergebnis = abgleichen(flt(doc.get("stundensatz")), doc.name)
	if ergebnis["geaendert"]:
		frappe.msgprint(
			_(
				"Stundensatz {0} €/h auch in Workstation {1} übernommen (Herstellkosten der Stückliste)."
			).format(
				frappe.format_value(ergebnis["stundensatz"], {"fieldtype": "Float", "precision": 2}),
				", ".join(ergebnis["geaendert"]),
			),
			alert=True,
		)


def pruefen() -> list[str]:
	"""Abweichungen Workstation ↔ Standardschema als Text (leer, wenn alles passt)."""
	s = standard_schema()
	if not s:
		return []
	return [
		_("Workstation {0}: {1} €/h, Standardschema {2}: {3} €/h.").format(
			name, flt(_satz(name), 2), s.name, flt(s.stundensatz, 2)
		)
		for name in workstations()
		if flt(_satz(name), 2) != flt(s.stundensatz, 2)
	]


def ensure_abgleich() -> None:
	"""``after_migrate``: abgleichen, Fehler nur protokollieren (Migrate läuft weiter)."""
	try:
		abgleichen()
	except Exception:
		frappe.log_error(title="Stundensatz Workstation/Kalkulationsschema")
