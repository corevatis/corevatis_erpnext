# Changelog

Alle nennenswerten Änderungen dieser App. Versionierung nach
[Semantic Versioning](https://semver.org/lang/de/); ein Release entsteht durch
ein Tag `vX.Y.Z`, das zur Version in `corevatis_erpnext/__init__.py` passt.

## [Unveröffentlicht]

### Desk: Navigation „Betrieb“ neu strukturiert

- Leiste `sidebar/betrieb/betrieb.json` neu geordnet (`modified` 2026-11-07 10:00, `migrate` übernimmt sie):
  oben ohne Abschnitt Vorgänge, Baustellen, Plantafel, Termine, Aufgaben, Nachrichten (immer offen), darunter
  sechs klappbare Gruppen mit Symbol: Anfrage bis Auftrag, Ausführung, Material, Abrechnung, Stammdaten,
  Einstellungen. Frappe v16 kann das nativ (Abschnitt mit `indent`, `collapsible`, `keep_closed`, Einträge mit
  `child`); den Auf/Zu-Zustand merkt Frappe je Leiste im localStorage, die Gruppe der geöffneten Seite klappt
  von selbst auf. Vorher standen alle Einträge ohne `child` flach untereinander, die Abschnitte waren unsichtbar.
- Neu: Einstellungen → Geräte (`Corevatis Geraet`, Handwerker#70). Der Eintrag „Alle Anfragen“ entfällt sichtbar;
  der Arbeitsbereich „Anfragen“ hat dafür oben die Kachel „Alle Anfragen“ (`modified` angehoben).
- Einträge je Rolle: neues Modul `prozess.navigation` (Regel je Link, Hook `boot_session` legt die
  auszublendenden Schlüssel in `frappe.boot.cv_navigation` ab), `betrieb.bundle.js` lässt sie beim Zeichnen
  weg. Die Einträge bleiben in `frappe.boot.module_sidebars`, damit geöffnete Belege in der Shell „Betrieb“
  bleiben. Büro und System Manager sehen alles; Meister ohne Abrechnung, Einstellungen, Kundenzugänge und
  ohne Kunden/Fahrzeuge/Textbausteine/Lager/Lieferanten; Monteur: Baustellen („Meine Baustellen“), Termine,
  Aufgaben, Nachrichten, Zeiten, Stundenkonto. Stundenkonto und Nachkalkulation stehen unter Ausführung
  (Meister behält die Nachkalkulation, Monteur das Stundenkonto aus P2.5). Rechte bleiben an den Doctypes.
- Neuer Test `test_sidebar` (Gruppen, keine doppelten Links, Ziele vorhanden, Symbole, Sicht je Rolle,
  Schlüssel passend zu Frappes Payload); `test_plaene_desk`, `test_bautagebuch_adapter` an die neue
  Reihenfolge angepasst.

### Kalkulation: Minuten je Einheit und Stundensatz aus einer Quelle (Handwerker#104)

- Arbeitspakete (5.1) nehmen die Minuten je Einheit aus der im angenommenen Angebot eingefrorenen
  Kalkulation (`Quotation.cv_kalkulation_json`), mit derselben Zuordnung wie die Nachkalkulation (P2.1):
  neues Modul `planung.kalkulation` (`kalkulation_laden`, `zuordnen`), `planung.positionen.positionen`
  liefert je Position `minuten_quelle` (`kalkulation`, `stueckliste`, `einheit`, `keine`) und die
  zugeordnete Kalkulationsposition. Nur ohne Kalkulation (Altbestand, Handposition) gilt die Stückliste;
  dann steht am Arbeitspaket ein Kommentar „Info“, ein Absatz in der Beschreibung (neue Spalte „Quelle“)
  und `minuten_quelle` in `cv_positionen`. Der Materialbedarf bleibt bei der Stückliste.
- Nachkalkulation liest die Sollminuten über `planung.positionen` (keine eigene Zuordnung mehr,
  `nachkalkulation.kalkulation_laden` bleibt als Import erhalten). Neu je Verfahren und Projekt
  `soll_h_pakete` (Summe `Task.cv_sollstunden`), Spalte „Soll h Pakete“ im Bericht und ein Hinweis am
  Projekt, wenn die Arbeitspakete mehr als 5 % vom Soll abweichen (`pakete_abweichung`). Kein Patch für
  bestehende Arbeitspakete.
- Stundensatz: neues Modul `angebot.stundensatz`. Das Standard-Kalkulationsschema ist die Quelle; beim
  Speichern (Hook `on_update` am Kern-Doctype „Kalkulationsschema“) und nach `migrate` setzt der Adapter die
  Kostenkomponente „Lohn Montage“ der Workstations auf diesen Satz und lässt die Stücklistenkosten neu
  rechnen. `pruefen()` meldet Abweichungen. Angebot, Nachkalkulation und Abrechnung lesen die Workstation
  nicht.
- Test `test_planung.test_schreibrechte_fuer_buttons` nimmt Büro auch das Anlegerecht an Material Request
  (seit P2.2 hat Büro dort volle Rechte; der Test schlug auf `main` fehl).
### Büro-Dashboard „Baustellen“ (Handwerker#43, 5.9)

- Neuer Script Report „Baustellen“ (Büro, Meister, System Manager): je laufendem Projekt (Status offen, Auftrag
  eingereicht) eine Zeile mit Kunde/Objekt, Meister, Status (offen/in Arbeit), Soll- und Ist-Stunden, Ist im Zeitraum,
  Abweichung gegen Soll x Fortschritt, Material Soll/Ist, Fortschritt (erledigte Arbeitspakete gewichtet nach
  Sollstunden, Mangel-Aufgaben ausgenommen), nächstem Termin, offenen Meldungen/Mängeln und Ampel (grün bis 5 % über
  Soll, gelb bis 15 %, sonst oder bei überfälligem Termin rot). Filter Ist von/bis (Standard laufende Woche), Meister,
  Status, Ampel; Link „Nachkalkulation“ je Zeile. Meister und Kunde als Data-Spalten (Benutzerberechtigungen).
- Neues Modul `baustellen`: Soll, Ist und Material aus `nachkalkulation.projekt_auswerten`/`ist_stunden`,
  Zuweisungen aus `planung.projekt.zuweisungen`, Meister der Besichtigung aus `prozess.kette`; keine eigene
  Soll/Ist-Rechnung.
- Neuer Arbeitsbereich „Baustellen“ mit vier Kennzahlen (Number Cards vom Typ Custom: laufend, rot, Stunden diese
  Woche, Meldungen und Mängel offen; 60 s je Benutzer zwischengespeichert) und Verknüpfungen; Eintrag in der Leiste
  „Betrieb“ unter „Vorgänge“ (`modified` 2026-11-06 09:00). Die Karten legt `after_install`/`after_migrate` an
  (`setup.install.ensure_baustellen`); `prozess.setup.ensure_kennzahlen` nimmt dafür Kartensatz und Doctype entgegen.
### Katalog Innenabdichtung: Innenwände und positionsgenaue Regeln (Handwerker#71)

- Neue Leistung **IA-10** „Innenwand: Sockelabdichtung mineralische Dichtschlämme 2-lagig, bis 1,50 m Höhe“
  (m², Stückliste 4 kg/m² Dichtschlämme, 24 min/m², Herstellkosten 25,60 €, Preisliste 37,12 €; Annahme 2.3).
  Langtext IA-05 präzisiert: Innenseite erdberührter Kelleraußenwände.
- Neue optionale Vorlage `10_leistungsregel_position.csv` (nur mit `corevatis`): positionsgenaue Regeln der
  Innenabdichtung, die der Angebotsgenerator für Aufmaßzeilen mit nur einem Verfahren wählt -
  „IA-05 Außenwand bis 1,50 m“ (Wandtyp Außenwand), „IA-10 Innenwand bis 1,50 m“ (Wandtyp Innenwand, nur
  zu sanierende Wände, Sanierhöhe = Feuchtehorizont + 30 cm), „IA-08 Boden zu sanieren“. Vorher ergaben
  feuchte Innenwände keine Position.
- Importer: liest die Vorlage nach den Artikeln, vergleicht `parameter` (JSON) inhaltlich; neue Funktion
  `katalog.importer.nachziehen` (nur genannte Artikel und fehlende Regeln, ohne Überschreiben).
- Patch `v0_5.katalog_innenwand`: ergänzt IA-10 (Artikel, Preis, Stückliste) und die Regeln, präzisiert den
  unveränderten Langtext von IA-05 und schaltet die Testregel zum Artikel IA-150 (Abnahme 2.2, ohne
  Stückliste, deckt IA-05 und IA-10 doppelt ab) ab, statt sie zu löschen.
### Arbeitskosten nach § 35a EStG: Maschinen- und Fahrtkosten (Handwerker#105)

- Neues Item-Feld `cv_maschinenkosten` („Maschinen- oder Fahrtkosten (§ 35a EStG)“): Kosten des Artikels sind
  Maschinen-, Geräte- oder Fahrtkosten. Nach § 35a Abs. 3 EStG und dem BMF-Schreiben vom 9.11.2016 gehören sie zu
  den begünstigten Arbeitskosten, nicht zum Material. Im Katalog ist MAT-BAGGER („Minibagger … (Gerätemiete)“,
  Stückliste AA-01) gekennzeichnet (Spalte `cv_maschinenkosten` in `06_item.csv`, Patch
  `v0_5.maschinenkosten_35a` für bestehende Sites).
- `angebot.generator`: die Stücklistenzeilen gekennzeichneter Artikel gehen als `maschinen_je_einheit` (Teil von
  `material_je_einheit`, Preis unverändert) an die Kalkulation des Kerns - nur wenn der Kern den Schlüssel kennt
  (corevatis mit `fix/lohn-35a`), sonst wie bisher.
- `abrechnung.lohn_material`: `kalkulation.maschinen` geht an `corevatis.engine.anteile` (zählt dort zum Lohn,
  mit Zuschlägen samt Anteil am Materialzuschlag); ein gekennzeichneter Artikel als eigene Position (Anfahrt,
  Gerätestunde) ist 100 % Lohn wie eine Regiestunde, auch als Nachtrag der Schlussrechnung.
- Corevatis Abrechnung Einstellungen: „Lohnanteil mit Zuschlägen“ (Standard an) begründet in der Beschreibung,
  warum Gemeinkosten und Gewinn anteilig verteilt werden; „Lohn und Material je Position ausweisen“ nennt die
  Auftragsbestätigung und den Wortlaut „Arbeitskosten“.
- Druck-Tests (`test_angebotsdruck`, `test_rechnungsdruck`) prüfen mit erpnext_druckformate `fix/lohn-35a` den
  Wortlaut „davon Arbeitskosten (§ 35a EStG, begünstigt)“ und den Satz zur Begünstigung, sonst wie bisher.
- Bestehende Angebote behalten ihre Aufteilung (die gespeicherte Kalkulation kennt die Maschinenkosten nicht);
  ein neu erzeugtes Angebot rechnet sie zum Lohn.

### Zeitfenster der Zeiterfassung (Handwerker#108)

- `zeiterfassung.zeitraum_pruefen`, `zeit_buchen` und `lauf_starten` nehmen optional `zurueck`/`voraus`
  (`timedelta`, nur als Schlüsselwort) entgegen: das Fenster des Kerns, in der Vorführung 365 Tage für eigene
  Buchungen. Es erweitert das Standardfenster (31 Tage zurück, 1 Tag voraus), verengt es aber nicht; ohne
  Angabe (Desk) gilt der Standard. Vorher lehnte der Adapter Zeitpunkte der Vorführungsuhr ab, die mehr als
  einen Tag voraus standen.
- Fehlertext von `ausserhalb_zeitraum` je Richtung („Der Zeitpunkt liegt 4 Tage in der Zukunft; erlaubt ist
  höchstens 1.“ bzw. „… in der Vergangenheit …“) statt immer „nur für die letzten 31 Tage“; `details` mit
  `richtung` (`zukunft`/`vergangenheit`), `abstand_tage`, `erlaubt_zurueck_tage`, `erlaubt_voraus_tage`,
  `max_tage_zurueck` bleibt.

### Phase 3 P3.7 Lohn und Material getrennt ausweisen (Handwerker#105)

- Felder `cv_materialanteil`/`cv_lohnanteil` je Position an Quotation Item (Spiegel Sales Order Item) und
  Sales Invoice Item (`cv_materialanteil` neu neben dem § 35a-`cv_lohnanteil`), Abschnitt „Material und
  Lohn“ mit `cv_material_netto`/`_brutto`, `cv_lohn_netto`/`_brutto` an Quotation (Spiegel Sales Order)
  und Sales Invoice; nur lesen, gesetzt vom Server.
- Neues Modul `abrechnung.lohn_material` (Hook `validate` an Quotation, Sales Order, Sales Invoice nach
  `rechnung_validieren`): rechnet über die Kern-Engine `corevatis.engine.anteile` (per Name, ohne Kern
  nichts). Angebot aus der Kalkulation (`cv_kalkulation_json`), Regiestunden (Verfahren „Sonstiges“, Einheit
  Stunde) 100 % Lohn, sonst „Lohnanteil Standard (%)“; Auftrag spiegelt die Angebotszeile anteilig, Rechnung
  die Auftragszeile (Teilmengen anteilig). Abschlags- und Schlussrechnung: Lohn = § 35a-Lohnanteil der
  Abrechnung, Abzugszeilen zählen nicht (Summen = Leistung); gewöhnliche Rechnungen bekommen den
  § 35a-Lohnanteil aus denselben Werten.
- Abrechnung: Abschlagsrechnungen nehmen den Lohn des Auftrags aus `cv_lohn_netto`, die Schlussrechnung den
  Lohn je Einheit aus `Sales Order Item.cv_lohnanteil` (vorher Kalkulation je Artikel); Nachträge als
  Regiestunde 100 % Lohn. § 35a und Ausweis zeigen so dieselben Zahlen.
- Corevatis Abrechnung Einstellungen: „Lohnanteil mit Zuschlägen“ jetzt Standard an (Material + Lohn =
  Netto), neuer Schalter „Lohn und Material je Position ausweisen“ (Standard an, Druckformat). Schutz der
  Check-Defaults wie Handwerker#99 (`load_from_db`).
- Patch `v0_5.lohn_material`: Felder anlegen, „Lohnanteil mit Zuschlägen“ einmalig an, Angebote, Aufträge
  und Rechnungen (nicht storniert) nachrechnen, ohne `modified` zu ändern.
- Tests: `test_lohn_material` (Einstellungen, Angebot → Auftrag → Teilrechnung, Abschlag und
  Schlussrechnung mit Regiestunde, ohne Engine), `test_angebot_felder`, Druck in `test_angebotsdruck`
  und `test_rechnungsdruck` (nur mit erpnext_druckformate ab #105); `AnteileStellvertreter` in
  `tests/_abrechnung.py`. Bestehende Abrechnungstests rechnen weiter ohne Zuschläge.

### Phase 3 P3.5 Sprachnotizen (Handwerker#97)

- Neue Datei `public/js/sprachnotiz.js` (doctype_js an Befund, Baustellenmeldung und ToDo): Abschnitt
  „Sprachnotiz“ im Formular mit Audio-Player und „Herunterladen“ für die Aufnahme aus der App (Kern-Feld
  `sprachnotiz` bzw. `cv_sprachnotiz` am ToDo; die Büro-Aufgabe einer Meldung zeigt die Sprachnotiz der
  Meldung). Abgespielt werden nur Audiodateien der eigenen Site; das Leserecht prüft der Server beim Laden der
  privaten Datei. Ohne Kern fehlt das Feld, es erscheint nichts.
- Baustellenakte (Projekt/Auftrag): Hinweis „Sprachnotiz“ an Meldungen und Tagesrapporten mit Aufnahme;
  Einträge der Art `mangel` heißen „Meldung“.

### Phase 3 P3.3 Pläne mit Pins (Handwerker#95)

- Seitenleiste „Betrieb“: Eintrag „Pläne“ (Doctype `Plan` aus dem Kern) direkt nach „Objekte“.
- Foto-Galerie am Befund: Fotos, die als Pin auf einem Plan liegen, tragen die Pin-Nummer („Pin 3“); liegt der
  Befund selbst als Pin auf Plänen, steht unter der Galerie „Auf Plan: …“ mit Link. Daten über die Kern-Methode
  `corevatis.plaene.desk.befund_pins` (nur Pläne mit Leserecht). Die Galerie hat dafür die Option `nachher`.

### Phase 3 P3.4 Aufgaben (Handwerker#96)

- Seitenleiste „Betrieb“: Eintrag „Aufgaben“ nach „Nachrichten“ (ToDo-Liste, gefiltert auf Aufgaben mit Quelle
  und Status offen). Die ToDo-Liste heißt „Aufgaben“, zeigt den Titel statt der Beschreibung, den Status als
  Pille (Offen, Erledigt, Verworfen, Überfällig rot, fälliges Datum rot) und springt mit „Öffnen“ zum Vorgang;
  Knopf „Neue Aufgabe“ für Büro, Meister, Monteur.
- Knopf „Aufgabe“ (Gruppe „Erstellen“) an Anfrage, Auftrag und Projekt: Dialog mit „Was ist zu tun?“,
  „Fällig bis“, „Wichtigkeit“, „Wer kümmert sich?“ und Beschreibung; legt über die Kern-Methode
  `corevatis.aufgaben.desk.anlegen` an (dieselben Prüfungen wie in der App).
- ToDo-Formular einer Aufgabe: „Erledigt“/„Wieder öffnen“ statt „Close“/„Reopen“, Sprung zum Vorgang, Hinweis
  „überfällig“ bzw. auf die Meldung.
- Prozessleiste: Pille „Aufgaben: n offen“ (gelb; mit überfälligen rot, alle erledigt grün) mit Knopf zur
  gefilterten Aufgabenliste; zählt Aufgaben an Anfrage, Aufträgen und Projekten des Vorgangs (nur lesbare,
  ToDo-Rechte des Benutzers). Ohne Kern entfallen Pille, Knöpfe und Listenanpassung.
### Neu (Phase 3 P3.2 Bautagebuch, Handwerker#94)

- Modul `corevatis_erpnext.bautagebuch` für den Kern (`corevatis.bautagebuch`): Hook `corevatis_anwesenheit_adapter`
  mit `anwesenheit(projekt, von, bis)` (Timesheet-Zeilen des Projekts je Mitarbeiter mit Benutzer) und
  `projekte_mit_zeiten(von, bis)` (nächtlicher Job), Hook `corevatis_koordinaten_adapter` mit `koordinaten(projekt)`
  (Objektanschrift bzw. Projektadresse über die Geokodierung, höchstens eine neue Anfrage je Aufruf; nur gefragt,
  wenn im Kern „Wetter automatisch“ eingeschaltet ist).
- Projekt: Knopf „Bautagebuch“ mit Dialog Zeitraum (erzeugen/aktualisieren, danach Liste gefiltert; „PDF in die
  Mappe“ für den Zeitraum); Prozessleiste mit Pille „Bautagebuch: n Tage“ und Knopf zur Liste.
- Leiste „Betrieb“: Eintrag „Bautagebuch“ unter „Meldungen“.
- Rechte: Knopf und Pille nur für Büro, Meister und System Manager; der Monteur hat im Kern kein Recht am
  Bautagebuch (Security-Review: Daten aller Monteure eines Tages), die Pille bleibt für ihn leer.

### Neu (Phase 3 P3.1 Foto-Markierung, Handwerker#93)

- **Foto-Galerie**: Fotos aus Fototabellen mit gesetztem `markiert` (am Befund: `Befund Foto`, Markierung aus der
  App mit Kreis, Pfeil, Freihand, Maßpfeil, Text und nummerierten Pins) tragen das Kennzeichen „markiert“ im
  Vorschaubild und in der Bildunterschrift der Vollansicht. `crm.galerie.get_bilder` liefert dazu `markiert` je
  Bild (nur mit Leserecht auf das Feld). Rapport und Meldung haben im Desk keine Galerie (keine neue gebaut).

### Behoben (Phase 2, Abnahme Build 33 auf mvp-test)

- **Meister ohne Benutzerberechtigung auf den eigenen Employee**: ERPNext legt beim Employee mit Benutzer eine User
  Permission auf diesen Employee an (für alle Doctypes). Dem Meister, der das Team plant, sperrte sie den Bericht
  „Fahrzeuglager“ (Filter auf einen Monteur wurde verworfen), „Fahrzeug beladen“ (403 beim Anlegen der Umlagerung
  mit `cv_mitarbeiter`), zuvor Stundenkonto und Abwesenheiten. `prozess.mitarbeiter.ensure_meister_ohne_benutzerberechtigung`
  (nach `migrate`) entfernt sie für Benutzer mit Rolle Meister und schaltet `create_user_permission` am Employee ab;
  `Employee.validate` hält das; `User.on_update` räumt beim Vergeben der Rolle auf. Monteure behalten ihre.
- **Kundenportal, Unterschrift**: eine durchgezogene Unterschrift (ein Strich) wurde mit „Bitte unterschreiben Sie im
  Feld“ abgelehnt, weil zwei Striche verlangt waren; jetzt zählt ein Strich mit Bewegung, ein bloßer Tipper nicht.
- **Termin anlegen**: Legt ein zweiter Büro-Benutzer (oder eine Vertretung) einen neuen Besichtigungstermin an,
  scheiterte das Absagen des alten privaten Termins eines anderen Benutzers mit 403 (Frappe gibt private Termine nur
  dem Eigentümer und per Freigabe frei). Wer Termine schreiben und die Anfrage bearbeiten darf, darf den alten
  Termin dieser Anfrage absagen.
- **Bestellung**: Zustandstext nach Teillieferung zählt Positionen („1 von 8 Positionen geliefert“) statt des
  mengengewichteten ERPNext-Prozentwerts („0 % geliefert“ bei 0,74 m³ von 1.100 Einheiten); vollständig
  gelieferte Bestellungen zeigen das auch im Status „To Bill“ (grün, kein Hauptknopf).
- **Berichte „Fahrzeuglager“ und „Nachbestellen“** (Nachprüfung Build 35): die Knöpfe „Fahrzeug beladen“, „Bedarf
  anlegen“ und „Mindestbestand setzen“ liefen für alle Rollen ins Leere (Konsole: `Cannot read properties of
  undefined (reading 'beladen_dialog')`), weil `materialwirtschaft.js` nur über `doctype_js` an den Formularen
  geladen wird, nicht auf Berichtsseiten. Die Datei ist jetzt `materialwirtschaft.bundle.js`; die Berichte laden sie
  per `frappe.require` nach, die Formulare bekommen sie weiter über `doctype_js`.
- **Meister liest Mitarbeiter** (Nachprüfung Build 34): ohne Leserecht auf Employee verwarf jedes Link-Feld auf
  Employee - Berichtsfilter „Fahrzeuglager“ und „Stundenkonto“, Dialoge - seinen Wert mit „Unzureichende
  Berechtigung für Employee“. `mitarbeiter.ensure_rechte` gibt dem Meister Lesen und Bericht am Employee; die
  Konto-Felder `cv_urlaubsanspruch`, `cv_urlaub_uebertrag`, `cv_saldo_uebertrag` liegen jetzt auf Feldrechtstufe 2
  (Büro und System Manager), die persönlichen ERPNext-Felder bleiben auf Stufe 1 (Issue #72).

### Hinzugefügt (Kundenportal, Phase 2 P2.7, Handwerker#91)

- **Kundenseite `/kunde/<token>`** ohne Anmeldung: Der Kunde sieht seinen Vorgang (Objekt, Anfrage), den
  Besichtigungstermin mit „Termin bestätigen“, das freigegebene Angebot (Netto, Brutto, Gültigkeit, PDF) mit
  „Angebot verbindlich annehmen“ (Unterschrift im Canvas, Pflichthaken zu den Bedingungen), den Stand der
  Ausführung, seine Rechnungen (Art, Datum, Betrag, Fälligkeit, Zahlstand, PDF), freigegebene Dokumente der
  Mappe und kann Fotos hochladen (JPEG/PNG/HEIC bis 10 MB, höchstens 30 je Zugang). Deutsch, Sie-Form, mobil
  brauchbar, ohne externe Skripte; Fußzeile verlinkt den Platzhalter `/datenschutz` (1.2).
- **Doctype „Kundenzugang“** (`corevatis_erpnext.kundenportal.zugang`): Zufallstoken (`secrets.token_urlsafe(32)`),
  gespeichert nur als SHA-256 (`token_hash`, unique), gültig 90 Tage (einstellbar bis 365), Widerruf mit
  Zeitpunkt, Zähler Aufrufe/Uploads, letzte Nutzung. Anker ist die Anfrage, sonst Projekt, Angebot oder Auftrag
  (`anker_fuer`, über `prozess.kette.lead_von`). Ein neuer Link widerruft die aktiven Zugänge des Vorgangs.
  Rechte: System Manager voll, Büro lesen/schreiben/anlegen, Meister lesen.
- **Desk**: Knopf „Kundenzugang“ an Anfrage, Kunde, Projekt und Angebot (`public/js/kundenzugang.js`):
  Dialog mit den Zugängen des Vorgangs (gültig bis, Aufrufe, Fotos, Widerrufen) und „Neuen Link erzeugen“ -
  Link und QR-Code (`pyqrcode`, serverseitig) erscheinen genau einmal zum Kopieren; Leiste „Betrieb“ → Belege
  → „Kundenzugänge“ (`betrieb.json`, `modified` 2026-11-03 10:00); Formular des Zugangs mit „Neuen Link
  erzeugen“ und „Widerrufen“. Direktes Anlegen im Formular erzeugt das Token und zeigt den Link einmalig.
- **Felder** (`kundenportal.felder`): `Event.cv_kunde_bestaetigt`/`cv_kundenzugang`, `Quotation.cv_kunde_angenommen_am`/
  `cv_kunde_unterschrift`, `File.cv_kunde_sichtbar` („Für den Kunden sichtbar“, Standard aus). Prozessleiste:
  Schritt Termin zeigt „· vom Kunden bestätigt“.
- **Aktionen** (`kundenportal.aktionen`, Gast-Endpunkte, nur POST außer Download): Terminbestätigung setzt die
  Felder am Termin und kommentiert Termin und Anfrage; die Annahme ruft die vorhandene Annahme 4.4
  (`angebot.annahme.angebot_annehmen`: Kunde, Auftrag eingereicht, Projekt, Anfrage „Gewonnen“) im
  Administrator-Kontext nach eigener Token-Prüfung auf, legt die Unterschrift als private PNG-Datei am Angebot
  ab (Mappe 03 Angebot, Dokumentart „Unterschrift Kunde“), schreibt Zeitpunkt und Datei ans Angebot und
  kommentiert Angebot und Auftrag mit gekürztem Absender-Hash; Kundenfotos landen als private Dateien an der
  Anfrage (sonst Projekt), Mappenregel Herkunft Webform → Ordner 01 Anfrage „Kundenfoto“, für den Kunden
  sichtbar; Downloads liefern die Mappendatei (über den Kern, S3-fähig), sonst den frischen Druck.
- **Textbaustein-Art „Kundenportal“** (P2.6): Begleittext der Seite mit Platzhaltern `[[Anrede]]`, `[[Firma]]`,
  `[[Objekt]]` …; Baustein „Kundenportal Begrüßung“ wird einmal angelegt (`ensure_setup`).
- **Sicherheit**: Token-Vergleich mit `hmac.compare_digest` auf dem Hash; unbekannt, abgelaufen und widerrufen
  antworten gleich (404); Rate-Limit im Redis-Cache je IP (120 Seitenaufrufe, 20 Fehlversuche je 10 min) und je
  Zugang (40 Aktionen je 10 min); kein frei wählbarer Belegname in den Aktionen (alles aus dem Zugang
  abgeleitet); Frappe prüft bei Gästen kein CSRF - Schutz ist das geheime Token im Anfragekörper; Unterschrift
  nur als PNG (Magic Bytes, 300 B bis 500 KB), Fotos nur JPEG/PNG/HEIC (Magic Bytes, `filetype`), Dateiname
  vom Server, immer privat; Kundendaten in der Vorlage HTML-maskiert, Begrüßung ist der (von Frappe beim
  Speichern bereinigte) Textbaustein des Büros; Fehler an den Kunden nur als Klartext, Details im Error Log.
- Härtung (Security-Review): `Kundenzugang.validate` bestimmt Anker, Kundenname und Token-Hash immer selbst und
  verwirft mitgeschickte Werte (REST mit Schreibrecht konnte sonst einen fremden Vorgang als Anker oder einen
  eigenen Hash setzen), verlangt Leserecht am Vorgang für den anlegenden Benutzer, nullt die Zähler, begrenzt
  die Gültigkeit auf 365 Tage und lässt Vorgang, Anker und Hash nach dem Anlegen nicht mehr ändern
  (`set_only_once` plus Prüfung in `validate`). Zweiter Nachtrag: keine Sicherheitsentscheidung hängt mehr an
  Dokument-Flags - die Rechteprüfung läuft immer, das Token erzeugt `validate` stets selbst und reicht den
  Klartext nur über das nicht gespeicherte Attribut `_token_klar` an `zugang.erzeugen` zurück.
  Test `test_zugang_manipulation_abgewiesen`.
- Tests `tests/test_kundenportal.py`: Felder und Einrichtung, Zugang anlegen/prüfen/widerrufen/ablaufen/ersetzen,
  Rechte, Seite (Kontext, 404), Terminbestätigung, Annahme mit Unterschrift (Auftrag, Projekt, Datei, Felder,
  Kommentar, Gast-Kontext), Fotoupload (Typ, Größe, Höchstzahl, Zähler), Dokumente und Download (Freigabe,
  ersetzte Versionen, fremde Datei 404), Rechnungsliste, Rate-Limit.
### Hinzugefügt (Materialwirtschaft, Phase 2 P2.2, Handwerker#90)

- Modul `corevatis_erpnext.materialwirtschaft`: ERPNext Einkauf und Lager in der Reihenfolge des Betriebs, Belege
  bleiben Standard (Material Request, Purchase Order, Purchase Receipt, Stock Entry, Item Reorder).
- **Material bestellen**: Hauptknopf an der Materialanforderung (Art Einkauf, offene Positionen) und Knopf „Material
  bestellen“ am Projekt (Menü Planung) sowie in der Prozessleiste. Dialog mit Positionen, Lieferant je Zeile
  (Standardlieferant aus Artikel/Artikelgruppe/Marke, sonst Auswahl), Lieferung ins Hauptlager oder direkt ins
  Fahrzeuglager eines zugewiesenen Monteurs, „sofort freigeben“. Ein Entwurf wird dabei eingereicht; je Lieferant
  entsteht eine Bestellung mit Projekt am Beleg und an jeder Zeile, Preis aus Einkaufspreisliste, letztem
  Einkaufspreis oder Bewertungspreis (`bestellung_vorbereiten`, `bestellen`).
- **Lieferung buchen**: Hauptknopf an der freigegebenen Bestellung. Dialog mit bestellter, schon gelieferter und jetzt
  gelieferter Menge (Teillieferung), Lager, Lieferschein-Nummer, Scanfeld (EAN zählt die Zeile hoch, `ean_suchen`),
  „sofort buchen“ (Bestand steigt). Ergebnis mit Bestand danach und offenen Positionen (`lieferung_vorbereiten`,
  `lieferung_buchen`). Die Eingangsrechnung bleibt ERPNext „Erstellen → Eingangsrechnung“.
- **Fahrzeug beladen**: Bericht „Fahrzeuglager“ (Leiste „Betrieb“ → Zeiten und Material) je Mitarbeiter: Bestand auf dem
  Fahrzeug, Bedarf der zugewiesenen Baustellen der nächsten sieben Tage (Materialanforderungen abzüglich schon
  entnommener Mengen), Mindestbestand des Fahrzeugs, Bestand im Hauptlager, Vorschlag; Knopf „Fahrzeug beladen“ bucht
  die Umlagerung Hauptlager → Fahrzeuglager (`fahrzeug_vorschlag`, `fahrzeug_beladen`).
- **Mindestbestand** je Artikel und Lager als ERPNext `Item Reorder` (Art Bestellung oder Umlagerung; Fahrzeuglager
  standardmäßig Umlagerung), gepflegt über den Dialog „Mindestbestand setzen“ (`mindestbestand_setzen`, Büro und
  Meister). Bericht „Nachbestellen“: Artikel unter Mindestbestand mit Bestand, verfügbarer Menge, Nachbestellmenge wie
  der ERPNext-Job, Art und Lieferant; Knopf „Bedarf anlegen“ legt je Lager und Art eine Materialanforderung an
  (`nachbestellung_anlegen`). Die ERPNext-Nachbestellautomatik und ihre E-Mails werden bei `migrate` ausgeschaltet
  (`Stock Settings.auto_indent`, `reorder_email_notify`).
- **Prozessleiste**: Pille „Material: 2 offen, 1 bestellt, 3 geliefert“ am Projekt und Auftrag mit Knopf „Bestellen“
  bzw. „Material“ (`stand`). Zustandstexte an Materialanforderung, Bestellung und Lieferung; verwirrende
  ERPNext-Menüpunkte (Angebotsanfrage, Lieferantenangebot, Arbeitsauftrag, Pickliste, Zahlung …) für Büro und Meister
  ausgeblendet.
- **Rechte** (`ensure_rechte`, idempotent): Büro voll auf Lieferant, Bestellung, Lieferung, Materialanforderung sowie
  lesen/schreiben/anlegen/buchen auf Materialbuchung (Fahrzeug beladen); Meister liest Lieferant, Bestellung und
  Lieferung (Materialanforderung und Materialbuchung hat er aus 0.4 voll). Leiste „Betrieb“: Bestellungen,
  Lieferungen, Nachbestellen, Fahrzeuglager (Zeiten und Material), Lieferanten (Stammdaten); Übersetzungen Lieferant,
  Bestellung, Lieferung, Mindestbestand.
- Nachkalkulation (P2.1) unverändert: Wareneingänge und Umlagerungen zählen nicht als Verbrauch (Test).

### Geändert (Abwesenheiten: Meister sieht das Team, Phase 2 P2.5, Handwerker#88)

- Liste und Formular „Abwesenheit“: der Meister sieht jetzt alle Abwesenheiten des Betriebs (Entscheidung Mike
  01.11.), aber ohne Art und Notiz - beide Felder liegen auf Feldrechtstufe 1 (nur Büro und System Manager),
  fremde Einträge erscheinen als „Abwesend“ (Krank ist ein Gesundheitsdatum). Der Monteur sieht weiter nur die
  eigenen Einträge, auch ohne Art; die eigene Art steht im Stundenkonto (Ansicht „Tage“) und in der App.
  Der Link `mitarbeiter` ignoriert Benutzerberechtigungen, weil ERPNexts automatische User Permission auf den
  eigenen Employee die Liste sonst auf die eigene Zeile filtert (`planung/abwesenheit.py`: `sieht_team`).
- Nachtrag (Review): Der Rahmen des Meisters ist seine Firma (`firma_des_benutzers`, Unterabfrage auf Employee),
  nicht „alle“. Filtern, Sortieren und Gruppieren nach Art oder Notiz sind ohne Stufe-1-Recht gesperrt
  (`geschuetzte_felder_pruefen` prüft die Listenanfrage; Frappe streicht geschützte Felder nur aus der Ausgabe und
  prüft Filter bloß auf Existenz). Änderungsverfolgung des Doctypes aus - Versionen zeigten die Art jedem Leser;
  Patch `v0_4.abwesenheit_versionen` löscht vorhandene Versionen.

### Hinzugefügt (Mitarbeiter: Urlaub, Krank, Stundenkonto, Phase 2 P2.5, Handwerker#88)

- Felder am Employee (`corevatis_erpnext.mitarbeiter.ensure_setup`, nach `after_install`/`after_migrate`):
  Urlaubsanspruch in Tagen je Jahr (`cv_urlaubsanspruch`, leer = 30), Resturlaub Vorjahr (`cv_urlaub_uebertrag`),
  Stundenkonto zum 1. Januar (`cv_saldo_uebertrag`). Kein HRMS, keine Leave Application.
- Bericht „Stundenkonto“ (Script Report, Leiste „Betrieb“ unter „Stammdaten“): je Mitarbeiter und Monat Solltage,
  Soll (Tagesarbeitszeit der Planung an Mo-Fr ohne Feiertage und freie Tage der Holiday List, halbe Feiertage halb),
  Abwesenheiten (Urlaub, Krank, Schulung, Sonstiges senken das Soll), Ist aus den Timesheets ohne Pausen, Saldo,
  Saldo Jahr (Übertrag plus Monate), Urlaubskonto (Anspruch, genommen, geplant, Rest) und Kranktage. Ansicht
  „Tage“: Monatsübersicht eines Mitarbeiters je Kalendertag mit laufendem Saldo. Tage nach heute zählen nicht ins
  Soll. Büro und Meister sehen alle, Monteur nur sich; Urlaubs- und Krankheitsspalten nur Büro und der Betroffene.
- Formular „Abwesenheit“: Hinweis zum Urlaubskonto (Rest vor und nach dem Eintrag, `urlaub_hinweis`); Urlaub über
  den Rest wird gespeichert, aber gemeldet (keine Sperre). Meister und Monteur dürfen den Report auf Abwesenheit.
- Hook `corevatis_stundenkonto_adapter` → `corevatis_erpnext.mitarbeiter.fuer_benutzer(user, jahr, monat)` für
  den Kern-Endpunkt `mein_stundenkonto` der App (Monat, Saldo Jahr, Urlaubskonto, Tage des Benutzers).
- Nachtrag (Review): Tagesansicht eines fremden Mitarbeiters liefert ohne Recht auf die Art nur „Abwesend Tage“
  gesamt (keine getrennten Kranktage); Link „Tage“ im Bericht ohne Inline-onclick (delegiert in `onload`).
### Hinzugefügt (Nachkalkulation, Phase 2 P2.1, Handwerker#87)

- **Bericht „Nachkalkulation“** (Leiste „Betrieb“ → Abrechnung; Rollen Büro, Meister, System Manager; Filter
  Projekt, Firma, Ist-Zeitraum, Verfahren, nur laufende Projekte): je Projekt eine Summenzeile, darunter je
  Verfahren (Arbeitspaket) Soll- und Ist-Stunden (davon Fahrt), Verbrauch in Prozent, Fortschritt, Lohn Soll/Ist,
  Material Soll/Ist, Erlös netto, Marge Soll/Ist und Ampel. Soll aus der Kalkulation des angenommenen Angebots
  (`Quotation.cv_kalkulation_json`: Minuten und Material je Einheit, Stundensatz des Schemas), sonst aus der
  Stückliste; Ist aus den Timesheets (Arbeitszeit + Fahrzeit, 3.9) und den Materialentnahmen (Stock Entry
  „Material Issue“, 3.11) des Projekts, zugeordnet über das Arbeitspaket (`Task.cv_verfahren`). Nach freigegebener
  Schlussrechnung (7.2) zählt deren Netto je Verfahren als Erlös. Ampel: Ist ÷ (Soll × Fortschritt) bis 100 % grün,
  bis 115 % gelb, darüber rot; Ist ohne Soll gelb, ohne Ist grau. Modul `corevatis_erpnext.nachkalkulation`.
- **Bericht „Mengenregeln nachjustieren“** (Leiste „Betrieb“ → Abrechnung → „Regeln nachjustieren“): Lernschleife
  über freigegebene Schlussrechnungen - je Leistung, Mengenregel und Verfahren Angebotsmenge gegen Ist-Menge
  (Mengenfaktor) und Soll- gegen Ist-Minuten je Einheit (Zeitfaktor) mit Vorschlagstext ab 5 % Abweichung.
  Nur Anzeige: Leistungsregeln und Stücklisten bleiben unverändert.
- **Prozessleiste am Projekt**: Pille „Nachkalkulation: 68 % der Sollstunden, Marge 31 %“ mit Ampel und Knopf
  „Bericht“ (öffnet den Bericht mit dem Projekt als Filter), nur für Büro und Meister (`kette.status` →
  `nachkalkulation`).
- Seitenleiste „Betrieb“: Einträge „Nachkalkulation“ und „Regeln nachjustieren“ unter Abrechnung (Zeitstempel
  angehoben, damit `migrate` sie nachzieht).
- Lernschleife: je Schlussrechnung wird das Leserecht an Auftrag und Projekt geprüft (Zeilen ohne Recht fehlen,
  Zeitfaktor ohne lesbares Projekt leer) - wie die Kennzahl der Prozessleiste.

### Hinzugefügt (Textbausteine im Desk, Phase 2 P2.6, Handwerker#89)

- Textbausteine sind ERPNexts „Terms and Conditions“ mit Custom Fields: Art (Vorbemerkung,
  Zahlungsbedingungen, Gewährleistung, Schlusstext, Mahntext, Anschreiben, Sonstiges), Belegart, Verfahren,
  Firma, Reihenfolge, Gültig ab/bis, Mahnstufe (`textbausteine/felder.py`). Eintrag „Textbausteine“ in der
  Leiste „Betrieb“ unter Stammdaten; Büro schreibt, Meister liest (`textbausteine.ensure_rechte`).
- Platzhalter `[[Kunde]]`, `[[Objekt]]`, `[[Angebot]]`, `[[Betrag]]`, `[[Faelligkeit]]` … aus einem festen
  Katalog (`textbausteine/platzhalter.py`); unbekannte Platzhalter lehnt das Speichern mit der Liste ab.
  Angebot, Auftrag und Rechnung setzen die Werte beim Speichern ein (Vorbemerkung, Garantiehinweis,
  Zahlungsbedingungen), die Mahnung beim Erstellen; HTML-maskiert, kein Jinja über Text des Büros.
- Auswahl je Firma, Verfahren und Belegart mit Rückfall (`textbausteine.bester`): am Angebot für
  Vorbemerkung, Gewährleistung und Zahlungsbedingungen (ein Baustein mit Firma oder Verfahren schlägt den
  Firmenstandard), an Auftrag und Rechnung für Zahlungsbedingungen, wenn der Beleg keine hat.
- Vorschau am Baustein mit dem letzten Beleg der Belegart (`textbausteine.vorschau`, Knopf „Vorschau“,
  Platzhalter-Hilfe unter dem Text, `public/js/textbaustein.js`). Im Feld „Textbaustein“ der Belege
  erscheinen nur Zahlungsbedingungen und Sonstiges (`public/js/beleg_textbaustein.js`).
- Mahntexte: je Stufe ein Baustein „Mahntext …“ mit dem Brieftext aus 7.5; Speichern schreibt den Text in
  den Mahntyp der Firma bzw. aller Firmen ohne eigenen Baustein (`textbausteine.mahntext_uebernehmen`).
- Serienbrief aus der Kundenliste (Aktion „Serienbrief“, `public/js/kunden_serienbrief.js`): Baustein der
  Art Anschreiben, Betreff, Firma; je Kunde eine Seite mit Briefkopf, Anschrift, Datum, Betreff und Text, als
  eine private PDF-Datei (`textbausteine/serienbrief.erstellen`, pypdf). Nur Büro, höchstens 200 Kunden,
  kein E-Mail-Versand.
- Übersetzungen: „Terms and Conditions“ heißt im Desk „Textbaustein“. Tests: `tests/test_textbausteine.py`.

### Behoben (Phase 2, Abnahme Build 31 auf mvp-test)

- Stundenkonto: der Meister sah im Bericht nur die eigene Zeile, weil Frappe Berichtszeilen mit Link-Spalte
  gegen die Benutzerberechtigung auf Employee filtert (ERPNext legt sie je Mitarbeiter an); Spalte jetzt ohne Link.
- Mitarbeiter: Büro darf Employee lesen und schreiben (Urlaubsanspruch, Resturlaub, Übertrag) - ERPNext gab das
  nur HR-Rollen. Hinweis im Formular Abwesenheit verwirft überholte Antworten (von, dann bis).
- Serienbrief: Briefkopf und Fußzeile des Site-Templates werden wie im Druck mit Jinja gerendert; vorher stand der
  Vorlagentext im Brief und jeder Brief hatte zwei Seiten.
- Nachkalkulation: Marge in der Prozessleiste ganzzahlig („Marge 93 %“ statt „93.0 %“).
- Nachkalkulation: Spalte „Verbrauch %“ zeigte den maskierten HTML-Balken („<div style…“); jetzt Zahl mit Prozent.

### Geändert (Textbausteine, Härtung, Handwerker#89)

- Mahntexte dürfen keine Vorlagen-Syntax (`{{`, `{%`, `{#`) enthalten: ERPNext rendert den Brieftext des Mahntyps als
  Jinja; Bürotext bleibt reiner Text mit `[[…]]`-Platzhaltern. Der Abgleich in die Mahntypen berücksichtigt nur Firmen,
  die der Speichernde lesen darf.

### Behoben (Schlussrechnung, Abnahme Build 30, Handwerker#49)

- Bestätigungsmeldung „Schlussrechnung angelegt“ zeigte alle Beträge hundertfach: die Engine liefert Dezimal-Zeichenketten,
  `format_currency` las den Punkt im deutschen Zahlenformat als Tausendertrenner. `geld()` (Abrechnung, Mahnwesen) und die
  Angebotsmeldung wandeln jetzt zuerst in Zahlen. Rechnung und Druck waren richtig.

### Behoben (Desk-Assets, Abnahme Build 29, Handwerker#85)

- `betrieb.js`, `betrieb.css`, `mahnung_zahlung.js`, `zuweisung.js`, `lead_termin.js` sind jetzt Bundles
  (`*.bundle.js`/`*.bundle.css`, Inhalts-Hash im Dateinamen, Auflösung über assets.json in `app_include_*` und
  `frappe.require`). Die bisherigen festen Pfade `/assets/corevatis_erpnext/js/…` liefert nginx mit „immutable,
  ein Jahr“ aus; Cloudflare und Browser hielten alte Stände, auf mvp-test fehlte dadurch der Ungelesen-Zähler
  am Eintrag „Nachrichten“ zwei Deploys lang. Regel: keine festen Asset-Pfade mehr, neue Desk-Skripte als Bundle.

### Behoben (DATEV-Export, Abnahme Build 28, Handwerker#51)

- Debitorennummer vergeben: Zeilensperre auf den DATEV Settings der Firma bis zum Commit. Zwei fast gleichzeitige
  Exporte legten je eine Kontozeile des Kunden an; erpnext_datev verknüpft jede Buchung mit der Kontozeile, die
  Kundenbuchungen erschienen im nächsten Export doppelt (mvp-test: 17 statt 12 Zeilen).
- Export bricht ab, wenn ein beteiligter Kunde mehr als eine Kontozeile für die Firma hat (Hinweis mit Kundennamen).
- Seitenleiste „Betrieb“: Zeitstempel angehoben, damit `migrate` den Eintrag „DATEV-Export“ nachzieht (der Eintrag
  war im JSON, aber ohne neuen Zeitstempel wurde die Leiste auf bestehenden Sites nicht aktualisiert).
- Offene Posten: Knopf „Mahnen“ nur, wenn der Benutzer Mahnungen anlegen darf (Meister sah ihn und bekam 403).

### Geändert (Nachrichten in Echtzeit, Handwerker#85)

- Ungelesen-Pille der Leiste „Betrieb“ (`betrieb.js`) aktualisiert sofort beim socket.io-Ereignis
  `corevatis_nachricht` des Kerns und beim Sichtbarwerden des Tabs; der 60-s-Takt bleibt Rückfall.

### Hinzugefügt (DATEV-Monatsexport, Phase 1 P1.2 / Aufgabe 7.4, Issue Handwerker#51)

- **Doctype „DATEV Export“** (Leiste „Betrieb“ → DATEV-Export; Rolle Büro und System Manager, kein
  Löschen für Büro): Firma, Zeitraum (Vormonat vorbelegt), Belegarten (Rechnungen und Zahlungen oder alle
  Buchungen), Festschreibung 0/1, „nur noch nicht exportierte Belege“, Belegbilder mit Druckformat.
  „Export erstellen“ (`corevatis_erpnext.datev.export_erstellen`) liefert drei private Dateien:
  `EXTF_Buchungsstapel_<Kürzel>_<JJJJ-MM>_<Exportnummer>.csv` (DATEV-Format 700, Buchungsstapel Version 9, 120 Spalten,
  cp1252, CRLF), `EXTF_Debitoren_…csv` (Stammdaten der betroffenen Kunden) und `Belegbilder_…zip`
  (Rechnungs-PDFs, Dateiname = Rechnungsnummer, dazu `Belegliste.csv`); Kennzahlen, Protokoll und
  Belegliste am Export. Ein erstellter Export ist eingefroren; Wiederholung über einen neuen Export.
  `bench execute corevatis_erpnext.datev.monatsexport --kwargs '{"firma": "…", "jahr": 2026, "monat": 10}'`.
- **Buchungsstapel über erpnext_datev** (Abfragen `run_query` je Belegart, CSV-Schreiber, Kopfzeile), ergänzt
  um: nur nicht stornierte Hauptbucheinträge, Konto = Debitorennummer bei Kundenzeilen (statt Sammelkonto
  1400), Belegfeld 1 = Rechnungsnummer (bei Zahlungen die bezahlte Rechnung), Belegfeld 2 und Auftragsnummer
  = Auftrag, Buchungstext „Abschlag 1 Kunde Auftrag“ / „Schlussrechnung …“ / „Gutschrift zu RE-…“ /
  „Zahlung RE-… Kunde“, Beleginfo 7/8 Auftrag und Vorgang, Festschreibung aus dem Export, Texte ohne
  Formelzeichen (`datev.export`).
- **Debitorennummern** (`datev.debitoren`): fortlaufend ab `DATEV Settings.cv_debitor_start` (10000) je
  Kunde und Firma in `Party Account.debtor_creditor_number` - beim Anlegen des Kunden (`Customer.validate`)
  und im Export; Debitoren-Stammdaten auch ohne „primäre“ Adresse.
- **Formatprüfung** (`datev.pruefung`, ohne Frappe): Kopfzeile (EXTF, 700, Kategorie, Formatversion,
  Berater-/Mandantennummer, Wirtschaftsjahr, Sachkontenlänge, Zeitraum, Festschreibung, Währung, SKR),
  Spaltenzahl und -positionen, je Zeile Umsatz (Dezimalkomma), Soll/Haben, Konto/Gegenkonto, BU-Schlüssel,
  Belegdatum TTMM im Zeitraum, Belegfeld 1 (Pflicht, Zeichen, 36), Belegfeld 2 (12), Buchungstext (60),
  Beleginfo, Auftragsnummer, Fälligkeit; Debitoren-Stammdaten. Läuft vor dem Speichern des Exports
  (Fehler → Status „Fehlgeschlagen“, nichts gespeichert, nichts markiert).
- **Schutz vor CSV-/Formel-Injection** (Security-Review): jede Textzelle des Buchungsstapels - auch von
  erpnext_datev gelieferte wie die Zahlungsreferenz aus dem Kontoauszug - läuft über `debitoren.text`
  (Leerraum, Anführungszeichen, Semikolon, führende `= + - @`), Belegfeld 1/2 ohne führendes `+`/`-`,
  Belegliste im ZIP über `csv.writer` mit Anführungszeichen; die Formatprüfung lehnt Zellen ab, die mit
  einem Formelzeichen beginnen. Anrede der Debitoren aus Kunde oder Hauptkontakt (`Customer.salutation`
  fehlt in v16), Rufnummern `+49 …` als `0049 …`.
- **Markierung** `cv_datev_export` / `cv_datev_exportiert_am` an Sales Invoice und Payment Entry (nur
  Server); Wiederholungen werden im Protokoll und in der Belegliste („zuvor exportiert in“) genannt;
  Hinweis auf Storni nach einem Export.
- **Site-Template** `datev` (`_standard.json`): DATEV Settings je Firma mit Platzhalter-Berater-/
  Mandantennummer (0000000/00000, gemeldet), Sachkontenlänge 4, Gegenkonto 9090/9000, Debitoren ab 10000,
  Druckformat „Rechnung Corevatis“; Prüfpunkte DATEV und Konten Forderungen 1410 (ERPNext-SKR03), Bank 1200, Kasse 1000,
  Mahngebühren 2650 (SKR04: 1200/1800/1600/7100).
- CI: erpnext_datev (`version-16`) in der Test-Bench installiert. Tests `test_datev` (Formatprüfung ohne
  Site; Monatsexport mit Abschlag, Schlussrechnung, Gutschrift, Zahlung; Wiederholung, nur neue, Festschreibung,
  eingefrorener Export, Rechte, Fehlerpfad). Übersetzungen.
### Hinzugefügt (Meine Woche in der App, Phase 1 P1.4b, Issue Handwerker#84)

- `planung.woche.woche(user, von, bis)` als Datenquelle des Kern-Endpunkts `meine_woche` (Hook
  `corevatis_woche_adapter`): Termine, Arbeitspakete (mehrtägige je Tag auf die Arbeitszeit
  beschnitten), Abwesenheiten (Art nur für Betroffene und Büro), Feiertage der Firma, Fahrzeug je
  Einsatz, Überschneidungen und Fahrzeit-Hinweise wie die eigene Zeile der Plantafel; `geaendert` je
  Eintrag (`modified` des Belegs). Volle Objektadresse eigener Arbeitspakete mit Leserecht auf das
  Projekt (Navigation), fremde Vorgänge nur PLZ/Ort; keine Geokodierung aus der App. Nur der Benutzer
  selbst oder ein Planer darf die Woche eines Benutzers abrufen.

### Hinzugefügt (Nachrichten am Vorgang, Phase 1 P1.6, Issue Handwerker#80)

- Prozessleiste: Zeile „Nachrichten: n ungelesen“ mit Sprung zur Kern-Page `nachrichten`
  (`prozess.kette._nachrichten`, Feld `nachrichten` in `prozess.status`; `None` ohne Kern).
- Leiste „Betrieb“: Eintrag „Nachrichten“ (Kern-Page) und Ungelesen-Zähler als Badge und im
  Fenstertitel (`betrieb.js`, `corevatis_erpnext.nachrichten.ungelesen_gesamt`, alle 60 s).
- Modul `corevatis_erpnext.nachrichten`: Kern-Funktionen nur per `frappe.get_attr`
  (`KERN_STAND`, `KERN_UNGELESEN`, Vertragstest im Kern).
### Hinzugefügt (Abrechnung Teil 2: Rechnungsdruck, Mahnwesen, Zahlungseingang - 7.3/7.5, Issues Handwerker#50, #77)

- **Rechnungsdruck**: Standard-Druckformat „Rechnung Corevatis“ (erpnext_druckformate, DIN 5008 wie das
  Angebot) für Rechnungen, „Mahnung Corevatis“ für Mahnungen (Site-Template `huelshoff`, ablösbar
  gegenüber „Ausgangsrechnung“/„Mahnung“). Die Dokumentenmappe legt das Rechnungs-PDF damit ab.
  Tests `test_rechnungsdruck` rendern Abschlags-, Schlussrechnung (kurz, lang) und Mahnung als PDF
  (Muster über `CV_MUSTER_DIR`).
- **Offene Posten** (`abrechnung.offene_posten`, Script Report „Offene Posten“ in der Leiste „Betrieb“):
  offene Rechnungen mit Rest, Fälligkeit, Tagen überfällig, Mahnstufe, Mahnvorschlag und Vorgang; Knopf
  „Mahnen“ je Zeile. Prozessleiste Schritt Rechnung und Rechnungsformular zeigen „bezahlt am …“,
  „teilbezahlt“, „offen …, fällig seit 5 Tagen, 1. Mahnung vom …“.
- **Mahnwesen** (`abrechnung.mahnung`) auf ERPNext Dunning: Mahntypen Zahlungserinnerung, 1. Mahnung,
  2. Mahnung je Firma mit Brieftexten (`abrechnung/texte`, angelegt wenn fehlend, nie überschrieben),
  Vorschlag nach Fristen (7 Tage nach Fälligkeit, dann 14 und 14 Tage), Mahngebühr aus den Einstellungen
  (1. Mahnung 0 €, 2. Mahnung 5 €, Ertragskonto 2650/7100 oder Einstellung), Zahlungsfrist (7 Tage),
  `mahnung_erstellen` (gebucht, Kommentar am Vorgang, kein Versand). Felder `Dunning.cv_mahnstufe`,
  `cv_zahlungsfrist`, `cv_projekt`, `Dunning Type.cv_mahnstufe`; Rolle Büro bucht Mahnungen (Custom DocPerm).
- **Zahlungseingang** (`abrechnung.kontoauszug` ohne Frappe, `abrechnung.zahlungseingang`, Script Report
  „Zahlungseingang“): Kontoauszug als CSV (Sparkasse, VR-Bank; Trennzeichen/Spalten erkannt, eigenes
  Mapping in den Einstellungen) oder CAMT.053 hochladen (privat, höchstens 5 MB, nach dem Einlesen
  gelöscht) → ERPNext Bank Transactions zum Firmenkonto (wird beim ersten Import als „Geschäftskonto“
  mit dem Sachkonto Bank angelegt), Dubletten über `transaction_id`; Zuordnungsvorschlag nach
  Rechnungs-/Auftragsnummer im Verwendungszweck, Betrag und Kundenname; „Zuordnen“ bucht einen Payment
  Entry (Teilzahlung erlaubt) und gleicht die Buchung ab, Kommentar am Vorgang, Abschlagszeile „bezahlt“;
  „Ignorieren“ (`Bank Transaction.cv_ignoriert`, `cv_notiz`, `cv_import`). Dialoge in
  `public/js/mahnung_zahlung.js` (Rechnung „Mahnen“, Bank Transaction „Rechnung zuordnen“).
- Einstellungen „Corevatis Abrechnung Einstellungen“: Abschnitte Mahnwesen und Zahlungseingang.
- Tests `test_mahnwesen`, `test_zahlungseingang` (Parser ohne Site, Import, Vorschlag, Teilzahlung,
  Dublette, Rechte, Dateiprüfung), `test_rechnungsdruck`; Übersetzungen.

### Hinzugefügt (Plantafel, Phase 1 P1.4, Issue Handwerker#79)

- **Desk-Seite „Plantafel“** (`page/plantafel`, Leiste „Betrieb“ statt „Wochenplan“, Arbeitsbereich
  „Vorgänge“): Mitarbeiter × Tage (Woche Mo–Fr, Sa/So einblendbar, Monat) mit Besichtigungsterminen,
  Arbeitspaketen (Zeitraum mit Uhrzeiten, Zeitraster nach Arbeitszeit), Abwesenheiten, Feiertagen,
  Fahrzeug je Einsatz, Tageslast; Überschneidungen und doppelt eingeplante Fahrzeuge rot, Fahrzeitlücken
  (D14-Regeln) als Hinweis. Reihenfolge der Mitarbeiter einstellbar (Planungseinstellungen
  `plantafel_reihenfolge`). Büro/Meister planen, Monteure sehen ihre eigene Zeile (nur lesen).
  `…planung.plantafel.tafel(von, ansicht, wochenende, projekt, mitarbeiter)`.
- **Verschieben per Ziehen:** `…planung.plantafel.arbeitspaket_verschieben(task, datum, mitarbeiter,
  von_mitarbeiter, trotzdem, freigeben)` (Zeitraum um ganze Tage, Zuweisung 5.2 umgehängt; Konflikte:
  Abwesenheit, Termin des Mitarbeiters, Feiertag am Zieltag) und `…crm.termin.termin_verschieben(termin,
  beginn, meister, trotzdem)` (Dauer bleibt, dieselbe Konfliktprüfung wie `termin_anlegen`, Zuweisung und
  Freigabe nachgezogen, Bestätigungstext neu, nicht gesendet). Konflikt → `TerminKonflikt` (417) mit
  Grund und „Trotzdem verschieben“ (Vermerk); jede Verschiebung schreibt einen Kommentar an den Beleg.
- Doctype **Abwesenheit** (Mitarbeiter, von, bis, Art Urlaub/Krank/Schulung/Sonstiges, ganztags oder
  Uhrzeiten, Notiz; Büro schreibt, Meister/Monteur lesen eigene über `permission_query_conditions`/
  `has_permission`). Terminplanung (`terminplanung.belegungen`), Konflikttexte („… ist Di 04.11. abwesend
  (Urlaub).“), Zuweisungs-Hinweis und Wochenplan (`abwesend` je Zeile) kennen Abwesenheiten. Die Art
  (Urlaub, Krank, …) sehen nur Büro/System Manager und der Betroffene selbst, alle anderen „Abwesend“
  (Security-Review: Krankheit ist ein Gesundheitsdatum).
- Doctype **Fahrzeug** (Kennzeichen, Bezeichnung, aktiv, optional Lager) und Feld `cv_fahrzeug` an Task
  und Event; `…planung.plantafel.fahrzeug_zuordnen(doctype, name, fahrzeug, trotzdem)` mit Konflikt bei
  gleichzeitigem Einsatz.
- **Druck** `…planung.plantafel.drucken(von, wochenende, projekt)`: Wochenplan als PDF im Querformat
  (Mitarbeiter × Tage, Einsätze mit Fahrzeug und Adresse - PLZ/Ort für fremde Vorgänge -, Abwesenheiten,
  Feiertage).
- Report „Wochenplan“ (5.2) leitet im Desk mit seinen Filtern zur Plantafel um; Daten und REST bleiben.
  `terminplanung.task_belegung` als gemeinsame Umrechnung Task → Belegung. Tests `test_plantafel`,
  Erweiterung `test_terminplanung` (Abwesenheit sperrt den Meister).

### Hinzugefügt (Terminplanung Besichtigung, Durchstich D14, Anforderung 31.10.)

- **Wunschtermin des Kunden**: Webform „Kostenlose Einschätzung“ mit Abschnitt „Wunschtermin für die
  Besichtigung“ (bis zu zwei Wünsche mit Datum und Zeitfenster vormittags/nachmittags/ganztags,
  „schnellstmöglich“); Prüfung im Browser und am Server (nicht in der Vergangenheit, höchstens 90 Tage
  voraus, Datum als Datum). Neue Lead-Felder `cv_wunsch1_datum`, `cv_wunsch1_fenster`, `cv_wunsch2_datum`,
  `cv_wunsch2_fenster`, `cv_wunsch_schnell` (Abschnitt nach der Anfrage). Die Prozessleiste zeigt im
  Schritt „Termin“ den Wunsch („Wunsch: Di 04.11. vormittags, sonst Mi 05.11. ganztags“) und nach dem
  Anlegen „Termin am … mit <Meister>“.
- `corevatis_erpnext.planung.terminplanung`: `vorschlaege(lead, dauer, ab, bis, meister)` (freie Zeiten je
  Meister in den Wunschfenstern und den nächsten 10 Arbeitstagen, bewertet nach Wunsch → früh → wenig
  Fahrt) und `pruefen(lead, beginn, meister, dauer)` (Konflikte in Klartext), beide nur Büro/Meister/
  System Manager mit Leserecht auf die Anfrage; Orte fremder Anfragen nur als PLZ/Ort, mit Leserecht die
  Anschrift, nie Kundennamen. Belegt ist ein Meister durch Termine, zugewiesene Arbeitspakete und
  Feiertage; frei innerhalb der Arbeitszeit (Mo-Fr) der Planungseinstellungen.
- `planung.slotrechner` (ohne Frappe): Fahrzeit = Luftlinie × Umwegfaktor ÷ Geschwindigkeit, mindestens
  Mindestfahrzeit, auf 5 Minuten aufgerundet; Slots mit Fahrt von der vorigen Belegung (sonst Firma ab
  Tagesbeginn) und zur nächsten (sonst Firma bis Tagesende) plus Puffer.
- `planung.geokodierung` und Doctype **Geokoordinate**: Adressen einmal bei Nominatim (OpenStreetMap,
  feste URL, eigener User-Agent, eine Anfrage je Sekunde, Timeout 5 s, keine Weiterleitungen, höchstens 8
  neue Adressen je Aufruf) nachschlagen und dauerhaft merken (auch „Kein Treffer“, neuer Versuch nach
  30 Tagen); nach Netzfehler 10 Minuten Pause; ohne Koordinaten Standardfahrzeit „geschätzt“. Manuell
  korrigierte Einträge („Manuell“) werden nicht überschrieben. Keine API-Schlüssel, keine Kosten.
- **Doppelbuchungsschutz**: `crm.termin.termin_anlegen` (einziger Einstieg, auch für Sync-Handler der
  App) prüft Termin plus Fahrzeiten plus Puffer gegen Termine, Arbeitspakete und Feiertage des Meisters
  und lehnt mit `crm.termin.TerminKonflikt` (417) ab: Klartext in `grund`/Meldung („Martin Meister ist
  09:00–10:30 bei Mühlenweg 5, 25 min Fahrt.“) und maschinenlesbar in `konflikte` (je Eintrag `meister`,
  `von`, `bis`, `ort`, `fahrzeit_min`, `art`, `text`; auch in der Antwort `konflikte`); `trotzdem=1`
  (Keyword-Argument) übersteuert mit Vermerk am Termin. `terminplanung.berechne_vorschlaege(lead, dauer,
  ab, bis, meister)` ist die frei aufrufbare Berechnung ohne Rechteprüfung (Aufrufer prüft), die
  Whitelist-Methode `vorschlaege` der geprüfte Wrapper. Der eigene, zu verschiebende Termin derselben Anfrage zählt nicht als Konflikt. Die Dauer
  ohne Angabe kommt aus „Dauer Besichtigung“.
- Dialog „Termin anlegen“ (`public/js/lead_termin.js`): oben die besten fünf Vorschläge (Wunsch des
  Kunden hervorgehoben, Klick übernimmt Datum, Uhrzeit, Dauer und Meister), Live-Konfliktprüfung mit
  Haken „Trotzdem anlegen“.
- „Corevatis Planung Einstellungen“, Abschnitt „Besichtigungstermine“: Dauer Besichtigung (60 min),
  Puffer zwischen Terminen (10 min), Standardfahrzeit ohne Geokodierung (30 min), Geokodierung aus,
  Umwegfaktor (1,3), Durchschnittsgeschwindigkeit (45 km/h), Mindestfahrzeit (10 min).
- Tests `test_terminplanung` (Rechenregeln, Geokodierung mit gemocktem Nominatim, Vorschläge, Konflikte,
  Rechte, Prozessleiste), Webform-Tests zum Wunschtermin.

### Hinzugefügt (Durchstich A10: Termin aus der Anfrage in der App)

- `corevatis_erpnext.crm.termin_mobil`: Sync-Entität `termin_anlegen` für die App, registriert über den
  Hook `corevatis_sync_handlers` des Kerns. Rechte wie „Termin anlegen“ im Desk (Büro/Meister,
  Schreibrecht an der Anfrage; Monteur abgelehnt), Payload geprüft (Beginn höchstens einen Tag zurück,
  Dauer 15–720 min, Meister aktiv mit Rolle Meister, Notiz), ruft `crm.termin.termin_anlegen` ohne
  Kundenmail (Bestätigungstext in der Antwort zum Teilen), Anfrage über den Workflow von „Neu“ auf
  „Termin“; Überschneidung (`crm.termin.TerminKonflikt`, D14) → `rejected`/`CONFLICT` mit
  `details.grund = ueberschneidung`, Klartext und `details.konflikte`, `trotzdem` wird durchgereicht
  (die App bietet „Trotzdem anlegen“). Antwort: Terminzeile wie `befunde` plus Meister, `anfrage` und
  `bestaetigung`. Idempotent über das Sync-Journal des Kerns.

### Geändert (Wand-Bezeichnungen, Rückmeldung 31.10.)

- Angebotsgenerator: jede Position nennt die Wände des Grundrisses, aus denen ihre Menge kommt –
  neues Feld `Quotation Item.cv_waende` („Keller L: W1, W3, W5, W6 · 19,65 m²“ je Raum) und im
  Langtext ein Absatz „Grundlage: Keller L W1, W3, W5, W6; Keller 1 A, C, D.“ (Druckformat „Angebot
  Corevatis“ druckt ihn unter dem Langtext); bei fester Menge „Menge vom Meister bestätigt.“ Quelle:
  Engine-Beiträge je Wand (`mengen.beitraege` bzw. `aufmass_mengen` des Kerns), Rückfall die Zeilen
  „Wandfläche netto“ des Aufmaßes.

### Hinzugefügt (Durchstich A1: Besichtigung in der App bis zum Angebot)

- `corevatis_erpnext.angebot.mobil` für die App, vom Kern über den Hook `corevatis_angebot_adapter`
  gerufen: `katalog` (Items mit Verfahren und gebuchter Stückliste, Kurztext), `angebot_erzeugen`
  (Generator 4.1 mit Status und Summen), `angebote` (Anfrage/Objekt), `angebot_info`, `angebot_pdf`
  (Standard-Druckformat des Angebots).
- Generator: Aufmaßzeilen mit `menge_fest` (vom Meister bestätigte Menge) geben die Menge je Item
  vor (Summe, ohne Mengenregel); Hinweise „nicht verwendet“ dieser Zeilen entfallen.

### Hinzugefügt (6.1 Abnahmeprotokoll, 6.2 Garantie und Rechnungsfreigabe)

- `corevatis_erpnext.abnahme`: Hook `Abnahmeprotokoll.on_update` (Kern-Doctype) legt beim Abschluss
  je Mangel einen Task am Projekt an („Mangel: …“, Fälligkeit = Frist, Verweis auf Protokoll und
  Mangelzeile, idempotent über `Task.cv_abnahmeprotokoll`/`cv_mangel_zeile`, Projektende wird bei
  späterer Frist verschoben); Fehler halten den Abschluss nicht auf (Error Log, Meldung, Button
  „Mangel-Aufgaben erzeugen“ = `corevatis_erpnext.abnahme.maengel_tasks_erzeugen`).
- `Task.on_update`: erledigter Mangel-Task setzt die Mangelzeile auf „Behoben“ (wieder offen →
  „Offen“, über `corevatis.abnahme.mangel.status_setzen`); „Behoben“ im Protokoll erledigt den Task.
- Projekt: `cv_abnahmeprotokoll`, `cv_abnahme_ergebnis`, `cv_abgenommen_am` (nur Server),
  `cv_vertragsgrundlage`, `cv_gewaehrleistung_jahre`, `cv_gewaehrleistung_bis` (berechnet);
  Single „Corevatis Abnahme Einstellungen“ (Vorgabe BGB 5 Jahre, VOB/B 4 Jahre, Mangel-Tasks,
  Rechnungssperre).
- `Sales Invoice.validate`: Rechnung zu einem nicht abgenommenen Projekt wird abgelehnt
  (`RechnungOhneAbnahme`), außer Abschlagsrechnung (`cv_abschlag`, Vorgabe 0 – bis 7.1) oder
  Übersteuerung durch einen System Manager mit Begründung (`cv_abnahme_uebersteuert`,
  `cv_uebersteuerung_grund`, `cv_uebersteuert_von`, Kommentar).
- Prozessleiste: Schritt „Abnahme“ über das Abnahmeprotokoll (`projekt`; erledigt bei
  abgeschlossenem Protokoll „Abgenommen“/„mit Mängeln“, verweigert = aktiv) statt Projektstatus
  „Completed“; Aktion „Abnahmeprotokoll anlegen“; Leiste auch im Abnahmeprotokoll; Ausführung gilt
  mit der Abnahme als erledigt.

### Hinzugefügt (5.2 Wochenplan und Zuweisung)

- `corevatis_erpnext.planung.zuweisung`: `zuweisen(task, employee)` und
  `zuweisung_entfernen(task, employee)` (Büro/Meister, sonst 403): Zuweisung als ToDo auf die Task
  über `frappe.desk.form.assign_to` (dieselbe Quelle wie `baustellen_heute` und der Projektzugang
  des Kerns), idempotent mit Zeilensperre, Entfernen storniert das offene ToDo; nur aktive
  Employees mit aktivem Benutzer mit Rolle Monteur oder Meister; Hinweise bei Vorgänger nicht
  fertig und Tageslast über der Tagesarbeitszeit. `zuweisungen(task)`, `mitarbeiter()` für die
  Oberfläche.
- Script Report **„Wochenplan“** (`planung.wochenplan`): Mitarbeiter × Arbeitstage zweier Wochen
  (umschaltbar), Arbeitspakete als Unterzeilen, Sollstunden anteilig je Tag aus Beginn/Ende,
  Tageslast mit Markierung über 8 h, Zeile „Nicht zugewiesen“, Filter Projekt/Mitarbeiter,
  Dialog „Mitarbeiter zuweisen“; Kurzlink und Link im Arbeitsbereich „Vorgänge“, Button
  „Wochenplan“ am Projekt.
- Task-Formular: Abschnitt „Zuweisung (Corevatis)“ (`cv_section_zuweisung`, HTML `cv_zuweisung`)
  mit Mitarbeiter-Auswahl, Zuweisungen und Hinweis auf offene Vorgänger.
- Tests `test_wochenplan`.

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
