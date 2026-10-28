"""Installations-Hooks (``hooks.after_install``)."""


def after_install() -> None:
	"""Nach ``bench install-app corevatis_erpnext``.

	Bewusst leer: das Site-Template (Aufgabe 0.4) richtet ``corevatis_erpnext.setup``
	ein. Der Hook existiert, damit der Installationstest der CI den Hook-Pfad mitprüft.
	"""
