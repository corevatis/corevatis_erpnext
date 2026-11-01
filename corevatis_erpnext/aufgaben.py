"""Aufgaben am Vorgang (Phase 3 P3.4, Handwerker#96): Pille „Aufgaben: n offen“ in der Prozessleiste.

Die Aufgaben selbst sind ToDos mit den Kern-Feldern ``cv_quelle``, ``cv_vorgang_doctype`` und
``cv_vorgang`` (App ``corevatis``, ``corevatis.aufgaben``). Der Adapter liest sie nur; Anlegen und
Erledigen laufen im Desk über die Kern-Methoden ``corevatis.aufgaben.desk.*`` (aus dem JavaScript,
kein Python-Import über die Lizenzgrenze). Ohne Kern fehlen die Felder, die Pille entfällt.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import today

TODO = "ToDo"
FELDER = ("cv_quelle", "cv_vorgang_doctype", "cv_vorgang")
VORGANG_DOCTYPES = ("Lead", "Sales Order", "Project")


def kern_da() -> bool:
	"""Kern-Felder am ToDo vorhanden (Kern installiert und migriert)."""
	meta = frappe.get_meta(TODO)
	return all(meta.has_field(f) for f in FELDER)


def stand(vorgaenge: list[tuple[str, str]]) -> dict | None:
	"""Offene und überfällige Aufgaben zu den Datensätzen des Vorgangs.

	:param vorgaenge: (Doctype, Name) von Anfrage, Aufträgen und Projekten, für die der Benutzer
	    Leserecht hat
	:return: ``None`` ohne Kern oder ohne Aufgaben, sonst Zähler, Text, Ampel und Filter für die Liste
	"""
	paare = [(dt, n) for dt, n in vorgaenge if dt in VORGANG_DOCTYPES and n]
	if not paare or not kern_da():
		return None
	namen = sorted({n for _dt, n in paare})
	# get_list prüft die ToDo-Rechte des Benutzers (Kern: Büro/Meister sehen alle Aufgaben,
	# Monteure nur ihre eigenen); verworfene Aufgaben zählen nicht
	zeilen = frappe.get_list(
		TODO,
		filters=[
			[TODO, "cv_quelle", "is", "set"],
			[TODO, "cv_vorgang", "in", namen],
			[TODO, "status", "!=", "Cancelled"],
		],
		fields=["name", "status", "date", "cv_vorgang_doctype", "cv_vorgang"],
		limit_page_length=0,
	)
	gueltig = set(paare)
	zeilen = [z for z in zeilen if (z.cv_vorgang_doctype, z.cv_vorgang) in gueltig]
	if not zeilen:
		return None
	heute = today()
	offen = [z for z in zeilen if z.status == "Open"]
	ueberfaellig = [z for z in offen if z.date and str(z.date) < heute]
	if ueberfaellig:
		text = _("Aufgaben: {0} offen, {1} überfällig").format(len(offen), len(ueberfaellig))
		ampel = "rot"
	elif offen:
		text = _("Aufgaben: {0} offen").format(len(offen))
		ampel = "gelb"
	else:
		text = _("Aufgaben: alle erledigt")
		ampel = "grün"
	return {
		"offen": len(offen),
		"ueberfaellig": len(ueberfaellig),
		"gesamt": len(zeilen),
		"text": text,
		"ampel": ampel,
		"vorgaenge": namen,
		# Anlegen im Dialog: der Datensatz, an dem die Leiste steht, sonst der erste des Vorgangs
		"anker": {"doctype": paare[0][0], "name": paare[0][1]},
	}
