app_name = "corevatis_erpnext"
app_title = "Corevatis ERPNext"
app_publisher = "Corevatis"
app_description = (
	"ERPNext-Adapter für Corevatis: Custom Fields, Hooks, Generatoren und Print Formats an ERPNext-Belegen"
)
app_email = ""
app_license = "GPL-3.0-only"

# Apps
# ------------------
# Adapter zwischen der Kern-App corevatis und ERPNext v16. Ruft den Kern nur
# über dessen whitelisted API (corevatis.api.*) auf — keine Python-Imports aus
# corevatis, keine gemeinsamen Datenklassen (docs/mvp-plan.md, Lizenzarchitektur).
required_apps = ["erpnext"]

# Kachel auf dem Startbildschirm (/desk): Einstieg „Anfragen“ (Befunde mvp-test: der Auftraggeber
# fand „kein CRM“ - v16 zeigt nur Apps mit diesem Hook, der Arbeitsbereich war nur per URL erreichbar)
add_to_apps_screen = [
	{
		"name": app_name,
		"logo": "/assets/corevatis_erpnext/images/anfragen.svg",
		"title": "Anfragen",
		"route": "/app/anfragen",
		"has_permission": "corevatis_erpnext.anfrage.setup.hat_anfragen_zugriff",
	}
]

# Installation
# ------------

after_install = "corevatis_erpnext.setup.install.after_install"
# Custom Fields und Fachrollen (ab 1.4 auch der Workflow) nach jedem migrate - idempotent
after_migrate = ["corevatis_erpnext.setup.install.after_migrate"]
# Kern-App corevatis nachträglich installiert/entfernt: Lead.cv_objekt anlegen/entfernen (2.1)
after_app_install = "corevatis_erpnext.vor_ort.lead_objekt.after_app_install"
before_app_uninstall = "corevatis_erpnext.vor_ort.lead_objekt.before_app_uninstall"

# Fixtures
# --------
# Keine. Das Email Template der Terminbestätigung (1.3) war bis 0.2 eine Fixture und wurde
# bei jedem migrate überschrieben; jetzt legt crm.email_vorlagen.ensure_email_templates es
# in after_install/after_migrate an und behält Änderungen aus dem Desk (Review Epic 1, E-1).

# Desk-Formulare
# --------------
# lead.js: Symptom-Checkboxen schreiben live in cv_symptome (1.1), Verlustgrund-Dialog
# vor der Workflow-Aktion „Als verloren markieren“ (1.4); galerie.js stellt
# corevatis_erpnext.galerie(frm) bereit (wiederverwendbar, z. B. für Objekt aus 2.1),
# lead_galerie.js hängt sie am Lead ein (1.5), lead_termin.js bringt „Termin anlegen“ (1.3)
doctype_js = {
	"Lead": [
		"public/js/lead.js",
		"public/js/galerie.js",
		"public/js/lead_galerie.js",
		"public/js/lead_termin.js",
	],
	# Angebot aus dem Aufmaß (4.1): Doctype Aufmass aus corevatis, ohne Kern lädt es nie
	"Aufmass": "public/js/aufmass_angebot.js",
}

# Verbindungen: Angebote am Objekt und am Aufmaß (Kern-Doctypes, Quotation.cv_objekt/cv_aufmass);
# am Lead zeigt ERPNext die Angebote schon (party_name)
override_doctype_dashboards = {
	"Objekt": "corevatis_erpnext.angebot.generator.objekt_dashboard",
	"Aufmass": "corevatis_erpnext.angebot.generator.aufmass_dashboard",
}

# Document Events
# ---------------
# Gutschriften (Sales Invoice mit is_return) bekommen den Rückgabe-Nummernkreis aus
# dem Site-Template (z. B. GS-.YYYY.-), solange die Standardserie gewählt ist.
doc_events = {
	# Prozess 1 (Epic 1): Symptom-Checkboxen und cv_symptome abgleichen (1.1),
	# „Verloren“ nur mit Verlustgrund (1.4), cv_termin nur über „Termin anlegen“ (1.3)
	"Lead": {
		"validate": [
			"corevatis_erpnext.anfrage.lead.sync_symptome",
			"corevatis_erpnext.anfrage.lead.validate_verlustgrund",
			"corevatis_erpnext.crm.termin.lead_validate",
		],
		# Web Form „Kostenlose Einschätzung“ (1.2): utm_source Website, cv_quelle Web Form
		"before_insert": "corevatis_erpnext.webform.einschaetzung.before_insert_lead",
	},
	# Leistungskatalog (2.2): cv_leistungsregel prüfen, wenn corevatis installiert ist
	"Item": {
		"validate": "corevatis_erpnext.katalog.item.validate",
	},
	"Sales Invoice": {
		"before_insert": "corevatis_erpnext.setup.naming.set_return_naming_series",
	},
	# Befunde mvp-test: Hinweis auf Positionen ohne Preis (falsche Preisliste)
	"Quotation": {
		"validate": "corevatis_erpnext.angebot.pruefung.positionen_ohne_preis",
	},
	# Befunde mvp-test: Objekt eines Leads am Lead eintragen (Lead.cv_objekt, 2.1);
	# der Doctype Objekt kommt aus corevatis - ohne Kern greifen die Hooks nie
	"Objekt": {
		"validate": "corevatis_erpnext.vor_ort.lead_objekt.objekt_kunde_pruefen",
		"after_insert": "corevatis_erpnext.vor_ort.lead_objekt.objekt_an_lead",
		"on_update": "corevatis_erpnext.vor_ort.lead_objekt.objekt_an_lead",
	},
}

# Web Form „Kostenlose Einschätzung“ (1.2): Honeypot, Datenschutz, Rate-Limit und private
# Fotos. Andere Web Forms reicht die Funktion unverändert an Frappe weiter.
override_whitelisted_methods = {
	"frappe.website.doctype.web_form.web_form.accept": "corevatis_erpnext.webform.einschaetzung.accept",
}

# Weitere Hooks (doc_events an Quotation/Sales Order/Sales Invoice, fixtures,
# Custom Fields) folgen mit den Fachaufgaben (Epics 2, 4, 5, 7).
