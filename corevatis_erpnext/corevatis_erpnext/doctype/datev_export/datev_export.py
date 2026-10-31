"""DATEV Export (7.4): Firma, Zeitraum und Optionen gibt das Büro vor; Status, Kennzahlen, Dateien,
Protokoll und Belegliste schreibt nur der Server (``corevatis_erpnext.datev.export.erstellen``).
Ein erstellter Export ist eingefroren: Zeitraum und Optionen lassen sich nicht mehr ändern (für eine
Wiederholung legt das Büro einen neuen Export an); löschen darf ihn nur ein System Manager."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate

from corevatis_erpnext.datev.export import STATUS_ENTWURF, STATUS_ERSTELLT, titel

EINGABEN = (
	"firma",
	"von",
	"bis",
	"belegarten",
	"festschreibung",
	"nur_neue",
	"mit_belegbildern",
	"druckformat",
)
SERVER_FELDER = (
	"status",
	"erstellt_am",
	"erstellt_von",
	"pruefung",
	"wiederholung",
	"anzahl_buchungen",
	"anzahl_rechnungen",
	"anzahl_gutschriften",
	"anzahl_zahlungen",
	"anzahl_sonstige",
	"summe_soll",
	"summe_haben",
	"rechnungen_brutto",
	"zahlungen_brutto",
	"anzahl_debitoren",
	"anzahl_belegbilder",
	"datei_buchungsstapel",
	"datei_debitoren",
	"datei_belegbilder",
	"protokoll",
)


def _wert(doc, feld: str):
	"""Vergleichbarer Wert einer Eingabe (Datum als Text, Haken als 0/1, leer als None)."""
	wert = doc.get(feld)
	if feld in ("von", "bis"):
		return str(getdate(wert)) if wert else None
	if feld in ("festschreibung", "nur_neue", "mit_belegbildern"):
		return cint(wert)
	return wert or None


class DATEVExport(Document):
	def validate(self):
		if self.von and self.bis and getdate(self.von) > getdate(self.bis):
			frappe.throw(_("Der Zeitraum beginnt nach seinem Ende."))
		if self.flags.cv_server:
			return
		alt = self.get_doc_before_save()
		if alt and alt.status == STATUS_ERSTELLT:
			geaendert = [f for f in EINGABEN if _wert(self, f) != _wert(alt, f)]
			if geaendert:
				frappe.throw(
					_(
						"Der Export {0} ist schon erstellt - Zeitraum und Einstellungen sind eingefroren ({1}). "
						"Für eine Wiederholung bitte einen neuen DATEV-Export anlegen."
					).format(self.name, ", ".join(geaendert))
				)
		# Serverfelder setzt nur der Export selbst
		for feld in SERVER_FELDER:
			self.set(feld, alt.get(feld) if alt else None)
		self.set("belege", [d.as_dict() for d in alt.get("belege", [])] if alt else [])
		if not alt:
			self.status = STATUS_ENTWURF
		self.titel = titel(self)

	def on_trash(self):
		if self.status == STATUS_ERSTELLT and "System Manager" not in frappe.get_roles():
			frappe.throw(
				_("Ein erstellter DATEV-Export lässt sich nicht löschen (Nachvollziehbarkeit)."),
				frappe.PermissionError,
			)
