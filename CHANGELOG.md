# Changelog

Alle nennenswerten Änderungen dieser App. Versionierung nach
[Semantic Versioning](https://semver.org/lang/de/); ein Release entsteht durch
ein Tag `vX.Y.Z`, das zur Version in `corevatis_erpnext/__init__.py` passt.

## [Unveröffentlicht]

### Hinzugefügt (3.11 Materialentnahme)

- `corevatis_erpnext.material`: Fahrzeuglager je Mitarbeiter (`Employee.cv_fahrzeuglager`,
  Rückfall Lager „Fahrzeug“ der Firma), Bestand mit Einheit und EAN, Entnahme als eingereichter
  Stock Entry „Material Issue“ mit Projekt/Task/Mitarbeiter, idempotent über
  `Stock Entry.cv_sync_key`, Fehler mit maschinenlesbarem Grund (`MaterialFehler.grund`).
- Custom Fields `Employee.cv_fahrzeuglager`, `Stock Entry.cv_task`/`cv_mitarbeiter`/`cv_sync_key`
  (after_install/after_migrate).
- Katalog: optionale `09_barcode.csv` mit EAN-13 für 29 Materialien; der Importer ergänzt
  fehlende Barcodes, ohne zu überschreiben.
- Testdaten-Helfer `tests.material_testdaten.fahrzeug_auffuellen` (Anfangsbestand per
  Material Receipt), Tests `test_material` (Bestand, Idempotenz dreifach, Fehler, negativer
  Bestand).

### Neu (Aufgabe 3.9)

- **Zeiterfassung** `corevatis_erpnext.zeiterfassung` für die Mobile-API des Kerns
  (`zeit_start`/`zeit_stopp`/`zeit_eintrag`, `meine_zeiten`; Aufruf per `frappe.get_attr`):
  Activity Types „Arbeitszeit“, „Fahrzeit“, „Pause“ (Pause nicht abrechenbar), Buchung als Zeile
  im Timesheet-Entwurf je Mitarbeiter und Tag (über Mitternacht geteilt), Überlappung über
  ERPNext als `ZeitKonflikt`, Idempotenz über `Timesheet Detail.cv_sync_key`, laufende Buchung
  im neuen Doctype „Zeitbuchung Lauf“ (eine je Employee), Tagesliste, Sollstunden aus der
  Holiday List (sonst 8 h Mo-Fr als Annahme).

### Hinzugefügt (3.7 Foto-Upload)

- Foto-Galerie (1.5) auch am **Befund** (Doctype aus `corevatis`): Fotos der App aus der Tabelle
  „Fotos“ und Anhänge als Thumbnails mit Vollbild, als Abschnitt im Formular-Dashboard
  (`befund_galerie.js`); `get_bilder` lässt `Befund` zu (Allowlist), Rechte wie bisher
  (Leserecht auf den Befund, Feldrechte, nur Anhänge des eigenen Datensatzes).

### Neu (5.1 Arbeitspakete und Materialbedarf)

- Modul `planung/`: beim Einreichen eines Auftrags mit Projekt je Verfahren ein Task
  (Sollstunden aus den Montageminuten der Stücklisten, Bauablauf-Reihenfolge mit `depends_on`,
  Termine fortlaufend nach Teamgröße und Tagesarbeitszeit, Projektende wird bei Bedarf verschoben)
  und eine Material Request (Entwurf, „Purchase“ ins Hauptlager oder „Material Transfer“ ins
  Fahrzeug, aggregiert mit Verschnitt, optional abzüglich Bestand). Whitelist
  `planung.tasks_erzeugen` / `planung.materialbedarf_erzeugen` (Büro/Meister, idempotent),
  Buttons an Auftrag und Projekt, Single „Corevatis Planung Einstellungen“, Custom Fields
  `Task.cv_auftrag/cv_verfahren/cv_sollstunden/cv_positionen`, `Material Request.cv_auftrag`.
- Workspace „Anfragen“: Karte „Aufträge und Planung“ (Auftrag, Projekt, Aufgabe,
  Materialanforderung), Links auf Vertriebs- und Planungs-Einstellungen.

### Behoben (Nachprüfung mvp-test)

- Angebotsannahme: `Quotation.cv_lead` und der Spiegel `Sales Order.cv_lead` blieben leer, weil
  ERPNext (`Customer.update_lead_status`) das Angebot beim Anlegen des Kunden schon auf den
  Kunden umstellt. Der Lead wird jetzt vorher gemerkt und serverseitig gesetzt.

### Behoben (Review Epic 4)

- **A-1** Angebotsannahme und Nachfassen: parallele Aufrufe (Doppelklick, REST-Wiederholung,
  zwei Scheduler-Läufe) ergeben genau einen Auftrag, ein Projekt bzw. ein Nachfass-ToDo.
  `annahme.angebot_sperren`: neue Transaktion, solange die Anfrage nichts geschrieben hat
  (REPEATABLE READ: Snapshot sonst von vor der Sperre), dann `FOR UPDATE` auf das Angebot vor
  jedem Lesen der Belegdaten; Dublettenprüfungen (`Sales Order Item.prevdoc_docname`, ToDos)
  als Locking Read; Riegel nach dem Anlegen (`nur_ein_auftrag`); Projektanlage sperrt den
  Auftrag. Tests mit zwei gleichzeitigen Anfragen über die WSGI-App (`tests/_parallel.py`).
- **A-2** Eventual-/Alternativpositionen: Quotation `before_submit` nimmt `has_alternative_item`
  der Zeile davor zurück (keine Alternativ-Sets mehr, Normalpositionen fallen auf dem
  ERPNext-Weg nicht mehr weg), Patch `v0_4.alternativ_sets_aufloesen` für eingereichte Angebote;
  am Angebot mit Kennzeichen ist ERPNext „Erstellen → Auftrag“ ausgeblendet und verweist auf
  „Angebot annehmen“. `is_alternative` bleibt der Summenmechanismus (einziger Weg in v16, der
  in Desk und Server gleich rechnet).
- **A-3** Kundendubletten: vor dem Anlegen wird ein bestehender Kunde mit derselben E-Mail
  (Kontakt mit Kundenverknüpfung) oder demselben Namen und derselben PLZ übernommen (Warnung),
  die Objektanschrift an ihn gehängt; neues Feld `Quotation.cv_lead` (gespiegelt) hält die
  Anfrage.
- **A-5** Projekt automatisch nur für Angebote aus dem Aufmaß (`cv_erzeugt_aus_aufmass`,
  `cv_aufmass`), sonst optional (`projekt_anlegen`, Dialog „Projekt anlegen“); gilt auch für den
  `on_submit`-Hook.
- **G-1/A-7** `cv_kalkulation_json` und `cv_einzelkosten` auf Feldebene 2 (Ebene 1 lesen in
  ERPNext schon Sales User u. a.); Büro und Meister bekommen Ebene 2 (`ensure_feldrechte`,
  Site-Template, `verify_site_template`); der Spiegel übernimmt `permlevel`.
- **G-2** „Entwurf ersetzen“ mit von Hand ergänzten Positionen verlangt `bestaetigen=1`
  (Antwort `bestaetigung_noetig`, `manuelle_positionen`; Dialog am Aufmaß).
- **N-1** Nachfassen: genau ein ToDo je Zyklus - Ersteller des Angebots (auch Meister), sonst
  der erste aktive Büro-Benutzer.
- Abnahme mvp-test: Generator wählt die Firma mit Vorrang Aufmaß/Objekt (Feld `company`, falls
  vorhanden) > Lead > Standardfirma; hat die Firma für das Aufmaß keine Leistungen (alter Lead
  einer Firma ohne Stücklisten), nimmt er die nächste mit Hinweis. 403 beim Nachfassen nennt
  „nachfassen“ statt „annehmen und ablehnen“.
- Security-Review: `Quotation.cv_lead` setzt nur der Server (`validate` verwirft Änderungen aus
  Formular/REST), bei der Annahme zählt es nur, wenn der Lead zum Kunden gehört; Annahme und
  `on_submit`-Hook ändern einen Lead nur mit Schreibrecht. Nachfass-Aufgaben gehen nur an
  Benutzer mit Rolle Büro/Meister und eigenem Leserecht am Angebot - keine Freigabe (DocShare)
  mehr.

### Behoben (Verifikation Epic 4 auf mvp-test)

- Aufmaß: Die Verbindung „Angebot“ zeigt jetzt die Anzahl. Ohne `fieldname` im Dashboard lud
  der Desk keine Zähler; das Kern-Doctype Aufmaß hat kein eigenes Dashboard.

### Neu (4.3, Angebotsdruck)

- Textbausteine (Terms and Conditions, `angebot.textbausteine`, create-if-missing in
  `after_install`/`after_migrate` und im Site-Template): „Angebot Standard“ (Zahlungsbedingungen,
  Gültigkeit), „Abdichtung Standard“ (Vorbemerkung), „Gewährleistung Abdichtung“
  (Garantiehinweis). Inhalt ist eine fachliche Annahme, kein Rechtsrat. Eine unveränderte
  „Angebot Standard“ in der Fassung aus 4.1 (dort Vorbemerkung) wird ersetzt.
- Quotation `before_insert`: leere `tc_name`/`terms` (Zahlungsbedingungen der Firma),
  `cv_vorbemerkung`, `cv_garantiehinweis` aus den Bausteinen; der Generator (4.1) nutzt
  dieselben Bausteine.
- Quotation `before_validate`: `cv_eventualposition`/`cv_alternativposition` setzen
  `is_alternative` (nicht in Summe und Steuer, wie im Druck ausgewiesen).
- Site-Template: `print_formats_replace` (eigene Formate, die ein Kundentemplate ablösen darf),
  Abschnitt `angebot` (`default_selling_terms` der Firma); Hülshoff: Standardformat Quotation
  „Angebot Corevatis“ (erpnext_druckformate ≥ `ae3b4ae`). `verify_site_template` prüft beides.
- Tests: Druck „Angebot Corevatis“ mit 1, 2 und 6 Seiten (wkhtmltopdf), Summen, Seitenzahlen,
  kein „None“, Maskierung; CI mit wkhtmltopdf 0.12.6 und Muster-PDFs als Artefakt.

### Hinzugefügt (4.1, Angebot aus dem Aufmaß)

- Custom Fields am Angebot (`angebot.felder`, idempotent in `after_install`/`after_migrate`):
  Quotation `cv_titel`, `cv_objekt`, `cv_aufmass`, `cv_kalkulationsschema` (Data, Namen der
  Kern-Datensätze), `cv_erzeugt_aus_aufmass` (Check, nur lesen), `cv_vorbemerkung` (Text
  Editor), `cv_garantiehinweis` (Small Text), `cv_kalkulation_json` (Code/JSON, nur lesen,
  nicht gedruckt); Quotation Item `cv_raum`, `cv_bauteil`, `cv_verfahren`,
  `cv_aufmass_position` (Data), `cv_mengen_herkunft` (Small Text), `cv_einzelkosten`
  (Currency, nur lesen), `cv_eventualposition`, `cv_alternativposition` (Check). Gemeinsamer
  Vertrag für Generator (4.1), Angebotslayout (4.3) und Annahme (4.4).
- Angebot aus dem Aufmaß: Whitelist `corevatis_erpnext.angebot.generator.erzeuge_angebot`
  (`aufmass`, `schema`, `kunde`, `lead`, `neu_erzeugen`, `erschwernis_prozent`; Rollen Büro,
  Meister). Leistungen aus `item_code` bzw. `verfahren` der Aufmaßzeilen (Leistungsregeln des
  Verfahrens mit Leistungsposition), Herstellkosten aus der Standard-Stückliste, Kalkulation
  im Kern per `frappe.get_attr("corevatis.angebot.kalkulation.kalkuliere")`. Angebot mit
  Preisliste „Standard-Verkauf“, Preisen der Engine, Steuervorlage „Umsatzsteuer 19 %“ samt
  Steuerzeilen (Befund mvp-test Nr. 4), `valid_till` + 30 Tage (Nr. 17), Titel, Vorbemerkung
  (Textbaustein „Angebot Standard“, create-if-missing), Garantiehinweis, `cv_kalkulation_json`.
  Partei: Kunde, Lead, Referenz des Aufmaßes oder Kunde des Objekts, sonst Platzhalterkunde
  „Interessent“. Idempotent: zweiter Aufruf meldet das bestehende Angebot, `neu_erzeugen=1`
  ersetzt einen Entwurf (gleiche Nummer).
- Aufmaß-Formular (Kern): Schaltfläche „Erstellen → Angebot erzeugen“ (Schema, Erschwernis) und
  „Angebot …“ (`public/js/aufmass_angebot.js`); Verbindungen „Angebot“ am Objekt und am Aufmaß
  (`override_doctype_dashboards`).

### Neu (Epic 4: Angebotsannahme 4.4, Nachfassen 4.5)

- `angebot.annahme.angebot_annehmen`: Angebot → Kunde (aus Lead, mit Anschrift und Kontakt) →
  Auftrag `AB-` ohne Eventual-/Alternativpositionen → Projekt (Typ „Sanierung“) → Lead
  „Gewonnen“; idempotent, Desk-Button mit Dialog (Datum, Liefertermin, einreichen).
- `angebot.annahme.angebot_ablehnen`: Angebot „Lost“ mit Grund, Lead „Verloren“ mit
  Verlustgrund; Verlustgründe des Leads als „Quotation Lost Reason“.
- Hooks: Sales Order `on_submit` (Projekt, Lead, Angebotsstatus ohne Zusatzpositionen),
  Quotation `on_update_after_submit` (ERPNext-Verloren-Dialog → Lead).
- Custom Fields: Spiegel `cv_*` Quotation → Sales Order (+ Positionen), Project `cv_objekt`,
  `cv_aufmass`, `cv_angebot`, `cv_lead`, Lead `cv_projekt`, ToDo `cv_nachfass_zyklus`.
- Nachfass-Automatik: Single „Corevatis Vertrieb Einstellungen“, täglicher Job
  `angebot.nachfassen.taeglich`, Email Template „Angebot Nachfassen“ (Email Queue, kein SMTP),
  Button „Nachfassen“ am Angebot.

### Geändert (Befunde mvp-test, Angebot)

- Nachprüfung: Standard-Preisliste auch als Site-Default (`frappe.defaults`), den das Desk-Formular liest;
  Patch `v0_4.preislisten_standardwert` (vorher zog ein neues Angebot weiter „Standard Selling“).
- Steuern: eigene Verkaufs-Steuervorlage „Umsatzsteuer 19 %“ je Firma (eine Zeile, fester
  Satz 19 % auf 1776/3806), Standard statt der ERPNext-Vorlage „Lieferung oder sonstige
  Leistung im Inland“ (zwei Zeilen 19 %/7 % mit Satz 0, Satz aus der Artikelsteuer; im Angebot
  stand immer eine leere 7-%-Zeile, Firmen ohne Artikelsteuer-Standard bekamen 0 € Steuer).
  Die ERPNext-Vorlage bleibt wählbar (7-%-Belege). Template-Schlüssel `taxes.sales_templates`,
  `taxes.replace_default_sales_templates`; neuer Prüfpunkt „genau eine Zeile“.
- Patch `v0_4.angebot_steuern_druck` für bestehende Sites: `add_taxes_from_item_tax_template = 0`
  (Sites mit Template vor #24, z. B. mvp-kunde2: acht Steuerzeilen je Angebot), Vorlage
  „Umsatzsteuer 19 %“ für jede deutsche Firma mit ERPNext-Steuervorlagen (auch Firmen ohne
  Template-Lauf), `print_generated_pdf`, Standard-Preislisten. Bestehende Belege bleiben.
- Drucken: Print Settings `print_generated_pdf = 1` (Template-Schlüssel `print_settings`) –
  „Drucken“ im Desk druckt das PDF statt der Browser-Vorschau, die über die Seitenlänge lief.
- Katalog: Importer setzt die Katalog-Preislisten als Standard in Selling/Buying Settings,
  solange dort die ERPNext-Vorgabe steht (`katalog.importer.standard_preislisten`); vorher zog
  ein neues Angebot „Standard Selling“ und alle Positionen standen auf 0,00 €.
- Angebot: Hinweis beim Speichern, wenn Positionen keinen Preis haben (`angebot.pruefung`).
- Objekt: ein für einen Lead angelegtes Objekt trägt sich in `Lead.cv_objekt` ein (nur wenn
  leer, nur mit Schreibrecht auf den Lead); `Objekt.validate` prüft Leserecht auf den verknüpften
  Kunden/Lead (Security-Review, IDOR); Lead-Formular „Erstellen → Objekt“.
- Arbeitsbereich „Anfragen“ als CRM-Einstieg: Kennzahlen (Neue Anfragen 7 Tage, Offene Termine,
  Angebote offen), Kurzlinks (Neue Anfrage, Offene Anfragen, Kanban, Termine heute, Kalender,
  Angebote, Web Form), Links zu Lead, Chance, Potenzieller Kunde, Kampagne, Kunde, Kontakt,
  Adresse, Termin, Angebot; sichtbar auch für System Manager; ganz oben. Kachel „Anfragen“ auf
  dem Startbildschirm (`add_to_apps_screen`). Template-Schlüssel `workspaces` blendet den fast
  leeren ERPNext-Arbeitsbereich „CRM“ aus (`is_hidden`, rücknehmbar).
- Übersetzung „Get Items From“ → „Positionen übernehmen aus“ (ERPNext: „Holen Sie Elemente aus“).

### Geändert (Review Epic 2)

- D-1: Bodenabdichtung im Katalog: IA-08 „Innenabdichtung Boden mineralische Dichtschlämme
  2-lagig“ und IA-09 „… sulfatbeständige Dichtschlämme 3-lagig, drückendes Wasser“ (m², Stückliste,
  Preis, `ANNAHME 2.3`) mit `cv_leistungsregel` `bodenflaeche`; neue allgemeine Regel
  `bodenflaeche` (Regelart „Bodenfläche“, braucht `corevatis` mit dieser Regelart) in
  `00_leistungsregel.csv`. Katalog jetzt 50 Leistungen, 48 Stücklisten. Hohlkehle innen
  Wand/Boden gab es schon (IA-04, `laufmeter`).
- D-3: README: Desk-Dialog Data Import nur für System Manager.

### Hinzugefügt (Epic 2)

- 2.1: `Lead.cv_objekt` (Link auf `Objekt` aus `corevatis`) im Abschnitt „Anfrage“ nach
  `cv_objekt_adresse`. Entsteht nur, wenn `corevatis` installiert ist (after_install/after_migrate
  und `after_app_install`), `before_app_uninstall` von `corevatis` entfernt es (`vor_ort.lead_objekt`).
- 2.2: Custom Fields am Item im Abschnitt „Corevatis-Leistung“ (Tab „Details“, vor „Beschreibung“): `cv_gewerk`,
  `cv_verfahren` (Select, Werte wie `Leistungsregel` in `corevatis`), `cv_leistungsregel` (Data:
  Name der Regel, kein Link). `doc_events` Item `validate`: Wert getrimmt; ist `corevatis`
  installiert, muss die Regel existieren und lesbar sein (`katalog.item`).
- 2.3: Leistungskatalog Abdichtung `katalog/daten/abdichtung/`: Data-Import-Vorlagen (CSV) für UOM,
  Item Group, Price List, Workstation, Operation, Item, Item Price und BOM (Kostenkomponente
  „Lohn Montage“ per after_install/after_migrate, der Doctype erlaubt keinen Data Import);
  48 Leistungspositionen (46 mit Stückliste aus Material und Operation „Montage“ in
  min/Einheit), 32 Materialien, Preislisten `Standard-Verkauf`/`Standard-Einkauf`, Workstation
  „Montage“ 48 €/h. Preise, Verbräuche und Zeiten sind gekennzeichnete Annahmen (`ANNAHME 2.3`).
- 2.3: `corevatis_erpnext.katalog.importieren(firma, paket, force)`: idempotenter Import derselben
  CSV-Dateien (Onboarding 0.7), ohne `force` kein Überschreiben abweichender Datensätze;
  `uebersicht` (Kosten/Preise lesen), `list_pakete`.
- 2.3: Vorlage `00_leistungsregel.csv` mit fünf allgemeinen Leistungsregeln (je Regelart, Standardparameter);
  nur eingespielt, wenn `corevatis` (Doctype `Leistungsregel`) installiert ist, damit `Item.validate`
  (2.2) die Regelnamen der Positionen findet.
- 2.3: CI-Katalog-Prüfung per `bench execute` nach den Tests (0 Änderungen nach dem Data Import).

### Geändert (Review Epic 1)

- E-1: Email Template „Terminbestätigung Besichtigung“ ist keine Fixture mehr (Frappe überschrieb es
  bei jedem `migrate`). `crm.email_vorlagen.ensure_email_templates` (after_install/after_migrate)
  legt es an und aktualisiert es nur, solange die Site-Kopie unverändert ist; Desk-Änderungen
  bleiben, `force=True` bzw. `cv_email_template_force_sync` holt die ausgelieferte Fassung zurück.
- E-2: „Lead-Dubletten per E-Mail erlauben“ wird nicht mehr site-weit gesetzt; Patch
  `v0_3.lead_dubletten_pruefung` schaltet die Einstellung auf bestehenden Sites wieder aus. Web Form:
  Anfrage mit bekannter E-Mail-Adresse → Kommentar mit den neuen Angaben und Fotos als Anhänge am
  offenen Lead (Status unverändert, kein neuer Contact); ohne offenen Lead neuer Lead. Erfolgstext
  ergänzt.
- E-3: „Termin verschieben“ sagt den alten Termin über `Event.save()` ab (Version, `modified`,
  Event-Hooks inkl. Google-Sync) statt `frappe.db.set_value`.
- E-4: Google-Calendar-Sync nur mit Opt-in der Site (`cv_termin_google_sync`), Standard aus.
- E-5: Meister-Auswahl im Termin-Dialog escaped (`frappe.utils.escape_html`).
- E-7: nicht lesbarer Terminbeginn bzw. Dauer ohne Zahl → Meldung (417) statt 500 bzw. stiller 60 min.
- E-8: Workflow-Übergang nach dem Termin über die Aktion „Termin vereinbaren“ (Präfix nur Rückfall).
- E-9: globale Sperre des Web Forms vor dem Dekodieren/Prüfen der Fotos.
- E-10: Anhang-Eintrag im Lead-Verlauf erst nach dem Upload (mit `frappe_s3_attachment` die
  endgültige URL statt `/private/files/…` → 403).
- E-11: Galerie nur JPG/PNG/GIF/WebP/HEIC, kein SVG; entschieden wird an der Ziel-URL (Pfad ohne
  Query/Fragment, beim S3-Download `file_name`) und am Typ der File-Zeile, nicht am angezeigten
  Dateinamen; „Original öffnen“ nur für diese Typen.
- E-13: „Als verloren markieren“ speichert Grund und Übergang gemeinsam
  (`anfrage.lead.als_verloren_markieren`), kein Vorab-`set_value`.
- T-1/T-3: Gast-Test für private Fotos verlangt 401/403; Actions per Commit-SHA, `frappe-bench`
  und `pip-licenses` per Version gepinnt, Dependabot für Actions.

### Hinzugefügt (Epic 1)

- 1.2 Einbettung: Das Formular meldet im iframe die Inhaltshöhe statt der iframe-Höhe (Befund mvp-test:
  immer 900 px, Formular scrollte im iframe).
- 1.4 „Wieder öffnen“ leert den Verlustgrund (Befund Verifikation mvp-test: der alte Grund blieb stehen,
  ein erneutes „Als verloren markieren“ hätte ihn ohne Rückfrage übernommen).
- 1.2 `cv_webform_embedding_domains` auch als Text (Leerzeichen/Komma-getrennt oder als Text abgelegte
  Liste): Press' Site Config speicherte die Liste als `"['http://localhost:8080']"`, die CSP fehlte.
- 1.3 Ort im Event nur PLZ/Ort auch bei einzeiliger Objektadresse aus dem Web Form (Befund mvp-test:
  „Testweg 1, 48143 Münster“ stand komplett im Kalender).
- 1.2 Platzhalterseite `/datenschutz` (Web Page „Datenschutz (Entwurf)“, Hinweis „Entwurf – nicht
  rechtsverbindlich, im Pilotbetrieb ohne Datenschutzerklärung“) für den Link im Web Form; angelegt in
  `after_install`/`after_migrate` nur, wenn `cv_webform_datenschutz_url` nicht gesetzt ist und unter der
  Route noch keine Web Page liegt (vorhandene Seiten bleiben unangetastet).
- 1.3 Termin am Lead: Button „Termin anlegen“ (Datum/Uhrzeit, Dauer, Meister, Notiz) legt ein
  `Event` an (Private mit Freigabe an Meister und Büro, Kategorie Meeting, Lead als Teilnehmer, Zuweisung an den Meister,
  Google-Calendar-Sync, falls der Meister einen Kalender verbunden hat – seit Review Epic 1 nur mit Opt-in), setzt `Lead.cv_termin`,
  sagt einen vorherigen offenen Termin ab und führt – falls ein Lead-Workflow aktiv ist – den
  Übergang nach „Termin…“ aus. Kundenbestätigung aus dem Email Template
  „Terminbestätigung Besichtigung“ (bis Review Epic 1 als Fixture) in die Email Queue (ohne E-Mail-Konto „Not Sent“,
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
  Beschreibung im Feld `cv_beschreibung`) mit bis zu 3 Fotos (JPG/PNG/HEIC nach Inhalt, je ≤ 10 MiB)
  als **private** Dateien am Lead (mit `frappe_s3_attachment` im Bucket). `accept` per
  `override_whitelisted_methods` (andere Web Forms unverändert): Honeypot, Pflicht-
  Zustimmung Datenschutz, Rate-Limit je Client-IP (nur Frappes `request_ip`, per `ipaddress` geprüft; `cv_webform_rate_limit`/`_window`) und global je Formular (`cv_webform_rate_limit_global`, 100 angenommene je Stunde),
  `upload_file` bleibt für Gäste gesperrt. Einbettung per iframe (`?embed=1`, Höhe per
  `postMessage`), `frame-ancestors` nur für dieses Formular aus
  `cv_webform_embedding_domains`. Patch `v0_2.webform_einschaetzung`: UTM Source
  „Website“ (setzte bis Review Epic 1 auch „Lead-Dubletten per E-Mail erlauben“, siehe E-2).
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
