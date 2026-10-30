"""Mangel → Task (Aufgabe 6.1, Abnahme „Mangel-Task mit Frist“).

Je Mangelzeile eines abgeschlossenen Abnahmeprotokolls ein Task am Projekt:

| Feld | Wert |
|---|---|
| ``subject`` | „Mangel: <erste Zeile der Beschreibung>“ (gekürzt) |
| ``project``, ``status``, ``priority`` | Projekt des Protokolls, Open, High |
| ``exp_end_date`` | Frist der Mangelzeile (Fälligkeit); liegt sie nach dem geplanten Projektende, wird ``Project.expected_end_date`` verschoben (sonst lehnt ERPNext den Task ab) |
| ``description`` | Beschreibung, Raum/Bauteil, Frist, Verweis auf Protokoll und Mangel |
| ``cv_abnahmeprotokoll``, ``cv_mangel_zeile`` | Herkunft; Idempotenz |

**Idempotenz:** gibt es zu Protokoll + Zeile schon einen (nicht stornierten) Task, wird er nur in
der Zeile eingetragen. Parallele Aufrufe: Zeilensperre auf dem Protokoll. Behobene Zeilen bekommen
keinen Task. Der Hook legt mit ``ignore_permissions`` an (kontrollierter Weg wie 5.1), der Button
``maengel_tasks_erzeugen`` mit den Rechten des Benutzers.

Rückmeldung: ``task_aktualisiert`` (``Task.on_update``) setzt die Mangelzeile beim Erledigen auf
„Behoben“ und beim Wiederöffnen auf „Offen“; ``behobene_tasks_erledigen`` erledigt den Task, wenn
Meister/Büro die Zeile im Protokoll auf „Behoben“ setzen.
"""

import frappe
from frappe import _
from frappe.utils import cstr, escape_html, get_url_to_form, getdate, now_datetime

from corevatis_erpnext.abnahme import konstanten as K

TITEL_MAX = 140
TASK_OFFEN = ("Open", "Working", "Pending Review", "Overdue")


def _titel(beschreibung: str) -> str:
	erste = (
		cstr(beschreibung).strip().splitlines()[0] if cstr(beschreibung).strip() else _("ohne Beschreibung")
	)
	titel = f"Mangel: {erste}"
	return titel if len(titel) <= TITEL_MAX else titel[: TITEL_MAX - 1] + "…"


def _beschreibung(doc, zeile) -> str:
	teile = [f"<p>{escape_html(cstr(zeile.get('beschreibung')))}</p>"]
	ort = " / ".join(cstr(zeile.get(f)).strip() for f in ("raum", "bauteil") if cstr(zeile.get(f)).strip())
	if ort:
		teile.append(f"<p><b>Raum/Bauteil:</b> {escape_html(ort)}</p>")
	if zeile.get("frist"):
		teile.append(f"<p><b>Frist:</b> {escape_html(frappe.utils.formatdate(zeile.frist))}</p>")
	link = get_url_to_form(K.ABNAHMEPROTOKOLL, doc.name)
	teile.append(
		f'<p>Aus dem Abnahmeprotokoll <a href="{escape_html(link)}">{escape_html(doc.name)}</a>, '
		f"Mangel {zeile.get('idx')}" + (" (Foto im Protokoll)" if zeile.get("foto") else "") + "</p>"
	)
	return "".join(teile)


def _vorhandener_task(protokoll: str, zeile: str) -> str | None:
	return frappe.db.get_value(
		"Task",
		{"cv_abnahmeprotokoll": protokoll, "cv_mangel_zeile": zeile, "status": ["!=", "Cancelled"]},
		"name",
		order_by="creation asc",
	)


def _projektende_verschieben(projekt: str, frist) -> None:
	ende = frappe.db.get_value("Project", projekt, "expected_end_date")
	if ende and getdate(frist) > getdate(ende):
		frappe.db.set_value("Project", projekt, "expected_end_date", getdate(frist))
		frappe.get_doc("Project", projekt).add_comment(
			"Info",
			_("Geplantes Projektende auf {0} verschoben (Frist eines Mangels aus der Abnahme).").format(
				frappe.utils.formatdate(frist)
			),
		)


def maengel_tasks_anlegen(doc, ignore_permissions: bool = True) -> list[dict]:
	"""Tasks zu allen offenen Mangelzeilen ohne Task. Rückgabe: ``[{zeile, task, neu}]``."""
	frappe.db.sql(f"select name from `tab{K.ABNAHMEPROTOKOLL}` where name=%s for update", doc.name)
	ergebnis = []
	for zeile in doc.get("maengel") or []:
		if zeile.get("status") == K.MANGEL_BEHOBEN:
			continue
		task = _vorhandener_task(doc.name, zeile.name)
		neu = not task
		if neu:
			if zeile.get("frist"):
				_projektende_verschieben(doc.projekt, zeile.frist)
			t = frappe.get_doc(
				{
					"doctype": "Task",
					"subject": _titel(zeile.get("beschreibung")),
					"project": doc.projekt,
					"status": "Open",
					"priority": "High",
					"exp_end_date": getdate(zeile.frist) if zeile.get("frist") else None,
					"description": _beschreibung(doc, zeile),
					"cv_abnahmeprotokoll": doc.name,
					"cv_mangel_zeile": zeile.name,
				}
			)
			t.insert(ignore_permissions=ignore_permissions)
			task = t.name
		if zeile.get("task") != task:
			frappe.db.set_value(K.ABNAHME_MANGEL, zeile.name, "task", task, update_modified=False)
			zeile.task = task
		ergebnis.append({"zeile": zeile.name, "idx": zeile.get("idx"), "task": task, "neu": neu})
	return ergebnis


def behobene_tasks_erledigen(doc) -> None:
	"""Mangelzeile im Protokoll auf „Behoben“ gesetzt: offenen Task erledigen."""
	for zeile in doc.get("maengel") or []:
		if zeile.get("status") != K.MANGEL_BEHOBEN or not zeile.get("task"):
			continue
		if frappe.db.get_value("Task", zeile.task, "status") in TASK_OFFEN:
			task = frappe.get_doc("Task", zeile.task)
			task.status = "Completed"
			task.flags.cv_aus_abnahme = True
			task.save(ignore_permissions=True)


def _mangel_status(protokoll: str, zeile: str, behoben: bool, quelle: str) -> None:
	if K.CORE_APP in frappe.get_installed_apps():
		frappe.get_attr(K.KERN_MANGEL_STATUS)(protokoll, zeile, behoben, quelle=quelle)
		return
	# ohne Kern-Funktion (Stellvertreter-Doctype in den Tests dieses Repos): Zeile direkt
	frappe.db.set_value(
		K.ABNAHME_MANGEL,
		{"name": zeile, "parent": protokoll},
		{
			"status": K.MANGEL_BEHOBEN if behoben else K.MANGEL_OFFEN,
			"behoben_am": now_datetime() if behoben else None,
		},
		update_modified=False,
	)


def task_aktualisiert(doc, method=None) -> None:
	"""``Task.on_update``: Status des Mangel-Tasks → Mangelzeile im Protokoll."""
	protokoll, zeile = doc.get("cv_abnahmeprotokoll"), doc.get("cv_mangel_zeile")
	if not protokoll or not zeile or doc.flags.cv_aus_abnahme:
		return
	vorher = doc.get_doc_before_save()
	alt = vorher.status if vorher is not None else None
	if doc.status == alt:
		return
	if doc.status == "Completed":
		behoben = True
	elif alt == "Completed" and doc.status in TASK_OFFEN:
		behoben = False
	else:
		return
	if not frappe.db.exists("DocType", K.ABNAHMEPROTOKOLL):
		return
	frappe.db.savepoint("cv_abnahme_task")
	try:
		_mangel_status(protokoll, zeile, behoben, doc.name)
	except Exception:
		frappe.db.rollback(save_point="cv_abnahme_task")
		frappe.log_error(
			title=f"Abnahme 6.1: Mangelstatus aus {doc.name}",
			reference_doctype="Task",
			reference_name=doc.name,
		)


@frappe.whitelist()
def maengel_tasks_erzeugen(protokoll: str) -> dict:
	"""Button „Mangel-Aufgaben erzeugen“ am abgeschlossenen Protokoll (Büro/Meister): legt fehlende
	Tasks mit den Rechten des Benutzers an (Schreibrecht Protokoll, Anlegerecht Task)."""
	if not set(frappe.get_roles()) & set(K.ROLLEN_ABNAHME):
		frappe.throw(_("Mangel-Aufgaben legen nur Büro oder Meister an."), frappe.PermissionError)
	if not frappe.db.exists(K.ABNAHMEPROTOKOLL, protokoll):
		frappe.throw(_("Abnahmeprotokoll {0} nicht gefunden.").format(protokoll), frappe.DoesNotExistError)
	frappe.has_permission(K.ABNAHMEPROTOKOLL, "write", doc=protokoll, throw=True)
	frappe.has_permission("Task", "create", throw=True)
	doc = frappe.get_doc(K.ABNAHMEPROTOKOLL, protokoll)
	if doc.status != K.STATUS_ABGESCHLOSSEN:
		frappe.throw(_("Mangel-Aufgaben entstehen erst mit dem Abschluss des Protokolls."))
	if not doc.projekt or not frappe.db.exists("Project", doc.projekt):
		frappe.throw(_("Projekt {0} nicht gefunden.").format(doc.projekt))
	frappe.has_permission("Project", "read", doc=doc.projekt, throw=True)
	return {"protokoll": doc.name, "tasks": maengel_tasks_anlegen(doc, ignore_permissions=False)}
