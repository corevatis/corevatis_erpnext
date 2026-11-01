"""Materialwirtschaft (Ausbauplan Phase 2, P2.2, Handwerker#90): Material je Baustelle bestellen,
Lieferung buchen, Fahrzeug beladen, Mindestbestand - ERPNext Einkauf und Lager in Handwerkersprache.

Belege bleiben ERPNext-Standard, der Adapter führt nur in der Reihenfolge des Betriebs hindurch:

- **Materialbedarf** (Material Request, 5.1) → **Bestellung** (Purchase Order, ``bestellen``): je
  Lieferant ein Beleg (Standardlieferant des Artikels aus ``Item Default``/Artikelgruppe/Marke, sonst
  Wahl im Dialog), Projekt am Beleg und an jeder Zeile, Lieferung ins Hauptlager oder direkt ins
  Fahrzeuglager eines zugewiesenen Monteurs. Ein Entwurf der Materialanforderung wird dabei
  eingereicht (ERPNext verlangt das für die Bestellung).
- **Bestellung** → **Lieferung** (Purchase Receipt, ``lieferung_buchen``): Mengenabgleich je Zeile,
  Teillieferungen, Lager wählbar, Lieferscheinnummer; Buchen erhöht den Bestand. Die Rechnung des
  Lieferanten (Purchase Invoice) ist nicht Teil des Durchstichs (ERPNext „Erstellen → Eingangsrechnung“).
- **Fahrzeug beladen** (Stock Entry „Material Transfer“ Hauptlager → Fahrzeuglager, ``fahrzeug_beladen``)
  mit Vorschlag aus dem Materialbedarf der zugewiesenen Baustellen der nächsten Tage und dem
  Mindestbestand des Fahrzeugs (``fahrzeug_vorschlag``); Fahrzeuglager aus ``Employee.cv_fahrzeuglager``
  (3.11, ``corevatis_erpnext.material``).
- **Mindestbestand** je Artikel und Lager = ERPNext ``Item Reorder`` (Stückliste „Nachbestellung“ am
  Artikel): Art „Purchase“ (Bestellung ins Lager) oder „Transfer“ (Umlagerung aus dem Hauptlager, für
  Fahrzeuge). Der ERPNext-Automatikjob (``Stock Settings.auto_indent``) bleibt aus - er würde täglich
  Materialanforderungen anlegen und E-Mails verschicken; stattdessen zeigt der Bericht „Nachbestellen“
  die Artikel unter Mindestbestand und legt auf Klick Materialanforderungen an (``nachbestellung_anlegen``).
- **Prozessleiste**: Pille „Material: 2 offen, 1 bestellt, 3 geliefert“ am Projekt (``stand``).

Nachkalkulation (P2.1) bleibt unberührt: Ist-Material sind nur Materialentnahmen („Material Issue“);
Wareneingänge und Umlagerungen sind kein Verbrauch (``nachkalkulation.ist_material`` filtert nach Zweck).

Rechte: Büro bestellt und bucht Lieferungen (``ensure_rechte`` gibt Büro Lieferant, Bestellung, Lieferung,
Materialanforderung voll und Materialbuchung zum Beladen), Meister liest Bestellungen und Lieferungen und
belädt Fahrzeuge (Materialbuchung voll, 0.4). Whitelisted Methoden prüfen Rolle und Belegrecht selbst;
Belege entstehen mit den Rechten des Benutzers (kein ``ignore_permissions``), nur der Mindestbestand am
Artikel wird nach Rollenprüfung für Büro geschrieben (Artikel ist für Büro sonst nur lesbar).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from corevatis_erpnext import material
from corevatis_erpnext.planung import materialbedarf
from corevatis_erpnext.planung.auftrag import darf_planen
from corevatis_erpnext.planung.einstellungen import einstellungen as planung_einstellungen

ROLLEN = ("Büro", "Meister", "System Manager")
TYP_EINKAUF = "Purchase"
TYP_UMLAGERUNG = "Material Transfer"
ART_BESTELLUNG = "Purchase"
ART_UMLAGERUNG = "Transfer"
ZWECK_UMLAGERUNG = "Material Transfer"
MR_OFFEN = ("Draft", "Submitted", "Pending", "Partially Ordered", "Partially Received")
PO_ZU = ("Closed", "Cancelled", "Completed", "Delivered")
VOLL = ("read", "write", "create", "submit", "cancel", "amend", "report", "print")
LESEN = ("read", "report", "print")
RECHTE = {
	"Büro": {
		"Supplier": VOLL,
		"Purchase Order": VOLL,
		"Purchase Receipt": VOLL,
		"Material Request": VOLL,
		# Fahrzeug beladen (Umlagerung); Monteure buchen weiter nur Entwürfe (0.4)
		"Stock Entry": ("read", "write", "create", "submit", "report", "print"),
	},
	"Meister": {"Supplier": LESEN, "Purchase Order": LESEN, "Purchase Receipt": LESEN},
}
MAX_ZEILEN = 200
VORSCHAU_TAGE = 7


class MaterialwirtschaftFehler(frappe.ValidationError):
	pass


# --- Einrichtung ---------------------------------------------------------------------------------


def ensure_setup() -> None:
	"""Aus ``after_install``/``after_migrate``: Rechte, Automatik-Nachbestellung aus (idempotent)."""
	ensure_rechte()
	ensure_keine_automatik()


def ensure_rechte() -> None:
	"""Büro bestellt und bucht Lieferungen, Meister liest (Custom DocPerm, wie ``mitarbeiter.ensure_rechte``)."""
	from frappe.permissions import add_permission, update_permission_property

	for rolle, je_doctype in RECHTE.items():
		if not frappe.db.exists("Role", rolle):
			continue
		for doctype, rechte in je_doctype.items():
			if not frappe.db.exists("DocType", doctype):
				continue
			add_permission(doctype, rolle, 0)
			for recht in rechte:
				if not frappe.db.get_value(
					"Custom DocPerm", {"parent": doctype, "role": rolle, "permlevel": 0}, recht
				):
					update_permission_property(doctype, rolle, 0, recht, 1, validate=False)
	frappe.clear_cache()


def ensure_keine_automatik() -> None:
	"""ERPNext-Nachbestellautomatik aus: sie legt täglich Materialanforderungen an und verschickt
	E-Mails (kein Mail im MVP). Der Bericht „Nachbestellen“ ersetzt sie."""
	if not frappe.db.exists("DocType", "Stock Settings"):
		return
	werte = frappe.db.get_singles_dict("Stock Settings")
	if cint(werte.get("auto_indent")) or cint(werte.get("reorder_email_notify")):
		frappe.db.set_single_value(
			"Stock Settings", {"auto_indent": 0, "reorder_email_notify": 0}, update_modified=False
		)
		frappe.clear_cache(doctype="Stock Settings")


# --- Rechte, Helfer ---------------------------------------------------------------------------------


def pruefe_rolle() -> None:
	if not darf_planen():
		frappe.throw(
			_("Material bestellen und buchen dürfen nur die Rollen Büro und Meister."), frappe.PermissionError
		)


def _darf(doctype: str, ptype: str, name=None) -> bool:
	try:
		return bool(
			frappe.has_permission(doctype, ptype, doc=name) if name else frappe.has_permission(doctype, ptype)
		)
	except (frappe.DoesNotExistError, frappe.PermissionError):
		return False


def _zeilen(zeilen) -> list[dict]:
	if isinstance(zeilen, str):
		zeilen = frappe.parse_json(zeilen)
	if not isinstance(zeilen, list):
		frappe.throw(_("Bitte Positionen angeben."), frappe.MandatoryError)
	if len(zeilen) > MAX_ZEILEN:
		frappe.throw(_("Höchstens {0} Positionen je Aufruf.").format(MAX_ZEILEN), MaterialwirtschaftFehler)
	return [z for z in zeilen if isinstance(z, dict)]


def lager_pruefen(lager: str | None, firma: str) -> str:
	"""Buchbares Lager der Firma (nicht Gruppe, nicht deaktiviert), sonst Fehler."""
	if not lager or not isinstance(lager, str):
		frappe.throw(_("Bitte ein Lager wählen."), frappe.MandatoryError)
	row = frappe.db.get_value("Warehouse", lager, ["company", "is_group", "disabled"], as_dict=True)
	if not row:
		frappe.throw(_("Lager {0} nicht gefunden.").format(lager), frappe.DoesNotExistError)
	if cint(row.is_group) or cint(row.disabled) or row.company != firma:
		frappe.throw(
			_("Lager {0} ist kein buchbares Lager der Firma {1}.").format(lager, firma),
			MaterialwirtschaftFehler,
		)
	return lager


def hauptlager(firma: str) -> str | None:
	return materialbedarf.lager(firma, planung_einstellungen())[0]


def _ist_fahrzeuglager(lager: str) -> bool:
	if frappe.db.get_value("Warehouse", lager, "warehouse_name") == material.FAHRZEUG_LAGER:
		return True
	return bool(frappe.db.exists("Employee", {material.EMPLOYEE_FELD: lager, "status": "Active"}))


def _lager_eintrag(name: str, quelle: str, wer: str | None = None) -> dict | None:
	info = material.lager(name)
	if not info:
		return None
	beschriftung = info["warehouse_name"]
	if wer:
		beschriftung = _("{0} (Fahrzeug {1})").format(info["warehouse_name"], wer)
	return {"warehouse": info["warehouse"], "label": beschriftung, "quelle": quelle}


def zugewiesene_mitarbeiter(projekt: str | None) -> list[dict]:
	"""Mitarbeiter mit Zuweisung (ToDo, nicht storniert) an Arbeitspaketen des Projekts."""
	if not projekt:
		return []
	tasks = frappe.get_all("Task", filters={"project": projekt, "status": ["!=", "Cancelled"]}, pluck="name")
	if not tasks:
		return []
	users = frappe.get_all(
		"ToDo",
		filters={"reference_type": "Task", "reference_name": ["in", tasks], "status": ["!=", "Cancelled"]},
		pluck="allocated_to",
		distinct=True,
	)
	users = [u for u in users if u]
	if not users:
		return []
	return frappe.get_all(
		"Employee",
		filters={"user_id": ["in", users], "status": "Active"},
		fields=["name", "employee_name", "user_id", material.EMPLOYEE_FELD],
		order_by="employee_name asc",
	)


def lager_optionen(firma: str, projekt: str | None = None) -> tuple[list[dict], str | None]:
	"""Lager zur Auswahl: Hauptlager, Fahrzeuglager der zugewiesenen Monteure, übrige Fahrzeuglager."""
	optionen: list[dict] = []
	gesehen: set[str] = set()

	def add(eintrag):
		if eintrag and eintrag["warehouse"] not in gesehen:
			gesehen.add(eintrag["warehouse"])
			optionen.append(eintrag)

	haupt = hauptlager(firma)
	if haupt:
		add(_lager_eintrag(haupt, "hauptlager"))
	for e in zugewiesene_mitarbeiter(projekt):
		fl = material.fahrzeuglager(e.name)
		if fl and fl["company"] == firma:
			add(_lager_eintrag(fl["warehouse"], "zugewiesen", e.employee_name))
	for fl in material.fahrzeuglager_liste(firma):
		add(_lager_eintrag(fl["warehouse"], "fahrzeug"))
	return optionen, haupt


def einkaufspreis(item_code: str, firma: str | None = None) -> float:
	"""Preis für die Bestellzeile: Einkaufspreisliste (Buying Settings, sonst „Standard-Einkauf“),
	letzter Einkaufspreis, Bewertungspreis - sonst 0 (Hinweis im Ergebnis)."""
	liste = frappe.db.get_single_value("Buying Settings", "buying_price_list") or "Standard-Einkauf"
	preis = frappe.db.get_value(
		"Item Price",
		{"item_code": item_code, "price_list": liste, "selling": 0},
		"price_list_rate",
		order_by="valid_from desc",
	)
	if flt(preis):
		return flt(preis)
	werte = (
		frappe.db.get_value("Item", item_code, ["last_purchase_rate", "valuation_rate"], as_dict=True) or {}
	)
	return flt(werte.get("last_purchase_rate")) or flt(werte.get("valuation_rate"))


def standardlieferant(item_code: str, firma: str) -> str | None:
	from erpnext.stock.doctype.material_request.material_request import get_default_supplier_for_item

	try:
		return get_default_supplier_for_item(item_code, firma)
	except Exception:
		return None


def _offene_menge(z, entwurf: float = 0.0) -> float:
	"""Noch nicht bestellte bzw. umgelagerte Menge einer Materialanforderungszeile (Lagereinheit); ``entwurf``:
	Menge in noch nicht freigegebenen Bestellungen (ERPNext zählt ``ordered_qty`` erst mit der Freigabe)."""
	return max(flt(z.stock_qty or z.qty) - max(flt(z.ordered_qty), flt(z.received_qty)) - flt(entwurf), 0.0)


def _entwuerfe(material_request: str) -> tuple[dict[str, float], list[str]]:
	"""Mengen (Lagereinheit) je Zeile der Materialanforderung in Bestellungen im Entwurf und deren Namen -
	sonst ließe sich dieselbe Zeile bis zur Freigabe mehrfach bestellen."""
	zeilen = frappe.db.sql(
		"""select poi.material_request_item, sum(poi.stock_qty), group_concat(distinct po.name)
		from `tabPurchase Order Item` poi join `tabPurchase Order` po on po.name = poi.parent
		where po.docstatus = 0 and poi.material_request = %s
		group by poi.material_request_item""",
		material_request,
	)
	mengen = {z[0]: flt(z[1]) for z in zeilen}
	namen = sorted({n for z in zeilen for n in (z[2] or "").split(",") if n})
	return mengen, namen


def _projekt_der_anforderung(mr) -> str | None:
	projekte = [z.project for z in mr.items if z.get("project")]
	if projekte:
		return projekte[0]
	if mr.get("cv_auftrag"):
		return frappe.db.get_value(
			"Project", {"sales_order": mr.cv_auftrag, "status": ["!=", "Cancelled"]}, "name"
		)
	return None


# --- Bestellung -------------------------------------------------------------------------------------


@frappe.whitelist()
def bestellung_vorbereiten(material_request: str) -> dict:
	"""Dialog „Material bestellen“: offene Positionen mit Standardlieferant und Preis, Lieferanten,
	Lager zur Auswahl (Hauptlager, Fahrzeuge der zugewiesenen Monteure)."""
	pruefe_rolle()
	frappe.has_permission("Material Request", "read", material_request, throw=True)
	mr = frappe.get_doc("Material Request", material_request)
	if mr.docstatus == 2 or mr.status == "Stopped":
		frappe.throw(
			_("Materialanforderung {0} ist storniert oder gestoppt.").format(mr.name),
			MaterialwirtschaftFehler,
		)
	if mr.material_request_type != TYP_EINKAUF:
		frappe.throw(
			_("Materialanforderung {0} ist eine Umlagerung - bitte „Fahrzeug beladen“ verwenden.").format(
				mr.name
			),
			MaterialwirtschaftFehler,
		)
	projekt = _projekt_der_anforderung(mr)
	entwurf, entwuerfe = _entwuerfe(mr.name)
	positionen = []
	for z in mr.items:
		offen = _offene_menge(z, entwurf.get(z.name, 0.0))
		if offen <= 0:
			continue
		positionen.append(
			{
				"material_request_item": z.name,
				"item_code": z.item_code,
				"item_name": z.item_name,
				"uom": z.stock_uom or z.uom,
				"menge": offen,
				"lieferant": standardlieferant(z.item_code, mr.company),
				"preis": einkaufspreis(z.item_code, mr.company),
				"projekt": z.get("project") or projekt,
			}
		)
	lieferanten = (
		frappe.get_all(
			"Supplier",
			filters={"disabled": 0},
			fields=["name", "supplier_name"],
			order_by="supplier_name asc",
			limit=MAX_ZEILEN,
		)
		if _darf("Supplier", "read")
		else []
	)
	lager, haupt = lager_optionen(mr.company, projekt)
	return {
		"material_request": mr.name,
		"docstatus": mr.docstatus,
		"status": mr.status,
		"firma": mr.company,
		"projekt": projekt,
		"positionen": positionen,
		"entwuerfe": entwuerfe,
		"lieferanten": lieferanten,
		"lager": lager,
		"standard_lager": mr.set_warehouse if mr.set_warehouse in {o["warehouse"] for o in lager} else haupt,
		"darf_bestellen": _darf("Purchase Order", "create"),
	}


@frappe.whitelist()
def anforderung_stand(material_request: str) -> dict:
	"""Formular Materialanforderung: offene Positionen (ohne Bestellentwürfe), Bestellentwürfe, Freigegebenes."""
	pruefe_rolle()
	frappe.has_permission("Material Request", "read", material_request, throw=True)
	mr = frappe.get_doc("Material Request", material_request)
	entwurf, entwuerfe = _entwuerfe(mr.name)
	offen = sum(1 for z in mr.items if _offene_menge(z, entwurf.get(z.name, 0.0)) > 1e-9)
	return {
		"material_request": mr.name,
		"typ": mr.material_request_type,
		"docstatus": mr.docstatus,
		"status": mr.status,
		"offen": offen,
		"entwuerfe": entwuerfe,
		"darf_bestellen": _darf("Purchase Order", "create"),
	}


@frappe.whitelist(methods=["POST"])
def bestellen(material_request: str, zeilen, lager: str | None = None, einreichen: int = 0) -> dict:
	"""Bestellungen (Purchase Order, Entwurf) aus einer Materialanforderung, eine je Lieferant.

	``zeilen``: ``[{"material_request_item", "lieferant", "menge"}]`` (Menge in Lagereinheit, höchstens die
	offene Menge). Ein Entwurf der Materialanforderung wird zuerst eingereicht. ``lager``: Ziel aller
	Zeilen (Hauptlager oder Fahrzeuglager). ``einreichen``: Bestellung gleich freigeben.
	"""
	from erpnext.stock.doctype.material_request.material_request import make_purchase_order

	pruefe_rolle()
	frappe.has_permission("Material Request", "read", material_request, throw=True)
	frappe.has_permission("Purchase Order", "create", throw=True)
	zeilen = _zeilen(zeilen)
	if not zeilen:
		frappe.throw(_("Bitte mindestens eine Position bestellen."), frappe.MandatoryError)
	mr = frappe.get_doc("Material Request", material_request)
	if mr.material_request_type != TYP_EINKAUF:
		frappe.throw(
			_("Materialanforderung {0} ist keine Bestellanforderung.").format(mr.name),
			MaterialwirtschaftFehler,
		)
	if mr.docstatus == 2 or mr.status == "Stopped":
		frappe.throw(
			_("Materialanforderung {0} ist storniert oder gestoppt.").format(mr.name),
			MaterialwirtschaftFehler,
		)
	hinweise: list[str] = []
	ziel = lager_pruefen(lager or mr.set_warehouse or hauptlager(mr.company), mr.company)
	projekt = _projekt_der_anforderung(mr)
	offen = {z.name: z for z in mr.items}
	entwurf, _entwurf_namen = _entwuerfe(mr.name)

	# Zeilen prüfen, bevor der Entwurf eingereicht wird (ein Fehler lässt den Entwurf unverändert)
	je_lieferant: dict[str, dict[str, float]] = {}
	for z in zeilen:
		name, lieferant, menge = z.get("material_request_item"), z.get("lieferant"), flt(z.get("menge"))
		pos = offen.get(name)
		if not pos:
			frappe.throw(
				_("Position {0} gehört nicht zu {1}.").format(name, mr.name), MaterialwirtschaftFehler
			)
		if not lieferant or not frappe.db.exists("Supplier", lieferant):
			frappe.throw(
				_("Bitte für {0} einen Lieferanten wählen.").format(pos.item_code), frappe.MandatoryError
			)
		if cint(frappe.db.get_value("Supplier", lieferant, "disabled")):
			frappe.throw(_("Lieferant {0} ist deaktiviert.").format(lieferant), MaterialwirtschaftFehler)
		verfuegbar = _offene_menge(pos, entwurf.get(name, 0.0))
		if menge <= 0 or menge > verfuegbar + 1e-9:
			frappe.throw(
				_("Menge für {0} muss größer 0 und höchstens {1} sein (offen ohne Bestellentwürfe).").format(
					pos.item_code, verfuegbar
				),
				MaterialwirtschaftFehler,
			)
		if any(name in mengen for mengen in je_lieferant.values()):
			frappe.throw(_("Position {0} doppelt angegeben.").format(pos.item_code), MaterialwirtschaftFehler)
		# ERPNext erwartet die Menge in der Einheit der Zeile (5.1 legt Zeilen in Lagereinheit an)
		je_lieferant.setdefault(lieferant, {})[name] = flt(menge / (flt(pos.conversion_factor) or 1), 6)

	if mr.docstatus == 0:
		frappe.has_permission("Material Request", "submit", mr, throw=True)
		mr.submit()
		hinweise.append(_("Materialanforderung {0} eingereicht.").format(mr.name))
		mr = frappe.get_doc("Material Request", material_request)

	bestellungen = []
	ohne_preis: list[str] = []
	heute = getdate(nowdate())
	for lieferant, mengen in je_lieferant.items():
		po = make_purchase_order(
			mr.name,
			args={"supplier": lieferant, "filtered_children": list(mengen), "requested_qty": mengen},
		)
		po.set_warehouse = ziel
		if projekt and po.meta.has_field("project"):
			po.project = projekt
		for item in po.items:
			item.warehouse = ziel
			if projekt and not item.get("project"):
				item.project = projekt
			if not item.schedule_date or getdate(item.schedule_date) < heute:
				item.schedule_date = heute
			if not flt(item.rate):
				item.rate = einkaufspreis(item.item_code, mr.company)
				if not flt(item.rate):
					ohne_preis.append(item.item_code)
		po.insert()
		if cint(einreichen):
			frappe.has_permission("Purchase Order", "submit", po, throw=True)
			po.submit()
		bestellungen.append(
			{
				"name": po.name,
				"lieferant": lieferant,
				"lieferant_name": po.supplier_name or lieferant,
				"positionen": len(po.items),
				"netto": flt(po.net_total),
				"lager": ziel,
				"docstatus": po.docstatus,
			}
		)
	if ohne_preis:
		hinweise.append(
			_("Ohne Preis (bitte in der Bestellung eintragen): {0}.").format(
				", ".join(sorted(set(ohne_preis)))
			)
		)
	return {
		"material_request": mr.name,
		"projekt": projekt,
		"lager": ziel,
		"bestellungen": bestellungen,
		"hinweise": hinweise,
	}


# --- Lieferung --------------------------------------------------------------------------------------


@frappe.whitelist()
def lieferung_vorbereiten(purchase_order: str) -> dict:
	"""Dialog „Lieferung buchen“: Zeilen der Bestellung mit bestellter, gelieferter und offener Menge."""
	pruefe_rolle()
	frappe.has_permission("Purchase Order", "read", purchase_order, throw=True)
	po = frappe.get_doc("Purchase Order", purchase_order)
	if po.docstatus != 1:
		frappe.throw(_("Bestellung {0} ist nicht freigegeben.").format(po.name), MaterialwirtschaftFehler)
	if po.status in ("Closed", "Cancelled"):
		frappe.throw(_("Bestellung {0} ist geschlossen.").format(po.name), MaterialwirtschaftFehler)
	positionen = [
		{
			"purchase_order_item": z.name,
			"item_code": z.item_code,
			"item_name": z.item_name,
			"uom": z.uom,
			"bestellt": flt(z.qty),
			"geliefert": flt(z.received_qty),
			"offen": max(flt(z.qty) - flt(z.received_qty), 0.0),
			"lager": z.warehouse,
			"eans": material._eans([z.item_code]).get(z.item_code, []),
		}
		for z in po.items
		if flt(z.qty) - flt(z.received_qty) > 1e-9
	]
	lager, haupt = lager_optionen(po.company, po.get("project"))
	return {
		"purchase_order": po.name,
		"lieferant": po.supplier,
		"lieferant_name": po.supplier_name,
		"projekt": po.get("project"),
		"positionen": positionen,
		"lager": lager,
		"standard_lager": po.set_warehouse or (positionen[0]["lager"] if positionen else haupt),
		"darf_buchen": _darf("Purchase Receipt", "create"),
	}


@frappe.whitelist(methods=["POST"])
def lieferung_buchen(
	purchase_order: str,
	zeilen=None,
	lager: str | None = None,
	einreichen: int = 1,
	lieferschein: str | None = None,
) -> dict:
	"""Lieferung (Purchase Receipt) zu einer freigegebenen Bestellung buchen.

	``zeilen``: ``[{"purchase_order_item", "menge"}]`` - ohne Angabe alle offenen Mengen; Teillieferungen
	erlaubt. ``lager``: Ziel aller Zeilen (sonst das Lager der Bestellung). ``einreichen`` (Standard):
	sofort buchen, der Bestand steigt. ``lieferschein``: Nummer des Lieferscheins des Lieferanten.
	"""
	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

	pruefe_rolle()
	frappe.has_permission("Purchase Order", "read", purchase_order, throw=True)
	frappe.has_permission("Purchase Receipt", "create", throw=True)
	po = frappe.get_doc("Purchase Order", purchase_order)
	if po.docstatus != 1 or po.status in ("Closed", "Cancelled"):
		frappe.throw(
			_("Bestellung {0} ist nicht freigegeben oder schon geschlossen.").format(po.name),
			MaterialwirtschaftFehler,
		)
	offen = {z.name: max(flt(z.qty) - flt(z.received_qty), 0.0) for z in po.items}
	mengen: dict[str, float] = {}
	if zeilen is not None:
		for z in _zeilen(zeilen):
			name, menge = z.get("purchase_order_item"), flt(z.get("menge"))
			if name not in offen:
				frappe.throw(
					_("Position {0} gehört nicht zu {1}.").format(name, po.name), MaterialwirtschaftFehler
				)
			if menge <= 0:
				continue
			if menge > offen[name] + 1e-9:
				frappe.throw(
					_("Gelieferte Menge {0} ist größer als die offene Menge {1}.").format(menge, offen[name]),
					MaterialwirtschaftFehler,
				)
			mengen[name] = menge
	else:
		mengen = {n: m for n, m in offen.items() if m > 0}
	if not mengen:
		frappe.throw(_("Keine Position mit gelieferter Menge."), frappe.MandatoryError)
	ziel = lager_pruefen(lager, po.company) if lager else None

	pr = make_purchase_receipt(po.name, args={"filtered_children": list(mengen)})
	if ziel:
		pr.set_warehouse = ziel
	if lieferschein and pr.meta.has_field("supplier_delivery_note"):
		pr.supplier_delivery_note = str(lieferschein)[:140]
	if po.get("project") and pr.meta.has_field("project"):
		pr.project = po.project
	for item in pr.items:
		menge = mengen.get(item.purchase_order_item)
		item.qty = menge
		item.received_qty = menge
		item.stock_qty = flt(menge) * (flt(item.conversion_factor) or 1)
		if ziel:
			item.warehouse = ziel
		if po.get("project") and not item.get("project"):
			item.project = po.project
	pr.insert()
	if cint(einreichen):
		frappe.has_permission("Purchase Receipt", "submit", pr, throw=True)
		pr.submit()
	po.reload()
	rest = {z.name: max(flt(z.qty) - flt(z.received_qty), 0.0) for z in po.items}
	return {
		"purchase_order": po.name,
		"lieferung": pr.name,
		"docstatus": pr.docstatus,
		"status": po.status,
		"positionen": [
			{
				"item_code": z.item_code,
				"menge": flt(z.qty),
				"uom": z.uom,
				"lager": z.warehouse,
				"bestand_nachher": flt(
					frappe.db.get_value(
						"Bin", {"item_code": z.item_code, "warehouse": z.warehouse}, "actual_qty"
					)
				),
			}
			for z in pr.items
		],
		"offen": sum(1 for m in rest.values() if m > 1e-9),
		"vollstaendig": all(m <= 1e-9 for m in rest.values()),
	}


@frappe.whitelist()
def ean_suchen(ean: str) -> dict | None:
	"""Artikel zu einem gescannten Barcode (Scanfeld im Lieferdialog)."""
	pruefe_rolle()
	code = material.artikel_zu_ean(ean)
	if not code:
		return None
	werte = frappe.db.get_value("Item", code, ["item_name", "stock_uom"], as_dict=True)
	return {"item_code": code, "item_name": werte.item_name, "uom": werte.stock_uom}


# --- Fahrzeug beladen -------------------------------------------------------------------------------


def _mitarbeiter(employee: str | None) -> dict:
	if employee:
		werte = material.mitarbeiter(employee)
		if not werte or werte["status"] != "Active":
			frappe.throw(
				_("Mitarbeiter {0} nicht gefunden oder nicht aktiv.").format(employee),
				frappe.DoesNotExistError,
			)
		return werte
	eigener = material.employee_fuer_user(frappe.session.user)
	if not eigener:
		frappe.throw(
			_("Bitte einen Mitarbeiter wählen (Ihrem Benutzer ist kein Mitarbeiter zugeordnet)."),
			frappe.MandatoryError,
		)
	return eigener


def _baustellen(user: str, von, bis) -> list[str]:
	"""Projekte der Arbeitspakete, die dem Benutzer zugewiesen sind und in den Zeitraum fallen."""
	tasks = frappe.get_all(
		"ToDo",
		filters={"reference_type": "Task", "allocated_to": user, "status": ["!=", "Cancelled"]},
		pluck="reference_name",
		distinct=True,
	)
	if not tasks:
		return []
	zeilen = frappe.get_all(
		"Task",
		filters={
			"name": ["in", tasks],
			"status": ["not in", ("Completed", "Cancelled", "Template")],
			"project": ["is", "set"],
		},
		fields=["project", "exp_start_date", "exp_end_date"],
	)
	projekte = []
	for t in zeilen:
		start = getdate(t.exp_start_date) if t.exp_start_date else None
		ende = getdate(t.exp_end_date) if t.exp_end_date else start
		if start and start > getdate(bis):
			continue
		if ende and ende < getdate(von):
			continue
		if t.project not in projekte:
			projekte.append(t.project)
	return projekte


def _bedarf_der_projekte(projekte: list[str]) -> dict[str, dict]:
	"""Angefordertes Material je Artikel (Materialanforderungen, nicht storniert/gestoppt) abzüglich der
	schon entnommenen Mengen (Materialentnahmen „Material Issue“) der Projekte."""
	if not projekte:
		return {}
	bedarf: dict[str, dict] = {}
	for z in frappe.db.sql(
		"""select mri.item_code, mri.project, sum(mri.stock_qty) as menge
		from `tabMaterial Request Item` mri join `tabMaterial Request` mr on mr.name = mri.parent
		where mr.docstatus < 2 and mr.status not in ('Stopped', 'Cancelled') and mri.project in %(p)s
		group by mri.item_code, mri.project""",
		{"p": tuple(projekte)},
		as_dict=True,
	):
		e = bedarf.setdefault(z.item_code, {"menge": 0.0, "projekte": []})
		e["menge"] += flt(z.menge)
		if z.project not in e["projekte"]:
			e["projekte"].append(z.project)
	for z in frappe.db.sql(
		"""select sed.item_code, sum(sed.qty) as menge
		from `tabStock Entry Detail` sed join `tabStock Entry` se on se.name = sed.parent
		where se.docstatus = 1 and se.purpose = 'Material Issue'
			and (se.project in %(p)s or sed.project in %(p)s)
		group by sed.item_code""",
		{"p": tuple(projekte)},
		as_dict=True,
	):
		if z.item_code in bedarf:
			bedarf[z.item_code]["menge"] = max(bedarf[z.item_code]["menge"] - flt(z.menge), 0.0)
	return bedarf


def _bestaende(lager: str) -> dict[str, float]:
	return {
		b.item_code: flt(b.actual_qty)
		for b in frappe.get_all("Bin", filters={"warehouse": lager}, fields=["item_code", "actual_qty"])
	}


def mindestbestaende(lager: str) -> dict[str, dict]:
	"""Mindestbestand je Artikel für ein Lager (``Item Reorder``)."""
	return {
		z.parent: {
			"mindestbestand": flt(z.warehouse_reorder_level),
			"nachbestellmenge": flt(z.warehouse_reorder_qty),
			"art": z.material_request_type,
		}
		for z in frappe.get_all(
			"Item Reorder",
			filters={"parenttype": "Item", "warehouse": lager},
			fields=["parent", "warehouse_reorder_level", "warehouse_reorder_qty", "material_request_type"],
		)
	}


def _artikel_infos(codes: list[str]) -> dict[str, frappe._dict]:
	if not codes:
		return {}
	return {
		i.name: i
		for i in frappe.get_all(
			"Item",
			filters={"name": ["in", codes]},
			fields=["name", "item_name", "stock_uom", "disabled", "is_stock_item"],
		)
	}


@frappe.whitelist()
def fahrzeug_vorschlag(employee: str | None = None, von=None, bis=None) -> dict:
	"""Bericht/Dialog „Fahrzeug beladen“: je Artikel Bedarf der zugewiesenen Baustellen im Zeitraum
	(Standard: heute bis in sieben Tagen, ``VORSCHAU_TAGE``), Bestand auf dem Fahrzeug und im Hauptlager,
	Mindestbestand des Fahrzeugs und Vorschlag = fehlende Menge, begrenzt auf den Bestand im Hauptlager.
	Dazu alles, was sonst auf dem Fahrzeug liegt."""
	pruefe_rolle()
	m = _mitarbeiter(employee)
	fahrzeug = material.fahrzeuglager(m["name"])
	if not fahrzeug:
		frappe.throw(
			_("Kein Fahrzeuglager für {0} - am Mitarbeiter eintragen.").format(m["employee_name"]),
			MaterialwirtschaftFehler,
		)
	haupt = hauptlager(fahrzeug["company"])
	von = getdate(von or nowdate())
	bis = getdate(bis) if bis else add_days(von, VORSCHAU_TAGE)
	hinweise: list[str] = []
	if not haupt:
		hinweise.append(_("Kein Hauptlager eingestellt (Corevatis Planung Einstellungen) - kein Vorschlag."))
	if haupt == fahrzeug["warehouse"]:
		hinweise.append(_("Haupt- und Fahrzeuglager sind gleich - keine Umlagerung möglich."))
		haupt = None
	projekte = _baustellen(m["user_id"], von, bis) if m.get("user_id") else []
	bedarf = _bedarf_der_projekte(projekte)
	auf_fahrzeug = _bestaende(fahrzeug["warehouse"])
	im_haupt = _bestaende(haupt) if haupt else {}
	minimum = mindestbestaende(fahrzeug["warehouse"])
	codes = sorted(set(bedarf) | {c for c, q in auf_fahrzeug.items() if q > 0} | set(minimum))
	infos = _artikel_infos(codes)
	zeilen = []
	for code in codes:
		info = infos.get(code)
		if not info or cint(info.disabled) or not cint(info.is_stock_item):
			continue
		b = bedarf.get(code, {"menge": 0.0, "projekte": []})
		fz = flt(auf_fahrzeug.get(code))
		hl = flt(im_haupt.get(code))
		mind = flt(minimum.get(code, {}).get("mindestbestand"))
		fehlt = max(flt(b["menge"]) - fz, mind - fz, 0.0)
		vorschlag = min(fehlt, hl) if haupt else 0.0
		if fehlt > 0 and vorschlag < fehlt and haupt:
			hinweise.append(
				_("{0}: {1} fehlen, im Hauptlager nur {2}.").format(
					info.item_name or code, flt(fehlt, 2), flt(hl, 2)
				)
			)
		zeilen.append(
			{
				"item_code": code,
				"item_name": info.item_name,
				"uom": info.stock_uom,
				"bedarf": flt(b["menge"], 3),
				"projekte": b["projekte"],
				"fahrzeug": fz,
				"hauptlager": hl,
				"mindestbestand": mind,
				"fehlt": flt(fehlt, 3),
				"vorschlag": flt(vorschlag, 3),
			}
		)
	zeilen.sort(key=lambda z: (-z["vorschlag"], -z["bedarf"], z["item_code"]))
	return {
		"employee": m["name"],
		"employee_name": m["employee_name"],
		"fahrzeuglager": fahrzeug["warehouse"],
		"hauptlager": haupt,
		"von": str(von),
		"bis": str(bis),
		"projekte": projekte,
		"zeilen": zeilen,
		"hinweise": hinweise,
		"darf_beladen": _darf("Stock Entry", "create"),
	}


@frappe.whitelist(methods=["POST"])
def fahrzeug_beladen(employee: str, zeilen, einreichen: int = 1) -> dict:
	"""Umlagerung Hauptlager → Fahrzeuglager des Mitarbeiters (Stock Entry „Material Transfer“).
	``zeilen``: ``[{"item_code", "menge"}]`` in Lagereinheit; Mengen über dem Hauptlagerbestand lehnt
	ERPNext beim Buchen ab (kein negativer Bestand)."""
	pruefe_rolle()
	frappe.has_permission("Stock Entry", "create", throw=True)
	m = _mitarbeiter(employee)
	fahrzeug = material.fahrzeuglager(m["name"])
	if not fahrzeug:
		frappe.throw(_("Kein Fahrzeuglager für {0}.").format(m["employee_name"]), MaterialwirtschaftFehler)
	haupt = hauptlager(fahrzeug["company"])
	if not haupt or haupt == fahrzeug["warehouse"]:
		frappe.throw(_("Kein Hauptlager für die Umlagerung eingestellt."), MaterialwirtschaftFehler)
	positionen = []
	gesehen: set[str] = set()
	for z in _zeilen(zeilen):
		code, menge = z.get("item_code"), flt(z.get("menge"))
		if not code or menge <= 0:
			continue
		if code in gesehen:
			frappe.throw(_("Artikel {0} doppelt angegeben.").format(code), MaterialwirtschaftFehler)
		gesehen.add(code)
		info = frappe.db.get_value("Item", code, ["is_stock_item", "disabled", "stock_uom"], as_dict=True)
		if not info or not cint(info.is_stock_item) or cint(info.disabled):
			frappe.throw(_("Artikel {0} ist kein Lagerartikel.").format(code), MaterialwirtschaftFehler)
		positionen.append(
			{
				"item_code": code,
				"qty": menge,
				"uom": info.stock_uom,
				"stock_uom": info.stock_uom,
				"conversion_factor": 1,
				"s_warehouse": haupt,
				"t_warehouse": fahrzeug["warehouse"],
			}
		)
	if not positionen:
		frappe.throw(_("Keine Position mit Menge."), frappe.MandatoryError)
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": ZWECK_UMLAGERUNG,
			"purpose": ZWECK_UMLAGERUNG,
			"company": fahrzeug["company"],
			"from_warehouse": haupt,
			"to_warehouse": fahrzeug["warehouse"],
			"remarks": _("Fahrzeug beladen: {0}").format(m["employee_name"]),
			"items": positionen,
		}
	)
	if se.meta.has_field(material.MITARBEITER_FELD):
		se.set(material.MITARBEITER_FELD, m["name"])
	se.insert()
	if cint(einreichen):
		frappe.has_permission("Stock Entry", "submit", se, throw=True)
		se.submit()
	return {
		"stock_entry": se.name,
		"docstatus": se.docstatus,
		"fahrzeuglager": fahrzeug["warehouse"],
		"hauptlager": haupt,
		"positionen": [
			{
				"item_code": z.item_code,
				"menge": flt(z.qty),
				"uom": z.uom,
				"fahrzeug_nachher": flt(
					frappe.db.get_value(
						"Bin", {"item_code": z.item_code, "warehouse": z.t_warehouse}, "actual_qty"
					)
				),
			}
			for z in se.items
		],
	}


# --- Mindestbestand, Nachbestellen -------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def mindestbestand_setzen(
	item_code: str, warehouse: str, mindestbestand, nachbestellmenge=0, art: str | None = None
) -> dict:
	"""Mindestbestand und Nachbestellmenge eines Artikels je Lager (``Item Reorder``). Art „Purchase“
	(Bestellung) oder „Transfer“ (Umlagerung aus dem Hauptlager, Standard für Fahrzeuglager). Beide 0 →
	Eintrag entfernt; Nachbestellmenge 0 → Mindestbestand (ERPNext verlangt eine Menge). Nach Rollenprüfung für Büro geschrieben (Artikel ist für Büro sonst nur lesbar)."""
	pruefe_rolle()
	if not frappe.db.exists("Item", item_code):
		frappe.throw(_("Artikel {0} nicht gefunden.").format(item_code), frappe.DoesNotExistError)
	firma = frappe.db.get_value("Warehouse", warehouse, "company")
	if not firma:
		frappe.throw(_("Lager {0} nicht gefunden.").format(warehouse), frappe.DoesNotExistError)
	lager_pruefen(warehouse, firma)
	mind, menge = flt(mindestbestand), flt(nachbestellmenge)
	if mind < 0 or menge < 0:
		frappe.throw(
			_("Mindestbestand und Nachbestellmenge dürfen nicht negativ sein."), MaterialwirtschaftFehler
		)
	if art not in (None, "", ART_BESTELLUNG, ART_UMLAGERUNG):
		frappe.throw(
			_("Art muss Purchase (Bestellung) oder Transfer (Umlagerung) sein."), MaterialwirtschaftFehler
		)
	art = art or (ART_UMLAGERUNG if _ist_fahrzeuglager(warehouse) else ART_BESTELLUNG)
	if mind > 0 and menge <= 0:
		# ERPNext verlangt eine Nachbestellmenge: ohne Angabe bis zum Mindestbestand auffüllen
		menge = mind
	item = frappe.get_doc("Item", item_code)
	vorhanden = [z for z in item.get("reorder_levels") if z.warehouse == warehouse]
	if mind <= 0 and menge <= 0:
		for z in vorhanden:
			item.remove(z)
	elif vorhanden:
		z = vorhanden[0]
		z.warehouse_group = z.warehouse_group or warehouse
		z.warehouse_reorder_level = mind
		z.warehouse_reorder_qty = menge
		z.material_request_type = art
		for doppelt in vorhanden[1:]:
			item.remove(doppelt)
	else:
		item.append(
			"reorder_levels",
			{
				"warehouse_group": warehouse,
				"warehouse": warehouse,
				"warehouse_reorder_level": mind,
				"warehouse_reorder_qty": menge,
				"material_request_type": art,
			},
		)
	item.flags.ignore_permissions = True
	item.save()
	return {
		"item_code": item_code,
		"warehouse": warehouse,
		"mindestbestand": mind,
		"nachbestellmenge": menge,
		"art": art if mind > 0 or menge > 0 else None,
	}


def nachbestellen(filters: dict | None = None) -> dict:
	"""Zeilen des Berichts „Nachbestellen“: alle Mindestbestände (``Item Reorder``) mit Bestand, verfügbarer
	Menge (ERPNext ``projected_qty``: Bestand plus bestellt minus angefordert), Nachbestellmenge wie der
	ERPNext-Job (Nachbestellmenge, aufgefüllt bis zum Mindestbestand), Art und Lieferant."""
	pruefe_rolle()
	f = frappe._dict(filters or {})
	bedingungen: dict = {"parenttype": "Item"}
	if f.get("lager"):
		bedingungen["warehouse"] = f.lager
	eintraege = frappe.get_all(
		"Item Reorder",
		filters=bedingungen,
		fields=[
			"parent",
			"warehouse",
			"warehouse_reorder_level",
			"warehouse_reorder_qty",
			"material_request_type",
		],
		order_by="warehouse asc, parent asc",
		limit=2000,
	)
	infos = _artikel_infos(sorted({e.parent for e in eintraege}))
	lager_firma = {}
	zeilen = []
	hinweise: list[str] = []
	for e in eintraege:
		info = infos.get(e.parent)
		if not info or cint(info.disabled) or not cint(info.is_stock_item):
			continue
		if e.warehouse not in lager_firma:
			lager_firma[e.warehouse] = frappe.db.get_value(
				"Warehouse", e.warehouse, ["company", "disabled"], as_dict=True
			)
		w = lager_firma[e.warehouse]
		if not w or cint(w.disabled) or (f.get("firma") and w.company != f.firma):
			continue
		b = (
			frappe.db.get_value(
				"Bin",
				{"item_code": e.parent, "warehouse": e.warehouse},
				["actual_qty", "projected_qty"],
				as_dict=True,
			)
			or {}
		)
		bestand, verfuegbar = flt(b.get("actual_qty")), flt(b.get("projected_qty"))
		mind, menge = flt(e.warehouse_reorder_level), flt(e.warehouse_reorder_qty)
		unter = verfuegbar <= mind and (mind or menge)
		nach = 0.0
		if unter:
			nach = menge
			if verfuegbar + nach <= mind:
				nach += mind - verfuegbar
		art = (
			e.material_request_type
			if e.material_request_type in (ART_BESTELLUNG, ART_UMLAGERUNG)
			else ART_BESTELLUNG
		)
		if cint(f.get("nur_unter", 1)) and not unter:
			continue
		zeilen.append(
			{
				"item_code": e.parent,
				"item_name": info.item_name,
				"uom": info.stock_uom,
				"lager": e.warehouse,
				"firma": w.company,
				"bestand": bestand,
				"verfuegbar": verfuegbar,
				"mindestbestand": mind,
				"nachbestellmenge": flt(nach, 3),
				"art": art,
				"art_text": _("Umlagerung") if art == ART_UMLAGERUNG else _("Bestellung"),
				"lieferant": standardlieferant(e.parent, w.company) if art == ART_BESTELLUNG else None,
				"unter": bool(unter),
			}
		)
	if cint(frappe.db.get_single_value("Stock Settings", "auto_indent")):
		hinweise.append(
			_(
				"Die ERPNext-Nachbestellautomatik ist eingeschaltet (Lagereinstellungen) - sie legt zusätzlich eigene Materialanforderungen an."
			)
		)
	return {"zeilen": zeilen, "hinweise": hinweise}


@frappe.whitelist(methods=["POST"])
def nachbestellung_anlegen(zeilen) -> dict:
	"""Materialanforderungen (Entwurf) aus Zeilen des Berichts „Nachbestellen“: je Lager und Art ein Beleg
	(Bestellung ins Lager bzw. Umlagerung aus dem Hauptlager). ``zeilen``: ``[{"item_code", "lager", "menge", "art"}]``."""
	pruefe_rolle()
	frappe.has_permission("Material Request", "create", throw=True)
	gruppen: dict[tuple[str, str], list[dict]] = {}
	for z in _zeilen(zeilen):
		code, lager, menge = z.get("item_code"), z.get("lager"), flt(z.get("menge"))
		art = z.get("art") or ART_BESTELLUNG
		if not code or not lager or menge <= 0:
			continue
		if art not in (ART_BESTELLUNG, ART_UMLAGERUNG):
			frappe.throw(_("Art {0} unbekannt.").format(art), MaterialwirtschaftFehler)
		info = frappe.db.get_value(
			"Item", code, ["item_name", "description", "stock_uom", "is_stock_item", "disabled"], as_dict=True
		)
		if not info or not cint(info.is_stock_item) or cint(info.disabled):
			frappe.throw(_("Artikel {0} ist kein Lagerartikel.").format(code), MaterialwirtschaftFehler)
		gruppen.setdefault((art, lager), []).append({"item_code": code, "menge": menge, "info": info})
	if not gruppen:
		frappe.throw(_("Keine Position mit Menge."), frappe.MandatoryError)
	heute = nowdate()
	ergebnis = []
	for (art, lager), positionen in gruppen.items():
		firma = frappe.db.get_value("Warehouse", lager, "company")
		if not firma:
			frappe.throw(_("Lager {0} nicht gefunden.").format(lager), frappe.DoesNotExistError)
		lager_pruefen(lager, firma)
		quelle = None
		if art == ART_UMLAGERUNG:
			quelle = hauptlager(firma)
			if not quelle or quelle == lager:
				frappe.throw(
					_("Für die Umlagerung nach {0} fehlt ein Hauptlager.").format(lager),
					MaterialwirtschaftFehler,
				)
		mr = frappe.get_doc(
			{
				"doctype": "Material Request",
				"material_request_type": TYP_UMLAGERUNG if art == ART_UMLAGERUNG else TYP_EINKAUF,
				"company": firma,
				"transaction_date": heute,
				"schedule_date": heute,
				"set_warehouse": lager,
				"set_from_warehouse": quelle,
				"items": [
					{
						"item_code": p["item_code"],
						"item_name": p["info"].item_name,
						"description": p["info"].description or p["info"].item_name,
						"qty": p["menge"],
						"uom": p["info"].stock_uom,
						"stock_uom": p["info"].stock_uom,
						"conversion_factor": 1,
						"schedule_date": heute,
						"warehouse": lager,
						"from_warehouse": quelle,
					}
					for p in positionen
				],
			}
		)
		mr.insert()
		ergebnis.append(
			{
				"name": mr.name,
				"typ": mr.material_request_type,
				"lager": lager,
				"quelle": quelle,
				"positionen": len(mr.items),
			}
		)
	return {"material_requests": ergebnis}


# --- Prozessleiste ----------------------------------------------------------------------------------


def bedarf_zum_projekt(projekt: str) -> list[dict]:
	"""Materialanforderungen des Projekts (über Zeilenprojekt oder Auftrag), nicht storniert."""
	namen = set(
		frappe.get_all(
			"Material Request Item",
			filters={"project": projekt, "docstatus": ["<", 2]},
			pluck="parent",
			distinct=True,
		)
	)
	auftrag = frappe.db.get_value("Project", projekt, "sales_order")
	if auftrag and frappe.db.has_column("Material Request", "cv_auftrag"):
		namen |= set(
			frappe.get_all(
				"Material Request", filters={"cv_auftrag": auftrag, "docstatus": ["<", 2]}, pluck="name"
			)
		)
	if not namen:
		return []
	liste = []
	for mr in frappe.get_all(
		"Material Request",
		filters={
			"name": ["in", sorted(namen)],
			"docstatus": ["<", 2],
			"status": ["not in", ("Stopped", "Cancelled")],
		},
		fields=[
			"name",
			"material_request_type",
			"status",
			"docstatus",
			"set_warehouse",
			"per_ordered",
			"per_received",
		],
		order_by="creation asc",
	):
		zeilen = frappe.get_all(
			"Material Request Item",
			filters={"parent": mr.name},
			fields=["name", "stock_qty", "qty", "ordered_qty", "received_qty"],
		)
		entwurf, entwuerfe = _entwuerfe(mr.name)
		offen = sum(1 for z in zeilen if _offene_menge(z, entwurf.get(z.name, 0.0)) > 1e-9)
		liste.append({**mr, "positionen": len(zeilen), "offen": offen, "entwuerfe": entwuerfe})
	return liste


def stand(projekt: str) -> dict | None:
	"""Pille „Material: 2 offen, 1 bestellt, 3 geliefert“ für die Prozessleiste; ``None`` ohne Rolle,
	ohne Leserecht oder ohne Materialanforderung zum Projekt."""
	if not darf_planen() or not _darf("Project", "read", projekt):
		return None
	anforderungen = bedarf_zum_projekt(projekt)
	if not anforderungen:
		return None
	offen = sum(a["offen"] for a in anforderungen)
	entwurf = frappe.db.sql(
		"""select count(*) from `tabPurchase Order Item` poi join `tabPurchase Order` po on po.name = poi.parent
		where po.docstatus = 0 and (poi.project = %(p)s or po.project = %(p)s)""",
		{"p": projekt},
	)[0][0]
	bestellt = frappe.db.sql(
		"""select count(*) from `tabPurchase Order Item` poi join `tabPurchase Order` po on po.name = poi.parent
		where po.docstatus = 1 and po.status not in %(zu)s and (poi.project = %(p)s or po.project = %(p)s)
			and poi.received_qty < poi.qty""",
		{"p": projekt, "zu": PO_ZU},
	)[0][0]
	geliefert = frappe.db.sql(
		"""select count(*) from `tabPurchase Receipt Item` pri join `tabPurchase Receipt` pr on pr.name = pri.parent
		where pr.docstatus = 1 and pr.status != 'Cancelled' and (pri.project = %(p)s or pr.project = %(p)s)""",
		{"p": projekt},
	)[0][0]
	teile = [_("{0} offen").format(offen)]
	if cint(entwurf):
		teile.append(_("{0} im Bestellentwurf").format(cint(entwurf)))
	teile += [_("{0} bestellt").format(cint(bestellt)), _("{0} geliefert").format(cint(geliefert))]
	if offen or cint(entwurf):
		ampel = "gelb"
	elif cint(bestellt):
		ampel = "grau"
	else:
		ampel = "grün"
	bestellbar = next(
		(a for a in anforderungen if a["material_request_type"] == TYP_EINKAUF and a["offen"]), None
	)
	return {
		"projekt": projekt,
		"offen": offen,
		"entwurf": cint(entwurf),
		"bestellt": cint(bestellt),
		"geliefert": cint(geliefert),
		"ampel": ampel,
		"text": _("Material: {0}").format(", ".join(teile)),
		"material_request": (bestellbar or anforderungen[-1])["name"],
		"bestellbar": bestellbar["name"] if bestellbar and _darf("Purchase Order", "create") else None,
	}


@frappe.whitelist()
def projekt_material(project: str) -> dict:
	"""Projektformular: Stand und Materialanforderungen (Knopf „Material bestellen“)."""
	pruefe_rolle()
	frappe.has_permission("Project", "read", project, throw=True)
	return {"stand": stand(project), "anforderungen": bedarf_zum_projekt(project)}
