"""Abwesenheit eines Mitarbeiters (Plantafel, Phase 1 P1.4): Urlaub, Krank, Schulung, Sonstiges.

Ohne ``hrms`` (nicht installiert) ist das die einzige Quelle für „nicht verfügbar“: Terminplanung
(D14) und Wochenplan/Plantafel werten den Zeitraum als belegt (``planung.abwesenheit``).
Ganztags = ganzer Kalendertag von ``von`` bis ``bis``; mit Uhrzeiten nur ein Tag (``von`` = ``bis``).
``benutzer`` ist der Benutzer des Mitarbeiters - darüber lesen Meister und Monteur nur ihre eigenen
Abwesenheiten (``permission_query_conditions``/``has_permission`` in ``hooks.py``).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate, to_timedelta

ARTEN = ("Urlaub", "Krank", "Schulung", "Sonstiges")


class Abwesenheit(Document):
	def validate(self):
		if self.art not in ARTEN:
			frappe.throw(_("Art der Abwesenheit: {0}.").format(", ".join(ARTEN)))
		if not self.von or not self.bis:
			frappe.throw(_("Bitte Beginn und Ende der Abwesenheit angeben."), frappe.MandatoryError)
		if getdate(self.bis) < getdate(self.von):
			frappe.throw(_("Das Ende der Abwesenheit liegt vor dem Beginn."))
		if cint(self.ganztags):
			self.von_uhrzeit = None
			self.bis_uhrzeit = None
		else:
			if not self.von_uhrzeit or not self.bis_uhrzeit:
				frappe.throw(_("Ohne „ganztags“ bitte Uhrzeit von und bis angeben."), frappe.MandatoryError)
			if getdate(self.von) != getdate(self.bis):
				frappe.throw(_("Uhrzeiten gelten nur für einen einzelnen Tag (Beginn = Ende)."))
			if to_timedelta(self.bis_uhrzeit) <= to_timedelta(self.von_uhrzeit):
				frappe.throw(_("Die Uhrzeit „bis“ muss nach „von“ liegen."))
		werte = frappe.db.get_value("Employee", self.mitarbeiter, ["employee_name", "user_id"], as_dict=True)
		if not werte:
			frappe.throw(
				_("Mitarbeiter {0} nicht gefunden.").format(self.mitarbeiter), frappe.DoesNotExistError
			)
		self.mitarbeiter_name = werte.employee_name
		self.benutzer = werte.user_id or None
