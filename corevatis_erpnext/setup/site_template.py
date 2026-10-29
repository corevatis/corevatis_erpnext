"""Site-Template (Aufgabe 0.4): ERPNext-Grundeinrichtung eines Handwerksbetriebs.

Einstieg::

    bench --site <site> execute corevatis_erpnext.setup.apply_site_template \
        --kwargs '{"template": "huelshoff"}'
    bench --site <site> execute corevatis_erpnext.setup.verify_site_template \
        --kwargs '{"template": "huelshoff"}'

Grundsätze:

- **Idempotent.** Jeder Schritt prüft erst und schreibt nur, wenn etwas fehlt. Ein
  zweiter Lauf meldet ``changes: []``.
- **Nichts löschen, nur ergänzen.** Bestehende Datensätze und Werte bleiben stehen.
  Ausnahmen, weil sie den Zweck des Templates bilden: ``system_settings`` und
  ``global_defaults`` werden auf die konfigurierten Werte gesetzt; Nummernkreise
  werden vorne eingereiht (alte Serien bleiben wählbar). Standardkonto und
  Standardlager setzt das Template nur, wenn leer oder die Firma in diesem Lauf
  entstanden ist.
- **ERPNext-Logik nachnutzen.** Auf einer frischen Site läuft der ERPNext-Setup-Wizard
  (``frappe.desk.page.setup_wizard.setup_wizard.setup_complete``) mit den Werten der
  Vorlage; ist er schon gelaufen, wird die Firma wie im Wizard als ``Company`` mit
  ``Standard Template`` angelegt. Kontenrahmen (``de_kontenplan_SKR03/04.json``) und
  Steuervorlagen (``country_wise_tax.json``) erzeugt ERPNext dabei selbst.
"""

from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path

import frappe
from frappe.utils import add_days, add_years, cint, flt, getdate, today
from frappe.utils.nestedset import get_root_of

from corevatis_erpnext.setup.config import TEMPLATE_DIR, find_placeholders, is_placeholder, load_config
from corevatis_erpnext.setup.naming import RETURN_SERIES_DEFAULT_KEY

# Kontonummern je Kontenrahmen (ERPNext-Vorlagen „SKR03/SKR04 mit Kontonummern“)
SKR_ACCOUNTS = {
	"SKR03": {"ust19": "1776", "ust7": "1771", "vst19": "1576", "vst7": "1571", "erloese19": "8400"},
	"SKR04": {"ust19": "3806", "ust7": "3801", "vst19": "1406", "vst7": "1401", "erloese19": "4400"},
}


SUBMIT_RIGHTS = ("submit", "cancel", "amend")
ALL_RIGHTS = (
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"report",
	"export",
	"import",
	"share",
	"print",
	"email",
	"select",
)

# Adressvorlage Deutschland nach DIN 5008: Inland ohne Länderzeile. Die Vorlage für
# Deutschland ist zugleich Standard für Länder ohne eigene Vorlage, daher die
# Länderzeile nur für ausländische Adressen.
ADDRESS_TEMPLATE_DE = (
	"{{ address_line1 }}<br>\n"
	"{% if address_line2 %}{{ address_line2 }}<br>{% endif -%}\n"
	"{{ pincode }} {{ city }}<br>\n"
	'{% if country and country != "Germany" %}{{ country | upper }}<br>{% endif -%}\n'
)


class Report:
	"""Protokoll eines Laufs: Änderungen, Hinweise, übersprungene Schritte."""

	def __init__(self) -> None:
		self.changes: list[str] = []
		self.warnings: list[str] = []
		self.skipped: list[str] = []

	def change(self, area: str, message: str) -> None:
		self.changes.append(f"{area}: {message}")

	def warn(self, area: str, message: str) -> None:
		self.warnings.append(f"{area}: {message}")

	def skip(self, area: str, message: str) -> None:
		self.skipped.append(f"{area}: {message}")

	def as_dict(self, **extra) -> dict:
		return {"changes": self.changes, "warnings": self.warnings, "skipped": self.skipped, **extra}


# ---------------------------------------------------------------------------
# Öffentliche Einstiege
# ---------------------------------------------------------------------------


def apply_site_template(
	config: dict | None = None,
	config_file: str | None = None,
	template: str | None = None,
	commit: bool = True,
) -> dict:
	"""Template anwenden. Rückgabe: Bericht (``changes``, ``warnings``, ``skipped``, ``company``)."""
	cfg = load_config(config, config_file, template)
	report = Report()
	for path in find_placeholders(cfg):
		report.warn("Platzhalter", f"{path} noch offen (nicht geschrieben)")

	wizard_ran = _run_setup_wizard(cfg, report)
	company, created = _ensure_company(cfg, wizard_ran, report)
	_ensure_fiscal_year(cfg, company, report)
	_ensure_system_settings(cfg, report)
	_ensure_global_defaults(cfg, company, report)
	_ensure_default_accounts(cfg, company, created, report)
	_ensure_taxes(cfg, company, report)
	_ensure_naming_series(cfg, report)
	_ensure_roles(cfg, report)
	_ensure_warehouses(cfg, company, created, report)
	_ensure_address_template(cfg, report)
	_ensure_company_address(cfg, company, report)
	_ensure_letter_head(cfg, company, report)
	_ensure_print_formats(cfg, report)
	_ensure_print_settings(cfg, report)
	_ensure_angebot(cfg, company, report)
	_ensure_workspaces(cfg, report)
	_ensure_erpnext_germany(cfg, report)

	if commit:
		frappe.db.commit()
	frappe.clear_cache()
	return report.as_dict(company=company)


def verify_site_template(
	config: dict | None = None, config_file: str | None = None, template: str | None = None
) -> dict:
	"""Prüfpunkte der Checkliste. Rückgabe: ``ok``, ``failed`` (Liste), ``checks`` (alle)."""
	cfg = load_config(config, config_file, template)
	return _verify(cfg)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def fiscal_year_dates(start: str = "01-01", reference: date | str | None = None) -> tuple[date, date]:
	"""Beginn und Ende des Geschäftsjahres, in dem ``reference`` (heute) liegt."""
	ref = getdate(reference or today())
	month, day = (int(p) for p in start.split("-"))
	begin = date(ref.year, month, day)
	if begin > ref:
		begin = date(ref.year - 1, month, day)
	return begin, getdate(add_days(add_years(begin, 1), -1))


def resolve_chart_name(country: str, chart: str) -> str:
	"""``SKR03`` → ``SKR03 mit Kontonummern`` (Name aus der ERPNext-Vorlage)."""
	from erpnext.accounts.doctype.account.chart_of_accounts.chart_of_accounts import get_charts_for_country

	names = [n for n in get_charts_for_country(country) if n.upper().startswith(chart.upper())]
	if len(names) != 1:
		frappe.throw(f"Kontenrahmen {chart} für {country} nicht eindeutig gefunden: {names}")
	return names[0]


def account_by_number(company: str, number: str) -> str | None:
	return frappe.db.get_value("Account", {"company": company, "account_number": number, "is_group": 0})


def _company_name(cfg: dict) -> str | None:
	return frappe.db.get_value("Company", {"company_name": cfg["company"]["company_name"]})


def _value(value):
	"""Konfigurationswert oder ``None`` für Platzhalter/leer."""
	if value in (None, "") or is_placeholder(value):
		return None
	return value


def _read_template_file(name: str) -> str:
	return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Schritte
# ---------------------------------------------------------------------------


def _run_setup_wizard(cfg: dict, report: Report) -> bool:
	"""Setup-Wizard auf frischer Site; ``True``, wenn er in diesem Lauf lief."""
	if frappe.is_setup_complete():
		return False

	from frappe.desk.page.setup_wizard.setup_wizard import setup_complete

	company = cfg["company"]
	settings = cfg.get("system_settings") or {}
	begin, end = fiscal_year_dates(company.get("fiscal_year_start", "01-01"))
	language = frappe.db.get_value("Language", settings.get("language", "de"), "language_name") or "English"
	args = {
		"language": language,
		"lang": language,
		"country": company["country"],
		"timezone": settings.get("time_zone", "Europe/Berlin"),
		"currency": company["default_currency"],
		"company_name": company["company_name"],
		"company_abbr": company["abbr"],
		"chart_of_accounts": resolve_chart_name(company["country"], company["chart_of_accounts"]),
		"fy_start_date": str(begin),
		"fy_end_date": str(end),
		"enable_telemetry": "0",
	}
	setup_complete(args)
	if not frappe.is_setup_complete():
		frappe.throw("ERPNext-Setup-Wizard ist nicht abgeschlossen (siehe Error Log „Setup failed“)")
	report.change(
		"Setup-Wizard", f"ausgeführt (Firma {company['company_name']}, {args['chart_of_accounts']})"
	)
	return True


def _ensure_company(cfg: dict, wizard_ran: bool, report: Report) -> tuple[str, bool]:
	"""Firma sicherstellen; ``created`` = in diesem Lauf entstanden (Wizard oder hier)."""
	spec = cfg["company"]
	chart = resolve_chart_name(spec["country"], spec["chart_of_accounts"])
	name = _company_name(cfg)
	created = bool(name and wizard_ran)

	if not name:
		other = frappe.db.get_value("Company", {"abbr": spec["abbr"]})
		if other:
			frappe.throw(f"Kürzel {spec['abbr']} ist schon von Firma {other} belegt")
		doc = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": spec["company_name"],
				"abbr": spec["abbr"],
				"default_currency": spec["default_currency"],
				"country": spec["country"],
				"enable_perpetual_inventory": 1,
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": chart,
			}
		)
		doc.insert(ignore_permissions=True)
		name, created = doc.name, True
		report.change("Firma", f"{name} angelegt ({chart})")
	else:
		current = frappe.db.get_value(
			"Company", name, ["country", "default_currency", "chart_of_accounts", "abbr"], as_dict=True
		)
		for field, wanted in (
			("country", spec["country"]),
			("default_currency", spec["default_currency"]),
			("chart_of_accounts", chart),
			("abbr", spec["abbr"]),
		):
			if current[field] != wanted:
				report.warn("Firma", f"{field} ist {current[field]!r}, Vorlage {wanted!r} (nicht geändert)")

	# optionale Stammdaten nur ergänzen, Platzhalter nie schreiben
	for field in ("phone_no", "email", "website", "tax_id", "fax"):
		wanted = _value(spec.get(field))
		if wanted and not frappe.db.get_value("Company", name, field):
			frappe.db.set_value("Company", name, field, wanted)
			report.change("Firma", f"{field} gesetzt")

	return name, created


def _ensure_fiscal_year(cfg: dict, company: str, report: Report) -> None:
	from erpnext.setup.setup_wizard.operations.install_fixtures import get_fy_details

	begin, end = fiscal_year_dates(cfg["company"].get("fiscal_year_start", "01-01"))
	fy = frappe.db.get_value(
		"Fiscal Year",
		{"year_start_date": ("<=", begin), "year_end_date": (">=", begin), "disabled": 0},
		"name",
	)
	if not fy:
		year = get_fy_details(begin, end)
		if frappe.db.exists("Fiscal Year", year):
			frappe.throw(f"Geschäftsjahr {year} existiert mit anderem Zeitraum; bitte manuell prüfen")
		frappe.get_doc(
			{"doctype": "Fiscal Year", "year": year, "year_start_date": begin, "year_end_date": end}
		).insert(ignore_permissions=True)
		report.change("Geschäftsjahr", f"{year} angelegt ({begin} - {end})")
		return

	doc = frappe.get_doc("Fiscal Year", fy)
	companies = [row.company for row in doc.get("companies")]
	if companies and company not in companies:
		doc.append("companies", {"company": company})
		doc.save(ignore_permissions=True)
		report.change("Geschäftsjahr", f"Firma {company} in {fy} ergänzt")


def _ensure_system_settings(cfg: dict, report: Report) -> None:
	wanted = cfg.get("system_settings") or {}
	if not wanted:
		return
	doc = frappe.get_single("System Settings")
	diff = {k: v for k, v in wanted.items() if str(doc.get(k) or "") != str(v)}
	if not diff:
		return
	doc.update(diff)
	doc.save(ignore_permissions=True)
	report.change("Systemeinstellungen", ", ".join(f"{k}={v}" for k, v in diff.items()))


def _ensure_global_defaults(cfg: dict, company: str, report: Report) -> None:
	doc = frappe.get_single("Global Defaults")
	diff = {}
	for field, value in (
		("default_company", company),
		("default_currency", cfg["company"]["default_currency"]),
		("country", cfg["company"]["country"]),
	):
		if not doc.get(field):
			diff[field] = value
	for field, value in (cfg.get("global_defaults") or {}).items():
		if str(doc.get(field) if doc.get(field) is not None else "") != str(value):
			diff[field] = value
	if not diff:
		return
	doc.update(diff)
	doc.save(ignore_permissions=True)
	report.change("Globale Standards", ", ".join(f"{k}={v}" for k, v in diff.items()))


def _ensure_default_accounts(cfg: dict, company: str, created: bool, report: Report) -> None:
	numbers = SKR_ACCOUNTS[cfg["company"]["chart_of_accounts"]]
	income = account_by_number(company, numbers["erloese19"])
	if not income:
		report.warn("Konten", f"Erlöskonto {numbers['erloese19']} fehlt")
		return
	current = frappe.db.get_value("Company", company, "default_income_account")
	if current != income and (created or not current):
		frappe.db.set_value("Company", company, "default_income_account", income)
		report.change("Konten", f"Standard-Erlöskonto {income}")


def _ensure_taxes(cfg: dict, company: str, report: Report) -> None:
	spec = cfg.get("taxes") or {}
	if not spec:
		return

	if not frappe.db.exists("Sales Taxes and Charges Template", {"company": company}):
		from erpnext.setup.setup_wizard.operations.taxes_setup import setup_taxes_and_charges

		setup_taxes_and_charges(company, cfg["company"]["country"])
		report.change("Steuern", "Steuervorlagen aus ERPNext (country_wise_tax.json) angelegt")

	if "add_taxes_from_item_tax_template" in spec:
		wanted = cint(spec["add_taxes_from_item_tax_template"])
		current = cint(frappe.db.get_single_value("Accounts Settings", "add_taxes_from_item_tax_template"))
		if current != wanted:
			frappe.db.set_single_value("Accounts Settings", "add_taxes_from_item_tax_template", wanted)
			report.change("Steuern", f"Accounts Settings add_taxes_from_item_tax_template={wanted}")

	chart = cfg["company"].get("chart_of_accounts")
	for message in ensure_sales_tax_templates(company, chart, spec):
		report.change("Steuern", message)

	for doctype, title, replace in (
		(
			"Sales Taxes and Charges Template",
			spec.get("default_sales_template"),
			spec.get("replace_default_sales_templates") or (),
		),
		("Purchase Taxes and Charges Template", spec.get("default_purchase_template"), ()),
	):
		if not title:
			continue
		name = frappe.db.get_value(doctype, {"company": company, "title": title})
		if not name:
			report.warn("Steuern", f"{doctype} {title!r} fehlt")
			continue
		message = _set_default_template(doctype, company, name, replace)
		if message:
			report.change("Steuern", message)

	for title in spec.get("item_tax_templates") or []:
		if not frappe.db.exists("Item Tax Template", {"company": company, "title": title}):
			report.warn("Steuern", f"Item Tax Template {title!r} fehlt")

	# Standard-Artikelsteuer an der obersten Artikelgruppe: Artikel ohne eigene
	# Vorlage erben 19 % (die Zeilen der Verkaufsvorlage haben Satz 0).
	default_title = spec.get("default_item_tax_template")
	template = default_title and frappe.db.get_value(
		"Item Tax Template", {"company": company, "title": default_title}
	)
	if not template:
		return
	root = get_root_of("Item Group")
	group = frappe.get_doc("Item Group", root)
	companies = {
		frappe.db.get_value("Item Tax Template", row.item_tax_template, "company")
		for row in group.get("taxes")
		if row.item_tax_template
	}
	if company in companies:
		return
	group.append("taxes", {"item_tax_template": template})
	group.flags.ignore_mandatory = True
	group.save(ignore_permissions=True)
	report.change("Steuern", f"Artikelgruppe {root}: Standard-Artikelsteuer {template}")


def skr_of(chart_name: str | None) -> str | None:
	"""``SKR03``/``SKR04`` aus dem Kontenrahmen-Namen der Firma bzw. der Vorlage."""
	for skr in SKR_ACCOUNTS:
		if chart_name and chart_name.startswith(skr):
			return skr
	return None


def ensure_sales_tax_templates(company: str, chart: str | None, spec: dict) -> list[str]:
	"""Eigene Verkaufs-Steuervorlagen (``taxes.sales_templates``) für die Firma anlegen.

	Hintergrund (Befunde mvp-test): Die deutsche ERPNext-Vorlage „Lieferung oder sonstige
	Leistung im Inland“ hat zwei Zeilen (USt 19 % und 7 %, Satz 0); den Satz liefert erst die
	Artikelsteuervorlage. Im Angebot stehen deshalb immer zwei Steuerzeilen, eine davon mit
	0,00 €, und Artikel ohne Artikelsteuervorlage (z. B. einer zweiten Firma) bleiben ohne
	Steuer. Die eigene Vorlage hat genau eine Zeile mit festem Satz. Bestehende Vorlagen
	werden nicht geändert. Rückgabe: Änderungsmeldungen.
	"""
	skr = skr_of(chart) or skr_of(frappe.db.get_value("Company", company, "chart_of_accounts"))
	if not skr:
		return []
	changes = []
	for title, row in (spec.get("sales_templates") or {}).items():
		if frappe.db.exists("Sales Taxes and Charges Template", {"company": company, "title": title}):
			continue
		account = account_by_number(company, SKR_ACCOUNTS[skr][row["account"]])
		if not account:
			continue
		doc = frappe.get_doc(
			{
				"doctype": "Sales Taxes and Charges Template",
				"title": title,
				"company": company,
				"taxes": [
					{
						"charge_type": "On Net Total",
						"account_head": account,
						"rate": row["rate"],
						"description": row.get("description") or title,
					}
				],
			}
		)
		doc.insert(ignore_permissions=True)
		changes.append(f"{doc.name} angelegt ({row['rate']} % auf {account})")
	return changes


def _set_default_template(doctype: str, company: str, name: str, replace=()) -> str | None:
	"""``name`` zum Standard der Firma machen, wenn es keinen gibt oder der bisherige eine der
	von ERPNext mitgelieferten Vorlagen aus ``replace`` ist (eigene Standards bleiben)."""
	current = frappe.db.get_value(
		doctype, {"company": company, "is_default": 1}, ["name", "title"], as_dict=True
	)
	if current and (current.name == name or current.title not in replace):
		return None
	if current:
		frappe.db.set_value(doctype, current.name, "is_default", 0)
	frappe.db.set_value(doctype, name, "is_default", 1)
	return f"{name} als Standard gesetzt" + (f" (statt {current.name})" if current else "")


def _ensure_naming_series(cfg: dict, report: Report) -> None:
	return_series = json.loads(frappe.db.get_default(RETURN_SERIES_DEFAULT_KEY) or "{}")
	wanted_returns = dict(return_series)

	for doctype, spec in (cfg.get("naming_series") or {}).items():
		if not frappe.db.exists("DocType", doctype):
			report.skip("Nummernkreise", f"{doctype} nicht installiert")
			continue
		field = frappe.get_meta(doctype).get_field("naming_series")
		if not field:
			report.warn("Nummernkreise", f"{doctype} hat kein Feld naming_series")
			continue

		head = [spec["series"]]
		if spec.get("return_series"):
			head.append(spec["return_series"])
			wanted_returns[doctype] = spec["return_series"]
		current = [o.strip() for o in (field.options or "").split("\n") if o.strip()]
		desired = head + [o for o in current if o not in head]
		if current == desired and field.default == spec["series"]:
			continue

		settings = frappe.get_doc("Document Naming Settings")
		settings.transaction_type = doctype
		settings.naming_series_options = "\n".join(desired)
		settings.user_must_always_select = 0
		settings.update_series()
		report.change("Nummernkreise", f"{doctype}: {' / '.join(head)} (Standard {spec['series']})")

	if wanted_returns != return_series:
		frappe.db.set_default(RETURN_SERIES_DEFAULT_KEY, json.dumps(wanted_returns, sort_keys=True))
		report.change("Nummernkreise", f"Rückgabe-/Gutschrift-Serien {wanted_returns}")


def _rights(cfg: dict, perm) -> list[str]:
	rights = cfg["permission_sets"][perm] if isinstance(perm, str) else list(perm)
	unknown = set(rights) - set(ALL_RIGHTS)
	if unknown:
		frappe.throw(f"Unbekannte Rechte {sorted(unknown)}")
	return rights


def _ensure_roles(cfg: dict, report: Report) -> None:
	for role, spec in (cfg.get("roles") or {}).items():
		if not frappe.db.exists("Role", role):
			frappe.get_doc(
				{"doctype": "Role", "role_name": role, "desk_access": cint(spec.get("desk_access", 1))}
			).insert(ignore_permissions=True)
			report.change("Rollen", f"Rolle {role} angelegt")

		_ensure_role_profile(role, spec.get("role_profile") or [], report)

		for doctype, perm in (spec.get("permissions") or {}).items():
			_ensure_docperm(doctype, role, _rights(cfg, perm), report)

	# Kalkulationsfelder am Angebot/Auftrag (Ebene 2, Review Epic 4 G-1): erst jetzt haben Büro
	# und Meister Ebene 0 an Quotation/Sales Order
	from corevatis_erpnext.angebot.felder import ensure_feldrechte

	for text in ensure_feldrechte():
		report.change("Rechte", text)


def _ensure_role_profile(role: str, extra_roles: list[str], report: Report) -> None:
	roles = [role]
	for name in extra_roles:
		if frappe.db.exists("Role", name):
			roles.append(name)
		else:
			report.skip("Rollen", f"Rollenprofil {role}: Rolle {name} nicht vorhanden")

	if not frappe.db.exists("Role Profile", role):
		doc = frappe.get_doc({"doctype": "Role Profile", "role_profile": role})
		for name in roles:
			doc.append("roles", {"role": name})
		doc.insert(ignore_permissions=True)
		report.change("Rollen", f"Rollenprofil {role} angelegt ({', '.join(roles)})")
		return

	doc = frappe.get_doc("Role Profile", role)
	present = {row.role for row in doc.get("roles")}
	missing = [name for name in roles if name not in present]
	if missing:
		for name in missing:
			doc.append("roles", {"role": name})
		doc.save(ignore_permissions=True)
		report.change("Rollen", f"Rollenprofil {role}: {', '.join(missing)} ergänzt")


def _ensure_docperm(doctype: str, role: str, rights: list[str], report: Report) -> None:
	if not frappe.db.exists("DocType", doctype):
		report.skip("Rechte", f"{doctype} nicht installiert ({role})")
		return
	if not frappe.get_meta(doctype).is_submittable:
		rights = [r for r in rights if r not in SUBMIT_RIGHTS]

	from frappe.permissions import setup_custom_perms

	# Erster eigener Eintrag: Frappe nutzt dann nur noch Custom DocPerm für den
	# Doctype - deshalb vorher die Standardrechte übernehmen (wie der Role
	# Permission Manager).
	if setup_custom_perms(doctype):
		report.change("Rechte", f"{doctype}: Standardrechte nach Custom DocPerm übernommen")

	name = frappe.db.get_value(
		"Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0}
	)
	if not name:
		doc = frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": doctype,
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": role,
				"permlevel": 0,
				"if_owner": 0,
				**{right: 1 for right in rights},
			}
		)
		doc.insert(ignore_permissions=True)
		report.change("Rechte", f"{role} auf {doctype}: {', '.join(rights)}")
	else:
		doc = frappe.get_doc("Custom DocPerm", name)
		missing = [right for right in rights if not cint(doc.get(right))]
		if not missing:
			return
		for right in missing:
			doc.set(right, 1)
		doc.save(ignore_permissions=True)
		report.change("Rechte", f"{role} auf {doctype}: {', '.join(missing)} ergänzt")
	frappe.clear_cache(doctype=doctype)


def _ensure_warehouses(cfg: dict, company: str, created: bool, report: Report) -> None:
	specs = cfg.get("warehouses") or []
	if not specs:
		return
	root = frappe.db.get_value("Warehouse", {"company": company, "is_group": 1}, "name", order_by="lft asc")
	for spec in specs:
		name = frappe.db.get_value(
			"Warehouse", {"company": company, "warehouse_name": spec["warehouse_name"]}
		)
		if not name:
			parent = spec.get("parent_warehouse")
			if parent:
				parent = frappe.db.get_value("Warehouse", {"company": company, "warehouse_name": parent})
			doc = frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": spec["warehouse_name"],
					"company": company,
					"is_group": cint(spec.get("is_group")),
					"parent_warehouse": parent or root,
				}
			)
			doc.insert(ignore_permissions=True)
			name = doc.name
			report.change("Lager", f"{name} angelegt")

		if cint(spec.get("is_default")):
			current = frappe.db.get_value("Company", company, "default_warehouse")
			if current != name and (created or not current):
				frappe.db.set_value("Company", company, "default_warehouse", name)
				report.change("Lager", f"Standardlager der Firma {name}")


def _ensure_address_template(cfg: dict, report: Report) -> None:
	if not cfg.get("address_template_germany"):
		return
	erpnext_default = Path(
		frappe.get_app_path("erpnext", "regional", "address_template", "templates", "germany.html")
	)
	standard = erpnext_default.read_text(encoding="utf-8").strip() if erpnext_default.is_file() else ""

	if not frappe.db.exists("Address Template", "Germany"):
		frappe.get_doc(
			{
				"doctype": "Address Template",
				"country": "Germany",
				"is_default": 0 if frappe.db.exists("Address Template", {"is_default": 1}) else 1,
				"template": ADDRESS_TEMPLATE_DE,
			}
		).insert(ignore_permissions=True)
		report.change("Adressvorlage", "Deutschland (DIN 5008) angelegt")
		return

	current = (frappe.db.get_value("Address Template", "Germany", "template") or "").strip()
	if current == ADDRESS_TEMPLATE_DE.strip():
		return
	if current and current != standard:
		report.skip("Adressvorlage", "Deutschland ist angepasst - nicht überschrieben")
		return
	doc = frappe.get_doc("Address Template", "Germany")
	doc.template = ADDRESS_TEMPLATE_DE
	doc.save(ignore_permissions=True)
	report.change("Adressvorlage", "Deutschland auf DIN 5008 (Inland ohne Länderzeile) umgestellt")


def _ensure_company_address(cfg: dict, company: str, report: Report) -> None:
	spec = cfg.get("address") or {}
	if not spec:
		return
	existing = frappe.db.get_value(
		"Dynamic Link", {"link_doctype": "Company", "link_name": company, "parenttype": "Address"}, "parent"
	)
	if existing:
		return
	required = ("address_line1", "city", "country")
	if any(not _value(spec.get(f)) for f in required):
		report.warn("Adresse", "Firmenadresse nicht angelegt (Platzhalter in Pflichtfeldern)")
		return
	doc = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": _value(spec.get("address_title")) or company,
			"address_type": "Billing",
			"is_your_company_address": 1,
			"links": [{"link_doctype": "Company", "link_name": company}],
			**{
				f: _value(spec.get(f))
				for f in ("address_line1", "address_line2", "pincode", "city", "country")
			},
		}
	)
	doc.insert(ignore_permissions=True)
	report.change("Adresse", f"Firmenadresse {doc.name} angelegt")


# Von ERPNext bei der Installation angelegte Briefköpfe (erpnext/setup/install.py);
# „Company Letterhead - Grey“ ist danach Site-Standard.
ERPNEXT_LETTER_HEADS = ("Company Letterhead", "Company Letterhead - Grey")
LETTER_HEAD_MARKER = "{#- Briefkopf (Corevatis-Site-Template)"
OWNER_LINE_SLOT = "$owner_line"


def jinja_literal_html(value) -> str:
	"""Konfigurationswert als festen Text für einen Jinja-Quelltext (Briefkopf, Druckformat).

	Der Briefkopf wird bei jedem Druck mit Frappes Jinja gerendert (ohne Autoescape, mit
	``frappe.db`` & Co. im Kontext). Werte aus der Konfiguration (später Wizard-Eingaben)
	dürfen dort weder Markup noch Jinja-Syntax einbringen: HTML-escaped und ``{``/``}``
	als Zeichenreferenz - ohne ``{`` gibt es keinen Delimiter (``{{``, ``{%``, ``{#``),
	der Browser bzw. wkhtmltopdf zeigt die Zeichen trotzdem unverändert an.
	"""
	text = html.escape("" if value is None else str(value), quote=True)
	return text.replace("{", "&#123;").replace("}", "&#125;")


def _ensure_letter_head(cfg: dict, company: str, report: Report) -> None:
	spec = cfg.get("letter_head")
	if spec is None:
		return
	name = _value(spec.get("letter_head_name")) or company
	if not frappe.db.exists("Letter Head", name):
		footer_source = _read_template_file("letter_head_footer.html")
		if footer_source.count(OWNER_LINE_SLOT) != 1:
			raise frappe.ValidationError(f"letter_head_footer.html: {OWNER_LINE_SLOT} genau einmal erwartet")
		# fester Text, kein Jinja: der Briefkopf-Quelltext wird bei jedem Druck gerendert
		footer = footer_source.replace(OWNER_LINE_SLOT, jinja_literal_html(_value(spec.get("owner_line"))))
		frappe.get_doc(
			{
				"doctype": "Letter Head",
				"letter_head_name": name,
				"source": "HTML",
				"content": _read_template_file("letter_head_header.html"),
				"footer_source": "HTML",
				"footer": footer,
				"is_default": 0,
			}
		).insert(ignore_permissions=True)
		report.change("Briefkopf", f"{name} angelegt")

	# Frappe setzt beim Anlegen außerhalb von install/migrate immer source = "Image"
	# (LetterHead.before_insert); für den eigenen HTML-Briefkopf zurückstellen.
	source, content = frappe.db.get_value("Letter Head", name, ["source", "content"])
	if source != "HTML" and (content or "").startswith(LETTER_HEAD_MARKER):
		frappe.db.set_value("Letter Head", name, "source", "HTML")
		report.change("Briefkopf", f"{name}: Quelle HTML")

	if not frappe.db.get_value("Company", company, "default_letter_head"):
		frappe.db.set_value("Company", company, "default_letter_head", name)
		report.change("Briefkopf", f"{name} als Standard der Firma")

	# Site-Standard: gilt für Belege ohne letter_head (z. B. über die REST-API angelegt;
	# nur der Desk übernimmt den Briefkopf der Firma). Nur ERPNexts mitgelieferten
	# Standard ersetzen, nie einen eigenen.
	current = frappe.db.get_value("Letter Head", {"is_default": 1})
	if current != name and (not current or current in ERPNEXT_LETTER_HEADS):
		doc = frappe.get_doc("Letter Head", name)
		doc.is_default = 1
		doc.save(ignore_permissions=True)
		report.change("Briefkopf", f"{name} als Site-Standard (statt {current or 'keinem'})")


def _ensure_print_formats(cfg: dict, report: Report) -> None:
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	# eigene Formate, die das Template als Standard ablösen darf (z. B. „Angebot“ durch
	# „Angebot Corevatis“, 4.3); alle anderen eigenen Standards bleiben
	replace = cfg.get("print_formats_replace") or {}
	for doctype, print_format in (cfg.get("print_formats") or {}).items():
		if not frappe.db.exists("Print Format", print_format):
			report.skip("Druckformate", f"{print_format} ({doctype}) nicht installiert")
			continue
		if frappe.db.get_value("Print Format", print_format, "doc_type") != doctype:
			report.warn("Druckformate", f"{print_format} gehört nicht zu {doctype}")
			continue
		current = frappe.get_meta(doctype).default_print_format
		if current == print_format:
			continue
		if _is_custom_print_format(current) and current not in replace.get(doctype, ()):
			report.skip("Druckformate", f"{doctype} hat schon eigenen Standard {current!r}")
			continue
		make_property_setter(doctype, None, "default_print_format", print_format, "Data", for_doctype=True)
		frappe.clear_cache(doctype=doctype)
		report.change("Druckformate", f"{doctype}: Standard {print_format}")


def _ensure_print_settings(cfg: dict, report: Report) -> None:
	for field, value in (cfg.get("print_settings") or {}).items():
		if not frappe.get_meta("Print Settings").has_field(field):
			report.warn("Druckeinstellungen", f"Feld {field} unbekannt")
			continue
		if str(frappe.db.get_single_value("Print Settings", field)) != str(value):
			frappe.db.set_single_value("Print Settings", field, value)
			report.change("Druckeinstellungen", f"{field}={value}")


def _ensure_angebot(cfg: dict, company: str, report: Report) -> None:
	"""Angebotsdruck (4.3): Textbausteine anlegen (auch ohne migrate, z. B. frische Site im
	Onboarding) und Zahlungsbedingungen als Standard der Firma, wenn dort noch keine stehen."""
	spec = cfg.get("angebot") or {}
	if not spec:
		return
	from corevatis_erpnext.angebot.textbausteine import ensure_textbausteine

	for titel in ensure_textbausteine():
		report.change("Angebot", f"Textbaustein {titel} angelegt")
	terms = spec.get("default_selling_terms")
	if terms and not frappe.db.get_value("Company", company, "default_selling_terms"):
		if frappe.db.exists("Terms and Conditions", terms):
			frappe.db.set_value("Company", company, "default_selling_terms", terms)
			report.change("Angebot", f"{terms} als Zahlungsbedingungen der Firma")
		else:
			report.warn("Angebot", f"Textbaustein {terms} fehlt")


def _ensure_workspaces(cfg: dict, report: Report) -> None:
	"""Sichtbarkeit von (ERPNext-)Arbeitsbereichen, z. B. CRM ausblenden; ``is_hidden: 0`` nimmt
	das zurück. Fehlende Arbeitsbereiche werden übersprungen."""
	for name, values in (cfg.get("workspaces") or {}).items():
		if not frappe.db.exists("Workspace", name):
			report.skip("Arbeitsbereiche", f"{name} nicht vorhanden")
			continue
		for field, value in values.items():
			if cint(frappe.db.get_value("Workspace", name, field)) != cint(value):
				frappe.db.set_value("Workspace", name, field, cint(value))
				report.change("Arbeitsbereiche", f"{name}: {field}={cint(value)}")


def _is_custom_print_format(name: str | None) -> bool:
	"""Eigenes (nicht von einer App geliefertes) Druckformat? Standardformate wie
	„Quotation Classic“ (ERPNext setzt sie im Doctype als Standard) dürfen ersetzt werden."""
	if not name:
		return False
	return frappe.db.get_value("Print Format", name, "standard") == "No"


def _ensure_erpnext_germany(cfg: dict, report: Report) -> None:
	spec = cfg.get("erpnext_germany") or {}
	if not spec:
		return
	if not frappe.db.exists("DocType", "ERPNext Germany Settings"):
		report.skip("erpnext_germany", "nicht installiert")
		return
	meta = frappe.get_meta("ERPNext Germany Settings")
	for field, value in spec.items():
		if not meta.has_field(field):
			report.warn("erpnext_germany", f"Feld {field} unbekannt")
			continue
		if cint(frappe.db.get_single_value("ERPNext Germany Settings", field)) != cint(value):
			frappe.db.set_single_value("ERPNext Germany Settings", field, value)
			report.change("erpnext_germany", f"{field}={value}")


# ---------------------------------------------------------------------------
# Prüfung
# ---------------------------------------------------------------------------


def _verify(cfg: dict) -> dict:
	checks: list[dict] = []

	def check(name: str, ok, detail: str = "") -> bool:
		checks.append({"check": name, "ok": bool(ok), "detail": detail})
		return bool(ok)

	spec = cfg["company"]
	check("Setup-Wizard abgeschlossen", frappe.is_setup_complete())
	company = _company_name(cfg)
	if not check("Firma vorhanden", company, spec["company_name"]):
		return _verify_result(checks)

	data = frappe.db.get_value(
		"Company",
		company,
		[
			"abbr",
			"country",
			"default_currency",
			"chart_of_accounts",
			"default_income_account",
			"default_warehouse",
			"default_letter_head",
		],
		as_dict=True,
	)
	chart = resolve_chart_name(spec["country"], spec["chart_of_accounts"])
	check("Firma: Kürzel", data.abbr == spec["abbr"], data.abbr)
	check("Firma: Land", data.country == spec["country"], data.country)
	check("Firma: Währung", data.default_currency == spec["default_currency"], data.default_currency)
	check("Firma: Kontenrahmen", data.chart_of_accounts == chart, data.chart_of_accounts)

	count = frappe.db.count("Account", {"company": company})
	check("Konten aus SKR angelegt", count > 100, f"{count} Konten")
	numbers = SKR_ACCOUNTS[spec["chart_of_accounts"]]
	accounts = {key: account_by_number(company, number) for key, number in numbers.items()}
	for key, number in numbers.items():
		check(f"Konto {number} ({key})", accounts[key], accounts[key] or "fehlt")
	check(
		"Standard-Erlöskonto",
		data.default_income_account == accounts["erloese19"],
		data.default_income_account or "leer",
	)

	begin, _end = fiscal_year_dates(spec.get("fiscal_year_start", "01-01"))
	from erpnext.accounts.utils import get_fiscal_year

	fy = get_fiscal_year(begin, company=company, raise_on_missing=False)
	check("Geschäftsjahr", fy, fy[0] if fy else "fehlt")

	taxes = cfg.get("taxes") or {}
	if taxes:
		for doctype, title, keys in (
			("Sales Taxes and Charges Template", taxes.get("default_sales_template"), ("ust19", "ust7")),
			(
				"Purchase Taxes and Charges Template",
				taxes.get("default_purchase_template"),
				("vst19", "vst7"),
			),
		):
			name = frappe.db.get_value(doctype, {"company": company, "title": title, "is_default": 1})
			own = (taxes.get("sales_templates") or {}).get(title) if doctype.startswith("Sales") else None
			if own:
				keys = (own["account"],)
			if check(f"{doctype} Standard {title!r}", name, name or "fehlt"):
				rows = frappe.get_all(
					doctype.replace(" Template", ""), {"parent": name}, ["account_head", "rate"]
				)
				heads = {r.account_head for r in rows}
				for key in keys:
					check(f"{name}: Konto {numbers[key]}", accounts[key] in heads)
				if own:
					check(
						f"{name}: genau eine Zeile mit {own['rate']} %",
						len(rows) == 1 and flt(rows[0].rate) == flt(own["rate"]),
						", ".join(f"{r.account_head} {r.rate}" for r in rows),
					)
		for title, key, rate in (("19 %", "ust19", 19), ("7 %", "ust7", 7)):
			name = frappe.db.get_value("Item Tax Template", {"company": company, "title": title})
			if check(f"Item Tax Template {title}", name, name or "fehlt"):
				got = frappe.db.get_value(
					"Item Tax Template Detail", {"parent": name, "tax_type": accounts[key]}, "tax_rate"
				)
				check(f"{name}: {numbers[key]} = {rate} %", got is not None and float(got) == rate, str(got))
		if "add_taxes_from_item_tax_template" in taxes:
			got = cint(frappe.db.get_single_value("Accounts Settings", "add_taxes_from_item_tax_template"))
			check(
				"Accounts Settings add_taxes_from_item_tax_template",
				got == cint(taxes["add_taxes_from_item_tax_template"]),
				str(got),
			)
		default_title = taxes.get("default_item_tax_template")
		if default_title:
			template = frappe.db.get_value("Item Tax Template", {"company": company, "title": default_title})
			root = get_root_of("Item Group")
			check(
				f"Artikelgruppe {root}: Standard-Artikelsteuer {default_title}",
				template and frappe.db.exists("Item Tax", {"parent": root, "item_tax_template": template}),
			)

	return_series = json.loads(frappe.db.get_default(RETURN_SERIES_DEFAULT_KEY) or "{}")
	for doctype, series in (cfg.get("naming_series") or {}).items():
		if not frappe.db.exists("DocType", doctype):
			continue
		field = frappe.get_meta(doctype).get_field("naming_series")
		options = [o.strip() for o in (field.options or "").split("\n") if o.strip()]
		check(f"Nummernkreis {doctype}", field.default == series["series"], f"Standard {field.default}")
		if series.get("return_series"):
			check(
				f"Nummernkreis {doctype} (Rückgabe)",
				series["return_series"] in options and return_series.get(doctype) == series["return_series"],
			)

	for role, role_spec in (cfg.get("roles") or {}).items():
		check(f"Rolle {role}", frappe.db.exists("Role", role))
		profile_roles = set(
			frappe.get_all("Has Role", {"parent": role, "parenttype": "Role Profile"}, pluck="role")
		)
		check(f"Rollenprofil {role}", role in profile_roles, ", ".join(sorted(profile_roles)))
		for doctype, perm in (role_spec.get("permissions") or {}).items():
			if not frappe.db.exists("DocType", doctype):
				continue
			rights = _rights(cfg, perm)
			if not frappe.get_meta(doctype).is_submittable:
				rights = [r for r in rights if r not in SUBMIT_RIGHTS]
			row = frappe.db.get_value(
				"Custom DocPerm",
				{"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
				list(rights),
				as_dict=True,
			)
			missing = [r for r in rights if not (row and cint(row.get(r)))]
			check(
				f"Rechte {role} auf {doctype}", not missing, f"fehlt: {', '.join(missing)}" if missing else ""
			)

	for wh in cfg.get("warehouses") or []:
		name = frappe.db.get_value("Warehouse", {"company": company, "warehouse_name": wh["warehouse_name"]})
		check(f"Lager {wh['warehouse_name']}", name, name or "fehlt")
		if cint(wh.get("is_default")):
			check("Standardlager", data.default_warehouse == name, data.default_warehouse or "leer")

	if cfg.get("roles"):
		from corevatis_erpnext.angebot.felder import feldrechte_fehlend

		fehlend = feldrechte_fehlend()
		check("Feldrechte Kalkulation (Ebene 2)", not fehlend, "; ".join(fehlend))

	if cfg.get("letter_head") is not None:
		name = _value(cfg["letter_head"].get("letter_head_name")) or company
		check("Briefkopf", frappe.db.exists("Letter Head", name), name)
		check("Briefkopf Standard der Firma", data.default_letter_head, data.default_letter_head or "leer")
		source = frappe.db.get_value("Letter Head", name, "source")
		check("Briefkopf Quelle HTML", source == "HTML", source or "fehlt")
		site_default = frappe.db.get_value("Letter Head", {"is_default": 1})
		check(
			"Briefkopf Site-Standard",
			site_default == name or (site_default and site_default not in ERPNEXT_LETTER_HEADS),
			site_default or "keiner",
		)

	if cfg.get("address_template_germany"):
		template = frappe.db.get_value("Address Template", "Germany", "template") or ""
		check("Adressvorlage Deutschland", "pincode" in template and "city" in template)

	for doctype, print_format in (cfg.get("print_formats") or {}).items():
		if frappe.db.exists("Print Format", print_format):
			current = frappe.get_meta(doctype).default_print_format
			check(
				f"Druckformat {doctype}",
				current == print_format
				or (
					current
					and _is_custom_print_format(current)
					and current not in (cfg.get("print_formats_replace") or {}).get(doctype, ())
				),
				current or "leer",
			)

	for field, value in (cfg.get("print_settings") or {}).items():
		got = frappe.db.get_single_value("Print Settings", field)
		check(f"Druckeinstellung {field}", str(got) == str(value), str(got))

	angebot = cfg.get("angebot") or {}
	if angebot:
		from corevatis_erpnext.angebot.textbausteine import TEXTBAUSTEINE

		for titel in TEXTBAUSTEINE:
			check(f"Textbaustein {titel}", frappe.db.exists("Terms and Conditions", titel))
		if angebot.get("default_selling_terms"):
			got = frappe.db.get_value("Company", company, "default_selling_terms")
			check("Zahlungsbedingungen der Firma", bool(got), got or "leer")
	for name, values in (cfg.get("workspaces") or {}).items():
		if frappe.db.exists("Workspace", name):
			for field, value in values.items():
				got = cint(frappe.db.get_value("Workspace", name, field))
				check(f"Arbeitsbereich {name} {field}", got == cint(value), str(got))

	settings = frappe.get_single("System Settings")
	for field, value in (cfg.get("system_settings") or {}).items():
		check(
			f"Systemeinstellung {field}",
			str(settings.get(field) or "") == str(value),
			str(settings.get(field)),
		)
	for field, value in (cfg.get("global_defaults") or {}).items():
		got = frappe.db.get_single_value("Global Defaults", field)
		check(f"Globaler Standard {field}", str(got) == str(value), str(got))

	if frappe.db.exists("DocType", "ERPNext Germany Settings"):
		for field, value in (cfg.get("erpnext_germany") or {}).items():
			got = frappe.db.get_single_value("ERPNext Germany Settings", field)
			check(f"erpnext_germany {field}", cint(got) == cint(value), str(got))

	return _verify_result(checks)


def _verify_result(checks: list[dict]) -> dict:
	failed = [f"{c['check']}: {c['detail']}" if c["detail"] else c["check"] for c in checks if not c["ok"]]
	return {"ok": not failed, "failed": failed, "checks": checks, "count": len(checks)}
