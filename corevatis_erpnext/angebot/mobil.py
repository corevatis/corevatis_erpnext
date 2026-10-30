"""Angebot für die App (Durchstich A1): der Meister fordert in der App das Angebot an und sieht es.

Der Kern (``corevatis.api.besichtigung``) ruft diese Funktionen über den Hook
``corevatis_angebot_adapter`` (Modulpfad, ``frappe.get_hooks``) - kein Import über die
Lizenzgrenze, Vertragstest ``corevatis/tests/test_erpnext_contract.py``:

- ``katalog()``: Leistungen zur Auswahl in der App - Items mit Verfahren (``cv_verfahren``), aktiv,
  Verkaufsartikel, mit gebuchter Stückliste der Standardfirma (sonst überspringt der Generator sie,
  z. B. Regiestunden). Kurztext aus der Beschreibung (Angebotstext) ohne HTML.
- ``angebot_erzeugen(aufmass, neu_erzeugen)``: Generator 4.1 (``generator.erzeuge_angebot``) mit den
  Rechten des Benutzers; feste Mengen der App kennt der Generator (``Aufmassposition.menge_fest``).
- ``angebote(lead, objekt)``: lesbare, nicht stornierte Angebote der Anfrage bzw. des Objekts.
- ``angebot_info(angebot)``: Anfrage/Objekt/Aufmaß eines lesbaren Angebots (Zugangsprüfung im Kern).
- ``angebot_pdf(angebot)``: PDF im Standard-Druckformat des Angebots (4.3: „Angebot Corevatis“).
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt, strip_html_tags

from corevatis_erpnext.angebot import generator

KURZTEXT_MAX = 300


def _kurztext(text: str | None) -> str | None:
	text = " ".join(strip_html_tags(text or "").split())
	if not text:
		return None
	return text if len(text) <= KURZTEXT_MAX else text[: KURZTEXT_MAX - 1] + "…"


def katalog() -> list[dict]:
	# Security-Review: Katalog (Items, Stücklisten) nur für Büro/Meister, wie der Generator
	frappe.only_for(generator.ROLLEN)
	firma = generator._standardfirma()
	mit_stueckliste = set(
		frappe.get_all(
			"BOM",
			filters={"docstatus": 1, "is_active": 1, **({"company": firma} if firma else {})},
			pluck="item",
		)
	)
	items = frappe.get_all(
		"Item",
		filters={"disabled": 0, "is_sales_item": 1, "cv_verfahren": ("is", "set")},
		fields=[
			"name",
			"item_name",
			"description",
			"stock_uom",
			"cv_verfahren",
			"cv_gewerk",
			"cv_leistungsregel",
		],
		order_by="cv_verfahren asc, name asc",
	)
	return [
		{
			"item_code": i.name,
			"bezeichnung": i.item_name,
			"kurztext": _kurztext(i.description) if _kurztext(i.description) != i.item_name else None,
			"einheit": i.stock_uom,
			"verfahren": i.cv_verfahren,
			"gewerk": i.cv_gewerk,
			"leistungsregel": i.cv_leistungsregel,
		}
		for i in items
		if i.name in mit_stueckliste
	]


def angebot_erzeugen(aufmass: str, neu_erzeugen=0) -> dict:
	ergebnis = generator.erzeuge_angebot(aufmass, neu_erzeugen=cint(neu_erzeugen))
	name = ergebnis.get("angebot")
	# Security-Review: Status und Summen nur mit Leserecht auf das Angebot
	if name and frappe.has_permission("Quotation", "read", doc=name):
		werte = frappe.db.get_value(
			"Quotation", name, ["status", "docstatus", "net_total", "grand_total"], as_dict=True
		)
		if werte:
			ergebnis.update(
				{
					"status": werte.status,
					"docstatus": werte.docstatus,
					"netto": flt(werte.net_total),
					"brutto": flt(werte.grand_total),
				}
			)
	return ergebnis


def _zeile(q) -> dict:
	return {
		"name": q.name,
		"titel": q.cv_titel or q.title,
		"status": q.status,
		"docstatus": q.docstatus,
		"datum": str(q.transaction_date) if q.transaction_date else None,
		"gueltig_bis": str(q.valid_till) if q.valid_till else None,
		"netto": flt(q.net_total),
		"brutto": flt(q.grand_total),
		"waehrung": q.currency,
		"aufmass": q.cv_aufmass,
	}


def angebote(lead: str | None = None, objekt: str | None = None) -> list[dict]:
	felder = [
		"name",
		"title",
		"cv_titel",
		"status",
		"docstatus",
		"transaction_date",
		"valid_till",
		"net_total",
		"grand_total",
		"currency",
		"cv_aufmass",
	]
	namen: dict[str, frappe._dict] = {}
	bedingungen = []
	if objekt:
		bedingungen.append({"cv_objekt": objekt})
	if lead:
		bedingungen.append({"quotation_to": "Lead", "party_name": lead})
		bedingungen.append({"cv_lead": lead})
	for filters in bedingungen:
		for q in frappe.get_list(
			"Quotation", filters={**filters, "docstatus": ("<", 2)}, fields=felder, order_by="creation desc"
		):
			namen.setdefault(q.name, q)
	return [_zeile(q) for q in sorted(namen.values(), key=lambda q: q.name, reverse=True)]


def angebot_info(angebot: str) -> dict | None:
	if not frappe.db.exists("Quotation", angebot) or not frappe.has_permission(
		"Quotation", "read", doc=angebot
	):
		return None
	q = frappe.db.get_value(
		"Quotation",
		angebot,
		["quotation_to", "party_name", "cv_lead", "cv_objekt", "cv_aufmass"],
		as_dict=True,
	)
	lead = q.cv_lead or (q.party_name if q.quotation_to == "Lead" else None)
	return {"lead": lead, "objekt": q.cv_objekt, "aufmass": q.cv_aufmass}


def angebot_pdf(angebot: str) -> tuple[str, bytes]:
	doc = frappe.get_doc("Quotation", angebot)
	doc.check_permission("read")
	druckformat = frappe.get_meta("Quotation").default_print_format or None
	inhalt = frappe.get_print("Quotation", doc.name, print_format=druckformat, doc=doc, as_pdf=True)
	return f"{doc.name}.pdf", inhalt
