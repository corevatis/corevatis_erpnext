"""Kundenportal (Phase 2 P2.7, Handwerker#91): Kunde nimmt Angebot online an, bestätigt den Termin,
lädt Fotos hoch, sieht Rechnungen und freigegebene Dokumente - ohne Benutzerkonto, über einen Link.

- ``zugang``: Doctype ``Kundenzugang`` (Token-Hash, Gültigkeit, Widerruf, Zähler), Rate-Limit.
- ``vorgang``: Daten der Seite aus der Prozesskette (``prozess.kette``).
- ``aktionen``: Gast-Endpunkte (Termin bestätigen, Angebot annehmen mit Unterschrift, Foto, Download).
- ``www/kunde``: die Seite ``/kunde/<token>`` (``hooks.website_route_rules``).
- Desk: Dialog „Kundenzugang“ an Anfrage, Kunde, Projekt, Angebot (``public/js/kundenzugang.js``), Liste
  „Kundenzugänge“ in der Leiste „Betrieb“, Formular des Zugangs.

Kein E-Mail-Versand: Den Link kopiert das Büro (oder druckt den QR-Code) und gibt ihn selbst weiter.
Datenschutz: Die Seite verlinkt den Platzhalter ``/datenschutz`` (1.2, Entwurf).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

from corevatis_erpnext.kundenportal import felder, zugang

BEGRUESSUNG_TITEL = "Kundenportal Begrüßung"
BEGRUESSUNG_TEXT = (
	"<p>[[Anrede]],</p>"
	"<p>hier finden Sie alles zu Ihrem Vorgang bei [[Firma]]: Ihren Termin, unser Angebot, den Stand der "
	"Arbeiten und Ihre Rechnungen. Sie können den Termin bestätigen, das Angebot mit Ihrer Unterschrift "
	"annehmen und uns Fotos vom Objekt schicken.</p>"
	"<p>Bei Fragen rufen Sie uns gern an.</p>"
)


def ensure_setup() -> None:
	"""Felder an Termin, Angebot und Datei, Textbaustein-Art „Kundenportal“ mit Begrüßung (idempotent)."""
	felder.ensure_felder()
	ensure_begruessung()


def ensure_begruessung() -> bool:
	from corevatis_erpnext.textbausteine.felder import ART_KUNDENPORTAL, DOCTYPE

	if not frappe.db.exists("DocType", DOCTYPE) or not frappe.get_meta(DOCTYPE).has_field("cv_art"):
		return False
	if frappe.db.exists(DOCTYPE, BEGRUESSUNG_TITEL):
		return False
	frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"title": BEGRUESSUNG_TITEL,
			"terms": BEGRUESSUNG_TEXT,
			"selling": 1,
			"buying": 0,
			"disabled": 0,
			"cv_art": ART_KUNDENPORTAL,
		}
	).insert(ignore_permissions=True)
	return True


# --- Desk ---------------------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def zugang_anlegen(doctype: str, name: str, gueltig_tage: int | str | None = None) -> dict:
	"""Neuen Link erzeugen (ersetzt den aktiven Link des Vorgangs); der Klartext kommt nur hier zurück."""
	return zugang.erzeugen(doctype, name, cint(gueltig_tage) or None)


@frappe.whitelist(methods=["POST"])
def zugang_widerrufen(name: str) -> dict:
	zugang.pruefe_rolle()
	frappe.has_permission(zugang.DOCTYPE, "write", name, throw=True)
	zugang.widerrufen(name, _("Vom Büro widerrufen."))
	return {"name": name, "widerrufen": 1}


@frappe.whitelist()
def uebersicht(doctype: str, name: str) -> dict:
	"""Für den Dialog: Vorgang (Anker) und die Zugänge dazu - ohne Token."""
	zugang.pruefe_rolle()
	frappe.has_permission(doctype, "read", name, throw=True)
	anker_doctype, anker = zugang.anker_fuer(doctype, name)
	liste = frappe.get_all(
		zugang.DOCTYPE,
		filters={"anker_doctype": anker_doctype, "anker": anker},
		fields=[
			"name",
			"gueltig_bis",
			"widerrufen",
			"widerrufen_am",
			"aufrufe",
			"uploads",
			"letzte_nutzung",
			"creation",
			"owner",
		],
		order_by="creation desc",
		limit=20,
	)
	return {
		"anker_doctype": anker_doctype,
		"anker": anker,
		"kunde_name": zugang.kunden_name(anker_doctype, anker),
		"zugaenge": liste,
		"gueltig_tage": zugang.GUELTIG_TAGE,
	}
