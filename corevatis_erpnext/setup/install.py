"""Installations-Hooks (``hooks.after_install``, ``hooks.after_migrate``)."""


def after_install() -> None:
	"""Nach ``bench install-app corevatis_erpnext``.

	Legt nur app-eigene, kundenneutrale Objekte an (Custom Fields am Lead, Fachrollen;
	``corevatis_erpnext.anfrage``; Web Form „Kostenlose Einschätzung“ (1.2): UTM Source
	„Website“ - ``corevatis_erpnext.webform.einschaetzung.setup``; Email Template der
	Terminbestätigung (1.3) - ``corevatis_erpnext.crm.email_vorlagen``). Das Site-Template
	(``corevatis_erpnext.setup.apply_site_template``) braucht kundenspezifische Werte
	(Firma, Kontenrahmen) und läuft deshalb explizit per ``bench execute`` bzw. aus dem
	Onboarding (0.7) - nicht bei jeder Installation und nicht bei jedem ``migrate``.
	"""
	from corevatis_erpnext.anfrage import ensure_anfrage_setup
	from corevatis_erpnext.crm.email_vorlagen import ensure_email_templates
	from corevatis_erpnext.crm.setup import ensure_custom_fields
	from corevatis_erpnext.katalog.item_felder import ensure_item_fields
	from corevatis_erpnext.vor_ort.lead_objekt import ensure_lead_objekt_field
	from corevatis_erpnext.webform.datenschutz import ensure_datenschutz_seite
	from corevatis_erpnext.webform.einschaetzung import setup as setup_einschaetzung

	ensure_anfrage_setup()
	# nach den Feldern aus 1.1: cv_termin/cv_galerie hängen sich an deren Layout (1.3/1.5)
	ensure_custom_fields()
	setup_einschaetzung()
	ensure_email_templates()
	# Platzhalter /datenschutz für den Link im Web Form (nur wenn die Route frei ist)
	ensure_datenschutz_seite()
	# Lead.cv_objekt (2.1), nur wenn corevatis (Doctype Objekt) schon installiert ist
	ensure_lead_objekt_field()
	# Item: Gewerk, Verfahren, Leistungsregel (2.2)
	ensure_item_fields()
	# Angebot (4.1): Felder an Quotation/Quotation Item (Aufmaß, Objekt, Kalkulation, Herkunft)
	from corevatis_erpnext.angebot.felder import ensure_angebot_felder

	ensure_angebot_felder()
	# Katalog (2.3): Kostenkomponente der Workstation „Montage“ (kein Data Import möglich)
	from corevatis_erpnext.katalog.importer import ensure_grundlagen

	ensure_grundlagen()
	# Angebotsdruck (4.3): Textbausteine anlegen, vorhandene bleiben unverändert
	from corevatis_erpnext.angebot.textbausteine import ensure_textbausteine

	ensure_textbausteine()
	# Textbausteine im Desk (P2.6): Felder, Rechte, Einordnung, Mahntexte, Anschreiben
	from corevatis_erpnext.textbausteine import ensure_setup as ensure_textbausteine_desk

	ensure_textbausteine_desk()

	ensure_angebot_setup()
	# Zeiterfassung (3.9): Activity Types Arbeitszeit/Fahrzeit/Pause, Timesheet Detail.cv_sync_key
	from corevatis_erpnext.zeiterfassung import ensure_setup as ensure_zeiterfassung

	ensure_zeiterfassung()
	# Materialentnahme (3.11): Employee.cv_fahrzeuglager, Stock Entry cv_task/cv_mitarbeiter/cv_sync_key
	from corevatis_erpnext.material import ensure_setup as ensure_material

	ensure_material()
	ensure_planung_setup()
	# Mitarbeiter (P2.5): Employee.cv_urlaubsanspruch, cv_urlaub_uebertrag, cv_saldo_uebertrag
	ensure_mitarbeiter()
	# Materialwirtschaft (P2.2): Rechte Büro/Meister auf Lieferant, Bestellung, Lieferung; Nachbestellautomatik aus
	ensure_materialwirtschaft()

	ensure_prozess()
	ensure_abnahme()
	# Dokumentenmappe (Epic D): Ampel am Projekt, Übersteuerung an Angebot und Rechnung
	ensure_mappe()
	ensure_abrechnung()
	ensure_datev()
	ensure_kundenportal()


def after_migrate() -> None:
	"""Nach jedem ``bench migrate``: dieselben idempotenten Schritte wie bei der Installation."""
	from corevatis_erpnext.anfrage import ensure_anfrage_setup
	from corevatis_erpnext.crm.email_vorlagen import ensure_email_templates
	from corevatis_erpnext.crm.setup import ensure_custom_fields
	from corevatis_erpnext.katalog.item_felder import ensure_item_fields
	from corevatis_erpnext.vor_ort.lead_objekt import ensure_lead_objekt_field
	from corevatis_erpnext.webform.datenschutz import ensure_datenschutz_seite

	ensure_anfrage_setup()
	# nach den Feldern aus 1.1: cv_termin/cv_galerie hängen sich an deren Layout (1.3/1.5)
	ensure_custom_fields()
	# Terminbestätigung: anlegen/aktualisieren, Änderungen aus dem Desk bleiben (E-1)
	ensure_email_templates()
	# Platzhalter /datenschutz (1.2), legt nichts an, wenn unter der Route schon eine Seite liegt
	ensure_datenschutz_seite()
	# Lead.cv_objekt (2.1), nur wenn corevatis (Doctype Objekt) installiert ist
	ensure_lead_objekt_field()
	# Item: Gewerk, Verfahren, Leistungsregel (2.2)
	ensure_item_fields()
	# Angebot (4.1): Felder an Quotation/Quotation Item
	from corevatis_erpnext.angebot.felder import ensure_angebot_felder

	ensure_angebot_felder()
	# Katalog (2.3): Kostenkomponente „Lohn Montage“ für die Workstation-Vorlage
	from corevatis_erpnext.katalog.importer import ensure_grundlagen

	ensure_grundlagen()
	# Angebotsdruck (4.3): Textbausteine anlegen, vorhandene bleiben unverändert
	from corevatis_erpnext.angebot.textbausteine import ensure_textbausteine

	ensure_textbausteine()
	# Textbausteine im Desk (P2.6): Felder, Rechte, Einordnung, Mahntexte, Anschreiben
	from corevatis_erpnext.textbausteine import ensure_setup as ensure_textbausteine_desk

	ensure_textbausteine_desk()

	# Angebotsannahme/Nachfassen (4.4/4.5) zuletzt: der Spiegel Quotation -> Sales Order
	# übernimmt die cv_*-Felder, die andere Schritte am Angebot angelegt haben
	ensure_angebot_setup()
	# Zeiterfassung (3.9): Activity Types Arbeitszeit/Fahrzeit/Pause, Timesheet Detail.cv_sync_key
	from corevatis_erpnext.zeiterfassung import ensure_setup as ensure_zeiterfassung

	ensure_zeiterfassung()
	# Planung (5.1): Task/Material Request cv_auftrag, cv_verfahren, cv_sollstunden, cv_positionen
	ensure_planung_setup()

	# Materialentnahme (3.11): Employee.cv_fahrzeuglager, Stock Entry cv_task/cv_mitarbeiter/cv_sync_key
	from corevatis_erpnext.material import ensure_setup as ensure_material

	ensure_material()
	# Mitarbeiter (P2.5): Employee.cv_urlaubsanspruch, cv_urlaub_uebertrag, cv_saldo_uebertrag
	ensure_mitarbeiter()
	# Materialwirtschaft (P2.2): Rechte Büro/Meister auf Lieferant, Bestellung, Lieferung; Nachbestellautomatik aus
	ensure_materialwirtschaft()
	# Abnahme (6.1/6.2): Task/Project/Sales Invoice-Felder
	ensure_abnahme()
	# Dokumentenmappe (Epic D): Ampel am Projekt, Übersteuerung an Angebot und Rechnung
	ensure_mappe()
	# Abrechnung (7.1/7.2): Sales-Invoice-Felder nach den Abnahme-Feldern, Abschlagsartikel, negative Preise
	ensure_abrechnung()
	# DATEV (7.4): Markierung an Rechnung/Zahlung (hängt sich hinter die Abrechnungsfelder), DATEV Settings
	ensure_datev()
	# Kundenportal (P2.7): Felder an Termin, Angebot, Datei; Textbaustein „Kundenportal Begrüßung“
	ensure_kundenportal()
	# Stundensatz (Handwerker#104): Workstation „Montage“ aus dem Standard-Kalkulationsschema
	from corevatis_erpnext.angebot.stundensatz import ensure_abgleich

	ensure_abgleich()


def ensure_kundenportal() -> None:
	"""Kundenportal (P2.7): Felder an Event, Quotation, File; Textbaustein der Art „Kundenportal“."""
	from corevatis_erpnext.kundenportal import ensure_setup as ensure_kundenportal_setup

	ensure_kundenportal_setup()


def ensure_mitarbeiter() -> None:
	"""Urlaub und Stundenkonto (P2.5): Felder am Employee hinter dem Fahrzeuglager."""
	from corevatis_erpnext.mitarbeiter import ensure_setup as ensure_mitarbeiter_setup

	ensure_mitarbeiter_setup()


def ensure_materialwirtschaft() -> None:
	from corevatis_erpnext.materialwirtschaft import ensure_setup as ensure_materialwirtschaft_setup

	ensure_materialwirtschaft_setup()


def ensure_abrechnung() -> None:
	from corevatis_erpnext.abrechnung import ensure_abrechnung_setup

	ensure_abrechnung_setup()


def ensure_datev() -> None:
	from corevatis_erpnext.datev import ensure_datev_setup

	ensure_datev_setup()


def ensure_abnahme() -> None:
	from corevatis_erpnext.abnahme.felder import ensure_abnahme_felder

	ensure_abnahme_felder()


def ensure_mappe() -> None:
	"""Felder der Dokumentenmappe - nach den Abnahmefeldern (sie hängen sich dahinter)."""
	from corevatis_erpnext.mappe.felder import ensure_mappe_felder

	ensure_mappe_felder()


def ensure_planung_setup() -> None:
	from corevatis_erpnext.planung.felder import ensure_planung_felder

	ensure_planung_felder()

	ensure_prozess()


def ensure_prozess() -> None:
	"""Prozessleiste: Kennzahlen und Blöcke der Arbeitsbereiche „Vorgänge“/„Meine Baustellen“,
	Lead-Formular aufräumen (``corevatis_erpnext.prozess.setup``)."""
	from corevatis_erpnext.prozess.desk import ensure_desk
	from corevatis_erpnext.prozess.setup import ensure_prozess_setup

	ensure_prozess_setup()
	# Durchstich D1 bis D3: Startseiten je Rolle, Modulsperren für Benutzer mit Fachrolle
	ensure_desk()
	# Durchstich A2: Benutzer mit Rolle Monteur/Meister bekommen automatisch einen Mitarbeiter
	from corevatis_erpnext.prozess.mitarbeiter import (
		ensure_meister_ohne_benutzerberechtigung,
		ensure_mitarbeiter_bestand,
		ensure_mitarbeiter_felder_geschuetzt,
	)

	ensure_mitarbeiter_bestand()
	# Abnahme Build 33: der Meister plant für das Team, ERPNexts User Permission auf den eigenen Employee stört
	ensure_meister_ohne_benutzerberechtigung()
	# Issue #72: persönliche Mitarbeiterfelder nur für System Manager und HR Manager
	ensure_mitarbeiter_felder_geschuetzt()


def ensure_angebot_setup() -> None:
	from corevatis_erpnext.anfrage.setup import ensure_roles
	from corevatis_erpnext.angebot.annahme import ensure_projekttyp, ensure_verlustgruende
	from corevatis_erpnext.angebot.annahme_felder import ensure_felder

	ensure_roles()
	ensure_felder()
	ensure_projekttyp()
	ensure_verlustgruende()
