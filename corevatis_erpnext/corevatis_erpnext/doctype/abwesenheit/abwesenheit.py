"""Abwesenheit eines Mitarbeiters (Plantafel, Phase 1 P1.4): Urlaub, Krank, Schulung, Sonstiges.

Ohne ``hrms`` (nicht installiert) ist das die einzige Quelle für „nicht verfügbar“: Terminplanung
(D14) und Wochenplan/Plantafel werten den Zeitraum als belegt (``planung.abwesenheit``).
Ganztags = ganzer Kalendertag von ``von`` bis ``bis``; mit Uhrzeiten nur ein Tag (``von`` = ``bis``).
``benutzer`` ist der Benutzer des Mitarbeiters - darüber liest der Monteur nur seine eigenen Abwesenheiten
(``permission_query_conditions``/``has_permission`` in ``hooks.py``). Der Meister sieht alle Einträge seiner
Firma, ``art`` und ``notiz`` aber nur beim Büro (Feldrecht Stufe 1): Krankheit ist ein Gesundheitsdatum. Deshalb
auch ohne Änderungsverfolgung (``track_changes``): Versionen würden die Art jedem Leser zeigen.
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
		self.urlaub_pruefen()

	def urlaub_pruefen(self) -> None:
		"""Urlaub über den Resturlaub des Jahres: Hinweis, keine Sperre (P2.5; das Büro entscheidet)."""
		if self.art != "Urlaub" or frappe.flags.in_install or frappe.flags.in_migrate:
			return
		from corevatis_erpnext.mitarbeiter import stundenkonto as sk

		jahr = getdate(self.von).year
		konto = sk.urlaubskonto(self.mitarbeiter, jahr, ausser=self.name if not self.is_new() else None)
		tage = sk.urlaubstage(
			self.mitarbeiter, self.von, self.bis, cint(self.ganztags), self.von_uhrzeit, self.bis_uhrzeit
		)
		rest = konto["rest"] - tage
		if rest < 0:
			frappe.msgprint(
				_(
					"Resturlaub {0} überschritten: {1} Tage verfügbar, dieser Eintrag {2} Tage (danach {3})."
				).format(jahr, f"{konto['rest']:g}", f"{tage:g}", f"{rest:g}"),
				title=_("Urlaubskonto"),
				indicator="orange",
			)
