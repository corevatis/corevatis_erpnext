"""Single „Corevatis Vertrieb Einstellungen“: Nachfass-Automatik für Angebote (Aufgabe 4.5).

Werte liest ``corevatis_erpnext.angebot.nachfassen.einstellungen`` (mit Standardwerten, auch
wenn das Formular nie gespeichert wurde).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class CorevatisVertriebEinstellungen(Document):
	def validate(self):
		if cint(self.nachfass_tage) < 1:
			frappe.throw(_("Nachfassen frühestens nach einem Tag."))
		if cint(self.nachfass_zyklen) < 1:
			frappe.throw(_("Mindestens ein Nachfassen je Angebot."))
		if self.nachfass_zuweisung == "Benutzer":
			if not self.nachfass_benutzer:
				frappe.throw(_("Bitte den Benutzer für das Nachfassen auswählen."))
			if not frappe.db.get_value("User", self.nachfass_benutzer, "enabled"):
				frappe.throw(_("Der Benutzer {0} ist deaktiviert.").format(self.nachfass_benutzer))
