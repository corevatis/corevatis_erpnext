import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class CorevatisAbnahmeEinstellungen(Document):
	def validate(self):
		for feld in ("gewaehrleistung_bgb_jahre", "gewaehrleistung_vob_jahre"):
			if not 0 < cint(self.get(feld)) <= 30:
				frappe.throw(_("Gewährleistung: bitte 1 bis 30 Jahre angeben."))
