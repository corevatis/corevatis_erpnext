"""Aufgabe 5.2: Wochenplan - Arbeitspakete (Task) x Mitarbeiter, zwei Wochen.

Zeilen sind die planbaren Mitarbeiter (``zuweisung.mitarbeiter_liste``: Employee mit aktivem
Benutzer, Rolle Monteur oder Meister) mit ihren Arbeitspaketen als Unterzeilen, dazu die Zeile
„Nicht zugewiesen“. Spalten sind die Arbeitstage (Mo-Fr) der gewählten und der folgenden Woche.

**Stunden je Tag:** Die Sollstunden der Task (``expected_time``, sonst ``cv_sollstunden``) werden
auf die Arbeitszeit zwischen ``exp_start_date`` und ``exp_end_date`` verteilt - anteilig nach der
Überschneidung mit dem Arbeitstag (Arbeitsbeginn + Tagesarbeitszeit aus „Corevatis Planung
Einstellungen“, Mo-Fr ohne Feiertage der Firma), also genau umgekehrt wie der Zeitplan aus 5.1.
Ohne Überschneidung (z. B. Termine nur als Datum) gleichmäßig auf die Arbeitstage. Sind einer Task
mehrere Mitarbeiter zugewiesen, trägt jeder seinen Anteil (Stunden ÷ Anzahl).

**Tageslast** je Mitarbeiter = Summe seiner Anteile über **alle** Projekte (auch mit Projektfilter);
über der Tagesarbeitszeit (8 h) ist der Tag markiert (``ueberlast``).

Zuweisung = ToDo auf die Task (``planung.zuweisung``), offene und erledigte zählen.
"""

from __future__ import annotations

import datetime as dt

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_datetime, getdate, to_timedelta, today

from corevatis_erpnext.planung.einstellungen import einstellungen
from corevatis_erpnext.planung.zuweisung import TODO_ZUGEWIESEN, mitarbeiter_liste, pruefe_planer

WOCHEN = 2
NICHT_ZUGEWIESEN = "-"
WOCHENTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def wochenbeginn(tag=None) -> dt.date:
	tag = getdate(tag or today())
	return tag - dt.timedelta(days=tag.weekday())


def spalte(tag: dt.date) -> str:
	return "d_" + tag.strftime("%Y%m%d")


def arbeitstage(von: dt.date, bis: dt.date, freie: set) -> list[dt.date]:
	tage = []
	tag = von
	while tag <= bis:
		if tag.weekday() < 5 and tag not in freie:
			tage.append(tag)
		tag += dt.timedelta(days=1)
	return tage


def stunden_je_tag(
	start, ende, stunden: float, arbeitsbeginn: str, tagesstunden: float, freie: set
) -> dict[dt.date, float]:
	"""Stunden einer Task je Arbeitstag, anteilig nach der Überschneidung mit dem Arbeitstag.

	Ohne Sollstunden erscheint die Task mit 0 h an ihren Tagen (sichtbar, ohne Last)."""
	stunden = max(flt(stunden), 0.0)
	if not start:
		return {}
	start = get_datetime(start)
	ende = get_datetime(ende) if ende else start
	if ende.time() == dt.time.min:
		# Ende nur als Datum (00:00): der ganze Tag gehört noch dazu
		ende += dt.timedelta(days=1)
	ende = max(ende, start)
	tage = arbeitstage(
		start.date(), (ende - dt.timedelta(microseconds=1)).date() if ende > start else start.date(), freie
	)
	beginn = to_timedelta(arbeitsbeginn)
	ueberschneidung = {}
	for tag in tage:
		von = dt.datetime.combine(tag, dt.time.min) + beginn
		bis = von + dt.timedelta(hours=tagesstunden)
		dauer = (min(bis, ende) - max(von, start)).total_seconds()
		if dauer > 0:
			ueberschneidung[tag] = dauer
	gesamt = sum(ueberschneidung.values())
	if gesamt > 0:
		return {tag: stunden * dauer / gesamt for tag, dauer in ueberschneidung.items()}
	if tage:
		return {tag: stunden / len(tage) for tag in tage}
	return {start.date(): stunden}


def _firma() -> str | None:
	return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)


def freie_tage(von: dt.date) -> set:
	from corevatis_erpnext.planung.arbeitspakete import freie_tage as feiertage

	firma = _firma()
	# ein Jahr zurück reicht für Tasks, die vor dem Zeitraum begonnen haben
	return feiertage(firma, add_days(von, -366)) if firma else set()


def _tasks(von: dt.date, bis: dt.date) -> list[frappe._dict]:
	"""Tasks mit Termin, die den Zeitraum berühren (Rechte des Benutzers)."""
	ende_zeitraum = dt.datetime.combine(bis, dt.time.max)
	kandidaten = frappe.get_list(
		"Task",
		filters={
			"status": ["not in", ["Cancelled", "Template"]],
			"is_template": 0,
			"exp_start_date": [
				"between",
				[dt.datetime.combine(add_days(von, -366), dt.time.min), ende_zeitraum],
			],
		},
		fields=[
			"name",
			"subject",
			"project",
			"status",
			"exp_start_date",
			"exp_end_date",
			"expected_time",
			"cv_sollstunden",
			"cv_verfahren",
		],
		order_by="exp_start_date asc, name asc",
		limit_page_length=0,
	)
	beginn = dt.datetime.combine(von, dt.time.min)
	return [t for t in kandidaten if get_datetime(t.exp_end_date or t.exp_start_date) >= beginn]


def _zuweisungen(tasks: list[str]) -> dict[str, list[str]]:
	if not tasks:
		return {}
	ergebnis: dict[str, list[str]] = {}
	for todo in frappe.get_all(
		"ToDo",
		filters={
			"reference_type": "Task",
			"reference_name": ["in", tasks],
			"status": ["in", TODO_ZUGEWIESEN],
		},
		fields=["reference_name", "allocated_to"],
		order_by="creation asc",
	):
		liste = ergebnis.setdefault(todo.reference_name, [])
		if todo.allocated_to not in liste:
			liste.append(todo.allocated_to)
	return ergebnis


def plan(von=None, projekt: str | None = None, mitarbeiter: str | None = None) -> dict:
	"""Wochenplan ab dem Montag der Woche von ``von`` über zwei Wochen."""
	e = einstellungen()
	von = wochenbeginn(von)
	bis = von + dt.timedelta(days=7 * WOCHEN - 1)
	freie = freie_tage(von)
	tage = [
		von + dt.timedelta(days=i) for i in range(7 * WOCHEN) if (von + dt.timedelta(days=i)).weekday() < 5
	]
	im_zeitraum = set(tage)

	tasks = _tasks(von, bis)
	zuweisung = _zuweisungen([t.name for t in tasks])
	projekte = (
		{
			p.name: p.project_name
			for p in frappe.get_all(
				"Project",
				filters={"name": ["in", list({t.project for t in tasks if t.project})]},
				fields=["name", "project_name"],
			)
		}
		if tasks
		else {}
	)

	leute = mitarbeiter_liste()
	nach_user = {m["user"]: m for m in leute}
	# Zuweisungen an Benutzer ohne planbaren Employee (z. B. Büro, deaktiviert) nicht verschweigen
	for users in zuweisung.values():
		for user in users:
			if user not in nach_user:
				eintrag = {
					"employee": frappe.db.get_value("Employee", {"user_id": user}, "name"),
					"employee_name": frappe.utils.get_fullname(user),
					"user": user,
					"nicht_planbar": True,
				}
				leute.append(eintrag)
				nach_user[user] = eintrag

	pakete = []
	for t in tasks:
		stunden = flt(t.expected_time) or flt(t.cv_sollstunden)
		verteilung = stunden_je_tag(
			t.exp_start_date, t.exp_end_date, stunden, e.arbeitsbeginn, e.tagesarbeitszeit, freie
		)
		verteilung = {tag: h for tag, h in verteilung.items() if tag in im_zeitraum}
		# ohne Termin oder außerhalb des Zeitraums: nicht im Plan
		if not verteilung:
			continue
		pakete.append(
			{
				"task": t.name,
				"titel": t.subject,
				"projekt": t.project,
				"projekt_name": projekte.get(t.project) or t.project or "",
				"verfahren": t.cv_verfahren or "",
				"status": t.status,
				"start": str(t.exp_start_date) if t.exp_start_date else None,
				"ende": str(t.exp_end_date) if t.exp_end_date else None,
				"sollstunden": flt(stunden, 2),
				"zugewiesen": zuweisung.get(t.name, []),
				"tage": verteilung,
			}
		)

	zeilen = []
	ueberlast_gesamt = 0
	grenze = flt(e.tagesarbeitszeit)
	for m in sorted(leute, key=lambda m: (m.get("nicht_planbar", False), (m["employee_name"] or "").lower())):
		if mitarbeiter and m.get("employee") != mitarbeiter:
			continue
		eigene = [p for p in pakete if m["user"] in p["zugewiesen"]]
		last = dict.fromkeys(tage, 0.0)
		kinder = []
		for p in eigene:
			anteil = {tag: h / len(p["zugewiesen"]) for tag, h in p["tage"].items()}
			for tag, h in anteil.items():
				last[tag] += h
			if projekt and p["projekt"] != projekt:
				continue
			kinder.append(_task_zeile(p, anteil, "M:" + m["user"], m))
		ueberlast = [spalte(tag) for tag in tage if flt(last[tag], 2) > grenze]
		ueberlast_gesamt += len(ueberlast)
		eltern = {
			"zeile": "M:" + m["user"],
			"eltern": None,
			"indent": 0,
			"typ": "mitarbeiter",
			"bezeichnung": m["employee_name"]
			+ (" (" + _("nicht planbar") + ")" if m.get("nicht_planbar") else ""),
			"mitarbeiter": m.get("employee"),
			"user": m["user"],
			"summe": flt(sum(last.values()), 2),
			"ueberlast": ueberlast,
			"anzahl": len(kinder),
		}
		for tag in tage:
			eltern[spalte(tag)] = flt(last[tag], 2)
		zeilen.append(eltern)
		zeilen += kinder

	offen = [p for p in pakete if not p["zugewiesen"] and (not projekt or p["projekt"] == projekt)]
	if not mitarbeiter:
		summe = dict.fromkeys(tage, 0.0)
		kinder = []
		for p in offen:
			for tag, h in p["tage"].items():
				summe[tag] += h
			kinder.append(_task_zeile(p, p["tage"], "M:" + NICHT_ZUGEWIESEN, None))
		eltern = {
			"zeile": "M:" + NICHT_ZUGEWIESEN,
			"eltern": None,
			"indent": 0,
			"typ": "nicht_zugewiesen",
			"bezeichnung": _("Nicht zugewiesen"),
			"summe": flt(sum(summe.values()), 2),
			"ueberlast": [],
			"anzahl": len(kinder),
		}
		for tag in tage:
			eltern[spalte(tag)] = flt(summe[tag], 2)
		zeilen.append(eltern)
		zeilen += kinder

	sichtbar = [p for p in pakete if not projekt or p["projekt"] == projekt]
	return {
		"von": str(von),
		"bis": str(bis),
		"tage": [{"datum": str(tag), "spalte": spalte(tag), "frei": tag in freie} for tag in tage],
		"tagesarbeitszeit": grenze,
		"zeilen": zeilen,
		"pakete": len(sichtbar),
		"nicht_zugewiesen": len(offen),
		"mitarbeiter": sum(1 for z in zeilen if z["typ"] == "mitarbeiter"),
		"ueberlast_tage": ueberlast_gesamt,
	}


def _task_zeile(p: dict, anteil: dict, eltern: str, m: dict | None) -> dict:
	zeile = {
		"zeile": f"{eltern}:{p['task']}",
		"eltern": eltern,
		"indent": 1,
		"typ": "task",
		"bezeichnung": " · ".join(x for x in (p["projekt_name"], p["verfahren"] or p["titel"]) if x),
		"task": p["task"],
		"titel": p["titel"],
		"projekt": p["projekt"],
		"verfahren": p["verfahren"],
		"status": p["status"],
		"start": p["start"],
		"ende": p["ende"],
		"sollstunden": p["sollstunden"],
		"anteil": len(p["zugewiesen"]) or 1,
		"mitarbeiter": m.get("employee") if m else None,
		"zugewiesen": ", ".join(frappe.utils.get_fullname(u) for u in p["zugewiesen"]),
		"summe": flt(sum(anteil.values()), 2),
		"ueberlast": [],
	}
	for tag, h in anteil.items():
		zeile[spalte(tag)] = flt(h, 2)
	return zeile


def ueberlast_tage(user: str, von: dt.date, bis: dt.date) -> list[tuple[dt.date, float, float]]:
	"""Tage im Zeitraum, an denen die Tageslast des Benutzers über der Tagesarbeitszeit liegt
	(für den Hinweis bei der Zuweisung). Rechnet je zwei Wochen über ``plan``."""
	ergebnis = []
	beginn = wochenbeginn(von)
	while beginn <= bis:
		p = plan(beginn)
		for z in p["zeilen"]:
			if z["typ"] != "mitarbeiter" or z["user"] != user:
				continue
			for t in p["tage"]:
				tag = getdate(t["datum"])
				if von <= tag <= bis and t["spalte"] in z["ueberlast"]:
					ergebnis.append((tag, z[t["spalte"]], p["tagesarbeitszeit"]))
		beginn += dt.timedelta(days=7 * WOCHEN)
	return ergebnis


@frappe.whitelist()
def wochenplan(von: str | None = None, projekt: str | None = None, mitarbeiter: str | None = None) -> dict:
	"""Wochenplan als JSON (Script Report „Wochenplan“, REST). Nur Büro, Meister, System Manager."""
	pruefe_planer()
	return plan(von, projekt or None, mitarbeiter or None)
