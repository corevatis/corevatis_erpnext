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
	if "Meister" in rollen:
		meister_benutzerberechtigung_entfernen(doc.name)


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


def _benutzer_mit_rolle(rolle: str) -> list[str]:
	namen = frappe.get_all(
		"Has Role", filters={"parenttype": "User", "role": rolle}, pluck="parent", distinct=True
	)
	return sorted(n for n in set(namen) if n not in ("Administrator", "Guest"))


def meister_benutzerberechtigung_entfernen(user: str) -> list[str]:
	"""ERPNext legt beim Employee mit Benutzer eine User Permission auf diesen Employee an
	(``Employee.create_user_permission``, für alle Doctypes). Für den Meister, der das Team plant, sperrt
	sie Listen, Berichte und Buchungen für andere Mitarbeiter (Abnahme Build 33: Bericht „Fahrzeuglager“
	ohne Zeilen, „Fahrzeug beladen“ 403, Abwesenheiten-Liste, Stundenkonto). Sie wird entfernt und am
	Employee abgeschaltet; Monteure behalten sie (nur eigene Belege im Desk)."""
	entfernt: list[str] = []
	for emp in frappe.get_all(
		"Employee", filters={"user_id": user, "create_user_permission": 1}, pluck="name"
	):
		frappe.db.set_value("Employee", emp, "create_user_permission", 0, update_modified=False)
	for up in frappe.get_all("User Permission", filters={"user": user, "allow": "Employee"}, pluck="name"):
		frappe.delete_doc("User Permission", up, ignore_permissions=True, force=True)
		entfernt.append(up)
	if entfernt:
		frappe.clear_cache(user=user)
	return entfernt


def ensure_meister_ohne_benutzerberechtigung() -> list[str]:
	"""Bestand: alle Benutzer mit Rolle Meister (``after_migrate``)."""
	entfernt: list[str] = []
	for user in _benutzer_mit_rolle("Meister"):
		entfernt += meister_benutzerberechtigung_entfernen(user)
	return entfernt


def employee_validate(doc, method=None) -> None:
	"""Employee eines Meisters ohne automatische User Permission (``meister_benutzerberechtigung_entfernen``)."""
	if doc.user_id and doc.get("create_user_permission") and "Meister" in frappe.get_roles(doc.user_id):
		doc.create_user_permission = 0


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


# Issue #72: Das Büro liest Mitarbeiter (Wochenplan, Zuweisung), sieht aber keine persönlichen
# Daten. Diese ERPNext-Felder liegen auf Berechtigungsstufe 1; dort dürfen nur System Manager und
# HR Manager lesen und schreiben. Wer nur Stufe 0 hat, bekommt die Felder weder in Formular noch
# API; beim Speichern bleiben ihre Werte unverändert (Frappe setzt sie auf den gespeicherten Stand).
MITARBEITER_GESCHUETZT = (
	"date_of_birth",
	"gender",
	"marital_status",
	"blood_group",
	"family_background",
	"health_details",
	"personal_email",
	"permanent_accommodation_type",
	"permanent_address",
	"current_accommodation_type",
	"current_address",
	"bio",
	"passport_number",
	"date_of_issue",
	"valid_upto",
	"place_of_issue",
	"salary_mode",
	"salary_currency",
	"ctc",
	"bank_name",
	"bank_ac_no",
	"iban",
	"education",
	"external_work_history",
	"internal_work_history",
	"resignation_letter_date",
	"relieving_date",
	"reason_for_leaving",
	"leave_encashed",
	"encashment_date",
	"held_on",
	"new_workplace",
	"feedback",
	"scheduled_confirmation_date",
	"final_confirmation_date",
	"contract_end_date",
	"notice_number_of_days",
	"date_of_retirement",
	"attendance_device_id",
)
PERMLEVEL_GESCHUETZT = 1
ROLLEN_PERSONALDATEN = ("System Manager", "HR Manager")


def ensure_mitarbeiter_felder_geschuetzt() -> list[str]:
	"""Property Setter ``permlevel`` für die persönlichen Felder und Rechte auf Stufe 1 für
	System Manager und HR Manager. Rückgabe: neu geschützte Felder."""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter
	from frappe.permissions import add_permission, update_permission_property

	if not frappe.db.exists("DocType", "Employee"):
		return []
	meta = frappe.get_meta("Employee")
	geaendert = []
	for feld in MITARBEITER_GESCHUETZT:
		df = meta.get_field(feld)
		if not df or int(df.permlevel or 0) == PERMLEVEL_GESCHUETZT:
			continue
		ps = make_property_setter(
			"Employee",
			feld,
			"permlevel",
			PERMLEVEL_GESCHUETZT,
			"Int",
			validate_fields_for_doctype=False,
		)
		frappe.db.set_value("Property Setter", ps.name, "module", "Corevatis ERPNext", update_modified=False)
		geaendert.append(feld)
	for rolle in ROLLEN_PERSONALDATEN:
		if not frappe.db.exists("Role", rolle):
			continue
		if not frappe.db.exists(
			"Custom DocPerm", {"parent": "Employee", "role": rolle, "permlevel": PERMLEVEL_GESCHUETZT}
		):
			add_permission("Employee", rolle, PERMLEVEL_GESCHUETZT)
			update_permission_property("Employee", rolle, PERMLEVEL_GESCHUETZT, "write", 1)
			geaendert.append(f"docperm:{rolle}")
	if geaendert:
		frappe.clear_cache(doctype="Employee")
	return geaendert
