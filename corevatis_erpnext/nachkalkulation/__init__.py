"""Nachkalkulation (Ausbauplan Phase 2, P2.1): Soll aus der Kalkulation des Auftrags gegen Ist aus
Zeiten und Material, je Projekt und je Verfahren (= Arbeitspaket, 5.1).

Soll je Auftragsposition (``planung.positionen``: Verfahren, Menge, Stückliste):

- **Sollstunden** = Minuten je Einheit x Auftragsmenge ÷ 60. Minuten je Einheit zuerst aus der
  Kalkulation des angenommenen Angebots (``Quotation.cv_kalkulation_json``, Position mit demselben
  Artikel in Reihenfolge), sonst aus der Stückliste (wie die Arbeitspakete, ``Task.cv_sollstunden``).
- **Sollmaterial** (Kosten) = Material je Einheit aus der Kalkulation (Einkaufspreis der Stückliste,
  ohne Zuschlag) x Auftragsmenge; ohne Kalkulation Stücklistenmengen x Bewertungspreis des Materials.
- **Erlös** = Nettobetrag der Auftragspositionen des Verfahrens; nach freigegebener Schlussrechnung
  (7.2) die Nettobeträge ihrer Positionen (Ist-Mengen, Mehrmengen, Nachträge; Abzüge der Abschläge
  zählen nicht).
- **Stundensatz** aus der Kalkulation (``schema.stundensatz``), sonst aus dem Kalkulationsschema des
  Angebots (Kern-Doctype nur per Name gelesen), sonst 0 mit Hinweis. Lohnkosten Soll und Ist rechnen
  mit demselben Satz (Annahme: kein Kostensatz je Mitarbeiter).

Ist je Verfahren (Zuordnung über das Arbeitspaket ``Task.cv_verfahren``; Buchungen ohne Arbeitspaket
sammeln sich unter „Ohne Arbeitspaket“):

- **Ist-Stunden** = Arbeitszeit + Fahrzeit der Timesheet-Zeilen des Projekts (Entwurf und eingereicht,
  Pause zählt nicht, 3.9); Fahrzeit auch getrennt.
- **Ist-Material** = Materialentnahmen (Stock Entry „Material Issue“, eingereicht, 3.11) des Projekts:
  Menge und Bewertung (``basic_amount``).

Ampel je Verfahren: erwartete Stunden = Sollstunden x Fortschritt (Arbeitspaket abgeschlossen oder
Fortschritt unbekannt → 100 %); Ist ÷ erwartet ≤ 1,00 grün, ≤ 1,15 gelb, darüber rot; Ist ohne Soll
gelb; ohne Ist grau. Marge = Erlös - Lohnkosten - Materialkosten (Soll und Ist).

Lernschleife (``regeln``): über freigegebene Schlussrechnungen je Artikel und Mengenregel die
Auftragsmenge gegen die Ist-Menge (Mengenfaktor) und je Verfahren Soll- gegen Ist-Minuten je Einheit
(Zeitfaktor) - nur Anzeige mit Vorschlag, keine Änderung an Regeln oder Stücklisten.

Rechte: Büro, Meister und System Manager (``planung.auftrag.darf_planen``); Projekte, Aufträge und
Schlussrechnungen nur, soweit der Benutzer sie lesen darf. Lizenzgrenze: Kern-Doctypes werden nur per Name
gelesen, kein Import.
"""

from __future__ import annotations

import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from corevatis_erpnext.abrechnung.felder import ART_ABZUG
from corevatis_erpnext.planung.auftrag import darf_planen
from corevatis_erpnext.planung.bauablauf import VERFAHREN_SONSTIGES
from corevatis_erpnext.planung.positionen import positionen

ARBEITSZEIT, FAHRZEIT, PAUSE = "Arbeitszeit", "Fahrzeit", "Pause"
OHNE_PAKET = "Ohne Arbeitspaket"
NACHTRAEGE = "Nachträge"
GRUEN, GELB, ROT, GRAU = "grün", "gelb", "rot", "grau"
AMPEL_GELB_AB = 1.0
AMPEL_ROT_AB = 1.15
PROJEKTE_MAX = 50
TASK_ABGESCHLOSSEN = ("Completed", "Cancelled")
SCHEMA_DOCTYPE = "Kalkulationsschema"
# Abweichung, ab der die Lernschleife einen Vorschlag macht (5 %)
REGEL_TOLERANZ = 0.05


class NachkalkulationFehler(frappe.ValidationError):
	pass


def pruefe_rolle() -> None:
	if not darf_planen():
		frappe.throw(_("Die Nachkalkulation sehen nur die Rollen Büro und Meister."), frappe.PermissionError)


def _lesbar(doctype: str, name: str) -> bool:
	return bool(frappe.has_permission(doctype, "read", doc=name))


def _hat(doctype: str, feld: str) -> bool:
	return frappe.db.has_column(doctype, feld)


# --- Kalkulation des Angebots ------------------------------------------------------------------------


def kalkulation_laden(auftrag) -> tuple[dict[str, list[dict]], float, str | None, list[str]]:
	"""Positionen der Kalkulation je Artikel (Reihenfolge wie im Angebot), Stundensatz, Schema, Hinweise."""
	hinweise: list[str] = []
	angebote = []
	for z in auftrag.items:
		if z.get("prevdoc_docname") and z.prevdoc_docname not in angebote:
			angebote.append(z.prevdoc_docname)
	je_artikel: dict[str, list[dict]] = defaultdict(list)
	stundensatz = 0.0
	schema_name = None
	if not _hat("Quotation", "cv_kalkulation_json"):
		return je_artikel, stundensatz, schema_name, hinweise
	for angebot in angebote:
		roh, schema_name = frappe.db.get_value(
			"Quotation", angebot, ["cv_kalkulation_json", "cv_kalkulationsschema"]
		) or (None, None)
		if not roh:
			continue
		try:
			daten = json.loads(roh) if isinstance(roh, str) else roh
		except ValueError:
			hinweise.append(_("Kalkulation des Angebots {0} nicht lesbar.").format(angebot))
			continue
		schema = daten.get("schema") or {}
		stundensatz = stundensatz or flt(schema.get("stundensatz"))
		schema_name = schema_name or schema.get("name")
		for p in daten.get("positionen") or []:
			code = p.get("item_code") or p.get("id")
			if not code:
				continue
			k = p.get("kalkulation") or {}
			mengen = p.get("mengen") or {}
			je_artikel[code].append(
				{
					"minuten": flt(k.get("minuten")),
					"material": flt(k.get("material")),
					"menge": flt(p.get("menge")),
					"einzelpreis": flt(p.get("einzelpreis") or k.get("einzelpreis")),
					"regel": mengen.get("regel") or None,
					"regelart": mengen.get("kind") or None,
					"verfahren": (p.get("herkunft") or {}).get("verfahren"),
				}
			)
	if not stundensatz and schema_name and frappe.db.exists("DocType", SCHEMA_DOCTYPE):
		stundensatz = flt(frappe.db.get_value(SCHEMA_DOCTYPE, schema_name, "stundensatz"))
	if not stundensatz:
		hinweise.append(_("Kein Stundensatz aus der Kalkulation: Lohnkosten werden mit 0 € gerechnet."))
	return je_artikel, stundensatz, schema_name, hinweise


def _materialwert(material: list[dict]) -> float:
	"""Stücklistenmaterial einer Position zum Bewertungspreis des Artikels."""
	summe = 0.0
	for m in material or []:
		preis = flt(frappe.get_cached_value("Item", m["item_code"], "valuation_rate"))
		summe += flt(m.get("menge")) * preis
	return summe


# --- Soll ---------------------------------------------------------------------------------------------


def _leer() -> dict:
	return {
		"soll_h": 0.0,
		"ist_h": 0.0,
		"fahrt_h": 0.0,
		"soll_material": 0.0,
		"ist_material": 0.0,
		"ist_material_menge": 0.0,
		"erloes": 0.0,
		"erloes_quelle": "Auftrag",
		"positionen": 0,
		"soll_menge": 0.0,
		"tasks": [],
	}


def soll_je_verfahren(auftrag, kalk: dict[str, list[dict]]) -> tuple[dict[str, dict], list[dict], list[str]]:
	"""Soll je Verfahren aus den Auftragspositionen; dazu die Positionen mit ihrer Soll-Herkunft."""
	pos, hinweise = positionen(auftrag)
	betrag = {z.name: flt(z.get("net_amount") or z.amount) for z in auftrag.items}
	zaehler: dict[str, int] = defaultdict(int)
	je_verfahren: dict[str, dict] = {}
	for z in pos:
		v = z["verfahren"] or VERFAHREN_SONSTIGES
		s = je_verfahren.setdefault(v, _leer())
		kandidaten = kalk.get(z["item_code"]) or []
		k = kandidaten[zaehler[z["item_code"]]] if zaehler[z["item_code"]] < len(kandidaten) else None
		zaehler[z["item_code"]] += 1
		if k and k["minuten"]:
			minuten = k["minuten"]
			quelle = "kalkulation"
		else:
			minuten = flt(z["minuten_je_einheit"])
			quelle = "stueckliste" if minuten else "keine"
		material = k["material"] * z["menge"] if k and k["material"] else _materialwert(z["material"])
		z["soll_minuten"] = flt(minuten * z["menge"], 6)
		z["soll_material"] = flt(material, 2)
		z["soll_quelle"] = quelle
		z["regel"] = k["regel"] if k else None
		z["regelart"] = k["regelart"] if k else None
		z["erloes"] = betrag.get(z["sales_order_item"], 0.0)
		s["soll_h"] += z["soll_minuten"] / 60
		s["soll_material"] += z["soll_material"]
		s["erloes"] += z["erloes"]
		s["positionen"] += 1
		s["soll_menge"] += z["menge"]
	return je_verfahren, pos, hinweise


# --- Ist ----------------------------------------------------------------------------------------------


def _tasks(project: str) -> list[frappe._dict]:
	felder = ["name", "subject", "status", "progress"]
	felder += [f for f in ("cv_verfahren", "cv_sollstunden") if _hat("Task", f)]
	return frappe.get_all("Task", filters={"project": project}, fields=felder, order_by="creation")


def _verfahren_je_task(tasks: list[frappe._dict]) -> dict[str, str]:
	return {t.name: (t.get("cv_verfahren") or "").strip() or VERFAHREN_SONSTIGES for t in tasks}


def ist_stunden(project: str, von=None, bis=None) -> list[frappe._dict]:
	"""Timesheet-Zeilen des Projekts je Arbeitspaket und Tätigkeit (Entwurf und eingereicht)."""
	bedingungen = ["t.docstatus < 2", "(td.project = %(p)s or t.parent_project = %(p)s)"]
	werte: dict = {"p": project}
	if von:
		bedingungen.append("date(td.from_time) >= %(von)s")
		werte["von"] = getdate(von)
	if bis:
		bedingungen.append("date(td.from_time) <= %(bis)s")
		werte["bis"] = getdate(bis)
	return frappe.db.sql(
		f"""
		select td.task, td.activity_type, sum(td.hours) as stunden
		from `tabTimesheet Detail` td
		join `tabTimesheet` t on t.name = td.parent
		where {" and ".join(bedingungen)}
		group by td.task, td.activity_type
		""",
		werte,
		as_dict=True,
	)


def ist_material(project: str, von=None, bis=None) -> list[frappe._dict]:
	"""Materialentnahmen des Projekts je Arbeitspaket und Artikel (eingereichte Stock Entries)."""
	task_spalte = "se.cv_task" if _hat("Stock Entry", "cv_task") else "null"
	bedingungen = [
		"se.docstatus = 1",
		"se.purpose = 'Material Issue'",
		"(se.project = %(p)s or sed.project = %(p)s)",
	]
	werte: dict = {"p": project}
	if von:
		bedingungen.append("se.posting_date >= %(von)s")
		werte["von"] = getdate(von)
	if bis:
		bedingungen.append("se.posting_date <= %(bis)s")
		werte["bis"] = getdate(bis)
	return frappe.db.sql(
		f"""
		select {task_spalte} as task, sed.item_code, sum(sed.qty) as menge,
			sum(coalesce(sed.basic_amount, sed.amount, 0)) as betrag
		from `tabStock Entry Detail` sed
		join `tabStock Entry` se on se.name = sed.parent
		where {" and ".join(bedingungen)}
		group by {task_spalte}, sed.item_code
		""",
		werte,
		as_dict=True,
	)


def schlussrechnung(auftrag_name: str) -> frappe._dict | None:
	"""Freigegebene Schlussrechnung zum Auftrag (7.2), falls vorhanden."""
	if not _hat("Sales Invoice", "cv_schlussrechnung"):
		return None
	namen = frappe.get_all(
		"Sales Invoice",
		filters={"cv_auftrag": auftrag_name, "cv_schlussrechnung": 1, "docstatus": 1},
		fields=["name", "posting_date", "cv_abrechnung_json"],
		order_by="posting_date desc, creation desc",
		limit=1,
	)
	return namen[0] if namen else None


def erloes_schlussrechnung(rechnung: str, verfahren_je_position: dict[str, str]) -> dict[str, float]:
	"""Nettobeträge der Schlussrechnung je Verfahren (Abzüge der Abschläge zählen nicht)."""
	felder = ["so_detail", "item_code", "net_amount", "amount"]
	felder += [f for f in ("cv_positionsart", "cv_bezug") if _hat("Sales Invoice Item", f)]
	ergebnis: dict[str, float] = defaultdict(float)
	codes = set()
	zeilen = frappe.get_all("Sales Invoice Item", filters={"parent": rechnung}, fields=felder, order_by="idx")
	for z in zeilen:
		if (z.get("cv_positionsart") or "") == ART_ABZUG:
			continue
		codes.add(z.item_code)
	je_artikel = {}
	if codes and _hat("Item", "cv_verfahren"):
		je_artikel = dict(
			frappe.get_all(
				"Item", filters={"name": ["in", list(codes)]}, fields=["name", "cv_verfahren"], as_list=True
			)
		)
	for z in zeilen:
		if (z.get("cv_positionsart") or "") == ART_ABZUG:
			continue
		v = (
			verfahren_je_position.get(z.get("so_detail") or "")
			or verfahren_je_position.get(z.get("cv_bezug") or "")
			or (je_artikel.get(z.item_code) or "").strip()
			or NACHTRAEGE
		)
		ergebnis[v] += flt(z.get("net_amount") or z.amount)
	return ergebnis


# --- Auswertung -------------------------------------------------------------------------------------


def ampel(soll_h: float, ist_h: float, fortschritt: float) -> tuple[str, float | None]:
	"""Farbe und Verhältnis Ist ÷ erwartete Stunden (Soll x Fortschritt)."""
	if ist_h <= 0:
		return GRAU, None
	if soll_h <= 0:
		return GELB, None
	anteil = fortschritt / 100 if 0 < fortschritt < 100 else 1.0
	erwartet = soll_h * anteil
	verhaeltnis = ist_h / erwartet if erwartet else None
	if verhaeltnis is None:
		return GELB, None
	if verhaeltnis <= AMPEL_GELB_AB:
		return GRUEN, verhaeltnis
	if verhaeltnis <= AMPEL_ROT_AB:
		return GELB, verhaeltnis
	return ROT, verhaeltnis


def _fortschritt(tasks: list[frappe._dict]) -> float:
	"""Fortschritt der Arbeitspakete eines Verfahrens in Prozent (100, wenn alle abgeschlossen)."""
	offen = [t for t in tasks if t.status not in TASK_ABGESCHLOSSEN]
	if tasks and not offen:
		return 100.0
	werte = [flt(t.progress) for t in offen if flt(t.progress) > 0]
	return sum(werte) / len(werte) if werte else 0.0


def projekt_auswerten(project: str, von=None, bis=None) -> dict:
	"""Nachkalkulation eines Projekts: ``zeilen`` je Verfahren, ``summe``, ``hinweise``, Kopfdaten."""
	p = frappe.db.get_value(
		"Project",
		project,
		["name", "project_name", "sales_order", "company", "status", "percent_complete"],
		as_dict=True,
	)
	if not p:
		frappe.throw(_("Projekt {0} nicht gefunden.").format(project), frappe.DoesNotExistError)
	waehrung = frappe.get_cached_value("Company", p.company, "default_currency") if p.company else None
	hinweise: list[str] = []
	je_verfahren: dict[str, dict] = {}
	stundensatz = 0.0
	schema_name = None
	pos: list[dict] = []
	auftrag = None
	if p.sales_order and frappe.db.exists("Sales Order", p.sales_order):
		auftrag = frappe.get_doc("Sales Order", p.sales_order)
		kalk, stundensatz, schema_name, hinweise = kalkulation_laden(auftrag)
		je_verfahren, pos, weitere = soll_je_verfahren(auftrag, kalk)
		hinweise += weitere
	else:
		hinweise.append(_("Kein Auftrag am Projekt: keine Sollwerte."))

	tasks = _tasks(project)
	verfahren_je_task = _verfahren_je_task(tasks)
	for t in tasks:
		je_verfahren.setdefault(verfahren_je_task[t.name], _leer())["tasks"].append(t)

	for z in ist_stunden(project, von, bis):
		if z.activity_type == PAUSE:
			continue
		v = verfahren_je_task.get(z.task) if z.task else None
		s = je_verfahren.setdefault(v or OHNE_PAKET, _leer())
		s["ist_h"] += flt(z.stunden)
		if z.activity_type == FAHRZEIT:
			s["fahrt_h"] += flt(z.stunden)
	for m in ist_material(project, von, bis):
		v = verfahren_je_task.get(m.task) if m.task else None
		s = je_verfahren.setdefault(v or OHNE_PAKET, _leer())
		s["ist_material"] += flt(m.betrag)
		s["ist_material_menge"] += flt(m.menge)

	rechnung = schlussrechnung(auftrag.name) if auftrag else None
	if rechnung:
		verfahren_je_position = {z["sales_order_item"]: z["verfahren"] for z in pos}
		for v, betrag in erloes_schlussrechnung(rechnung.name, verfahren_je_position).items():
			s = je_verfahren.setdefault(v, _leer())
			s["erloes"] = betrag
			s["erloes_quelle"] = "Schlussrechnung"
		for s in je_verfahren.values():
			if s["erloes_quelle"] != "Schlussrechnung" and s["positionen"]:
				# Verfahren ohne Zeile in der Schlussrechnung (Ist-Menge 0): kein Erlös
				s["erloes"], s["erloes_quelle"] = 0.0, "Schlussrechnung"

	zeilen = []
	for v in sorted(je_verfahren, key=lambda x: (x in (OHNE_PAKET, NACHTRAEGE), x)):
		s = je_verfahren[v]
		fortschritt = _fortschritt(s["tasks"])
		farbe, verhaeltnis = ampel(s["soll_h"], s["ist_h"], fortschritt)
		soll_lohn = s["soll_h"] * stundensatz
		ist_lohn = s["ist_h"] * stundensatz
		zeilen.append(
			{
				"verfahren": v,
				"tasks": [t.name for t in s["tasks"]],
				"task": s["tasks"][0].name if len(s["tasks"]) == 1 else None,
				"status": _status_text(s["tasks"]),
				"fortschritt": flt(fortschritt, 0),
				"soll_h": flt(s["soll_h"], 2),
				"ist_h": flt(s["ist_h"], 2),
				"fahrt_h": flt(s["fahrt_h"], 2),
				"verbrauch": flt(s["ist_h"] / s["soll_h"] * 100, 0) if s["soll_h"] else None,
				"ampel": farbe,
				"verhaeltnis": flt(verhaeltnis, 2) if verhaeltnis is not None else None,
				"soll_material": flt(s["soll_material"], 2),
				"ist_material": flt(s["ist_material"], 2),
				"soll_lohn": flt(soll_lohn, 2),
				"ist_lohn": flt(ist_lohn, 2),
				"erloes": flt(s["erloes"], 2),
				"erloes_quelle": s["erloes_quelle"],
				"marge_soll": flt(s["erloes"] - soll_lohn - s["soll_material"], 2),
				"marge_ist": flt(s["erloes"] - ist_lohn - s["ist_material"], 2),
				"marge_prozent": flt((s["erloes"] - ist_lohn - s["ist_material"]) / s["erloes"] * 100, 1)
				if s["erloes"]
				else None,
				"positionen": s["positionen"],
				"currency": waehrung,
			}
		)
	summe = _summe(zeilen, waehrung)
	summe["ampel"] = _ampel_gesamt(zeilen)
	return {
		"projekt": p.name,
		"projekt_name": p.project_name,
		"auftrag": auftrag.name if auftrag else None,
		"firma": p.company,
		"status": p.status,
		"stundensatz": stundensatz,
		"kalkulationsschema": schema_name,
		"schlussrechnung": rechnung.name if rechnung else None,
		"zeilen": zeilen,
		"positionen": pos,
		"summe": summe,
		"hinweise": hinweise,
	}


def _status_text(tasks: list[frappe._dict]) -> str:
	if not tasks:
		return ""
	if all(t.status in TASK_ABGESCHLOSSEN for t in tasks):
		return _("abgeschlossen")
	if any(t.status == "Working" for t in tasks):
		return _("in Arbeit")
	return _("offen")


def _summe(zeilen: list[dict], waehrung: str | None) -> dict:
	felder = (
		"soll_h",
		"ist_h",
		"fahrt_h",
		"soll_material",
		"ist_material",
		"soll_lohn",
		"ist_lohn",
		"erloes",
		"marge_soll",
		"marge_ist",
	)
	s = {f: flt(sum(flt(z[f]) for z in zeilen), 2) for f in felder}
	s["verbrauch"] = flt(s["ist_h"] / s["soll_h"] * 100, 0) if s["soll_h"] else None
	s["marge_prozent"] = flt(s["marge_ist"] / s["erloes"] * 100, 1) if s["erloes"] else None
	s["currency"] = waehrung
	return s


def _ampel_gesamt(zeilen: list[dict]) -> str:
	farben = [z["ampel"] for z in zeilen if z["ampel"] != GRAU]
	if not farben:
		return GRAU
	if ROT in farben:
		return ROT
	if GELB in farben:
		return GELB
	return GRUEN


# --- Übersicht (Bericht) -----------------------------------------------------------------------------


def projekte_waehlen(filters: dict) -> list[str]:
	"""Projekte des Berichts: gewähltes Projekt, sonst Projekte mit Auftrag (Leserecht des Benutzers)."""
	f = filters or {}
	if f.get("projekt"):
		if not _lesbar("Project", f["projekt"]):
			frappe.throw(
				_("Keine Berechtigung für Projekt {0}.").format(f["projekt"]), frappe.PermissionError
			)
		return [f["projekt"]]
	bedingungen: dict = {"sales_order": ["is", "set"]}
	if cint(f.get("nur_offene")):
		bedingungen["status"] = "Open"
	if f.get("firma"):
		bedingungen["company"] = f["firma"]
	return frappe.get_list(
		"Project", filters=bedingungen, pluck="name", order_by="modified desc", limit=PROJEKTE_MAX
	)


def uebersicht(filters: dict | None = None) -> dict:
	"""Zeilen des Berichts „Nachkalkulation“: je Projekt eine Summenzeile, darunter die Verfahren."""
	pruefe_rolle()
	f = filters or {}
	zeilen: list[dict] = []
	hinweise: list[str] = []
	auswertungen = []
	for projekt in projekte_waehlen(f):
		a = projekt_auswerten(projekt, f.get("von"), f.get("bis"))
		verfahren = [z for z in a["zeilen"] if not f.get("verfahren") or z["verfahren"] == f["verfahren"]]
		if f.get("verfahren") and not verfahren:
			continue
		auswertungen.append(a)
		s = a["summe"]
		zeilen.append(
			{
				"bezeichnung": f"{a['projekt']} {a['projekt_name'] or ''}".strip(),
				"projekt": a["projekt"],
				"indent": 0,
				"status": _(a["status"]) if a["status"] else "",
				"ampel": s["ampel"],
				**{k: s[k] for k in s if k not in ("ampel",)},
				"hinweis": "; ".join(a["hinweise"])[:200],
			}
		)
		for z in verfahren:
			zeilen.append(
				{
					"bezeichnung": z["verfahren"],
					"projekt": a["projekt"],
					"indent": 1,
					"arbeitspaket": z["task"],
					"status": z["status"],
					"fortschritt": z["fortschritt"],
					**{
						k: z[k] for k in z if k not in ("tasks", "task", "status", "fortschritt", "verfahren")
					},
					"hinweis": _("Erlös aus {0}").format(_(z["erloes_quelle"])),
				}
			)
		hinweise += [f"{a['projekt']}: {h}" for h in a["hinweise"]]
	return {"zeilen": zeilen, "auswertungen": auswertungen, "hinweise": hinweise}


def kennzahl(project: str) -> dict | None:
	"""Kurzform für die Prozessleiste: Verbrauch der Sollstunden, Marge, Ampel. ``None`` ohne Daten
	oder ohne Rolle."""
	if not darf_planen() or not _lesbar("Project", project):
		return None
	a = projekt_auswerten(project)
	s = a["summe"]
	if not s["soll_h"] and not s["ist_h"]:
		return None
	teile = []
	if s["verbrauch"] is not None:
		teile.append(_("{0} % der Sollstunden").format(cint(s["verbrauch"])))
	elif s["ist_h"]:
		teile.append(_("{0} h ohne Sollstunden").format(s["ist_h"]))
	if s["marge_prozent"] is not None:
		teile.append(_("Marge {0} %").format(flt(s["marge_prozent"], 0)))
	return {
		"projekt": project,
		"ampel": s["ampel"],
		"verbrauch": s["verbrauch"],
		"soll_h": s["soll_h"],
		"ist_h": s["ist_h"],
		"marge_prozent": s["marge_prozent"],
		"text": _("Nachkalkulation: {0}").format(", ".join(teile)) if teile else _("Nachkalkulation"),
	}


# --- Lernschleife: Mengenregeln ---------------------------------------------------------------------


def regeln(filters: dict | None = None) -> dict:
	"""Soll gegen Ist je Artikel und Mengenregel über freigegebene Schlussrechnungen (nur Anzeige)."""
	pruefe_rolle()
	f = filters or {}
	if not _hat("Sales Invoice", "cv_schlussrechnung"):
		return {"zeilen": [], "hinweise": [_("Abrechnung (7.2) nicht eingerichtet.")]}
	bedingungen: dict = {"cv_schlussrechnung": 1, "docstatus": 1}
	if f.get("firma"):
		bedingungen["company"] = f["firma"]
	if f.get("von"):
		bedingungen["posting_date"] = [">=", getdate(f["von"])]
	if f.get("bis"):
		bedingungen["posting_date"] = (
			["between", [getdate(f["von"]), getdate(f["bis"])]] if f.get("von") else ["<=", getdate(f["bis"])]
		)
	rechnungen = frappe.get_list(
		"Sales Invoice",
		filters=bedingungen,
		fields=["name", "cv_auftrag", "project", "posting_date", "cv_abrechnung_json"],
		order_by="posting_date desc",
		limit=200,
	)
	sammlung: dict[tuple, dict] = {}
	hinweise: list[str] = []
	for r in rechnungen:
		if not r.cv_auftrag or not frappe.db.exists("Sales Order", r.cv_auftrag):
			continue
		if not _lesbar("Sales Order", r.cv_auftrag):
			continue
		try:
			roh = r.cv_abrechnung_json
			ergebnis = (json.loads(roh) if isinstance(roh, str) else roh) or {}
		except ValueError:
			hinweise.append(_("Abrechnung der Schlussrechnung {0} nicht lesbar.").format(r.name))
			continue
		# Ergebnis der Engine: ``id`` = Auftragszeile (so_detail), Nachträge ohne Auftragszeile zählen nicht
		ist_je_zeile = {
			p.get("id"): flt(p.get("menge_ist"))
			for p in ergebnis.get("positionen") or []
			if p.get("id") and not p.get("nachtrag")
		}
		auftrag = frappe.get_doc("Sales Order", r.cv_auftrag)
		projekt = r.project or frappe.db.get_value("Project", {"sales_order": auftrag.name}, "name")
		if projekt and not _lesbar("Project", projekt):
			projekt = None
		kalk, _satz, _schema, _h = kalkulation_laden(auftrag)
		_soll, pos, _h2 = soll_je_verfahren(auftrag, kalk)
		zeitfaktor: dict[str, float | None] = {}
		if projekt:
			a = projekt_auswerten(projekt)
			for z in a["zeilen"]:
				zeitfaktor[z["verfahren"]] = (
					(z["ist_h"] / z["soll_h"]) if z["soll_h"] and z["ist_h"] else None
				)
		for z in pos:
			if f.get("verfahren") and z["verfahren"] != f["verfahren"]:
				continue
			ist = ist_je_zeile.get(z["sales_order_item"])
			if ist is None:
				continue
			schluessel = (z["item_code"], z.get("regel") or "", z["verfahren"])
			e = sammlung.setdefault(
				schluessel,
				{
					"item_code": z["item_code"],
					"bezeichnung": z["item_name"],
					"verfahren": z["verfahren"],
					"regel": z.get("regel") or "",
					"regelart": z.get("regelart") or "",
					"einheit": z["einheit"],
					"projekte": set(),
					"soll_menge": 0.0,
					"ist_menge": 0.0,
					"soll_minuten": 0.0,
					"zeitfaktoren": [],
					"minuten_je_einheit": flt(z["soll_minuten"] / z["menge"], 2) if z["menge"] else 0.0,
				},
			)
			e["projekte"].add(projekt or auftrag.name)
			e["soll_menge"] += flt(z["menge"])
			e["ist_menge"] += ist
			e["soll_minuten"] += flt(z["soll_minuten"])
			if zeitfaktor.get(z["verfahren"]):
				e["zeitfaktoren"].append(zeitfaktor[z["verfahren"]])
	zeilen = []
	for e in sammlung.values():
		mengenfaktor = e["ist_menge"] / e["soll_menge"] if e["soll_menge"] else None
		zf = sum(e["zeitfaktoren"]) / len(e["zeitfaktoren"]) if e["zeitfaktoren"] else None
		zeilen.append(
			{
				"item_code": e["item_code"],
				"bezeichnung": e["bezeichnung"],
				"verfahren": e["verfahren"],
				"regel": e["regel"],
				"regelart": e["regelart"],
				"einheit": e["einheit"],
				"projekte": len(e["projekte"]),
				"soll_menge": flt(e["soll_menge"], 2),
				"ist_menge": flt(e["ist_menge"], 2),
				"mengenfaktor": flt(mengenfaktor, 2) if mengenfaktor is not None else None,
				"soll_min_je_einheit": e["minuten_je_einheit"],
				"ist_min_je_einheit": flt(e["minuten_je_einheit"] * zf, 1)
				if zf and e["minuten_je_einheit"]
				else None,
				"zeitfaktor": flt(zf, 2) if zf is not None else None,
				"vorschlag": vorschlag(mengenfaktor, zf, e["regel"], e["minuten_je_einheit"]),
			}
		)
	zeilen.sort(key=lambda z: (-(abs((z["mengenfaktor"] or 1) - 1)), z["item_code"]))
	return {"zeilen": zeilen, "hinweise": hinweise}


def vorschlag(mengenfaktor: float | None, zeitfaktor: float | None, regel: str, minuten: float) -> str:
	teile = []
	if mengenfaktor is not None and abs(mengenfaktor - 1) >= REGEL_TOLERANZ:
		ziel = _("Regel {0}").format(regel) if regel else _("Menge der Position")
		teile.append(
			_("{0}: Ist-Menge liegt bei {1} % der Angebotsmenge, Faktor {2} prüfen.").format(
				ziel,
				cint(mengenfaktor * 100),
				frappe.format_value(flt(mengenfaktor, 2), {"fieldtype": "Float", "precision": 2}),
			)
		)
	if zeitfaktor is not None and minuten and abs(zeitfaktor - 1) >= REGEL_TOLERANZ:
		teile.append(
			_("Montageminuten je Einheit {0} statt {1} (Zeitfaktor {2}).").format(
				frappe.format_value(flt(minuten * zeitfaktor, 1), {"fieldtype": "Float", "precision": 1}),
				frappe.format_value(flt(minuten, 1), {"fieldtype": "Float", "precision": 1}),
				frappe.format_value(flt(zeitfaktor, 2), {"fieldtype": "Float", "precision": 2}),
			)
		)
	return " ".join(teile) if teile else _("passt")
