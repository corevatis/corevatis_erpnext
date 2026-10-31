"""Dokumentenmappe (Epic D) im ERPNext-Adapter: Hooks an Angebot, Auftrag und Rechnung.

Die Logik (Mappenregeln, Pflichtdokumente, Ampel, Journal) rechnet im Kern ``corevatis``; der
Adapter ruft sie nur per ``frappe.get_attr`` über die Funktionen in ``KERN_*`` (kein Import,
Lizenzgrenze; Vertragstest ``corevatis/tests/test_erpnext_contract.py::test_mappe``). Ohne
installierten Kern (oder ohne den Doctype ``Mappenregel``) sind alle Hooks wirkungslos.

- ``Quotation.validate`` → ``angebot_validieren``: Übersteuerungsfelder bei jedem Speichern sichern;
  ``Quotation.before_submit`` → ``angebot_pruefen``: Pflichtdokumente ab Schritt Angebot
  (Übersteuern mit Begründung über ``cv_mappe_uebersteuert``/``cv_mappe_grund``, nur Büro).
- ``Quotation.on_submit`` → ``angebot_freigegeben``: PDF „Angebot Corevatis“ (Standard-Druckformat
  des Angebots) als private Datei am Angebot in Ordner 03 Angebot.
- ``Sales Order.on_submit`` → ``auftrag_freigegeben``: Auftragsbestätigung (PDF) in 04 Auftrag.
- ``Sales Invoice.validate`` → ``rechnung_pruefen`` (vor der Abnahmesperre 6.2): Pflichtdokumente
  ab Schritt Rechnung - ohne Abnahmeprotokoll in der Mappe wird die Rechnung abgelehnt, außer
  Gutschrift, Abschlagsrechnung (``cv_abschlag``) oder Übersteuerung mit Begründung (Büro/System
  Manager, protokolliert in ``Mappenpruefung``). ``Sales Invoice.on_submit`` → PDF in 07 Rechnung.

Fehler beim PDF halten die Freigabe nicht auf (Savepoint, Error Log, orange Meldung - Muster 5.1).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, strip_html

KERN_APP = "corevatis"
KERN_DOCTYPE = "Mappenregel"
KERN_BELEG_PDF = "corevatis.mappe.ablage.beleg_pdf"
KERN_PRUEFEN = "corevatis.mappe.pruefung.pruefen"
KERN_UEBERSTEUERUNG = "corevatis.mappe.pruefung.uebersteuerung_pruefen"
KERN_STAND = "corevatis.mappe.ampel.stand"
KERN_ANKER = "corevatis.mappe.anker.ermitteln"

SCHRITT_ANGEBOT, SCHRITT_RECHNUNG = "Angebot", "Rechnung"
EREIGNIS_FREIGABE = "Freigabe"
DRUCKFORMAT_ANGEBOT = "Angebot Corevatis"
PAGE = "mappe"


def kern_da() -> bool:
	return KERN_APP in frappe.get_installed_apps() and bool(frappe.db.exists("DocType", KERN_DOCTYPE))


def _kern(pfad: str):
	return frappe.get_attr(pfad)


def anker(doctype: str, name: str) -> str | None:
	"""Vorgang (Anfrage, sonst Projekt) eines Belegs - aus dem Kern."""
	if not kern_da():
		return None
	return _kern(KERN_ANKER)(doctype, name)


def stand(doctype: str, name: str) -> dict | None:
	"""Ampel der Mappe zu einem Beleg für die Prozessleiste (``prozess.kette``); ``None`` ohne Kern."""
	if not kern_da():
		return None
	a = anker(doctype, name)
	if not a:
		return None
	s = _kern(KERN_STAND)(a, False)
	return {"vorgang": a, "ampel": s.get("ampel"), "text": s.get("text"), "schritt": s.get("schritt")}


# --- Pflichtprüfung -----------------------------------------------------------------------------


def _uebersteuert(doc) -> bool:
	"""Übersteuerungsfelder sichern (Kern: nur Büro/System Manager, Begründung). Ohne Kern wird eine
	gesetzte Übersteuerung verworfen - sonst ließe sich die Abnahmesperre 6.2 ohne Prüfung umgehen."""
	if not doc.meta.has_field("cv_mappe_uebersteuert"):
		return False
	if not kern_da():
		if cint(doc.get("cv_mappe_uebersteuert")):
			doc.cv_mappe_uebersteuert = 0
			doc.cv_mappe_grund = None
			if doc.meta.has_field("cv_mappe_uebersteuert_von"):
				doc.cv_mappe_uebersteuert_von = None
		return False
	return bool(_kern(KERN_UEBERSTEUERUNG)(doc))


def _pruefen(doc, schritt: str, protokoll: bool) -> dict | None:
	"""``protokoll``: Ergebnis in ``Mappenpruefung`` festhalten - immer auch bei einer neu gesetzten
	Übersteuerung (Flag ``cv_mappe_uebersteuert_neu`` setzt der Kern in ``_uebersteuert``)."""
	uebersteuert = _uebersteuert(doc)
	protokoll = protokoll or bool(doc.flags.get("cv_mappe_uebersteuert_neu"))
	if not kern_da():
		return None
	a = anker_des_belegs(doc)
	if not a:
		return None
	ergebnis = _kern(KERN_PRUEFEN)(
		schritt,
		a,
		doc.doctype,
		doc.name,
		uebersteuert,
		doc.get("cv_mappe_grund"),
		protokoll,
	)
	if ergebnis and ergebnis.get("hinweis") and protokoll:
		frappe.msgprint(ergebnis["hinweis"], indicator="orange", alert=True)
	return ergebnis


def anker_des_belegs(doc) -> str | None:
	"""Vorgang eines Belegs - gespeichert über seinen Namen, sonst (Anlegen mit ``docstatus`` 1,
	``frappe.client.insert``) über die Verweise im Speicher: Projekt und Aufträge der Rechnung,
	Anfrage bzw. Aufmaß des Angebots (Security-Review)."""
	# ``frappe.get_doc(dict)`` ohne Insert hat weder Namen noch ``__islocal``: wie neu behandeln
	if doc.name and not doc.is_new():
		if a := anker(doc.doctype, doc.name):
			return a
	kandidaten: list[tuple[str, str | None]] = []
	if doc.doctype == "Sales Invoice":
		kandidaten.append(("Project", doc.get("project")))
		for zeile in doc.get("items") or []:
			kandidaten.append(("Project", zeile.get("project")))
			kandidaten.append(("Sales Order", zeile.get("sales_order")))
	elif doc.doctype == "Quotation":
		kandidaten.append(("Lead", doc.get("cv_lead")))
		if doc.get("quotation_to") == "Lead":
			kandidaten.append(("Lead", doc.get("party_name")))
		kandidaten.append(("Aufmass", doc.get("cv_aufmass")))
	for doctype, name in kandidaten:
		if name and frappe.db.exists(doctype, name) and (a := anker(doctype, name)):
			return a
	return None


def angebot_validieren(doc, method=None) -> None:
	"""``Quotation.validate``: Übersteuerungsfelder bei **jedem** Speichern sichern. Nur bei
	``before_submit`` geprüft, ließe sich die Übersteuerung im Entwurf ohne Rolle setzen und beim
	Freigeben als „unverändert“ durchreichen (Security-Review)."""
	_uebersteuert(doc)


def angebot_pruefen(doc, method=None) -> None:
	"""``Quotation.before_submit``: Pflichtdokumente ab Schritt Angebot."""
	_pruefen(doc, SCHRITT_ANGEBOT, protokoll=True)


def rechnung_pruefen(doc, method=None) -> None:
	"""``Sales Invoice.validate`` (Entwurf und Buchen), vor der Abnahmesperre 6.2.

	Gutschriften und Abschlagsrechnungen (``cv_abschlag``) sind nicht betroffen. Protokolliert
	wird beim Buchen und bei einer neu gesetzten Übersteuerung; Zwischenstände beim Speichern
	nicht (sonst eine Zeile je Speichern)."""
	if cint(doc.get("is_return")) or cint(doc.get("cv_abschlag")):
		_uebersteuert(doc)
		return
	_pruefen(doc, SCHRITT_RECHNUNG, protokoll=doc.docstatus == 1)


# --- PDFs in die Mappe --------------------------------------------------------------------------


def _pdf(doc, print_format: str | None, bezeichnung: str) -> dict | None:
	if not kern_da():
		return None
	savepoint = "cv_mappe_pdf"
	frappe.db.savepoint(savepoint)
	try:
		return _kern(KERN_BELEG_PDF)(doc.doctype, doc.name, print_format, EREIGNIS_FREIGABE)
	except Exception as fehler:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(
			title=f"Dokumentenmappe: {bezeichnung} zu {doc.name} nicht abgelegt",
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
		frappe.msgprint(
			_("{0} konnte nicht in die Mappe gelegt werden: {1}").format(
				bezeichnung, strip_html(str(fehler))
			),
			indicator="orange",
			alert=True,
		)
		return None


def _standard_druckformat(doctype: str, bevorzugt: str | None = None) -> str | None:
	if bevorzugt and frappe.db.exists("Print Format", bevorzugt):
		return bevorzugt
	return frappe.get_meta(doctype).default_print_format or None


def angebot_freigegeben(doc, method=None) -> None:
	"""``Quotation.on_submit``: PDF des Angebots (Druckformat 4.3) in Ordner 03 Angebot."""
	_pdf(doc, _standard_druckformat("Quotation", DRUCKFORMAT_ANGEBOT), _("Angebots-PDF"))


def auftrag_freigegeben(doc, method=None) -> None:
	"""``Sales Order.on_submit``: Auftragsbestätigung als PDF in Ordner 04 Auftrag."""
	_pdf(doc, _standard_druckformat("Sales Order"), _("Auftragsbestätigung"))


def rechnung_freigegeben(doc, method=None) -> None:
	"""``Sales Invoice.on_submit``: Rechnung als PDF in Ordner 07 Rechnung (keine Gutschrift-PDFs)."""
	if cint(doc.get("is_return")):
		return
	_pdf(doc, _standard_druckformat("Sales Invoice"), _("Rechnungs-PDF"))
