# Changelog

Alle nennenswerten Änderungen dieser App. Versionierung nach
[Semantic Versioning](https://semver.org/lang/de/); ein Release entsteht durch
ein Tag `vX.Y.Z`, das zur Version in `corevatis_erpnext/__init__.py` passt.

## [Unveröffentlicht]

### Hinzugefügt (Epic 1)

- 1.4 „Wieder öffnen“ leert den Verlustgrund (Befund Verifikation mvp-test: der alte Grund blieb stehen,
  ein erneutes „Als verloren markieren“ hätte ihn ohne Rückfrage übernommen).
- 1.2 `cv_webform_embedding_domains` auch als Text (Leerzeichen/Komma-getrennt oder als Text abgelegte
  Liste): Press' Site Config speicherte die Liste als `"['http://localhost:8080']"`, die CSP fehlte.
- 1.2 Platzhalterseite `/datenschutz` (Web Page „Datenschutz (Entwurf)“, Hinweis „Entwurf – nicht
  rechtsverbindlich, im Pilotbetrieb ohne Datenschutzerklärung“) für den Link im Web Form; angelegt in
  `after_install`/`after_migrate` nur, wenn `cv_webform_datenschutz_url` nicht gesetzt ist und unter der
  Route noch keine Web Page liegt (vorhandene Seiten bleiben unangetastet).
- 1.3 Termin am Lead: Button „Termin anlegen“ (Datum/Uhrzeit, Dauer, Meister, Notiz) legt ein
  `Event` an (Private mit Freigabe an Meister und Büro, Kategorie Meeting, Lead als Teilnehmer, Zuweisung an den Meister,
  Google-Calendar-Sync, falls der Meister einen Kalender verbunden hat), setzt `Lead.cv_termin`,
  sagt einen vorherigen offenen Termin ab und führt – falls ein Lead-Workflow aktiv ist – den
  Übergang nach „Termin…“ aus. Kundenbestätigung aus dem Email Template
  „Terminbestätigung Besichtigung“ (Fixture) in die Email Queue (ohne E-Mail-Konto „Not Sent“,
  kein Versand), Button „Bestätigung anzeigen/kopieren“ (Text kopieren, als Textdatei
  speichern, erneut in die Warteschlange). Whitelist `corevatis_erpnext.crm.termin.*`, Rollen
  Büro/Meister.
- 1.5 Foto-Galerie: `corevatis_erpnext.galerie(frm)` (Thumbnails, Lightbox mit Vor/Zurück,
  Tastatur und Wischen, Bildunterschrift Dateiname/Datum/Quelle), am Lead im HTML-Feld
  `cv_galerie`; Bildliste per `corevatis_erpnext.crm.galerie.get_bilder(doctype, name)` aus
  Attach-/Attach-Image-Feldern (inkl. Kindtabellen) und File-Anhängen, mit Leserecht-Prüfung.
- Custom Fields am Lead: `cv_termin` (Link Event, nach `cv_verlustgrund`),
  `cv_section_galerie`/`cv_galerie` (HTML, nach `cv_foto_3`); angelegt in `after_install`
  und `after_migrate` nach den Feldern aus 1.1.
- 1.3 Workflow: Übergang „Termin vereinbaren“ (Neu → Termin, Workflow „Lead Anfrage“ aus 1.4)
  wird beim Anlegen des Termins ausgeführt.
- Security-Review 1.3/1.5: Event Private mit Freigabe an Meister/Büro, nur Lead-Verweis und
  PLZ/Ort im Event; `cv_termin` nur über `termin_anlegen` (Lead-`validate`), fremde Events
  werden weder abgesagt noch angezeigt; Galerie löst File-Datensätze nur am eigenen Dokument
  auf und lässt nur Datei-URLs der Site zu.

### Sicherheit (Review Epic 0)

- E-1: `letter_head.owner_line` wird als fester Text in den Briefkopf-Quelltext
  eingesetzt (HTML-escaped, `{`/`}` als Zeichenreferenz, kein `string.Template`):
  Jinja-Syntax aus der Konfiguration (später Wizard-Eingaben) wird beim Druck nicht mehr
  ausgeführt.
- Vertragstests zu `corevatis.setup.onboard` (C-3): Signaturen von
  `apply_site_template`/`list_templates` und Rückgabe `changes`/`warnings`/`skipped`.

### Hinzugefügt

- Web Form „Kostenlose Einschätzung“ (Aufgabe 1.2, Route `/kostenlose-einschaetzung`):
  Gast-Anfrage → Lead (`utm_source` „Website“, `cv_quelle` „Web Form“, Symptome,
  Beschreibung als CRM-Notiz) mit bis zu 3 Fotos (JPG/PNG/HEIC nach Inhalt, je ≤ 10 MiB)
  als **private** Dateien am Lead (mit `frappe_s3_attachment` im Bucket). `accept` per
  `override_whitelisted_methods` (andere Web Forms unverändert): Honeypot, Pflicht-
  Zustimmung Datenschutz, Rate-Limit je Client-IP (nur Frappes `request_ip`, per `ipaddress` geprüft; `cv_webform_rate_limit`/`_window`) und global je Formular (`cv_webform_rate_limit_global`, 100 angenommene je Stunde),
  `upload_file` bleibt für Gäste gesperrt. Einbettung per iframe (`?embed=1`, Höhe per
  `postMessage`), `frame-ancestors` nur für dieses Formular aus
  `cv_webform_embedding_domains`. Patch `v0_2.webform_einschaetzung`: UTM Source
  „Website“, Lead-Dubletten per E-Mail erlaubt.
- `corevatis_erpnext.setup.list_templates()` (Allowlist der Vorlagen für das
  Onboarding, Aufgabe 0.7); Vorlagennamen nur noch `^[_a-z0-9-]{1,64}$`, kein Pfad
  außerhalb von `templates/` (Härtung gegen Pfad-Traversal über `extends`/`template`).
- Site-Template (Aufgabe 0.4): `corevatis_erpnext.setup.apply_site_template` und
  `verify_site_template` für `bench execute`, JSON-Vorlagen `_standard` und `huelshoff`
  (Firma, SKR03/SKR04, Steuern 19 %/7 %, Nummernkreise, Rollen Büro/Meister/Monteur,
  Lager Hauptlager/Fahrzeug, Briefkopf, Adressvorlage DE, Standard-Druckformate,
  Systemeinstellungen); idempotent.
- `doc_events` Sales Invoice `before_insert`: Gutschriften im Nummernkreis `GS-.YYYY.-`.
- CI: Bench mit erpnext_germany und erpnext_druckformate, Template-Lauf und Prüfpunkte.

## [0.1.0] – 2026-10-28

### Hinzugefügt

- App-Skelett für Frappe/ERPNext v16 (Python 3.14): Modul `Corevatis ERPNext`,
  Hooks mit `required_apps = ["erpnext"]`, Übersetzungsdatei `translations/de.csv`.
- Platzhalter-Paket `corevatis_erpnext.setup` für das Site-Template (Aufgabe 0.4).
- CI: ruff, import-linter (kein Python-Import aus `corevatis`), frische Bench mit
  ERPNext `version-16`, `bench run-tests`, Lizenzliste; Release-Workflow für Tags `v*`.
