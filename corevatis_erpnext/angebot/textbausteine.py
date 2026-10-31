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


# Erste Fassung aus dem Generator 4.1: „Angebot Standard“ war dort die Vorbemerkung. Ab 4.3 ist
# „Angebot Standard“ der Baustein für Zahlungsbedingungen (Company.default_selling_terms), die
# Vorbemerkung heißt „Abdichtung Standard“. Eine unveränderte Kopie dieser Fassung wird ersetzt.
ALTE_FASSUNGEN = {
	ANGEBOT_STANDARD: (
		"<p>Sehr geehrte Damen und Herren,</p>"
		"<p>vielen Dank für Ihre Anfrage und die Gelegenheit zur Besichtigung. Auf Grundlage unseres "
		"Aufmaßes vor Ort bieten wir Ihnen die nachstehenden Leistungen an. Mengen sind nach Aufmaß "
		"ermittelt; abgerechnet wird nach tatsächlich ausgeführter Menge.</p>"
	),
}


def ensure_textbausteine() -> list[str]:
	"""Fehlende Bausteine anlegen (idempotent). Vorhandene bleiben unverändert - außer einer
	unveränderten alten Fassung aus ``ALTE_FASSUNGEN``. Rückgabe: angelegte/ersetzte Titel."""
	angelegt = []
	for titel, datei in TEXTBAUSTEINE.items():
		if frappe.db.exists("Terms and Conditions", titel):
			alt = ALTE_FASSUNGEN.get(titel)
			if alt and cstr(frappe.db.get_value("Terms and Conditions", titel, "terms")).strip() == alt:
				doc = frappe.get_doc("Terms and Conditions", titel)
				doc.terms = baustein_text(datei)
				doc.save(ignore_permissions=True)
				angelegt.append(titel)
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
		# P2.6: ein Baustein mit Firma oder Verfahren schlägt den Firmenstandard; sonst der Standard
		# (``default_selling_terms``), und erst ohne ihn ein Baustein nur nach Belegart
		b = _baustein("Zahlungsbedingungen", doc)
		standard = frappe.get_cached_value("Company", doc.company, "default_selling_terms")
		text = _terms(standard)
		if b and (b.punkte > 1 or not text):
			doc.tc_name = b.name
			doc.terms = b.terms
		elif text:
			doc.tc_name = standard
			doc.terms = text

	texte_einsetzen(doc)


def texte_einsetzen(doc) -> None:
	"""Leere ``cv_vorbemerkung``/``cv_garantiehinweis`` aus den Bausteinen füllen (auch vom
	Generator 4.1 genutzt)."""
	meta = frappe.get_meta(doc.doctype)
	if (
		meta.has_field("cv_vorbemerkung")
		and not frappe.utils.strip_html(cstr(doc.get("cv_vorbemerkung"))).strip()
	):
		b = _baustein("Vorbemerkung", doc)
		text = cstr(b.terms) if b else _terms(VORBEMERKUNG_STANDARD)
		if text:
			doc.cv_vorbemerkung = text
	if meta.has_field("cv_garantiehinweis") and not cstr(doc.get("cv_garantiehinweis")).strip():
		b = _baustein("Gewährleistung", doc)
		text = cstr(b.terms) if b else _terms(GARANTIE_STANDARD)
		if text:
			doc.cv_garantiehinweis = absaetze_als_text(text)


def _baustein(art: str, doc):
	"""Passendster Baustein der Art für das Angebot (P2.6: Firma, Verfahren der Positionen, Belegart
	Angebot); ``None`` ohne Treffer - dann gelten die festen Titel aus 4.3."""
	from corevatis_erpnext import textbausteine as tb
	from corevatis_erpnext.textbausteine import platzhalter

	return tb.bester(
		art,
		firma=doc.get("company"),
		belegart="Angebot",
		verfahren=platzhalter.verfahren_des_belegs(doc),
	)


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
	Steuern heraus (Server ``taxes_and_totals.filter_rows`` und gleich im Desk-Formular). Läuft vor
	``validate``, also vor der Summenberechnung. ``is_alternative`` wird nur gesetzt, nie
	zurückgenommen (das Büro kann es auch direkt setzen).

	``is_alternative`` ist in ERPNext v16 der einzige Weg, eine Zeile mit Preis aus Netto, Steuer
	und Brutto zu nehmen, der in Desk (JS) und Server gleich rechnet. Seine zweite Bedeutung -
	„Ersatz für die Zeile davor“ (``has_alternative_item``, Auswahl-Dialog beim Auftrag) - hebt
	``alternativ_sets_aufloesen`` für Corevatis-Kennzeichen auf (Review Epic 4, A-2).
	"""
	for row in doc.get("items") or []:
		if ist_kennzeichen_zeile(row) and not row.get("is_alternative"):
			row.is_alternative = 1


def ist_kennzeichen_zeile(row) -> bool:
	return bool(row.get("cv_eventualposition") or row.get("cv_alternativposition"))


def saetze_ohne_ersatz(doc) -> list:
	"""Zeilen mit ``has_alternative_item``, deren „Alternativen“ nur Corevatis-Kennzeichen sind.

	ERPNext (``Quotation.set_has_alternative_item``, ``before_submit``) markiert die Zeile **vor**
	einer ``is_alternative``-Zeile als „hat Alternative“. Der ERPNext-Weg „Erstellen → Auftrag“
	zeigt dann einen Auswahl-Dialog, in dem diese Normalposition ohne Haken stillschweigend aus
	dem Auftrag fällt (``can_map_row``). Eine Eventual- oder Alternativposition aus Corevatis ist
	aber kein Ersatz für die Zeile davor, sondern eine eigene, optionale Position. Echte
	ERPNext-Alternativen (``is_alternative`` ohne ``cv_*``-Kennzeichen) bleiben unberührt.
	"""
	zeilen = list(doc.get("items") or [])
	ergebnis = []
	for idx, row in enumerate(zeilen):
		if not row.get("has_alternative_item"):
			continue
		folgende = []
		for naechste in zeilen[idx + 1 :]:
			if not naechste.get("is_alternative"):
				break
			folgende.append(naechste)
		if folgende and all(ist_kennzeichen_zeile(z) for z in folgende):
			ergebnis.append(row)
	return ergebnis


def alternativ_sets_aufloesen(doc, method=None) -> None:
	"""``before_submit`` Quotation (nach ERPNexts eigenem ``before_submit``): ``has_alternative_item``
	für Normalpositionen vor Corevatis-Eventual-/Alternativpositionen zurücknehmen.

	Wirkung auf dem ERPNext-Weg „Erstellen → Auftrag“: Im Auswahl-Dialog stehen nur noch die
	Eventual-/Alternativpositionen; Normalpositionen gehen immer in den Auftrag
	(``can_map_row``: einfache Zeile → übernehmen). Der Corevatis-Weg „Angebot annehmen“ war davon
	nie betroffen (``filtered_children``).
	"""
	for row in saetze_ohne_ersatz(doc):
		row.has_alternative_item = 0
