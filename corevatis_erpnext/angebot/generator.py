"""Aufgabe 4.1: Angebot (Quotation) aus dem Aufmaß.

Ablauf (``erzeuge_angebot``):

1. Aufmaß aus ``corevatis`` lesen (nur per Doctype-Name, kein Import über die Lizenzgrenze).
2. Leistungen bestimmen (``leistungen_aus_aufmass``): Zeilen mit ``item_code`` nennen das Item
   direkt; Zeilen mit nur ``verfahren`` holen die aktiven Leistungsregeln dieses Verfahrens,
   die eine Leistungsposition (``Leistungsregel.item_code``) tragen. Die Mengenregel ist die
   dort gefundene Regel, sonst ``Item.cv_leistungsregel``. Zeilen mit ``menge_fest`` (App,
   Durchstich A1: vom Meister bestätigte Menge je Raum/Bauteil) geben die Menge fest vor - die
   Summe dieser Zeilen je Item, ohne Mengenregel.
3. Herstellkosten je Einheit aus der Standard-Stückliste der Firma: Material zum
   Einkaufspreis (``raw_material_cost``), Montage in Minuten (Arbeitsgänge) - wie
   ``katalog.uebersicht``. Gerechnet wird hier nichts (Architekturregel 7).
4. Kalkulation im Kern: ``frappe.get_attr(KERN_KALKULATION)`` (Aufmaß → Mengen →
   Kalkulationsschema → Einzelpreise, ``corevatis.engine.quote``).
5. Quotation anlegen: Partei (Kunde, Lead oder Platzhalter „Interessent“), Preisliste
   „Standard-Verkauf“, Preise aus der Engine (``rate``), Steuervorlage „Umsatzsteuer 19 %“
   mit Steuerzeilen, ``valid_till`` + 30 Tage, Titel, Vorbemerkung, Garantiehinweis,
   ``cv_*``-Felder (``angebot.felder``) und die vollständige Kalkulation als JSON.

Idempotent: Gibt es zum Aufmaß schon ein Angebot (nicht storniert), meldet der Aufruf es
statt ein zweites anzulegen; ``neu_erzeugen=1`` ersetzt Positionen und Werte eines
**Entwurfs** (gleiche Nummer), ein gebuchtes Angebot bleibt unangetastet.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, nowdate

from corevatis_erpnext.angebot import textbausteine
from corevatis_erpnext.angebot.felder import MODULE

# Vertrag mit corevatis (Doctype-Namen, Fassade) - geprüft in corevatis/tests/test_erpnext_contract.py
KERN_APP = "corevatis"
AUFMASS_DOCTYPE = "Aufmass"
OBJEKT_DOCTYPE = "Objekt"
LEISTUNGSREGEL_DOCTYPE = "Leistungsregel"
KALKULATIONSSCHEMA_DOCTYPE = "Kalkulationsschema"
KERN_KALKULATION = "corevatis.angebot.kalkulation.kalkuliere"
# Schlüssel, die der Adapter je Leistung an den Kern gibt (Teilmenge von LEISTUNG_SCHLUESSEL im Kern)
LEISTUNG_SCHLUESSEL = (
	"item_code",
	"bezeichnung",
	"einheit",
	"leistungsregel",
	"menge",
	"material_je_einheit",
	"montage_min_je_einheit",
	"herkunft",
)

ROLLEN = ("Büro", "Meister", "System Manager")
STEUERVORLAGE = "Umsatzsteuer 19 %"
PREISLISTE = "Standard-Verkauf"
PLATZHALTER_KUNDE = "Interessent"
GUELTIGKEIT_TAGE = 30
DATA_LAENGE = 140


class AngebotFehler(frappe.ValidationError):
	pass


# --------------------------------------------------------------------------- Hilfen


def kern_vorhanden() -> bool:
	return bool(frappe.db.exists("DocType", AUFMASS_DOCTYPE))


def _aufmass_lesen(name: str):
	"""Aufmaß aus dem Kern (per Doctype-Name) mit Leserecht des Benutzers."""
	doc = frappe.get_doc(AUFMASS_DOCTYPE, name)
	doc.check_permission("read")
	return doc


def _objekt_werte(objekt: str | None) -> frappe._dict:
	"""Kunde und Titel des Objekts (Kern), leer ohne Objekt."""
	if not objekt:
		return frappe._dict()
	return frappe._dict(
		frappe.db.get_value(OBJEKT_DOCTYPE, objekt, ["kunde_doctype", "kunde", "titel"], as_dict=True) or {}
	)


def _kern_kalkulation():
	"""Fassade des Kerns per Name (kein Import über die Lizenzgrenze)."""
	return frappe.get_attr(KERN_KALKULATION)


def _kurz(texte, laenge: int = DATA_LAENGE) -> str | None:
	"""Eindeutige Werte (Reihenfolge bleibt) für ein Data-Feld, höchstens 140 Zeichen."""
	werte = list(dict.fromkeys(t for t in texte if t))
	if not werte:
		return None
	text = ", ".join(werte)
	return text if len(text) <= laenge else text[: laenge - 1] + "…"


def _bom_kosten(item_code: str, firma: str) -> dict | None:
	"""Herstellkosten je Einheit aus der gebuchten, aktiven Standard-Stückliste der Firma."""
	namen = frappe.get_all(
		"BOM",
		filters={"item": item_code, "company": firma, "docstatus": 1, "is_active": 1},
		order_by="is_default desc, creation desc",
		pluck="name",
		limit=1,
	)
	if not namen:
		return None
	bom = frappe.get_doc("BOM", namen[0])
	menge = flt(bom.quantity) or 1
	minuten = sum(flt(op.time_in_mins) for op in bom.get("operations") or [])
	return {
		"bom": bom.name,
		"material_je_einheit": str(flt(flt(bom.raw_material_cost) / menge, 6)),
		"montage_min_je_einheit": str(flt(minuten / menge, 6)),
	}


# --------------------------------------------------------------------------- Leistungen


def _regeln_je_verfahren(verfahren: str) -> list[tuple[str, str]]:
	"""Aktive Leistungsregeln eines Verfahrens mit Leistungsposition: [(regel, item_code)]."""
	return [
		(r.name, r.item_code)
		for r in frappe.get_all(
			LEISTUNGSREGEL_DOCTYPE,
			filters={"verfahren": verfahren, "aktiv": 1, "item_code": ("is", "set")},
			fields=["name", "item_code"],
			order_by="name asc",
		)
	]


def leistungen_aus_aufmass(aufmass, firma: str) -> tuple[list[dict], list[str]]:
	"""Leistungen für ``corevatis.angebot.kalkulation.kalkuliere`` und Warnungen.

	``aufmass``: Dokument ``Aufmass`` (Kern). Reihenfolge: erste Nennung im Aufmaß.
	"""
	auswahl: dict[str, dict] = {}
	warnungen: list[str] = []

	def merken(item_code: str, zeile, regel: str | None = None):
		eintrag = auswahl.setdefault(item_code, {"regel": None, "zeilen": [], "menge": None})
		if regel and not eintrag["regel"]:
			eintrag["regel"] = regel
		eintrag["zeilen"].append(zeile)
		if cint(zeile.get("menge_fest")):
			eintrag["menge"] = flt(eintrag["menge"]) + flt(zeile.get("menge"))

	verfahren_gesehen: set[str] = set()
	for zeile in aufmass.get("positionen") or []:
		code = (zeile.get("item_code") or "").strip()
		if code:
			merken(code, zeile)
			continue
		verfahren = zeile.get("verfahren")
		if not verfahren:
			continue
		treffer = _regeln_je_verfahren(verfahren)
		if not treffer and verfahren not in verfahren_gesehen:
			warnungen.append(
				_("Method {0}: no active quantity rule with a service item - add items manually.").format(
					verfahren
				)
			)
		verfahren_gesehen.add(verfahren)
		for regel, item_code in treffer:
			merken(item_code, zeile, regel)

	leistungen = []
	for code, eintrag in auswahl.items():
		item = frappe.db.get_value(
			"Item",
			code,
			["name", "item_name", "stock_uom", "disabled", "cv_verfahren", "cv_leistungsregel"],
			as_dict=True,
		)
		if not item or item.disabled:
			warnungen.append(_("Item {0} not found or disabled - skipped.").format(code))
			continue
		regel = eintrag["regel"] or item.cv_leistungsregel
		fest = eintrag["menge"]
		if not regel and fest is None:
			warnungen.append(_("Item {0} has no quantity rule - skipped.").format(code))
			continue
		kosten = _bom_kosten(code, firma)
		if not kosten:
			warnungen.append(_("Item {0} has no submitted BOM for {1} - skipped.").format(code, firma))
			continue
		zeilen = eintrag["zeilen"]
		leistung = {
			"item_code": code,
			"bezeichnung": item.item_name,
			"einheit": item.stock_uom,
			"leistungsregel": regel,
			"material_je_einheit": kosten["material_je_einheit"],
			"montage_min_je_einheit": kosten["montage_min_je_einheit"],
			"herkunft": {
				"aufmass_positionen": [z.get("name") for z in zeilen if z.get("name")],
				"raeume": list(dict.fromkeys(z.get("raum") for z in zeilen if z.get("raum"))),
				"bauteile": list(dict.fromkeys(z.get("bauteil") for z in zeilen if z.get("bauteil"))),
				"verfahren": item.cv_verfahren
				or next((z.get("verfahren") for z in zeilen if z.get("verfahren")), None),
				"stueckliste": kosten["bom"],
			},
		}
		if fest is not None:
			# feste Menge der App statt Mengenregel (Kern: ``kalkuliere`` mit ``menge``)
			leistung["menge"] = str(flt(fest, 6))
			leistung["leistungsregel"] = None
		leistungen.append(leistung)
	return leistungen, warnungen


# --------------------------------------------------------------------------- Partei, Firma, Stammdaten


def _standardfirma() -> str | None:
	return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)


def _platzhalterkunde() -> str:
	name = frappe.db.get_value("Customer", {"customer_name": PLATZHALTER_KUNDE}, "name")
	if name:
		return name
	gruppe = frappe.db.get_single_value("Selling Settings", "customer_group") or frappe.db.get_value(
		"Customer Group", {"is_group": 0}, "name"
	)
	gebiet = frappe.db.get_single_value("Selling Settings", "territory") or frappe.db.get_value(
		"Territory", {"is_group": 0}, "name"
	)
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": PLATZHALTER_KUNDE,
			"customer_type": "Individual",
			"customer_group": gruppe,
			"territory": gebiet,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _partei(aufmass, kunde: str | None, lead: str | None) -> tuple[str, str]:
	"""(quotation_to, party_name): Kunde > Lead > Referenz des Aufmaßes > Kunde des Objekts >
	Platzhalterkunde „Interessent“ (bis zur Annahme, 4.4)."""
	kandidaten = [("Customer", kunde), ("Lead", lead)]
	if aufmass.get("referenz_doctype") in ("Lead", "Customer"):
		kandidaten.append((aufmass.referenz_doctype, aufmass.get("referenz_name")))
	objekt = _objekt_werte(aufmass.get("objekt"))
	if objekt.get("kunde_doctype") in ("Lead", "Customer"):
		kandidaten.append((objekt.kunde_doctype, objekt.kunde))
	for doctype, name in kandidaten:
		if not name:
			continue
		if not frappe.db.exists(doctype, name):
			frappe.throw(_("{0} {1} does not exist.").format(_(doctype), name), AngebotFehler)
		frappe.get_doc(doctype, name).check_permission("read")
		return doctype, name
	return "Customer", _platzhalterkunde()


def _firma_des_objekts(aufmass) -> str | None:
	"""Firma am Aufmaß bzw. Objekt (Kern), falls der Doctype ein Feld ``company`` hat."""
	if aufmass.get("company"):
		return aufmass.company
	for doctype, name in ((AUFMASS_DOCTYPE, aufmass.get("name")), (OBJEKT_DOCTYPE, aufmass.get("objekt"))):
		if name and frappe.db.exists("DocType", doctype) and frappe.db.has_column(doctype, "company"):
			wert = frappe.db.get_value(doctype, name, "company")
			if wert:
				return wert
	return None


def _firmen(aufmass, quotation_to: str, party: str) -> list[str]:
	"""Firmen in der Reihenfolge des Vorrangs (Abnahme mvp-test): Firma des Aufmaßes/Objekts,
	Firma des Leads, Standardfirma - nur vorhandene, ohne Doppelte."""
	kandidaten = [
		_firma_des_objekts(aufmass),
		frappe.db.get_value("Lead", party, "company") if quotation_to == "Lead" else None,
		_standardfirma(),
	]
	firmen = [f for f in dict.fromkeys(kandidaten) if f and frappe.db.exists("Company", f)]
	if not firmen:
		frappe.throw(_("No company found (set a default company)."), AngebotFehler)
	return firmen


def _firma(quotation_to: str, party: str, aufmass=None) -> str:
	return _firmen(aufmass or frappe._dict(), quotation_to, party)[0]


def _steuervorlage(firma: str) -> str | None:
	return frappe.db.get_value(
		"Sales Taxes and Charges Template", {"company": firma, "title": STEUERVORLAGE, "disabled": 0}, "name"
	) or frappe.db.get_value(
		"Sales Taxes and Charges Template", {"company": firma, "is_default": 1, "disabled": 0}, "name"
	)


def ensure_textbaustein() -> str:
	"""Textbausteine des Angebots (4.3) anlegen, falls sie fehlen; Rückgabe: Name der Vorbemerkung."""
	textbausteine.ensure_textbausteine()
	return textbausteine.VORBEMERKUNG_STANDARD


def _titel(aufmass, positionen: list[dict]) -> str:
	verfahren = [p["herkunft"].get("verfahren") for p in positionen if p.get("herkunft")]
	verfahren = [v for v in dict.fromkeys(verfahren) if v and v != "Sonstiges"]
	teile = [", ".join(verfahren) or _("Remediation"), _objekt_werte(aufmass.get("objekt")).get("titel")]
	return _kurz([" · ".join(t for t in teile if t)]) or aufmass.name


# --------------------------------------------------------------------------- Angebot


def bestehende_angebote(aufmass: str) -> list[dict]:
	return frappe.get_all(
		"Quotation",
		filters={"cv_aufmass": aufmass, "docstatus": ("<", 2)},
		fields=["name", "docstatus", "status"],
		order_by="creation desc",
	)


def _fehlertext(kalkulation: dict) -> str:
	zeilen = [
		f"{f.get('pfad')}: {f.get('meldung')} ({f.get('code')})" for f in kalkulation.get("fehler") or []
	]
	return "<br>".join(frappe.utils.escape_html(z) for z in zeilen) or _("Calculation failed.")


# --------------------------------------------------------------------------- Wände je Position
# Rückmeldung Mike 31.10.: Kunde und Büro müssen die Menge einer Position dem Grundriss zuordnen
# können - dieselben Wand-IDs wie im Grundriss und im Aufmaß (W1 … aus dem Scan, A-D von Hand).

GRUNDLAGE_FEST = "Menge vom Meister bestätigt"
ART_NETTO = "Wandfläche netto"  # Aufmaßzeile mit dem Engine-Beitrag je Wand und Leistung (Kern 3.3)


def _dezimal(wert) -> Decimal:
	try:
		d = Decimal(str(wert))
	except (InvalidOperation, ValueError, TypeError):
		return Decimal(0)
	return d if d.is_finite() else Decimal(0)


def _wand_id(eintrag: dict) -> tuple[str, str]:
	"""(Raum, Wand) aus einem Engine-Beitrag (``id`` = ``Raum/Wand``, ``raum``)."""
	wid = str(eintrag.get("id") or "")
	raum = str(eintrag.get("raum") or "")
	if raum and wid.startswith(raum + "/"):
		return raum, wid[len(raum) + 1 :]
	vorne, _sep, wand = wid.rpartition("/")
	return raum or vorne, wand


def waende_der_position(p: dict, kalkulation: dict, aufmass=None) -> list[tuple[str, list[str], Decimal]]:
	"""Wände, aus denen die Menge einer Position kommt: ``[(Raum, [Wand, …], Menge des Raums)]`` in der
	Reihenfolge des Aufmaßes (Wände im Umlauf des Grundrisses).

	Quelle sind die Engine-Mengen je Wand: bei fester Menge (App) ``aufmass_mengen[item].waende`` aus
	``kalkuliere`` (= ``aufmass.mengen`` der Mobile-API), bei einer Mengenregel die Beiträge der Wände
	(``mengen.beitraege``, Quelle ``wand``). Rückfall (Kern ohne ``aufmass_mengen``): die Zeilen
	„Wandfläche netto“ des Aufmaßes mit diesem Item. Wände mit Menge 0 zählen nicht."""
	code = p.get("item_code")
	eintraege: list[tuple[str, str, Decimal]] = []
	fest = (kalkulation.get("aufmass_mengen") or {}).get(code) or {}
	if p.get("mengen"):
		for b in p["mengen"].get("beitraege") or []:
			if b.get("quelle") == "wand":
				eintraege.append((*_wand_id(b), _dezimal(b.get("menge"))))
	elif fest.get("waende"):
		for w in fest["waende"]:
			eintraege.append((*_wand_id(w), _dezimal(w.get("menge"))))
	elif aufmass is not None:
		for z in aufmass.get("positionen") or []:
			if z.get("art") == ART_NETTO and z.get("item_code") == code and z.get("wand"):
				eintraege.append((z.get("raum") or "", z.get("wand"), _dezimal(z.get("menge"))))
	je_raum: dict[str, tuple[list[str], Decimal]] = {}
	for raum, wand, menge in eintraege:
		if not wand or not menge:
			continue
		waende, summe = je_raum.get(raum, ([], Decimal(0)))
		if wand not in waende:
			waende.append(wand)
		je_raum[raum] = (waende, summe + menge)
	return [(raum, waende, summe) for raum, (waende, summe) in je_raum.items()]


def _zahl_de(wert: Decimal) -> str:
	return f"{wert.quantize(Decimal('0.01')):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def waende_text(waende: list[tuple[str, list[str], Decimal]], einheit: str | None) -> str | None:
	"""``cv_waende``: je Raum eine Zeile „Wohnzimmer: W1, W2, W5 · 23,32 m²“."""
	zeilen = [
		f"{raum or 'ohne Raum'}: {', '.join(w)} · {_zahl_de(summe)}{f' {einheit}' if einheit else ''}"
		for raum, w, summe in waende
	]
	return "\n".join(zeilen) or None


def grundlage_text(waende: list[tuple[str, list[str], Decimal]], fest: bool) -> str | None:
	"""Zeile unter dem Langtext im Druck: „Grundlage: Wohnzimmer W1, W2, W5; Keller 1 A, C“, bei fester
	Menge dazu „Menge vom Meister bestätigt“."""
	teile = []
	if waende:
		teile.append(
			"Grundlage: "
			+ "; ".join(" ".join(t for t in (raum, ", ".join(w)) if t) for raum, w, _m in waende)
		)
	if fest:
		teile.append(GRUNDLAGE_FEST)
	return ". ".join(teile) + "." if teile else None


def _beschreibung(item_code: str, grundlage: str | None) -> str | None:
	"""Langtext des Items plus Absatz „Grundlage …“ (Druckformat „Angebot Corevatis“: unter dem
	Langtext); ohne Grundlage ``None`` - dann setzt ERPNext die Beschreibung des Items wie bisher."""
	if not grundlage:
		return None
	text = frappe.db.get_value("Item", item_code, ["description", "item_name"], as_dict=True) or {}
	basis = (text.get("description") or "").strip()
	if frappe.utils.strip_html_tags(basis).strip() == (text.get("item_name") or "").strip():
		basis = ""  # Langtext wiederholt nur den Kurztext - im Druck steht dann nur die Grundlage
	return f'{basis}<p class="cv-grundlage">{frappe.utils.escape_html(grundlage)}</p>'


def _zeile(p: dict, kalkulation: dict | None = None, aufmass=None) -> dict:
	mengen = p.get("mengen") or {}
	herkunft = p.get("herkunft") or {}
	raeume = [r["raum"] for r in mengen.get("je_raum") or [] if r.get("raum") and flt(r.get("menge"))]
	bauteile = [b.get("bauteil") for b in mengen.get("beitraege") or [] if flt(b.get("menge"))]
	waende = waende_der_position(p, kalkulation or {}, aufmass)
	fest = p.get("menge_quelle") == "fest"
	zeile = {
		"item_code": p["item_code"],
		"qty": flt(p["menge"]),
		"uom": p["einheit"],
		"rate": flt(p["einzelpreis"]),
		"cv_raum": _kurz(raeume or herkunft.get("raeume") or []),
		"cv_bauteil": _kurz(bauteile or herkunft.get("bauteile") or []),
		"cv_verfahren": _kurz([herkunft.get("verfahren")]),
		"cv_aufmass_position": _kurz(herkunft.get("aufmass_positionen") or []),
		"cv_mengen_herkunft": p.get("mengen_herkunft"),
		"cv_einzelkosten": flt(p["kalkulation"]["herstellkosten"]),
		"cv_waende": waende_text(waende, p.get("einheit")),
	}
	beschreibung = _beschreibung(p["item_code"], grundlage_text(waende, fest))
	if beschreibung:
		zeile["description"] = beschreibung
	return zeile


def angebot_fuellen(doc, aufmass, kalkulation: dict, quotation_to: str, party: str, firma: str) -> None:
	"""Quotation (neu oder Entwurf) mit der Kalkulation füllen - ohne Speichern."""
	from erpnext.controllers.accounts_controller import get_taxes_and_charges

	heute = nowdate()
	doc.update(
		{
			"quotation_to": quotation_to,
			"party_name": party,
			"company": firma,
			"order_type": "Sales",
			"transaction_date": heute,
			"valid_till": add_days(heute, GUELTIGKEIT_TAGE),
			"cv_titel": _titel(aufmass, kalkulation["positionen"]),
			"cv_aufmass": aufmass.name,
			"cv_objekt": aufmass.get("objekt"),
			"cv_kalkulationsschema": kalkulation.get("kalkulationsschema"),
			"cv_erzeugt_aus_aufmass": 1,
			"cv_kalkulation_json": json.dumps(kalkulation, ensure_ascii=False, indent=1, default=str),
		}
	)
	if frappe.db.exists("Price List", PREISLISTE):
		doc.selling_price_list = PREISLISTE
	# Befund mvp-test 31.10.: Beim Ersetzen eines Entwurfs am Folgetag blieb der alte Zahlungsplan
	# (Fälligkeit vor dem neuen Buchungsdatum) stehen; ERPNext baut ihn aus den Zahlungsbedingungen neu
	doc.set("payment_schedule", [])
	# Vorbemerkung und Garantiehinweis aus den Textbausteinen (4.3), wie beim Anlegen im Desk
	ensure_textbaustein()
	textbausteine.texte_einsetzen(doc)
	doc.set("items", [])
	for p in kalkulation["positionen"]:
		doc.append("items", _zeile(p, kalkulation, aufmass))
	# Befund mvp-test Nr. 4: per REST/Server angelegte Angebote bekamen keine Steuerzeilen
	vorlage = _steuervorlage(firma)
	doc.taxes_and_charges = vorlage
	doc.set("taxes", [])
	if vorlage:
		for steuer in get_taxes_and_charges("Sales Taxes and Charges Template", vorlage):
			doc.append("taxes", steuer)


def manuelle_positionen(quotation: str) -> list[str]:
	"""Positionen eines Angebots ohne Herkunft aus dem Aufmaß: weder ``cv_aufmass_position`` noch
	``cv_mengen_herkunft`` (beides setzt nur der Generator; Pauschalen eines Verfahrens haben
	evtl. keine Aufmaßzeile, aber immer eine Mengenherkunft)."""
	zeilen = frappe.get_all(
		"Quotation Item",
		filters={"parent": quotation, "parenttype": "Quotation"},
		fields=["idx", "item_code", "item_name", "cv_aufmass_position", "cv_mengen_herkunft"],
		order_by="idx asc",
	)
	return [
		f"{z.idx}. {z.item_code}"
		+ (f" ({z.item_name})" if z.item_name and z.item_name != z.item_code else "")
		for z in zeilen
		if not (z.cv_aufmass_position or "").strip() and not (z.cv_mengen_herkunft or "").strip()
	]


@frappe.whitelist(methods=["POST"])
def erzeuge_angebot(
	aufmass: str,
	schema: str | None = None,
	kunde: str | None = None,
	lead: str | None = None,
	neu_erzeugen=0,
	erschwernis_prozent=None,
	bestaetigen=0,
) -> dict:
	"""Angebot aus einem Aufmaß erzeugen (Rollen Büro, Meister).

	``neu_erzeugen=1`` ersetzt alle Positionen des neuesten Entwurfs. Hat der Entwurf von Hand
	ergänzte Positionen (ohne ``cv_aufmass_position``, z. B. Regiestunden, Pauschalen), wird
	nichts geändert, solange ``bestaetigen`` nicht 1 ist - die Antwort nennt die Positionen
	(``bestaetigung_noetig``, ``manuelle_positionen``; Review Epic 4 G-2).

	Rückgabe: ``angebot`` (Name), ``neu`` (angelegt/ersetzt oder nur gemeldet), ``ersetzt``,
	``positionen``, ``netto``, ``steuer``, ``brutto``, ``warnungen``.
	"""
	frappe.only_for(ROLLEN)
	if not kern_vorhanden():
		frappe.throw(_("The Corevatis core app is not installed (Aufmaß missing)."), AngebotFehler)
	am = _aufmass_lesen(aufmass)

	bestehend = bestehende_angebote(am.name)
	entwurf = None
	if bestehend:
		if not cint(neu_erzeugen):
			return {
				"angebot": bestehend[0].name,
				"neu": False,
				"ersetzt": False,
				"meldung": _("Quotation {0} already exists for measurement {1}.").format(
					bestehend[0].name, am.name
				),
			}
		entwurf = next((b.name for b in bestehend if b.docstatus == 0), None)
		if not entwurf:
			frappe.throw(
				_("Quotation {0} is already submitted; amend it instead of regenerating.").format(
					bestehend[0].name
				),
				AngebotFehler,
			)
		manuell = manuelle_positionen(entwurf)
		if manuell and not cint(bestaetigen):
			return {
				"angebot": entwurf,
				"neu": False,
				"ersetzt": False,
				"bestaetigung_noetig": True,
				"manuelle_positionen": manuell,
				"meldung": _(
					"Draft {0} contains manually added items that will be lost when it is replaced: {1}"
				).format(entwurf, "; ".join(manuell)),
			}

	quotation_to, party = _partei(am, kunde, lead)
	# Firma mit Vorrang; hat sie für dieses Aufmaß keine Leistungen (z. B. alter Lead einer Firma
	# ohne Katalog/Stücklisten), die nächste - mit Hinweis
	firmen = _firmen(am, quotation_to, party)
	for firma in firmen:
		leistungen, warnungen = leistungen_aus_aufmass(am, firma)
		if leistungen:
			break
	if leistungen and firma != firmen[0]:
		warnungen.append(
			_("Company {0} has no services for measurement {1}; the quotation uses company {2}.").format(
				firmen[0], am.name, firma
			)
		)
	if not leistungen:
		frappe.throw(
			_("No services found for measurement {0}.").format(am.name)
			+ ("<br>" + "<br>".join(frappe.utils.escape_html(w) for w in warnungen) if warnungen else ""),
			AngebotFehler,
		)
	kalkulation = _kern_kalkulation()(
		aufmass=am.name, leistungen=leistungen, schema=schema or None, erschwernis_prozent=erschwernis_prozent
	)
	if not kalkulation.get("ok"):
		frappe.throw(_fehlertext(kalkulation), AngebotFehler, title=_("Calculation failed"))

	doc = frappe.get_doc("Quotation", entwurf) if entwurf else frappe.new_doc("Quotation")
	angebot_fuellen(doc, am, kalkulation, quotation_to, party, firma)
	if entwurf:
		doc.save()
	else:
		doc.insert()
	warnungen += [w.get("meldung") for w in kalkulation.get("warnungen") or []]
	warnungen += [h.get("meldung") for h in _hinweise(am, kalkulation)]
	if not doc.taxes:
		warnungen.append(_("No sales tax template {0} for company {1}.").format(STEUERVORLAGE, firma))
	return {
		"angebot": doc.name,
		"neu": True,
		"ersetzt": bool(entwurf),
		"positionen": len(doc.items),
		"netto": doc.net_total,
		"steuer": doc.total_taxes_and_charges,
		"brutto": doc.grand_total,
		"warnungen": [w for w in warnungen if w],
	}


def _hinweise(aufmass, kalkulation: dict) -> list[dict]:
	"""Hinweise der Abbildung ohne „nicht verwendet“ für Zeilen mit fester Menge (die nutzt keine
	Mengenregel, sondern die Leistung selbst)."""
	fest = {
		f"aufmass.positionen[{cint(z.get('idx')) - 1}]"
		for z in aufmass.get("positionen") or []
		if cint(z.get("menge_fest"))
	}
	return [
		h
		for h in kalkulation.get("hinweise") or []
		if not (h.get("code") == "nicht_verwendet" and h.get("pfad") in fest)
	]


# --------------------------------------------------------------------------- Dashboards


def objekt_dashboard(data):
	"""Objekt (Kern): Angebote des Objekts (``Quotation.cv_objekt``) im Bereich Verbindungen."""
	return _dashboard(data, "cv_objekt")


def aufmass_dashboard(data):
	"""Aufmaß (Kern): Angebote aus dem Aufmaß (``Quotation.cv_aufmass``)."""
	return _dashboard(data, "cv_aufmass")


def _dashboard(data, feld: str):
	data = data or {}
	# Ohne ``fieldname`` lädt der Desk keine Zähler (Frappe ``form/dashboard.js``
	# ``set_open_count``); das Aufmaß hat im Kern kein eigenes Dashboard. Für Quotation
	# gilt ``non_standard_fieldnames``, ``fieldname`` ist nur der Rückfall.
	data.setdefault("fieldname", feld)
	data.setdefault("non_standard_fieldnames", {})["Quotation"] = feld
	data.setdefault("transactions", []).append({"label": _("Offer"), "items": ["Quotation"]})
	return data


__all__ = ["KERN_KALKULATION", "MODULE", "erzeuge_angebot", "leistungen_aus_aufmass"]
