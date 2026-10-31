"""Fahrzeug des Betriebs als Planungsressource (Plantafel, Phase 1 P1.4).

Ein Fahrzeug wird je Einsatz zugeordnet (``Task.cv_fahrzeug``, ``Event.cv_fahrzeug``); zwei Einsätze
zur gleichen Zeit mit demselben Fahrzeug sind ein Konflikt (``planung.plantafel``). Das optionale
Lager verbindet es mit dem Fahrzeuglager der Materialentnahme (3.11).
"""

import frappe
from frappe import _
from frappe.model.document import Document


class Fahrzeug(Document):
	def before_insert(self):
		# vor der Namensvergabe (autoname field:kennzeichen)
		self._normalisieren()

	def validate(self):
		self._normalisieren()
		if self.lager and not frappe.db.exists("Warehouse", self.lager):
			frappe.throw(_("Lager {0} nicht gefunden.").format(self.lager), frappe.DoesNotExistError)

	def _normalisieren(self) -> None:
		self.kennzeichen = " ".join((self.kennzeichen or "").split()).upper()
		if not self.kennzeichen:
			frappe.throw(_("Bitte ein Kennzeichen angeben."), frappe.MandatoryError)
