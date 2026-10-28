# Changelog

Alle nennenswerten Änderungen dieser App. Versionierung nach
[Semantic Versioning](https://semver.org/lang/de/); ein Release entsteht durch
ein Tag `vX.Y.Z`, das zur Version in `corevatis_erpnext/__init__.py` passt.

## [0.1.0] – 2026-10-28

### Hinzugefügt

- App-Skelett für Frappe/ERPNext v16 (Python 3.14): Modul `Corevatis ERPNext`,
  Hooks mit `required_apps = ["erpnext"]`, Übersetzungsdatei `translations/de.csv`.
- Platzhalter-Paket `corevatis_erpnext.setup` für das Site-Template (Aufgabe 0.4).
- CI: ruff, import-linter (kein Python-Import aus `corevatis`), frische Bench mit
  ERPNext `version-16`, `bench run-tests`, Lizenzliste; Release-Workflow für Tags `v*`.
