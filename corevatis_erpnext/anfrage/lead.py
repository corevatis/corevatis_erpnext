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
	"""``validate``-Hook am Lead (1.4): „Verloren“ nur mit Verlustgrund - auch per API/Kanban."""
	if doc.get("cv_status") == STATUS_VERLOREN and not doc.get("cv_verlustgrund"):
		frappe.throw(
			_("Please select a loss reason before marking the inquiry as lost."),
			frappe.MandatoryError,
			title=_("Loss Reason"),
		)
