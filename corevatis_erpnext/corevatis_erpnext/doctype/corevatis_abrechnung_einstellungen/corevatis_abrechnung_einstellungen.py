import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

DOCTYPE = "Corevatis Abrechnung Einstellungen"


def check_standards() -> dict[str, int]:
	"""Check-Felder mit Doctype-Default (Feld → Default 0/1), aus den Metadaten."""
	return {
		df.fieldname: cint(df.default)
		for df in frappe.get_meta(DOCTYPE).fields
		if df.fieldtype == "Check" and df.default not in (None, "")
	}


def nicht_gespeichert(felder) -> list[str]:
	"""Felder ohne Wert in ``tabSingles`` (Zeile fehlt, NULL oder leer)."""
	gespeichert = frappe.db.get_singles_dict(DOCTYPE) or {}
	return [feld for feld in felder if gespeichert.get(feld) in (None, "")]


class CorevatisAbrechnungEinstellungen(Document):
	# Wie Corevatis Einstellungen (Handwerker#99): Frappe setzt Doctype-Defaults nur bei insert;
	# Check-Felder, die nie in tabSingles standen, lädt v16 als 0, und save() schriebe diese 0 - der
	# neue Schalter „Lohn und Material je Position ausweisen“ (Standard an, #105) wäre nach dem ersten
	# Speichern der Einstellungen aus. Fehlt der Wert in der Datenbank, gilt beim Laden der Default;
	# ein gespeicherter Wert (auch 0) und ein nach dem Laden gesetzter Wert bleiben unangetastet.

	def load_from_db(self):
		ergebnis = super().load_from_db()
		self._standards_fuer_nicht_gespeicherte_checks(nur_leere=False)
		return ergebnis

	def _standards_fuer_nicht_gespeicherte_checks(self, *, nur_leere: bool):
		standards = check_standards()
		felder = [feld for feld in standards if not nur_leere or self.get(feld) is None]
		if not felder:
			return
		for feld in nicht_gespeichert(felder):
			self.set(feld, standards[feld])

	def validate(self):
		from corevatis_erpnext.abrechnung.einstellungen import VORLAGE_EIGENE

		self._standards_fuer_nicht_gespeicherte_checks(nur_leere=True)

		if not 0 <= flt(self.lohnanteil_prozent_standard) <= 100:
			frappe.throw(_("Lohnanteil Standard: bitte 0 bis 100 % angeben."))
		if flt(self.abschlag_mindestsumme) < 0:
			frappe.throw(_("Mindestsumme darf nicht negativ sein."))
		zeilen = self.get("vorlage_zeilen") or []
		if self.abschlagsplan_vorlage == VORLAGE_EIGENE and not zeilen:
			frappe.throw(_("Eigene Vorlage: bitte mindestens eine Zeile mit Prozent und Fälligkeit angeben."))
		summe = sum(flt(z.prozent) for z in zeilen)
		if any(flt(z.prozent) <= 0 for z in zeilen):
			frappe.throw(_("Eigene Vorlage: jede Zeile braucht einen Prozentsatz über 0."))
		if summe > 100:
			frappe.throw(
				_("Eigene Vorlage: die Abschläge übersteigen zusammen 100 % ({0} %).").format(flt(summe, 2))
			)
		if self.abschlag_artikel and not frappe.db.get_value("Item", self.abschlag_artikel, "is_sales_item"):
			frappe.throw(_("Der Abschlagsartikel muss ein Verkaufsartikel sein."))
