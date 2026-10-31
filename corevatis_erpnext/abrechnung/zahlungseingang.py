"""Zahlungseingang (7.5, Handwerker#77): Kontoauszug einlesen, Zahlungen Rechnungen zuordnen.

Ablauf: Das Büro lädt den Kontoauszug (CSV der Sparkasse/VR-Bank oder CAMT.053) hoch;
``kontoauszug_importieren`` liest ihn (``abrechnung.kontoauszug``), legt je Buchung eine ERPNext
**Bank Transaction** zum Bankkonto des Betriebs an (gebucht, Status „Unreconciled“) und erkennt
Dubletten über ``transaction_id`` (Dublettenschlüssel der Buchung). ``vorschlaege`` ordnet jeder
offenen Gutschrift Rechnungen zu - zuerst über die Rechnungs- oder Auftragsnummer im
Verwendungszweck, dann Betrag und Kundenname. Das Büro bestätigt (``zuordnen``): Payment Entry gegen
die Rechnung (Teilzahlung erlaubt), Bank Transaction abgeglichen (wie das ERPNext-Abgleichwerkzeug),
Kommentar am Vorgang; der Payment-Entry-Hook aus 7.1 setzt die Abschlagszeile auf „bezahlt“, die
Prozessleiste zeigt „bezahlt am …“ bzw. „teilbezahlt“. Buchungen ohne Rechnung (Mieten, Zinsen) werden
ignoriert (``ignorieren``, Haken am Bank Transaction).

Sicherheit: nur Büro/System Manager; Datei privat, höchstens ``MAX_BYTES``, nur csv/txt/xml, nur die
eigene Datei; XML ohne DOCTYPE/ENTITY; Beträge nur aus den Buchungen und Rechnungen (der Dialog gibt
höchstens eine Teilsumme vor, geprüft gegen offenen Betrag und Buchung).
"""

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money, format_date, getdate

from corevatis_erpnext.abrechnung import kontoauszug as K
from corevatis_erpnext.abrechnung.auftrag import pruefe_rolle
from corevatis_erpnext.abrechnung.einstellungen import CSV_AUTOMATISCH, einstellungen
from corevatis_erpnext.abrechnung.kern import AbrechnungFehler
from corevatis_erpnext.abrechnung.offene_posten import offene_rechnungen, rechnungsart_text

MAX_BYTES = 5 * 1024 * 1024
ENDUNGEN = ("csv", "txt", "xml")
BANK_NAME = "Hausbank"
KONTO_NAME = "Geschäftskonto"
ZAHLUNGSARTEN = ("Wire Transfer", "Bank Draft")
IBAN_MUSTER = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
CENT = 0.005
# Punkte der Zuordnung
P_RECHNUNG, P_AUFTRAG, P_BETRAG_OFFEN, P_BETRAG_BRUTTO, P_NAME = 100, 60, 40, 25, 30
MINDESTPUNKTE = 25
BUCHUNG_FELDER = (
	"name",
	"date",
	"deposit",
	"withdrawal",
	"unallocated_amount",
	"allocated_amount",
	"description",
	"reference_number",
	"transaction_id",
	"bank_party_name",
	"bank_party_iban",
	"status",
	"bank_account",
	"company",
	"currency",
)


class ZahlungFehler(AbrechnungFehler):
	pass


# --- Bankkonto ----------------------------------------------------------------------------------


def _firma() -> str:
	firma = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	if not firma:
		firmen = frappe.get_all("Company", pluck="name", limit=1)
		firma = firmen[0] if firmen else None
	if not firma:
		frappe.throw(_("Keine Firma eingerichtet."), ZahlungFehler)
	return firma


# Sachkonto Bank: SKR03 1200 „Bankkonto“, SKR04 1800 „Bank“ - sonst das Standard-Bankkonto der Firma,
# sonst das erste Konto der Art „Bank“
BANK_KONTONUMMERN = ("1200", "1800")


def _sachkonto_bank(company: str) -> str | None:
	for nummer in BANK_KONTONUMMERN:
		konto = frappe.db.get_value(
			"Account",
			{
				"company": company,
				"account_number": nummer,
				"account_type": "Bank",
				"is_group": 0,
				"disabled": 0,
			},
		)
		if konto:
			return konto
	konto = frappe.get_cached_value("Company", company, "default_bank_account")
	if konto:
		return konto
	return frappe.db.get_value(
		"Account",
		{"company": company, "account_type": "Bank", "is_group": 0, "disabled": 0},
		"name",
		order_by="account_number asc",
	)


def ensure_bankkonto(company: str) -> str:
	"""Firmenkonto anlegen, wenn die Firma keines hat: Bank „Hausbank“, Bankkonto „Geschäftskonto“
	mit dem Sachkonto Bank der Firma (SKR03 1200). IBAN und Bankname trägt das Büro nach (sie stehen
	danach in Fußzeile und Rechnung)."""
	vorhanden = frappe.db.get_value(
		"Bank Account", {"company": company, "is_company_account": 1, "disabled": 0}
	)
	if vorhanden:
		return vorhanden
	if not frappe.db.exists("Bank", BANK_NAME):
		frappe.get_doc({"doctype": "Bank", "bank_name": BANK_NAME}).insert(ignore_permissions=True)
	sachkonto = _sachkonto_bank(company)
	if not sachkonto:
		frappe.throw(
			_("Die Firma {0} hat kein Sachkonto der Art „Bank“ - bitte zuerst im Kontenplan anlegen.").format(
				company
			),
			ZahlungFehler,
		)
	doc = frappe.get_doc(
		{
			"doctype": "Bank Account",
			"account_name": KONTO_NAME,
			"bank": BANK_NAME,
			"company": company,
			"is_company_account": 1,
			"is_default": 1,
			"account": sachkonto,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def bankkonto(company: str, name: str | None = None) -> frappe._dict:
	"""Bankkonto des Betriebs: Parameter, Einstellung, Standardkonto der Firma, sonst neu angelegt."""
	e = einstellungen()
	name = name or e.bankkonto
	if name:
		if not frappe.db.exists("Bank Account", name):
			frappe.throw(_("Bankkonto {0} nicht gefunden.").format(name), frappe.DoesNotExistError)
	else:
		name = frappe.db.get_value(
			"Bank Account", {"company": company, "is_company_account": 1, "is_default": 1, "disabled": 0}
		) or ensure_bankkonto(company)
	konto = frappe.db.get_value(
		"Bank Account",
		name,
		["name", "account", "bank", "iban", "company", "is_company_account"],
		as_dict=True,
	)
	if konto.company and konto.company != company:
		frappe.throw(_("Bankkonto {0} gehört nicht zur Firma {1}.").format(name, company), ZahlungFehler)
	if not cint(konto.is_company_account):
		frappe.throw(_("Bankkonto {0} ist kein Firmenkonto.").format(name), ZahlungFehler)
	if not konto.account:
		konto.account = _sachkonto_bank(company)
		if not konto.account:
			frappe.throw(
				_("Dem Bankkonto {0} ist kein Sachkonto zugeordnet - bitte am Bankkonto eintragen.").format(
					name
				),
				ZahlungFehler,
			)
		frappe.db.set_value("Bank Account", name, "account", konto.account, update_modified=False)
	return konto


# --- Datei --------------------------------------------------------------------------------------


def datei_lesen(datei: str) -> tuple[bytes, str, str]:
	"""Hochgeladene Datei (Name oder ``file_url`` eines ``File``) prüfen und lesen: privat, eigene
	Datei (oder System Manager), höchstens ``MAX_BYTES``, Endung csv/txt/xml. Rückgabe
	``(inhalt, dateiname, file_name)``."""
	if not datei or not isinstance(datei, str):
		frappe.throw(_("Bitte eine Datei hochladen."), frappe.MandatoryError)
	name = frappe.db.get_value("File", {"file_url": datei}) if datei.startswith("/") else None
	name = name or (datei if frappe.db.exists("File", datei) else None)
	if not name:
		frappe.throw(_("Datei nicht gefunden."), frappe.DoesNotExistError)
	f = frappe.get_doc("File", name)
	if f.owner != frappe.session.user and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Nur die eigene hochgeladene Datei kann eingelesen werden."), frappe.PermissionError)
	if not cint(f.is_private):
		frappe.throw(_("Der Kontoauszug muss als private Datei hochgeladen werden."), ZahlungFehler)
	endung = (f.file_name or "").rsplit(".", 1)[-1].lower() if "." in (f.file_name or "") else ""
	if endung not in ENDUNGEN:
		frappe.throw(_("Nur CSV- oder CAMT-XML-Dateien (.csv, .txt, .xml)."), ZahlungFehler)
	if cint(f.file_size) > MAX_BYTES:
		frappe.throw(_("Die Datei ist größer als {0} MB.").format(MAX_BYTES // (1024 * 1024)), ZahlungFehler)
	inhalt = f.get_content()
	if isinstance(inhalt, str):
		inhalt = inhalt.encode("utf-8")
	if len(inhalt) > MAX_BYTES:
		frappe.throw(_("Die Datei ist größer als {0} MB.").format(MAX_BYTES // (1024 * 1024)), ZahlungFehler)
	return inhalt, f.file_name or name, name


def _mapping(e: frappe._dict) -> dict:
	return {
		k: e.get(f"csv_spalte_{k}")
		for k in ("datum", "betrag", "verwendungszweck", "name", "iban")
		if e.get(f"csv_spalte_{k}")
	}


# --- Import -------------------------------------------------------------------------------------


def _dublette(schluessel: str) -> str | None:
	return frappe.db.get_value("Bank Transaction", {"transaction_id": schluessel}, "name")


def _anlegen(b: K.Buchung, konto: frappe._dict, company: str, quelle: str, schluessel: str):
	beschreibung = " · ".join(t for t in (b.verwendungszweck, b.buchungstext) if t)
	bt = frappe.get_doc(
		{
			"doctype": "Bank Transaction",
			"date": b.datum,
			"bank_account": konto.name,
			"company": company,
			"currency": b.waehrung or frappe.get_cached_value("Company", company, "default_currency"),
			"deposit": max(flt(b.betrag), 0.0),
			"withdrawal": max(-flt(b.betrag), 0.0),
			"description": beschreibung[:1000] or None,
			"reference_number": (b.referenz or "")[:140] or None,
			"transaction_id": schluessel,
			"bank_party_name": (b.name or "")[:140] or None,
			"bank_party_iban": b.iban if b.iban and IBAN_MUSTER.match(b.iban) else None,
			"transaction_type": (b.buchungstext or "")[:50] or None,
		}
	)
	if bt.meta.has_field("cv_import"):
		bt.cv_import = f"{quelle} ({_('Zeile')} {b.zeile})"[:140]
	bt.insert()
	bt.submit()
	return bt


@frappe.whitelist(methods=["POST"])
def kontoauszug_importieren(datei: str, bankkonto_name: str | None = None, nur_gutschriften=None) -> dict:
	"""Kontoauszug (CSV oder CAMT.053) als Bank Transactions anlegen; Dubletten überspringen.

	:param datei: ``file_url`` oder Name der privaten, eigenen Datei
	:param bankkonto_name: Bankkonto (leer: Einstellung bzw. Standardkonto der Firma)
	:param nur_gutschriften: 1 = Abgänge überspringen (leer: Einstellung, Standard 1)
	"""
	pruefe_rolle()
	frappe.has_permission("Bank Transaction", "create", throw=True)
	inhalt, dateiname, file_name = datei_lesen(datei)
	e = einstellungen()
	company = _firma()
	konto = bankkonto(company, bankkonto_name)
	try:
		buchungen, hinweise, fmt = K.lese(
			inhalt,
			dateiname,
			mapping=_mapping(e),
			trennzeichen=None if e.csv_trennzeichen == CSV_AUTOMATISCH else e.csv_trennzeichen,
		)
	except K.KontoauszugFehler as fehler:
		frappe.throw(str(fehler), ZahlungFehler, title=_("Kontoauszug"))
	konto_iban = K.normalisiere(konto.iban) if konto.iban else ""
	fremd = {b.konto_iban for b in buchungen if b.konto_iban and konto_iban and b.konto_iban != konto_iban}
	if fremd:
		frappe.throw(
			_("Die Datei gehört zum Konto {0}, eingestellt ist {1} ({2}).").format(
				", ".join(sorted(fremd)), konto.name, konto.iban
			),
			ZahlungFehler,
		)
	nur = e.nur_gutschriften if nur_gutschriften in (None, "") else cint(nur_gutschriften)
	angelegt, dubletten, abgaenge, fehler = [], [], 0, []
	for b in buchungen:
		if nur and not b.gutschrift:
			abgaenge += 1
			continue
		schluessel = b.schluessel()
		vorhanden = _dublette(schluessel)
		if vorhanden:
			dubletten.append(vorhanden)
			continue
		savepoint = "cv_kontoauszug_zeile"
		frappe.db.savepoint(savepoint)
		try:
			angelegt.append(_anlegen(b, konto, company, dateiname, schluessel).name)
		except Exception as ausnahme:
			frappe.db.rollback(save_point=savepoint)
			frappe.log_error(title=f"Kontoauszug {dateiname}: Zeile {b.zeile}")
			fehler.append(_("Zeile {0}: {1}").format(b.zeile, frappe.utils.strip_html(str(ausnahme))[:200]))
	# Datei nach dem Einlesen entfernen - die Buchungen stehen als Bank Transactions in der Site
	frappe.delete_doc("File", file_name, ignore_permissions=True, force=True)
	return {
		"format": fmt,
		"bankkonto": konto.name,
		"gelesen": len(buchungen),
		"angelegt": angelegt,
		"dubletten": dubletten,
		"abgaenge": abgaenge,
		"fehler": fehler,
		"hinweise": hinweise,
		"vorschlaege": vorschlaege(namen=angelegt) if angelegt else [],
	}


# --- Vorschlag ----------------------------------------------------------------------------------


def offene_buchungen(bankkonto_name: str | None = None, mit_ignorierten=0, namen=None) -> list[frappe._dict]:
	"""Gebuchte Gutschriften mit offenem Betrag (Rechte des Benutzers)."""
	filters: dict = {"docstatus": 1, "deposit": [">", 0], "unallocated_amount": [">", 0]}
	if bankkonto_name:
		filters["bank_account"] = bankkonto_name
	if namen:
		filters["name"] = ["in", list(namen)]
	felder = list(BUCHUNG_FELDER)
	if frappe.db.has_column("Bank Transaction", "cv_ignoriert"):
		felder += ["cv_ignoriert", "cv_notiz"]
		if not cint(mit_ignorierten) and not namen:
			filters["cv_ignoriert"] = 0
	return frappe.get_list(
		"Bank Transaction",
		filters=filters,
		fields=felder,
		order_by="date desc, creation desc",
		limit_page_length=0,
	)


def kandidaten(bt: frappe._dict, rechnungen: list[frappe._dict]) -> list[dict]:
	"""Rechnungen, die zur Buchung passen, mit Punkten und Gründen - beste zuerst."""
	betrag = flt(bt.unallocated_amount) or flt(bt.deposit)
	text = f"{bt.description or ''} {bt.reference_number or ''}"
	normal = K.normalisiere(text)
	nummern = set(K.rechnungsnummern_im_text(text, [r.name for r in rechnungen]))
	ergebnis = []
	for r in rechnungen:
		if bt.company and r.company != bt.company:
			continue
		if bt.currency and r.currency and r.currency != bt.currency:
			continue
		punkte, gruende = 0, []
		if r.name in nummern:
			punkte += P_RECHNUNG
			gruende.append(_("Rechnungsnummer im Verwendungszweck"))
		auftrag = r.get("cv_auftrag")
		if auftrag and len(K.normalisiere(auftrag)) >= 6 and K.normalisiere(auftrag) in normal:
			punkte += P_AUFTRAG
			gruende.append(_("Auftragsnummer {0} im Verwendungszweck").format(auftrag))
		offen = flt(r.outstanding_amount)
		if abs(offen - betrag) <= CENT:
			punkte += P_BETRAG_OFFEN
			gruende.append(_("Betrag entspricht dem offenen Betrag"))
		elif abs(flt(r.grand_total) - betrag) <= CENT:
			punkte += P_BETRAG_BRUTTO
			gruende.append(_("Betrag entspricht dem Rechnungsbetrag"))
		aehnlich = K.namensaehnlichkeit(bt.bank_party_name, r.customer_name)
		if aehnlich >= 0.5:
			punkte += P_NAME
			gruende.append(_("Zahler passt zum Kunden {0}").format(r.customer_name))
		if punkte < MINDESTPUNKTE:
			continue
		ergebnis.append(
			{
				"rechnung": r.name,
				"art": rechnungsart_text(r),
				"kunde": r.customer,
				"kunde_name": r.customer_name,
				"datum": r.posting_date,
				"faellig": r.due_date,
				"brutto": flt(r.grand_total),
				"offen": offen,
				"projekt": r.project,
				"auftrag": auftrag,
				"punkte": punkte,
				"gruende": gruende,
				"sicherheit": "sicher"
				if punkte >= P_RECHNUNG
				else ("wahrscheinlich" if punkte >= 70 else "möglich"),
				"vorschlag_betrag": round(min(betrag, offen), 2),
				"teilzahlung": betrag + CENT < offen,
				"ueberzahlung": betrag > offen + CENT,
			}
		)
	ergebnis.sort(key=lambda k: (-k["punkte"], str(k["faellig"] or ""), k["rechnung"]))
	return ergebnis


@frappe.whitelist()
def vorschlaege(
	bank_transaction: str | None = None, bankkonto_name: str | None = None, mit_ignorierten=0, namen=None
) -> list[dict]:
	"""Offene Gutschriften mit Zuordnungsvorschlägen (Rolle Büro)."""
	pruefe_rolle()
	if isinstance(namen, str):
		namen = frappe.parse_json(namen) if namen else None
	if bank_transaction:
		namen = [bank_transaction]
	buchungen = offene_buchungen(bankkonto_name, mit_ignorierten, namen)
	if not buchungen:
		return []
	rechnungen = offene_rechnungen()
	ergebnis = []
	for bt in buchungen:
		k = kandidaten(bt, rechnungen)
		ergebnis.append(
			{
				"name": bt.name,
				"datum": bt.date,
				"betrag": flt(bt.unallocated_amount),
				"gutschrift": flt(bt.deposit),
				"zahler": bt.bank_party_name,
				"iban": bt.bank_party_iban,
				"verwendungszweck": bt.description,
				"referenz": bt.reference_number,
				"status": bt.status,
				"ignoriert": cint(bt.get("cv_ignoriert")),
				"notiz": bt.get("cv_notiz"),
				"bankkonto": bt.bank_account,
				"currency": bt.currency,
				"kandidaten": k[:5],
				"bester": k[0] if k else None,
			}
		)
	return ergebnis


# --- Zuordnen -----------------------------------------------------------------------------------


def _zahlungsart() -> str | None:
	for art in ZAHLUNGSARTEN:
		if frappe.db.exists("Mode of Payment", art):
			return art
	return None


def _kommentar(rechnung: frappe._dict, text: str) -> None:
	for doctype, name in (("Project", rechnung.get("project")), ("Sales Invoice", rechnung.name)):
		if name and frappe.db.exists(doctype, name):
			frappe.get_doc(doctype, name).add_comment("Comment", text)


@frappe.whitelist(methods=["POST"])
def zuordnen(bank_transaction: str, sales_invoice: str, betrag=None) -> dict:
	"""Zahlung einer Buchung der Rechnung zuordnen: Payment Entry (gebucht), Bank Transaction abgeglichen.

	:param betrag: Teilbetrag (leer: kleinerer Wert aus offenem Betrag der Buchung und der Rechnung)
	"""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	pruefe_rolle()
	frappe.has_permission("Bank Transaction", "write", bank_transaction, throw=True)
	frappe.has_permission("Payment Entry", "create", throw=True)
	frappe.has_permission("Sales Invoice", "read", sales_invoice, throw=True)
	bt = frappe.get_doc("Bank Transaction", bank_transaction)
	if bt.docstatus != 1:
		frappe.throw(_("Buchung {0} ist nicht gebucht.").format(bt.name), ZahlungFehler)
	if flt(bt.deposit) <= 0:
		frappe.throw(_("Buchung {0} ist keine Gutschrift.").format(bt.name), ZahlungFehler)
	if flt(bt.unallocated_amount) <= 0:
		frappe.throw(_("Buchung {0} ist schon vollständig zugeordnet.").format(bt.name), ZahlungFehler)
	r = frappe.db.get_value(
		"Sales Invoice",
		sales_invoice,
		[
			"name",
			"docstatus",
			"is_return",
			"company",
			"customer",
			"customer_name",
			"outstanding_amount",
			"grand_total",
			"currency",
			"project",
		],
		as_dict=True,
	)
	if not r or r.docstatus != 1 or cint(r.is_return):
		frappe.throw(
			_("Rechnung {0} ist nicht gebucht oder eine Gutschrift.").format(sales_invoice), ZahlungFehler
		)
	if flt(r.outstanding_amount) <= 0:
		frappe.throw(_("Rechnung {0} ist schon bezahlt.").format(r.name), ZahlungFehler)
	if bt.company and r.company != bt.company:
		frappe.throw(_("Rechnung {0} gehört zu einer anderen Firma.").format(r.name), ZahlungFehler)
	if bt.currency and r.currency != bt.currency:
		frappe.throw(
			_("Währung der Buchung ({0}) und der Rechnung ({1}) passen nicht.").format(
				bt.currency, r.currency
			),
			ZahlungFehler,
		)
	hoechstens = round(min(flt(bt.unallocated_amount), flt(r.outstanding_amount)), 2)
	betrag = round(flt(betrag), 2) if betrag not in (None, "") else hoechstens
	if betrag <= 0 or betrag > hoechstens + CENT:
		frappe.throw(
			_(
				"Der Betrag muss zwischen 0,01 und {0} liegen (offen auf der Buchung {1}, auf der Rechnung {2})."
			).format(
				fmt_money(hoechstens, currency=r.currency),
				fmt_money(flt(bt.unallocated_amount), currency=r.currency),
				fmt_money(flt(r.outstanding_amount), currency=r.currency),
			),
			ZahlungFehler,
		)
	sachkonto = frappe.db.get_value("Bank Account", bt.bank_account, "account") if bt.bank_account else None
	if not sachkonto:
		frappe.throw(
			_(
				"Dem Bankkonto der Buchung ist kein Sachkonto zugeordnet - bitte am Bankkonto {0} eintragen."
			).format(bt.bank_account or "-"),
			ZahlungFehler,
		)
	pe = get_payment_entry("Sales Invoice", r.name, party_amount=betrag, bank_account=sachkonto)
	pe.posting_date = getdate(bt.date)
	pe.reference_no = (bt.reference_number or bt.transaction_id or bt.name)[:140]
	pe.reference_date = getdate(bt.date)
	pe.mode_of_payment = _zahlungsart()
	pe.bank_account = bt.bank_account
	pe.paid_amount = betrag
	pe.received_amount = betrag
	for ref in pe.references:
		ref.allocated_amount = betrag if ref.reference_name == r.name else 0
	pe.insert()
	pe.submit()
	# Bank Transaction abgleichen (wie erpnext Bank Reconciliation Tool, create_payment_entry_and_reconcile)
	bt.add_payment_entries(
		[{"payment_doctype": "Payment Entry", "payment_name": pe.name}], is_new_voucher=True
	)
	bt.validate_duplicate_references()
	bt.allocate_payment_entries()
	bt.update_allocated_amount()
	bt.set_status()
	bt.save()
	nach = frappe.db.get_value("Sales Invoice", r.name, ["outstanding_amount", "status"], as_dict=True)
	status = "bezahlt" if flt(nach.outstanding_amount) <= 0 else "teilbezahlt"
	text = _("Zahlung {0} am {1} zu Rechnung {2} zugeordnet ({3}, Kontoauszug {4}){5}.").format(
		fmt_money(betrag, currency=r.currency),
		format_date(bt.date),
		r.name,
		pe.name,
		bt.name,
		""
		if status == "bezahlt"
		else " - "
		+ _("offen bleiben {0}").format(fmt_money(flt(nach.outstanding_amount), currency=r.currency)),
	)
	_kommentar(r, text)
	bt.reload()
	return {
		"payment_entry": pe.name,
		"rechnung": r.name,
		"betrag": betrag,
		"offen_danach": flt(nach.outstanding_amount),
		"status": status,
		"bank_transaction": {
			"name": bt.name,
			"unallocated_amount": flt(bt.unallocated_amount),
			"status": bt.status,
		},
		"text": text,
	}


@frappe.whitelist(methods=["POST"])
def ignorieren(bank_transaction: str, grund: str | None = None, aufheben=0) -> dict:
	"""Buchung ohne Rechnungsbezug aus der Liste nehmen (oder zurückholen)."""
	pruefe_rolle()
	frappe.has_permission("Bank Transaction", "write", bank_transaction, throw=True)
	if not frappe.db.has_column("Bank Transaction", "cv_ignoriert"):
		frappe.throw(_("Felder fehlen - bitte migrate ausführen."), ZahlungFehler)
	if cint(frappe.db.get_value("Bank Transaction", bank_transaction, "docstatus")) != 1:
		frappe.throw(_("Buchung {0} ist nicht gebucht.").format(bank_transaction), ZahlungFehler)
	werte = {"cv_ignoriert": 0 if cint(aufheben) else 1}
	if not cint(aufheben):
		werte["cv_notiz"] = (frappe.utils.strip_html(grund or ""))[:500] or None
	frappe.db.set_value("Bank Transaction", bank_transaction, werte)
	return {"bank_transaction": bank_transaction, **werte}
