"""Aufgabe 5.1: Planung aus dem Auftrag - Arbeitspakete (Task) und Materialbedarf (Material Request).

- ``tasks_erzeugen(sales_order|project)`` (whitelisted, Büro/Meister): je Verfahren ein Task am
  Projekt mit Sollstunden aus der Stückliste, Bauablauf-Reihenfolge und Terminen
  (``planung.arbeitspakete``).
- ``materialbedarf_erzeugen(sales_order|project)`` (whitelisted, Büro/Meister): Material Request
  (Entwurf) mit dem aggregierten Material der Stücklisten (``planung.materialbedarf``).
- Hook ``Sales Order.on_submit`` → ``auftrag_eingereicht``: beides automatisch für Aufträge mit
  Projekt (bzw. aus dem Aufmaß), abschaltbar in „Corevatis Planung Einstellungen“.

Beide Wege sind idempotent (``cv_auftrag`` an Task und Material Request).
"""

import frappe
from frappe import _

from corevatis_erpnext.planung.arbeitspakete import arbeitspakete_anlegen, tasks_erzeugen
from corevatis_erpnext.planung.auftrag import ist_corevatis_auftrag, projekt_zum_auftrag
from corevatis_erpnext.planung.einstellungen import einstellungen as planung_einstellungen
from corevatis_erpnext.planung.materialbedarf import materialbedarf_anlegen, materialbedarf_erzeugen


def auftrag_eingereicht(doc, method=None) -> None:
	"""Sales Order ``on_submit`` (nach ``angebot.annahme.auftrag_eingereicht``, das das Projekt anlegt).

	Ein Fehler hier hält das Einreichen nicht auf: Rücksprung zum Savepoint, Fehlerprotokoll und
	Hinweis - Arbeitspakete/Materialbedarf lassen sich danach über die Buttons nachholen.
	"""
	projekt = projekt_zum_auftrag(doc)
	if not projekt:
		if ist_corevatis_auftrag(doc):
			frappe.msgprint(
				_("Auftrag {0} hat kein Projekt - keine Arbeitspakete und kein Materialbedarf.").format(
					doc.name
				),
				indicator="orange",
				alert=True,
			)
		return
	e = planung_einstellungen()
	schritte = (
		(e.arbeitspakete_automatisch, arbeitspakete_anlegen, _("Arbeitspakete")),
		(e.materialbedarf_automatisch, materialbedarf_anlegen, _("Materialbedarf")),
	)
	for aktiv, funktion, bezeichnung in schritte:
		if not aktiv:
			continue
		frappe.db.savepoint("cv_planung")
		try:
			funktion(doc, projekt, e)
		except Exception as fehler:
			frappe.db.rollback(save_point="cv_planung")
			frappe.log_error(
				title=f"Planung 5.1: {bezeichnung} zu {doc.name}",
				reference_doctype="Sales Order",
				reference_name=doc.name,
			)
			frappe.msgprint(
				_(
					"{0} zum Auftrag {1} nicht erzeugt: {2} - bitte über den Button am Auftrag nachholen."
				).format(bezeichnung, doc.name, frappe.utils.strip_html(str(fehler))),
				indicator="orange",
				alert=True,
			)


__all__ = ["auftrag_eingereicht", "materialbedarf_erzeugen", "tasks_erzeugen"]
