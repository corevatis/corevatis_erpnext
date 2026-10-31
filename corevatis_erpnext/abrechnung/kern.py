"""Rechenlogik der Abrechnung kommt aus dem Kern ``corevatis.engine.invoice`` - per Name, kein Import
über die Lizenzgrenze (wie ``angebot.generator.KERN_KALKULATION``). Der Adapter liefert Eingaben
(Auftrag, Plan, Rechnungen) und legt aus dem Ergebnis die Belege an; Beträge rechnet er nicht.

Der Vertragstest im Kern (``corevatis/tests/test_erpnext_contract.py``) prüft die Pfade und die
Schlüssel der Eingaben gegen die Engine.
"""

from __future__ import annotations

import frappe
from frappe import _

KERN_APP = "corevatis"
KERN_ABSCHLAGSPLAN = "corevatis.engine.invoice.abschlagsplan"
KERN_ABSCHLAG = "corevatis.engine.invoice.abschlag"
KERN_SCHLUSSRECHNUNG = "corevatis.engine.invoice.schlussrechnung"
# Rundung der Site (System Settings ``rounding_method``) → Option ``rundung`` der Engine
RUNDUNG_SITE = {
	"Banker's Rounding (legacy)": "bankers",
	"Banker's Rounding": "bankers",
	"Commercial Rounding": "kaufmaennisch",
}
# Schlüssel der Eingaben (Vertrag mit ``corevatis.engine.invoice``)
AUFTRAG_SCHLUESSEL = ("name", "netto", "steuersatz", "lohnanteil_netto")
PLAN_ZEILE_SCHLUESSEL = (
	"nr",
	"bezeichnung",
	"prozent",
	"betrag",
	"faellig_bei",
	"datum",
	"status",
	"rechnung",
	"netto",
)
POSITION_SCHLUESSEL = (
	"id",
	"item_code",
	"bezeichnung",
	"einheit",
	"menge_auftrag",
	"menge_ist",
	"menge",
	"einzelpreis",
	"lohn_je_einheit",
	"lohnanteil_prozent",
	"lohnanteil",
)
ABSCHLAG_SCHLUESSEL = ("nr", "rechnung", "datum", "netto", "steuer", "brutto", "bezeichnung")


class AbrechnungFehler(frappe.ValidationError):
	pass


def kern_vorhanden() -> bool:
	return KERN_APP in frappe.get_installed_apps()


def _aufloesen(pfad: str):
	"""Funktion der Engine per Name (kein Import über die Lizenzgrenze); ``None`` ohne Kern.

	``frappe.get_attr`` wirft für eine nicht installierte App ``AppNotInstalledError`` (kein
	ImportError) - deshalb zuerst die installierten Apps prüfen."""
	if not kern_vorhanden():
		return None
	try:
		return frappe.get_attr(pfad)
	except (ImportError, AttributeError):
		return None


def verfuegbar() -> bool:
	return _aufloesen(KERN_ABSCHLAGSPLAN) is not None


def _kern(pfad: str):
	fn = _aufloesen(pfad)
	if fn is None:
		frappe.throw(
			_(
				"Die Kern-App corevatis ist nicht installiert - Abschläge und Schlussrechnung sind nicht möglich."
			),
			AbrechnungFehler,
		)
	return fn


def rundung() -> str:
	return RUNDUNG_SITE.get(frappe.get_system_settings("rounding_method") or "", "bankers")


def pruefe_ergebnis(ergebnis: dict, titel: str) -> dict:
	"""Fehler der Engine als eine Meldung (Pfad: Text) - alles oder nichts."""
	if ergebnis.get("ok"):
		return ergebnis
	zeilen = [f"{m.get('pfad') or '-'}: {m.get('meldung')}" for m in ergebnis.get("fehler") or []]
	frappe.throw(
		_("{0}: Berechnung nicht möglich.").format(titel)
		+ "<br>"
		+ "<br>".join(frappe.utils.escape_html(z) for z in zeilen),
		AbrechnungFehler,
		title=titel,
	)
	return ergebnis


def plan_rechnen(plan: dict, auftrag: dict) -> dict:
	return pruefe_ergebnis(
		_kern(KERN_ABSCHLAGSPLAN)(plan, auftrag, {"rundung": rundung()}), _("Abschlagsplan")
	)


def abschlag_rechnen(
	auftrag: dict, nr: int, prozent=None, betrag=None, bereits_gestellt=None, bezeichnung=None
) -> dict:
	return pruefe_ergebnis(
		_kern(KERN_ABSCHLAG)(
			auftrag, nr, prozent, betrag, bereits_gestellt, bezeichnung, {"rundung": rundung()}
		),
		_("Abschlagsrechnung"),
	)


def schlussrechnung_rechnen(
	auftrag: dict, positionen: list, nachtraege: list, abschlaege: list, optionen: dict
) -> dict:
	optionen = {**optionen, "rundung": rundung()}
	return pruefe_ergebnis(
		_kern(KERN_SCHLUSSRECHNUNG)(auftrag, positionen, nachtraege, abschlaege, optionen),
		_("Schlussrechnung"),
	)
