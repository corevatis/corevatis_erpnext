"""Projekt verständlich machen (Rückmeldung 31.10., Muster Angebot D8).

Der Betrieb fand nach der Annahme ein „leeres Projekt“: Die Arbeitspakete (5.1) standen nur unter
„Verbindungen“, und nichts sagte, wie daraus Arbeit für die Monteure wird. ``zustand`` liefert dem
Projektformular (``public/js/planung.js``) und der Prozessleiste (Schritt „Ausführung“):

- einen **Zustandstext** in Klartext („Aus Auftrag AB-… vom …: 4 Arbeitspakete, 0 zugewiesen.
  Nächster Schritt: …“),
- **genau einen Hauptknopf** je Zustand: keine Arbeitspakete → „Arbeitspakete anlegen“ (5.1),
  Arbeitspakete ohne Zuweisung → „Wochenplan öffnen“ (5.2), alle zugewiesen → „Baustellenakte“,
- die **Arbeitspakete** als Liste (Verfahren, Sollstunden, Termin, zugewiesen an, Status).

Zugewiesen ist ein Arbeitspaket mit einem ToDo, das nicht storniert ist (5.2: Zuweisung = ToDo;
erledigte bleiben zugewiesen). Offen ohne Zuweisung zählt nur, was nicht erledigt oder storniert ist.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, format_date, getdate

from corevatis_erpnext.planung.auftrag import darf_planen

KEINE = "keine_arbeitspakete"
NICHT_ZUGEWIESEN = "nicht_zugewiesen"
ZUGEWIESEN = "zugewiesen"
ABGESCHLOSSEN = "abgeschlossen"
TASK_ZU = ("Completed", "Cancelled", "Template")
PROJEKT_ZU = ("Completed", "Cancelled")
WEITER = "\u2013"  # Gedankenstrich (ruff RUF001)


def zuweisungen(tasks: list[str]) -> dict[str, list[dict]]:
	"""Zuweisungen je Arbeitspaket: ToDo (Task), nicht storniert, älteste zuerst."""
	if not tasks:
		return {}
	ergebnis: dict[str, list[dict]] = {}
	for z in frappe.get_all(
		"ToDo",
		filters={"reference_type": "Task", "reference_name": ["in", tasks], "status": ["!=", "Cancelled"]},
		fields=["reference_name", "allocated_to", "status"],
		order_by="creation asc",
	):
		liste = ergebnis.setdefault(z.reference_name, [])
		if z.allocated_to and z.allocated_to not in [e["user"] for e in liste]:
			liste.append({"user": z.allocated_to, "erledigt": z.status == "Closed"})
	return ergebnis


def zaehlen(projekte: list[str]) -> frappe._dict:
	"""Arbeitspakete (nicht storniert), davon zugewiesen und offen ohne Zuweisung (alle Projekte)."""
	if not projekte:
		return frappe._dict(arbeitspakete=0, zugewiesen=0, ohne_zuweisung=0)
	tasks = frappe.get_all(
		"Task",
		filters={"project": ["in", projekte], "status": ["!=", "Cancelled"]},
		fields=["name", "status"],
	)
	je_task = zuweisungen([t.name for t in tasks])
	return frappe._dict(
		arbeitspakete=len(tasks),
		zugewiesen=sum(1 for t in tasks if je_task.get(t.name)),
		ohne_zuweisung=sum(1 for t in tasks if t.status not in TASK_ZU and not je_task.get(t.name)),
	)


def _anzahl(n: int) -> str:
	return _("1 Arbeitspaket") if n == 1 else _("{0} Arbeitspakete").format(n)


def _darf(doctype: str, ptype: str, name: str | None = None) -> bool:
	try:
		return bool(
			frappe.has_permission(doctype, ptype, doc=name) if name else frappe.has_permission(doctype, ptype)
		)
	except (frappe.DoesNotExistError, frappe.PermissionError):
		return False


def zustand_bestimmen(
	projekt: frappe._dict, auftrag: frappe._dict | None, k: frappe._dict, zeiten: int
) -> dict:
	"""Zustand, Text, Farbe und Aktion (ohne Rechte) aus den Kennzahlen - rein, testbar."""
	herkunft = (
		_("Aus Auftrag {0} vom {1}: ").format(
			auftrag.name, format_date(getdate(auftrag.transaction_date), "dd.MM.yyyy")
		)
		if auftrag
		else _("Projekt ohne Auftrag: ")
	)
	n = k.arbeitspakete
	if projekt.status in PROJEKT_ZU:
		text = _("Projekt abgeschlossen.") if projekt.status == "Completed" else _("Projekt storniert.")
		if n:
			text += " " + _("{0}, {1} Zeitbuchung(en). Nachlesen in der Baustellenakte.").format(
				_anzahl(n), zeiten
			)
		return {
			"zustand": ABGESCHLOSSEN,
			"text": text,
			"farbe": "blue",
			"aktion": "baustellenakte" if n else None,
		}
	if not n:
		if auftrag:
			text = herkunft + _(
				"noch keine Arbeitspakete. Nächster Schritt: „Arbeitspakete anlegen“ {0} je Verfahren ein Paket"
				" mit Sollstunden und Termin aus den Auftragspositionen; danach Monteure im Wochenplan zuweisen."
			).format(WEITER)
			aktion = "arbeitspakete_anlegen"
		else:
			text = herkunft + _(
				"noch keine Arbeitspakete. Nächster Schritt: „Arbeitspaket anlegen“; danach Monteure im Wochenplan zuweisen."
			)
			aktion = "arbeitspaket_anlegen"
		return {"zustand": KEINE, "text": text, "farbe": "orange", "aktion": aktion}
	if k.ohne_zuweisung:
		text = herkunft + _(
			"{0}, {1} zugewiesen. Nächster Schritt: Monteure im Wochenplan zuweisen {2} dann sehen sie die"
			" Baustelle in der App unter „Baustellen heute“."
		).format(_anzahl(n), k.zugewiesen, WEITER)
		return {"zustand": NICHT_ZUGEWIESEN, "text": text, "farbe": "blue", "aktion": "wochenplan"}
	text = herkunft + _(
		"{0}, alle zugewiesen, {1} Zeitbuchung(en). Die Monteure sehen die Baustelle in der App unter"
		" „Baustellen heute“ (ab Beginn ihres Pakets). Was auf der Baustelle passiert: Baustellenakte."
	).format(_anzahl(n), zeiten)
	return {"zustand": ZUGEWIESEN, "text": text, "farbe": "green", "aktion": "baustellenakte"}


AKTIONEN = {
	"arbeitspakete_anlegen": "Arbeitspakete anlegen",
	"arbeitspaket_anlegen": "Arbeitspaket anlegen",
	"wochenplan": "Wochenplan öffnen",
	"baustellenakte": "Baustellenakte",
}


def aktion_erlaubt(aktion: str | None, projekt: str) -> bool:
	"""Nur mit den Rechten, die das Ziel selbst prüft (5.1 ``tasks_erzeugen``, 5.2 Wochenplan)."""
	if aktion == "arbeitspakete_anlegen":
		return darf_planen() and _darf("Project", "write", projekt) and _darf("Task", "create")
	if aktion == "arbeitspaket_anlegen":
		return _darf("Task", "create")
	if aktion == "wochenplan":
		return darf_planen()
	return aktion == "baustellenakte"


def _arbeitspakete(projekt: str) -> list[dict]:
	felder = ["name", "subject", "status", "expected_time", "exp_start_date", "exp_end_date"]
	felder += [f for f in ("cv_verfahren", "cv_sollstunden") if frappe.db.has_column("Task", f)]
	tasks = frappe.get_list(
		"Task",
		filters={"project": projekt, "status": ["!=", "Cancelled"]},
		fields=felder,
		order_by="exp_start_date asc, creation asc",
	)
	je_task = zuweisungen([t.name for t in tasks])
	namen: dict[str, str] = {}

	def voller_name(user: str) -> str:
		if user not in namen:
			namen[user] = frappe.utils.get_fullname(user)
		return namen[user]

	return [
		{
			"name": t.name,
			"titel": t.subject,
			"verfahren": t.get("cv_verfahren") or None,
			"sollstunden": flt(t.get("cv_sollstunden") or t.expected_time, 2),
			"start": str(t.exp_start_date) if t.exp_start_date else None,
			"ende": str(t.exp_end_date) if t.exp_end_date else None,
			"status": t.status,
			"zugewiesen": [
				{"user": z["user"], "name": voller_name(z["user"]), "erledigt": z["erledigt"]}
				for z in je_task.get(t.name, [])
			],
		}
		for t in tasks
	]


@frappe.whitelist()
def zustand(project: str) -> dict:
	"""Zustand des Projekts für das Formular: Text, ein Hauptknopf, Arbeitspakete (Leserecht Projekt)."""
	from corevatis_erpnext.prozess.kette import _zeitbuchungen

	frappe.has_permission("Project", "read", project, throw=True)
	projekt = frappe.db.get_value("Project", project, ["name", "status", "sales_order"], as_dict=True)
	auftrag = None
	if projekt.sales_order:
		auftrag = frappe.db.get_value(
			"Sales Order", projekt.sales_order, ["name", "transaction_date", "docstatus"], as_dict=True
		)
	k = zaehlen([project])
	zeiten = _zeitbuchungen([project])
	ergebnis = zustand_bestimmen(projekt, auftrag, k, zeiten)
	aktion = ergebnis.pop("aktion")
	ergebnis.update(
		projekt=project,
		auftrag=auftrag.name if auftrag and _darf("Sales Order", "read", auftrag.name) else None,
		kennzahlen={**k, "zeitbuchungen": zeiten},
		aktion={"key": aktion, "label": _(AKTIONEN[aktion])}
		if aktion and aktion_erlaubt(aktion, project)
		else None,
		darf_planen=darf_planen(),
		arbeitspakete=_arbeitspakete(project),
	)
	return ergebnis
