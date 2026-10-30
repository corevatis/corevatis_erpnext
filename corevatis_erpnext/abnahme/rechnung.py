"""Aufgabe 6.2: Rechnungsfreigabe erst nach der Abnahme (Abnahme „Rechnung ohne Abnahme blockiert“).

``Sales Invoice.validate``: Gehört die Rechnung zu einem Projekt (``project`` im Kopf, ``project``
einer Position oder über den Auftrag einer Position: ``Project.sales_order``/``Sales Order.project``)
und ist eines dieser Projekte **nicht abgenommen** (``garantie.abgenommen``: Ergebnis „Abgenommen“
oder „Abgenommen mit Mängeln“ = mit Vorbehalt), wird sie abgelehnt - außer

- sie ist als **Abschlagsrechnung** markiert (``cv_abschlag``, Vorgabe 0; ANNAHME bis 7.1, das
  Abschläge über den Abschlagsplan erzeugt und das Feld übernimmt), oder
- ein **System Manager** übersteuert mit Begründung (``cv_abnahme_uebersteuert``,
  ``cv_uebersteuerung_grund`` ≥ 5 Zeichen; ``cv_uebersteuert_von`` und ein Kommentar an der
  Rechnung protokollieren es). Andere Benutzer können die Übersteuerung weder setzen noch ändern.

Gutschriften (``is_return``) und Rechnungen ohne Projekt sind nie betroffen; die Sperre lässt sich
in den Abnahme-Einstellungen abschalten.
"""

import frappe
from frappe import _
from frappe.utils import cint, cstr

from corevatis_erpnext.abnahme import konstanten as K
from corevatis_erpnext.abnahme.einstellungen import einstellungen
from corevatis_erpnext.abnahme.garantie import abgenommen

BEGRUENDUNG_MIN = 5


class RechnungOhneAbnahme(frappe.ValidationError):
	"""Rechnung zu einem nicht abgenommenen Projekt (6.2)."""


def rechnung_projekte(doc) -> list[str]:
	projekte = {doc.get("project")}
	auftraege = set()
	for zeile in doc.get("items") or []:
		projekte.add(zeile.get("project"))
		auftraege.add(zeile.get("sales_order"))
	auftraege.discard(None)
	auftraege.discard("")
	if auftraege:
		projekte |= set(
			frappe.get_all("Project", filters={"sales_order": ["in", list(auftraege)]}, pluck="name")
		)
		projekte |= set(
			frappe.get_all("Sales Order", filters={"name": ["in", list(auftraege)]}, pluck="project")
		)
	return sorted(p for p in projekte if p and frappe.db.exists("Project", p))


def _uebersteuerung(doc) -> None:
	"""Übersteuerung nur durch System Manager, mit Begründung; protokolliert."""
	if not doc.meta.has_field("cv_abnahme_uebersteuert"):
		return
	vorher = None if doc.is_new() else doc.get_doc_before_save()
	an = cint(doc.cv_abnahme_uebersteuert)
	grund = cstr(doc.cv_uebersteuerung_grund).strip()
	alt = (
		(cint(vorher.get("cv_abnahme_uebersteuert")), cstr(vorher.get("cv_uebersteuerung_grund")).strip())
		if vorher
		else (0, "")
	)
	if (an, grund if an else "") == (alt[0], alt[1] if alt[0] else ""):
		if vorher is not None:
			doc.cv_uebersteuert_von = vorher.get("cv_uebersteuert_von")
		elif not an:
			doc.cv_uebersteuert_von = None
		return
	if K.ROLLE_UEBERSTEUERN not in frappe.get_roles():
		frappe.throw(
			_("Eine Rechnung ohne Abnahme kann nur ein System Manager freigeben (Übersteuerung)."),
			frappe.PermissionError,
		)
	if an:
		if len(grund) < BEGRUENDUNG_MIN:
			frappe.throw(
				_("Bitte eine Begründung für die Übersteuerung angeben (mindestens {0} Zeichen).").format(
					BEGRUENDUNG_MIN
				),
				frappe.MandatoryError,
			)
		doc.cv_uebersteuerung_grund = grund
		doc.cv_uebersteuert_von = frappe.session.user
		doc.flags.cv_uebersteuerung_kommentar = _("Rechnung ohne Abnahme freigegeben von {0}: {1}").format(
			frappe.session.user, grund
		)
	else:
		doc.cv_uebersteuerung_grund = None
		doc.cv_uebersteuert_von = None
		doc.flags.cv_uebersteuerung_kommentar = _(
			"Übersteuerung „Rechnung ohne Abnahme“ aufgehoben von {0}"
		).format(frappe.session.user)


def rechnung_pruefen(doc, method=None) -> None:
	"""``Sales Invoice.validate`` (Entwurf speichern und Buchen)."""
	_uebersteuerung(doc)
	if cint(doc.get("is_return")) or not einstellungen().rechnung_ohne_abnahme_sperren:
		return
	offen = [p for p in rechnung_projekte(doc) if not abgenommen(p)]
	if not offen:
		return
	if cint(doc.get("cv_abschlag")) or cint(doc.get("cv_abnahme_uebersteuert")):
		return
	frappe.throw(
		_(
			"Rechnung zu Projekt {0} ist noch nicht möglich: Das Projekt ist nicht abgenommen. "
			"Bitte zuerst das Abnahmeprotokoll abschließen. Eine Abschlagsrechnung bitte als "
			"„Abschlagsrechnung“ markieren; nur ein System Manager kann mit Begründung übersteuern."
		).format(", ".join(offen)),
		RechnungOhneAbnahme,
		title=_("Rechnung ohne Abnahme"),
	)


def rechnung_kommentar(doc, method=None) -> None:
	"""``Sales Invoice.on_update``: Protokoll der Übersteuerung als Kommentar an der Rechnung."""
	text = doc.flags.pop("cv_uebersteuerung_kommentar", None)
	if text:
		doc.add_comment("Info", text)
