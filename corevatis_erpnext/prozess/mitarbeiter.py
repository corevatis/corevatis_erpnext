"""Durchstich A2: Wer in der App Zeiten bucht oder Material entnimmt, braucht einen Mitarbeiter
(ERPNext ``Employee``) am Benutzer. Statt einer Fehlermeldung in der App legt das Büro nur den
Benutzer mit Rolle Monteur oder Meister an; der Mitarbeiter entsteht automatisch (idempotent,
``User.on_update`` und einmal je ``migrate`` für den Bestand). Vorname/Nachname kommen vom
Benutzer, die Firma ist die Standardfirma der Site.
"""

from __future__ import annotations

import frappe
from frappe.utils import today

ROLLEN_MITARBEITER = ("Monteur", "Meister")


def user_on_update(doc, method=None) -> None:
	if doc.get("user_type") != "System User" or doc.name in ("Administrator", "Guest"):
		return
	if not doc.enabled:
		return
	rollen = {r.role for r in doc.get("roles", [])}
	if not rollen & set(ROLLEN_MITARBEITER):
		return
	ensure_mitarbeiter(doc.name)


def ensure_mitarbeiter(user: str) -> str | None:
	"""Mitarbeiter zum Benutzer anlegen, wenn keiner existiert; Rückgabe: Employee-Name."""
	vorhanden = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if vorhanden:
		return vorhanden
	company = frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")
	if not company:
		return None
	u = frappe.db.get_value("User", user, ["first_name", "last_name", "full_name"], as_dict=True)
	if not u:
		return None
	emp = frappe.get_doc(
		{
			"doctype": "Employee",
			"first_name": u.first_name or u.full_name or user.split("@")[0],
			"last_name": u.last_name,
			"employee_name": u.full_name or u.first_name,
			"user_id": user,
			"company": company,
			"status": "Active",
			"date_of_joining": today(),
			# ERPNext verlangt Geschlecht und Geburtsdatum; beides ist für den Betrieb ohne Belang
			"gender": _gender_platzhalter(),
			"date_of_birth": "1980-01-01",
			"create_user_permission": 0,
		}
	)
	emp.flags.ignore_permissions = True
	emp.insert()
	return emp.name


def _gender_platzhalter() -> str | None:
	for name in ("Other", "Prefer not to say", "Male"):
		if frappe.db.exists("Gender", name):
			return name
	return None


def ensure_mitarbeiter_bestand() -> None:
	namen = frappe.get_all(
		"Has Role",
		filters={"parenttype": "User", "role": ["in", list(ROLLEN_MITARBEITER)]},
		pluck="parent",
		distinct=True,
	)
	for name in sorted(set(namen)):
		user = frappe.db.get_value("User", name, ["enabled", "user_type"], as_dict=True)
		if (
			user
			and user.enabled
			and user.user_type == "System User"
			and name not in ("Administrator", "Guest")
		):
			ensure_mitarbeiter(name)
