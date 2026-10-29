"""Aufgabe 4.4: Angebotsannahme - Quotation → Customer (aus dem Lead) → Sales Order → Project.

Ablauf ``angebot_annehmen`` (Button „Angebot annehmen“ am eingereichten Angebot,
``public/js/quotation.js``; REST ``POST /api/method/corevatis_erpnext.angebot.annahme.angebot_annehmen``):

1. **Kunde:** Ist das Angebot an einen Lead gerichtet, entsteht mit ERPNext-Standard
   ``erpnext.crm.doctype.lead.lead._make_customer`` ein Customer (``lead_name`` = Lead).
   Vorher bekommt der Lead eine Anschrift (Address) aus dem Objekt bzw. der Objektadresse,
   falls er noch keine hat (Befund mvp-test 14), und einen Kontakt, falls keiner verknüpft
   ist. ERPNext verknüpft beide beim Anlegen mit dem Kunden und stellt die Angebote des Leads
   auf den Kunden um. Gibt es schon einen Kunden mit diesem Lead, wird er benutzt.
2. **Auftrag:** ERPNext ``_make_sales_order`` (Mapping wie „Erstellen → Auftrag“) ohne
   Eventual- und Alternativpositionen (``cv_eventualposition``, ``cv_alternativposition`` aus
   4.1 sowie ERPNext ``is_alternative``), Nummernkreis ``AB-.YYYY.-`` (Site-Template), Steuer-
   vorlage und -zeilen aus dem Angebot, ``cv_*``-Felder von Angebot und Positionen gespiegelt
   (``angebot.annahme_felder``). Auftragsdatum, Liefertermin (= geplantes Ende) und Einreichen sind
   Parameter.
3. **Projekt:** ERPNext Project „<Objektadresse> - <Verfahren>“ (Gedankenstrich ``TRENNER``), Kunde, Auftrag, Status Open,
   Projekttyp „Sanierung“ (wird angelegt, falls er fehlt), erwarteter Start = Auftragsdatum,
   erwartetes Ende = Liefertermin, ``cv_objekt``/``cv_aufmass``/``cv_angebot``/``cv_lead``.
4. **Lead:** ``cv_projekt`` gesetzt, Workflow-Übergang „Als gewonnen markieren“ (aus
   „Besichtigt“ vorher „Angebot erstellt“). Ist der Übergang nicht möglich (Rolle, Zustand),
   bleibt der Auftrag bestehen und die Antwort enthält eine Warnung.

Idempotent: Ein zweiter Aufruf legt nichts doppelt an, sondern meldet Kunde, Auftrag und
Projekt (``bereits_angenommen``); fehlt nur das Projekt, wird es ergänzt, und ein Auftrags-
entwurf wird auf Wunsch eingereicht. Parallele Aufrufe serialisiert eine Zeilensperre auf dem
Angebot.

``angebot_ablehnen``: Angebot „Lost“ über ERPNext ``declare_enquiry_lost`` (Grund als
„Quotation Lost Reason“ und ``order_lost_reason``), Lead „Verloren“ mit ``cv_verlustgrund`` -
wie ``anfrage.lead.als_verloren_markieren`` in einem Speichervorgang. Hat der Lead noch ein
anderes offenes Angebot, bleibt er offen (Warnung).

Hooks (``hooks.doc_events``): Sales Order ``on_submit`` legt das Projekt auch für Aufträge an,
die über den ERPNext-Weg „Erstellen → Auftrag“ entstanden sind, setzt den Lead auf „Gewonnen“
und stellt ein Angebot, dessen offene Reste nur Eventual-/Alternativpositionen sind, von
„Partially Ordered“ auf „Ordered“. Quotation ``on_update_after_submit``: Wird ein Angebot über
den ERPNext-Dialog „Als verloren festlegen“ verloren gegeben, folgt der Lead.

Rechte: Rollen Büro, Meister (oder System Manager/Administrator) und Leserecht auf das Angebot
(Ablehnen: Schreibrecht). Kunde, Auftrag und Projekt legt die Methode mit
``ignore_permissions`` an - sie ist der kontrollierte Weg, ein Meister braucht dafür kein
Recht „Auftrag anlegen“. Den Lead schaltet der Workflow mit den Rechten des Benutzers.
"""

import re

import frappe
from frappe import _
from frappe.model import no_value_fields
from frappe.utils import add_days, cint, flt, getdate, today

from corevatis_erpnext.anfrage.custom_fields import STATUS_VERLOREN, VERLUSTGRUENDE

ROLLEN = ("Büro", "Meister", "System Manager")
PROJEKTTYP = "Sanierung"
LIEFERFRIST_TAGE = 14
SERIE_AUFTRAG = "AB-.YYYY.-"
FELD_EVENTUAL = "cv_eventualposition"
FELD_ALTERNATIV = "cv_alternativposition"
STATUS_GEWONNEN = "Gewonnen"
PROJEKT_RUECKFALL = "Sanierung"
# Projektname „<Objektadresse> <Gedankenstrich> <Verfahren>“ (Escape wegen ruff RUF001)
TRENNER = " \u2013 "
PROJEKTNAME_LAENGE = 140
STATUS_OFFEN = ("Open", "Replied", "Partially Ordered")


# --- Rechte ---------------------------------------------------------------------------------


def darf_annehmen(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(set(ROLLEN) & set(frappe.get_roles(user)))


def pruefe_rolle() -> None:
	# frappe.only_for ist in Tests abgeschaltet - eigene Prüfung (wie crm.termin)
	if not darf_annehmen():
		frappe.throw(
			_("Angebote annehmen und ablehnen dürfen nur die Rollen Büro und Meister."),
			frappe.PermissionError,
		)


# --- Whitelist ------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def angebot_annehmen(
	quotation: str,
	auftragsdatum: str | None = None,
	lieferdatum: str | None = None,
	einreichen: int | str | bool | None = 1,
) -> dict:
	"""Angebot annehmen: Kunde, Auftrag (AB-) und Projekt anlegen, Lead „Gewonnen“.

	:param quotation: Name des eingereichten Angebots
	:param auftragsdatum: Datum des Auftrags (Standard heute), zugleich erwarteter Projektstart
	:param lieferdatum: Liefertermin = erwartetes Projektende (Standard Auftragsdatum + 14 Tage)
	:param einreichen: 1 = Auftrag einreichen (Standard), 0 = Auftrag als Entwurf lassen
	"""
	pruefe_rolle()
	frappe.has_permission("Quotation", "read", quotation, throw=True)
	# Zeilensperre: ein Doppelklick bzw. paralleler REST-Aufruf wartet und findet dann den Auftrag
	frappe.db.get_value("Quotation", quotation, "name", for_update=True)
	angebot = frappe.get_doc("Quotation", quotation)
	if angebot.docstatus != 1:
		frappe.throw(_("Nur ein eingereichtes Angebot kann angenommen werden."))
	if angebot.status == "Lost":
		frappe.throw(_("Das Angebot {0} ist als verloren markiert.").format(angebot.name))

	start = getdate(auftragsdatum) if auftragsdatum else getdate(today())
	ende = getdate(lieferdatum) if lieferdatum else getdate(add_days(start, LIEFERFRIST_TAGE))
	if ende < start:
		frappe.throw(_("Der Liefertermin darf nicht vor dem Auftragsdatum liegen."))

	warnungen: list[str] = []
	lead = lead_zum_angebot(angebot)
	auftrag = bestehender_auftrag(angebot.name)
	bereits = bool(auftrag)

	kunde, kunde_neu = kunde_sicherstellen(angebot, lead, warnungen)
	if not auftrag:
		pruefe_gueltigkeit(angebot)
		auftrag = auftrag_anlegen(angebot.name, start, ende)
	projekt, projekt_neu = projekt_sicherstellen(auftrag, lead, start, ende)

	lead_status = None
	if lead:
		lead_mit_projekt(lead, projekt)
		lead_status, warnung = lead_gewonnen(lead)
		if warnung:
			warnungen.append(warnung)

	auftrag.reload()
	eingereicht = False
	if cint(einreichen) and auftrag.docstatus == 0:
		auftrag.flags.ignore_permissions = True
		auftrag.submit()
		eingereicht = True

	return {
		"angebot": angebot.name,
		"bereits_angenommen": bereits and not projekt_neu and not eingereicht,
		"kunde": kunde,
		"kunde_neu": kunde_neu,
		"auftrag": auftrag.name,
		"auftrag_neu": not bereits,
		"auftrag_docstatus": auftrag.docstatus,
		"projekt": projekt,
		"projekt_neu": projekt_neu,
		"lead": lead,
		"lead_status": lead_status,
		"warnungen": warnungen,
	}


@frappe.whitelist(methods=["POST"])
def angebot_ablehnen(quotation: str, grund: str, details: str | None = None) -> dict:
	"""Angebot „Lost“ mit Grund, Lead „Verloren“ mit Verlustgrund.

	:param grund: einer der Verlustgründe des Leads (``cv_verlustgrund``)
	:param details: Freitext für ``order_lost_reason`` (Standard: der Grund)
	"""
	pruefe_rolle()
	frappe.has_permission("Quotation", "write", quotation, throw=True)
	if not grund or grund not in verlustgruende():
		frappe.throw(_("Bitte einen Ablehnungsgrund auswählen."), frappe.MandatoryError)

	frappe.db.get_value("Quotation", quotation, "name", for_update=True)
	angebot = frappe.get_doc("Quotation", quotation)
	if angebot.docstatus != 1:
		frappe.throw(_("Nur ein eingereichtes Angebot kann abgelehnt werden."))
	auftrag = bestehender_auftrag(angebot.name)
	if auftrag:
		frappe.throw(_("Zum Angebot {0} gibt es bereits den Auftrag {1}.").format(angebot.name, auftrag.name))

	bereits = angebot.status == "Lost"
	if not bereits:
		ensure_verlustgruende()
		angebot.flags.ignore_permissions = True
		frappe.flags.cv_angebot_ablehnen = angebot.name
		try:
			angebot.declare_enquiry_lost(
				[{"lost_reason": grund}], [], detailed_reason=(details or "").strip() or grund
			)
		finally:
			frappe.flags.cv_angebot_ablehnen = None

	lead = lead_zum_angebot(angebot)
	lead_status, warnung = lead_verloren(lead, grund, ausser=angebot.name)
	return {
		"angebot": angebot.name,
		"status": frappe.db.get_value("Quotation", angebot.name, "status"),
		"bereits_abgelehnt": bereits,
		"lead": lead,
		"lead_status": lead_status,
		"warnungen": [warnung] if warnung else [],
	}


@frappe.whitelist()
def verlustgruende() -> list[str]:
	"""Auswahl für den Ablehnungsdialog (= Optionen von ``Lead.cv_verlustgrund``)."""
	optionen = [
		o.strip()
		for o in (frappe.get_meta("Lead").get_options("cv_verlustgrund") or "").split("\n")
		if o.strip()
	]
	return optionen or list(VERLUSTGRUENDE)


@frappe.whitelist()
def annahme_status(quotation: str) -> dict:
	"""Für die Buttons am Angebot: vorhandener Auftrag und Projekt."""
	pruefe_rolle()
	frappe.has_permission("Quotation", "read", quotation, throw=True)
	auftrag = bestehender_auftrag(quotation)
	projekt = None
	if auftrag:
		projekt = auftrag.get("project") or frappe.db.get_value("Project", {"sales_order": auftrag.name})
	return {
		"auftrag": auftrag.name if auftrag else None,
		"auftrag_docstatus": auftrag.docstatus if auftrag else None,
		"projekt": projekt,
	}


# --- Kunde ----------------------------------------------------------------------------------


def lead_zum_angebot(angebot) -> str | None:
	"""Lead des Angebots: direkt (an Lead) oder über ``Customer.lead_name`` (nach der Umwandlung)."""
	if angebot.quotation_to == "Lead":
		return angebot.party_name or None
	if angebot.quotation_to == "Customer" and angebot.party_name:
		return frappe.db.get_value("Customer", angebot.party_name, "lead_name") or None
	return None


def kunde_sicherstellen(angebot, lead: str | None, warnungen: list[str]) -> tuple[str, bool]:
	if angebot.quotation_to == "Customer":
		return angebot.party_name, False
	if angebot.quotation_to != "Lead" or not lead:
		frappe.throw(
			_("Angebote an „{0}“ werden nicht unterstützt - bitte an einen Kunden oder Lead richten.").format(
				angebot.quotation_to
			)
		)

	vorhanden = frappe.db.get_value("Customer", {"lead_name": lead}, "name")
	if vorhanden:
		_angebot_auf_kunde(angebot, vorhanden)
		return vorhanden, False

	_anschrift_und_kontakt(lead, angebot, warnungen)

	from erpnext.crm.doctype.lead.lead import _make_customer

	kunde = _make_customer(lead, ignore_permissions=True)
	kunde.flags.ignore_permissions = True
	kunde.customer_group = _kundengruppe(kunde.get("customer_group"))
	if not kunde.get("territory"):
		kunde.territory = frappe.db.get_single_value("Selling Settings", "territory") or None
	# Customer.on_update markiert Hauptadresse/-kontakt per frappe.set_value - mit den Rechten des
	# Benutzers (ein Meister darf Adressen nur lesen). Deshalb erst nach dem Anlegen setzen.
	adresse, kontakt = kunde.get("customer_primary_address"), kunde.get("customer_primary_contact")
	kunde.customer_primary_address = kunde.customer_primary_contact = None
	try:
		kunde.insert()
	except frappe.MandatoryError as e:
		frappe.throw(
			_("Der Kunde konnte nicht aus der Anfrage {0} angelegt werden: {1}").format(lead, e),
			title=_("Kunde fehlt"),
		)
	_hauptadresse_und_kontakt(kunde, adresse, kontakt)
	_angebot_auf_kunde(angebot, kunde.name)
	return kunde.name, True


def _kundengruppe(vorgabe: str | None) -> str | None:
	"""Erste Kundengruppe ohne Untergruppen (ERPNext lehnt Gruppen-Knoten ab)."""
	for name in (
		vorgabe,
		frappe.db.get_single_value("Selling Settings", "customer_group"),
		frappe.db.get_default("Customer Group"),
	):
		if name and frappe.db.get_value("Customer Group", name, "is_group") == 0:
			return name
	return frappe.db.get_value("Customer Group", {"is_group": 0}, "name", order_by="lft asc") or vorgabe


def _hauptadresse_und_kontakt(kunde, adresse: str | None, kontakt: str | None) -> None:
	from frappe.contacts.doctype.address.address import get_address_display

	werte = {}
	if adresse:
		werte["customer_primary_address"] = adresse
		werte["primary_address"] = get_address_display(adresse)
	if kontakt:
		k = frappe.db.get_value("Contact", kontakt, ["mobile_no", "email_id"], as_dict=True) or {}
		werte.update(
			customer_primary_contact=kontakt, mobile_no=k.get("mobile_no"), email_id=k.get("email_id")
		)
		frappe.db.set_value("Contact", kontakt, "is_primary_contact", 1, update_modified=False)
	if werte:
		kunde.db_set(werte, update_modified=False)


def _angebot_auf_kunde(angebot, kunde: str) -> None:
	"""ERPNext stellt Angebote des Leads beim Anlegen des Kunden um (``Customer.update_lead_status``),
	aber nur mit Schreibrecht des Benutzers - hier für *dieses* Angebot ausdrücklich."""
	if frappe.db.get_value("Quotation", angebot.name, "quotation_to") == "Lead":
		frappe.db.set_value(
			"Quotation",
			angebot.name,
			{"quotation_to": "Customer", "party_name": kunde},
			update_modified=False,
		)
	angebot.quotation_to, angebot.party_name = "Customer", kunde


def _verknuepft(doctype: str, link_doctype: str, link_name: str) -> list[str]:
	return frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": doctype, "link_doctype": link_doctype, "link_name": link_name},
		pluck="parent",
		order_by="creation asc",
	)


def _anschrift_und_kontakt(lead_name: str, angebot, warnungen: list[str]) -> None:
	"""Anschrift und Kontakt am Lead, damit ERPNext sie an den neuen Kunden hängt."""
	lead = frappe.get_doc("Lead", lead_name)
	if not _verknuepft("Address", "Lead", lead.name):
		teile = objekt_anschrift(angebot, lead)
		if teile.get("strasse") and teile.get("ort"):
			land = (
				lead.get("country")
				or frappe.db.get_value("Company", angebot.company, "country")
				or frappe.db.get_default("country")
				or "Germany"
			)
			frappe.get_doc(
				{
					"doctype": "Address",
					"address_title": lead.get("lead_name") or lead.name,
					"address_type": "Billing",
					"address_line1": teile["strasse"],
					"pincode": teile.get("plz"),
					"city": teile["ort"],
					"country": land,
					"is_primary_address": 1,
					"links": [{"link_doctype": "Lead", "link_name": lead.name}],
				}
			).insert(ignore_permissions=True)
		else:
			warnungen.append(
				_(
					"Keine Anschrift für den Kunden gefunden (Objektadresse fehlt) - bitte am Kunden nachtragen."
				)
			)
	if not _verknuepft("Contact", "Lead", lead.name) and (
		lead.get("email_id") or lead.get("mobile_no") or lead.get("phone")
	):
		kontakt = lead.create_contact()
		kontakt.append("links", {"link_doctype": "Lead", "link_name": lead.name})
		kontakt.save(ignore_permissions=True)


# --- Objektadresse --------------------------------------------------------------------------

_PLZ_ORT = re.compile(r"(?:^|[\s,;])(\d{5})\s+([^,;\n]+)")


def zerlege_anschrift(text: str | None) -> dict:
	"""„Kellerweg 7, 48143 Münster“ bzw. mehrzeilig → Straße, PLZ, Ort (leer, wenn keine PLZ)."""
	text = (text or "").strip()
	treffer = list(_PLZ_ORT.finditer(text))
	if not treffer:
		return {}
	t = treffer[-1]
	davor = [teil.strip() for teil in re.split(r"[\n,;]+", text[: t.start(1)]) if teil.strip()]
	return {"strasse": davor[-1] if davor else "", "plz": t.group(1), "ort": t.group(2).strip()}


def _objekt_name(angebot, lead_doc=None) -> str | None:
	name = angebot.get("cv_objekt") if angebot else None
	if not name and lead_doc is not None:
		name = lead_doc.get("cv_objekt")
	return str(name) if name else None


def objekt_anschrift(angebot, lead_doc=None) -> dict:
	"""Straße/PLZ/Ort: Objekt (Kern-App ``corevatis``, nur per Doctype-Name), sonst die Objekt-
	adresse des Leads (1.1, Address nur, wenn mit dem Lead verknüpft - ``crm.termin.objekt_adresse``)."""
	name = _objekt_name(angebot, lead_doc)
	if name and frappe.db.exists("DocType", "Objekt") and frappe.db.exists("Objekt", name):
		werte = frappe.db.get_value("Objekt", name, ["strasse", "plz", "ort"], as_dict=True) or {}
		if werte.get("strasse") or werte.get("ort"):
			return {
				"strasse": werte.get("strasse") or "",
				"plz": werte.get("plz") or "",
				"ort": werte.get("ort") or "",
			}
	if lead_doc is not None:
		from corevatis_erpnext.crm.termin import objekt_adresse

		text = objekt_adresse(lead_doc)
		teile = zerlege_anschrift(text)
		if teile:
			return teile
		if text:
			return {"text": " ".join(text.split())}
	return {}


def objekt_text(angebot, lead: str | None, auftrag=None) -> str:
	"""Objektadresse einzeilig für den Projektnamen."""
	lead_doc = frappe.get_doc("Lead", lead) if lead and frappe.db.exists("Lead", lead) else None
	teile = objekt_anschrift(angebot, lead_doc)
	if teile.get("strasse") or teile.get("ort"):
		ort = " ".join(filter(None, [teile.get("plz"), teile.get("ort")]))
		return ", ".join(filter(None, [teile.get("strasse"), ort]))
	if teile.get("text"):
		return teile["text"].replace("\n", ", ")
	adresse = (auftrag.get("customer_address") if auftrag else None) or angebot.get("customer_address")
	if adresse:
		a = frappe.db.get_value("Address", adresse, ["address_line1", "pincode", "city"], as_dict=True) or {}
		text = ", ".join(
			filter(None, [a.get("address_line1"), " ".join(filter(None, [a.get("pincode"), a.get("city")]))])
		)
		if text:
			return text
	return (auftrag.get("customer_name") if auftrag else None) or angebot.get("customer_name") or angebot.name


# --- Auftrag --------------------------------------------------------------------------------


def ist_zusatzposition(zeile) -> bool:
	"""Eventual- oder Alternativposition (4.1) bzw. ERPNext-Alternative - nicht in den Auftrag."""
	return bool(
		cint(zeile.get(FELD_EVENTUAL))
		or cint(zeile.get(FELD_ALTERNATIV))
		or cint(zeile.get("is_alternative"))
	)


def bestehender_auftrag(quotation: str):
	"""Nicht stornierter Auftrag zu diesem Angebot (Entwurf oder eingereicht), sonst ``None``."""
	namen = frappe.get_all(
		"Sales Order Item",
		filters={"prevdoc_docname": quotation, "docstatus": ["<", 2], "parenttype": "Sales Order"},
		pluck="parent",
		order_by="creation asc",
		limit=1,
	)
	return frappe.get_doc("Sales Order", namen[0]) if namen else None


def pruefe_gueltigkeit(angebot) -> None:
	"""Wie ERPNext ``make_sales_order``: abgelaufenes Angebot nur mit Einstellung in Selling Settings."""
	if cint(
		frappe.db.get_single_value("Selling Settings", "allow_sales_order_creation_for_expired_quotation")
	):
		return
	if angebot.valid_till and (
		getdate(angebot.valid_till) < getdate(angebot.transaction_date)
		or getdate(angebot.valid_till) < getdate(today())
	):
		frappe.throw(
			_(
				"Das Angebot {0} war nur bis {1} gültig. Bitte „Gültig bis“ verlängern (Angebot aktualisieren) oder ein neues Angebot erstellen."
			).format(angebot.name, frappe.format(angebot.valid_till, {"fieldtype": "Date"}))
		)


def auftrag_anlegen(quotation: str, start, ende):
	from erpnext.selling.doctype.quotation.quotation import _make_sales_order

	angebot = frappe.get_doc("Quotation", quotation)
	uebernehmen = [z.name for z in angebot.items if not ist_zusatzposition(z)]
	if not uebernehmen:
		frappe.throw(
			_("Das Angebot enthält nur Eventual- oder Alternativpositionen - nichts zu beauftragen.")
		)

	auftrag = _make_sales_order(
		angebot.name, ignore_permissions=True, args={"filtered_children": uebernehmen}
	)
	auftrag.transaction_date = start
	auftrag.delivery_date = ende
	for zeile in auftrag.items:
		zeile.delivery_date = ende
	if SERIE_AUFTRAG in (auftrag.meta.get_options("naming_series") or "").split("\n"):
		auftrag.naming_series = SERIE_AUFTRAG
	cv_felder_kopieren(angebot, auftrag)
	auftrag.flags.ignore_permissions = True
	auftrag.insert()
	return auftrag


def _cv_feldnamen(meta_ziel, meta_quelle) -> list[str]:
	return [
		df.fieldname
		for df in meta_ziel.fields
		if df.fieldname.startswith("cv_")
		and df.fieldtype not in no_value_fields
		and meta_quelle.has_field(df.fieldname)
	]


def cv_felder_kopieren(angebot, auftrag) -> None:
	"""``cv_*``-Werte vom Angebot und den Angebotspositionen in Auftrag und Auftragspositionen
	(auch Felder mit ``no_copy``, die ``get_mapped_doc`` auslässt)."""
	for feld in _cv_feldnamen(auftrag.meta, angebot.meta):
		if angebot.get(feld) not in (None, ""):
			auftrag.set(feld, angebot.get(feld))
	positionen = {z.name: z for z in angebot.items}
	felder = _cv_feldnamen(frappe.get_meta("Sales Order Item"), frappe.get_meta("Quotation Item"))
	for zeile in auftrag.items:
		quelle = positionen.get(zeile.get("quotation_item"))
		if not quelle:
			continue
		for feld in felder:
			if quelle.get(feld) not in (None, ""):
				zeile.set(feld, quelle.get(feld))


# --- Projekt --------------------------------------------------------------------------------


def ensure_projekttyp() -> None:
	if not frappe.db.exists("Project Type", PROJEKTTYP):
		frappe.get_doc(
			{
				"doctype": "Project Type",
				"project_type": PROJEKTTYP,
				"description": "Bauwerksabdichtung/Sanierung aus einem angenommenen Angebot (Corevatis)",
			}
		).insert(ignore_permissions=True)


def verfahren_text(auftrag) -> str:
	"""Verfahren der Positionen in Positionsreihenfolge, sonst „Sanierung“.

	Je Position ``cv_verfahren`` der Angebotsposition (4.1, in den Auftrag gespiegelt), sonst
	``Item.cv_verfahren`` (2.2).
	"""
	codes = [z.item_code for z in auftrag.items if z.item_code]
	je_artikel = {}
	if codes and frappe.db.has_column("Item", "cv_verfahren"):
		je_artikel = dict(
			frappe.get_all(
				"Item", filters={"name": ["in", codes]}, fields=["name", "cv_verfahren"], as_list=True
			)
		)
	werte = [(z.get("cv_verfahren") or je_artikel.get(z.item_code) or "").strip() for z in auftrag.items]
	werte = [w for w in dict.fromkeys(werte) if w and w != "Sonstiges"]
	return " / ".join(werte) or PROJEKT_RUECKFALL


def projekt_name(basis: str, auftrag: str) -> str:
	"""``project_name`` ist in ERPNext eindeutig: bei Gleichstand „(<Auftrag>)“ anhängen."""
	basis = " ".join(basis.split())
	name = basis[:PROJEKTNAME_LAENGE]
	if not frappe.db.exists("Project", {"project_name": name}):
		return name
	zusatz = f" ({auftrag})"
	return basis[: PROJEKTNAME_LAENGE - len(zusatz)] + zusatz


def projekt_sicherstellen(auftrag, lead: str | None, start=None, ende=None) -> tuple[str, bool]:
	"""Projekt zum Auftrag (vorhanden: ``Sales Order.project`` bzw. ``Project.sales_order``)."""
	vorhanden = auftrag.get("project") or frappe.db.get_value("Project", {"sales_order": auftrag.name})
	if vorhanden:
		return vorhanden, False

	angebote = [z.prevdoc_docname for z in auftrag.items if z.get("prevdoc_docname")]
	angebot = frappe.get_doc("Quotation", angebote[0]) if angebote else None
	ensure_projekttyp()
	werte = {
		"doctype": "Project",
		"project_name": projekt_name(
			(objekt_text(angebot, lead, auftrag) if angebot else auftrag.customer_name)
			+ TRENNER
			+ verfahren_text(auftrag),
			auftrag.name,
		),
		"status": "Open",
		"project_type": PROJEKTTYP,
		"customer": auftrag.customer,
		"company": auftrag.company,
		"sales_order": auftrag.name,
		"expected_start_date": start or auftrag.transaction_date,
		"expected_end_date": ende or auftrag.delivery_date,
		"cv_angebot": angebot.name if angebot else None,
		"cv_lead": lead,
	}
	for feld in ("cv_objekt", "cv_aufmass"):
		wert = (auftrag.get(feld) or (angebot.get(feld) if angebot else None)) or None
		if not wert and feld == "cv_objekt" and lead:
			wert = frappe.db.get_value("Lead", lead, feld) if frappe.db.has_column("Lead", feld) else None
		werte[feld] = str(wert) if wert else None
	projekt = frappe.get_doc(werte)
	projekt.flags.ignore_permissions = True
	projekt.insert()
	return projekt.name, True


# --- Lead -----------------------------------------------------------------------------------


def lead_mit_projekt(lead: str, projekt: str) -> None:
	if frappe.db.has_column("Lead", "cv_projekt") and not frappe.db.get_value("Lead", lead, "cv_projekt"):
		frappe.db.set_value("Lead", lead, "cv_projekt", projekt, update_modified=False)


def _workflow_schritte(lead: str, aktionen: list[str], savepoint: str) -> str | None:
	"""Workflow-Aktionen nacheinander; Fehler → Rücksprung zum Savepoint, Rückgabe Fehlertext."""
	from frappe.model.workflow import apply_workflow

	from corevatis_erpnext.crm.termin import _html_zu_text

	meldungen = len(frappe.local.message_log)
	frappe.db.savepoint(savepoint)
	try:
		for aktion in aktionen:
			apply_workflow(frappe.get_doc("Lead", lead), aktion)
	except (frappe.ValidationError, frappe.PermissionError) as e:
		frappe.db.rollback(save_point=savepoint)
		del frappe.local.message_log[meldungen:]
		return _html_zu_text(str(e)).strip() or e.__class__.__name__
	return None


def lead_gewonnen(lead: str) -> tuple[str | None, str | None]:
	"""Lead über den Workflow „Lead Anfrage“ auf „Gewonnen“ (1.4). Rückgabe ``(zustand, warnung)``."""
	from frappe.model.workflow import get_workflow_name

	from corevatis_erpnext.anfrage.workflow import ACTION_ANGEBOT, ACTION_GEWONNEN

	if not get_workflow_name("Lead") or not frappe.db.has_column("Lead", "cv_status"):
		return None, None
	zustand = frappe.db.get_value("Lead", lead, "cv_status")
	if zustand == STATUS_GEWONNEN:
		return zustand, None
	if zustand == "Angebot":
		aktionen = [ACTION_GEWONNEN]
	elif zustand == "Besichtigt":
		aktionen = [ACTION_ANGEBOT, ACTION_GEWONNEN]
	else:
		return zustand, _(
			"Anfrage {0} steht auf „{1}“ und wurde nicht auf „Gewonnen“ gesetzt - bitte im Lead weiterschalten."
		).format(lead, zustand)
	fehler = _workflow_schritte(lead, aktionen, "cv_lead_gewonnen")
	if fehler:
		return zustand, _("Anfrage {0} nicht auf „Gewonnen“ gesetzt: {1}").format(lead, fehler)
	return STATUS_GEWONNEN, None


def _andere_offene_angebote(lead: str, ausser: str | None) -> list[str]:
	kunden = frappe.get_all("Customer", filters={"lead_name": lead}, pluck="name")
	parteien = [["Quotation", "quotation_to", "=", "Lead"], ["Quotation", "party_name", "=", lead]]
	namen = frappe.get_all(
		"Quotation",
		filters=[*parteien, ["docstatus", "=", 1], ["status", "in", STATUS_OFFEN]],
		pluck="name",
	)
	if kunden:
		namen += frappe.get_all(
			"Quotation",
			filters={
				"quotation_to": "Customer",
				"party_name": ["in", kunden],
				"docstatus": 1,
				"status": ["in", STATUS_OFFEN],
			},
			pluck="name",
		)
	return sorted(set(namen) - {ausser})


def lead_verloren(lead: str | None, grund: str, ausser: str | None = None) -> tuple[str | None, str | None]:
	"""Lead „Verloren“ mit Verlustgrund (Workflow-Aktion, Grund im selben Speichern wie 1.4)."""
	from frappe.model.workflow import get_workflow_name

	from corevatis_erpnext.anfrage.workflow import ACTION_VERLOREN

	if not lead or not get_workflow_name("Lead") or not frappe.db.has_column("Lead", "cv_status"):
		return None, None
	zustand = frappe.db.get_value("Lead", lead, "cv_status")
	if zustand == STATUS_VERLOREN:
		return zustand, None
	if zustand == STATUS_GEWONNEN:
		return zustand, _("Anfrage {0} ist bereits „Gewonnen“ und bleibt es.").format(lead)
	andere = _andere_offene_angebote(lead, ausser)
	if andere:
		return zustand, _("Anfrage {0} bleibt offen: weiteres offenes Angebot {1}.").format(
			lead, ", ".join(andere)
		)
	if grund not in VERLUSTGRUENDE:
		grund = "Sonstiges"
	frappe.flags.cv_verlustgrund = {"lead": lead, "grund": grund}
	try:
		fehler = _workflow_schritte(lead, [ACTION_VERLOREN], "cv_lead_verloren")
	finally:
		frappe.flags.cv_verlustgrund = None
	if fehler:
		return zustand, _("Anfrage {0} nicht auf „Verloren“ gesetzt: {1}").format(lead, fehler)
	return STATUS_VERLOREN, None


def ensure_verlustgruende() -> None:
	"""Verlustgründe des Leads auch als „Quotation Lost Reason“ (ERPNext-Dialog und Berichte)."""
	for grund in VERLUSTGRUENDE:
		if not frappe.db.exists("Quotation Lost Reason", grund):
			frappe.get_doc({"doctype": "Quotation Lost Reason", "order_lost_reason": grund}).insert(
				ignore_permissions=True
			)


# --- doc_events -----------------------------------------------------------------------------


def auftrag_eingereicht(doc, method=None) -> None:
	"""Sales Order ``on_submit``: Projekt, Lead „Gewonnen“, Angebotsstatus ohne Zusatzpositionen."""
	angebote = list(dict.fromkeys(z.prevdoc_docname for z in doc.items if z.get("prevdoc_docname")))
	if not angebote:
		return
	for name in angebote:
		angebotsstatus_korrigieren(name)
	angebot = frappe.get_doc("Quotation", angebote[0])
	lead = lead_zum_angebot(angebot)
	projekt, _neu = projekt_sicherstellen(doc, lead)
	doc.project = projekt
	if lead:
		lead_mit_projekt(lead, projekt)
		_zustand, warnung = lead_gewonnen(lead)
		if warnung:
			frappe.msgprint(warnung, indicator="orange", alert=True)


def angebotsstatus_korrigieren(quotation: str) -> None:
	"""„Partially Ordered“ → „Ordered“, wenn nur Eventual-/Alternativpositionen offen sind.

	ERPNext zählt jede nicht beauftragte Zeile als offen (``Quotation.get_ordered_status``);
	Eventualpositionen werden aber nie beauftragt - das Angebot hinge sonst für immer als
	„teilweise bestellt“ in „Angebote offen“ und im Nachfassen.
	"""
	from erpnext.selling.doctype.quotation.quotation import get_ordered_items

	angebot = frappe.get_doc("Quotation", quotation)
	if angebot.status != "Partially Ordered":
		return
	bestellt = get_ordered_items(quotation)
	offen = [
		z for z in angebot.items if not ist_zusatzposition(z) and flt(bestellt.get(z.name)) < flt(z.stock_qty)
	]
	if not offen:
		angebot.db_set("status", "Ordered", update_modified=False)


def angebot_aktualisiert(doc, method=None) -> None:
	"""Quotation ``on_update_after_submit``: über ERPNext „Als verloren festlegen“ verloren → Lead folgt.

	``declare_enquiry_lost`` setzt den Status per ``db_set`` vor dem Speichern; erkennbar ist der
	Vorgang an neu angehängten Verlustgründen. ``angebot_ablehnen`` erledigt den Lead selbst.
	"""
	if doc.status != "Lost" or frappe.flags.get("cv_angebot_ablehnen") == doc.name:
		return
	vorher = doc.get_doc_before_save()
	alt = {z.name for z in (vorher.get("lost_reasons") if vorher else [])}
	neu = [z for z in doc.get("lost_reasons") or [] if z.name not in alt]
	if not neu:
		return
	grund = next((z.lost_reason for z in neu if z.lost_reason in VERLUSTGRUENDE), "Sonstiges")
	_zustand, warnung = lead_verloren(lead_zum_angebot(doc), grund, ausser=doc.name)
	if warnung:
		frappe.msgprint(warnung, indicator="orange", alert=True)
