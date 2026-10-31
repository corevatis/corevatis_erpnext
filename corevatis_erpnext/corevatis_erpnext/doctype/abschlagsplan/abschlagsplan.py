"""Abschlagsplan (7.1): Zeilen mit Prozent oder Betrag, Fälligkeit, Status und Rechnung je Auftrag.

Das Büro pflegt offene Zeilen (Prozent/Betrag, Bezeichnung, Fälligkeit, Datum). Status, Rechnung,
Beträge und Stand setzt nur der Server (``abrechnung.plan``), Zeilen mit Rechnung sind eingefroren.
Je Auftrag gibt es genau einen Plan. Die Beträge rechnet die Engine des Kerns
(``corevatis.engine.invoice.abschlagsplan``); der Plan speichert ihr Ergebnis je Zeile.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

SERVER_FELDER_ZEILE = (
	"status",
	"rechnung",
	"netto",
	"steuer",
	"brutto",
	"kumuliert_prozent",
	"kumuliert_netto",
)
EINGEFROREN = ("prozent", "betrag", "bezeichnung", "faellig_bei")
SERVER_FELDER = (
	"status",
	"gestellt_prozent",
	"gestellt_netto",
	"gestellt_brutto",
	"offen_netto",
	"schlussrechnung",
	"auftragssumme_netto",
	"steuersatz",
	"titel",
	"vorlage",
)


class Abschlagsplan(Document):
	def validate(self):
		from corevatis_erpnext.abrechnung import plan

		self._nur_ein_plan_je_auftrag()
		self._zeilen_pruefen()
		if not self.flags.cv_server:
			self._server_felder_schuetzen()
		plan.durchrechnen(self)

	def _nur_ein_plan_je_auftrag(self):
		anderer = frappe.db.get_value(
			"Abschlagsplan", {"auftrag": self.auftrag, "name": ["!=", self.name]}, "name"
		)
		if anderer:
			frappe.throw(
				_("Zum Auftrag {0} gibt es schon den Abschlagsplan {1}.").format(self.auftrag, anderer),
				frappe.DuplicateEntryError,
			)
		if not self.projekt:
			self.projekt = frappe.db.get_value("Sales Order", self.auftrag, "project") or frappe.db.get_value(
				"Project", {"sales_order": self.auftrag, "status": ["!=", "Cancelled"]}, "name"
			)

	def _zeilen_pruefen(self):
		if not self.get("zeilen"):
			frappe.throw(_("Bitte mindestens eine Abschlagszeile angeben."), frappe.MandatoryError)
		for i, z in enumerate(self.zeilen, start=1):
			z.nr = i
			hat_prozent, hat_betrag = flt(z.prozent) > 0, flt(z.betrag) > 0
			if hat_prozent == hat_betrag:
				frappe.throw(
					_("Zeile {0}: bitte entweder Prozent oder Betrag angeben.").format(i),
					frappe.ValidationError,
				)
			if flt(z.prozent) > 100:
				frappe.throw(_("Zeile {0}: höchstens 100 %.").format(i), frappe.ValidationError)
			if z.faellig_bei == "Datum" and not z.datum:
				frappe.throw(
					_("Zeile {0}: Fälligkeit „Datum“ braucht ein Datum.").format(i), frappe.MandatoryError
				)
			if not z.status:
				z.status = "offen"

	def _server_felder_schuetzen(self):
		"""Status, Rechnung und Beträge setzt nur der Server; Zeilen mit Rechnung sind eingefroren."""
		vorher = None if self.is_new() else self.get_doc_before_save()
		alte = {z.name: z for z in (vorher.get("zeilen") if vorher else [])}
		for z in self.zeilen:
			alt = alte.get(z.name)
			if alt is None:
				for feld in SERVER_FELDER_ZEILE:
					z.set(feld, "offen" if feld == "status" else None)
				continue
			for feld in SERVER_FELDER_ZEILE:
				z.set(feld, alt.get(feld))
			if alt.get("rechnung"):
				for feld in EINGEFROREN:
					if (z.get(feld) or None) != (alt.get(feld) or None):
						frappe.throw(
							_(
								"Zeile {0} ist schon abgerechnet ({1}) und kann nicht mehr geändert werden."
							).format(z.nr, alt.get("rechnung")),
							frappe.ValidationError,
						)
		geloescht = set(alte) - {z.name for z in self.zeilen}
		for name in geloescht:
			if alte[name].get("rechnung"):
				frappe.throw(
					_("Zeile {0} ist schon abgerechnet ({1}) und kann nicht gelöscht werden.").format(
						alte[name].nr, alte[name].rechnung
					),
					frappe.ValidationError,
				)
		for feld in SERVER_FELDER:
			if vorher is not None:
				self.set(feld, vorher.get(feld))

	def on_trash(self):
		if any(z.rechnung for z in self.get("zeilen") or []):
			frappe.throw(_("Der Abschlagsplan hat schon Rechnungen und kann nicht gelöscht werden."))
