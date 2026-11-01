"""Handwerker#105: Lohn und Material getrennt ausweisen - Bestand nachrechnen.

1. Felder anlegen (``post_model_sync`` läuft vor ``after_migrate``): Angebot, Spiegel am Auftrag,
   Rechnung.
2. Einstellung „Lohnanteil mit Zuschlägen“ einmalig an (Entscheidung Mike 02.11.: Material + Lohn =
   Netto, Zuschläge nach Kostenanteilen). Später bewusst ausgeschaltet bleibt aus (Patch läuft einmal).
3. Angebote, Aufträge und Rechnungen (Entwurf und freigegeben, nicht storniert) in dieser Reihenfolge
   nachrechnen (Auftrag spiegelt das Angebot, Rechnung den Auftrag); ``modified`` bleibt. Abschlags-
   und Schlussrechnungen behalten ihren § 35a-Lohnanteil. Ohne Kern (``corevatis``) passiert nichts.
"""

import frappe

from corevatis_erpnext.abrechnung import lohn_material
from corevatis_erpnext.abrechnung.einstellungen import EINSTELLUNGEN


def execute():
	from corevatis_erpnext.abrechnung.felder import ensure_abrechnung_felder
	from corevatis_erpnext.angebot.annahme_felder import ensure_spiegel
	from corevatis_erpnext.angebot.felder import ensure_angebot_felder

	ensure_angebot_felder()
	ensure_spiegel()
	ensure_abrechnung_felder()
	for doctype in ("Quotation", "Sales Order", "Sales Invoice", "Quotation Item", "Sales Order Item"):
		frappe.clear_cache(doctype=doctype)
	if frappe.db.exists("DocType", EINSTELLUNGEN):
		frappe.db.set_single_value(EINSTELLUNGEN, "lohnanteil_mit_zuschlaegen", 1)
	if lohn_material._engine() is None:
		print("Lohn/Material: Kern corevatis nicht installiert - nichts nachgerechnet")
		return
	for doctype in lohn_material.DOCTYPES:
		namen = frappe.get_all(doctype, filters={"docstatus": ["<", 2]}, pluck="name", order_by="creation")
		fertig = 0
		for name in namen:
			try:
				fertig += lohn_material.nachtragen(doctype, name)
			except Exception:
				frappe.log_error(title=f"Lohn/Material nachrechnen {doctype} {name}")
		print(f"Lohn/Material: {fertig} von {len(namen)} {doctype} nachgerechnet")
