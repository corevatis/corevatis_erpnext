import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class CorevatisAbrechnungEinstellungen(Document):
	def validate(self):
		from corevatis_erpnext.abrechnung.einstellungen import VORLAGE_EIGENE

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
