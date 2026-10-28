"""Installations-Hooks (``hooks.after_install``, ``hooks.after_migrate``)."""


def after_install() -> None:
	"""Nach ``bench install-app corevatis_erpnext``.

	Legt nur app-eigene, kundenneutrale Objekte an (Custom Fields am Lead, Fachrollen;
	``corevatis_erpnext.anfrage``; Web Form „Kostenlose Einschätzung“ (1.2): UTM Source
	„Website“, Lead-Dubletten per E-Mail erlaubt - ``corevatis_erpnext.webform.einschaetzung.setup``,
	auf bestehenden Sites einmalig per Patch ``v0_2.webform_einschaetzung``). Das Site-Template
	(``corevatis_erpnext.setup.apply_site_template``) braucht kundenspezifische Werte
	(Firma, Kontenrahmen) und läuft deshalb explizit per ``bench execute`` bzw. aus dem
	Onboarding (0.7) - nicht bei jeder Installation und nicht bei jedem ``migrate``.
	"""
	from corevatis_erpnext.anfrage import ensure_anfrage_setup
	from corevatis_erpnext.crm.setup import ensure_custom_fields
	from corevatis_erpnext.webform.einschaetzung import setup as setup_einschaetzung

	ensure_anfrage_setup()
	# nach den Feldern aus 1.1: cv_termin/cv_galerie hängen sich an deren Layout (1.3/1.5)
	ensure_custom_fields()
	setup_einschaetzung()


def after_migrate() -> None:
	"""Nach jedem ``bench migrate``: dieselben idempotenten Schritte wie bei der Installation."""
	from corevatis_erpnext.anfrage import ensure_anfrage_setup
	from corevatis_erpnext.crm.setup import ensure_custom_fields

	ensure_anfrage_setup()
	# nach den Feldern aus 1.1: cv_termin/cv_galerie hängen sich an deren Layout (1.3/1.5)
	ensure_custom_fields()
