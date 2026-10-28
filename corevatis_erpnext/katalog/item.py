"""``doc_events`` am Item (Aufgabe 2.2)."""

import frappe
from frappe import _

from corevatis_erpnext.katalog.item_felder import LEISTUNGSREGEL_DOCTYPE


def validate(doc, method=None) -> None:
	"""``cv_leistungsregel`` bereinigen und, wenn ``corevatis`` installiert ist, auf Existenz
	prüfen. Ohne Kern (Doctype ``Leistungsregel`` fehlt) wird der Wert unverändert gespeichert."""
	wert = (doc.get("cv_leistungsregel") or "").strip() or None
	doc.cv_leistungsregel = wert
	if not wert or not frappe.db.exists("DocType", LEISTUNGSREGEL_DOCTYPE):
		return
	# nur wer Leistungsregeln lesen darf, verknüpft sie (kein Existenz-Orakel über das Item)
	if doc.has_value_changed("cv_leistungsregel") and not frappe.has_permission(
		LEISTUNGSREGEL_DOCTYPE, "read"
	):
		frappe.throw(_("Not permitted to use quantity rules."), frappe.PermissionError)
	if not frappe.db.exists(LEISTUNGSREGEL_DOCTYPE, wert):
		frappe.throw(
			_("Quantity rule {0} does not exist.").format(frappe.bold(wert)), title=_("Unknown quantity rule")
		)
