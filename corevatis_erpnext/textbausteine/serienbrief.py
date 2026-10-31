"""Serienbrief (P2.6): ein Anschreiben-Baustein an eine Kundenauswahl, als ein PDF zum Herunterladen.

Kein E-Mail-Versand (Entscheidung „erstmal kein Mail“). Je Kunde eine Seite mit Briefkopf der Firma
(Letter Head), Anschrift, Datum, Betreff und dem Text des Bausteins mit ersetzten Platzhaltern; die
Seiten werden mit pypdf zu einer Datei zusammengesetzt und als private Datei gespeichert.
Nur Büro und System Manager; höchstens ``MAX_KUNDEN`` je Lauf.
"""

from __future__ import annotations

from io import BytesIO

import frappe
from frappe import _
from frappe.utils import cstr, escape_html, format_date, get_fullname, today
from frappe.utils.pdf import get_pdf

from corevatis_erpnext.textbausteine import DOCTYPE, ROLLEN_SCHREIBEN, TextbausteinFehler, platzhalter

MAX_KUNDEN = 200

STIL = """
<style>
	.cv-brief { font-family: Arial, Helvetica, sans-serif; font-size: 11pt; color: #000; }
	.cv-brief .anschrift { margin-top: 20mm; min-height: 40mm; line-height: 1.35; }
	.cv-brief .absender { font-size: 8pt; color: #444; margin-bottom: 4mm; }
	.cv-brief .datum { text-align: right; margin: 4mm 0 10mm 0; }
	.cv-brief .betreff { font-weight: bold; margin-bottom: 6mm; }
	.cv-brief .text p { margin: 0 0 3mm 0; }
</style>
"""


def _anschrift(kunde: str) -> list[str]:
	from frappe.contacts.doctype.address.address import get_default_address

	zeilen = [cstr(frappe.db.get_value("Customer", kunde, "customer_name") or kunde)]
	adresse = get_default_address("Customer", kunde)
	if adresse:
		a = frappe.db.get_value(
			"Address", adresse, ["address_line1", "address_line2", "pincode", "city"], as_dict=True
		)
		for teil in (a.address_line1, a.address_line2):
			if cstr(teil).strip():
				zeilen.append(cstr(teil).strip())
		ort = " ".join(t for t in (cstr(a.pincode).strip(), cstr(a.city).strip()) if t)
		if ort:
			zeilen.append(ort)
	return zeilen


def _briefkopf(firma: str) -> tuple[str, str]:
	name = frappe.get_cached_value("Company", firma, "default_letter_head")
	if not name or not frappe.db.exists("Letter Head", name):
		return "", ""
	lh = frappe.get_cached_doc("Letter Head", name)
	kopf = (
		cstr(lh.content)
		if lh.source == "HTML"
		else (f'<img src="{escape_html(lh.image)}">' if lh.image else "")
	)
	fuss = cstr(lh.footer) if getattr(lh, "footer_source", "HTML") == "HTML" else ""
	return kopf, fuss


def brief_html(kopf: str, fuss: str, absender: str, anschrift: list[str], betreff: str, text: str) -> str:
	return (
		f"{STIL}<div class='cv-brief'>{kopf}"
		f"<div class='anschrift'><div class='absender'>{escape_html(absender)}</div>"
		+ "<br>".join(escape_html(z) for z in anschrift)
		+ f"</div><div class='datum'>{escape_html(format_date(today()))}</div>"
		f"<div class='betreff'>{betreff}</div><div class='text'>{text}</div>{fuss}</div>"
	)


def _absender(firma: str) -> str:
	c = frappe.get_cached_value("Company", firma, ["company_name"], as_dict=True)
	return cstr(c.company_name if c else firma)


@frappe.whitelist(methods=["POST"])
def erstellen(kunden, baustein: str, betreff: str, firma: str | None = None) -> dict:
	"""PDF mit einem Anschreiben je Kunde. ``kunden``: Liste der Kundennamen (JSON oder Liste).
	Rückgabe: ``file_url`` (private Datei), ``anzahl``, ``ausgelassen`` (ohne Leserecht)."""
	frappe.only_for(list(ROLLEN_SCHREIBEN))
	if isinstance(kunden, str):
		kunden = frappe.parse_json(kunden)
	kunden = [cstr(k) for k in (kunden or []) if k]
	if not kunden:
		frappe.throw(_("Bitte mindestens einen Kunden auswählen."), TextbausteinFehler)
	if len(kunden) > MAX_KUNDEN:
		frappe.throw(_("Höchstens {0} Kunden je Serienbrief.").format(MAX_KUNDEN), TextbausteinFehler)
	frappe.has_permission(DOCTYPE, "read", baustein, throw=True)
	b = frappe.get_doc(DOCTYPE, baustein)
	if b.disabled:
		frappe.throw(_("Der Textbaustein {0} ist deaktiviert.").format(b.title), TextbausteinFehler)
	betreff = cstr(betreff).strip()
	if not betreff:
		frappe.throw(_("Bitte einen Betreff angeben."), TextbausteinFehler)
	firma = (
		firma
		or b.get("cv_firma")
		or frappe.defaults.get_user_default("Company")
		or frappe.db.get_value("Company", {}, "name")
	)
	if not firma or not frappe.db.exists("Company", firma):
		frappe.throw(_("Keine Firma für den Briefkopf gefunden."), TextbausteinFehler)

	kopf, fuss = _briefkopf(firma)
	absender = _absender(firma)
	seiten: list[bytes] = []
	ausgelassen: list[str] = []
	for kunde in kunden:
		if not frappe.db.exists("Customer", kunde) or not frappe.has_permission("Customer", "read", kunde):
			ausgelassen.append(kunde)
			continue
		doc = frappe.get_doc("Customer", kunde)
		kontext = platzhalter.kontext_aus(doc, firma=firma)
		html = brief_html(
			kopf,
			fuss,
			absender,
			_anschrift(kunde),
			platzhalter.ersetzen(escape_html(betreff), kontext),
			platzhalter.ersetzen(b.terms, kontext),
		)
		seiten.append(get_pdf(html, {"page-size": "A4"}))
	if not seiten:
		frappe.throw(_("Kein Kunde der Auswahl ist lesbar."), TextbausteinFehler)

	inhalt = zusammenfuegen(seiten)
	dateiname = f"Serienbrief_{today()}_{frappe.scrub(b.title)}.pdf"
	datei = frappe.get_doc(
		{"doctype": "File", "file_name": dateiname, "is_private": 1, "content": inhalt}
	).insert(ignore_permissions=True)
	frappe.logger("corevatis_erpnext").info(
		f"Serienbrief {datei.name}: {len(seiten)} Briefe, Baustein {b.name}, von {get_fullname(frappe.session.user)}"
	)
	return {"file_url": datei.file_url, "name": datei.name, "anzahl": len(seiten), "ausgelassen": ausgelassen}


def zusammenfuegen(pdfs: list[bytes]) -> bytes:
	from pypdf import PdfReader, PdfWriter

	if len(pdfs) == 1:
		return pdfs[0]
	writer = PdfWriter()
	for p in pdfs:
		writer.append(PdfReader(BytesIO(p)))
	out = BytesIO()
	writer.write(out)
	return out.getvalue()
