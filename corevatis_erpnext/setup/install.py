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
	# Katalog (2.3): Kostenkomponente der Workstation „Montage“ (kein Data Import möglich)
	from corevatis_erpnext.katalog.importer import ensure_grundlagen

	ensure_grundlagen()


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
	# Katalog (2.3): Kostenkomponente „Lohn Montage“ für die Workstation-Vorlage
	from corevatis_erpnext.katalog.importer import ensure_grundlagen

	ensure_grundlagen()
