"""``doc_events`` am Lead (Aufgabe 1.1): Symptome zwischen Checkboxen und Text abgleichen.

``cv_symptome`` (Small Text, ein Symptom je Zeile) ist das Feld für API, Liste, Bericht
und Kanban-Karte; die Checkboxen ``cv_symptom_*`` sind die Eingabe in Desk und Web Form.
Beide bleiben deckungsgleich:

- neuer Lead: Vereinigung aus Checkboxen und bekannten Zeilen im Text,
- Checkbox geändert: Checkboxen gelten, der Text wird neu aufgebaut,
- nur der Text geändert (z. B. per REST): der Text gilt, die Checkboxen folgen.

Zeilen, die keinem Standardsymptom entsprechen, bleiben als Freitext am Ende erhalten.
Kein Rechnen, nur Normalisieren - bleibt deshalb im Adapter.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint

from corevatis_erpnext.anfrage.custom_fields import STATUS_VERLOREN, SYMPTOME

_BY_VALUE = {value.casefold(): fieldname for fieldname, _label, value in SYMPTOME}
_SPLIT = re.compile(r"[\n,;]+")


def split_symptome(text: str | None) -> tuple[list[str], list[str]]:
	"""Zerlegt Text in (bekannte Checkbox-Feldnamen, Freitext-Zeilen), ohne Dubletten."""
	known: list[str] = []
	extra: list[str] = []
	for raw in _SPLIT.split(text or ""):
		line = raw.strip()
		if not line:
			continue
		fieldname = _BY_VALUE.get(line.casefold())
		if fieldname:
			if fieldname not in known:
				known.append(fieldname)
		elif line not in extra:
			extra.append(line)
	return known, extra


def build_symptome(checked: set[str] | list[str], extra: list[str]) -> str:
	"""Text aus angehakten Feldern (Standardreihenfolge) und Freitext-Zeilen."""
	lines = [value for fieldname, _label, value in SYMPTOME if fieldname in checked]
	return "\n".join(lines + list(extra))


def sync_symptome(doc, method=None) -> None:
	"""``validate``-Hook am Lead."""
	before = None if doc.is_new() else doc.get_doc_before_save()
	known, extra = split_symptome(doc.get("cv_symptome"))
	checked = {fieldname for fieldname, _label, _value in SYMPTOME if cint(doc.get(fieldname))}

	if before is None:
		checked |= set(known)
	else:
		checks_changed = any(
			cint(doc.get(fieldname)) != cint(before.get(fieldname)) for fieldname, _l, _v in SYMPTOME
		)
		text_changed = (doc.get("cv_symptome") or "") != (before.get("cv_symptome") or "")
		if text_changed and not checks_changed:
			checked = set(known)

	for fieldname, _label, _value in SYMPTOME:
		doc.set(fieldname, 1 if fieldname in checked else 0)
	doc.set("cv_symptome", build_symptome(checked, extra) or None)


def validate_verlustgrund(doc, method=None) -> None:
	"""``validate``-Hook am Lead (1.4): „Verloren“ nur mit Verlustgrund - auch per API/Kanban.

	Beim Verlassen von „Verloren“ (z. B. „Wieder öffnen“) wird der alte Grund geleert: Er
	gehört zum abgeschlossenen Verlust, und der Desk-Dialog fragt bei einem erneuten
	„Als verloren markieren“ nur, wenn kein Grund gesetzt ist.
	"""
	vorgabe = frappe.flags.get("cv_verlustgrund")
	if vorgabe and vorgabe.get("lead") == doc.name and doc.get("cv_status") == STATUS_VERLOREN:
		# aus als_verloren_markieren: Grund im selben save wie der Workflow-Übergang
		doc.cv_verlustgrund = vorgabe["grund"]
	before = doc.get_doc_before_save() if not doc.is_new() else None
	if (
		before
		and before.get("cv_status") == STATUS_VERLOREN
		and doc.get("cv_status") != STATUS_VERLOREN
		and doc.get("cv_verlustgrund")
	):
		doc.cv_verlustgrund = None
	if doc.get("cv_status") == STATUS_VERLOREN and not doc.get("cv_verlustgrund"):
		frappe.throw(
			_("Please select a loss reason before marking the inquiry as lost."),
			frappe.MandatoryError,
			title=_("Loss Reason"),
		)


@frappe.whitelist(methods=["POST"])
def als_verloren_markieren(lead: str, verlustgrund: str) -> dict:
	"""Desk-Dialog „Als verloren markieren“ (1.4): Verlustgrund und Workflow-Übergang in **einem**
	Speichervorgang (Review Epic 1, E-13).

	Vorher setzte der Dialog den Grund per ``frappe.db.set_value`` und rief danach die
	Workflow-Aktion auf - schlug die fehl, blieb der Grund am offenen Lead stehen, und der
	nächste Versuch fragte nicht mehr. ``apply_workflow`` lädt den Lead neu aus der Datenbank;
	der Grund wird deshalb über ``frappe.flags`` an ``validate_verlustgrund`` übergeben und dort
	im selben ``save`` gesetzt (eine Version, ein ``modified``). Schlägt der Übergang fehl
	(Rolle, Zustand), wird nichts gespeichert.
	"""
	from frappe.model.workflow import apply_workflow

	from corevatis_erpnext.anfrage.workflow import ACTION_VERLOREN

	doc = frappe.get_doc("Lead", lead)
	doc.check_permission("write")
	optionen = [
		o for o in (frappe.get_meta("Lead").get_options("cv_verlustgrund") or "").split("\n") if o.strip()
	]
	if not verlustgrund or verlustgrund not in optionen:
		frappe.throw(
			_("Please select a loss reason before marking the inquiry as lost."), frappe.MandatoryError
		)
	frappe.flags.cv_verlustgrund = {"lead": doc.name, "grund": verlustgrund}
	try:
		return apply_workflow(doc, ACTION_VERLOREN).as_dict()
	finally:
		frappe.flags.cv_verlustgrund = None
