"""Debitorennummern und Debitoren-Stammdaten für DATEV (7.4).

DATEV führt Kunden als Personenkonten (Debitoren): bei Sachkontenlänge 4 fünfstellig, 10000 bis 69999.
erpnext_datev bringt dafür das Feld ``Party Account.debtor_creditor_number`` (je Kunde und Firma) mit,
vergibt es aber nicht. Hier: fortlaufende Vergabe ab ``DATEV Settings.cv_debitor_start`` (Standard 10000)
beim Anlegen eines Kunden (``Customer.validate``, Hook) und spätestens im Export für jeden betroffenen
Kunden - damit der Steuerberater von Anfang an stabile Nummern bekommt.

``stammdaten_zeilen`` liefert die Zeilen für ``EXTF_Debitoren`` (Spalten wie erpnext_datev
``DebtorsCreditors``), auch für Kunden ohne „primäre“ Adresse (erpnext_datev lässt die aus).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import frappe
from frappe import _
from frappe.utils import cint

from corevatis_erpnext.datev.felder import DEBITOR_START_STANDARD, SETTINGS_DOCTYPE

FELD = "debtor_creditor_number"
ENDE_DEBITOREN = 69999
RE_FORMELZEICHEN = re.compile(r"^[=+\-@\t\r]+")


class DebitorenFehler(frappe.ValidationError):
	pass


def einstellungen(company: str) -> frappe._dict | None:
	"""DATEV Settings der Firma (erpnext_datev) samt Corevatis-Feldern; ``None`` ohne App oder ohne Eintrag."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return None
	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	felder = [
		"name",
		"client",
		"client_number",
		"consultant_number",
		"account_number_length",
		"temporary_against_account_number",
		"opening_against_account_number",
	]
	felder += [f for f in ("cv_debitor_start", "cv_druckformat") if meta.has_field(f)]
	return frappe.db.get_value(SETTINGS_DOCTYPE, {"client": company}, felder, as_dict=True)


def nummer(customer: str, company: str) -> str | None:
	"""Debitorennummer des Kunden bei der Firma, falls vergeben."""
	return (
		frappe.db.get_value(
			"Party Account", {"parent": customer, "parenttype": "Customer", "company": company}, FELD
		)
		or None
	)


def naechste_nummer(company: str, start: int) -> int:
	"""Kleinste freie Nummer ab ``start`` (höchste vergebene + 1)."""
	hoechste = frappe.db.sql(
		"""
		SELECT MAX(CAST(debtor_creditor_number AS UNSIGNED))
		FROM `tabParty Account`
		WHERE parenttype = 'Customer' AND company = %(company)s
			AND debtor_creditor_number REGEXP '^[0-9]+$'
			AND CAST(debtor_creditor_number AS UNSIGNED) BETWEEN %(start)s AND %(ende)s
		""",
		{"company": company, "start": start, "ende": ENDE_DEBITOREN},
	)[0][0]
	naechste = int(hoechste) + 1 if hoechste else start
	if naechste > ENDE_DEBITOREN:
		frappe.throw(
			_("Keine freie Debitorennummer mehr (Bereich {0} bis {1}).").format(start, ENDE_DEBITOREN),
			DebitorenFehler,
		)
	return naechste


def _start(e: frappe._dict | None) -> int:
	return cint(e.get("cv_debitor_start")) if e and e.get("cv_debitor_start") else DEBITOR_START_STANDARD


def forderungskonto(company: str) -> str | None:
	return frappe.db.get_value("Company", company, "default_receivable_account") or frappe.db.get_value(
		"Account", {"company": company, "account_type": "Receivable", "is_group": 0}
	)


def vergeben(customer: str, company: str, e: frappe._dict | None = None) -> tuple[str | None, bool]:
	"""Debitorennummer lesen oder neu vergeben. Rückgabe ``(nummer, neu)``; ``(None, False)`` ohne
	DATEV Settings der Firma."""
	vorhanden = nummer(customer, company)
	if vorhanden:
		return vorhanden, False
	e = e or einstellungen(company)
	if not e:
		return None, False
	neu = str(naechste_nummer(company, _start(e)))
	zeile = frappe.db.get_value(
		"Party Account", {"parent": customer, "parenttype": "Customer", "company": company}, "name"
	)
	if zeile:
		frappe.db.set_value("Party Account", zeile, FELD, neu, update_modified=False)
	else:
		doc = frappe.get_doc("Customer", customer)
		row = doc.append("accounts", {"company": company, "account": forderungskonto(company), FELD: neu})
		# Kindzeile direkt schreiben: kein zweites Speichern des Kunden (Hooks, Zeitstempel)
		row.db_insert()
	frappe.clear_document_cache("Customer", customer)
	return neu, True


def kunde_validate(doc, method=None) -> None:
	"""``Customer.validate``: neuer Kunde bekommt je Firma mit DATEV Settings eine Debitorennummer."""
	if not doc.is_new() or not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		return
	meta = frappe.get_meta(SETTINGS_DOCTYPE)
	felder = ["client"] + (["cv_debitor_start"] if meta.has_field("cv_debitor_start") else [])
	for e in frappe.get_all(SETTINGS_DOCTYPE, fields=felder):
		zeile = next((r for r in doc.get("accounts", []) if r.company == e.client), None)
		if zeile and zeile.get(FELD):
			continue
		neu = str(naechste_nummer(e.client, _start(e)))
		if zeile:
			zeile.set(FELD, neu)
		else:
			doc.append("accounts", {"company": e.client, "account": forderungskonto(e.client), FELD: neu})


# --- Stammdaten -----------------------------------------------------------------------------------


def text(wert, max_len: int) -> str:
	"""Textfeld für die CSV: Leerraum gebündelt, Anführungszeichen ersetzt, führende Formelzeichen
	entfernt (Tabellenkalkulation des Steuerberaters), auf ``max_len`` gekürzt."""
	if wert is None:
		return ""
	s = " ".join(str(wert).replace('"', "'").split())
	s = RE_FORMELZEICHEN.sub("", s).strip()
	return s[:max_len]


def name_teilen(customer_name: str) -> tuple[str, str]:
	"""„Erika Musterfrau“ → (Vorname „Erika“, Name „Musterfrau“); ein Wort → nur Name."""
	teile = (customer_name or "").split(None, 1)
	if len(teile) == 2:
		return teile[0], teile[1]
	return "", teile[0] if teile else ""


def _adresse(customer: str, primary: str | None) -> frappe._dict | None:
	felder = [
		"name",
		"address_line1",
		"address_line2",
		"pincode",
		"city",
		"country",
		"email_id",
		"phone",
		"fax",
	]
	if primary and frappe.db.exists("Address", primary):
		return frappe.db.get_value("Address", primary, felder, as_dict=True)
	namen = frappe.get_all(
		"Dynamic Link",
		{"link_doctype": "Customer", "link_name": customer, "parenttype": "Address"},
		pluck="parent",
	)
	if not namen:
		return None
	adressen = frappe.get_all(
		"Address",
		filters={"name": ["in", namen], "disabled": 0},
		fields=[*felder, "is_primary_address"],
		order_by="is_primary_address desc, creation asc",
		limit=1,
	)
	return adressen[0] if adressen else None


def _kundenfelder() -> list[str]:
	"""Stammdatenfelder des Kunden; ``salutation`` gibt es nicht in jeder ERPNext-Version."""
	meta = frappe.get_meta("Customer")
	felder = ["customer_name", "customer_type", "tax_id", "website", "customer_primary_address"]
	felder += [f for f in ("salutation", "customer_primary_contact") if meta.has_field(f)]
	return felder


def _anrede_kontakt(kontakt: str | None) -> str:
	"""Anrede aus dem Hauptkontakt, wenn der Kunde selbst keine trägt."""
	if not kontakt:
		return ""
	return frappe.db.get_value("Contact", kontakt, "salutation") or ""


def stammdaten_zeilen(kunden: Iterable[str], company: str) -> tuple[list[dict], list[str]]:
	"""Zeilen für ``EXTF_Debitoren`` der genannten Kunden. Rückgabe ``(zeilen, hinweise)``."""
	zeilen: list[dict] = []
	hinweise: list[str] = []
	laender: dict[str, str] = {}
	for kunde in kunden:
		nr = nummer(kunde, company)
		if not nr:
			hinweise.append(_("{0}: keine Debitorennummer - nicht in den Stammdaten").format(kunde))
			continue
		k = frappe.db.get_value("Customer", kunde, _kundenfelder(), as_dict=True)
		if not k:
			continue
		anrede = k.get("salutation") or _anrede_kontakt(k.get("customer_primary_contact"))
		adr = _adresse(kunde, k.customer_primary_address)
		if not adr:
			hinweise.append(_("{0}: keine Adresse hinterlegt").format(k.customer_name or kunde))
		land = ""
		if adr and adr.country:
			if adr.country not in laender:
				laender[adr.country] = (frappe.db.get_value("Country", adr.country, "code") or "").upper()
			land = laender[adr.country]
		zeile = {
			"Konto": nr,
			"Adressatentyp": "1" if k.customer_type == "Individual" else "2",
			"Kurzbezeichnung": text(k.customer_name, 15),
			"Anrede": text(anrede, 30),
			"Straße": text(adr.address_line1 if adr else "", 36),
			"Adresszusatz": text(adr.address_line2 if adr else "", 36),
			"Postleitzahl": text(adr.pincode if adr else "", 10),
			"Ort": text(adr.city if adr else "", 30),
			"Land": land,
			"E-Mail": text(adr.email_id if adr else "", 60),
			"Telefon": text(adr.phone if adr else "", 60),
			"Fax": text(adr.fax if adr else "", 60),
			"Internet": text(k.website, 60),
			"Steuernummer": text(k.tax_id, 20),
		}
		if k.customer_type == "Individual":
			vorname, name = name_teilen(k.customer_name)
			zeile["Vorname (Adressatentyp natürl. Person)"] = text(vorname, 30)
			zeile["Name (Adressatentyp natürl. Person)"] = text(name, 30)
		else:
			zeile["Name (Adressatentyp Unternehmen)"] = text(k.customer_name, 50)
		zeilen.append(zeile)
	return zeilen, hinweise
