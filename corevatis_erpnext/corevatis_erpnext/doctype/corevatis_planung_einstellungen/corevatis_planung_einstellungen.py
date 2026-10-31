"""Single „Corevatis Planung Einstellungen“: Arbeitspakete und Materialbedarf aus dem Auftrag (5.1),
Terminplanung der Besichtigung (Dauer, Fahrzeit, Puffer, Geokodierung - Durchstich D14).

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
		# Terminplanung (D14)
		if not 15 <= cint(self.besichtigung_dauer) <= 720:
			frappe.throw(_("Die Dauer der Besichtigung muss zwischen 15 und 720 Minuten liegen."))
		if not 1 <= flt(self.fahrzeit_umwegfaktor) <= 3:
			frappe.throw(_("Der Umwegfaktor muss zwischen 1 und 3 liegen."))
		if not 5 <= flt(self.fahrzeit_kmh) <= 130:
			frappe.throw(_("Die Durchschnittsgeschwindigkeit muss zwischen 5 und 130 km/h liegen."))
		for feld, bezeichnung in (
			("fahrzeit_minimum", _("Mindestfahrzeit")),
			("fahrzeit_standard", _("Standardfahrzeit")),
			("fahrzeit_puffer", _("Puffer zwischen Terminen")),
		):
			if not 0 <= cint(self.get(feld)) <= 240:
				frappe.throw(_("{0}: bitte 0 bis 240 Minuten.").format(bezeichnung))
