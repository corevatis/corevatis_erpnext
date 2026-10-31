"""Offene Posten (7.5): offene Rechnungen mit Rest, Fälligkeit, Tagen überfällig, Mahnstufe und Vorgang
- Quelle der Liste „Offene Posten“ (Script Report) - sowie Zahlungs- und Mahnstand je Rechnung für die
Prozessleiste („bezahlt am …“, „teilbezahlt“, „offen …, fällig seit 5 Tagen, 1. Mahnung vom …“) und das
Rechnungsformular.

Rechte: Die Liste holt die Rechnungen über ``frappe.get_list`` (Rechte des Benutzers) und ist für
Büro, Meister und System Manager; Beträge kommen immer aus den Belegen.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, fmt_money, format_date, getdate, today

from corevatis_erpnext.abrechnung.auftrag import ist_bezahlt

ROLLEN_LESEN = ("Büro", "Meister", "System Manager")
RECHNUNG_FELDER = (
	"name",
	"customer",
	"customer_name",
	"company",
	"currency",
	"posting_date",
	"due_date",
	"grand_total",
	"outstanding_amount",
	"status",
	"docstatus",
	"is_return",
	"project",
)
CV_FELDER = ("cv_auftrag", "cv_abschlag", "cv_abschlag_nr", "cv_schlussrechnung")


def darf_lesen(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(ROLLEN_LESEN) & set(frappe.get_roles(user)))


def _in(namen) -> list[str]:
	return sorted({n for n in namen if n})


def mahnstand(namen) -> dict[str, frappe._dict]:
	"""Je Rechnung die höchste gebuchte Mahnstufe (``Dunning.cv_mahnstufe``, sonst ERPNexts
	``dunning_level`` - 1), die letzte Mahnung mit Datum und Status, die Anzahl der Mahnungen."""
	namen = _in(namen)
	if not namen or not frappe.db.exists("DocType", "Dunning"):
		return {}
	mit_stufe = frappe.db.has_column("Dunning", "cv_mahnstufe")
	stufe_sql = "d.cv_mahnstufe" if mit_stufe else "NULL"
	zeilen = frappe.db.sql(
		f"""select op.sales_invoice, d.name as mahnung, d.posting_date as datum, d.status,
			{stufe_sql} as cv_mahnstufe, op.dunning_level
		from `tabOverdue Payment` op
		join `tabDunning` d on d.name = op.parent
		where op.parenttype = 'Dunning' and d.docstatus = 1 and op.sales_invoice in %(namen)s
		order by d.posting_date asc, d.creation asc""",
		{"namen": namen},
		as_dict=True,
	)
	stand: dict[str, frappe._dict] = {}
	for z in zeilen:
		stufe = cint(z.cv_mahnstufe) if z.cv_mahnstufe is not None else max(cint(z.dunning_level) - 1, 0)
		s = stand.setdefault(z.sales_invoice, frappe._dict(stufe=-1, anzahl=0))
		s.anzahl += 1
		if stufe >= s.stufe:
			s.update(stufe=stufe, mahnung=z.mahnung, datum=z.datum, status=z.status)
	return stand


def bezahlt_am(namen) -> dict[str, object]:
	"""Datum der letzten gebuchten Zahlung (Payment Entry) je Rechnung."""
	namen = _in(namen)
	if not namen:
		return {}
	zeilen = frappe.db.sql(
		"""select per.reference_name, max(pe.posting_date) as datum
		from `tabPayment Entry Reference` per
		join `tabPayment Entry` pe on pe.name = per.parent
		where pe.docstatus = 1 and per.reference_doctype = 'Sales Invoice' and per.reference_name in %(namen)s
		group by per.reference_name""",
		{"namen": namen},
		as_dict=True,
	)
	return {z.reference_name: z.datum for z in zeilen}


def mahnstufe_text(stufe) -> str:
	from corevatis_erpnext.abrechnung.mahnung import LABELS

	return LABELS.get(cint(stufe), _("Mahnung")) if stufe is not None else ""


def ist_teilbezahlt(r) -> bool:
	return (
		r.docstatus == 1
		and not ist_bezahlt(r)
		and flt(r.grand_total) > 0
		and flt(r.outstanding_amount) < flt(r.grand_total) - 0.005
	)


def tage_ueberfaellig(r, heute=None) -> int:
	if not r.get("due_date"):
		return 0
	return max(cint(date_diff(heute or today(), r.due_date)), 0)


def zahlungsinfo(r, currency=None, stand=None, bezahlt=None) -> str:
	"""Kurztext zum Zahlungsstand einer gebuchten Rechnung (Prozessleiste, Rechnungsformular):
	„bezahlt am 05.11.2026“ · „teilbezahlt, offen 500,00 €, fällig seit 5 Tagen, 1. Mahnung vom 20.11.2026“
	· „offen 1.431,26 €, fällig am 17.11.2026“."""
	currency = currency or r.get("currency") or None
	if ist_bezahlt(r):
		return _("bezahlt am {0}").format(format_date(bezahlt)) if bezahlt else _("bezahlt")
	teile = []
	if ist_teilbezahlt(r):
		teile.append(_("teilbezahlt"))
	teile.append(_("offen {0}").format(fmt_money(flt(r.outstanding_amount), currency=currency)))
	if r.get("due_date"):
		tage = cint(date_diff(today(), r.due_date))
		if tage > 0:
			teile.append(_("fällig seit {0} Tagen").format(tage))
		else:
			teile.append(_("fällig am {0}").format(format_date(r.due_date)))
	if stand and stand.get("stufe") is not None and stand.get("datum"):
		teile.append(_("{0} vom {1}").format(mahnstufe_text(stand.stufe), format_date(stand.datum)))
	return ", ".join(teile)


def rechnungsart_text(r) -> str:
	if cint(r.get("cv_abschlag")):
		nr = cint(r.get("cv_abschlag_nr"))
		return _("Abschlag {0}").format(nr) if nr else _("Abschlag")
	if cint(r.get("cv_schlussrechnung")):
		return _("Schlussrechnung")
	return _("Rechnung")


def _felder() -> list[str]:
	felder = list(RECHNUNG_FELDER)
	if frappe.db.has_column("Sales Invoice", "cv_schlussrechnung"):
		felder += list(CV_FELDER)
	return felder


def offene_rechnungen(filters: dict | None = None, company: str | None = None) -> list[frappe._dict]:
	"""Gebuchte, nicht bezahlte Rechnungen (keine Gutschriften) mit den Rechten des Benutzers."""
	f = {"docstatus": 1, "outstanding_amount": [">", 0], "is_return": 0}
	filters = filters or {}
	if company or filters.get("company"):
		f["company"] = company or filters["company"]
	if filters.get("kunde"):
		f["customer"] = filters["kunde"]
	if cint(filters.get("nur_ueberfaellig")):
		f["due_date"] = ["<", today()]
	return frappe.get_list(
		"Sales Invoice",
		filters=f,
		fields=_felder(),
		order_by="due_date asc, posting_date asc",
		limit_page_length=0,
	)


@frappe.whitelist()
def offene_posten(filters=None) -> list[dict]:
	"""Zeilen der Liste „Offene Posten“ (Büro, Meister, System Manager)."""
	from corevatis_erpnext.abrechnung import mahnung

	if not darf_lesen():
		frappe.throw(_("Die offenen Posten sehen Büro und Meister."), frappe.PermissionError)
	if isinstance(filters, str):
		filters = frappe.parse_json(filters) if filters else {}
	filters = frappe._dict(filters or {})
	rechnungen = offene_rechnungen(filters)
	namen = [r.name for r in rechnungen]
	stand = mahnstand(namen)
	e = mahnung.einstellungen()
	heute = getdate(today())
	zeilen = []
	for r in rechnungen:
		s = stand.get(r.name)
		v = mahnung.vorschlag(r, s, heute, e)
		if cint(filters.get("nur_mahnbar")) and not v["moeglich"]:
			continue
		zeilen.append(
			{
				"rechnung": r.name,
				"art": rechnungsart_text(r),
				"kunde": r.customer,
				"kunde_name": r.customer_name,
				"datum": r.posting_date,
				"faellig": r.due_date,
				"tage_ueberfaellig": tage_ueberfaellig(r, heute),
				"brutto": flt(r.grand_total),
				"offen": flt(r.outstanding_amount),
				"teilbezahlt": 1 if ist_teilbezahlt(r) else 0,
				"mahnstufe": mahnstufe_text(s.stufe) if s else "",
				"mahnung": s.mahnung if s else None,
				"mahnung_datum": s.datum if s else None,
				"vorschlag": v["label"] if v["moeglich"] else "",
				"vorschlag_stufe": v["stufe"] if v["moeglich"] else None,
				"hinweis": v["grund"],
				"vorgang": r.project,
				"auftrag": r.get("cv_auftrag"),
				"currency": r.currency,
			}
		)
	return zeilen
