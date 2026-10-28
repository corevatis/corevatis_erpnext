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
  corevatis_erpnext/    Modul "Corevatis ERPNext"
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
