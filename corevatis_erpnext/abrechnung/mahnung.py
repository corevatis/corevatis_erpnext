"""Mahnwesen (7.5, Handwerker#77) auf ERPNext ``Dunning`` in Handwerkersprache.

- Drei **Mahntypen** je Firma (``Dunning Type``, ERPNext benennt sie „<Stufe> - <Kürzel>“):
  Zahlungserinnerung (Stufe 0, keine Gebühr), 1. Mahnung (Stufe 1), 2. Mahnung (Stufe 2) mit
  Mahngebühr aus den Einstellungen; Brieftexte aus ``abrechnung/texte/mahnung_<stufe>_*.html``
  (Textbausteine wie 4.3: angelegt, wenn der Typ fehlt, nie überschrieben - Anpassungen im Desk bleiben).
  ``ensure_mahntypen`` läuft bei Install/Migrate; die Gebühr der Mahnung kommt beim Erstellen immer
  frisch aus den Einstellungen.
- **Vorschlag** (``vorschlag``): Zahlungserinnerung ``mahnfrist_erinnerung`` Tage nach Fälligkeit (7),
  1. Mahnung ``mahnfrist_mahnung1`` Tage nach der Erinnerung (14), 2. Mahnung ``mahnfrist_mahnung2``
  Tage nach der 1. Mahnung (14); danach „Inkasso oder Rechtsweg prüfen“. Grundlage sind die gebuchten
  Mahnungen der Rechnung (``offene_posten.mahnstand``).
- **Mahnen** (``mahnung_erstellen``): ERPNexts ``create_dunning`` aus der Rechnung (überfällige
  Zahlungsplanzeilen), Mahntyp der Stufe, Gebühr, Ertragskonto, Zahlungsfrist (``cv_zahlungsfrist``),
  Brieftext, dann gebucht (Submit). Eine andere Stufe als vorgeschlagen oder vor Ablauf der Frist nur
  mit ``bestaetigen=1``. Kein Versand (kein SMTP): das Büro druckt „Mahnung Corevatis“.

Rechte: nur Büro (und System Manager), ERPNext-Rechte an Dunning (Büro bekommt per Custom DocPerm
Submit/Cancel); Beträge nur aus den Rechnungen und den Einstellungen.
"""

from __future__ import annotations

from pathlib import Path

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, fmt_money, format_date, getdate, today

from corevatis_erpnext.abrechnung.auftrag import pruefe_rolle
from corevatis_erpnext.abrechnung.einstellungen import einstellungen
from corevatis_erpnext.abrechnung.kern import AbrechnungFehler
from corevatis_erpnext.abrechnung.offene_posten import RECHNUNG_FELDER, mahnstand

STUFEN = ((0, "Zahlungserinnerung"), (1, "1. Mahnung"), (2, "2. Mahnung"))
LABELS = dict(STUFEN)
LETZTE_STUFE = 2
SPRACHE = "de"
TEXTE = Path(__file__).resolve().parent / "texte"
# Ertragskonto für Mahngebühren, wenn in den Einstellungen keines steht: „sonstige Zinsen und ähnliche
# Erträge“ (SKR03 2650, SKR04 7100), sonst das Standard-Erlöskonto der Firma
GEBUEHR_KONTONUMMERN = ("2650", "7100")
# Rechte an ERPNext-Doctypes, die die Rolle Büro (Rollenprofil Accounts User) nicht mitbringt
RECHTE_BUERO = {
	"Dunning": ("read", "write", "create", "submit", "cancel", "amend", "print", "email", "report"),
	"Dunning Type": ("read",),
}
# Der Meister liest Rechnungen (Offene Posten sind ein Bericht über Sales Invoice, Rechnungsdruck 7.3);
# ohne diese Rechte sah er auf mvp-test weder die Liste noch das Rechnungsformular (Abnahme Build 27)
RECHTE_MEISTER = {"Sales Invoice": ("read", "print", "report")}
RECHTE = {"Büro": RECHTE_BUERO, "Meister": RECHTE_MEISTER}


class MahnungFehler(AbrechnungFehler):
	pass


# --- Mahntypen ----------------------------------------------------------------------------------


def _abbr(company: str) -> str:
	return frappe.get_cached_value("Company", company, "abbr") or company


def typ_label(stufe: int, company: str) -> str:
	"""Bezeichnung des Mahntyps (``Dunning Type.dunning_type``). ERPNext erzwingt sie firmenübergreifend
	eindeutig, darum mit Firmenkürzel; der Brief zeigt die Stufe ohne Kürzel (über ``cv_mahnstufe``)."""
	return f"{LABELS[stufe]} ({_abbr(company)})"


def typ_name(stufe: int, company: str) -> str:
	"""Name, wie ERPNext ihn vergibt: Bezeichnung plus Firmenkürzel."""
	return f"{typ_label(stufe, company)} - {_abbr(company)}"


def vorhandener_typ(stufe: int, company: str) -> str | None:
	"""Name des vorhandenen Mahntyps der Firma für die Stufe: aktueller Name, sonst über ``cv_mahnstufe``
	(ältere Anlage ohne Kürzel in der Bezeichnung), sonst None."""
	name = typ_name(stufe, company)
	if frappe.db.exists("Dunning Type", name):
		return name
	if frappe.get_meta("Dunning Type").has_field("cv_mahnstufe"):
		return frappe.db.get_value("Dunning Type", {"company": company, "cv_mahnstufe": stufe})
	return None


def gebuehr(stufe: int, e: frappe._dict | None = None) -> float:
	e = e or einstellungen()
	if stufe == 1:
		return flt(e.mahngebuehr_mahnung1, 2)
	if stufe == 2:
		return flt(e.mahngebuehr_mahnung2, 2)
	return 0.0


def gebuehr_konto(company: str, e: frappe._dict | None = None) -> str | None:
	e = e or einstellungen()
	if e.mahngebuehr_konto and frappe.db.get_value("Account", e.mahngebuehr_konto, "company") == company:
		return e.mahngebuehr_konto
	for nummer in GEBUEHR_KONTONUMMERN:
		konto = frappe.db.get_value(
			"Account", {"company": company, "account_number": nummer, "is_group": 0, "disabled": 0}
		)
		if konto:
			return konto
	return frappe.get_cached_value("Company", company, "default_income_account")


def brieftext(stufe: int) -> tuple[str, str]:
	return (
		(TEXTE / f"mahnung_{stufe}_text.html").read_text(encoding="utf-8").strip(),
		(TEXTE / f"mahnung_{stufe}_schluss.html").read_text(encoding="utf-8").strip(),
	)


def ensure_mahntypen(company: str | None = None) -> list[str]:
	"""Mahntypen je Firma anlegen, wenn sie fehlen (idempotent); vorhandene bleiben unverändert,
	nur ein fehlendes ``cv_mahnstufe`` wird nachgetragen. Rückgabe: angelegte Namen.

	Die Bezeichnung trägt das Firmenkürzel, weil ERPNext ``dunning_type`` über alle Firmen eindeutig
	verlangt (auf mvp-test mit zwei Firmen schlug die Migration sonst fehl)."""
	if not frappe.db.exists("DocType", "Dunning Type"):
		return []
	firmen = [company] if company else frappe.get_all("Company", pluck="name")
	e = einstellungen()
	sprache = SPRACHE if frappe.db.exists("Language", SPRACHE) else (frappe.local.lang or "en")
	mit_stufe = frappe.get_meta("Dunning Type").has_field("cv_mahnstufe")
	angelegt = []
	for firma in firmen:
		for stufe in LABELS:
			name = vorhandener_typ(stufe, firma)
			if name:
				if mit_stufe and frappe.db.get_value("Dunning Type", name, "cv_mahnstufe") is None:
					frappe.db.set_value("Dunning Type", name, "cv_mahnstufe", stufe, update_modified=False)
				continue
			text, schluss = brieftext(stufe)
			fee = gebuehr(stufe, e)
			doc = frappe.get_doc(
				{
					"doctype": "Dunning Type",
					"dunning_type": typ_label(stufe, firma),
					"company": firma,
					"dunning_fee": fee,
					"rate_of_interest": 0,
					"is_default": 1 if stufe == 0 else 0,
					"income_account": gebuehr_konto(firma, e) if fee else None,
					"cost_center": frappe.get_cached_value("Company", firma, "cost_center") if fee else None,
					"dunning_letter_text": [
						{
							"language": sprache,
							"is_default_language": 1,
							"body_text": text,
							"closing_text": schluss,
						}
					],
				}
			)
			if mit_stufe:
				doc.cv_mahnstufe = stufe
			doc.insert(ignore_permissions=True)
			angelegt.append(doc.name)
	return angelegt


def mahntyp(stufe: int, company: str) -> str:
	name = vorhandener_typ(stufe, company)
	if not name:
		ensure_mahntypen(company)
		name = vorhandener_typ(stufe, company) or typ_name(stufe, company)
	return name


def ensure_rechte() -> None:
	"""Rolle Büro: Mahnungen buchen (ERPNext gibt Accounts User kein Submit an Dunning) und Mahntypen lesen.
	Rolle Meister: Rechnungen lesen, drucken und als Bericht (Offene Posten) sehen."""
	from frappe.permissions import add_permission, update_permission_property

	for rolle, je_doctype in RECHTE.items():
		if not frappe.db.exists("Role", rolle):
			continue
		for doctype, rechte in je_doctype.items():
			if not frappe.db.exists("DocType", doctype):
				continue
			add_permission(doctype, rolle, 0)
			for recht in rechte:
				if not frappe.db.get_value(
					"Custom DocPerm", {"parent": doctype, "role": rolle, "permlevel": 0}, recht
				):
					update_permission_property(doctype, rolle, 0, recht, 1, validate=False)
	frappe.clear_cache()


# --- Vorschlag ----------------------------------------------------------------------------------


def vorschlag(rechnung, stand: frappe._dict | None, heute=None, e: frappe._dict | None = None) -> dict:
	"""Nächste Mahnstufe für eine gebuchte, offene Rechnung.

	Rückgabe: ``stufe`` (0/1/2 oder ``None``), ``label``, ``moeglich`` (Frist abgelaufen), ``grund``
	(Klartext), ``tage_ueberfaellig``, ``ab`` (Datum, ab dem die Stufe fällig ist), ``gebuehr``."""
	e = e or einstellungen()
	heute = getdate(heute or today())
	ergebnis = {
		"stufe": None,
		"label": None,
		"moeglich": False,
		"grund": "",
		"tage_ueberfaellig": 0,
		"ab": None,
		"gebuehr": 0.0,
		"letzte_stufe": stand.stufe if stand else None,
	}
	if flt(rechnung.get("outstanding_amount")) <= 0 or cint(rechnung.get("docstatus")) != 1:
		ergebnis["grund"] = _("bezahlt")
		return ergebnis
	if not rechnung.get("due_date"):
		ergebnis["grund"] = _("ohne Fälligkeit")
		return ergebnis
	tage = cint(date_diff(heute, rechnung.due_date))
	ergebnis["tage_ueberfaellig"] = max(tage, 0)
	if tage <= 0:
		ergebnis["grund"] = _("fällig am {0}").format(format_date(rechnung.due_date))
		return ergebnis
	letzte = stand.stufe if stand else None
	if letzte is None:
		naechste, frist, ab = 0, e.mahnfrist_erinnerung, getdate(rechnung.due_date)
	elif letzte == 0:
		naechste, frist, ab = 1, e.mahnfrist_mahnung1, getdate(stand.datum)
	elif letzte == 1:
		naechste, frist, ab = 2, e.mahnfrist_mahnung2, getdate(stand.datum)
	else:
		ergebnis["grund"] = _("2. Mahnung vom {0} - Inkasso oder Rechtsweg prüfen").format(
			format_date(stand.datum)
		)
		return ergebnis
	ergebnis.update(
		stufe=naechste,
		label=LABELS[naechste],
		gebuehr=gebuehr(naechste, e),
		ab=add_days(ab, frist),
	)
	ueberfaellig = _("{0} Tage überfällig").format(tage)
	if letzte is not None:
		ueberfaellig += ", " + _("{0} vom {1}").format(LABELS[letzte], format_date(stand.datum))
	if cint(date_diff(heute, ab)) >= frist:
		ergebnis["moeglich"] = True
		ergebnis["grund"] = ueberfaellig
	else:
		ergebnis["grund"] = (
			ueberfaellig + " - " + _("{0} ab {1}").format(LABELS[naechste], format_date(ergebnis["ab"]))
		)
	return ergebnis


def _rechnung(sales_invoice: str) -> frappe._dict:
	frappe.has_permission("Sales Invoice", "read", sales_invoice, throw=True)
	felder = [*RECHNUNG_FELDER, "letter_head"]
	if frappe.db.has_column("Sales Invoice", "cv_schlussrechnung"):
		felder += ["cv_auftrag", "cv_abschlag", "cv_abschlag_nr", "cv_schlussrechnung"]
	r = frappe.db.get_value("Sales Invoice", sales_invoice, felder, as_dict=True)
	if not r:
		frappe.throw(_("Rechnung {0} nicht gefunden.").format(sales_invoice), frappe.DoesNotExistError)
	return r


@frappe.whitelist()
def mahnvorschlag(sales_invoice: str) -> dict:
	"""Vorschlag für den Dialog „Mahnen“: Rechnung, Stand, nächste Stufe, alle Stufen mit Gebühr."""
	pruefe_rolle()
	r = _rechnung(sales_invoice)
	e = einstellungen()
	stand = mahnstand([r.name]).get(r.name)
	v = vorschlag(r, stand, None, e)
	return {
		"rechnung": {
			"name": r.name,
			"kunde": r.customer_name,
			"datum": r.posting_date,
			"faellig": r.due_date,
			"brutto": flt(r.grand_total),
			"offen": flt(r.outstanding_amount),
			"currency": r.currency,
			"projekt": r.project,
		},
		"stand": {
			"stufe": stand.stufe,
			"label": LABELS.get(stand.stufe),
			"mahnung": stand.mahnung,
			"datum": stand.datum,
			"status": stand.status,
			"anzahl": stand.anzahl,
		}
		if stand
		else None,
		"vorschlag": v,
		"stufen": [{"stufe": s, "label": l, "gebuehr": gebuehr(s, e)} for s, l in STUFEN],
		"zahlungsfrist_tage": e.mahnung_zahlungsfrist,
		"zahlungsfrist": add_days(today(), e.mahnung_zahlungsfrist),
	}


# --- Mahnen -------------------------------------------------------------------------------------


def _kommentar(r: frappe._dict, text: str) -> None:
	for doctype, name in (("Project", r.get("project")), ("Sales Invoice", r.name)):
		if name and frappe.db.exists(doctype, name):
			frappe.get_doc(doctype, name).add_comment("Comment", text)


@frappe.whitelist(methods=["POST"])
def mahnung_erstellen(sales_invoice: str, stufe=None, bestaetigen=0) -> dict:
	"""Mahnung (Dunning) zur Rechnung anlegen und buchen.

	:param stufe: 0 Zahlungserinnerung, 1/2 Mahnung; leer = vorgeschlagene Stufe
	:param bestaetigen: 1 = auch vor Ablauf der Frist bzw. mit anderer Stufe als vorgeschlagen
	"""
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import create_dunning

	pruefe_rolle()
	frappe.has_permission("Dunning", "create", throw=True)
	r = _rechnung(sales_invoice)
	if r.docstatus != 1 or cint(r.is_return):
		frappe.throw(_("Nur gebuchte Rechnungen (keine Gutschriften) können gemahnt werden."), MahnungFehler)
	if flt(r.outstanding_amount) <= 0:
		frappe.throw(_("Rechnung {0} ist bezahlt.").format(r.name), MahnungFehler)
	if not r.due_date or getdate(r.due_date) >= getdate(today()):
		frappe.throw(
			_("Rechnung {0} ist noch nicht überfällig (fällig am {1}).").format(
				r.name, format_date(r.due_date)
			),
			MahnungFehler,
		)
	e = einstellungen()
	stand = mahnstand([r.name]).get(r.name)
	v = vorschlag(r, stand, None, e)
	if stufe in (None, ""):
		stufe = v["stufe"]
	stufe = cint(stufe)
	if stufe not in LABELS:
		frappe.throw(_("Mahnstufe 0 (Zahlungserinnerung), 1 oder 2 angeben."), frappe.ValidationError)
	if not cint(bestaetigen):
		if v["stufe"] is None:
			frappe.throw(v["grund"] or _("Keine Mahnung vorgeschlagen."), MahnungFehler, title=_("Mahnen"))
		if stufe != v["stufe"] or not v["moeglich"]:
			frappe.throw(
				_(
					"Vorgeschlagen ist {0} ({1}). Eine andere Stufe oder eine Mahnung vor der Frist bitte ausdrücklich bestätigen."
				).format(v["label"], v["grund"]),
				MahnungFehler,
				title=_("Mahnen"),
			)
	doc = create_dunning(r.name)
	fee = gebuehr(stufe, e)
	doc.dunning_type = mahntyp(stufe, r.company)
	doc.dunning_fee = fee
	doc.rate_of_interest = 0
	doc.income_account = gebuehr_konto(r.company, e) if fee else None
	doc.cost_center = frappe.get_cached_value("Company", r.company, "cost_center") if fee else None
	if fee and not doc.income_account:
		frappe.throw(
			_(
				"Für die Mahngebühr fehlt ein Ertragskonto - bitte in den Abrechnungs-Einstellungen eintragen."
			),
			MahnungFehler,
		)
	if frappe.db.exists("Language", SPRACHE):
		doc.language = SPRACHE
	doc.letter_head = r.get("letter_head") or frappe.get_cached_value(
		"Company", r.company, "default_letter_head"
	)
	if doc.meta.has_field("cv_mahnstufe"):
		doc.cv_mahnstufe = stufe
		doc.cv_zahlungsfrist = add_days(today(), e.mahnung_zahlungsfrist)
		doc.cv_projekt = r.project
	doc.get_dunning_letter_text()
	# Textbausteine (P2.6): Platzhalter des Mahntexts mit den Werten der Mahnung füllen
	from corevatis_erpnext.textbausteine import platzhalter

	kontext = platzhalter.kontext_aus(doc)
	doc.body_text = platzhalter.ersetzen(doc.body_text, kontext)
	doc.closing_text = platzhalter.ersetzen(doc.closing_text, kontext)
	doc.insert()
	doc.submit()
	text = _("{0} {1} erstellt: offen {2}, Mahngebühr {3}, zahlbar bis {4}.").format(
		LABELS[stufe],
		doc.name,
		fmt_money(flt(doc.total_outstanding), currency=r.currency),
		fmt_money(fee, currency=r.currency),
		format_date(doc.get("cv_zahlungsfrist") or add_days(today(), e.mahnung_zahlungsfrist)),
	)
	_kommentar(r, text)
	return {
		"mahnung": doc.name,
		"stufe": stufe,
		"label": LABELS[stufe],
		"rechnung": r.name,
		"offen": flt(doc.total_outstanding),
		"gebuehr": fee,
		"gesamt": flt(doc.grand_total),
		"zahlungsfrist": doc.get("cv_zahlungsfrist"),
		"bestaetigt": bool(cint(bestaetigen)),
		"text": text,
	}
