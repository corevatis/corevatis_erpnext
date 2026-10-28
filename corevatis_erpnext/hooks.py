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

# Weitere Hooks (doc_events an Quotation/Sales Order/Sales Invoice, fixtures,
# Custom Fields) folgen mit den Fachaufgaben (Epics 2, 4, 5, 7).
