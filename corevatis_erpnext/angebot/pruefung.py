"""``doc_events`` Quotation."""

import frappe
from frappe.utils import flt


def positionen_ohne_preis(doc, method=None) -> None:
	"""Hinweis (kein Abbruch) beim Speichern, wenn Positionen ohne Preis sind.

	Befund mvp-test: Ein Angebot mit der ERPNext-Preisliste „Standard Selling“ statt der
	Katalog-Preisliste speicherte alle Positionen mit 0,00 € ohne jede Meldung.
	"""
	leer = [
		f"{row.idx} ({row.item_code})"
		for row in doc.get("items") or []
		if row.item_code and not flt(row.rate) and not row.get("is_free_item")
	]
	if not leer:
		return
	frappe.msgprint(
		frappe._("Positionen ohne Preis: {0}. Preisliste „{1}“ prüfen oder Preis eintragen.").format(
			", ".join(leer), doc.get("selling_price_list") or "-"
		),
		title=frappe._("Preis fehlt"),
		indicator="orange",
	)
