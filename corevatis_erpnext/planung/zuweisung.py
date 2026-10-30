"""Aufgabe 5.2: Zuweisung eines Mitarbeiters zu einem Arbeitspaket (Task) über ein ToDo.

Die Zuweisung ist ein **ToDo** auf die Task mit ``allocated_to`` = Benutzer des Employee, angelegt
über ``frappe.desk.form.assign_to`` - dieselbe Quelle wie die Monteur-App (``baustellen_heute``,
3.1) und der Projektzugang des Kerns (``corevatis.api.projekt_zugang``, 3.12: ToDo auf Projekt
oder Task, das Büro/Meister angelegt haben). Entfernen storniert das offene ToDo („Cancelled“,
wie „Zuweisung entfernen“ im Desk); erledigte Zuweisungen (ERPNext schließt sie beim Erledigen
der Task) bleiben als Verlauf stehen.

- ``zuweisen(task, employee)`` / ``zuweisung_entfernen(task, employee)`` (whitelisted, POST):
  nur Büro, Meister, System Manager (sonst 403), idempotent (Zeilensperre auf der Task),
  Employee aktiv mit aktivem Benutzer mit Rolle Monteur oder Meister (sonst 417).
- ``zuweisungen(task)``: Stand für Dialog und Task-Formular (ToDos, Mitarbeiter-Auswahl,
  Vorgänger nicht fertig).
- ``mitarbeiter()``: Auswahl der planbaren Mitarbeiter (Select statt Link: Büro und Meister
  haben laut Site-Template kein Leserecht auf Employee).

Datum des ToDo = Tag der Zuweisung (Standard des Doctype ToDo, auch bei leerem Wert). Für
``baustellen_heute`` (Datum ≤ Tag) ist das immer erfüllt, maßgeblich ist der Beginn der Task
(``exp_start_date``) - verschiebt der Wochenplan die Task, bleibt die Zuweisung richtig.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.desk.form import assign_to
from frappe.utils import getdate

from corevatis_erpnext.planung.auftrag import PlanungFehler, darf_planen

ROLLEN_MITARBEITER = ("Monteur", "Meister")
TASK_GESCHLOSSEN = ("Completed", "Cancelled", "Template")
# zählt als Zuweisung (offen oder erledigt); „Cancelled“ = entfernt
TODO_ZUGEWIESEN = ("Open", "Closed")


def pruefe_planer() -> None:
	if not darf_planen():
		frappe.throw(
			_("Mitarbeiter zuweisen dürfen nur die Rollen Büro und Meister."), frappe.PermissionError
		)


def mitarbeiter_liste() -> list[dict]:
	"""Aktive Employees mit aktivem Systembenutzer, der die Rolle Monteur oder Meister hat."""
	zeilen = frappe.get_all(
		"Employee",
		filters={"status": "Active", "user_id": ["is", "set"]},
		fields=["name", "employee_name", "user_id"],
		order_by="employee_name asc, name asc",
	)
	if not zeilen:
		return []
	users = list({z.user_id for z in zeilen})
	aktiv = set(
		frappe.get_all(
			"User",
			filters={"name": ["in", users], "enabled": 1, "user_type": "System User"},
			pluck="name",
		)
	)
	mit_rolle = set(
		frappe.get_all(
			"Has Role",
			filters={"parenttype": "User", "parent": ["in", users], "role": ["in", ROLLEN_MITARBEITER]},
			pluck="parent",
		)
	)
	return [
		{"employee": z.name, "employee_name": z.employee_name or z.name, "user": z.user_id}
		for z in zeilen
		if z.user_id in aktiv and z.user_id in mit_rolle
	]


def mitarbeiter_pruefen(employee: str) -> frappe._dict:
	"""Employee für eine neue Zuweisung: aktiv, mit aktivem Benutzer mit Rolle Monteur/Meister."""
	if not employee:
		frappe.throw(_("Bitte einen Mitarbeiter angeben."), frappe.MandatoryError)
	werte = frappe.db.get_value(
		"Employee", employee, ["name", "employee_name", "user_id", "status"], as_dict=True
	)
	if not werte:
		frappe.throw(_("Mitarbeiter {0} nicht gefunden.").format(employee), frappe.DoesNotExistError)
	if werte.status != "Active":
		frappe.throw(_("Mitarbeiter {0} ist nicht aktiv.").format(werte.employee_name), PlanungFehler)
	if not werte.user_id:
		frappe.throw(
			_(
				"Mitarbeiter {0} hat keinen Benutzer - ohne Benutzer keine Zuweisung (App, Aufgabenliste)."
			).format(werte.employee_name),
			PlanungFehler,
		)
	user = frappe.db.get_value("User", werte.user_id, ["enabled", "user_type"], as_dict=True)
	if not user or not user.enabled or user.user_type != "System User":
		frappe.throw(
			_("Der Benutzer {0} von Mitarbeiter {1} ist nicht aktiv.").format(
				werte.user_id, werte.employee_name
			),
			PlanungFehler,
		)
	if not set(ROLLEN_MITARBEITER) & set(frappe.get_roles(werte.user_id)):
		frappe.throw(
			_("Mitarbeiter {0} hat weder die Rolle Monteur noch Meister.").format(werte.employee_name),
			PlanungFehler,
		)
	return werte


def _user_von(employee: str) -> str:
	user = frappe.db.get_value("Employee", employee, "user_id")
	if not user:
		if not frappe.db.exists("Employee", employee):
			frappe.throw(_("Mitarbeiter {0} nicht gefunden.").format(employee), frappe.DoesNotExistError)
		frappe.throw(_("Mitarbeiter {0} hat keinen Benutzer.").format(employee), PlanungFehler)
	return user


def task_sperren(task: str) -> frappe._dict:
	"""Zeilensperre auf der Task (Muster ``planung.auftrag.auftrag_sperren``, Review Epic 4 A-1):
	ein paralleler Aufruf (Doppelklick, REST-Wiederholung) wartet und sieht danach das ToDo des
	ersten. Danach Leserecht (``assign_to`` prüft es ebenfalls)."""
	if not frappe.db.transaction_writes:
		frappe.db.begin()
		frappe.db.value_cache.clear()
	if not frappe.db.sql("select name from `tabTask` where name=%s for update", task):
		frappe.throw(_("Arbeitspaket {0} nicht gefunden.").format(task), frappe.DoesNotExistError)
	frappe.has_permission("Task", "read", task, throw=True)
	return frappe.db.get_value(
		"Task",
		task,
		["name", "subject", "project", "status", "priority", "exp_start_date", "exp_end_date"],
		as_dict=True,
	)


def offene_todos(task: str, user: str) -> list[str]:
	return frappe.get_all(
		"ToDo",
		filters={"reference_type": "Task", "reference_name": task, "allocated_to": user, "status": "Open"},
		pluck="name",
		order_by="creation asc",
	)


def vorgaenger_offen(task: str) -> list[dict]:
	"""Vorgänger (``depends_on``, 5.1-Kette) der Task, die noch nicht erledigt sind."""
	vorgaenger = frappe.get_all(
		"Task Depends On", filters={"parenttype": "Task", "parent": task}, pluck="task", order_by="idx asc"
	)
	if not vorgaenger:
		return []
	zeilen = frappe.get_all(
		"Task",
		filters={"name": ["in", vorgaenger], "status": ["not in", ["Completed", "Cancelled"]]},
		fields=["name", "subject", "status", "exp_end_date"],
	)
	reihenfolge = {name: i for i, name in enumerate(vorgaenger)}
	zeilen.sort(key=lambda z: reihenfolge[z.name])
	return [
		{
			"task": z.name,
			"titel": z.subject,
			"status": z.status,
			"ende": str(z.exp_end_date) if z.exp_end_date else None,
		}
		for z in zeilen
	]


def _hinweis_vorgaenger(task: str) -> list[str]:
	return [
		_("Vorgänger {0} „{1}“ ist noch nicht fertig (Status {2}).").format(
			v["task"], v["titel"], _(v["status"])
		)
		for v in vorgaenger_offen(task)
	]


def _hinweis_ueberlast(task: frappe._dict, user: str) -> list[str]:
	from corevatis_erpnext.planung.wochenplan import ueberlast_tage

	if not task.exp_start_date:
		return []
	tage = ueberlast_tage(
		user, getdate(task.exp_start_date), getdate(task.exp_end_date or task.exp_start_date)
	)
	if not tage:
		return []
	return [
		_("Tageslast über {0} h: {1}.").format(
			frappe.format(tage[0][2], {"fieldtype": "Float", "precision": 1}),
			", ".join(
				f"{frappe.format(tag, {'fieldtype': 'Date'})} {frappe.format(last, {'fieldtype': 'Float', 'precision': 2})} h"
				for tag, last, _grenze in tage
			),
		)
	]


def stand(task: str) -> dict:
	"""Zuweisungen (ToDos, ohne entfernte) der Task mit Mitarbeiter."""
	todos = frappe.get_all(
		"ToDo",
		filters={"reference_type": "Task", "reference_name": task, "status": ["in", TODO_ZUGEWIESEN]},
		fields=["name", "allocated_to", "status", "owner", "assigned_by", "creation"],
		order_by="creation asc",
	)
	users = list({t.allocated_to for t in todos})
	employees = {}
	if users:
		for e in frappe.get_all(
			"Employee", filters={"user_id": ["in", users]}, fields=["name", "employee_name", "user_id"]
		):
			employees.setdefault(e.user_id, e)
	zugewiesen = []
	for t in todos:
		e = employees.get(t.allocated_to)
		zugewiesen.append(
			{
				"todo": t.name,
				"status": t.status,
				"user": t.allocated_to,
				"employee": e.name if e else None,
				"employee_name": (e.employee_name if e else None)
				or frappe.utils.get_fullname(t.allocated_to),
				"zugewiesen_von": t.assigned_by or t.owner,
				"seit": str(t.creation),
			}
		)
	return {"task": task, "zugewiesen": zugewiesen}


@frappe.whitelist()
def mitarbeiter() -> list[dict]:
	"""Auswahl (Select) im Wochenplan und am Task: ``value`` = Employee, ``label`` = Name."""
	pruefe_planer()
	return [{"value": m["employee"], "label": m["employee_name"]} for m in mitarbeiter_liste()]


@frappe.whitelist()
def zuweisungen(task: str) -> dict:
	"""Stand für Dialog und Task-Formular: Zuweisungen, Vorgänger nicht fertig, Mitarbeiter-Auswahl."""
	pruefe_planer()
	if not frappe.db.exists("Task", task):
		frappe.throw(_("Arbeitspaket {0} nicht gefunden.").format(task), frappe.DoesNotExistError)
	frappe.has_permission("Task", "read", task, throw=True)
	t = frappe.db.get_value(
		"Task", task, ["subject", "project", "status", "exp_start_date", "exp_end_date"], as_dict=True
	)
	ergebnis = stand(task)
	ergebnis.update(
		{
			"titel": t.subject,
			"projekt": t.project,
			"status": t.status,
			"start": str(t.exp_start_date) if t.exp_start_date else None,
			"ende": str(t.exp_end_date) if t.exp_end_date else None,
			"geschlossen": t.status in TASK_GESCHLOSSEN,
			"vorgaenger_offen": vorgaenger_offen(task),
			"mitarbeiter": [
				{"value": m["employee"], "label": m["employee_name"]} for m in mitarbeiter_liste()
			],
		}
	)
	return ergebnis


@frappe.whitelist(methods=["POST"])
def zuweisen(task: str, employee: str) -> dict:
	"""Mitarbeiter einem Arbeitspaket zuweisen: genau ein offenes ToDo (``assign_to``) je Task und
	Benutzer. Wiederholung legt nichts an (``neu: false``). Hinweise: Vorgänger nicht fertig,
	Tageslast über der Tagesarbeitszeit."""
	pruefe_planer()
	m = mitarbeiter_pruefen(employee)
	t = task_sperren(task)
	if t.status in TASK_GESCHLOSSEN:
		frappe.throw(
			_("Arbeitspaket {0} ist {1} - keine neue Zuweisung.").format(task, _(t.status)), PlanungFehler
		)
	hinweise = _hinweis_vorgaenger(task)
	vorhanden = offene_todos(task, m.user_id)
	neu = not vorhanden
	if neu:
		projekt_name = frappe.db.get_value("Project", t.project, "project_name") if t.project else None
		beschreibung = _("Arbeitspaket {0}").format(frappe.utils.escape_html(t.subject or task))
		if t.project:
			beschreibung += f" ({frappe.utils.escape_html(projekt_name or t.project)})"
		assign_to._add(
			{
				"assign_to": [m.user_id],
				"doctype": "Task",
				"name": task,
				"description": beschreibung,
				"priority": t.priority or "Medium",
				# Datum = heute (Standard): maßgeblich ist der Beginn der Task (baustellen_heute)
			},
			ignore_permissions=False,
		)
		vorhanden = offene_todos(task, m.user_id)
	else:
		hinweise.insert(0, _("{0} ist dem Arbeitspaket bereits zugewiesen.").format(m.employee_name))
	hinweise += _hinweis_ueberlast(t, m.user_id)
	ergebnis = stand(task)
	ergebnis.update(
		{
			"employee": m.name,
			"employee_name": m.employee_name,
			"user": m.user_id,
			"todo": vorhanden[0] if vorhanden else None,
			"neu": neu,
			"hinweise": hinweise,
		}
	)
	return ergebnis


@frappe.whitelist(methods=["POST"])
def zuweisung_entfernen(task: str, employee: str) -> dict:
	"""Zuweisung entfernen: offene ToDos des Benutzers auf die Task stornieren (``Cancelled``).
	Ohne offene Zuweisung nichts (``entfernt: false``). Der Employee muss nicht mehr aktiv sein."""
	pruefe_planer()
	user = _user_von(employee)
	task_sperren(task)
	todos = offene_todos(task, user)
	for todo in todos:
		assign_to.set_status("Task", task, todo=todo, assign_to=user, status="Cancelled")
	ergebnis = stand(task)
	ergebnis.update(
		{
			"employee": employee,
			"user": user,
			"entfernt": bool(todos),
			"todos": todos,
			"hinweise": []
			if todos
			else [_("{0} war dem Arbeitspaket nicht zugewiesen.").format(frappe.utils.get_fullname(user))],
		}
	)
	return ergebnis
