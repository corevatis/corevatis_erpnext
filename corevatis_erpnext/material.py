"""Materialentnahme aus dem Fahrzeuglager (Aufgabe 3.11): ERPNext-Lager für die Mobile-API.

Die Kern-App ``corevatis`` (Lese-Endpunkte ``material_lager``/``material_bestand``, Sync-Entität
``material_entnahme``) prüft Rechte, Flag und Payload und ruft diese Funktionen per
``frappe.get_attr("corevatis_erpnext.material.…")`` auf (kein Import, Lizenzgrenze). Hier steckt
nur das ERPNext-Wissen (Warehouse, Bin, Item, Item Barcode, Stock Entry):

- **Fahrzeuglager des Monteurs** (``fahrzeuglager``): Custom Field ``Employee.cv_fahrzeuglager``
  (Link Warehouse). Ist es leer, gilt das Lager ``Fahrzeug`` der Firma des Mitarbeiters
  (Site-Template 0.4: ``Fahrzeug - <Kürzel>``). Mehrere Fahrzeuge = weitere Lager anlegen und am
  Employee eintragen.
- **Bestand** (``bestand``): Lagerartikel (``is_stock_item``, nicht deaktiviert, keine Vorlage)
  mit ``Bin.actual_qty`` im Lager, Lagereinheit, EANs aus ``Item Barcode``. Ohne Suche nur
  Artikel, die im Lager einen Bestand(seintrag) haben; mit ``suche``/``ean`` alle passenden
  Lagerartikel (auch mit Bestand 0).
- **Entnahme** (``entnahme_buchen``): ein Stock Entry „Material Issue“ aus dem Lager, mit
  ``project`` am Beleg und an jeder Zeile, Task in ``cv_task``, Mitarbeiter in
  ``cv_mitarbeiter`` - **direkt eingereicht** (``submit``), damit der Bestand sofort sinkt
  (Entscheidung 3.11; die Rolle Monteur bucht im Desk weiterhin nur Entwürfe, 0.4).
  Gebucht wird mit ``ignore_permissions``: die Funktion ist nicht whitelisted, die Rechte
  (Monteur nur eigenes Fahrzeuglager, Meister/Büro frei) prüft der Kern vor dem Aufruf.
- **Idempotenz:** Der ``idempotency_key`` der Mobile-Mutation steht am Beleg
  (``Stock Entry.cv_sync_key``). Ein zweiter Aufruf mit demselben Key liefert den vorhandenen
  Beleg (``duplikat: True``) und bucht nichts - zusätzlich zum ``Sync Journal`` des Kerns.
- **Fehler** als ``MaterialFehler`` (``frappe.ValidationError``) mit maschinenlesbarem
  ``grund``: ``ean_unbekannt``, ``artikel_unbekannt``, ``kein_lagerartikel``, ``menge_ungueltig``,
  ``menge_ganzzahlig``, ``bestand_unzureichend``, ``lager_unbekannt``, ``projekt_unbekannt``,
  ``task_unbekannt``, ``task_projekt``, ``schluessel_fremd``, ``zu_viele_positionen``.
  **Negativer Bestand:** Menge > Bestand wird abgelehnt (``bestand_unzureichend``), solange
  ERPNext keinen negativen Bestand erlaubt (Stock Settings ``allow_negative_stock`` bzw.
  ``Item.allow_negative_stock``); ist er erlaubt, wird gebucht und ``hinweise`` nennt die
  Unterdeckung.
"""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, flt, now_datetime

MODULE = "Corevatis ERPNext"

FAHRZEUG_LAGER = "Fahrzeug"
EMPLOYEE_FELD = "cv_fahrzeuglager"
SYNC_KEY_FELD = "cv_sync_key"
TASK_FELD = "cv_task"
MITARBEITER_FELD = "cv_mitarbeiter"
ZWECK = "Material Issue"
MAX_POSITIONEN = 100
MAX_TREFFER = 2000


class MaterialFehler(frappe.ValidationError):
	"""Fachlicher Fehler einer Entnahme oder Abfrage; ``grund`` ist maschinenlesbar."""

	def __init__(self, grund: str, message: str, details: dict | None = None):
		super().__init__(message)
		self.grund = grund
		self.details = details or {}


# --------------------------------------------------------------------------- Einrichtung


def custom_fields() -> dict[str, list[dict]]:
	return {
		"Employee": [
			{
				"fieldname": EMPLOYEE_FELD,
				"fieldtype": "Link",
				"options": "Warehouse",
				"label": "Fahrzeuglager",
				"insert_after": "user_id",
				"module": MODULE,
				"description": "Lager der Materialentnahme in der Corevatis-App; leer = Lager "
				"„Fahrzeug“ der Firma.",
			}
		],
		"Stock Entry": [
			{
				"fieldname": TASK_FELD,
				"fieldtype": "Link",
				"options": "Task",
				"label": "Aufgabe",
				"insert_after": "project",
				"module": MODULE,
				"no_copy": 1,
			},
			{
				"fieldname": MITARBEITER_FELD,
				"fieldtype": "Link",
				"options": "Employee",
				"label": "Mitarbeiter (App)",
				"insert_after": TASK_FELD,
				"module": MODULE,
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": SYNC_KEY_FELD,
				"fieldtype": "Data",
				"label": "Mobile Sync Key",
				"insert_after": MITARBEITER_FELD,
				"read_only": 1,
				"hidden": 1,
				"no_copy": 1,
				"print_hide": 1,
				"search_index": 1,
				"module": MODULE,
				"description": "Idempotency key of the mobile booking (Corevatis app).",
			},
		],
	}


def ensure_setup() -> None:
	"""Aus ``after_install``/``after_migrate``: Custom Fields an Employee und Stock Entry."""
	create_custom_fields(custom_fields(), update=True)


# --------------------------------------------------------------------------- Mitarbeiter, Lager


def employee_fuer_user(user: str) -> dict | None:
	"""Aktiver Employee mit ``user_id`` = Benutzer (``name``, ``employee_name``, ``company``)."""
	if not user:
		return None
	rows = frappe.get_all(
		"Employee",
		filters={"user_id": user, "status": "Active"},
		fields=["name", "employee_name", "company", "user_id"],
		order_by="creation asc",
		limit=1,
	)
	return dict(rows[0]) if rows else None


def mitarbeiter(employee: str) -> dict | None:
	"""Employee mit Status (``name``, ``employee_name``, ``company``, ``user_id``, ``status``)."""
	if not employee or not isinstance(employee, str):
		return None
	row = frappe.db.get_value(
		"Employee", employee, ["name", "employee_name", "company", "user_id", "status"], as_dict=True
	)
	return dict(row) if row else None


def lager_rechte(warehouse: str) -> dict:
	"""Rechte des angemeldeten Benutzers am Lager (Security-Review 3.11).

	``lesen``: Leserecht auf das Warehouse (User Permissions, Firma greifen); ``buchen``: Recht,
	einen Stock Entry einzureichen. Die Mobile-API verlangt ``lesen`` immer und ``buchen``, wenn
	Meister/Büro aus einem anderen als dem eigenen Fahrzeuglager buchen.
	"""
	company = frappe.db.get_value("Warehouse", warehouse, "company")
	# Submit-Recht am konkreten (ungespeicherten) Beleg prüfen, damit User Permissions auf
	# Firma/Lager greifen - ohne Dokument prüft Frappe nur die Rolle (Security-Review 3.11)
	entwurf = frappe.new_doc("Stock Entry")
	entwurf.update(
		{"purpose": ZWECK, "stock_entry_type": ZWECK, "company": company, "from_warehouse": warehouse}
	)
	entwurf.append("items", {"s_warehouse": warehouse})
	return {
		"lesen": bool(frappe.has_permission("Warehouse", "read", doc=warehouse)),
		"buchen": bool(frappe.has_permission("Stock Entry", "submit", doc=entwurf)),
	}


def _lager_info(name: str, quelle: str) -> dict | None:
	row = frappe.db.get_value(
		"Warehouse", name, ["name", "warehouse_name", "company", "is_group", "disabled"], as_dict=True
	)
	if not row or cint(row.is_group) or cint(row.disabled):
		return None
	return {
		"warehouse": row.name,
		"warehouse_name": row.warehouse_name,
		"company": row.company,
		"quelle": quelle,
	}


def standard_firma() -> str | None:
	firma = frappe.defaults.get_global_default("company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	if firma and frappe.db.exists("Company", firma):
		return firma
	firmen = frappe.get_all("Company", pluck="name", order_by="creation asc", limit=1)
	return firmen[0] if firmen else None


def fahrzeug_lager_der_firma(company: str | None) -> dict | None:
	"""Lager „Fahrzeug“ der Firma (Site-Template 0.4), sonst ``None``."""
	company = company or standard_firma()
	if not company:
		return None
	name = frappe.db.get_value(
		"Warehouse",
		{"warehouse_name": FAHRZEUG_LAGER, "company": company, "is_group": 0, "disabled": 0},
		"name",
	)
	return _lager_info(name, "fallback") if name else None


def fahrzeuglager(employee: str | None) -> dict | None:
	"""Fahrzeuglager des Mitarbeiters: ``cv_fahrzeuglager`` am Employee, sonst „Fahrzeug“ der Firma.

	Ohne ``employee`` das Fahrzeuglager der Standardfirma (Meister/Büro ohne Employee).
	Antwort ``{"warehouse", "warehouse_name", "company", "quelle": "employee"|"fallback"}`` oder
	``None`` (kein passendes, aktives Lager).
	"""
	company = None
	if employee:
		row = frappe.db.get_value("Employee", employee, [EMPLOYEE_FELD, "company"], as_dict=True)
		if not row:
			return None
		company = row.company
		if row.get(EMPLOYEE_FELD):
			info = _lager_info(row.get(EMPLOYEE_FELD), "employee")
			if info:
				return info
	return fahrzeug_lager_der_firma(company)


def lager(warehouse: str) -> dict | None:
	"""Ein buchbares Lager (nicht Gruppe, nicht deaktiviert) oder ``None``."""
	if not warehouse or not isinstance(warehouse, str):
		return None
	return _lager_info(warehouse, "angegeben")


def fahrzeuglager_liste(company: str | None = None) -> list[dict]:
	"""Buchbare Lager zur Auswahl (Meister/Büro): Fahrzeuglager der Mitarbeiter und „Fahrzeug“."""
	namen = set(
		frappe.get_all(
			"Employee", filters={EMPLOYEE_FELD: ["is", "set"], "status": "Active"}, pluck=EMPLOYEE_FELD
		)
	)
	filters = {"warehouse_name": FAHRZEUG_LAGER, "is_group": 0, "disabled": 0}
	if company:
		filters["company"] = company
	namen |= set(frappe.get_all("Warehouse", filters=filters, pluck="name"))
	liste = [_lager_info(n, "auswahl") for n in sorted(namen)]
	return [info for info in liste if info and (not company or info["company"] == company)]


# --------------------------------------------------------------------------- Bestand


def _ganzzahlige_einheiten(uoms: set[str]) -> set[str]:
	if not uoms:
		return set()
	return set(
		frappe.get_all("UOM", filters={"name": ["in", sorted(uoms)], "must_be_whole_number": 1}, pluck="name")
	)


def _eans(item_codes: list[str]) -> dict[str, list[str]]:
	if not item_codes:
		return {}
	out: dict[str, list[str]] = {}
	for row in frappe.get_all(
		"Item Barcode",
		filters={"parent": ["in", item_codes], "parenttype": "Item"},
		fields=["parent", "barcode"],
		order_by="idx asc",
	):
		out.setdefault(row.parent, []).append(row.barcode)
	return out


def artikel_zu_ean(ean: str) -> str | None:
	"""Item Code zu einem Barcode (``Item Barcode.barcode``, exakt) oder ``None``."""
	if not ean or not isinstance(ean, str):
		return None
	return frappe.db.get_value("Item Barcode", {"barcode": ean.strip(), "parenttype": "Item"}, "parent")


def bestand(warehouse: str, suche: str | None = None, ean: str | None = None) -> list[dict]:
	"""Lagerartikel mit Bestand im Lager.

	Je Artikel ``item_code``, ``item_name``, ``uom`` (Lagereinheit), ``menge`` (``Bin.actual_qty``,
	0 ohne Bin), ``ganzzahlig`` (UOM „Must be whole number“), ``eans``, ``item_group``.
	Ohne ``suche``/``ean``: nur Artikel mit Bin im Lager. ``suche``: Teiltext in Code oder Name;
	``ean``: exakter Barcode. Höchstens ``MAX_TREFFER`` Artikel, sortiert nach Code.
	"""
	bins = {
		row.item_code: flt(row.actual_qty)
		for row in frappe.get_all("Bin", filters={"warehouse": warehouse}, fields=["item_code", "actual_qty"])
	}
	filters: dict = {"is_stock_item": 1, "disabled": 0, "has_variants": 0}
	or_filters = None
	if ean:
		code = artikel_zu_ean(ean)
		if not code:
			return []
		filters["name"] = code
	elif suche:
		text = f"%{suche.strip()}%"
		or_filters = {"item_code": ["like", text], "item_name": ["like", text]}
	else:
		if not bins:
			return []
		filters["name"] = ["in", sorted(bins)]
	items = frappe.get_all(
		"Item",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "item_name", "stock_uom", "item_group"],
		order_by="name asc",
		limit=MAX_TREFFER,
	)
	ganz = _ganzzahlige_einheiten({i.stock_uom for i in items if i.stock_uom})
	eans = _eans([i.name for i in items])
	return [
		{
			"item_code": i.name,
			"item_name": i.item_name,
			"uom": i.stock_uom,
			"menge": bins.get(i.name, 0.0),
			"ganzzahlig": i.stock_uom in ganz,
			"eans": eans.get(i.name, []),
			"item_group": i.item_group,
		}
		for i in items
	]


# --------------------------------------------------------------------------- Entnahme


def _negativ_erlaubt(item_code: str) -> bool:
	if cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock")):
		return True
	meta = frappe.get_meta("Item")
	return bool(
		meta.has_field("allow_negative_stock")
		and cint(frappe.db.get_value("Item", item_code, "allow_negative_stock"))
	)


def _beleg(name: str, duplikat: bool, hinweise: list[str] | None = None) -> dict:
	se = frappe.get_doc("Stock Entry", name)
	return {
		"stock_entry": se.name,
		"docstatus": se.docstatus,
		"posting_date": str(se.posting_date),
		"posting_time": str(se.posting_time),
		"warehouse": se.from_warehouse or (se.items[0].s_warehouse if se.items else None),
		"project": se.project,
		"task": se.get(TASK_FELD),
		"employee": se.get(MITARBEITER_FELD),
		"positionen": [
			{
				"item_code": z.item_code,
				"item_name": z.item_name,
				"menge": flt(z.qty),
				"uom": z.uom,
				"bestand_nachher": flt(
					frappe.db.get_value(
						"Bin", {"item_code": z.item_code, "warehouse": z.s_warehouse}, "actual_qty"
					)
				),
			}
			for z in se.items
		],
		"modified": str(se.modified),
		"duplikat": duplikat,
		"hinweise": hinweise or [],
	}


def vorhandene_entnahme(idempotency_key: str) -> dict | None:
	"""Beleg zu einem Key (nicht storniert) als ``{"name", "employee"}`` oder ``None``."""
	if not idempotency_key:
		return None
	row = frappe.db.get_value(
		"Stock Entry",
		{SYNC_KEY_FELD: idempotency_key, "docstatus": ["<", 2]},
		["name", MITARBEITER_FELD],
		as_dict=True,
	)
	return {"name": row.name, "employee": row.get(MITARBEITER_FELD)} if row else None


def _position(index: int, pos: dict) -> dict:
	if not isinstance(pos, dict):
		raise MaterialFehler("menge_ungueltig", f"position {index}: object expected", {"index": index})
	item_code = pos.get("item_code")
	ean = pos.get("ean")
	if not item_code and ean:
		item_code = artikel_zu_ean(str(ean))
		if not item_code:
			raise MaterialFehler("ean_unbekannt", f"unknown EAN {ean}", {"index": index, "ean": str(ean)})
	if not item_code or not isinstance(item_code, str):
		raise MaterialFehler(
			"artikel_unbekannt", f"position {index}: item_code or ean required", {"index": index}
		)
	item = frappe.db.get_value(
		"Item",
		item_code,
		["name", "item_name", "stock_uom", "is_stock_item", "disabled", "has_variants"],
		as_dict=True,
	)
	if not item:
		raise MaterialFehler(
			"artikel_unbekannt", f"unknown item {item_code}", {"index": index, "item_code": item_code}
		)
	if not cint(item.is_stock_item) or cint(item.disabled) or cint(item.has_variants):
		raise MaterialFehler(
			"kein_lagerartikel",
			f"{item_code} is not an active stock item",
			{"index": index, "item_code": item_code},
		)
	try:
		menge = flt(pos.get("menge", pos.get("qty")))
	except (TypeError, ValueError):
		menge = 0
	if menge <= 0:
		raise MaterialFehler(
			"menge_ungueltig", f"{item_code}: quantity must be > 0", {"index": index, "item_code": item_code}
		)
	if _ganzzahlige_einheiten({item.stock_uom}) and menge != int(menge):
		raise MaterialFehler(
			"menge_ganzzahlig",
			f"{item_code}: quantity in {item.stock_uom} must be a whole number",
			{"index": index, "item_code": item_code, "uom": item.stock_uom},
		)
	return {"item_code": item.name, "item_name": item.item_name, "uom": item.stock_uom, "menge": menge}


def entnahme_buchen(
	employee: str | None,
	warehouse: str,
	project: str | None,
	task: str | None,
	positionen: list[dict],
	idempotency_key: str,
	notiz: str | None = None,
	mit_rechten: bool = False,
) -> dict:
	"""Materialentnahme als eingereichten Stock Entry „Material Issue“ buchen.

	``mit_rechten``: Insert/Submit mit den Frappe-Rechten des Benutzers (User Permissions auf
	Firma/Lager) - der Kern setzt es für Buchungen außerhalb des eigenen Fahrzeuglagers. Sonst
	bucht der Server für den Monteur, der im Desk nur Entwürfe anlegen darf (0.4).

	``positionen``: ``[{"item_code" | "ean", "menge"}]`` (``qty`` als Synonym), Lagereinheit.
	Antwort: ``stock_entry``, ``docstatus`` (1), ``posting_date``, ``warehouse``, ``project``,
	``task``, ``employee``, ``positionen`` (mit ``bestand_nachher``), ``duplikat``, ``hinweise``.
	Fehler als ``MaterialFehler`` (Modul-Docstring).
	"""
	vorhanden = vorhandene_entnahme(idempotency_key)
	if vorhanden:
		if (vorhanden["employee"] or None) != (employee or None):
			raise MaterialFehler(
				"schluessel_fremd",
				"idempotency key belongs to another employee",
				{"stock_entry": vorhanden["name"]},
			)
		return _beleg(vorhanden["name"], duplikat=True)

	info = lager(warehouse)
	if not info:
		raise MaterialFehler(
			"lager_unbekannt", f"warehouse {warehouse!r} not found or not bookable", {"warehouse": warehouse}
		)
	if not isinstance(positionen, list) or not positionen:
		raise MaterialFehler("menge_ungueltig", "at least one position required")
	if len(positionen) > MAX_POSITIONEN:
		raise MaterialFehler(
			"zu_viele_positionen", f"at most {MAX_POSITIONEN} positions", {"max": MAX_POSITIONEN}
		)
	if task:
		task_projekt = frappe.db.get_value("Task", task, "project")
		if task_projekt is None and not frappe.db.exists("Task", task):
			raise MaterialFehler("task_unbekannt", f"task {task} not found", {"task": task})
		if project and task_projekt and task_projekt != project:
			raise MaterialFehler(
				"task_projekt",
				f"task {task} belongs to project {task_projekt}",
				{"task": task, "project": task_projekt},
			)
		project = project or task_projekt
	if project and not frappe.db.exists("Project", project):
		raise MaterialFehler("projekt_unbekannt", f"project {project} not found", {"project": project})

	zeilen = [_position(i, p) for i, p in enumerate(positionen)]
	# Bestand je Artikel (mehrere Zeilen desselben Artikels zusammen)
	summe: dict[str, float] = {}
	for z in zeilen:
		summe[z["item_code"]] = summe.get(z["item_code"], 0.0) + z["menge"]
	fehlend, hinweise = [], []
	for item_code, menge in summe.items():
		verfuegbar = flt(
			frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": info["warehouse"]}, "actual_qty")
		)
		if menge > verfuegbar + 1e-9:
			eintrag = {"item_code": item_code, "verfuegbar": verfuegbar, "angefordert": menge}
			if _negativ_erlaubt(item_code):
				hinweise.append(
					f"{item_code}: {menge:g} entnommen, Bestand war {verfuegbar:g} (negativer Bestand erlaubt)"
				)
			else:
				fehlend.append(eintrag)
	if fehlend:
		raise MaterialFehler(
			"bestand_unzureichend",
			"quantity exceeds stock in " + info["warehouse"],
			{"warehouse": info["warehouse"], "positionen": fehlend},
		)

	bemerkung = f"Materialentnahme Corevatis-App ({now_datetime():%Y-%m-%d %H:%M})"
	if notiz:
		bemerkung += f": {str(notiz)[:500]}"
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": ZWECK,
			"purpose": ZWECK,
			"company": info["company"],
			"from_warehouse": info["warehouse"],
			"project": project,
			TASK_FELD: task,
			MITARBEITER_FELD: employee,
			SYNC_KEY_FELD: idempotency_key,
			"remarks": bemerkung,
			"items": [
				{
					"item_code": z["item_code"],
					"qty": z["menge"],
					"uom": z["uom"],
					"stock_uom": z["uom"],
					"conversion_factor": 1,
					"s_warehouse": info["warehouse"],
					"project": project,
				}
				for z in zeilen
			],
		}
	)
	if mit_rechten:
		se.insert()
		se.submit()
	else:
		# ignore_permissions bewusst: eigenes Fahrzeuglager, Rechte prüft der Kern (Modul-Docstring)
		se.flags.ignore_permissions = True
		se.insert()
		se.submit()
	return _beleg(se.name, duplikat=False, hinweise=hinweise)


def entnahmen(
	warehouse: str | None = None, employee: str | None = None, datum=None, limit: int = 50
) -> list[dict]:
	"""Eingereichte App-Entnahmen eines Tages (Verlauf), neueste zuerst."""
	filters: dict = {"purpose": ZWECK, "docstatus": 1, SYNC_KEY_FELD: ["is", "set"]}
	if datum:
		filters["posting_date"] = datum
	if warehouse:
		filters["from_warehouse"] = warehouse
	if employee:
		filters[MITARBEITER_FELD] = employee
	namen = frappe.get_all(
		"Stock Entry", filters=filters, pluck="name", order_by="creation desc", limit=cint(limit) or 50
	)
	out = []
	for name in namen:
		beleg = _beleg(name, duplikat=False)
		beleg.pop("duplikat", None)
		beleg.pop("hinweise", None)
		beleg["idempotency_key"] = frappe.db.get_value("Stock Entry", name, SYNC_KEY_FELD)
		out.append(beleg)
	return out
