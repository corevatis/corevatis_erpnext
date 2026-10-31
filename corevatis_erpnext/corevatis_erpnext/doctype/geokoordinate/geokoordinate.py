"""Geokodierte Adresse (Zwischenspeicher der Terminplanung, Durchstich D14).

Name = Hash der normalisierten Adresse (``planung.geokodierung.adresse_hash``): eine Adresse wird
genau einmal bei OpenStreetMap angefragt. Legt das Büro einen Eintrag von Hand an oder korrigiert
die Koordinaten, gilt die Quelle „Manuell“ und das Ergebnis wird nicht mehr überschrieben.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


class Geokoordinate(Document):
	def before_insert(self):
		from corevatis_erpnext.planung.geokodierung import adresse_hash, normalisieren

		self.adresse = normalisieren(self.adresse)
		if not self.adresse:
			frappe.throw(_("Bitte eine Adresse angeben."), frappe.MandatoryError)
		self.adresse_hash = adresse_hash(self.adresse)

	def validate(self):
		from corevatis_erpnext.planung.geokodierung import QUELLE_KEIN_TREFFER, QUELLE_MANUELL

		# leere Float-Felder speichert Frappe als 0 - (0, 0) gilt deshalb als „keine Koordinaten“;
		# die Spalten sind NOT NULL, deshalb nie None stehen lassen
		self.lat, self.lon = flt(self.lat), flt(self.lon)
		hat_koordinaten = bool(self.lat or self.lon)
		self.gefunden = 1 if hat_koordinaten else 0
		if hat_koordinaten:
			if not (-90 <= flt(self.lat) <= 90 and -180 <= flt(self.lon) <= 180):
				frappe.throw(
					_("Breitengrad muss zwischen -90 und 90, Längengrad zwischen -180 und 180 liegen.")
				)
			if not self.quelle or self.quelle == QUELLE_KEIN_TREFFER:
				self.quelle = QUELLE_MANUELL
			if not self.flags.ignore_permissions and not self.is_new():
				vorher = self.get_doc_before_save()
				if vorher and (flt(vorher.lat) != flt(self.lat) or flt(vorher.lon) != flt(self.lon)):
					# im Desk korrigiert: Quelle „Manuell“, Nominatim überschreibt nicht mehr
					self.quelle = QUELLE_MANUELL
		elif not self.quelle:
			self.quelle = QUELLE_KEIN_TREFFER
		if not self.stand:
			self.stand = now_datetime()
