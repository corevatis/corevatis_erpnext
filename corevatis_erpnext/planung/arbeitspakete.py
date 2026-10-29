"""Aufgabe 5.1: Arbeitspakete (Task) je Verfahren aus dem Auftrag, Sollstunden aus der Stückliste.

Je Verfahren (``Sales Order Item.cv_verfahren``, sonst ``Item.cv_verfahren``, sonst „Sonstiges“)
entsteht **ein** Task am Projekt des Auftrags (Entscheidung: keine Unter-Tasks je Position - die
Monteur-App zeigt ein Arbeitspaket je Verfahren, die Positionen stehen in der Beschreibung und
in ``cv_positionen``; Zeiterfassung und Fortschritt laufen je Arbeitspaket):

- Titel „<Verfahren> <Gedankenstrich> <Räume, Bauteile>“ (ohne Raum/Bauteil: die Artikelnummern),
- **Sollstunden** = Σ Montageminuten je Einheit mal Menge ÷ 60 (``expected_time``, ``cv_sollstunden``),
- Reihenfolge und Abhängigkeit (``depends_on``) nach dem Bauablauf (``planung.bauablauf``),
- ``exp_start_date``/``exp_end_date`` fortlaufend ab Projektstart (sonst Auftragsdatum):
  Dauer = Sollstunden ÷ Teamgröße, Arbeitstag = Tagesarbeitszeit ab Arbeitsbeginn, Mo-Fr ohne
  Feiertage der Firma. Reicht das geplante Projektende nicht, wird es verschoben (Hinweis).
- Zuweisung bleibt leer (Wochenplan 5.2).

Idempotent: Gibt es zum Auftrag schon Arbeitspakete (``Task.cv_auftrag``, nicht storniert),
legt ein weiterer Aufruf nichts an und meldet sie.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_datetime, getdate

from corevatis_erpnext.planung import bauablauf
from corevatis_erpnext.planung.auftrag import auftrag_laden, pruefe_rolle, pruefe_schreibrechte
from corevatis_erpnext.planung.einstellungen import einstellungen
from corevatis_erpnext.planung.positionen import positionen

TRENNER = " \u2013 "  # Gedankenstrich (ruff RUF001)
TITEL_LAENGE = 140


def bestehende_arbeitspakete(auftrag: str) -> list[str]:
	"""Arbeitspakete zum Auftrag (Locking Read: zuletzt bestätigter Stand, A-1)."""
	zeilen = frappe.db.sql(
		"""select name from `tabTask` where cv_auftrag=%s and status != 'Cancelled'
		order by exp_start_date asc, creation asc for update""",
		auftrag,
	)
	return [z[0] for z in zeilen]


def _kurz(text: str, laenge: int = TITEL_LAENGE) -> str:
	return text if len(text) <= laenge else text[: laenge - 1] + "…"


def _liste(werte) -> list[str]:
	ergebnis = []
	for wert in werte:
		for teil in (wert or "").split(","):
			teil = teil.strip()
			if teil and teil not in ergebnis:
				ergebnis.append(teil)
	return ergebnis


def pakete_bilden(liste: list[dict]) -> list[dict]:
	"""Positionen je Verfahren, Verfahren und Positionen in Bauablauf-Reihenfolge."""
	erste: dict[str, int] = {}
	je_verfahren: dict[str, list[dict]] = {}
	for i, p in enumerate(liste):
		erste.setdefault(p["verfahren"], i)
		je_verfahren.setdefault(p["verfahren"], []).append(p)
	pakete = []
	for verfahren in sorted(je_verfahren, key=lambda v: bauablauf.verfahren_rang(v, erste)):
		zeilen = sorted(je_verfahren[verfahren], key=lambda p: (p["phasen_rang"], p["pos"]))
		orte = _liste([p["raum"] for p in zeilen] + [p["bauteil"] for p in zeilen])
		zusatz = ", ".join(orte) if orte else ", ".join(dict.fromkeys(p["item_code"] for p in zeilen))
		sollstunden = flt(sum(p["sollminuten"] for p in zeilen) / 60, 2)
		pakete.append(
			{
				"verfahren": verfahren,
				"titel": _kurz(bauablauf.titel(verfahren) + TRENNER + zusatz),
				"sollstunden": sollstunden,
				"positionen": zeilen,
			}
		)
	return pakete


def _zahl(wert, stellen: int = 2) -> str:
	return frappe.format(flt(wert, stellen), {"fieldtype": "Float", "precision": stellen})


def beschreibung(paket: dict, auftrag: str, teamgroesse: int) -> str:
	esc = frappe.utils.escape_html
	zeilen = []
	for p in paket["positionen"]:
		ort = ", ".join(x for x in (p["raum"], p["bauteil"]) if x)
		zeilen.append(
			"<tr>"
			f"<td>{p['pos']}</td><td>{esc(p['phase'] or '')}</td>"
			f"<td>{esc(p['item_code'])} {esc(p['item_name'] or '')}</td>"
			f"<td style='text-align:right'>{_zahl(p['menge'])} {esc(p['einheit'] or '')}</td>"
			f"<td>{esc(ort)}</td>"
			f"<td style='text-align:right'>{_zahl(p['minuten_je_einheit'])}</td>"
			f"<td style='text-align:right'>{_zahl(p['sollminuten'] / 60)}</td>"
			"</tr>"
		)
	kopf = _(
		"Arbeitspaket aus Auftrag {0}: Sollstunden {1} h, Team {2} → Dauer {3} h. Positionen in Bauablauf-Reihenfolge:"
	).format(
		esc(auftrag),
		_zahl(paket["sollstunden"]),
		teamgroesse,
		_zahl(paket["sollstunden"] / teamgroesse),
	)
	return (
		f"<p>{kopf}</p><table class='table table-bordered'><thead><tr>"
		f"<th>{_('Pos.')}</th><th>{_('Phase')}</th><th>{_('Leistung')}</th><th>{_('Menge')}</th>"
		f"<th>{_('Raum/Bauteil')}</th><th>{_('min/Einheit')}</th><th>{_('Sollstunden')}</th>"
		"</tr></thead><tbody>" + "".join(zeilen) + "</tbody></table>"
	)


def positionen_json(paket: dict) -> str:
	felder = (
		"pos",
		"sales_order_item",
		"item_code",
		"item_name",
		"menge",
		"einheit",
		"phase",
		"raum",
		"bauteil",
		"aufmass_position",
		"bom",
		"minuten_je_einheit",
	)
	return json.dumps(
		[
			{**{f: p.get(f) for f in felder}, "sollstunden": flt(p["sollminuten"] / 60, 4)}
			for p in paket["positionen"]
		],
		ensure_ascii=False,
		indent=1,
	)


def freie_tage(firma: str, start) -> set:
	"""Feiertage der Firma (``Company.default_holiday_list``) ab Start, ein Jahr voraus."""
	liste = frappe.db.get_value("Company", firma, "default_holiday_list")
	if not liste:
		return set()
	return {
		getdate(d)
		for d in frappe.get_all(
			"Holiday",
			filters={
				"parent": liste,
				"holiday_date": ["between", [getdate(start), getdate(add_days(start, 366))]],
			},
			pluck="holiday_date",
		)
	}


def arbeitspakete_anlegen(auftrag, projekt: str, e=None, ignore_permissions: bool = True) -> dict:
	"""Tasks je Verfahren anlegen (ohne Rollen- und Sperrprüfung - Aufrufer: Hook, Whitelist).

	``ignore_permissions`` nur im Submit-Hook; die Whitelist legt mit den Rechten des Benutzers an.
	"""
	e = e or einstellungen()
	hinweise: list[str] = []
	vorhanden = bestehende_arbeitspakete(auftrag.name)
	if vorhanden:
		hinweise.append(
			_("Zum Auftrag {0} gibt es bereits {1} Arbeitspakete - nichts angelegt.").format(
				auftrag.name, len(vorhanden)
			)
		)
		return _ergebnis(auftrag, projekt, vorhanden, False, hinweise)

	liste, hinweise_pos = positionen(auftrag)
	hinweise += hinweise_pos
	pakete = pakete_bilden(liste)
	if not pakete:
		hinweise.append(_("Auftrag {0} hat keine Positionen - keine Arbeitspakete.").format(auftrag.name))
		return _ergebnis(auftrag, projekt, [], False, hinweise)

	start_projekt, ende_projekt = frappe.db.get_value(
		"Project", projekt, ["expected_start_date", "expected_end_date"]
	)
	start = getdate(start_projekt or auftrag.transaction_date)
	plan = bauablauf.zeitplan(
		start,
		[p["sollstunden"] / e.teamgroesse for p in pakete],
		tagesstunden=e.tagesarbeitszeit,
		arbeitsbeginn=e.arbeitsbeginn,
		freie_tage=freie_tage(auftrag.company, start),
	)
	letzter_tag = getdate(plan[-1][1])
	werte = {}
	if not start_projekt:
		werte["expected_start_date"] = start
	if not ende_projekt or getdate(ende_projekt) < letzter_tag:
		werte["expected_end_date"] = letzter_tag
		if ende_projekt:
			hinweise.append(
				_("Geplantes Projektende von {0} auf {1} verschoben (Sollstunden der Arbeitspakete).").format(
					frappe.format(ende_projekt, {"fieldtype": "Date"}),
					frappe.format(letzter_tag, {"fieldtype": "Date"}),
				)
			)
	if werte:
		# Task.validate_parent_project_dates: Termine nur innerhalb des Projektzeitraums
		frappe.db.set_value("Project", projekt, werte)

	namen = []
	vorher = None
	for paket, (von, bis) in zip(pakete, plan, strict=True):
		task = frappe.get_doc(
			{
				"doctype": "Task",
				"subject": paket["titel"],
				"project": projekt,
				"company": auftrag.company,
				"status": "Open",
				"priority": "Medium",
				"exp_start_date": get_datetime(von),
				"exp_end_date": get_datetime(bis),
				"expected_time": paket["sollstunden"],
				"task_weight": paket["sollstunden"],
				"description": beschreibung(paket, auftrag.name, e.teamgroesse),
				"cv_auftrag": auftrag.name,
				"cv_verfahren": paket["verfahren"],
				"cv_sollstunden": paket["sollstunden"],
				"cv_positionen": positionen_json(paket),
				"depends_on": [{"task": vorher}] if vorher else [],
			}
		)
		task.flags.ignore_permissions = ignore_permissions
		task.insert()
		namen.append(task.name)
		vorher = task.name
	return _ergebnis(auftrag, projekt, namen, True, hinweise)


def _ergebnis(auftrag, projekt, namen: list[str], neu: bool, hinweise: list[str]) -> dict:
	tasks = (
		frappe.get_all(
			"Task",
			filters={"name": ["in", namen]},
			fields=["name", "subject", "cv_verfahren", "cv_sollstunden", "exp_start_date", "exp_end_date"],
			order_by="exp_start_date asc, creation asc",
		)
		if namen
		else []
	)
	return {
		"auftrag": auftrag.name if auftrag else None,
		"projekt": projekt,
		"neu": neu,
		"arbeitspakete": [
			{
				"name": t.name,
				"titel": t.subject,
				"verfahren": t.cv_verfahren,
				"sollstunden": flt(t.cv_sollstunden),
				"start": str(t.exp_start_date) if t.exp_start_date else None,
				"ende": str(t.exp_end_date) if t.exp_end_date else None,
			}
			for t in tasks
		],
		"sollstunden": flt(sum(flt(t.cv_sollstunden) for t in tasks), 2),
		"hinweise": hinweise,
	}


@frappe.whitelist(methods=["POST"])
def tasks_erzeugen(sales_order: str | None = None, project: str | None = None) -> dict:
	"""Arbeitspakete (Tasks je Verfahren) für einen eingereichten Auftrag mit Projekt.

	Für Büro/Meister (Button „Arbeitspakete erzeugen“ an Auftrag und Projekt). Ohne Projekt kein
	Fehler, nur ein Hinweis. Idempotent.
	"""
	pruefe_rolle()
	hinweise: list[str] = []
	auftrag, projekt = auftrag_laden(sales_order, project, hinweise)
	if not auftrag or not projekt:
		return _ergebnis(auftrag, projekt, [], False, hinweise)
	pruefe_schreibrechte(projekt, "Task")
	ergebnis = arbeitspakete_anlegen(auftrag, projekt, ignore_permissions=False)
	ergebnis["hinweise"] = hinweise + ergebnis["hinweise"]
	return ergebnis
