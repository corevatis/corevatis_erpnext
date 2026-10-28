"""Workflow „Lead Anfrage“ auf ``Lead`` (Aufgabe 1.4), idempotent aus Code.

Zustandsfeld ist ``cv_status`` (eigenes Select), nicht ``Lead.status``: ERPNext setzt
``status`` in ``Lead.validate`` selbst (Opportunity, Quotation, Converted, ...), ein
Workflow darauf würde mit dieser Automatik kollidieren und bräuchte Property Setter
auf den Standardoptionen. Begründung: ``docs/1.4-lead-workflow.md`` im Repo Handwerker.

Der Code ist Quelle der Wahrheit: ``ensure_workflow()`` stellt bei jedem ``migrate``
den Sollzustand her (Änderungen am Workflow auf der Site werden überschrieben) und
speichert nur, wenn sich etwas unterscheidet.
"""

import frappe

from corevatis_erpnext.anfrage.custom_fields import ANFRAGE_STATUS

WORKFLOW_NAME = "Lead Anfrage"
DOCTYPE = "Lead"
STATE_FIELD = "cv_status"

BUERO = "Büro"
MEISTER = "Meister"
# Bearbeiten im jeweiligen Zustand (Formular); System Manager als Rückfallebene
EDIT_ROLES = (BUERO, MEISTER, "System Manager")

# Zustand -> Stil des Workflow State (Farbe in Liste und Formular)
STATE_STYLES = {
	"Neu": "Warning",
	"Termin": "Info",
	"Besichtigt": "Primary",
	"Angebot": "Inverse",
	"Gewonnen": "Success",
	"Verloren": "Danger",
}

ACTION_TERMIN = "Termin vereinbaren"
ACTION_BESICHTIGT = "Besichtigung abschließen"
ACTION_ANGEBOT = "Angebot erstellt"
ACTION_GEWONNEN = "Als gewonnen markieren"
ACTION_VERLOREN = "Als verloren markieren"
ACTION_WIEDER_OEFFNEN = "Wieder öffnen"

# (Zustand, Aktion, Folgezustand, Rollen)
TRANSITIONS = (
	("Neu", ACTION_TERMIN, "Termin", (BUERO, MEISTER)),
	("Termin", ACTION_BESICHTIGT, "Besichtigt", (MEISTER,)),
	("Besichtigt", ACTION_ANGEBOT, "Angebot", (BUERO, MEISTER)),
	("Angebot", ACTION_GEWONNEN, "Gewonnen", (BUERO,)),
	("Neu", ACTION_VERLOREN, "Verloren", (BUERO, MEISTER)),
	("Termin", ACTION_VERLOREN, "Verloren", (BUERO, MEISTER)),
	("Besichtigt", ACTION_VERLOREN, "Verloren", (BUERO, MEISTER)),
	("Angebot", ACTION_VERLOREN, "Verloren", (BUERO, MEISTER)),
	("Verloren", ACTION_WIEDER_OEFFNEN, "Neu", (BUERO,)),
)

assert tuple(STATE_STYLES) == ANFRAGE_STATUS


def ensure_workflow_states() -> None:
	for state, style in STATE_STYLES.items():
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc(
				{"doctype": "Workflow State", "workflow_state_name": state, "style": style}
			).insert(ignore_permissions=True)
		elif frappe.db.get_value("Workflow State", state, "style") != style:
			frappe.db.set_value("Workflow State", state, "style", style)


def ensure_actions() -> None:
	for action in dict.fromkeys(t[1] for t in TRANSITIONS):
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
				ignore_permissions=True
			)


def desired_states() -> list[dict]:
	return [
		{"state": state, "doc_status": "0", "allow_edit": role}
		for state in ANFRAGE_STATUS
		for role in EDIT_ROLES
	]


def desired_transitions() -> list[dict]:
	return [
		{
			"state": state,
			"action": action,
			"next_state": next_state,
			"allowed": role,
			"allow_self_approval": 1,
		}
		for state, action, next_state, roles in TRANSITIONS
		for role in roles
	]


def _rows(doc, table: str, keys: tuple[str, ...]) -> list[tuple]:
	return [tuple(str(row.get(key) or "") for key in keys) for row in doc.get(table)]


def _wanted(rows: list[dict], keys: tuple[str, ...]) -> list[tuple]:
	return [tuple(str(row.get(key) or "") for key in keys) for row in rows]


STATE_KEYS = ("state", "doc_status", "allow_edit", "update_field")
TRANSITION_KEYS = ("state", "action", "next_state", "allowed", "allow_self_approval", "condition")


def ensure_workflow() -> None:
	ensure_workflow_states()
	ensure_actions()

	header = {
		"document_type": DOCTYPE,
		"workflow_state_field": STATE_FIELD,
		"is_active": 1,
		"override_status": 0,
		"send_email_alert": 0,
	}
	states, transitions = desired_states(), desired_transitions()

	if frappe.db.exists("Workflow", WORKFLOW_NAME):
		doc = frappe.get_doc("Workflow", WORKFLOW_NAME)
		same = (
			all(str(doc.get(key)) == str(value) for key, value in header.items())
			and _rows(doc, "states", STATE_KEYS) == _wanted(states, STATE_KEYS)
			and _rows(doc, "transitions", TRANSITION_KEYS) == _wanted(transitions, TRANSITION_KEYS)
		)
		if same:
			return
		doc.update(header)
		doc.set("states", states)
		doc.set("transitions", transitions)
		doc.save(ignore_permissions=True)
	else:
		frappe.get_doc(
			{
				"doctype": "Workflow",
				"workflow_name": WORKFLOW_NAME,
				**header,
				"states": states,
				"transitions": transitions,
			}
		).insert(ignore_permissions=True)

	frappe.clear_cache(doctype=DOCTYPE)
