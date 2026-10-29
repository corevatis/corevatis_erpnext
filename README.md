# Corevatis ERPNext

Öffentlicher ERPNext-Adapter des Corevatis-Handwerker-MVP für **Frappe/ERPNext v16**
(Python 3.14): Custom Fields an Lead, Item, Quotation, Sales Order, Project und
Sales Invoice, `doc_events`-Hooks, Generatoren (Aufmaß → Quotation, Sales Order →
Tasks + Material Request, Abschlags-/Schlussrechnung), Pflichtprüfungen und
Print Formats auf ERPNext-Belegen.

Die Geschäftslogik (Mengen, Kalkulation, Abschläge) liegt nicht hier, sondern in
der Kern-App `corevatis`; dieser Adapter ruft sie ausschließlich über deren
whitelisted API (`corevatis.api.*`) auf.

## Site-Template

```bash
bench --site <site> execute corevatis_erpnext.setup.apply_site_template --kwargs '{"template": "huelshoff"}'
bench --site <site> execute corevatis_erpnext.setup.verify_site_template --kwargs '{"template": "huelshoff"}'
```

Eigene Vorlage: JSON mit `"extends": "_standard"` und `company` (Name, Kürzel,
`chart_of_accounts` SKR03/SKR04), Aufruf mit `{"config_file": "/absoluter/pfad.json"}`.
Idempotent; ein zweiter Lauf meldet `"changes": []`.

Steuern: Standard-Verkaufsvorlage ist „Umsatzsteuer 19 %“ (eine Zeile); die ERPNext-Vorlage
„Lieferung oder sonstige Leistung im Inland“ (19 %/7 %, Satz aus der Artikelsteuer) bleibt für
Belege mit 7-%-Positionen wählbar. Drucken im Desk druckt das PDF (`print_generated_pdf`). Der
ERPNext-Arbeitsbereich „CRM“ ist ausgeblendet (`workspaces`), Einstieg ist „Anfragen“.
Bestehende Sites übernimmt der Patch `v0_4.angebot_steuern_druck` beim `migrate` (Steuern,
Drucken, Preislisten); „CRM ausblenden“ kommt mit dem nächsten `apply_site_template`.

## Leistungskatalog

```bash
bench --site <site> execute corevatis_erpnext.katalog.importieren --kwargs '{"firma": "Hülshoff Sanierungstechnik"}'
bench --site <site> execute corevatis_erpnext.katalog.uebersicht --kwargs '{"firma": "Hülshoff Sanierungstechnik"}'
```

Paket `katalog/daten/abdichtung/` (Aufgabe 2.3): `00_leistungsregel.csv` (sechs allgemeine
Leistungsregeln, je Regelart eine, nur mit installiertem `corevatis`) und acht Data-Import-Vorlagen (CSV, Feldnamen als
Spaltenköpfe) für UOM, Item Group, Price List, Workstation, Operation, Item, Item Price und
BOM (Kostenkomponente „Lohn Montage“ legt die App selbst an) – im Desk über **Data Import** in der Reihenfolge der
Dateinamen einspielbar (BOM mit „Submit After Import“; den Dialog **Data Import** öffnet in
Frappe v16 nur der **System Manager**, Büro/Meister nicht) oder per Importer. 50
Leistungspositionen (48 mit Stückliste, davon Bodenabdichtung IA-08/IA-09 mit Regel `bodenflaeche`: Material zu `Standard-Einkauf` + Operation „Montage“
in min/Einheit), 32 Materialien (29 mit EAN, `09_barcode.csv`, 3.11), Preisliste `Standard-Verkauf`. Alle Preise und Zeiten sind
gekennzeichnete Annahmen (`ANNAHME 2.3`), bis Hülshoffs Angebote vorliegen. Der Importer ist
idempotent und überschreibt abweichende Datensätze (z. B. im Desk geänderte Preise) nur mit
`"force": true`; Stücklisten bekommen dann eine neue Standard-Version. Herstellkosten rechnet
ERPNext in der Stückliste, die Angebotskalkulation folgt in `corevatis` (4.1). Doku:
`docs/2.3-leistungskatalog.md` im Repo Handwerker.

## Web Form „Kostenlose Einschätzung“

Route `/kostenlose-einschaetzung/new` (Gast, kein Login): Anfrage → Lead mit bis zu
3 privaten Fotos. Logik in `corevatis_erpnext/webform/einschaetzung.py`, Formular unter
`corevatis_erpnext/corevatis_erpnext/web_form/kostenlose_einschaetzung/`.

| site_config | Bedeutung | Standard |
|---|---|---|
| `cv_webform_embedding_domains` | Origins, die das Formular per iframe einbetten dürfen (`frame-ancestors`) | keine |
| `cv_webform_datenschutz_url` | Link der Datenschutzerklärung | `/datenschutz` |
| `cv_webform_rate_limit` / `cv_webform_rate_window` | Einsendungen je Client-IP (`request_ip`) und Zeitfenster (s); ohne gültige IP gemeinsamer Bucket mit einem Drittel | 10 / 3600 |
| `cv_webform_rate_limit_global` | angenommene Einsendungen je Zeitfenster insgesamt (Rückfallschranke; abgewiesene Versuche zählen nicht) | 100 |

**Bekannte E-Mail-Adresse:** Gibt es zur E-Mail schon einen *offenen* Lead (Anfragestatus nicht
Gewonnen/Verloren, ERPNext-Status nicht Converted/Do Not Contact), legt das Formular keinen neuen
Lead und keinen neuen Contact an: Die neuen Angaben erscheinen als Kommentar „Weitere Anfrage über
das Web Form“ im Verlauf dieses Leads, die Fotos als private Anhänge (leere Foto-Felder werden
gefüllt, belegte bleiben); Status und Felder des Leads bleiben unverändert. Ohne offenen Lead
(z. B. nur ein verlorener) entsteht ein neuer Lead. Die ERPNext-Einstellung „Lead-Dubletten per
E-Mail erlauben“ bleibt aus – die Dublettenprüfung für Desk, Import und E-Mail gilt weiter. Die
Antwort an den Browser ist in allen Fällen gleich; der Erfolgstext weist darauf hin, dass eine noch
offene Anfrage ergänzt wird.

Einbettung (WordPress, „Individuelles HTML“-Block):

```html
<iframe id="cv-einschaetzung" src="https://<site>/kostenlose-einschaetzung/new?embed=1"
  title="Kostenlose Einschätzung" style="width:100%;border:0;min-height:900px" loading="lazy"></iframe>
<script>
window.addEventListener("message", function (e) {
  var f = document.getElementById("cv-einschaetzung");
  if (e.origin !== "https://<site>" || !e.data) return;
  if (e.data.type === "corevatis:einschaetzung:height") f.style.height = e.data.height + "px";
  if (e.data.type === "corevatis:einschaetzung:submitted") f.scrollIntoView({behavior: "smooth"});
});
</script>
```

## CRM am Lead (Epic 1)

- **Termin anlegen** (Büro/Meister): Dialog → `corevatis_erpnext.crm.termin.termin_anlegen`
  (Event + Zuweisung Meister + `Lead.cv_termin`, Kundenbestätigung in die Email Queue).
  Im MVP gibt es kein SMTP: Ohne ausgehendes E-Mail-Konto bleibt der Eintrag „Not Sent“, der
  Text wird über „Bestätigung anzeigen/kopieren“ manuell übermittelt. „Termin anlegen“ bei
  bestehendem Termin sagt den alten über `Event.save()` ab (Hooks wie der Google-Sync laufen).
- **Terminbestätigung (Email Template „Terminbestätigung Besichtigung“)**: keine Fixture mehr.
  `corevatis_erpnext.crm.email_vorlagen.ensure_email_templates` legt die Vorlage in
  `after_install`/`after_migrate` an und aktualisiert sie nur, solange sie auf der Site
  unverändert ist (Hash-Vergleich wie beim Druckformat-Sync). **Änderungen im Desk bleiben
  erhalten**; die ausgelieferte Fassung zurückholen:
  `bench --site <site> execute corevatis_erpnext.crm.email_vorlagen.ensure_email_templates --kwargs '{"force": true}'`
  (oder einmalig `cv_email_template_force_sync: 1` in der site_config).
- **Google Calendar** (Datenschutzentscheidung): Termine gehen nur an den Google-Kalender des
  Meisters, wenn die Site es erlaubt – site_config `cv_termin_google_sync: 1`, **Standard aus**.
  Übertragen werden dann Betreff (mit Kundenname) und PLZ/Ort.
- **Als verloren markieren**: Der Desk-Dialog speichert Verlustgrund und Workflow-Übergang in
  einem Aufruf (`corevatis_erpnext.anfrage.lead.als_verloren_markieren`).
- **Foto-Galerie**: `corevatis_erpnext.galerie(frm, {fieldname})` aus `public/js/galerie.js`,
  für weitere Doctypes die Datei in `doctype_js` eintragen und im `refresh` aufrufen.

## Angebot annehmen, ablehnen, nachfassen (Epic 4)

- **Angebot annehmen** (4.4, Büro/Meister, Button am eingereichten Angebot bzw.
  `POST /api/method/corevatis_erpnext.angebot.annahme.angebot_annehmen` mit `quotation`,
  `auftragsdatum`, `lieferdatum`, `einreichen`): Kunde aus dem Lead (ERPNext `_make_customer`,
  Anschrift aus Objekt/Objektadresse und Kontakt werden übernommen), Auftrag `AB-` ohne
  Eventual-/Alternativpositionen (`cv_eventualposition`, `cv_alternativposition`,
  `is_alternative`), Steuervorlage und `cv_*`-Felder vom Angebot, Projekt „<Objektadresse> –
  <Verfahren>“ (Projekttyp „Sanierung“), Lead „Gewonnen“ mit `cv_projekt`. Idempotent: ein
  zweiter Aufruf meldet den vorhandenen Auftrag und das Projekt.
- **Angebot ablehnen** (`angebot_ablehnen`, `quotation`, `grund`, `details`): Angebot „Lost“
  mit Grund, Lead „Verloren“ mit `cv_verlustgrund` (bleibt offen, wenn er ein weiteres offenes
  Angebot hat). Der ERPNext-Dialog „Als verloren festlegen“ führt den Lead ebenfalls nach.
- **Spiegel Angebot → Auftrag:** Alle `cv_*`-Custom-Fields an Quotation/Quotation Item entstehen
  bei jedem `migrate` gleichnamig an Sales Order/Sales Order Item (`angebot.annahme_felder`).
- **Sales Order einreichen** (auch über ERPNext „Erstellen → Auftrag“): Projekt anlegen, Lead
  „Gewonnen“, Angebotsstatus „Ordered“, wenn nur Eventual-/Alternativpositionen offen sind.
- **Nachfassen** (4.5): täglicher Scheduler-Job `corevatis_erpnext.angebot.nachfassen.taeglich`
  legt für eingereichte, offene Angebote nach X Tagen ein ToDo „Angebot <Nr> nachfassen“ an
  (`ToDo.cv_nachfass_zyklus`, höchstens N Zyklen, nie zwei offene). Einstellungen im Single
  **Corevatis Vertrieb Einstellungen** (`/app/corevatis-vertrieb-einstellungen`): Tage (7),
  Zyklen (2), abgelaufene Angebote, Zuweisung (Rolle Büro / fester Benutzer), Follow-up-Mail
  (Standard aus; Email Template „Angebot Nachfassen“, ohne SMTP nur Email Queue „Not Sent“).
  Button „Nachfassen“ am Angebot (`nachfassen`) legt die Aufgabe sofort an und zeigt den Text.

## Zeiterfassung (Aufgabe 3.9)

`corevatis_erpnext.zeiterfassung` ist der ERPNext-Teil der Monteur-Zeiterfassung. Die Kern-App
`corevatis` (Sync-Entitäten `zeit_start`, `zeit_stopp`, `zeit_eintrag`, Endpunkt
`meine_zeiten`) prüft Rechte und Ablauf und ruft die Funktionen per `frappe.get_attr` auf:

| Funktion | Zweck |
|---|---|
| `activity_types_sicherstellen()` | „Arbeitszeit“, „Fahrzeit“, „Pause“ anlegen, falls sie fehlen (auch bei `migrate`) |
| `employee_fuer_user(user)`, `mitarbeiter(employee)` | Employee ↔ User (`user_id`, nur aktive) |
| `zeit_buchen(employee, project, task, activity_type, von, bis, idempotency_key, notiz)` | Zeile im Timesheet-Entwurf des Tages (je Mitarbeiter und Tag eins, über Mitternacht geteilt); Überlappung → `ZeitKonflikt("ueberlappung")`; gleicher Key → vorhandene Zeilen |
| `offene_buchung(employee)`, `lauf_starten(…)`, `lauf_beenden(employee)` | laufende Buchung im Doctype „Zeitbuchung Lauf“ (Name = Employee) |
| `tagesbuchungen(employee, datum)` | Zeilen des Tages mit Task-Titel und Projektname |
| `sollstunden(employee, datum)` | Holiday List (Employee bzw. Firma): Feiertag 0 h, halber Tag 4 h, sonst 8 h; ohne Liste 8 h Mo-Fr (Annahme) |

Pause ist nicht abrechenbar (`is_billable` 0), Arbeits- und Fahrzeit sind abrechenbar. Die
Timesheets bleiben Entwurf, das Büro reicht sie im Desk ein.

## Arbeitspakete und Materialbedarf aus dem Auftrag (Epic 5)

- **Sales Order einreichen** (Hook nach dem Projekt aus 4.4, nur mit Projekt): je Verfahren ein
  **Task** am Projekt („<Verfahren> – <Räume, Bauteile>“, Sollstunden = Montageminuten der
  Stückliste × Menge, Reihenfolge und `depends_on` nach Bauablauf, Termine fortlaufend ab
  Projektstart mit Sollstunden ÷ Teamgröße und Tagesarbeitszeit) und eine **Material Request**
  (Entwurf, Standard „Purchase“ ins Hauptlager, Material der Stücklisten aggregiert + Verschnitt,
  `project`/`sales_order` je Position, `cv_auftrag`). Ein Fehler hält das Einreichen nicht auf.
- **Buttons** „Planung → Arbeitspakete erzeugen / Materialbedarf erzeugen“ an Auftrag und Projekt
  (Büro/Meister): `POST /api/method/corevatis_erpnext.planung.tasks_erzeugen` bzw.
  `…planung.materialbedarf_erzeugen` mit `sales_order` oder `project`; idempotent, ohne Projekt
  nur ein Hinweis.
- Einstellungen im Single **Corevatis Planung Einstellungen** (`/app/corevatis-planung-einstellungen`):
  Automatik je Teil, Teamgröße (2), Tagesarbeitszeit (8 h), Arbeitsbeginn (07:00), Art
  (Purchase / Material Transfer Hauptlager → Fahrzeug), Verschnitt (5 %), Bestand berücksichtigen,
  Haupt- und Fahrzeuglager.

## Materialentnahme aus dem Fahrzeuglager (3.11)

Modul `corevatis_erpnext.material` - Adapter für die Mobile-API des Kerns
(`corevatis.api.v1.material_lager`, `material_bestand`, Sync-Entität `material_entnahme`; Aufruf
per `frappe.get_attr`, kein Import):

- **Fahrzeuglager je Monteur:** Custom Field `Employee.cv_fahrzeuglager` (Link Warehouse); leer =
  Lager „Fahrzeug“ der Firma des Mitarbeiters (Site-Template: `Fahrzeug - HST`). Weitere
  Fahrzeuge = weitere Lager (z. B. unter „Alle Lager“) und am Employee eintragen.
- **Bestand:** Lagerartikel mit `Bin.actual_qty`, Lagereinheit, ganzzahlige Einheit, EANs aus
  `Item Barcode`.
- **Entnahme:** Stock Entry „Material Issue“ aus dem Lager, **eingereicht** (Bestand sinkt
  sofort), Projekt am Beleg und an den Zeilen, `cv_task`, `cv_mitarbeiter`; Idempotenz über
  `Stock Entry.cv_sync_key` (zusätzlich zum `Sync Journal` des Kerns). Menge über Bestand wird
  abgelehnt (`bestand_unzureichend`), außer ERPNext erlaubt negativen Bestand (Stock Settings
  bzw. Artikel) - dann gebucht mit Hinweis.
- **EANs im Katalog:** optionale `katalog/daten/abdichtung/09_barcode.csv` (29 Materialien,
  interne EAN-13 aus dem GS1-Bereich `20…` als Annahme bis zu den Herstellercodes). Der
  Importer ergänzt fehlende Barcodes an vorhandenen Artikeln, überschreibt nichts; im Desk
  stehen sie am Artikel unter „Barcodes“.
- **Testdaten** (nicht Produktcode): `bench --site <site> execute
  corevatis_erpnext.tests.material_testdaten.fahrzeug_auffuellen --kwargs '{"warehouse": "Fahrzeug - HST"}'`
  füllt acht Materialien per „Material Receipt“ auf eine Zielmenge auf (mehrfach aufrufbar).

Doku: `docs/3.11-materialentnahme.md` im Repo Handwerker.

## Lizenz

Copyright (C) 2026 Corevatis

This program is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, version 3. This program is distributed in the hope that it will be
useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE)
(GNU GPL v3).

## Installation

```bash
bench get-app erpnext --branch version-16
bench get-app https://github.com/corevatis/corevatis_erpnext --branch main
bench --site <site> install-app corevatis_erpnext   # installiert erpnext mit (required_apps)
```

## Struktur

```
corevatis_erpnext/
  hooks.py              required_apps = ["erpnext"], after_install, override accept (Web Form)
  webform/              Web Form „Kostenlose Einschätzung“ (Aufgabe 1.2)
  setup/                Site-Template (Aufgabe 0.4): site_template.py, config.py, templates/*.json
  katalog/              Leistungskatalog (2.3): importer.py, daten/<paket>/*.csv (Data-Import-Vorlagen)
  crm/                  Lead: Termin + Terminbestätigung (1.3), Foto-Galerie get_bilder (1.5),
                        Custom Fields cv_termin/cv_galerie (setup.py)
  crm/vorlagen/         Email Templates „Terminbestätigung Besichtigung“, „Angebot Nachfassen“
  angebot/              Angebot: Preis-Hinweis, Annahme/Ablehnung (4.4), Nachfassen (4.5), Feld-Spiegel
  planung/              Auftrag → Arbeitspakete (Task) und Materialbedarf (Material Request) (5.1)
  public/js/            Desk-Skripte (hooks.doctype_js): galerie.js, lead_galerie.js, lead_termin.js,
                        quotation_annahme.js, planung.js
  corevatis_erpnext/    Modul "Corevatis ERPNext" (Singles „Corevatis Vertrieb Einstellungen“,
                        „Corevatis Planung Einstellungen“)
  translations/de.csv   deutsche Übersetzungen
  tests/                Installationstests
```

## CI

| Job | Prüfung |
|---|---|
| `lint` | ruff check/format, import-linter (kein Python-Import aus `corevatis`) |
| `tests` | frische Bench (Frappe + ERPNext `version-16`, Python 3.14, Node 24, MariaDB 11.8), `install-app corevatis_erpnext`, `bench run-tests --app corevatis_erpnext`, Lizenzliste `THIRD_PARTY_LICENSES.md` (Artefakt) |

Release: Version in `corevatis_erpnext/__init__.py` und `CHANGELOG.md` anheben,
Tag `vX.Y.Z` pushen – der Workflow `Release` erzeugt das GitHub-Release mit
Changelog-Auszug und Lizenzliste.
