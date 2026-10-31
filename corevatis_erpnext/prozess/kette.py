"""Prozessleiste: Stand eines Vorgangs über die Kette der Kernprozesse.

Ein Vorgang ist die Kette **Anfrage → Termin → Befund/Aufmaß → Angebot → Auftrag →
Ausführung → Abnahme → Rechnung** (``docs/kernprozesse.md``, Prozesse 1-7). Anker ist der Lead;
ohne Lead (z. B. ein Angebot direkt an einen Bestandskunden) beginnt die Kette beim
Startdatensatz.

``status(doctype, name)`` (whitelisted) ermittelt aus den Verknüpfungen, welche Schritte erledigt
sind und welcher der aktuelle ist, und schlägt die nächste Aktion vor. Die Aktionen sind die
vorhandenen Wege aus 1.3 (Termin anlegen), 2.1 (Objekt/Aufmaß), 4.1 (Angebot erzeugen) und 4.4
(Angebot annehmen) - die Leiste ruft sie nur auf, sie dupliziert nichts.

Verknüpfungen (Feldnamen sind Verträge der jeweiligen Aufgaben):

| Schritt | Belege | Verknüpfung |
|---|---|---|
| Anfrage | Lead | Anker |
| Termin | Event | ``Lead.cv_termin`` (1.3), sonst Event mit dem Lead als Teilnehmer |
| Befund/Aufmaß | Objekt, Aufmass, Befund (Kern) | ``Lead.cv_objekt``, ``Objekt.kunde`` = Lead (2.1); Aufmaß/Befund am Objekt oder ``referenz_name`` = Lead |
| Angebot | Quotation | ``cv_aufmass`` (4.1), ``cv_lead`` (4.4), ``party_name`` = Lead |
| Auftrag | Sales Order | ``Sales Order Item.prevdoc_docname`` = Angebot, ``cv_lead`` (4.4) |
| Ausführung | Project, Task, Timesheet, Tagesrapport (Kern, 5.3) | ``Project.sales_order``, ``cv_lead``, ``cv_angebot``, ``Lead.cv_projekt`` (4.4) |
| Abnahme | Abnahmeprotokoll (Kern, 6.1) | ``Abnahmeprotokoll.projekt`` |
| Rechnung | Sales Invoice | ``project`` oder ``Sales Invoice Item.sales_order`` |

Rechte: Der Benutzer braucht Leserecht auf den Startdatensatz. Die Kette wird ohne Rechte
ermittelt (sonst wäre der Stand für einen Monteur falsch), ausgegeben werden aber nur Namen von
Datensätzen, die der Benutzer lesen darf. Ein Schritt mit Belegen, von denen er keinen lesen
darf, ist ``gesperrt``: Stand ja, Namen, Texte und Aktionen nein. Aktionen erscheinen nur mit
den Rechten, die die aufgerufene Methode selbst prüft.

Kern-Doctypes (``Objekt``, ``Aufmass``, ``Befund``, später ``Tagesrapport`` und
``Abnahmeprotokoll``) werden nur über ihren Namen gelesen (Lizenzgrenze, kein Import aus
``corevatis``); fehlen sie, bleibt der Schritt ohne Belege.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, format_date, get_datetime, getdate, now_datetime, today

SCHRITTE = (
	("anfrage", "Anfrage"),
	("termin", "Termin"),
	("aufmass", "Befund/Aufmaß"),
	("angebot", "Angebot"),
	("auftrag", "Auftrag"),
	("ausfuehrung", "Ausführung"),
	("abnahme", "Abnahme"),
	("rechnung", "Rechnung"),
)
SCHRITT_LABEL = dict(SCHRITTE)
SCHRITT_KEYS = tuple(key for key, _label in SCHRITTE)

ERLEDIGT, AKTIV, OFFEN, ENTFAELLT, VERLOREN = "erledigt", "aktiv", "offen", "entfaellt", "verloren"
# Schritte, die als „übersprungen“ gelten, wenn ein späterer Schritt bis zum Auftrag schon läuft
# (z. B. Aufmaß ohne vorherigen Termin). Abnahme und Rechnung nie: Abschlagsrechnungen laufen
# vor der Abnahme.
UEBERSPRINGBAR = ("termin", "aufmass", "angebot")

STARTDOCTYPES = (
	"Lead",
	"Objekt",
	"Aufmass",
	"Befund",
	"Quotation",
	"Sales Order",
	"Project",
	"Abnahmeprotokoll",
	"Sales Invoice",
)

OBJEKT, AUFMASS, BEFUND = "Objekt", "Aufmass", "Befund"
# Kern-Doctypes (5.3, 6.1): Verknüpfung zum Projekt über ``projekt`` (Vertragstest im Kern)
TAGESRAPPORT, ABNAHMEPROTOKOLL = "Tagesrapport", "Abnahmeprotokoll"
# Abnahme (6.1): abgeschlossenes Protokoll mit einem dieser Ergebnisse = Schritt erledigt
ABNAHME_ABGESCHLOSSEN = "Abgeschlossen"
ABNAHME_ERLEDIGT = ("Abgenommen", "Abgenommen mit Mängeln")
ABNAHME_VERWEIGERT = "Abnahme verweigert"
AUFMASS_ABGESCHLOSSEN = "Abgeschlossen"

ROLLEN_ANGEBOT = ("Büro", "Meister", "System Manager")
STATUS_ANFRAGE_VERLOREN = "Verloren"
QUOTATION_OFFEN = ("Open", "Replied")
QUOTATION_STATUS_DE = {
	"Draft": "Entwurf",
	"Open": "freigegeben",
	"Replied": "beantwortet",
	"Partially Ordered": "teilweise beauftragt",
	"Ordered": "beauftragt",
	"Lost": "verloren",
	"Expired": "abgelaufen",
}
LEAD_FELDER = (
	"name",
	"lead_name",
	"company_name",
	"status",
	"cv_status",
	"cv_verlustgrund",
	"cv_objekt_adresse",
	"cv_termin",
	# Wunschtermin des Kunden (D14)
	"cv_wunsch1_datum",
	"cv_wunsch1_fenster",
	"cv_wunsch2_datum",
	"cv_wunsch2_fenster",
	"cv_wunsch_schnell",
	"cv_objekt",
	"cv_projekt",
	"lead_owner",
	"owner",
	"creation",
)


# --- Hilfen ---------------------------------------------------------------------------------


def _cache() -> dict:
	"""Zwischenspeicher für einen Aufruf (``neuer_aufruf`` leert ihn; frappe.local lebt in Tests und
	``bench execute`` länger als eine Anfrage)."""
	if not hasattr(frappe.local, "cv_prozess_cache"):
		frappe.local.cv_prozess_cache = {}
	return frappe.local.cv_prozess_cache


def neuer_aufruf() -> None:
	frappe.local.cv_prozess_cache = {}


def doctype_da(doctype: str) -> bool:
	cache = _cache()
	key = ("dt", doctype)
	if key not in cache:
		cache[key] = bool(frappe.db.exists("DocType", doctype))
	return cache[key]


def spalte_da(doctype: str, spalte: str) -> bool:
	return doctype_da(doctype) and bool(frappe.db.has_column(doctype, spalte))


def _spalten(doctype: str, felder) -> list[str]:
	return [f for f in felder if f == "name" or spalte_da(doctype, f)]


def _wert(doctype: str, name: str | None, felder) -> frappe._dict:
	if not name or not doctype_da(doctype):
		return frappe._dict()
	felder = _spalten(doctype, felder)
	return frappe.db.get_value(doctype, name, felder, as_dict=True) or frappe._dict()


def _namen(doctype: str, filters: dict, order_by: str = "creation asc") -> list[str]:
	if not doctype_da(doctype):
		return []
	for feld in filters:
		if not spalte_da(doctype, feld):
			return []
	return frappe.get_all(doctype, filters=filters, pluck="name", order_by=order_by)


def _in(werte) -> list:
	return [w for w in dict.fromkeys(werte) if w]


def lesbar(doctype: str, name: str) -> bool:
	"""Leserecht des angemeldeten Benutzers; fehlende Datensätze gelten als nicht lesbar."""
	cache = _cache()
	key = ("lesbar", frappe.session.user, doctype, name)
	if key not in cache:
		try:
			cache[key] = bool(frappe.has_permission(doctype, "read", doc=name))
		except (frappe.DoesNotExistError, frappe.PermissionError):
			cache[key] = False
	return cache[key]


def _tage_seit(wert) -> int:
	if not wert:
		return 0
	return max(date_diff(today(), getdate(wert)), 0)


# --- Anker ----------------------------------------------------------------------------------


def _lead_vom_kunden(kunde: str | None) -> str | None:
	if not kunde:
		return None
	lead = frappe.db.get_value("Customer", kunde, "lead_name")
	return lead if lead and frappe.db.exists("Lead", lead) else None


def lead_von(doctype: str, name: str, tiefe: int = 0) -> str | None:
	"""Anker-Lead eines Datensatzes (ohne Rechteprüfung, nur für die Kette)."""
	if tiefe > 5 or not name:
		return None
	weiter = tiefe + 1
	if doctype == "Lead":
		return name if frappe.db.exists("Lead", name) else None
	if doctype == OBJEKT:
		w = _wert(OBJEKT, name, ("kunde_doctype", "kunde"))
		if w.get("kunde_doctype") == "Lead" and w.get("kunde"):
			return lead_von("Lead", w.kunde, weiter)
		if w.get("kunde_doctype") == "Customer":
			if lead := _lead_vom_kunden(w.get("kunde")):
				return lead
		if spalte_da("Lead", "cv_objekt"):
			return frappe.db.get_value("Lead", {"cv_objekt": name}, "name")
		return None
	if doctype in (AUFMASS, BEFUND):
		w = _wert(doctype, name, ("referenz_doctype", "referenz_name", "objekt"))
		if w.get("referenz_doctype") in ("Lead", "Quotation", "Project") and w.get("referenz_name"):
			if lead := lead_von(w.referenz_doctype, w.referenz_name, weiter):
				return lead
		return lead_von(OBJEKT, w.get("objekt"), weiter) if w.get("objekt") else None
	if doctype == "Quotation":
		w = _wert("Quotation", name, ("quotation_to", "party_name", "cv_lead", "cv_aufmass"))
		if w.get("cv_lead"):
			return lead_von("Lead", w.cv_lead, weiter)
		if w.get("quotation_to") == "Lead":
			return lead_von("Lead", w.get("party_name"), weiter)
		if w.get("cv_aufmass") and doctype_da(AUFMASS) and frappe.db.exists(AUFMASS, w.cv_aufmass):
			if lead := lead_von(AUFMASS, w.cv_aufmass, weiter):
				return lead
		return _lead_vom_kunden(w.get("party_name")) if w.get("quotation_to") == "Customer" else None
	if doctype == "Sales Order":
		w = _wert("Sales Order", name, ("cv_lead", "customer"))
		if w.get("cv_lead"):
			return lead_von("Lead", w.cv_lead, weiter)
		for angebot in _angebote_des_auftrags([name]):
			if lead := lead_von("Quotation", angebot, weiter):
				return lead
		return _lead_vom_kunden(w.get("customer"))
	if doctype == "Project":
		w = _wert("Project", name, ("cv_lead", "sales_order", "customer"))
		if w.get("cv_lead"):
			return lead_von("Lead", w.cv_lead, weiter)
		if w.get("sales_order"):
			if lead := lead_von("Sales Order", w.sales_order, weiter):
				return lead
		return _lead_vom_kunden(w.get("customer"))
	if doctype == ABNAHMEPROTOKOLL:
		projekt = _wert(ABNAHMEPROTOKOLL, name, ("projekt",)).get("projekt")
		return lead_von("Project", projekt, weiter) if projekt else None
	if doctype == "Sales Invoice":
		w = _wert("Sales Invoice", name, ("project", "customer"))
		if w.get("project"):
			if lead := lead_von("Project", w.project, weiter):
				return lead
		for auftrag in _auftraege_der_rechnung(name):
			if lead := lead_von("Sales Order", auftrag, weiter):
				return lead
		return _lead_vom_kunden(w.get("customer"))
	return None


def _angebote_des_auftrags(auftraege: list[str]) -> list[str]:
	if not auftraege:
		return []
	return _in(
		frappe.get_all(
			"Sales Order Item",
			filters={"parent": ["in", auftraege], "prevdoc_docname": ["is", "set"]},
			pluck="prevdoc_docname",
		)
	)


def _auftraege_der_rechnung(rechnung: str) -> list[str]:
	return _in(
		frappe.get_all(
			"Sales Invoice Item",
			filters={"parent": rechnung, "sales_order": ["is", "set"]},
			pluck="sales_order",
		)
	)


# --- Kette sammeln --------------------------------------------------------------------------


def sammeln(
	lead: str | None, start_doctype: str | None = None, start_name: str | None = None
) -> frappe._dict:
	"""Alle Belege des Vorgangs (ohne Rechteprüfung). Rückgabe: ``frappe._dict`` mit Listen."""
	k = frappe._dict(
		lead=None,
		termin=None,
		meister=None,
		objekte=[],
		aufmasse=[],
		befunde=0,
		angebote=[],
		auftraege=[],
		projekte=[],
		tasks=0,
		zugewiesen=0,
		ohne_zuweisung=0,
		zeiten=0,
		rapporte=0,
		abnahmen=[],
		rechnungen=[],
		nachfass=[],
		start=(start_doctype, start_name),
	)
	objekte: list[str] = []
	aufmasse: list[str] = []
	angebote: list[str] = []
	auftraege: list[str] = []
	projekte: list[str] = []
	rechnungen: list[str] = []

	# Startdatensatz
	if start_doctype == OBJEKT:
		objekte.append(start_name)
	elif start_doctype == AUFMASS:
		aufmasse.append(start_name)
	elif start_doctype == BEFUND:
		objekte.append(_wert(BEFUND, start_name, ("objekt",)).get("objekt"))
	elif start_doctype == "Quotation":
		angebote.append(start_name)
	elif start_doctype == "Sales Order":
		auftraege.append(start_name)
	elif start_doctype == "Project":
		projekte.append(start_name)
	elif start_doctype == ABNAHMEPROTOKOLL:
		projekte.append(_wert(ABNAHMEPROTOKOLL, start_name, ("projekt",)).get("projekt"))
	elif start_doctype == "Sales Invoice":
		rechnungen.append(start_name)
		w = _wert("Sales Invoice", start_name, ("project",))
		projekte.append(w.get("project"))
		auftraege += _auftraege_der_rechnung(start_name)

	# rückwärts vom Startdatensatz: Projekt → Auftrag → Angebot → Aufmaß → Objekt
	for projekt in _in(projekte):
		auftraege.append(_wert("Project", projekt, ("sales_order",)).get("sales_order"))
	angebote += _angebote_des_auftrags(_in(auftraege))
	for angebot in _in(angebote):
		aufmasse.append(_wert("Quotation", angebot, ("cv_aufmass",)).get("cv_aufmass"))
	if doctype_da(AUFMASS):
		aufmasse = [a for a in _in(aufmasse) if frappe.db.exists(AUFMASS, a)]
		for aufmass in aufmasse:
			objekte.append(_wert(AUFMASS, aufmass, ("objekt",)).get("objekt"))
	else:
		aufmasse = []

	# vorwärts vom Lead
	if lead:
		k.lead = _wert("Lead", lead, LEAD_FELDER)
		k.termin = _termin(k.lead)
		if k.termin:
			k.meister = _meister_des_termins(k.termin.name)
		objekte.append(k.lead.get("cv_objekt"))
		objekte += _namen(OBJEKT, {"kunde_doctype": "Lead", "kunde": lead})
		projekte.append(k.lead.get("cv_projekt"))
	objekte = [o for o in _in(objekte) if doctype_da(OBJEKT) and frappe.db.exists(OBJEKT, o)]

	if doctype_da(AUFMASS):
		if objekte:
			aufmasse += _namen(AUFMASS, {"objekt": ["in", objekte]})
		if lead:
			aufmasse += _namen(AUFMASS, {"referenz_doctype": "Lead", "referenz_name": lead})
	aufmasse = _in(aufmasse)
	if doctype_da(BEFUND):
		befunde = set()
		if objekte:
			befunde |= set(_namen(BEFUND, {"objekt": ["in", objekte]}))
		if lead:
			befunde |= set(_namen(BEFUND, {"referenz_doctype": "Lead", "referenz_name": lead}))
		k.befunde = len(befunde)

	if aufmasse and spalte_da("Quotation", "cv_aufmass"):
		angebote += _namen("Quotation", {"cv_aufmass": ["in", aufmasse], "docstatus": ["<", 2]})
	if lead:
		if spalte_da("Quotation", "cv_lead"):
			angebote += _namen("Quotation", {"cv_lead": lead, "docstatus": ["<", 2]})
		angebote += _namen("Quotation", {"quotation_to": "Lead", "party_name": lead, "docstatus": ["<", 2]})
	angebote = _in(angebote)

	if angebote:
		auftraege += _in(
			frappe.get_all("Sales Order Item", filters={"prevdoc_docname": ["in", angebote]}, pluck="parent")
		)
	if lead and spalte_da("Sales Order", "cv_lead"):
		auftraege += _namen("Sales Order", {"cv_lead": lead})
	auftraege = _in(auftraege)

	if auftraege:
		projekte += _namen("Project", {"sales_order": ["in", auftraege]})
	if lead:
		projekte += _namen("Project", {"cv_lead": lead})
	if angebote:
		projekte += _namen("Project", {"cv_angebot": ["in", angebote]})
	projekte = [p for p in _in(projekte) if frappe.db.exists("Project", p)]

	if projekte:
		rechnungen += _namen("Sales Invoice", {"project": ["in", projekte], "docstatus": ["<", 2]})
	if auftraege:
		rechnungen += _in(
			frappe.get_all("Sales Invoice Item", filters={"sales_order": ["in", auftraege]}, pluck="parent")
		)
	rechnungen = _in(rechnungen)

	# Datensätze mit den Feldern für die Auswertung, stornierte (docstatus 2) fallen weg
	k.objekte = _zeilen(OBJEKT, objekte, ("name", "titel", "creation"))
	k.aufmasse = _zeilen(AUFMASS, aufmasse, ("name", "status", "objekt", "creation", "modified"))
	k.angebote = _zeilen(
		"Quotation",
		angebote,
		("name", "docstatus", "status", "transaction_date", "creation", "owner", "cv_aufmass"),
	)
	k.auftraege = _zeilen(
		"Sales Order",
		auftraege,
		("name", "docstatus", "status", "transaction_date", "per_billed", "creation", "owner"),
	)
	k.projekte = _zeilen(
		"Project",
		projekte,
		("name", "project_name", "status", "percent_complete", "expected_start_date", "creation"),
	)
	k.rechnungen = _zeilen(
		"Sales Invoice", rechnungen, ("name", "docstatus", "status", "is_return", "creation")
	)
	if k.projekte:
		namen = [p.name for p in k.projekte]
		from corevatis_erpnext.planung.projekt import zaehlen

		pakete = zaehlen(namen)
		k.tasks, k.zugewiesen, k.ohne_zuweisung = (
			pakete.arbeitspakete,
			pakete.zugewiesen,
			pakete.ohne_zuweisung,
		)
		k.zeiten = _zeitbuchungen(namen)
		k.rapporte = len(_kern_belege(TAGESRAPPORT, namen))
		k.abnahmen = _zeilen(
			ABNAHMEPROTOKOLL,
			_kern_belege(ABNAHMEPROTOKOLL, namen),
			("name", "docstatus", "status", "ergebnis", "datum", "abgeschlossen_am", "creation"),
		)
	if k.angebote:
		k.nachfass = frappe.get_all(
			"ToDo",
			filters={
				"reference_type": "Quotation",
				"reference_name": ["in", [a.name for a in k.angebote]],
				"status": ["!=", "Cancelled"],
			},
			fields=["name", "status", "date", "modified"],
		)
	return k


def _zeilen(doctype: str, namen: list[str], felder) -> list[frappe._dict]:
	namen = _in(namen)
	if not namen or not doctype_da(doctype):
		return []
	filters = {"name": ["in", namen], "docstatus": ["<", 2]}
	return frappe.get_all(doctype, filters=filters, fields=_spalten(doctype, felder), order_by="creation asc")


def _termin(lead: frappe._dict) -> frappe._dict | None:
	felder = ["name", "starts_on", "status"]
	name = lead.get("cv_termin")
	if name:
		termin = frappe.db.get_value("Event", name, felder, as_dict=True)
		if termin and termin.status != "Cancelled":
			return termin
	events = frappe.get_all(
		"Event Participants",
		filters={"reference_doctype": "Lead", "reference_docname": lead.name, "parenttype": "Event"},
		pluck="parent",
	)
	if not events:
		return None
	termine = frappe.get_all(
		"Event",
		filters={"name": ["in", events], "status": ["!=", "Cancelled"]},
		fields=felder,
		order_by="starts_on desc",
		limit=1,
	)
	return termine[0] if termine else None


def _meister_des_termins(event: str) -> str | None:
	users = frappe.get_all(
		"ToDo",
		filters={"reference_type": "Event", "reference_name": event, "status": ["!=", "Cancelled"]},
		pluck="allocated_to",
		order_by="creation asc",
		limit=1,
	)
	return users[0] if users else None


def _zeitbuchungen(projekte: list[str]) -> int:
	if not projekte or not doctype_da("Timesheet Detail"):
		return 0
	zeilen = frappe.db.sql(
		"""select count(*) from `tabTimesheet Detail` d
		join `tabTimesheet` t on t.name = d.parent
		where d.project in %(projekte)s and t.docstatus < 2""",
		{"projekte": tuple(projekte)},
	)
	return cint(zeilen[0][0]) if zeilen else 0


def _kern_belege(doctype: str, projekte: list[str]) -> list[str]:
	"""Belege eines (späteren) Kern-Doctypes zum Projekt, Feld wird aus der Meta gelesen."""
	if not projekte or not doctype_da(doctype):
		return []
	for feld in ("projekt", "project"):
		if spalte_da(doctype, feld):
			return _namen(doctype, {feld: ["in", projekte]})
	if spalte_da(doctype, "referenz_doctype") and spalte_da(doctype, "referenz_name"):
		return _namen(doctype, {"referenz_doctype": "Project", "referenz_name": ["in", projekte]})
	return []


# --- Auswertung -----------------------------------------------------------------------------


def _schritt(key: str, status: str, text: str = "", belege=None, datum=None, betrieb: bool = False) -> dict:
	"""``betrieb``: der Betrieb ist am Zug, obwohl der Schritt schon begonnen hat (Entwurf)."""
	return {
		"key": key,
		"label": SCHRITT_LABEL[key],
		"status": status,
		"text": text,
		"belege": belege or [],
		"datum": datum,
		"betrieb": betrieb,
		"aktion": None,
	}


def _beleg(doctype: str, name: str, info: str = "") -> dict:
	return {"doctype": doctype, "name": name, "info": info}


def _datum(wert) -> str:
	return format_date(getdate(wert), "dd.MM.yyyy") if wert else ""


def _passendes(zeilen: list[frappe._dict], bedingung) -> frappe._dict | None:
	"""Neueste Zeile, die die Bedingung erfüllt - ohne Rückfall auf irgendeine Zeile. ``_neuestes``
	fällt auf die letzte Zeile zurück; dadurch bot die Leiste „Angebot annehmen“ für einen Entwurf
	an (Rückmeldung Mike 30.10.)."""
	passend = [z for z in zeilen or [] if bedingung(z)]
	return passend[-1] if passend else None


def _neuestes(zeilen: list[frappe._dict], bevorzugt=None) -> frappe._dict | None:
	if not zeilen:
		return None
	if bevorzugt:
		passend = [z for z in zeilen if bevorzugt(z)]
		if passend:
			return passend[-1]
	return zeilen[-1]


def schritte(k: frappe._dict, einst=None) -> list[dict]:
	"""Rohstatus aller acht Schritte (ohne Rechte und Aktionen)."""
	lead = k.lead
	jetzt = now_datetime()
	ergebnis: list[dict] = []

	# 1 Anfrage
	if lead:
		if lead.get("cv_status") == STATUS_ANFRAGE_VERLOREN:
			grund = lead.get("cv_verlustgrund")
			text = _("Verloren: {0}").format(grund) if grund else _("Verloren")
			ergebnis.append(_schritt("anfrage", VERLOREN, text, [_beleg("Lead", lead.name)], lead.creation))
		else:
			text = _("Anfrage vom {0}").format(_datum(lead.creation))
			ergebnis.append(_schritt("anfrage", ERLEDIGT, text, [_beleg("Lead", lead.name)], lead.creation))
	else:
		ergebnis.append(_schritt("anfrage", ENTFAELLT, _("ohne Anfrage")))

	# 2 Termin
	termin = k.termin
	if termin:
		beginn = get_datetime(termin.starts_on)
		text = beginn.strftime("%d.%m.%Y %H:%M")
		status = ERLEDIGT if beginn <= jetzt or termin.status == "Completed" else AKTIV
		if status == ERLEDIGT:
			text = _("Besichtigt am {0}").format(text)
		else:
			text = _("Termin am {0}").format(text)
			if k.meister:
				# D14: „Termin am 04.11.2026 09:30 mit Martin Meister“
				text += " " + _("mit {0}").format(frappe.utils.get_fullname(k.meister) or k.meister)
		ergebnis.append(_schritt("termin", status, text, [_beleg("Event", termin.name)], termin.starts_on))
	else:
		text = _("Noch kein Termin")
		if lead:
			# D14: Wunsch des Kunden aus der Webform, z. B. „Wunsch: Di 04.11. vormittags, sonst …“
			from corevatis_erpnext.planung.terminplanung import wunsch_text

			w = wunsch_text(lead)
			if w:
				text += " \u00b7 " + _("Wunsch: {0}").format(w)
		ergebnis.append(_schritt("termin", OFFEN if lead else ENTFAELLT, text))

	# 3 Befund/Aufmaß
	belege = [_beleg(OBJEKT, o.name, o.get("titel") or "") for o in k.objekte]
	belege += [_beleg(AUFMASS, a.name, a.get("status") or "") for a in k.aufmasse]
	abgeschlossen = [a for a in k.aufmasse if a.get("status") == AUFMASS_ABGESCHLOSSEN]
	befunde = _(" · {0} Befund(e)").format(k.befunde) if k.befunde else ""
	if abgeschlossen:
		status, text = ERLEDIGT, _("Aufmaß abgeschlossen") + befunde
	elif k.aufmasse:
		status, text = AKTIV, _("Aufmaß in Arbeit") + befunde
	elif k.objekte:
		status, text = AKTIV, _("Objekt angelegt, Aufmaß fehlt") + befunde
	elif not doctype_da(AUFMASS):
		status, text = OFFEN, _("Kern-App nicht installiert")
	else:
		status, text = OFFEN, _("Noch kein Objekt")
	if status == AKTIV and k.angebote:
		# ein Angebot gibt es schon: das Aufmaß hat seinen Zweck erfüllt
		status = ERLEDIGT
	datum = (abgeschlossen or k.aufmasse or k.objekte or [frappe._dict()])[-1].get("creation")
	ergebnis.append(_schritt("aufmass", status, text, belege, datum))

	# 4 Angebot
	angebot = _neuestes(
		k.angebote, lambda a: a.docstatus == 1 and a.status in ("Ordered", "Partially Ordered")
	) or _neuestes(k.angebote, lambda a: a.docstatus == 1 and a.status != "Lost")
	angebot = angebot or _neuestes(k.angebote)
	belege = [
		_beleg("Quotation", a.name, _(QUOTATION_STATUS_DE.get(a.status, a.status or ""))) for a in k.angebote
	]
	if not angebot:
		ergebnis.append(_schritt("angebot", OFFEN, _("Noch kein Angebot")))
	elif k.auftraege or angebot.status in ("Ordered", "Partially Ordered"):
		ergebnis.append(_schritt("angebot", ERLEDIGT, _("Angenommen"), belege, angebot.transaction_date))
	elif angebot.status == "Lost":
		ergebnis.append(_schritt("angebot", VERLOREN, _("Abgelehnt"), belege, angebot.transaction_date))
	elif angebot.docstatus == 0:
		ergebnis.append(
			_schritt(
				"angebot",
				AKTIV,
				_("Entwurf: prüfen und freigeben"),
				belege,
				angebot.creation,
				betrieb=True,
			)
		)
	else:
		text = _("Freigegeben am {0}, wartet auf Antwort des Kunden").format(_datum(angebot.transaction_date))
		ergebnis.append(_schritt("angebot", AKTIV, text, belege, angebot.transaction_date))

	# 5 Auftrag
	auftrag = _neuestes(k.auftraege, lambda a: a.docstatus == 1)
	belege = [_beleg("Sales Order", a.name) for a in k.auftraege]
	if not auftrag:
		ergebnis.append(_schritt("auftrag", OFFEN, _("Noch kein Auftrag")))
	elif auftrag.docstatus == 1:
		text = _("Auftrag vom {0}").format(_datum(auftrag.transaction_date))
		ergebnis.append(_schritt("auftrag", ERLEDIGT, text, belege, auftrag.transaction_date))
	else:
		ergebnis.append(
			_schritt("auftrag", AKTIV, _("Auftrag im Entwurf"), belege, auftrag.creation, betrieb=True)
		)

	# Abnahme (6.1): das zuletzt abgeschlossene Protokoll zählt (Nachabnahme nach Verweigerung)
	abgeschlossen = [a for a in k.abnahmen if a.get("status") == ABNAHME_ABGESCHLOSSEN]
	letzte_abnahme = max(abgeschlossen, key=lambda a: str(a.get("abgeschlossen_am") or ""), default=None)
	abgenommen = bool(letzte_abnahme) and letzte_abnahme.get("ergebnis") in ABNAHME_ERLEDIGT

	# 6 Ausführung
	belege = [_beleg("Project", p.name, p.get("project_name") or "") for p in k.projekte]
	projekt = _neuestes(k.projekte)
	if not projekt:
		ergebnis.append(_schritt("ausfuehrung", OFFEN, _("Noch kein Projekt")))
	else:
		# Rückmeldung 31.10.: „4 Arbeitspakete, 2 zugewiesen, 0 Zeitbuchungen“
		teile = [
			_("{0} Arbeitspaket(e)").format(k.tasks),
			_("{0} zugewiesen").format(k.zugewiesen),
			_("{0} Zeitbuchung(en)").format(k.zeiten),
		]
		if doctype_da(TAGESRAPPORT):
			teile.append(_("{0} Rapport(e)").format(k.rapporte))
		fertig = projekt.status == "Completed" or flt(projekt.percent_complete) >= 100 or abgenommen
		status = ERLEDIGT if fertig else AKTIV
		text = _("Ausgeführt") if fertig else ", ".join(teile)
		ergebnis.append(
			_schritt("ausfuehrung", status, text, belege, projekt.expected_start_date or projekt.creation)
		)

	# 7 Abnahme (Abnahmeprotokoll aus 6.1, Feld ``projekt``; löst den Projektstatus „Completed“ ab)
	belege = [_beleg(ABNAHMEPROTOKOLL, a.name, a.get("ergebnis") or _("Entwurf")) for a in k.abnahmen]
	entwurf = any(a.get("status") != ABNAHME_ABGESCHLOSSEN for a in k.abnahmen)
	if abgenommen:
		text = _("{0} am {1}").format(_(letzte_abnahme.ergebnis), _datum(letzte_abnahme.datum))
		ergebnis.append(_schritt("abnahme", ERLEDIGT, text, belege, letzte_abnahme.datum))
	elif entwurf:
		ergebnis.append(_schritt("abnahme", AKTIV, _("Abnahmeprotokoll in Arbeit"), belege, betrieb=True))
	elif letzte_abnahme:
		text = _("Abnahme verweigert am {0} - Nachabnahme nötig").format(_datum(letzte_abnahme.datum))
		ergebnis.append(_schritt("abnahme", AKTIV, text, belege, letzte_abnahme.datum, betrieb=True))
	else:
		ergebnis.append(_schritt("abnahme", OFFEN, _("Noch nicht abgenommen")))

	# 8 Rechnung
	belege = [_beleg("Sales Invoice", r.name, _("Entwurf") if r.docstatus == 0 else "") for r in k.rechnungen]
	gebucht = [r for r in k.rechnungen if r.docstatus == 1 and not r.is_return]
	abgerechnet = flt(auftrag.per_billed) if auftrag and auftrag.docstatus == 1 else 0
	if gebucht and (not auftrag or abgerechnet >= 100):
		ergebnis.append(_schritt("rechnung", ERLEDIGT, _("Abgerechnet"), belege))
	elif gebucht:
		text = _("Teilweise abgerechnet ({0} %)").format(cint(abgerechnet))
		ergebnis.append(_schritt("rechnung", AKTIV, text, belege))
	elif k.rechnungen:
		ergebnis.append(_schritt("rechnung", AKTIV, _("Rechnung im Entwurf"), belege))
	elif projekt and not abgenommen:
		ergebnis.append(_schritt("rechnung", OFFEN, _("Schlussrechnung nach der Abnahme (Abschlag möglich)")))
	else:
		ergebnis.append(_schritt("rechnung", OFFEN, _("Noch keine Rechnung")))

	# übersprungene Schritte: offen, obwohl es bis zum Auftrag schon weiter ging
	bis_auftrag = SCHRITT_KEYS.index("auftrag")
	for index, schritt in enumerate(ergebnis):
		if schritt["key"] not in UEBERSPRINGBAR or schritt["status"] != OFFEN:
			continue
		spaeter = ergebnis[index + 1 : bis_auftrag + 1]
		if any(s["status"] in (ERLEDIGT, AKTIV) for s in spaeter):
			schritt["status"] = ENTFAELLT
			schritt["text"] = _("übersprungen")
	return ergebnis


def aktueller_schritt(liste: list[dict]) -> str | None:
	for schritt in liste:
		if schritt["status"] in (OFFEN, AKTIV, VERLOREN):
			return schritt["key"]
	return None


def vorgang_status(liste: list[dict]) -> str:
	if any(s["status"] == VERLOREN for s in liste):
		return "verloren"
	return "offen" if aktueller_schritt(liste) else "abgeschlossen"


# --- Ampel ----------------------------------------------------------------------------------


def ampel(k: frappe._dict, liste: list[dict], einst) -> dict:
	"""Ampel des Vorgangs: rot = überfällig (Schwellen aus den Einstellungen), gelb = Betrieb ist
	am Zug, grün = läuft bzw. wartet auf Termin/Kunde, grau = abgeschlossen oder verloren."""
	status = {s["key"]: s["status"] for s in liste}
	aktuell = aktueller_schritt(liste)
	vstatus = vorgang_status(liste)
	if vstatus == "verloren":
		return {"farbe": "grau", "text": _("verloren"), "gruende": []}
	if vstatus == "abgeschlossen":
		return {"farbe": "grau", "text": _("abgeschlossen"), "gruende": []}

	gruende: list[str] = []
	jetzt = now_datetime()
	# Anfrage ohne Termin
	if aktuell == "termin" and status["termin"] == OFFEN and k.lead:
		tage = _tage_seit(k.lead.creation)
		if tage > einst.ampel_anfrage_ohne_termin_tage:
			gruende.append(_("Anfrage seit {0} Tagen ohne Termin").format(tage))
	# Termin ohne Aufmaß
	if k.termin and status["aufmass"] != ERLEDIGT and status["angebot"] in (OFFEN, ENTFAELLT):
		beginn = get_datetime(k.termin.starts_on)
		if beginn <= jetzt:
			tage = _tage_seit(beginn)
			if tage > einst.ampel_termin_ohne_aufmass_tage:
				gruende.append(_("Termin vor {0} Tagen, Aufmaß fehlt").format(tage))
	# Angebot ohne Nachfass
	if not k.auftraege:
		grenze = einst.ampel_angebot_ohne_nachfass_tage
		for angebot in k.angebote:
			if angebot.docstatus != 1 or angebot.status not in QUOTATION_OFFEN:
				continue
			tage = _tage_seit(angebot.transaction_date)
			if tage <= grenze:
				continue
			nachfass = [
				t
				for t in k.nachfass
				if t.status == "Open" or (t.status == "Closed" and _tage_seit(t.modified) <= grenze)
			]
			if not nachfass:
				gruende.append(_("Angebot seit {0} Tagen ohne Antwort und ohne Nachfassen").format(tage))
				break
	# Auftrag ohne Rapport
	auftrag = _neuestes(k.auftraege, lambda a: a.docstatus == 1)
	if auftrag and status["ausfuehrung"] != ERLEDIGT and not k.zeiten and not k.rapporte:
		projekt = _neuestes(k.projekte)
		start = (projekt and projekt.expected_start_date) or auftrag.transaction_date
		if start and getdate(start) <= getdate(today()):
			tage = _tage_seit(start)
			if tage > einst.ampel_auftrag_ohne_rapport_tage:
				gruende.append(_("Auftrag seit {0} Tagen ohne Zeitbuchung oder Rapport").format(tage))

	if gruende:
		return {"farbe": "rot", "text": gruende[0], "gruende": gruende}
	schritt = next(s for s in liste if s["key"] == aktuell)
	if schritt["status"] == OFFEN or schritt["betrieb"]:
		return {"farbe": "gelb", "text": _("{0}: Betrieb ist am Zug").format(schritt["label"]), "gruende": []}
	return {"farbe": "grün", "text": _("läuft"), "gruende": []}


def verantwortlich(k: frappe._dict, aktuell: str | None) -> str | None:
	if aktuell in ("termin", "aufmass") and k.meister:
		return k.meister
	if k.lead:
		# Web-Form-Anfragen gehören „Guest“: kein Verantwortlicher, bis das Büro einen setzt
		for user in (k.lead.get("lead_owner"), k.lead.get("owner")):
			if user and user not in ("Administrator", "Guest"):
				return user
		return None
	for zeilen in (k.auftraege, k.angebote):
		if zeilen:
			return zeilen[-1].get("owner")
	return None


# --- Aktionen -------------------------------------------------------------------------------


def _hat_rolle(rollen) -> bool:
	return frappe.session.user == "Administrator" or bool(set(rollen) & set(frappe.get_roles()))


def _darf(doctype: str, ptype: str, name: str | None = None) -> bool:
	if not doctype_da(doctype):
		return False
	try:
		return bool(
			frappe.has_permission(doctype, ptype, doc=name) if name else frappe.has_permission(doctype, ptype)
		)
	except (frappe.DoesNotExistError, frappe.PermissionError):
		return False


def _aktion(
	label: str, doctype: str | None = None, name: str | None = None, js: str | None = None, **extra
) -> dict:
	return {"label": label, "doctype": doctype, "name": name, "js": js, **extra}


def aktionen(k: frappe._dict, liste: list[dict]) -> None:
	"""Setzt ``aktion`` an den Schritten - nur mit den Rechten, die die Zielmethode prüft."""
	from corevatis_erpnext.anfrage.custom_fields import STATUS_VERLOREN
	from corevatis_erpnext.angebot.annahme import darf_annehmen, zerlege_anschrift
	from corevatis_erpnext.crm.termin import darf_termine

	schritt = {s["key"]: s for s in liste}
	lead = k.lead
	verloren = bool(lead) and lead.get("cv_status") == STATUS_VERLOREN
	lead_offen = bool(lead) and not verloren

	# Termin anlegen (1.3: Rollen Büro/Meister, Schreibrecht Lead)
	if (
		lead_offen
		and schritt["termin"]["status"] == OFFEN
		and darf_termine()
		and _darf("Lead", "write", lead.name)
	):
		schritt["termin"]["aktion"] = _aktion(
			_("Termin anlegen"), "Lead", lead.name, "corevatis_erpnext.termin.anlegen_dialog"
		)

	# Objekt / Aufmaß anlegen (2.1, Formular des Kerns)
	auf = schritt["aufmass"]
	if lead_offen and auf["status"] == OFFEN and doctype_da(OBJEKT) and _darf(OBJEKT, "create"):
		werte = {"kunde_doctype": "Lead", "kunde": lead.name}
		anschrift = zerlege_anschrift(lead.get("cv_objekt_adresse"))
		werte.update({f: anschrift.get(f) for f in ("strasse", "plz", "ort") if anschrift.get(f)})
		auf["aktion"] = _aktion(_("Objekt anlegen"), neu=OBJEKT, werte=werte)
	elif (
		not verloren and auf["status"] == AKTIV and k.objekte and not k.aufmasse and _darf(AUFMASS, "create")
	):
		werte = {"objekt": k.objekte[-1].name}
		if lead:
			werte.update({"referenz_doctype": "Lead", "referenz_name": lead.name})
		auf["aktion"] = _aktion(_("Aufmaß anlegen"), neu=AUFMASS, werte=werte)

	# Angebot erzeugen (4.1) / einreichen / annehmen (4.4)
	ang = schritt["angebot"]
	if ang["status"] == OFFEN and k.aufmasse and not verloren:
		aufmass = _neuestes(k.aufmasse, lambda a: a.get("status") == AUFMASS_ABGESCHLOSSEN)
		if _hat_rolle(ROLLEN_ANGEBOT) and _darf("Quotation", "create") and lesbar(AUFMASS, aufmass.name):
			ang["aktion"] = _aktion(
				_("Angebot erzeugen"), AUFMASS, aufmass.name, "corevatis_erpnext.angebot.erzeugen_dialog"
			)
	elif ang["status"] == AKTIV:
		entwurf = _passendes(k.angebote, lambda a: a.docstatus == 0)
		offen = _passendes(k.angebote, lambda a: a.docstatus == 1 and a.status in QUOTATION_OFFEN)
		# Ein neuer Entwurf (z. B. aus der Besichtigung) kommt zuerst: erst freigeben, dann kann der
		# Kunde annehmen (Durchstich, Rückmeldung 30.10.)
		if entwurf and _darf("Quotation", "submit", entwurf.name):
			ang["aktion"] = _aktion(
				_("Angebot freigeben"), "Quotation", entwurf.name, "corevatis_erpnext.prozess.freigeben"
			)
		elif offen and darf_annehmen() and lesbar("Quotation", offen.name):
			ang["aktion"] = _aktion(
				_("Vom Kunden angenommen"),
				"Quotation",
				offen.name,
				"corevatis_erpnext.angebot.annehmen_dialog",
			)

	# Auftrag einreichen
	auftrag_entwurf = _passendes(k.auftraege, lambda a: a.docstatus == 0)
	if (
		schritt["auftrag"]["status"] == AKTIV
		and auftrag_entwurf
		and _darf("Sales Order", "submit", auftrag_entwurf.name)
	):
		schritt["auftrag"]["aktion"] = _aktion(
			_("Auftrag einreichen"),
			"Sales Order",
			auftrag_entwurf.name,
			"corevatis_erpnext.prozess.einreichen",
		)

	# Ausführung (Rückmeldung 31.10., wie das Projektformular ``planung.projekt``): ohne Arbeitspakete
	# „Arbeitspakete anlegen“ (5.1 ``tasks_erzeugen``, Projekt mit Auftrag; sonst ein Paket von Hand),
	# Pakete ohne Zuweisung „Wochenplan öffnen“ (5.2), alle zugewiesen „Baustellenakte“
	projekt = _neuestes(k.projekte)
	if projekt and schritt["ausfuehrung"]["status"] == AKTIV:
		from corevatis_erpnext.planung import projekt as planung_projekt

		if not k.tasks:
			mit_auftrag = frappe.db.get_value("Project", projekt.name, "sales_order")
			key = "arbeitspakete_anlegen" if mit_auftrag else "arbeitspaket_anlegen"
		elif k.ohne_zuweisung:
			key = "wochenplan"
		else:
			key = "baustellenakte"
		if planung_projekt.aktion_erlaubt(key, projekt.name):
			label = _(planung_projekt.AKTIONEN[key])
			if key == "arbeitspakete_anlegen":
				aktion = _aktion(
					label, "Project", projekt.name, "corevatis_erpnext.prozess.arbeitspakete_erzeugen"
				)
			elif key == "arbeitspaket_anlegen":
				aktion = _aktion(label, neu="Task", werte={"project": projekt.name})
			elif key == "wochenplan":
				aktion = _aktion(
					label, route=["query-report", "Wochenplan"], route_options={"projekt": projekt.name}
				)
			else:
				aktion = _aktion(label, "Project", projekt.name, "corevatis_erpnext.planung.baustellenakte")
			schritt["ausfuehrung"]["aktion"] = aktion

	# Abnahmeprotokoll anlegen (6.1, Formular des Kerns; Meister/Büro mit Anlegerecht)
	abn = schritt["abnahme"]
	if (
		projekt
		and doctype_da(ABNAHMEPROTOKOLL)
		and not any(a.get("status") != ABNAHME_ABGESCHLOSSEN for a in k.abnahmen)
		and abn["status"] in (OFFEN, AKTIV)
		and _darf(ABNAHMEPROTOKOLL, "create")
		and lesbar("Project", projekt.name)
	):
		abn["aktion"] = _aktion(
			_("Abnahmeprotokoll anlegen"), neu=ABNAHMEPROTOKOLL, werte={"projekt": projekt.name}
		)

	# Rechnung erstellen (ERPNext „Erstellen → Rechnung“ am Auftrag)
	auftrag = _neuestes(k.auftraege, lambda a: a.docstatus == 1)
	if (
		auftrag
		and schritt["rechnung"]["status"] != ERLEDIGT
		and flt(auftrag.per_billed) < 100
		and _darf("Sales Invoice", "create")
		and lesbar("Sales Order", auftrag.name)
	):
		schritt["rechnung"]["aktion"] = _aktion(
			_("Rechnung erstellen"),
			"Sales Order",
			auftrag.name,
			"corevatis_erpnext.prozess.rechnung_aus_auftrag",
		)


# --- Ausgabe mit Rechten --------------------------------------------------------------------


def auswerten(k: frappe._dict, einst=None, mit_aktionen: bool = True) -> dict:
	from corevatis_erpnext.prozess.einstellungen import einstellungen

	einst = einst or einstellungen()
	liste = schritte(k, einst)
	if mit_aktionen:
		aktionen(k, liste)
	aktuell = aktueller_schritt(liste)
	ergebnis_ampel = ampel(k, liste, einst)

	for schritt in liste:
		alle = schritt["belege"]
		schritt["belege"] = [b for b in alle if lesbar(b["doctype"], b["name"])]
		schritt["gesperrt"] = bool(alle) and not schritt["belege"]
		if schritt["gesperrt"]:
			schritt["text"] = ""
			schritt["aktion"] = None
		schritt["aktuell"] = schritt["key"] == aktuell
		schritt.pop("datum", None)
		schritt.pop("betrieb", None)

	lead = k.lead
	lead_lesbar = bool(lead) and lesbar("Lead", lead.name)
	titel = None
	if lead_lesbar:
		titel = lead.get("lead_name") or lead.get("company_name") or lead.name
	aktuell_schritt = next((s for s in liste if s["key"] == aktuell), None)
	return {
		"vorgang": {
			"lead": lead.name if lead_lesbar else None,
			"titel": titel,
			"status": vorgang_status(liste),
		},
		"aktuell": aktuell,
		"schritte": liste,
		"naechste_aktion": aktuell_schritt["aktion"] if aktuell_schritt else None,
		"ampel": ergebnis_ampel
		if (lead_lesbar or not lead)
		else {"farbe": ergebnis_ampel["farbe"], "text": "", "gruende": []},
		"mappe": _mappe(k, lead_lesbar),
	}


def _mappe(k: frappe._dict, lead_lesbar: bool) -> dict | None:
	"""Dokumentenmappe (Epic D): Ampel-Hinweis und Knopf „Mappe“ in der Leiste. Aus dem Kern
	(``corevatis_erpnext.mappe.stand``); ``None`` ohne Kern oder ohne lesbaren Anker (Anfrage bzw.
	ein lesbares Projekt). Fehler der Mappe halten die Leiste nicht auf."""
	try:
		from corevatis_erpnext import mappe

		if not mappe.kern_da():
			return None
		if k.lead and lead_lesbar:
			return mappe.stand("Lead", k.lead.name)
		for projekt in k.projekte or []:
			if lesbar("Project", projekt.name):
				return mappe.stand("Project", projekt.name)
		return None
	except Exception:
		frappe.log_error(title="Prozessleiste: Mappe")
		return None


@frappe.whitelist()
def status(doctype: str, name: str) -> dict:
	"""Stand des Vorgangs zu einem Datensatz (Prozessleiste im Formularkopf).

	:param doctype: Lead, Objekt, Aufmass, Befund, Quotation, Sales Order, Project oder Sales Invoice
	:param name: Name des Datensatzes (Leserecht nötig)
	"""
	neuer_aufruf()
	if doctype not in STARTDOCTYPES or not doctype_da(doctype):
		frappe.throw(_("Für {0} gibt es keine Prozessleiste.").format(doctype), frappe.ValidationError)
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} nicht gefunden.").format(_(doctype), name), frappe.DoesNotExistError)
	frappe.has_permission(doctype, "read", doc=name, throw=True)
	neuer_aufruf()
	k = sammeln(lead_von(doctype, name), doctype, name)
	return auswerten(k)
