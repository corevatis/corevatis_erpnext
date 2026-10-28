"""Nummernkreis für Rückgaben/Gutschriften (``doc_events`` ``before_insert``).

ERPNext führt Gutschriften (Sales Invoice mit ``is_return``) im selben Nummernkreis
wie Rechnungen, solange niemand die Serie von Hand wählt. Das Site-Template legt je
Doctype eine Rückgabe-Serie fest (Site-Default ``corevatis_return_naming_series``,
z. B. ``{"Sales Invoice": "GS-.YYYY.-"}``). Dieser Hook setzt sie vor der
Namensvergabe - nur, wenn der Beleg die Standardserie trägt; eine bewusst gewählte
andere Serie bleibt.
"""

from __future__ import annotations

import json

import frappe

RETURN_SERIES_DEFAULT_KEY = "corevatis_return_naming_series"


def get_return_series(doctype: str) -> str | None:
	raw = frappe.db.get_default(RETURN_SERIES_DEFAULT_KEY)
	if not raw:
		return None
	try:
		return json.loads(raw).get(doctype)
	except (TypeError, ValueError):
		return None


def set_return_naming_series(doc, method=None) -> None:
	if not doc.get("is_return"):
		return
	series = get_return_series(doc.doctype)
	if not series:
		return
	meta = frappe.get_meta(doc.doctype)
	if series not in [o.strip() for o in meta.get_naming_series_options()]:
		return
	default = meta.get_field("naming_series").default
	if not doc.get("naming_series") or doc.naming_series == default:
		doc.naming_series = series
