"""Nachrichten am Vorgang (P1.6) im ERPNext-Adapter: Stand für die Prozessleiste und den
Ungelesen-Zähler der Leiste „Betrieb“.

Die Nachrichten selbst (Doctype ``Nachricht``, Mobile-API, Push, Desk-Page ``nachrichten``) liegen im
Kern ``corevatis``; der Adapter ruft nur zwei Kern-Funktionen per ``frappe.get_attr`` (kein Import,
Lizenzgrenze; Vertragstest ``corevatis/tests/test_erpnext_contract.py::test_nachrichten``). Ohne
installierten Kern liefert alles ``None`` bzw. ``0``.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint

KERN_APP = "corevatis"
KERN_DOCTYPE = "Nachricht"
KERN_ANKER = "corevatis.mappe.anker.ermitteln"
KERN_STAND = "corevatis.nachrichten.lesestand.stand_fuer_prozessleiste"
KERN_UNGELESEN = "corevatis.nachrichten.lesestand.ungelesen_gesamt"
PAGE = "nachrichten"


def kern_da() -> bool:
	return KERN_APP in frappe.get_installed_apps() and bool(frappe.db.exists("DocType", KERN_DOCTYPE))


def _kern(pfad: str):
	return frappe.get_attr(pfad)


def stand(doctype: str, name: str) -> dict | None:
	"""Zeile „Nachrichten: n ungelesen“ für die Prozessleiste eines Belegs (``prozess.kette``):
	``vorgang`` (Anker), ``gesamt``, ``ungelesen``, ``letzte``; ``None`` ohne Kern."""
	if not kern_da():
		return None
	anker = _kern(KERN_ANKER)(doctype, name) or name
	s = _kern(KERN_STAND)(anker) or {}
	return {
		"vorgang": anker,
		"gesamt": cint(s.get("gesamt")),
		"ungelesen": cint(s.get("ungelesen")),
		"letzte": s.get("letzte"),
	}


@frappe.whitelist()
def ungelesen_gesamt() -> int:
	"""Ungelesene Nachrichten des angemeldeten Benutzers über alle seine Vorgänge (Badge in der
	Leiste „Betrieb“, ``betrieb.js``); ``0`` ohne Kern."""
	if not kern_da():
		return 0
	return cint(_kern(KERN_UNGELESEN)())
