"""Review Epic 4, A-2: ``has_alternative_item`` an eingereichten Angeboten zurücknehmen, wenn die
„Alternativen“ der Zeile nur Corevatis-Eventual-/Alternativpositionen sind.

Neue Angebote erledigt der Hook ``textbausteine.alternativ_sets_aufloesen`` beim Einreichen.
Echte ERPNext-Alternativen (``is_alternative`` ohne ``cv_*``-Kennzeichen) bleiben.
"""

import frappe

from corevatis_erpnext.angebot.textbausteine import saetze_ohne_ersatz


def execute():
	if not frappe.db.has_column("Quotation Item", "cv_eventualposition"):
		return
	eltern = frappe.get_all(
		"Quotation Item",
		filters={"has_alternative_item": 1, "parenttype": "Quotation"},
		pluck="parent",
	)
	for name in sorted(set(eltern)):
		for row in saetze_ohne_ersatz(frappe.get_doc("Quotation", name)):
			row.db_set("has_alternative_item", 0, update_modified=False)
