"""Installations-Hooks (``hooks.after_install``)."""


def after_install() -> None:
	"""Nach ``bench install-app corevatis_erpnext``.

	Bewusst leer: Das Site-Template (``corevatis_erpnext.setup.apply_site_template``)
	braucht kundenspezifische Werte (Firma, Kontenrahmen) und läuft deshalb explizit
	per ``bench execute`` bzw. später aus dem Onboarding (0.7) - nicht bei jeder
	Installation und nicht bei jedem ``migrate``. Der Hook existiert, damit der
	Installationstest der CI den Hook-Pfad mitprüft.
	"""
