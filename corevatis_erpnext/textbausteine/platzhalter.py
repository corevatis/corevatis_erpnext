"""Platzhalter in Textbausteinen (P2.6, Handwerker#89): ``[[Kunde]]``, ``[[Betrag]]``, ``[[Faelligkeit]]`` …

Eigene, enge Syntax statt Jinja. ERPNext rendert ``Terms and Conditions`` zwar als Jinja
(``get_terms_and_conditions``), aber Text des Büros soll keine Ausdrücke ausführen; doppelte eckige
Klammern lässt Jinja unangetastet. Der Katalog (``KATALOG``) ist fest, unbekannte Platzhalter weist
``pruefen`` ab, ``ersetzen`` setzt Werte HTML-maskiert ein (Text-Editor-Felder) oder als Klartext
(``Small Text``). Fehlende Werte ergeben eine leere Zeichenkette; unbekannte Platzhalter bleiben
sichtbar stehen, damit ein Fehler im Beleg auffällt statt stumm zu verschwinden.
"""

from __future__ import annotations

import re
from collections import Counter

import frappe
from frappe.utils import cstr, escape_html, flt, fmt_money, format_date, get_fullname, strip_html, today

MUSTER = re.compile(r"\[\[\s*([A-Za-zÄÖÜäöüß_]+)\s*\]\]")

# Name -> Beschreibung (Hilfe am Formular und Fehlermeldung)
KATALOG: dict[str, str] = {
	"Anrede": "Briefanrede: „Sehr geehrte Frau Muster“ / „Sehr geehrter Herr Muster“, sonst „Sehr geehrte Damen und Herren“",
	"Kunde": "Name des Kunden",
	"Kundennummer": "Kundennummer",
	"Ansprechpartner": "Ansprechpartner des Kunden am Beleg",
	"Objekt": "Sanierungsobjekt (Straße, PLZ Ort), sonst Adresse des Kunden",
	"Firma": "Name der eigenen Firma",
	"Beleg": "Nummer des Belegs (Angebot, Auftrag, Rechnung oder Mahnung)",
	"Angebot": "Angebotsnummer",
	"Auftrag": "Auftragsnummer",
	"Rechnung": "Rechnungsnummer",
	"Vorgang": "Nummer des Vorgangs (Projekt)",
	"Datum": "Belegdatum",
	"Gueltig_bis": "Gültig bis (Angebot)",
	"Faelligkeit": "Fälligkeit (Rechnung)",
	"Zahlungsfrist": "Zahlungsfrist (Mahnung)",
	"Netto": "Nettobetrag",
	"Betrag": "Bruttobetrag",
	"Offen": "Offener Betrag",
	"Mahngebuehr": "Mahngebühr",
	"Sachbearbeiter": "Name des angemeldeten Bearbeiters",
	"Heute": "Heutiges Datum",
}

ANREDE_NEUTRAL = "Sehr geehrte Damen und Herren"


def enthaelt(text) -> bool:
	return "[[" in cstr(text)


def namen(text) -> list[str]:
	"""Alle Platzhalternamen im Text, in Reihenfolge, ohne Doppelte."""
	return list(dict.fromkeys(MUSTER.findall(cstr(text))))


def pruefen(text) -> list[str]:
	"""Unbekannte Platzhalter im Text (leer = alles gut)."""
	return [n for n in namen(text) if n not in KATALOG]


def ersetzen(text, kontext: dict, html: bool = True) -> str:
	"""Bekannte Platzhalter durch Werte aus ``kontext`` ersetzen; unbekannte bleiben stehen."""
	text = cstr(text)
	if "[[" not in text:
		return text

	def wert(m: re.Match) -> str:
		name = m.group(1)
		if name not in KATALOG:
			return m.group(0)
		w = cstr(kontext.get(name, ""))
		return escape_html(w) if html else w

	return MUSTER.sub(wert, text)


def katalog() -> list[dict]:
	return [{"name": n, "beschreibung": b} for n, b in KATALOG.items()]


# --- Kontext aus einem Beleg -------------------------------------------------------------------


def _geld(wert, waehrung: str | None) -> str:
	if wert in (None, ""):
		return ""
	return fmt_money(flt(wert), currency=waehrung or "EUR")


def _datum(wert) -> str:
	return format_date(wert) if wert else ""


def _anrede(contact: str | None) -> str:
	if not contact or not frappe.db.exists("Contact", contact):
		return ANREDE_NEUTRAL
	c = frappe.db.get_value("Contact", contact, ["salutation", "first_name", "last_name"], as_dict=True)
	nachname = cstr(c.last_name).strip() or cstr(c.first_name).strip()
	anrede = cstr(c.salutation).strip()
	if not nachname or anrede not in ("Frau", "Herr", "Mr", "Ms", "Mrs"):
		return ANREDE_NEUTRAL
	if anrede in ("Herr", "Mr"):
		return f"Sehr geehrter Herr {nachname}"
	return f"Sehr geehrte Frau {nachname}"


def _objekt(doc) -> str:
	name = doc.get("cv_objekt")
	if name and frappe.db.exists("DocType", "Objekt") and frappe.db.exists("Objekt", name):
		o = frappe.db.get_value("Objekt", name, ["strasse", "plz", "ort"], as_dict=True)
		teile = [
			cstr(o.strasse).strip(),
			" ".join(t for t in (cstr(o.plz).strip(), cstr(o.ort).strip()) if t),
		]
		text = ", ".join(t for t in teile if t)
		if text:
			return text
	for feld in ("cv_objekt_adresse", "address_display", "customer_address"):
		if doc.get(feld):
			return " ".join(strip_html(cstr(doc.get(feld))).split())
	return ""


def _kunde(doc) -> tuple[str, str]:
	"""(Name, Kundennummer) für Angebot, Auftrag, Rechnung, Mahnung und Kunde."""
	if doc.doctype == "Customer":
		return cstr(doc.get("customer_name") or doc.name), cstr(doc.name)
	if doc.doctype == "Quotation":
		if doc.get("quotation_to") == "Customer":
			return cstr(doc.get("customer_name") or doc.get("party_name")), cstr(doc.get("party_name"))
		return cstr(doc.get("customer_name") or doc.get("party_name")), ""
	return cstr(doc.get("customer_name") or doc.get("customer")), cstr(doc.get("customer") or "")


def _erste(zeilen, feld: str) -> str:
	for z in zeilen or []:
		if z.get(feld):
			return cstr(z.get(feld))
	return ""


def kontext_aus(doc, firma: str | None = None) -> dict:
	"""Werte aller Platzhalter für einen Beleg (Quotation, Sales Order, Sales Invoice, Dunning) oder
	einen Kunden (Customer). Fehlende Angaben sind leer."""
	company = firma or doc.get("company")
	waehrung = doc.get("currency") or "EUR"
	kunde, kundennummer = _kunde(doc)
	k = {
		"Anrede": _anrede(doc.get("contact_person") or doc.get("customer_primary_contact")),
		"Kunde": kunde,
		"Kundennummer": kundennummer,
		"Ansprechpartner": " ".join(strip_html(cstr(doc.get("contact_display"))).split()),
		"Objekt": _objekt(doc),
		"Firma": cstr(frappe.get_cached_value("Company", company, "company_name")) if company else "",
		"Beleg": "" if doc.doctype == "Customer" else cstr(doc.name or ""),
		"Angebot": "",
		"Auftrag": "",
		"Rechnung": "",
		"Vorgang": cstr(doc.get("project") or doc.get("cv_projekt") or ""),
		"Datum": _datum(doc.get("transaction_date") or doc.get("posting_date")),
		"Gueltig_bis": _datum(doc.get("valid_till")),
		"Faelligkeit": _datum(doc.get("due_date")),
		"Zahlungsfrist": _datum(doc.get("cv_zahlungsfrist")),
		"Netto": _geld(doc.get("net_total"), waehrung),
		"Betrag": _geld(doc.get("grand_total"), waehrung),
		"Offen": _geld(doc.get("outstanding_amount") or doc.get("total_outstanding"), waehrung),
		"Mahngebuehr": _geld(doc.get("dunning_fee"), waehrung),
		"Sachbearbeiter": cstr(get_fullname(frappe.session.user)),
		"Heute": _datum(today()),
	}
	if doc.doctype == "Quotation":
		k["Angebot"] = cstr(doc.name or "")
	elif doc.doctype == "Sales Order":
		k["Auftrag"] = cstr(doc.name or "")
		k["Angebot"] = _erste(doc.get("items"), "prevdoc_docname")
	elif doc.doctype == "Sales Invoice":
		k["Rechnung"] = cstr(doc.name or "")
		k["Auftrag"] = cstr(doc.get("cv_auftrag") or _erste(doc.get("items"), "sales_order"))
	elif doc.doctype == "Dunning":
		k["Rechnung"] = _erste(doc.get("overdue_payments"), "sales_invoice")
		k["Faelligkeit"] = _datum(_erste(doc.get("overdue_payments"), "due_date"))
		if not k["Offen"]:
			k["Offen"] = _geld(doc.get("total_outstanding"), waehrung)
	return k


def verfahren_des_belegs(doc) -> str:
	"""Häufigstes Verfahren der Positionen: ``cv_verfahren`` der Zeile (Generator 4.1), sonst das des
	Artikels (``Item.cv_verfahren``, Desk und Kopien); leer ohne Verfahren."""
	artikel_hat_feld = frappe.get_meta("Item").has_field("cv_verfahren")
	zaehler: Counter = Counter()
	for z in doc.get("items") or []:
		verfahren = cstr(z.get("cv_verfahren"))
		if not verfahren and artikel_hat_feld and z.get("item_code"):
			verfahren = cstr(frappe.get_cached_value("Item", z.item_code, "cv_verfahren"))
		if verfahren:
			zaehler[verfahren] += 1
	return zaehler.most_common(1)[0][0] if zaehler else ""
