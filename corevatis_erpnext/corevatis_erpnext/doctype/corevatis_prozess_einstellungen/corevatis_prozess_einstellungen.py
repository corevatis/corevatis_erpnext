"""Single „Corevatis Prozess Einstellungen“: Ampel-Schwellen der Vorgangsübersicht (Prozessleiste).

Werte liest ``corevatis_erpnext.prozess.einstellungen.einstellungen`` (mit Standardwerten, auch
wenn das Formular nie gespeichert wurde).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from corevatis_erpnext.prozess.einstellungen import SCHWELLEN


class CorevatisProzessEinstellungen(Document):
	def validate(self):
		for feld in SCHWELLEN:
			if cint(self.get(feld)) < 1:
				frappe.throw(_("Die Ampel-Schwellen müssen mindestens einen Tag betragen."))
		if cint(self.uebersicht_max_vorgaenge) < 10:
			frappe.throw(_("Die Vorgangsübersicht wertet mindestens 10 Vorgänge aus."))
