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

# Desk-Assets nur als Bundles (public/**/*.bundle.*): Frappe hängt beim Build einen Inhalts-Hash an den
# Dateinamen und löst die Namen über assets.json auf. Pfade wie /assets/<app>/js/x.js liefert nginx mit
# „immutable, max-age 1 Jahr“ aus, Cloudflare und Browser behalten alte Stände (mvp-test, 31.10.2026:
# der Ungelesen-Zähler der Leiste war nach zwei Deploys noch unsichtbar).
# Durchstich D4: Nummernkreis-Wähler in allen Formularen ausblenden (public/css/betrieb.bundle.css)
app_include_css = "betrieb.bundle.css"
# Durchstich D1: Links im Desk bleiben in der Leiste „Betrieb“; Ungelesen-Zähler (public/js/betrieb.bundle.js)
app_include_js = "betrieb.bundle.js"

# Kachel auf dem Startbildschirm (/desk). Befunde mvp-test: der Auftraggeber fand „kein CRM“ -
# v16 zeigt nur Apps mit diesem Hook und davon nur den ersten Eintrag je App. Prozessleiste: eine
# Kachel „Vorgänge“; /cv_start leitet Büro/Meister auf den Arbeitsbereich „Vorgänge“ und Monteure
# auf „Meine Baustellen“ (Abnahme mvp-test). „Anfragen“ bleibt als Arbeitsbereich.
add_to_apps_screen = [
	{
		"name": app_name,
		"logo": "/assets/corevatis_erpnext/images/anfragen.svg",
		"title": "Vorgänge",
		"route": "/cv_start",
		"has_permission": "corevatis_erpnext.prozess.setup.hat_startkachel_zugriff",
	},
]

# Installation
# ------------

after_install = "corevatis_erpnext.setup.install.after_install"
# Custom Fields und Fachrollen (ab 1.4 auch der Workflow) nach jedem migrate - idempotent
after_migrate = ["corevatis_erpnext.setup.install.after_migrate"]
# Durchstich D2: Kachel „ERPNext“ auf /desk nur für System Manager (Boot-Daten filtern)
boot_session = ["corevatis_erpnext.prozess.desk.boot_session"]
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
# lead_galerie.js hängt sie am Lead ein (1.5), lead_termin.bundle.js bringt „Termin anlegen“ (1.3)
# Prozessleiste (prozessleiste.js) im Formularkopf aller Belege der Kette; prozess_lead.js räumt
# das Lead-Formular auf (Befunde mvp-test Nr. 21)
_PROZESSLEISTE = "public/js/prozessleiste.js"
doctype_js = {
	"Lead": [
		"public/js/lead.js",
		"public/js/galerie.js",
		"public/js/lead_galerie.js",
		"public/js/lead_termin.bundle.js",
		_PROZESSLEISTE,
		"public/js/prozess_lead.js",
	],
	# Angebot annehmen/ablehnen (4.4), Nachfassen (4.5); Mappenprüfung übersteuern (Epic D)
	"Quotation": ["public/js/quotation_annahme.js", _PROZESSLEISTE, "public/js/mappe_uebersteuern.js"],
	# Angebot aus dem Aufmaß (4.1): Doctype Aufmass aus corevatis, ohne Kern lädt es nie
	"Aufmass": ["public/js/aufmass_angebot.js", _PROZESSLEISTE],
	# Foto-Galerie am Befund (3.7): Fotos der App; Doctype aus corevatis, ohne Kern lädt es nie
	"Befund": ["public/js/galerie.js", "public/js/befund_galerie.js", _PROZESSLEISTE],
	# Arbeitspakete und Materialbedarf erzeugen (5.1), am Projekt Button „Wochenplan öffnen“
	# (5.2; öffnet die Plantafel P1.4 mit Projektfilter)
	# Abrechnung (7.1/7.2): „Abschlag stellen“, „Schlussrechnung erstellen“ an Auftrag und Projekt
	"Sales Order": ["public/js/planung.js", "public/js/abrechnung.js", _PROZESSLEISTE],
	"Project": ["public/js/planung.js", "public/js/abrechnung.js", _PROZESSLEISTE],
	# Zuweisung Mitarbeiter (5.2): Abschnitt „Zuweisung“ mit Dropdown; zuweisung.bundle.js ist die
	# Bibliothek (auch vom Wochenplan per frappe.require geladen)
	"Task": ["public/js/zuweisung.bundle.js", "public/js/task_zuweisung.js"],
	"Objekt": [_PROZESSLEISTE],
	# Rechnung: Mappenprüfung übersteuern (Epic D, D.5); Abrechnung (7.1/7.2): Zustandstext,
	# „Rechnung freigeben“, ERPNext-Ballast ausblenden; 7.5: „Mahnen“
	"Sales Invoice": [
		"public/js/abrechnung.js",
		"public/js/mahnung_zahlung.bundle.js",
		_PROZESSLEISTE,
		"public/js/mappe_uebersteuern.js",
	],
	"Abschlagsplan": ["public/js/abrechnung.js"],
	# Mahnwesen und Zahlungseingang (7.5): Dialoge „Mahnen“, „Rechnung zuordnen“, Zustandstext der Mahnung
	"Bank Transaction": ["public/js/mahnung_zahlung.bundle.js"],
	"Dunning": ["public/js/mahnung_zahlung.bundle.js"],
	# Abnahme (6.1): Doctype aus corevatis, ohne Kern lädt es nie; „Mangel-Aufgaben erzeugen“
	"Abnahmeprotokoll": ["public/js/abnahme.js", _PROZESSLEISTE],
}

# Verbindungen: Angebote am Objekt und am Aufmaß (Kern-Doctypes, Quotation.cv_objekt/cv_aufmass);
# am Lead zeigt ERPNext die Angebote schon (party_name)
override_doctype_dashboards = {
	"Objekt": "corevatis_erpnext.angebot.generator.objekt_dashboard",
	"Aufmass": "corevatis_erpnext.angebot.generator.aufmass_dashboard",
}

# Besichtigung in der App bis zum Angebot (Durchstich A1): der Kern ruft Katalog, Generator, Angebote
# und PDF über diesen Hook (corevatis.api.besichtigung, kein Import über die Lizenzgrenze)
corevatis_angebot_adapter = "corevatis_erpnext.angebot.mobil"
# Meine Woche in der App (P1.4b): Einsätze eines Benutzers wie seine Zeile in der Plantafel
# (Kern-Endpunkt corevatis.api.v1.meine_woche ruft woche(user, von, bis))
corevatis_woche_adapter = "corevatis_erpnext.planung.woche"
# Stundenkonto in der App (Phase 2 P2.5): Monat, Saldo Jahr und Urlaubskonto des Benutzers
# (Kern-Endpunkt corevatis.api.v1.mein_stundenkonto ruft fuer_benutzer(user, jahr, monat))
corevatis_stundenkonto_adapter = "corevatis_erpnext.mitarbeiter"

# Termin aus der Anfrage in der App (Durchstich A10): Sync-Entität des Kerns (corevatis.api.sync_schema,
# Hook corevatis_sync_handlers) - legt den Besichtigungstermin wie „Termin anlegen“ im Desk an
corevatis_sync_handlers = {
	"termin_anlegen": "corevatis_erpnext.crm.termin_mobil.termin_anlegen",
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
		# Dokumentenmappe (D.5) zuerst: Pflichtdokumente ab Schritt Rechnung (Abnahmeprotokoll in der
		# Mappe), Übersteuern mit Begründung durch das Büro - danach die Abnahmesperre 6.2
		"validate": [
			# 7.1/7.2: Abrechnungsfelder (Auftrag, Art, Beträge) setzt nur der Server
			"corevatis_erpnext.abrechnung.auftrag.rechnung_validieren",
			"corevatis_erpnext.mappe.rechnung_pruefen",
			"corevatis_erpnext.abnahme.rechnung_pruefen",
		],
		"on_update": "corevatis_erpnext.abnahme.rechnung.rechnung_kommentar",
		# 7.1/7.2: Freigabe einer Abschlags-/Schlussrechnung nur durch das Büro, Positionen zum Auftrag
		"before_submit": "corevatis_erpnext.abrechnung.auftrag.rechnung_freigabe",
		"on_submit": [
			"corevatis_erpnext.abnahme.rechnung.rechnung_kommentar",
			# Dokumentenmappe (D.2): Rechnungs-PDF in Ordner 07 Rechnung
			"corevatis_erpnext.mappe.rechnung_freigegeben",
			# 7.1/7.2: abgerechneter Anteil am Auftrag (per_billed) und Stand des Abschlagsplans
			"corevatis_erpnext.abrechnung.auftrag.rechnung_eingereicht",
		],
		"on_cancel": "corevatis_erpnext.abrechnung.auftrag.rechnung_storniert",
		"on_trash": "corevatis_erpnext.abrechnung.auftrag.rechnung_geloescht",
	},
	# 7.1: Abschlagszeile „bezahlt“, wenn eine Zahlung zur Abschlagsrechnung gebucht/storniert wird
	"Payment Entry": {
		"on_submit": "corevatis_erpnext.abrechnung.auftrag.zahlung_gebucht",
		"on_cancel": "corevatis_erpnext.abrechnung.auftrag.zahlung_gebucht",
	},
	# 7.4 DATEV: neuer Kunde bekommt je Firma mit DATEV-Einstellungen eine Debitorennummer (ab 10000)
	"Customer": {
		"validate": "corevatis_erpnext.datev.debitoren.kunde_validate",
	},
	# Befunde mvp-test: Hinweis auf Positionen ohne Preis (falsche Preisliste)
	# Angebotsdruck (4.3): Textbausteine in leere Felder, Eventual-/Alternativpositionen
	# als is_alternative (nicht in der Summe) - vor der Summenberechnung
	"Quotation": {
		"before_insert": "corevatis_erpnext.angebot.textbausteine.standard_texte",
		"before_validate": "corevatis_erpnext.angebot.textbausteine.kennzeichen_ausserhalb_summe",
		# Review Epic 4 A-2: Eventual-/Alternativpositionen sind kein Ersatz für die Zeile davor;
		# Dokumentenmappe (D.5): Pflichtdokumente ab Schritt Angebot
		"before_submit": [
			"corevatis_erpnext.angebot.textbausteine.alternativ_sets_aufloesen",
			"corevatis_erpnext.mappe.angebot_pruefen",
		],
		"validate": [
			"corevatis_erpnext.angebot.pruefung.positionen_ohne_preis",
			# Security-Review: cv_lead setzt nur der Server
			"corevatis_erpnext.angebot.annahme.cv_lead_schuetzen",
			# Dokumentenmappe (D.5): Übersteuerung nur Büro/System Manager, bei jedem Speichern gesichert
			"corevatis_erpnext.mappe.angebot_validieren",
		],
		# Dokumentenmappe (D.2): „PDF bei Freigabe“ - Angebot Corevatis in Ordner 03 Angebot
		"on_submit": "corevatis_erpnext.mappe.angebot_freigegeben",
		# 4.4: über ERPNext „Als verloren festlegen“ verloren -> Lead „Verloren“
		"on_update_after_submit": "corevatis_erpnext.angebot.annahme.angebot_aktualisiert",
	},
	# 4.4: Projekt, Lead „Gewonnen“ und Angebotsstatus auch beim ERPNext-Weg „Erstellen → Auftrag“;
	# 5.1 danach: Arbeitspakete (Task) und Materialbedarf (Material Request) - braucht das Projekt
	# 7.1 danach: Abschlagsplan aus der Vorlage, wenn der Auftrag ein Projekt hat
	"Sales Order": {
		"on_submit": [
			"corevatis_erpnext.angebot.annahme.auftrag_eingereicht",
			"corevatis_erpnext.planung.auftrag_eingereicht",
			# Dokumentenmappe (D.2): Auftragsbestätigung als PDF in Ordner 04 Auftrag
			"corevatis_erpnext.mappe.auftrag_freigegeben",
			# 7.1: Abschlagsplan aus der Vorlage, wenn der Auftrag ein Projekt hat
			"corevatis_erpnext.abrechnung.plan.auftrag_eingereicht",
		],
	},
	# Befunde mvp-test: Objekt eines Leads am Lead eintragen (Lead.cv_objekt, 2.1);
	# der Doctype Objekt kommt aus corevatis - ohne Kern greifen die Hooks nie
	# Abnahme (6.1/6.2): Mangel-Tasks und Abnahme am Projekt beim Abschluss des Protokolls (Kern-
	# Doctype, ohne Kern greift der Hook nie); erledigter Mangel-Task -> Mangelzeile „Behoben“;
	# Abnahme-/Gewährleistungsfelder am Projekt nur vom Server
	"Abnahmeprotokoll": {
		"validate": "corevatis_erpnext.abnahme.protokoll_pruefen",
		"on_update": "corevatis_erpnext.abnahme.protokoll_aktualisiert",
	},
	# Rückmeldung 31.10.: ERPNext-Arbeitsaufträge (Fertigung) zu Aufträgen mit Projekt abfangen -
	# die Arbeit der Monteure sind die Arbeitspakete (Task) am Projekt
	"Work Order": {
		"before_validate": "corevatis_erpnext.planung.fertigung.arbeitsauftrag_pruefen",
	},
	"Task": {
		"validate": "corevatis_erpnext.abnahme.task_pruefen",
		"on_update": "corevatis_erpnext.abnahme.task_aktualisiert",
	},
	"Project": {
		"validate": "corevatis_erpnext.abnahme.projekt_validieren",
		# 7.1: Abschlagsplan beim Anlegen des Projekts aus der Annahme (4.4)
		"after_insert": "corevatis_erpnext.abrechnung.plan.projekt_angelegt",
	},
	"Objekt": {
		"validate": "corevatis_erpnext.vor_ort.lead_objekt.objekt_kunde_pruefen",
		"after_insert": "corevatis_erpnext.vor_ort.lead_objekt.objekt_an_lead",
		"on_update": "corevatis_erpnext.vor_ort.lead_objekt.objekt_an_lead",
	},
	# Durchstich D1: Benutzer mit Fachrolle sehen im Dock nur die Leiste „Betrieb“
	"User": {
		"validate": "corevatis_erpnext.prozess.desk.user_validate",
		# Durchstich A2: Monteur/Meister bekommen automatisch einen Mitarbeiter (Employee)
		"on_update": "corevatis_erpnext.prozess.mitarbeiter.user_on_update",
	},
}

# Plantafel (P1.4): Meister und Monteur lesen nur ihre eigenen Abwesenheiten (Büro alle)
permission_query_conditions = {
	"Abwesenheit": "corevatis_erpnext.planung.abwesenheit.permission_query_conditions",
}
has_permission = {
	"Abwesenheit": "corevatis_erpnext.planung.abwesenheit.has_permission",
}

# Web Form „Kostenlose Einschätzung“ (1.2): Honeypot, Datenschutz, Rate-Limit und private
# Fotos. Andere Web Forms reicht die Funktion unverändert an Frappe weiter.
override_whitelisted_methods = {
	"frappe.website.doctype.web_form.web_form.accept": "corevatis_erpnext.webform.einschaetzung.accept",
	# Durchstich D2: Kachel „ERPNext“ auf /desk nur für System Manager
	"frappe.apps.get_apps": "corevatis_erpnext.prozess.desk.get_apps",
}

# Scheduled Tasks
# ---------------
# 4.5: Nachfass-Aufgaben (ToDo) für offene Angebote, Einstellungen im Single
# „Corevatis Vertrieb Einstellungen“
scheduler_events = {
	"daily": ["corevatis_erpnext.angebot.nachfassen.taeglich"],
}

# Weitere Hooks (doc_events an Quotation/Sales Order/Sales Invoice, fixtures,
# Custom Fields) folgen mit den Fachaufgaben (Epics 2, 4, 5, 7).
