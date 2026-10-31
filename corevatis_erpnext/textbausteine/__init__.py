"""Textbausteine, Vorlagen und Serienbriefe im Desk (P2.6, Handwerker#89).

Bausteine sind ERPNexts ``Terms and Conditions`` (seit 4.3 die Ablage der Angebotstexte): kein
zweites Doctype, ERPNexts ``tc_name``-Mechanik an Angebot, Auftrag und Rechnung bleibt. Dazu kommen
Custom Fields (``felder.py``): Art (Vorbemerkung, Zahlungsbedingungen, Gewährleistung, Schlusstext,
Mahntext, Anschreiben, Sonstiges), Belegart, Verfahren, Firma, Reihenfolge, Gültig ab/bis, Mahnstufe.

- ``bester``/``text`` wählen den passendsten Baustein: Firma vor Verfahren vor Belegart, leere Felder
  gelten für alle; abgelaufene und deaktivierte Bausteine zählen nicht.
- Platzhalter ``[[Kunde]]`` … (``platzhalter.py``) prüft ``tc_validate`` beim Speichern; die Belege
  ersetzen sie beim Speichern (``beleg_validate``), die Mahnung beim Erstellen (``mahnung.py``).
  Die Texte stehen danach im Beleg - eine spätere Änderung des Bausteins ändert keinen Beleg.
- Mahntexte: Baustein der Art Mahntext je Stufe; ``tc_on_update`` schreibt den Text in den Brieftext
  des Mahntyps (ERPNext Dunning Type) der Firma bzw. aller Firmen ohne eigenen Baustein.
- Vorschau am Formular (``vorschau``) mit dem letzten Beleg der passenden Art.
- Serienbrief (``serienbrief.py``): Anschreiben an eine Kundenauswahl als ein PDF.

Rechte: Büro liest, schreibt, legt an und löscht; Meister liest (``ensure_rechte``).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, cstr, getdate, today

from corevatis_erpnext.textbausteine import platzhalter
from corevatis_erpnext.textbausteine.felder import (
	ART_ANSCHREIBEN,
	ART_GEWAEHRLEISTUNG,
	ART_MAHNTEXT,
	ART_VORBEMERKUNG,
	ART_ZAHLUNG,
	BELEGART_ANGEBOT,
	BELEGART_ANSCHREIBEN,
	BELEGART_JE_DOCTYPE,
	BELEGART_MAHNUNG,
	DOCTYPE,
	ensure_felder,
)

ROLLEN_SCHREIBEN = ("Büro", "System Manager")
RECHTE = {
	"Büro": {DOCTYPE: ("read", "write", "create", "delete", "print", "report")},
	"Meister": {DOCTYPE: ("read", "report")},
}
ANSCHREIBEN_STANDARD = "Anschreiben Standard"
MAHNTEXT_PRAEFIX = "Mahntext"
# Felder der Belege, in denen Platzhalter ersetzt werden: (Feld, HTML?)
BELEG_TEXTFELDER = (("terms", True), ("cv_vorbemerkung", True), ("cv_garantiehinweis", False))
JINJA_MARKEN = ("{{", "{%", "{#")


class TextbausteinFehler(frappe.ValidationError):
	pass


# --- Einrichtung ---------------------------------------------------------------------------------


def ensure_setup() -> None:
	"""Felder, Rechte, Einordnung der vorhandenen Bausteine, Mahntexte und ein Anschreiben (idempotent)."""
	if not frappe.db.exists("DocType", DOCTYPE):
		return
	ensure_felder()
	ensure_rechte()
	ensure_standard()
	ensure_mahntexte()
	ensure_anschreiben()


def ensure_rechte() -> None:
	from frappe.permissions import add_permission, update_permission_property

	for rolle, je_doctype in RECHTE.items():
		if not frappe.db.exists("Role", rolle):
			continue
		for doctype, rechte in je_doctype.items():
			add_permission(doctype, rolle, 0)
			for recht in rechte:
				if not frappe.db.get_value(
					"Custom DocPerm", {"parent": doctype, "role": rolle, "permlevel": 0}, recht
				):
					update_permission_property(doctype, rolle, 0, recht, 1, validate=False)
	frappe.clear_cache()


def ensure_standard() -> list[str]:
	"""Die Bausteine aus 4.3 bekommen Art und Belegart, wenn noch keine gesetzt ist. Texte bleiben."""
	from corevatis_erpnext.angebot.textbausteine import (
		ANGEBOT_STANDARD,
		GARANTIE_STANDARD,
		VORBEMERKUNG_STANDARD,
	)

	einordnung = {
		ANGEBOT_STANDARD: (ART_ZAHLUNG, BELEGART_ANGEBOT),
		VORBEMERKUNG_STANDARD: (ART_VORBEMERKUNG, BELEGART_ANGEBOT),
		GARANTIE_STANDARD: (ART_GEWAEHRLEISTUNG, BELEGART_ANGEBOT),
	}
	geaendert = []
	for titel, (art, belegart) in einordnung.items():
		if not frappe.db.exists(DOCTYPE, titel) or frappe.db.get_value(DOCTYPE, titel, "cv_art"):
			continue
		frappe.db.set_value(DOCTYPE, titel, {"cv_art": art, "cv_belegart": belegart}, update_modified=False)
		geaendert.append(titel)
	return geaendert


def mahntext_titel(stufe: int) -> str:
	from corevatis_erpnext.abrechnung.mahnung import LABELS

	return f"{MAHNTEXT_PRAEFIX} {LABELS[stufe]}"


def ensure_mahntexte() -> list[str]:
	"""Je Mahnstufe ein Baustein mit dem Brieftext aus 7.5, falls er fehlt; vorhandene bleiben."""
	from corevatis_erpnext.abrechnung.mahnung import LABELS, brieftext

	angelegt = []
	for stufe in LABELS:
		titel = mahntext_titel(stufe)
		if frappe.db.exists(DOCTYPE, titel):
			continue
		text, _schluss = brieftext(stufe)
		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"title": titel,
				"terms": text,
				"selling": 1,
				"buying": 0,
				"disabled": 0,
				"cv_art": ART_MAHNTEXT,
				"cv_belegart": BELEGART_MAHNUNG,
				"cv_mahnstufe": str(stufe),
			}
		)
		doc.flags.cv_kein_sync = True  # Mahntypen tragen denselben Text schon
		doc.insert(ignore_permissions=True)
		angelegt.append(titel)
	return angelegt


ANSCHREIBEN_TEXT = (
	"<p>[[Anrede]],</p>"
	"<p>vielen Dank für Ihr Vertrauen in [[Firma]]. Zu Ihrem Objekt [[Objekt]] melden wir uns mit diesem "
	"Schreiben.</p>"
	"<p>Für Fragen stehen wir Ihnen gern zur Verfügung.</p>"
	"<p>Mit freundlichen Grüßen<br>[[Sachbearbeiter]]<br>[[Firma]]</p>"
)


def ensure_anschreiben() -> bool:
	if frappe.db.exists(DOCTYPE, ANSCHREIBEN_STANDARD):
		return False
	frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"title": ANSCHREIBEN_STANDARD,
			"terms": ANSCHREIBEN_TEXT,
			"selling": 1,
			"buying": 0,
			"disabled": 0,
			"cv_art": ART_ANSCHREIBEN,
			"cv_belegart": BELEGART_ANSCHREIBEN,
		}
	).insert(ignore_permissions=True)
	return True


# --- Auswahl -------------------------------------------------------------------------------------


def _passt(wert, gesucht) -> bool:
	return not wert or (bool(gesucht) and cstr(wert) == cstr(gesucht))


def finden(
	art: str,
	firma: str | None = None,
	belegart: str | None = None,
	verfahren: str | None = None,
	mahnstufe: int | None = None,
	datum=None,
) -> list[frappe._dict]:
	"""Passende Bausteine, der beste zuerst: Treffer bei Firma (3) vor Verfahren (2) vor Belegart (1),
	leere Felder des Bausteins gelten für alle; dann Reihenfolge und Titel."""
	if not frappe.db.exists("DocType", DOCTYPE) or not frappe.get_meta(DOCTYPE).has_field("cv_art"):
		return []
	heute = getdate(datum or today())
	filters = {"cv_art": art, "disabled": 0}
	if mahnstufe is not None:
		filters["cv_mahnstufe"] = str(mahnstufe)
	zeilen = frappe.get_all(
		DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"title",
			"terms",
			"cv_art",
			"cv_belegart",
			"cv_verfahren",
			"cv_firma",
			"cv_reihenfolge",
			"cv_gueltig_ab",
			"cv_gueltig_bis",
			"cv_mahnstufe",
		],
		ignore_permissions=True,
	)
	treffer = []
	for z in zeilen:
		if not (
			_passt(z.cv_firma, firma)
			and _passt(z.cv_belegart, belegart)
			and _passt(z.cv_verfahren, verfahren)
		):
			continue
		if z.cv_gueltig_ab and getdate(z.cv_gueltig_ab) > heute:
			continue
		if z.cv_gueltig_bis and getdate(z.cv_gueltig_bis) < heute:
			continue
		z.punkte = (3 if z.cv_firma else 0) + (2 if z.cv_verfahren else 0) + (1 if z.cv_belegart else 0)
		treffer.append(z)
	treffer.sort(key=lambda z: (-z.punkte, cint(z.cv_reihenfolge), cstr(z.title)))
	return treffer


def bester(art: str, **kwargs) -> frappe._dict | None:
	treffer = finden(art, **kwargs)
	return treffer[0] if treffer else None


def text(art: str, **kwargs) -> str:
	b = bester(art, **kwargs)
	return cstr(b.terms) if b else ""


# --- Hooks am Baustein ---------------------------------------------------------------------------


def tc_validate(doc, method=None) -> None:
	"""Unbekannte Platzhalter ablehnen, Gültigkeit prüfen, Mahntext braucht die Stufe."""
	unbekannt = platzhalter.pruefen(doc.get("terms"))
	if unbekannt:
		frappe.throw(
			_("Unbekannte Platzhalter: {0}. Bekannt sind: {1}.").format(
				", ".join(f"[[{n}]]" for n in unbekannt),
				", ".join(f"[[{n}]]" for n in platzhalter.KATALOG),
			),
			TextbausteinFehler,
		)
	if doc.get("cv_gueltig_ab") and doc.get("cv_gueltig_bis"):
		if getdate(doc.cv_gueltig_ab) > getdate(doc.cv_gueltig_bis):
			frappe.throw(_("„Gültig ab“ liegt nach „Gültig bis“."), TextbausteinFehler)
	if doc.get("cv_art") == ART_MAHNTEXT:
		if cstr(doc.get("cv_mahnstufe")) == "":
			frappe.throw(_("Ein Mahntext braucht die Mahnstufe (0, 1 oder 2)."), TextbausteinFehler)
		# ERPNext rendert den Brieftext des Mahntyps als Jinja-Vorlage (eingeschränkte Umgebung). Bürotext
		# soll kein Vorlagen-Code sein: Platzhalter nur als [[…]], sonst bricht eine Mahnung an einem Tippfehler.
		if any(marke in cstr(doc.get("terms")) for marke in JINJA_MARKEN):
			frappe.throw(
				_(
					"Mahntexte dürfen keine Vorlagen-Syntax ({0}) enthalten. Platzhalter schreiben Sie als [[Name]]."
				).format(", ".join(JINJA_MARKEN)),
				TextbausteinFehler,
			)
		doc.cv_belegart = BELEGART_MAHNUNG
	elif doc.get("cv_mahnstufe"):
		doc.cv_mahnstufe = ""


def tc_on_update(doc, method=None) -> None:
	if doc.get("cv_art") == ART_MAHNTEXT and not doc.flags.get("cv_kein_sync"):
		mahntext_uebernehmen(doc)


def mahntext_uebernehmen(doc) -> list[str]:
	"""Text des Bausteins in den Brieftext der Mahntypen schreiben (ERPNext ``Dunning Type``): bei
	Firma nur dort, sonst in alle Firmen ohne eigenen Baustein dieser Stufe. Rückgabe: Mahntypen."""
	from corevatis_erpnext.abrechnung.mahnung import vorhandener_typ

	if not frappe.db.exists("DocType", "Dunning Type"):
		return []
	stufe = cint(doc.get("cv_mahnstufe"))
	if doc.get("cv_firma"):
		firmen = [doc.cv_firma]
	else:
		eigene = {
			z.cv_firma
			for z in frappe.get_all(
				DOCTYPE,
				filters={
					"cv_art": ART_MAHNTEXT,
					"cv_mahnstufe": str(stufe),
					"disabled": 0,
					"name": ("!=", doc.name),
				},
				fields=["cv_firma"],
				ignore_permissions=True,
			)
			if z.cv_firma
		}
		# Nur Firmen, die der Speichernde lesen darf (Benutzerberechtigungen); fremde bleiben unberührt.
		firmen = [f for f in frappe.get_list("Company", pluck="name") if f not in eigene]
	geaendert = []
	for firma in firmen:
		typ = vorhandener_typ(stufe, firma)
		if not typ:
			continue
		zeilen = frappe.get_all(
			"Dunning Letter Text",
			filters={"parent": typ, "parenttype": "Dunning Type"},
			fields=["name", "language", "is_default_language"],
			order_by="is_default_language desc, idx asc",
			parent_doctype="Dunning Type",
		)
		if not zeilen:
			continue
		frappe.db.set_value(
			"Dunning Letter Text", zeilen[0].name, "body_text", cstr(doc.terms), update_modified=False
		)
		geaendert.append(typ)
	return geaendert


# --- Hooks an den Belegen ------------------------------------------------------------------------


def beleg_before_insert(doc, method=None) -> None:
	"""Auftrag und Rechnung ohne Zahlungsbedingungen bekommen den passenden Baustein (Firma, Verfahren,
	Belegart) - ERPNext kopiert sonst nur den Firmenstandard oder nichts."""
	if doc.get("tc_name") or cstr(doc.get("terms")).strip():
		return
	b = bester(
		ART_ZAHLUNG,
		firma=doc.get("company"),
		belegart=BELEGART_JE_DOCTYPE.get(doc.doctype),
		verfahren=platzhalter.verfahren_des_belegs(doc),
	)
	if b:
		doc.tc_name = b.name
		doc.terms = b.terms


def beleg_validate(doc, method=None) -> None:
	"""Platzhalter in den Textfeldern des Belegs ersetzen (nach ERPNexts eigenem validate, das
	``terms`` aus ``tc_name`` füllt)."""
	meta = frappe.get_meta(doc.doctype)
	kontext = None
	for feld, html in BELEG_TEXTFELDER:
		if not meta.has_field(feld) or not platzhalter.enthaelt(doc.get(feld)):
			continue
		if kontext is None:
			kontext = platzhalter.kontext_aus(doc)
		doc.set(feld, platzhalter.ersetzen(doc.get(feld), kontext, html=html))


# --- Desk ----------------------------------------------------------------------------------------


@frappe.whitelist()
def platzhalter_katalog() -> list[dict]:
	return platzhalter.katalog()


_BEISPIEL_DOCTYPE = {
	BELEGART_ANGEBOT: "Quotation",
	"Auftragsbestätigung": "Sales Order",
	"Rechnung": "Sales Invoice",
	BELEGART_MAHNUNG: "Dunning",
	BELEGART_ANSCHREIBEN: "Customer",
}


def _beispiel(baustein) -> tuple[str, str] | None:
	"""Letzter Beleg der Belegart (und Firma) des Bausteins, den der Benutzer lesen darf."""
	belegart = baustein.get("cv_belegart") or (
		BELEGART_ANSCHREIBEN if baustein.get("cv_art") == ART_ANSCHREIBEN else BELEGART_ANGEBOT
	)
	doctype = _BEISPIEL_DOCTYPE.get(belegart, "Quotation")
	kandidaten = [doctype] + (["Sales Invoice"] if doctype == "Dunning" else [])
	for dt in kandidaten:
		if not frappe.db.exists("DocType", dt) or not frappe.has_permission(dt, "read"):
			continue
		filters = {"docstatus": ("!=", 2)} if dt != "Customer" else {}
		if baustein.get("cv_firma") and frappe.get_meta(dt).has_field("company"):
			filters["company"] = baustein.cv_firma
		zeilen = frappe.get_list(dt, filters=filters, fields=["name"], order_by="modified desc", limit=1)
		if zeilen:
			return dt, zeilen[0].name
	return None


@frappe.whitelist()
def vorschau(name: str, beleg_doctype: str | None = None, beleg_name: str | None = None) -> dict:
	"""Text des Bausteins mit den Werten eines Beispielbelegs (letzter Beleg der Belegart, oder der
	angegebene). Rückgabe: ``html``, ``beleg`` (Klartext), ``werte`` (je Platzhalter)."""
	frappe.has_permission(DOCTYPE, "read", name, throw=True)
	baustein = frappe.get_doc(DOCTYPE, name)
	if beleg_doctype and beleg_name:
		if beleg_doctype not in _BEISPIEL_DOCTYPE.values():
			frappe.throw(_("Belegart {0} wird nicht unterstützt.").format(beleg_doctype), TextbausteinFehler)
		frappe.has_permission(beleg_doctype, "read", beleg_name, throw=True)
		beispiel = (beleg_doctype, beleg_name)
	else:
		beispiel = _beispiel(baustein)
	if not beispiel:
		return {"html": cstr(baustein.terms), "beleg": "", "werte": []}
	doc = frappe.get_doc(*beispiel)
	kontext = platzhalter.kontext_aus(doc, firma=baustein.get("cv_firma"))
	return {
		"html": platzhalter.ersetzen(baustein.terms, kontext),
		"beleg": f"{_(beispiel[0])} {beispiel[1]}",
		"werte": [{"name": n, "wert": kontext.get(n, "")} for n in platzhalter.namen(baustein.terms)],
	}
