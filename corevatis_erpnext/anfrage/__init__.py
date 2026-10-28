"""Prozess 1 „Anfrage und CRM“ (Epic 1): Gewerkefelder am Lead, Workspace „Anfragen“.

Einstieg ``ensure_anfrage_setup()`` läuft nach ``install-app`` und nach jedem
``migrate`` (``hooks.after_install``/``hooks.after_migrate``) und ist idempotent.
Feldnamen und Optionen: ``custom_fields.py`` und ``docs/1.1-lead-felder.md`` im Repo
Handwerker.
"""


def ensure_anfrage_setup() -> None:
	from corevatis_erpnext.anfrage.setup import ensure_anfrage_setup as _ensure

	_ensure()
