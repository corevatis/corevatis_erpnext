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

# Installation
# ------------

after_install = "corevatis_erpnext.setup.install.after_install"
# Custom Fields und Fachrollen (ab 1.4 auch der Workflow) nach jedem migrate - idempotent
after_migrate = ["corevatis_erpnext.setup.install.after_migrate"]

# Desk-Formulare
# --------------
# lead.js: Symptom-Checkboxen schreiben live in cv_symptome (1.1), Verlustgrund-Dialog
# vor der Workflow-Aktion „Als verloren markieren“ (1.4)
doctype_js = {
	"Lead": [
		"public/js/lead.js",
	],
}

# Document Events
# ---------------
# Gutschriften (Sales Invoice mit is_return) bekommen den Rückgabe-Nummernkreis aus
# dem Site-Template (z. B. GS-.YYYY.-), solange die Standardserie gewählt ist.
doc_events = {
	# Prozess 1 (Epic 1): Symptom-Checkboxen und cv_symptome abgleichen (1.1),
	# „Verloren“ nur mit Verlustgrund (1.4)
	"Lead": {
		"validate": [
			"corevatis_erpnext.anfrage.lead.sync_symptome",
			"corevatis_erpnext.anfrage.lead.validate_verlustgrund",
		],
		# Web Form „Kostenlose Einschätzung“ (1.2): utm_source Website, cv_quelle Web Form
		"before_insert": "corevatis_erpnext.webform.einschaetzung.before_insert_lead",
	},
	"Sales Invoice": {
		"before_insert": "corevatis_erpnext.setup.naming.set_return_naming_series",
	},
}

# Web Form „Kostenlose Einschätzung“ (1.2): Honeypot, Datenschutz, Rate-Limit und private
# Fotos. Andere Web Forms reicht die Funktion unverändert an Frappe weiter.
override_whitelisted_methods = {
	"frappe.website.doctype.web_form.web_form.accept": "corevatis_erpnext.webform.einschaetzung.accept",
}

# Weitere Hooks (doc_events an Quotation/Sales Order/Sales Invoice, fixtures,
# Custom Fields) folgen mit den Fachaufgaben (Epics 2, 4, 5, 7).
