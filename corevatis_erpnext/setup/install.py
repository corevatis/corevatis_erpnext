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

	ensure_angebot_setup()
	# Zeiterfassung (3.9): Activity Types Arbeitszeit/Fahrzeit/Pause, Timesheet Detail.cv_sync_key
	from corevatis_erpnext.zeiterfassung import ensure_setup as ensure_zeiterfassung

	ensure_zeiterfassung()
	ensure_planung_setup()


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

	# Angebotsannahme/Nachfassen (4.4/4.5) zuletzt: der Spiegel Quotation -> Sales Order
	# übernimmt die cv_*-Felder, die andere Schritte am Angebot angelegt haben
	ensure_angebot_setup()
	# Zeiterfassung (3.9): Activity Types Arbeitszeit/Fahrzeit/Pause, Timesheet Detail.cv_sync_key
	from corevatis_erpnext.zeiterfassung import ensure_setup as ensure_zeiterfassung

	ensure_zeiterfassung()
	# Planung (5.1): Task/Material Request cv_auftrag, cv_verfahren, cv_sollstunden, cv_positionen
	ensure_planung_setup()


def ensure_planung_setup() -> None:
	from corevatis_erpnext.planung.felder import ensure_planung_felder

	ensure_planung_felder()


def ensure_angebot_setup() -> None:
	from corevatis_erpnext.anfrage.setup import ensure_roles
	from corevatis_erpnext.angebot.annahme import ensure_projekttyp, ensure_verlustgruende
	from corevatis_erpnext.angebot.annahme_felder import ensure_felder

	ensure_roles()
	ensure_felder()
	ensure_projekttyp()
	ensure_verlustgruende()
