"""Aufgaben 6.1/6.2: Abnahme im ERPNext-Adapter.

Hooks am Kern-Doctype ``Abnahmeprotokoll`` (ohne Kern greifen sie nie) und an ERPNext-Belegen:

- ``Abnahmeprotokoll.on_update`` → ``protokoll_aktualisiert``: beim Abschluss **je Mangel ein Task**
  am Projekt (``tasks.maengel_tasks_anlegen``: Titel „Mangel: …“, Fälligkeit = Frist, Verweis auf
  Protokoll und Mangelzeile, idempotent über ``Task.cv_abnahmeprotokoll``/``cv_mangel_zeile``) und
  die **Abnahme am Projekt** (``garantie.projekt_aktualisieren``: abgenommen am, Ergebnis,
  Gewährleistung bis). Beim Wiederöffnen wird das Projekt zurückgesetzt bzw. auf ein anderes
  abgeschlossenes Protokoll gestellt. Fehler halten den Abschluss **nicht** auf (Savepoint,
  Error Log, orange Meldung; Button „Mangel-Aufgaben erzeugen“ holt nach - Muster 5.1).
- ``Task.on_update`` → ``task_aktualisiert``: Mangel-Task erledigt → Mangelzeile „Behoben“
  (wieder offen → „Offen``), über ``corevatis.abnahme.mangel.status_setzen`` (``frappe.get_attr``).
- ``Project.validate`` → ``garantie.projekt_validieren``: Abnahmefelder nur vom Server,
  Gewährleistung bis = abgenommen am + Jahre.
- ``Sales Invoice.validate`` → ``rechnung.rechnung_pruefen``: Rechnung zu einem Projekt ohne
  Abnahme wird abgelehnt, außer Abschlagsrechnung (``cv_abschlag``) oder Übersteuerung durch einen
  System Manager mit Begründung (protokolliert).
"""

import frappe
from frappe import _
from frappe.utils import strip_html

from corevatis_erpnext.abnahme import konstanten as K
from corevatis_erpnext.abnahme.einstellungen import einstellungen
from corevatis_erpnext.abnahme.garantie import projekt_aktualisieren, projekt_validieren
from corevatis_erpnext.abnahme.rechnung import rechnung_pruefen
from corevatis_erpnext.abnahme.tasks import (
	behobene_tasks_erledigen,
	maengel_tasks_anlegen,
	maengel_tasks_erzeugen,
	task_aktualisiert,
)


def _schritt(doc, funktion, bezeichnung: str, *args) -> None:
	savepoint = "cv_abnahme"
	frappe.db.savepoint(savepoint)
	try:
		funktion(*args)
	except Exception as fehler:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(
			title=f"Abnahme 6.1: {bezeichnung} zu {doc.name}",
			reference_doctype=K.ABNAHMEPROTOKOLL,
			reference_name=doc.name,
		)
		frappe.msgprint(
			_("{0} zum Abnahmeprotokoll {1} nicht erledigt: {2} - bitte über den Button nachholen.").format(
				bezeichnung, doc.name, strip_html(str(fehler))
			),
			indicator="orange",
			alert=True,
		)


def protokoll_aktualisiert(doc, method=None) -> None:
	"""``Abnahmeprotokoll.on_update``: Mangel-Tasks und Abnahme am Projekt."""
	vorher = doc.get_doc_before_save()
	war_abgeschlossen = vorher is not None and vorher.get("status") == K.STATUS_ABGESCHLOSSEN
	abgeschlossen = doc.get("status") == K.STATUS_ABGESCHLOSSEN
	if not (abgeschlossen or war_abgeschlossen):
		return
	if not doc.get("projekt") or not frappe.db.exists("Project", doc.projekt):
		return
	if abgeschlossen:
		e = einstellungen()
		if e.mangel_tasks_automatisch and any(
			z.get("status") != K.MANGEL_BEHOBEN and not z.get("task") for z in doc.get("maengel") or []
		):
			_schritt(doc, maengel_tasks_anlegen, _("Mangel-Aufgaben"), doc)
		if war_abgeschlossen:
			# Mangelstatus im Desk auf „Behoben“ gesetzt: Task mit erledigen
			_schritt(doc, behobene_tasks_erledigen, _("Mangel-Aufgaben erledigen"), doc)
	_schritt(doc, projekt_aktualisieren, _("Abnahme am Projekt"), doc.projekt)


__all__ = [
	"maengel_tasks_erzeugen",
	"projekt_validieren",
	"protokoll_aktualisiert",
	"rechnung_pruefen",
	"task_aktualisiert",
]
