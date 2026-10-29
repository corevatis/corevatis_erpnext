"""Gemeinsame Hilfen der Planung (5.1): Rechte, Auftrag und Projekt, Sperre."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

ROLLEN = ("Büro", "Meister", "System Manager")


class PlanungFehler(frappe.ValidationError):
	pass


def darf_planen(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(set(ROLLEN) & set(frappe.get_roles(user)))


def pruefe_rolle() -> None:
	if not darf_planen():
		frappe.throw(
			_("Arbeitspakete und Materialbedarf erzeugen dürfen nur die Rollen Büro und Meister."),
			frappe.PermissionError,
		)


def ist_corevatis_auftrag(auftrag) -> bool:
	"""Auftrag aus einem Angebot aus dem Aufmaß (Spiegel von ``cv_erzeugt_aus_aufmass``/``cv_aufmass``)."""
	return bool(cint(auftrag.get("cv_erzeugt_aus_aufmass")) or auftrag.get("cv_aufmass"))


def projekt_zum_auftrag(auftrag) -> str | None:
	return auftrag.get("project") or frappe.db.get_value(
		"Project", {"sales_order": auftrag.name, "status": ["!=", "Cancelled"]}, "name"
	)


def auftrag_sperren(name: str) -> None:
	"""Zeilensperre auf dem Auftrag (wie ``angebot.annahme.angebot_sperren``, Review Epic 4 A-1).

	Hat die Anfrage noch nichts geschrieben, beginnt eine neue Transaktion (REPEATABLE READ: der
	Lese-Snapshot entstünde sonst schon bei der Anmeldung); dann ``FOR UPDATE`` vor jedem Lesen
	der Belegdaten. Ein paralleler Aufruf (Doppelklick, REST-Wiederholung) wartet hier und sieht
	danach die Belege des ersten.
	"""
	if not frappe.db.transaction_writes:
		frappe.db.begin()
		frappe.db.value_cache.clear()
	if not frappe.db.sql("select name from `tabSales Order` where name=%s for update", name):
		frappe.throw(_("Auftrag {0} nicht gefunden.").format(name), frappe.DoesNotExistError)


def auftrag_laden(sales_order: str | None, project: str | None, hinweise: list[str]):
	"""Auftrag zum Aufruf (direkt oder über ``Project.sales_order``) mit Rechte- und Statusprüfung.

	Rückgabe ``(auftrag, projekt)``; ``(None, None)`` mit Hinweis, wenn ein Projekt keinen Auftrag hat.
	"""
	if not sales_order and project:
		frappe.has_permission("Project", "read", project, throw=True)
		sales_order = frappe.db.get_value("Project", project, "sales_order")
		if not sales_order:
			hinweise.append(_("Projekt {0} hat keinen Auftrag - nichts zu erzeugen.").format(project))
			return None, project
	if not sales_order:
		frappe.throw(_("Bitte einen Auftrag oder ein Projekt angeben."), frappe.MandatoryError)
	auftrag_sperren(sales_order)
	frappe.has_permission("Sales Order", "read", sales_order, throw=True)
	auftrag = frappe.get_doc("Sales Order", sales_order)
	if auftrag.docstatus != 1:
		frappe.throw(
			_(
				"Auftrag {0} ist nicht eingereicht - Arbeitspakete und Materialbedarf erst nach dem Einreichen."
			).format(auftrag.name),
			PlanungFehler,
		)
	projekt = project or projekt_zum_auftrag(auftrag)
	if not projekt:
		hinweise.append(
			_("Auftrag {0} hat kein Projekt - bitte zuerst ein Projekt anlegen (Angebot annehmen).").format(
				auftrag.name
			)
		)
	return auftrag, projekt
