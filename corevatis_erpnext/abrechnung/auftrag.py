"""Gemeinsame Hilfen der Abrechnung (7.1/7.2): Rechte, Auftrag, Steuersatz, gestellte Rechnungen,
abgerechneter Anteil am Auftrag.

**Abgerechneter Anteil (Abnahmekriterium #48, „Prozent abgerechnet stimmt“):** ERPNext rechnet
``Sales Order.per_billed`` aus ``Sales Order Item.billed_amt``, und das nur für Rechnungszeilen, die
eine Auftragszeile nennen (``so_detail``). Eine Abschlagsrechnung hat eine Service-Position ohne
Auftragszeile. ``abrechnung_aktualisieren`` verteilt deshalb die Netto-Summe der freigegebenen
Abschlagsrechnungen anteilig (nach Zeilenbetrag) auf die Auftragszeilen und setzt ``billed_amt`` =
direkt abgerechnet (ERPNext-Summe über ``so_detail``) + Abschlagsanteil; nach einer freigegebenen
Schlussrechnung gilt jede Zeile als voll abgerechnet. Danach rechnet ERPNext selbst ``per_billed`` und
den Status (``update_billing_percentage``). Läuft nach jedem Freigeben/Stornieren einer Rechnung zum
Auftrag - auch nach ERPNexts eigener Neuberechnung, die den Abschlagsanteil sonst überschriebe.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

from corevatis_erpnext.abrechnung.kern import AbrechnungFehler
from corevatis_erpnext.planung.auftrag import auftrag_sperren, projekt_zum_auftrag

ROLLEN = ("Büro", "System Manager")
STEUER_AUF_NETTO = ("On Net Total",)
RECHNUNG_FELDER = (
	"name",
	"docstatus",
	"status",
	"posting_date",
	"net_total",
	"total_taxes_and_charges",
	"grand_total",
	"outstanding_amount",
	"is_return",
	"cv_auftrag",
	"cv_abschlag",
	"cv_schlussrechnung",
	"cv_abschlag_nr",
	"cv_abschlag_prozent",
	"cv_abschlag_zeile",
	"cv_abschlagsplan",
	"cv_lohnanteil_netto",
)


def darf_abrechnen(user: str | None = None) -> bool:
	"""Rechnungen stellt nur das Büro (und System Manager); der Meister liest."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(set(ROLLEN) & set(frappe.get_roles(user)))


def pruefe_rolle() -> None:
	if not darf_abrechnen():
		frappe.throw(
			_("Abschlags- und Schlussrechnungen stellt nur das Büro (Rolle Büro)."), frappe.PermissionError
		)


def felder_da() -> bool:
	return bool(frappe.db.has_column("Sales Invoice", "cv_schlussrechnung"))


def auftrag_laden(sales_order: str | None, project: str | None, sperren: bool = True):
	"""Eingereichter Auftrag (direkt oder über ``Project.sales_order``) mit Leserecht; ``sperren``:
	Zeilensperre vor dem Schreiben (Doppelklick, parallele Aufrufe - Muster 5.1).

	Rückgabe ``(auftrag, projekt)``. Ein Projekt ohne Auftrag ist ein Fehler: ohne Auftragssumme kein
	Abschlag. Ein mitgegebenes Projekt braucht Leserecht und muss zum Auftrag gehören (Security-Review).
	"""
	if project:
		frappe.has_permission("Project", "read", project, throw=True)
	if not sales_order and project:
		sales_order = frappe.db.get_value("Project", project, "sales_order")
		if not sales_order:
			frappe.throw(
				_("Projekt {0} hat keinen Auftrag - ohne Auftragssumme keine Abrechnung.").format(project),
				AbrechnungFehler,
			)
	if not sales_order:
		frappe.throw(_("Bitte einen Auftrag oder ein Projekt angeben."), frappe.MandatoryError)
	if sperren:
		auftrag_sperren(sales_order)
	elif not frappe.db.exists("Sales Order", sales_order):
		frappe.throw(_("Auftrag {0} nicht gefunden.").format(sales_order), frappe.DoesNotExistError)
	frappe.has_permission("Sales Order", "read", sales_order, throw=True)
	auftrag = frappe.get_doc("Sales Order", sales_order)
	if auftrag.docstatus != 1:
		frappe.throw(
			_("Auftrag {0} ist nicht eingereicht - Rechnungen erst nach dem Einreichen.").format(
				auftrag.name
			),
			AbrechnungFehler,
		)
	if project and not (
		auftrag.get("project") == project
		or frappe.db.get_value("Project", project, "sales_order") == auftrag.name
	):
		frappe.throw(
			_("Projekt {0} gehört nicht zum Auftrag {1}.").format(project, auftrag.name), AbrechnungFehler
		)
	return auftrag, project or projekt_zum_auftrag(auftrag)


def steuersatz(auftrag) -> float:
	"""Steuersatz des Auftrags: Summe der Steuerzeilen auf den Nettobetrag (Umsatzsteuer 19 %)."""
	return flt(
		sum(
			flt(z.rate)
			for z in auftrag.get("taxes") or []
			if z.charge_type in STEUER_AUF_NETTO and not cint(z.included_in_print_rate)
		)
	)


def auftrag_eingabe(auftrag, lohnanteil_netto: float | None = None) -> dict:
	"""Auftrag für die Engine: Name, Auftragssumme netto, Steuersatz, Lohnanteil des Auftrags."""
	eingabe = {
		"name": auftrag.name,
		"netto": str(flt(auftrag.net_total, 2)),
		"steuersatz": str(steuersatz(auftrag)),
	}
	if lohnanteil_netto is not None:
		eingabe["lohnanteil_netto"] = str(flt(lohnanteil_netto, 2))
	return eingabe


def rechnungen_zum_auftrag(sales_order: str, docstatus=None, art: str | None = None) -> list[frappe._dict]:
	"""Rechnungen mit ``cv_auftrag`` = Auftrag (keine Gutschriften), älteste zuerst.

	``art``: ``abschlag`` oder ``schlussrechnung``; ``docstatus``: Zahl oder Liste.
	"""
	if not felder_da():
		return []
	filters = {"cv_auftrag": sales_order, "is_return": 0}
	if docstatus is not None:
		filters["docstatus"] = ["in", docstatus] if isinstance(docstatus, (list, tuple)) else docstatus
	else:
		filters["docstatus"] = ["<", 2]
	if art == "abschlag":
		filters["cv_abschlag"] = 1
	elif art == "schlussrechnung":
		filters["cv_schlussrechnung"] = 1
	return frappe.get_all(
		"Sales Invoice",
		filters=filters,
		fields=list(RECHNUNG_FELDER),
		order_by="cv_abschlag_nr asc, posting_date asc, creation asc",
	)


def abschlaege_gestellt(sales_order: str) -> list[frappe._dict]:
	"""Freigegebene Abschlagsrechnungen zum Auftrag (die Abzüge der Schlussrechnung)."""
	return [r for r in rechnungen_zum_auftrag(sales_order, docstatus=1, art="abschlag")]


def schlussrechnung_zum_auftrag(sales_order: str) -> frappe._dict | None:
	zeilen = rechnungen_zum_auftrag(sales_order, art="schlussrechnung")
	return zeilen[-1] if zeilen else None


def ist_bezahlt(rechnung: frappe._dict) -> bool:
	return rechnung.docstatus == 1 and (rechnung.status == "Paid" or flt(rechnung.outstanding_amount) <= 0)


# --- Abgerechneter Anteil am Auftrag ----------------------------------------------------------


def _direkt_abgerechnet(so_detail: str) -> float:
	"""Wie ERPNext ``update_billed_amount_based_on_so``: Rechnungszeilen zur Auftragszeile."""
	wert = frappe.db.sql(
		"""select coalesce(sum(si_item.amount), 0)
		from `tabSales Invoice Item` si_item
		join `tabSales Invoice` si on si.name = si_item.parent
		where si_item.so_detail = %s and ifnull(si_item.dn_detail, '') = ''
		and si_item.docstatus = 1 and si.update_stock = 0""",
		so_detail,
	)
	return flt(wert[0][0]) if wert else 0.0


def abschlagssumme(sales_order: str) -> float:
	"""Netto der freigegebenen Abschlagsrechnungen abzüglich Gutschriften darauf."""
	abschlaege = abschlaege_gestellt(sales_order)
	summe = sum(flt(r.net_total) for r in abschlaege)
	if abschlaege:
		gutschriften = frappe.get_all(
			"Sales Invoice",
			filters={"is_return": 1, "docstatus": 1, "return_against": ["in", [r.name for r in abschlaege]]},
			pluck="net_total",
		)
		summe += sum(flt(g) for g in gutschriften)  # Gutschriften sind negativ
	return max(summe, 0.0)


def abrechnung_aktualisieren(sales_order: str) -> dict:
	"""``billed_amt`` je Auftragszeile = direkt abgerechnet + Anteil der Abschläge; nach der
	Schlussrechnung voll. Danach ERPNext ``per_billed``/Status. Rückgabe: Kennzahlen."""
	if not frappe.db.exists("Sales Order", sales_order):
		return {}
	auftrag = frappe.get_doc("Sales Order", sales_order)
	if auftrag.docstatus != 1:
		return {}
	gesamt = sum(flt(z.amount) for z in auftrag.items)
	abschlag = abschlagssumme(sales_order) if felder_da() else 0.0
	schluss = schlussrechnung_zum_auftrag(sales_order)
	voll = bool(schluss and schluss.docstatus == 1)
	for zeile in auftrag.items:
		anteil = abschlag * flt(zeile.amount) / gesamt if gesamt else 0.0
		abgerechnet = _direkt_abgerechnet(zeile.name) + anteil
		if voll:
			abgerechnet = max(abgerechnet, flt(zeile.amount))
		frappe.db.set_value(
			"Sales Order Item",
			zeile.name,
			"billed_amt",
			flt(abgerechnet, zeile.precision("billed_amt") or 2),
			update_modified=False,
		)
	auftrag.update_billing_percentage(update_modified=True)
	# ERPNext setzt ``billing_status`` nur auf dem Weg über Rechnungszeilen mit Auftragszeile
	per_billed = flt(frappe.db.get_value("Sales Order", sales_order, "per_billed"))
	billing_status = (
		"Not Billed" if per_billed <= 0 else ("Fully Billed" if per_billed >= 100 else "Partly Billed")
	)
	if frappe.db.get_value("Sales Order", sales_order, "billing_status") != billing_status:
		frappe.db.set_value(
			"Sales Order", sales_order, "billing_status", billing_status, update_modified=False
		)
	return {
		"auftrag": sales_order,
		"abschlaege_netto": flt(abschlag, 2),
		"schlussrechnung": schluss.name if schluss else None,
		"per_billed": per_billed,
		"billing_status": billing_status,
	}


def auftraege_der_rechnung(doc) -> list[str]:
	namen = {doc.get("cv_auftrag")}
	for zeile in doc.get("items") or []:
		namen.add(zeile.get("sales_order"))
	return sorted(n for n in namen if n)


# --- Hooks an Sales Invoice und Payment Entry -------------------------------------------------

# Felder, die nur der Server setzt (``abschlag_stellen``/``schlussrechnung_erstellen`` mit
# ``doc.flags.cv_server``); ``cv_abschlag`` bleibt wie in 6.2 vom Büro setzbar (Abnahmesperre)
SERVER_FELDER = (
	"cv_schlussrechnung",
	"cv_auftrag",
	"cv_abschlagsplan",
	"cv_abschlag_nr",
	"cv_abschlag_prozent",
	"cv_abschlag_zeile",
	"cv_auftragssumme_netto",
	"cv_kumuliert_prozent",
	"cv_kumuliert_netto",
	"cv_leistung_netto",
	"cv_leistung_steuer",
	"cv_leistung_brutto",
	"cv_abschlaege_netto",
	"cv_abschlaege_steuer",
	"cv_abschlaege_brutto",
	"cv_lohnanteil_netto",
	"cv_lohnanteil_steuer",
	"cv_lohnanteil_brutto",
	"cv_lohnanteil_prozent",
	"cv_abrechnung_json",
)
SERVER_FELDER_POSITION = ("cv_positionsart", "cv_menge_auftrag", "cv_lohnanteil", "cv_bezug")
ABSCHLAEGE_TABELLE = "cv_abschlaege"


def rechnung_validieren(doc, method=None) -> None:
	"""``Sales Invoice.validate``: die Abrechnungsfelder setzt nur der Server (Security-Review) -
	Werte aus Formular/REST fallen auf den gespeicherten Stand zurück (neu: leer), damit niemand eine
	Rechnung als Abschlag/Schlussrechnung eines Auftrags ausgibt und so Plan oder ``per_billed`` steuert."""
	if doc.flags.cv_server or not felder_da():
		return
	vorher = None if doc.is_new() else doc.get_doc_before_save()
	for feld in SERVER_FELDER:
		if not doc.meta.has_field(feld):
			continue
		wert = vorher.get(feld) if vorher else None
		if feld == "cv_schlussrechnung":
			wert = cint(wert)
		doc.set(feld, wert)
	if doc.meta.has_field(ABSCHLAEGE_TABELLE):
		alte = vorher.get(ABSCHLAEGE_TABELLE) if vorher else []
		doc.set(ABSCHLAEGE_TABELLE, [z.as_dict() for z in alte or []])
	alte_zeilen = {z.name: z for z in (vorher.get("items") if vorher else []) or []}
	for zeile in doc.get("items") or []:
		alt = alte_zeilen.get(zeile.name)
		for feld in SERVER_FELDER_POSITION:
			if zeile.meta.has_field(feld):
				zeile.set(feld, alt.get(feld) if alt else None)


def _rechnung_passt_zum_auftrag(doc, auftrag) -> None:
	"""Freigabe einer Abschlags-/Schlussrechnung: Kunde und Positionen gehören zum Auftrag."""
	if doc.customer != auftrag.customer:
		frappe.throw(
			_("Rechnung {0}: Kunde {1} passt nicht zum Auftrag {2} ({3}).").format(
				doc.name, doc.customer, auftrag.name, auftrag.customer
			),
			AbrechnungFehler,
		)
	zeilen = {z.name for z in auftrag.items}
	for zeile in doc.get("items") or []:
		if zeile.get("sales_order") != auftrag.name or (
			zeile.get("so_detail") and zeile.so_detail not in zeilen
		):
			frappe.throw(
				_("Rechnung {0}: Position {1} ({2}) gehört nicht zum Auftrag {3}.").format(
					doc.name, zeile.idx, zeile.item_code, auftrag.name
				),
				AbrechnungFehler,
			)


def rechnung_freigabe_pruefen(doc) -> None:
	"""Vor dem Freigeben einer Abschlags-/Schlussrechnung: Rolle Büro, Leserecht am Auftrag, Kunde und
	Positionen passen zum Auftrag (Security-Review)."""
	if not doc.get("cv_auftrag"):
		return
	if not darf_abrechnen():
		frappe.throw(
			_("Abschlags- und Schlussrechnungen gibt nur das Büro frei (Rolle Büro)."), frappe.PermissionError
		)
	frappe.has_permission("Sales Order", "read", doc.cv_auftrag, throw=True)
	auftrag = frappe.get_doc("Sales Order", doc.cv_auftrag)
	if auftrag.docstatus != 1:
		frappe.throw(_("Auftrag {0} ist nicht eingereicht.").format(auftrag.name), AbrechnungFehler)
	_rechnung_passt_zum_auftrag(doc, auftrag)


def _nach_rechnung(doc) -> None:
	from corevatis_erpnext.abrechnung import plan

	for auftrag in auftraege_der_rechnung(doc):
		abrechnung_aktualisieren(auftrag)
		plan.aktualisieren_zum_auftrag(auftrag)


def rechnung_eingereicht(doc, method=None) -> None:
	"""``Sales Invoice.on_submit``: Freigabe prüfen, abgerechneter Anteil am Auftrag, Stand des Abschlagsplans."""
	rechnung_freigabe_pruefen(doc)
	_nach_rechnung(doc)


def rechnung_storniert(doc, method=None) -> None:
	"""``Sales Invoice.on_cancel``: Anteil zurück, Planzeile wieder offen."""
	_nach_rechnung(doc)


def rechnung_geloescht(doc, method=None) -> None:
	"""``Sales Invoice.on_trash`` (Entwurf gelöscht): Planzeile wieder offen."""
	from corevatis_erpnext.abrechnung import plan

	if doc.get("cv_abschlagsplan") and frappe.db.exists("Abschlagsplan", doc.cv_abschlagsplan):
		plan.aktualisieren(doc.cv_abschlagsplan, ohne_rechnung=doc.name)


def zahlung_gebucht(doc, method=None) -> None:
	"""``Payment Entry.on_submit``/``on_cancel``: Abschlagszeile „bezahlt“ nachführen (7.5 baut aus)."""
	from corevatis_erpnext.abrechnung import plan

	if not felder_da() or not frappe.db.exists("DocType", "Abschlagsplan"):
		return
	rechnungen = {
		z.reference_name for z in doc.get("references") or [] if z.reference_doctype == "Sales Invoice"
	}
	if not rechnungen:
		return
	plaene = frappe.get_all(
		"Sales Invoice",
		filters={"name": ["in", sorted(rechnungen)], "cv_abschlagsplan": ["is", "set"]},
		pluck="cv_abschlagsplan",
		distinct=True,
	)
	for name in plaene:
		plan.aktualisieren(name)
