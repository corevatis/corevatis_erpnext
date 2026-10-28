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
	from corevatis_erpnext.webform.datenschutz import ensure_datenschutz_seite
	from corevatis_erpnext.webform.einschaetzung import setup as setup_einschaetzung

	ensure_anfrage_setup()
	# nach den Feldern aus 1.1: cv_termin/cv_galerie hängen sich an deren Layout (1.3/1.5)
	ensure_custom_fields()
	setup_einschaetzung()
	ensure_email_templates()
	# Platzhalter /datenschutz für den Link im Web Form (nur wenn die Route frei ist)
	ensure_datenschutz_seite()


def after_migrate() -> None:
	"""Nach jedem ``bench migrate``: dieselben idempotenten Schritte wie bei der Installation."""
	from corevatis_erpnext.anfrage import ensure_anfrage_setup
	from corevatis_erpnext.crm.email_vorlagen import ensure_email_templates
	from corevatis_erpnext.crm.setup import ensure_custom_fields
	from corevatis_erpnext.webform.datenschutz import ensure_datenschutz_seite

	ensure_anfrage_setup()
	# nach den Feldern aus 1.1: cv_termin/cv_galerie hängen sich an deren Layout (1.3/1.5)
	ensure_custom_fields()
	# Terminbestätigung: anlegen/aktualisieren, Änderungen aus dem Desk bleiben (E-1)
	ensure_email_templates()
	# Platzhalter /datenschutz (1.2), legt nichts an, wenn unter der Route schon eine Seite liegt
	ensure_datenschutz_seite()
