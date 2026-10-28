"""Importer für Katalogpakete (Aufgabe 2.3) - liest dieselben CSV-Dateien wie Frappe Data Import.

Format der CSV-Dateien (Frappe-Data-Import-Vorlage mit Feldnamen als Spaltenköpfen, damit sie
unabhängig von der Sprache der Site passen): Spalten ohne Punkt sind Felder des Doctypes,
``tabelle.feld`` sind Felder einer Kindtabelle. Ein Datensatz beginnt mit einer Zeile, in der
mindestens ein Feld des Doctypes gefüllt ist; folgende Zeilen mit leeren Doctype-Spalten sind
weitere Kindzeilen (z. B. weitere Materialien einer Stückliste). Zahlen mit Dezimalpunkt.

Regeln:

- **Idempotent.** Fehlt ein Datensatz, wird er angelegt; ein zweiter Lauf meldet
  ``changes: []``.
- **Kein Überschreiben.** Weicht ein vorhandener Datensatz von der CSV ab (Preis im Desk
  geändert, Stückliste angepasst), bleibt er unverändert und steht unter ``skipped``. Erst
  ``force=True`` übernimmt die CSV-Werte; Stücklisten werden dann nicht geändert, sondern als
  neue Version angelegt und als Standard gebucht (die alte bleibt erhalten).
- **Firma.** Stücklisten sind firmenbezogen: ``firma`` ersetzt die Spalte ``company`` der
  BOM-Vorlage (ohne ``firma`` gilt der Wert der Vorlage). Artikel, Artikelgruppen,
  Preislisten und Preise gelten site-weit.
- Artikelgruppen ohne übergeordnete Gruppe kommen unter die Wurzel des Baums (wie beim
  Data Import, wo ERPNext die leere Gruppe an die Wurzel hängt).
"""

from __future__ import annotations

import re
from pathlib import Path

import frappe
from frappe.utils import cint, cstr, flt
from frappe.utils.csvutils import read_csv_content
from frappe.utils.nestedset import get_root_of

DATEN_DIR = Path(__file__).resolve().parent / "daten"
PAKET_RE = re.compile(r"^[a-z0-9_-]{1,64}$")

# Reihenfolge = Abhängigkeiten (wie die Nummern der Dateinamen für den Data Import im Desk)
DATEIEN = (
	("UOM", "01_uom.csv"),
	("Item Group", "02_item_group.csv"),
	("Price List", "03_price_list.csv"),
	("Workstation Operating Component", "04_workstation_operating_component.csv"),
	("Workstation", "05_workstation.csv"),
	("Operation", "06_operation.csv"),
	("Item", "07_item.csv"),
	("Item Price", "08_item_price.csv"),
	("BOM", "09_bom.csv"),
)

# Schlüsselfeld je Doctype (Name des Datensatzes); Item Price und BOM werden gesucht
SCHLUESSEL = {
	"UOM": "uom_name",
	"Item Group": "item_group_name",
	"Price List": "price_list_name",
	"Workstation Operating Component": "component_name",
	"Workstation": "workstation_name",
	"Operation": "name",
	"Item": "item_code",
}

# Felder, die nur informieren (Annahme-Hinweise) und beim Abgleich nicht zählen
NICHT_VERGLEICHEN = {
	"UOM": {"description"},
	"Workstation": {"description"},
	"Operation": {"description"},
	"Item Price": {"reference", "note"},
}

# Kopffelder der Stückliste, die der Abgleich prüft (Kindtabellen: siehe _bom_signatur)
BOM_KOPF = ("quantity", "with_operations", "rm_cost_as_per", "buying_price_list", "currency")
NUMERISCH = {"Int", "Check", "Float", "Currency", "Percent"}


class Bericht:
	def __init__(self, paket: str, firma: str, force: bool):
		self.paket = paket
		self.firma = firma
		self.force = force
		self.changes: list[str] = []
		self.skipped: list[str] = []
		self.warnings: list[str] = []
		self.unveraendert = 0
		self.kosten_geaendert = False

	def as_dict(self) -> dict:
		return {
			"paket": self.paket,
			"firma": self.firma,
			"force": self.force,
			"changes": self.changes,
			"skipped": self.skipped,
			"warnings": self.warnings,
			"unveraendert": self.unveraendert,
		}


# --------------------------------------------------------------------------- Pakete lesen


def list_pakete() -> list[str]:
	return sorted(
		p.name
		for p in DATEN_DIR.iterdir()
		if p.is_dir() and PAKET_RE.match(p.name) and all((p / datei).is_file() for _dt, datei in DATEIEN)
	)


def paket_dir(paket: str) -> Path:
	if not paket or not PAKET_RE.match(paket) or paket not in list_pakete():
		frappe.throw(f"Unbekanntes Katalogpaket: {paket!r} (vorhanden: {', '.join(list_pakete())})")
	return DATEN_DIR / paket


def lese_csv(pfad: Path) -> tuple[list[str], list[list]]:
	"""Kopfzeile und Datenzeilen - mit dem CSV-Leser von Frappe (wie Data Import)."""
	rows = read_csv_content(pfad.read_bytes())
	if not rows:
		return [], []
	header = [cstr(h).strip() for h in rows[0]]
	return header, [list(r) + [None] * (len(header) - len(r)) for r in rows[1:]]


def lese_paket(paket: str, warnings: list[str] | None = None) -> dict[str, list[dict]]:
	"""Alle Vorlagen eines Pakets als ``{doctype: [datensatz, ...]}`` (Kindtabellen als Listen)."""
	verzeichnis = paket_dir(paket)
	out = {}
	for doctype, datei in DATEIEN:
		header, rows = lese_csv(verzeichnis / datei)
		out[doctype] = zu_datensaetzen(doctype, header, rows, warnings)
	return out


def zu_datensaetzen(doctype: str, header: list[str], rows: list[list], warnings=None) -> list[dict]:
	meta = frappe.get_meta(doctype)
	kopf = [i for i, h in enumerate(header) if "." not in h]
	tabellen: dict[str, list[int]] = {}
	for i, h in enumerate(header):
		if "." in h:
			tabellen.setdefault(h.split(".", 1)[0], []).append(i)

	unbekannt = [header[i] for i in kopf if header[i] != "name" and not meta.has_field(header[i])]
	if unbekannt and warnings is not None:
		warnings.append(
			f"{doctype}: Feld(er) {', '.join(unbekannt)} fehlen auf dieser Site - Werte nicht übernommen"
		)

	datensaetze: list[dict] = []
	for row in rows:
		if all(_leer(v) for v in row):
			continue
		if any(not _leer(row[i]) for i in kopf) or not datensaetze:
			datensatz = {}
			for i in kopf:
				feld = header[i]
				if feld in unbekannt:
					continue
				datensatz[feld] = _wert(meta, feld, row[i])
			datensaetze.append(datensatz)
		for tabelle, indizes in tabellen.items():
			if all(_leer(row[i]) for i in indizes):
				continue
			kind_meta = frappe.get_meta(meta.get_field(tabelle).options)
			zeile = {header[i].split(".", 1)[1]: row[i] for i in indizes}
			datensaetze[-1].setdefault(tabelle, []).append(
				{feld: _wert(kind_meta, feld, wert) for feld, wert in zeile.items()}
			)
	return datensaetze


def _leer(wert) -> bool:
	return wert is None or cstr(wert).strip() == ""


def _wert(meta, feld: str, wert):
	if _leer(wert):
		return None
	df = meta.get_field(feld)
	feldtyp = df.fieldtype if df else "Data"
	text = cstr(wert).strip()
	if feldtyp in ("Check", "Int"):
		return cint(text)
	if feldtyp in ("Float", "Currency", "Percent"):
		# Dezimalpunkt wie in den Vorlagen; kein flt(), das Kommas als Tausendertrennung entfernt
		return float(text)
	return text


def _norm(wert):
	if wert is None or (isinstance(wert, str) and wert.strip() == ""):
		return ""
	if isinstance(wert, bool | int | float):
		return round(float(wert), 6)
	return frappe.utils.strip_html_tags(cstr(wert)).strip()


# --------------------------------------------------------------------------- Import


def importieren(firma=None, paket="abdichtung", force=False, commit=True) -> dict:
	force = bool(cint(force))
	warnings: list[str] = []
	daten = lese_paket(paket, warnings)
	firma = _firma(firma, daten["BOM"])
	bericht = Bericht(paket, firma, force)
	bericht.warnings.extend(warnings)

	_fehlende_einheiten(daten, bericht)
	for doctype, _datei in DATEIEN:
		if doctype == "BOM":
			_boms(daten["BOM"], bericht)
		elif doctype == "Item Price":
			_einfach(doctype, daten[doctype], bericht, finde=_finde_preis, label=_label_preis)
		else:
			_einfach(doctype, daten[doctype], bericht)
		if commit:
			frappe.db.commit()

	if bericht.kosten_geaendert:
		_bom_kosten_aktualisieren(daten["BOM"], bericht)
		if commit:
			frappe.db.commit()
	return bericht.as_dict()


def _firma(firma: str | None, boms: list[dict]) -> str:
	if not firma:
		firma = next((b.get("company") for b in boms if b.get("company")), None)
	if not firma or not frappe.db.exists("Company", firma):
		frappe.throw(f"Firma {firma!r} existiert nicht - Parameter firma angeben")
	return firma


def _fehlende_einheiten(daten: dict, bericht: Bericht) -> None:
	"""Einheiten, die Artikel/Stücklisten nutzen, aber nicht in der UOM-Vorlage stehen (``Kg`` aus
	dem ERPNext-Stamm), anlegen, falls die Site sie nicht hat."""
	eigene = {u["uom_name"] for u in daten["UOM"]}
	genutzt = {i["stock_uom"] for i in daten["Item"]}
	genutzt |= {z["uom"] for b in daten["BOM"] for z in b.get("items", []) if z.get("uom")}
	for uom in sorted(genutzt - eigene):
		if not frappe.db.exists("UOM", uom):
			frappe.get_doc({"doctype": "UOM", "uom_name": uom}).insert(ignore_permissions=True)
			bericht.changes.append(f"UOM {uom} angelegt (nicht in der Vorlage, ERPNext-Stamm fehlte)")


def _vorbereiten(doctype: str, daten: dict) -> dict:
	daten = dict(daten)
	if doctype == "Item Group" and not daten.get("parent_item_group"):
		daten["parent_item_group"] = get_root_of("Item Group")
	return daten


def _finde_name(doctype: str, daten: dict) -> str | None:
	name = daten.get(SCHLUESSEL[doctype])
	return name if name and frappe.db.exists(doctype, name) else None


def _label_name(doctype: str, daten: dict) -> str:
	return f"{doctype} {daten.get(SCHLUESSEL[doctype])}"


def _finde_preis(doctype: str, daten: dict) -> str | None:
	return frappe.db.get_value(
		"Item Price",
		{
			"price_list": daten["price_list"],
			"item_code": daten["item_code"],
			"uom": daten["uom"],
			"customer": ["is", "not set"],
			"supplier": ["is", "not set"],
			"batch_no": ["is", "not set"],
		},
		"name",
		order_by="creation asc",
	)


def _label_preis(doctype: str, daten: dict) -> str:
	return f"Item Price {daten['item_code']} ({daten['price_list']}, {daten['uom']})"


def _abweichungen(doc, daten: dict, ignorieren=()) -> list[str]:
	felder = []
	for feld, soll in daten.items():
		if feld in ignorieren or feld in ("doctype", "name"):
			continue
		if isinstance(soll, list):
			spalten = sorted({k for zeile in soll for k in zeile})
			ist_zeilen = [tuple(_norm(z.get(k)) for k in spalten) for z in doc.get(feld)]
			soll_zeilen = [tuple(_norm(z.get(k)) for k in spalten) for z in soll]
			if sorted(ist_zeilen) != sorted(soll_zeilen):
				felder.append(feld)
		elif _norm(doc.get(feld)) != _norm(soll):
			felder.append(feld)
	return felder


def _einfach(doctype: str, datensaetze: list[dict], bericht: Bericht, finde=_finde_name, label=_label_name):
	ignorieren = NICHT_VERGLEICHEN.get(doctype, set())
	for roh in datensaetze:
		daten = _vorbereiten(doctype, roh)
		name = finde(doctype, daten)
		if not name:
			doc = frappe.get_doc({"doctype": doctype, **daten})
			if doctype == "Operation":
				doc.name = daten["name"]
			doc.insert(ignore_permissions=True)
			bericht.changes.append(f"{label(doctype, daten)} angelegt")
			continue

		doc = frappe.get_doc(doctype, name)
		felder = _abweichungen(doc, daten, ignorieren)
		if not felder:
			bericht.unveraendert += 1
			continue
		if not bericht.force:
			bericht.skipped.append(
				f"{label(doctype, daten)}: weicht ab ({', '.join(felder)}) - nicht überschrieben (force=True)"
			)
			continue
		if doctype == "Item" and "stock_uom" in felder:
			bericht.warnings.append(f"{label(doctype, daten)}: Lagereinheit wird nicht geändert")
			felder.remove("stock_uom")
		for feld in felder:
			doc.set(feld, daten[feld])
		doc.save(ignore_permissions=True)
		bericht.changes.append(f"{label(doctype, daten)} aktualisiert ({', '.join(felder)})")
		if doctype == "Workstation" or (
			doctype == "Item Price" and frappe.db.get_value("Price List", daten["price_list"], "buying")
		):
			bericht.kosten_geaendert = True


# --------------------------------------------------------------------------- Stücklisten


def _bestehende_bom(item: str, firma: str) -> str | None:
	namen = frappe.get_all(
		"BOM",
		filters={"item": item, "company": firma, "docstatus": 1, "is_active": 1},
		order_by="is_default desc, creation desc",
		pluck="name",
		limit=1,
	)
	return namen[0] if namen else None


def _bom_signatur(quelle) -> tuple:
	"""Vergleichsschlüssel einer Stückliste (Dokument oder CSV-Datensatz)."""

	kopf = tuple(_norm(quelle.get(f)) for f in BOM_KOPF)
	material = sorted(
		(_norm(z.get("item_code")), _norm(z.get("qty")), _norm(z.get("uom")))
		for z in quelle.get("items") or []
	)
	arbeitsgaenge = sorted(
		(_norm(z.get("operation")), _norm(z.get("workstation")), _norm(z.get("time_in_mins")))
		for z in quelle.get("operations") or []
	)
	return kopf, tuple(material), tuple(arbeitsgaenge)


def _boms(datensaetze: list[dict], bericht: Bericht) -> None:
	for roh in datensaetze:
		daten = {**roh, "company": bericht.firma}
		item = daten["item"]
		vorhanden = _bestehende_bom(item, bericht.firma)
		if vorhanden:
			if _bom_signatur(frappe.get_doc("BOM", vorhanden)) == _bom_signatur(daten):
				bericht.unveraendert += 1
				continue
			if not bericht.force:
				bericht.skipped.append(
					f"BOM {vorhanden} ({item}): weicht von der Vorlage ab - nicht überschrieben (force=True)"
				)
				continue
		bom = frappe.get_doc({"doctype": "BOM", **daten})
		bom.insert(ignore_permissions=True)
		bom.submit()
		ersetzt = f", ersetzt {vorhanden} als Standard" if vorhanden else ""
		bericht.changes.append(f"BOM {bom.name} ({item}) angelegt{ersetzt}")


def _bom_kosten_aktualisieren(datensaetze: list[dict], bericht: Bericht) -> None:
	"""Nach geänderten Einkaufspreisen/Stundensatz (force): Kosten der gebuchten Stücklisten mit
	ERPNext neu ermitteln (wie „Kosten aktualisieren“ im BOM-Formular)."""
	anzahl = 0
	for daten in datensaetze:
		name = _bestehende_bom(daten["item"], bericht.firma)
		if not name:
			continue
		bom = frappe.get_doc("BOM", name)
		alt = flt(bom.total_cost)
		bom.update_cost(update_parent=False, from_child_bom=True, update_hour_rate=True, save=True)
		if flt(frappe.db.get_value("BOM", name, "total_cost")) != alt:
			anzahl += 1
	if anzahl:
		bericht.changes.append(f"BOM-Kosten neu ermittelt ({anzahl} Stücklisten)")


# --------------------------------------------------------------------------- Übersicht


def uebersicht(firma=None, paket="abdichtung") -> list[dict]:
	daten = lese_paket(paket)
	firma = _firma(firma, daten["BOM"])
	verkauf = next(p["price_list_name"] for p in daten["Price List"] if p.get("selling"))
	mit_bom = {b["item"] for b in daten["BOM"]}
	zeilen = []
	for item in daten["Item"]:
		if not item.get("is_sales_item"):
			continue
		code = item["item_code"]
		bom = _bestehende_bom(code, firma) if code in mit_bom else None
		kosten = (
			frappe.db.get_value(
				"BOM",
				bom,
				["raw_material_cost", "operating_cost", "total_cost", "quantity", "is_default"],
				as_dict=True,
			)
			if bom
			else {}
		)
		preis = frappe.db.get_value(
			"Item Price",
			{"price_list": verkauf, "item_code": code, "uom": item["stock_uom"]},
			"price_list_rate",
		)
		zeilen.append(
			{
				"item_code": code,
				"item_name": item.get("item_name"),
				"uom": item["stock_uom"],
				"cv_verfahren": item.get("cv_verfahren"),
				"bom": bom,
				"is_default": cint(kosten.get("is_default")) if kosten else None,
				"material": flt(kosten.get("raw_material_cost")) if kosten else None,
				"montage": flt(kosten.get("operating_cost")) if kosten else None,
				"herstellkosten": flt(kosten.get("total_cost")) if kosten else None,
				"bom_menge": flt(kosten.get("quantity")) if kosten else None,
				"verkaufspreis": flt(preis) if preis is not None else None,
			}
		)
	return zeilen
