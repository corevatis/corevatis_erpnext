"""Aufgabe 6.2: Abnahme und Gewährleistung am Projekt.

``projekt_aktualisieren(projekt)`` (beim Abschluss und Wiederöffnen eines Protokolls) übernimmt das
**zuletzt abgeschlossene** Abnahmeprotokoll des Projekts:

- ``cv_abnahmeprotokoll``, ``cv_abnahme_ergebnis``;
- ``cv_abgenommen_am`` = Gewährleistungsbeginn des Protokolls (Abnahmedatum), nur bei „Abgenommen“
  oder „Abgenommen mit Mängeln“ - bei „Abnahme verweigert“ leer;
- ``cv_vertragsgrundlage`` (leer → Einstellungen, Vorgabe BGB), ``cv_gewaehrleistung_jahre``
  (leer → BGB 5 bzw. VOB/B 4 Jahre aus den Einstellungen), ``cv_gewaehrleistung_bis`` =
  abgenommen am + Jahre (Fristende am gleichen Kalendertag, §§ 187 Abs. 1, 188 Abs. 2 BGB).

Ohne abgeschlossenes Protokoll werden die Felder geleert. Das Projekt gilt als **abgenommen**
(Rechnungsfreigabe, ``rechnung``), wenn ``cv_abgenommen_am`` gesetzt ist. ERPNexts eigener
Projektstatus (Open/Completed/Cancelled) bleibt unberührt: offene Mangel-Tasks laufen weiter.

``projekt_validieren`` (``Project.validate``): die Abnahmefelder setzt nur der Server - ein
Benutzer (Desk/REST) kann sie nicht fälschen und so die Rechnungssperre umgehen; Vertragsgrundlage
und Jahre darf das Büro ändern, ``cv_gewaehrleistung_bis`` wird nachgerechnet.
"""

import frappe
from frappe import _
from frappe.utils import add_years, cint, getdate

from corevatis_erpnext.abnahme import konstanten as K
from corevatis_erpnext.abnahme.einstellungen import einstellungen, jahre_fuer

SERVER_FELDER = ("cv_abnahmeprotokoll", "cv_abnahme_ergebnis", "cv_abgenommen_am")
FELDER = (*SERVER_FELDER, "cv_vertragsgrundlage", "cv_gewaehrleistung_jahre", "cv_gewaehrleistung_bis")


def bis(abgenommen_am, jahre: int):
	return add_years(getdate(abgenommen_am), cint(jahre)) if abgenommen_am and cint(jahre) > 0 else None


def _wert(feld: str, wert):
	if feld in ("cv_abgenommen_am", "cv_gewaehrleistung_bis"):
		return getdate(wert) if wert else None
	if feld == "cv_gewaehrleistung_jahre":
		return cint(wert)
	return wert or None


def _felder_da() -> bool:
	return bool(frappe.db.has_column("Project", "cv_abgenommen_am"))


def letztes_protokoll(projekt: str) -> frappe._dict | None:
	if not frappe.db.exists("DocType", K.ABNAHMEPROTOKOLL):
		return None
	zeilen = frappe.get_all(
		K.ABNAHMEPROTOKOLL,
		filters={"projekt": projekt, "status": K.STATUS_ABGESCHLOSSEN},
		fields=["name", "ergebnis", "datum", "gewaehrleistung_beginn", "abgeschlossen_am"],
		order_by="abgeschlossen_am desc, modified desc",
		limit=1,
	)
	return zeilen[0] if zeilen else None


def projekt_aktualisieren(projekt: str) -> dict:
	"""Abnahmefelder des Projekts aus dem letzten abgeschlossenen Protokoll. Rückgabe: neue Werte."""
	if not _felder_da():
		return {}
	alt = frappe.db.get_value("Project", projekt, list(FELDER), as_dict=True) or frappe._dict()
	z = letztes_protokoll(projekt)
	werte = dict.fromkeys(FELDER[:3])
	werte["cv_gewaehrleistung_bis"] = None
	if z:
		werte["cv_abnahmeprotokoll"] = z.name
		werte["cv_abnahme_ergebnis"] = z.ergebnis
		if z.ergebnis in K.ERGEBNISSE_ABGENOMMEN:
			werte["cv_abgenommen_am"] = getdate(z.gewaehrleistung_beginn or z.datum)
	if werte["cv_abgenommen_am"]:
		e = einstellungen()
		grundlage = alt.get("cv_vertragsgrundlage") or e.vertragsgrundlage
		jahre = cint(alt.get("cv_gewaehrleistung_jahre")) or jahre_fuer(grundlage, e)
		werte.update(
			cv_vertragsgrundlage=grundlage,
			cv_gewaehrleistung_jahre=jahre,
			cv_gewaehrleistung_bis=bis(werte["cv_abgenommen_am"], jahre),
		)
	geaendert = {k: v for k, v in werte.items() if _wert(k, alt.get(k)) != _wert(k, v)}
	if geaendert:
		frappe.db.set_value("Project", projekt, geaendert)
		if {"cv_abnahmeprotokoll", "cv_abnahme_ergebnis"} & set(geaendert):
			if z:
				text = _("Abnahme {0}: {1}").format(z.name, z.ergebnis)
				if werte["cv_gewaehrleistung_bis"]:
					text += _(", Gewährleistung bis {0}").format(
						frappe.utils.formatdate(werte["cv_gewaehrleistung_bis"])
					)
			else:
				text = _("Abnahme aufgehoben (Protokoll wieder geöffnet)")
			frappe.get_doc("Project", projekt).add_comment("Info", text)
	return werte


def projekt_validieren(doc, method=None) -> None:
	"""``Project.validate``: Abnahmefelder schützen, Gewährleistung nachrechnen."""
	if not doc.meta.has_field("cv_abgenommen_am"):
		return
	if doc.is_new():
		for f in SERVER_FELDER:
			doc.set(f, None)
	else:
		gespeichert = frappe.db.get_value("Project", doc.name, list(SERVER_FELDER), as_dict=True) or {}
		for f in SERVER_FELDER:
			doc.set(f, gespeichert.get(f))
	if doc.cv_vertragsgrundlage and doc.cv_vertragsgrundlage not in ("BGB", "VOB/B"):
		frappe.throw(_("Unbekannte Vertragsgrundlage {0}.").format(doc.cv_vertragsgrundlage))
	if not doc.cv_abgenommen_am:
		doc.cv_gewaehrleistung_bis = None
		return
	jahre = cint(doc.cv_gewaehrleistung_jahre) or jahre_fuer(doc.cv_vertragsgrundlage)
	if not 0 < jahre <= 30:
		frappe.throw(_("Gewährleistung: bitte 1 bis 30 Jahre angeben."))
	doc.cv_gewaehrleistung_jahre = jahre
	doc.cv_gewaehrleistung_bis = bis(doc.cv_abgenommen_am, jahre)


def abgenommen(projekt: str) -> bool:
	"""Projekt abgenommen (auch mit Mängeln bzw. Vorbehalt) - Grundlage der Rechnungsfreigabe."""
	if not _felder_da():
		return False
	w = frappe.db.get_value("Project", projekt, ["cv_abnahme_ergebnis", "cv_abgenommen_am"], as_dict=True)
	return bool(w and w.cv_abgenommen_am and w.cv_abnahme_ergebnis in K.ERGEBNISSE_ABGENOMMEN)
