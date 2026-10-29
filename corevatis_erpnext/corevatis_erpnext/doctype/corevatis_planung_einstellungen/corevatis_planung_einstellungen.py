"""Single „Corevatis Planung Einstellungen“: Arbeitspakete und Materialbedarf aus dem Auftrag (5.1).

Werte liest ``corevatis_erpnext.planung.einstellungen.einstellungen`` (mit Standardwerten, auch
wenn das Formular nie gespeichert wurde).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class CorevatisPlanungEinstellungen(Document):
	def validate(self):
		if cint(self.teamgroesse) < 1:
			frappe.throw(_("Die Teamgröße muss mindestens 1 sein."))
		if not 0 < flt(self.tagesarbeitszeit) <= 24:
			frappe.throw(_("Die Tagesarbeitszeit muss zwischen 0 und 24 Stunden liegen."))
		if not 0 <= flt(self.verschnitt_prozent) <= 100:
			frappe.throw(_("Der Verschnitt muss zwischen 0 und 100 % liegen."))
		if (
			self.material_request_typ == "Material Transfer"
			and self.lager_haupt
			and self.lager_haupt == self.lager_fahrzeug
		):
			frappe.throw(_("Für eine Umlagerung müssen Haupt- und Fahrzeuglager verschieden sein."))
