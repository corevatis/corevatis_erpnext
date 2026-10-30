"""ERPNext-Arbeitsaufträge (Work Order, Fertigung) zu Corevatis-Aufträgen abfangen (Rückmeldung 31.10.).

Der Betrieb suchte am Auftrag „Aufträge für die Monteure“ und fand ERPNext „Erstellen →
Arbeitsauftrag“. Ein Arbeitsauftrag ist ein Fertigungsauftrag: Er übernimmt die Menge der Position
als Herstellmenge (23,32 m²), seine Lagereinheit ist beim Anlegen aus dem Auftrag die globale
Vorgabe „Nos“ (Lagereinstellungen, ``Document._set_defaults``; ``fetch_if_empty`` greift dann nicht)
und „Nos“ verlangt ganze Zahlen - daher „Die herzustellende Menge (23.32) kann nicht ein Bruchteil der
Maßeinheit Nos sein“. Danach verlangt er Lager (Ware in Arbeit, Fertigwaren).

Die Arbeit der Monteure sind die Arbeitspakete (Task) am Projekt (5.1), zugewiesen im Wochenplan
(5.2). Arbeitsaufträge zu einem Auftrag mit Projekt oder aus dem Aufmaß werden deshalb mit einer
Erklärung abgelehnt (``Work Order.before_validate``, vor ERPNexts Mengenprüfung). Die
Desk-Menüs dazu blendet ``planung.js`` aus.
"""

from __future__ import annotations

import frappe
from frappe import _

from corevatis_erpnext.planung.auftrag import PlanungFehler, ist_corevatis_auftrag


def _auftrag_und_projekt(doc) -> tuple[str | None, str | None]:
	auftrag = doc.get("sales_order")
	projekt = doc.get("project")
	if not auftrag and projekt:
		auftrag = frappe.db.get_value("Project", projekt, "sales_order")
	if auftrag and not projekt:
		projekt = frappe.db.get_value("Sales Order", auftrag, "project") or frappe.db.get_value(
			"Project", {"sales_order": auftrag, "status": ["!=", "Cancelled"]}, "name"
		)
	return auftrag, projekt


def arbeitsauftrag_pruefen(doc, method=None) -> None:
	"""``Work Order.before_validate``: kein Fertigungsauftrag zu einem Corevatis-Auftrag."""
	if doc.docstatus == 2:
		return
	auftrag, projekt = _auftrag_und_projekt(doc)
	if not auftrag:
		return
	felder = [f for f in ("cv_erzeugt_aus_aufmass", "cv_aufmass") if frappe.db.has_column("Sales Order", f)]
	so = frappe.db.get_value("Sales Order", auftrag, ["name", *felder], as_dict=True)
	if not projekt and not (so and ist_corevatis_auftrag(so)):
		return
	ziel = projekt or auftrag
	frappe.throw(
		_(
			"Ein Arbeitsauftrag (ERPNext-Fertigung) passt nicht zum Handwerksbetrieb: Er verlangt ganze"
			" Stückzahlen und Lager. Die Arbeit für die Monteure steht in den Arbeitspaketen am Projekt {0}:"
			" dort „Arbeitspakete anlegen“ bzw. „Wochenplan öffnen“ und Monteure zuweisen."
		).format(frappe.bold(ziel)),
		PlanungFehler,
		title=_("Arbeitspakete statt Arbeitsauftrag"),
	)
