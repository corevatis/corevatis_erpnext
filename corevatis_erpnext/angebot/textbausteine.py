"""Textbausteine für das Angebot (Aufgabe 4.3): Zahlungsbedingungen, Vorbemerkung, Garantiehinweis.

Die Bausteine sind ``Terms and Conditions`` (ERPNext-Standard, im Desk unter „Allgemeine
Geschäftsbedingungen“ pflegbar):

- „Angebot Standard“ - Zahlungsbedingungen und Gültigkeit; Standard der Firma
  (``Company.default_selling_terms``, setzt das Site-Template), ERPNext übernimmt ihn als
  ``tc_name``/``terms`` ins Angebot.
- „Abdichtung Standard“ - Vorbemerkung; landet beim Anlegen in ``Quotation.cv_vorbemerkung``.
- „Gewährleistung Abdichtung“ - Garantiehinweis; landet beim Anlegen (als Text) in
  ``Quotation.cv_garantiehinweis``.

``ensure_textbausteine`` legt fehlende Bausteine an (``after_install``/``after_migrate``) und
ändert vorhandene nie: Anpassungen des Büros bleiben. Der Inhalt ist eine fachliche
**Annahme** (kein Rechtsrat) und vor dem Einsatz mit dem Betrieb abzustimmen
(docs/4.3-angebotsdruck.md im Repo Handwerker).

``standard_texte`` (``doc_events`` Quotation ``before_insert``) füllt leere Felder eines neuen
Angebots aus den Bausteinen. Die Texte stehen danach im Beleg: eine spätere Änderung des
Bausteins ändert kein verschicktes Angebot.
"""

import re
from html import unescape
from pathlib import Path

import frappe
from frappe.utils import cstr

ORDNER = Path(__file__).resolve().parent / "texte"

ANGEBOT_STANDARD = "Angebot Standard"
VORBEMERKUNG_STANDARD = "Abdichtung Standard"
GARANTIE_STANDARD = "Gewährleistung Abdichtung"

TEXTBAUSTEINE = {
	ANGEBOT_STANDARD: "angebot_standard.html",
	VORBEMERKUNG_STANDARD: "abdichtung_standard.html",
	GARANTIE_STANDARD: "gewaehrleistung_abdichtung.txt",
}


def baustein_text(datei: str) -> str:
	"""Inhalt als HTML (Text Editor). ``.txt``: ein Absatz je Zeile."""
	text = (ORDNER / datei).read_text(encoding="utf-8").strip()
	if datei.endswith(".txt"):
		return "".join(
			f"<p>{frappe.utils.escape_html(zeile)}</p>" for zeile in text.splitlines() if zeile.strip()
		)
	return text


def ensure_textbausteine() -> list[str]:
	"""Fehlende Bausteine anlegen (idempotent, vorhandene bleiben unverändert)."""
	angelegt = []
	for titel, datei in TEXTBAUSTEINE.items():
		if frappe.db.exists("Terms and Conditions", titel):
			continue
		frappe.get_doc(
			{
				"doctype": "Terms and Conditions",
				"title": titel,
				"terms": baustein_text(datei),
				"selling": 1,
				"buying": 0,
				"disabled": 0,
			}
		).insert(ignore_permissions=True)
		angelegt.append(titel)
	return angelegt


def _terms(name: str) -> str:
	if not name or not frappe.db.exists("Terms and Conditions", {"name": name, "disabled": 0}):
		return ""
	return cstr(frappe.db.get_value("Terms and Conditions", name, "terms"))


def standard_texte(doc, method=None) -> None:
	"""``before_insert`` Quotation: leere Textfelder aus den Bausteinen füllen.

	- ``tc_name``/``terms``: Standard der Firma (``default_selling_terms``), wenn das Angebot
	  ohne Desk-Formular entsteht (REST, Generator 4.1) - der Desk setzt ihn selbst;
	- ``cv_vorbemerkung``: „Abdichtung Standard“;
	- ``cv_garantiehinweis``: „Gewährleistung Abdichtung“ als Text.

	Felder, die schon etwas enthalten, bleiben. Ohne die Custom Fields (4.1) nur ``terms``.
	"""
	if not doc.get("tc_name") and not cstr(doc.get("terms")).strip() and doc.get("company"):
		standard = frappe.get_cached_value("Company", doc.company, "default_selling_terms")
		text = _terms(standard)
		if text:
			doc.tc_name = standard
			doc.terms = text

	meta = frappe.get_meta(doc.doctype)
	if (
		meta.has_field("cv_vorbemerkung")
		and not frappe.utils.strip_html(cstr(doc.get("cv_vorbemerkung"))).strip()
	):
		text = _terms(VORBEMERKUNG_STANDARD)
		if text:
			doc.cv_vorbemerkung = text
	if meta.has_field("cv_garantiehinweis") and not cstr(doc.get("cv_garantiehinweis")).strip():
		text = _terms(GARANTIE_STANDARD)
		if text:
			doc.cv_garantiehinweis = absaetze_als_text(text)


def absaetze_als_text(html: str) -> str:
	"""HTML-Absätze als Klartext, ein Absatz je Zeile (``Small Text``)."""
	text = re.sub(r"</p>|<br\s*/?>|</div>", "\n", cstr(html), flags=re.IGNORECASE)
	text = unescape(re.sub(r"<[^>]+>", "", text))
	return "\n".join(zeile.strip() for zeile in text.splitlines() if zeile.strip())


def kennzeichen_ausserhalb_summe(doc, method=None) -> None:
	"""``before_validate`` Quotation: Eventual-/Alternativpositionen aus der Summe nehmen.

	Das Druckformat „Angebot Corevatis“ druckt Positionen mit ``cv_eventualposition`` oder
	``cv_alternativposition`` als „nicht in der Angebotssumme enthalten“. Damit das stimmt,
	setzt der Hook ERPNexts ``is_alternative``: ERPNext lässt solche Positionen aus Summen und
	Steuern heraus. Läuft vor ``validate``, also vor der Summenberechnung. ``is_alternative``
	wird nur gesetzt, nie zurückgenommen (das Büro kann es auch direkt setzen).
	"""
	for row in doc.get("items") or []:
		if (row.get("cv_eventualposition") or row.get("cv_alternativposition")) and not row.get(
			"is_alternative"
		):
			row.is_alternative = 1
