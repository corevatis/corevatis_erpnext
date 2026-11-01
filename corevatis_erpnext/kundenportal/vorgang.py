"""Daten des Vorgangs für die Kundenseite (P2.7): Termin, Angebot, Auftrag, Projekt, Rechnungen, Dokumente.

Die Belege liefert ``prozess.kette.sammeln`` (dieselbe Kette wie die Prozessleiste, ohne Rechteprüfung -
der Zugang ist die Berechtigung). Herausgegeben wird nur, was der Kunde ohnehin bekommt: sein Termin,
sein freigegebenes Angebot, seine Auftragsbestätigung, seine Rechnungen mit Betrag, Fälligkeit und
Zahlstand, die für ihn freigegebenen Dateien der Mappe. Keine Kalkulation, keine internen Notizen,
keine Mitarbeiterdaten außer dem Namen des Meisters am Termin.

Dokumente: Dateien an den freigegebenen Belegen des Vorgangs (Angebot, Auftragsbestätigung, Rechnung,
Abnahmeprotokoll), erkannt an der Dokumentart der Mappe (``cv_dokumentart``) oder - ohne Mappe - als
PDF am gebuchten Beleg; dazu alles mit Haken „Für den Kunden sichtbar“ (``File.cv_kunde_sichtbar``),
Kundenuploads eingeschlossen. Ersetzte Versionen (``cv_ersetzt``) und Unterschriften bleiben außen vor.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import (
	cint,
	cstr,
	flt,
	fmt_money,
	format_date,
	format_datetime,
	get_datetime,
	getdate,
	now_datetime,
	today,
)

from corevatis_erpnext.kundenportal import felder as F
from corevatis_erpnext.kundenportal.zugang import MAX_UPLOADS
from corevatis_erpnext.prozess import kette

AUTO_DOKUMENTARTEN = ("Angebot", "Auftragsbestätigung", "Abnahmeprotokoll", "Rechnung")
DOKUMENTART_UNTERSCHRIFT = "Unterschrift Kunde"
DOKUMENTART_KUNDENFOTO = "Kundenfoto"
ABNAHMEPROTOKOLL = "Abnahmeprotokoll"
WOCHENTAGE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
ANGEBOT_OFFEN = ("Open", "Replied")
ANGEBOT_ANGENOMMEN = ("Ordered", "Partially Ordered")


# --- Kette --------------------------------------------------------------------------------------------


def lead_des_zugangs(z) -> str | None:
	if z.anker_doctype == "Lead":
		return z.anker
	return kette.lead_von(z.anker_doctype, z.anker)


def kette_fuer(z) -> frappe._dict:
	kette.neuer_aufruf()
	lead = lead_des_zugangs(z)
	if z.anker_doctype == "Lead":
		return kette.sammeln(lead, "Lead", lead)
	return kette.sammeln(lead, z.anker_doctype, z.anker)


def _hat(doctype: str, feld: str) -> bool:
	return kette.spalte_da(doctype, feld)


# --- Firma und Objekt ---------------------------------------------------------------------------------


def firma(k: frappe._dict) -> str | None:
	for doctype, liste in (
		("Quotation", k.angebote),
		("Sales Order", k.auftraege),
		("Project", k.projekte),
		("Sales Invoice", k.rechnungen),
	):
		for z in liste or []:
			c = frappe.db.get_value(doctype, z.name, "company")
			if c:
				return c
	if k.lead and frappe.db.has_column("Lead", "company"):
		c = frappe.db.get_value("Lead", k.lead.name, "company")
		if c:
			return c
	return frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {}, "name")


def groesse_text(anzahl_bytes: int) -> str:
	b = cint(anzahl_bytes)
	if b >= 1024 * 1024:
		return f"{b / (1024 * 1024):.1f} MB".replace(".", ",")
	if b >= 1024:
		return f"{b // 1024} KB"
	return f"{b} B" if b else ""


def firma_daten(name: str | None) -> dict:
	if not name:
		return {}
	w = (
		frappe.db.get_value("Company", name, ["company_name", "phone_no", "email", "website"], as_dict=True)
		or {}
	)
	return {
		"name": name,
		"anzeige": cstr(w.get("company_name") or name),
		"telefon": cstr(w.get("phone_no")),
		"email": cstr(w.get("email")),
		"web": cstr(w.get("website")),
	}


def objekt_text(k: frappe._dict) -> str:
	from corevatis_erpnext.angebot.annahme import objekt_anschrift

	lead_doc = frappe.get_doc("Lead", k.lead.name) if k.lead else None
	angebot = frappe.get_doc("Quotation", k.angebote[-1].name) if k.angebote else None
	teile = objekt_anschrift(angebot, lead_doc) if (angebot or lead_doc) else {}
	if teile.get("strasse") or teile.get("ort"):
		ort = " ".join(filter(None, [teile.get("plz"), teile.get("ort")]))
		return ", ".join(filter(None, [teile.get("strasse"), ort]))
	if teile.get("text"):
		return teile["text"].replace("\n", ", ")
	for p in k.projekte or []:
		if p.get("project_name"):
			return cstr(p.project_name)
	return ""


# --- Termin -------------------------------------------------------------------------------------------


def termin_text(beginn) -> str:
	d = get_datetime(beginn)
	return f"{WOCHENTAGE[d.weekday()]} {d.strftime('%d.%m.%Y')}, {d.strftime('%H:%M')} Uhr"


def termin(k: frappe._dict) -> dict | None:
	t = k.termin
	if not t:
		return None
	felder = ["name", "subject", "starts_on", "ends_on", "status"]
	if _hat("Event", F.FELD_TERMIN_BESTAETIGT):
		felder.append(F.FELD_TERMIN_BESTAETIGT)
	e = frappe.db.get_value("Event", t.name, felder, as_dict=True)
	if not e or e.status == "Cancelled":
		return None
	beginn = get_datetime(e.starts_on)
	bestaetigt = e.get(F.FELD_TERMIN_BESTAETIGT)
	zukunft = beginn > now_datetime()
	return {
		"name": e.name,
		"beginn": e.starts_on,
		"text": termin_text(e.starts_on),
		"ende": e.ends_on and get_datetime(e.ends_on).strftime("%H:%M"),
		"meister": frappe.utils.get_fullname(k.meister) if k.meister else "",
		"bestaetigt": bestaetigt,
		"bestaetigt_text": format_datetime(bestaetigt, "dd.MM.yyyy HH:mm") if bestaetigt else "",
		"vorbei": not zukunft,
		"erledigt": e.status == "Completed",
		"bestaetigbar": zukunft and e.status == "Open" and not bestaetigt,
	}


# --- Angebot, Auftrag, Projekt ------------------------------------------------------------------------


def _geld(wert, waehrung) -> str:
	return fmt_money(flt(wert), currency=waehrung or "EUR")


def angebot(k: frappe._dict) -> dict | None:
	"""Das maßgebliche Angebot: ein offenes (neuestes), sonst das angenommene, sonst das letzte freigegebene."""
	freigegeben = [a for a in (k.angebote or []) if a.docstatus == 1]
	if not freigegeben:
		return None
	offen = [a for a in freigegeben if a.status in ANGEBOT_OFFEN]
	angenommen = [a for a in freigegeben if a.status in ANGEBOT_ANGENOMMEN]
	zeile = (offen or angenommen or freigegeben)[-1]
	felder = [
		"name",
		"status",
		"transaction_date",
		"valid_till",
		"net_total",
		"grand_total",
		"currency",
		"customer_name",
	]
	for f in ("cv_titel", F.FELD_ANGEBOT_ANGENOMMEN, F.FELD_ANGEBOT_UNTERSCHRIFT):
		if _hat("Quotation", f):
			felder.append(f)
	a = frappe.db.get_value("Quotation", zeile.name, felder, as_dict=True)
	if not a:
		return None
	if a.status in ANGEBOT_ANGENOMMEN:
		status = "angenommen"
	elif a.status == "Lost":
		status = "abgelehnt"
	elif a.status == "Expired" or (a.valid_till and getdate(a.valid_till) < getdate(today())):
		status = "abgelaufen"
	elif a.status in ANGEBOT_OFFEN:
		status = "offen"
	else:
		status = "sonstig"
	angenommen_am = a.get(F.FELD_ANGEBOT_ANGENOMMEN)
	return {
		"name": a.name,
		"titel": cstr(a.get("cv_titel") or ""),
		"datum": format_date(a.transaction_date),
		"gueltig_bis": format_date(a.valid_till) if a.valid_till else "",
		"netto": _geld(a.net_total, a.currency),
		"brutto": _geld(a.grand_total, a.currency),
		"status": status,
		"annehmbar": status == "offen",
		"angenommen_am": format_datetime(angenommen_am, "dd.MM.yyyy HH:mm") if angenommen_am else "",
		"unterschrift": bool(a.get(F.FELD_ANGEBOT_UNTERSCHRIFT)),
	}


def auftrag(k: frappe._dict) -> dict | None:
	eingereicht = [a for a in (k.auftraege or []) if a.docstatus == 1]
	if not eingereicht:
		return None
	a = eingereicht[-1]
	w = frappe.db.get_value("Sales Order", a.name, ["delivery_date", "grand_total", "currency"], as_dict=True)
	return {
		"name": a.name,
		"datum": format_date(a.transaction_date),
		"ende": format_date(w.delivery_date) if w and w.delivery_date else "",
		"brutto": _geld(w.grand_total, w.currency) if w else "",
	}


PROJEKT_STATUS = {"Open": "in Ausführung", "Completed": "abgeschlossen", "Cancelled": "abgebrochen"}


def projekt(k: frappe._dict) -> dict | None:
	if not k.projekte:
		return None
	p = k.projekte[-1]
	w = frappe.db.get_value("Project", p.name, ["expected_end_date"], as_dict=True) or {}
	return {
		"name": p.name,
		"status": PROJEKT_STATUS.get(cstr(p.status), cstr(p.status)),
		"start": format_date(p.expected_start_date) if p.expected_start_date else "",
		"ende": format_date(w.get("expected_end_date")) if w.get("expected_end_date") else "",
		"fortschritt": cint(round(flt(p.percent_complete))),
		"abgenommen": bool(k.abnahmen),
	}


# --- Rechnungen ---------------------------------------------------------------------------------------


def rechnung_art(r) -> str:
	if cint(r.get("is_return")):
		return _("Gutschrift")
	if cint(r.get("cv_abschlag")):
		nr = cint(r.get("cv_abschlag_nr"))
		return _("Abschlagsrechnung {0}").format(nr) if nr else _("Abschlagsrechnung")
	if cint(r.get("cv_schlussrechnung")):
		return _("Schlussrechnung")
	return _("Rechnung")


def rechnung_stand(r) -> tuple[str, str]:
	"""(Text, Farbe) zum Zahlstand: bezahlt, teilweise bezahlt, offen, überfällig."""
	offen = flt(r.get("outstanding_amount"))
	brutto = flt(r.get("grand_total"))
	if cint(r.get("is_return")):
		return _("verrechnet"), "grau"
	if offen <= 0.005:
		return _("bezahlt"), "gruen"
	if r.get("due_date") and getdate(r.due_date) < getdate(today()):
		return _("überfällig"), "rot"
	if brutto and offen < brutto - 0.005:
		return _("teilweise bezahlt"), "gelb"
	return _("offen"), "gelb"


def rechnungen(k: frappe._dict) -> list[dict]:
	ergebnis = []
	for r in sorted(
		(r for r in (k.rechnungen or []) if r.docstatus == 1),
		key=lambda r: (cstr(r.posting_date), cstr(r.creation)),
	):
		stand, farbe = rechnung_stand(r)
		ergebnis.append(
			{
				"name": r.name,
				"art": rechnung_art(r),
				"datum": format_date(r.posting_date),
				"faellig": format_date(r.due_date) if r.get("due_date") else "",
				"brutto": _geld(r.grand_total, r.currency),
				"offen": _geld(r.outstanding_amount, r.currency),
				"stand": stand,
				"farbe": farbe,
			}
		)
	return ergebnis


# --- Dokumente ----------------------------------------------------------------------------------------


def mappe_felder_da() -> bool:
	return _hat("File", "cv_dokumentart")


def belege_mit_dateien(k: frappe._dict) -> dict[str, list[str]]:
	"""Freigegebene Belege des Vorgangs, an denen Dateien für den Kunden liegen können."""
	b: dict[str, list[str]] = {
		"Quotation": [a.name for a in (k.angebote or []) if a.docstatus == 1],
		"Sales Order": [a.name for a in (k.auftraege or []) if a.docstatus == 1],
		"Sales Invoice": [r.name for r in (k.rechnungen or []) if r.docstatus == 1],
	}
	if k.abnahmen and kette.doctype_da(ABNAHMEPROTOKOLL):
		b[ABNAHMEPROTOKOLL] = [
			a.name for a in k.abnahmen if cstr(a.get("status")) == kette.ABNAHME_ABGESCHLOSSEN
		]
	if k.lead:
		b["Lead"] = [k.lead.name]
	b["Project"] = [p.name for p in (k.projekte or [])]
	return {dt: namen for dt, namen in b.items() if namen}


def _datei_felder() -> list[str]:
	felder = [
		"name",
		"file_name",
		"file_url",
		"file_size",
		"is_private",
		"creation",
		"attached_to_doctype",
		"attached_to_name",
	]
	for f in ("cv_dokumentart", "cv_ersetzt", "cv_ordner", F.FELD_DATEI_SICHTBAR):
		if _hat("File", f):
			felder.append(f)
	return felder


def _ist_pdf(f) -> bool:
	return cstr(f.file_name or f.file_url).lower().endswith(".pdf")


def dokumente(k: frappe._dict) -> list[dict]:
	b = belege_mit_dateien(k)
	if not b:
		return []
	felder = _datei_felder()
	mappe = mappe_felder_da()
	sichtbar_feld = _hat("File", F.FELD_DATEI_SICHTBAR)
	alle: list[frappe._dict] = []
	for doctype, namen in b.items():
		alle += frappe.get_all(
			"File",
			filters={"attached_to_doctype": doctype, "attached_to_name": ["in", namen], "is_folder": 0},
			fields=felder,
		)
	ersetzt = {f.get("cv_ersetzt") for f in alle if f.get("cv_ersetzt")}
	ergebnis = []
	for f in alle:
		if f.name in ersetzt:
			continue
		art = cstr(f.get("cv_dokumentart"))
		freigegeben = bool(sichtbar_feld and cint(f.get(F.FELD_DATEI_SICHTBAR)))
		beleg = f.attached_to_doctype in ("Quotation", "Sales Order", "Sales Invoice", ABNAHMEPROTOKOLL)
		if mappe:
			automatisch = beleg and art in AUTO_DOKUMENTARTEN
		else:
			automatisch = beleg and _ist_pdf(f)
		if art == DOKUMENTART_UNTERSCHRIFT and not freigegeben:
			continue
		if not (freigegeben or automatisch):
			continue
		ergebnis.append(
			{
				"name": f.name,
				"titel": cstr(f.file_name or f.file_url).rsplit("/", 1)[-1],
				"art": art or (_("PDF") if _ist_pdf(f) else _("Datei")),
				"beleg": f.attached_to_name,
				"datum": format_date(f.creation),
				"groesse": groesse_text(f.file_size),
				"bild": cstr(f.file_name or f.file_url)
				.lower()
				.endswith((".jpg", ".jpeg", ".png", ".heic", ".heif")),
			}
		)
	ergebnis.sort(key=lambda d: d["name"])
	ergebnis.sort(key=lambda d: cstr(d["datum"]), reverse=True)
	return ergebnis


def datei_erlaubt(k: frappe._dict, datei: str) -> bool:
	"""Darf die Datei über den Zugang ausgeliefert werden? (dieselbe Liste wie auf der Seite)"""
	return any(d["name"] == datei for d in dokumente(k))


def beleg_pdf_datei(doctype: str, name: str, dokumentart: str) -> str | None:
	"""Aktuelle Mappendatei (PDF der Freigabe) am Beleg, sonst ``None``."""
	if not mappe_felder_da():
		dateien = frappe.get_all(
			"File",
			filters={"attached_to_doctype": doctype, "attached_to_name": name, "is_folder": 0},
			fields=["name", "file_name", "file_url"],
			order_by="creation desc",
		)
		pdfs = [d.name for d in dateien if _ist_pdf(d)]
		return pdfs[0] if pdfs else None
	dateien = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": doctype,
			"attached_to_name": name,
			"is_folder": 0,
			"cv_dokumentart": dokumentart,
		},
		fields=["name", "cv_ersetzt"],
		order_by="creation desc",
	)
	ersetzt = {d.cv_ersetzt for d in dateien if d.cv_ersetzt}
	aktuell = [d.name for d in dateien if d.name not in ersetzt]
	return aktuell[0] if aktuell else None


# --- Begrüßung (Textbaustein) -------------------------------------------------------------------------


def begruessung(k: frappe._dict, firma_name: str | None, kunde: str) -> str:
	"""Begleittext der Seite aus dem Textbaustein der Art „Kundenportal“ (P2.6), Platzhalter ersetzt;
	ohne Baustein ein fester Satz."""
	from corevatis_erpnext.textbausteine import felder as tb_felder
	from corevatis_erpnext.textbausteine import platzhalter, text

	try:
		roh = text(tb_felder.ART_KUNDENPORTAL, firma=firma_name)
	except Exception:
		roh = ""
	if not roh:
		return ""
	kontext = {
		"Anrede": platzhalter.ANREDE_NEUTRAL,
		"Kunde": kunde,
		"Firma": cstr(frappe.get_cached_value("Company", firma_name, "company_name")) if firma_name else "",
		"Objekt": objekt_text(k),
		"Heute": format_date(today()),
	}
	if k.angebote:
		kontext["Angebot"] = k.angebote[-1].name
	if k.auftraege:
		kontext["Auftrag"] = k.auftraege[-1].name
	if k.projekte:
		kontext["Vorgang"] = k.projekte[-1].name
	return platzhalter.ersetzen(roh, kontext, html=True)


# --- Alles für die Seite ------------------------------------------------------------------------------


def daten(z) -> dict:
	k = kette_fuer(z)
	f = firma(k)
	kunde = cstr(z.kunde_name) or (k.lead.lead_name if k.lead else "")
	return {
		"zugang": z.name,
		"kunde": kunde,
		"firma": firma_daten(f),
		"objekt": objekt_text(k),
		"anfrage": k.lead.name if k.lead else None,
		"termin": termin(k),
		"angebot": angebot(k),
		"auftrag": auftrag(k),
		"projekt": projekt(k),
		"rechnungen": rechnungen(k),
		"dokumente": dokumente(k),
		"uploads": cint(z.get("uploads")),
		"uploads_frei": max(0, MAX_UPLOADS - cint(z.get("uploads"))),
		"begruessung": begruessung(k, f, kunde),
		"gueltig_bis": format_date(z.gueltig_bis),
	}
