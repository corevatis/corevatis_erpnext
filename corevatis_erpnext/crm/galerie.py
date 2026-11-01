"""Aufgabe 1.5: Bildliste für die Foto-Galerie (``public/js/galerie.js``).

``get_bilder(doctype, name)`` liefert alle Bilder eines Datensatzes in Upload-Reihenfolge:

- Werte aller ``Attach``-/``Attach Image``-Felder des Datensatzes und seiner
  Kindtabellen (z. B. ``cv_foto_1..3`` oder eine Kindtabelle „Lead Foto“ aus 1.1,
  das Standardfeld ``image``),
- alle ``File``-Anhänge des Datensatzes (``attached_to_doctype``/``attached_to_name``).

Metadaten (Dateiname, Datum, privat) kommen nur aus den Anhängen dieses Datensatzes; ein
Feldwert ohne passenden Anhang erscheint mit Namen aus der URL und dem Datum des
Datensatzes. Nur Datei-URLs der Site werden geliefert (``/files/``, ``/private/files/``,
``/api/method/frappe_s3_attachment.``), externe URLs nicht.

Fotozeilen mit ``markiert`` (``Befund Foto`` aus corevatis, Markierung im Foto 3.4/P3.1) liefern
``markiert: true``; die Galerie zeigt daran ein Kennzeichen „markiert“ (das Bild selbst enthält
die Markierung schon).

Doppelte URLs (Feldwert und zugehöriger Anhang) erscheinen einmal. Die URLs werden
unverändert durchgereicht: ``/files/...`` (öffentlich), ``/private/files/...`` und
``/api/method/frappe_s3_attachment.controller.generate_file?...`` (S3) laden über die
Site mit dem Sitzungs-Cookie; die Rechte prüft Frappe bzw. ``frappe_s3_attachment``
beim Abruf. Die Methode selbst verlangt Leserecht auf den Datensatz.
"""

import os
from urllib.parse import parse_qs, unquote, urlparse

import frappe
from frappe import _

# Nur Rastertypen ohne aktiven Inhalt: kein SVG (würde über „Original öffnen“ inline auf der
# Site-Origin geladen - Stored XSS, Review Epic 1 E-11). Gleiche Liste in public/js/galerie.js.
BILD_ENDUNGEN = {"jpg", "jpeg", "png", "gif", "webp", "heic"}
ANHANG_FELDTYPEN = ("Attach", "Attach Image")
# Allowlist: Galerie nur an diesen Doctypes (Objekt und Befund aus corevatis, 2.1; Befund mit
# den Fotos der App ab 3.7)
GALERIE_DOCTYPES = ("Lead", "Objekt", "Befund")
ERLAUBTE_PRAEFIXE = ("/files/", "/private/files/", "/api/method/frappe_s3_attachment.")
# Check-Feld der Fotozeile: Bild enthält die Markierung aus der App (Befund Foto, P3.1)
MARKIERT_FELD = "markiert"


@frappe.whitelist()
def get_bilder(doctype: str, name: str) -> list[dict]:
	if doctype not in GALERIE_DOCTYPES:
		frappe.throw(
			_("Die Foto-Galerie ist für {0} nicht freigegeben.").format(doctype), frappe.PermissionError
		)
	if not name or not frappe.db.exists("DocType", doctype):
		frappe.throw(_("Unbekannter Datensatz."), frappe.DoesNotExistError)
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} nicht gefunden.").format(_(doctype), name), frappe.DoesNotExistError)
	frappe.has_permission(doctype, "read", doc=name, throw=True)

	doc = frappe.get_doc(doctype, name)
	# Feldrechte (permlevel) wie bei der REST-API: nicht lesbare Felder leeren
	doc.apply_fieldlevel_read_permissions()
	dateien = frappe.get_all(
		"File",
		filters={"attached_to_doctype": doctype, "attached_to_name": name, "is_folder": 0},
		fields=["name", "file_name", "file_url", "thumbnail_url", "is_private", "creation", "file_type"],
		order_by="creation asc, name asc",
	)
	nach_url = {d.file_url: d for d in dateien if d.file_url}

	bilder: dict[str, dict] = {}
	for d in dateien:
		_eintrag(bilder, d.file_url, d, quelle=_("Anhang"))

	for url, quelle, markiert in _feldwerte(doc):
		# Metadaten nur aus Anhängen *dieses* Datensatzes, keine Suche in der ganzen File-Tabelle
		datei = nach_url.get(url)
		_eintrag(bilder, url, datei, quelle=quelle, fallback_creation=doc.creation, markiert=markiert)

	return sorted(bilder.values(), key=lambda b: (str(b["creation"] or ""), b["file_name"] or ""))


def _feldwerte(doc) -> list[tuple[str, str, bool]]:
	"""Anhangsfelder des Datensatzes und seiner Kindtabellen, nur mit Leserecht auf das
	Feld (permlevel) - zusätzlich zu ``apply_fieldlevel_read_permissions``. Dritter Wert:
	Zeile mit gesetztem ``markiert`` (Markierung im Foto, P3.1)."""
	# Kindtabellen haben keine eigenen Rechte: es gelten die permlevel des Elterndokuments
	zugriff = set(doc.meta.get_permlevel_access("read"))
	werte = []
	for df in doc.meta.fields:
		if (df.permlevel or 0) not in zugriff:
			continue
		if df.fieldtype in ANHANG_FELDTYPEN and doc.get(df.fieldname):
			werte.append((doc.get(df.fieldname), _(df.label or df.fieldname), False))
		elif df.fieldtype in ("Table", "Table MultiSelect") and df.options:
			kind = frappe.get_meta(df.options)
			felder = [
				f for f in kind.fields if f.fieldtype in ANHANG_FELDTYPEN and (f.permlevel or 0) in zugriff
			]
			markiert_feld = any(
				f.fieldname == MARKIERT_FELD and f.fieldtype == "Check" and (f.permlevel or 0) in zugriff
				for f in kind.fields
			)
			for row in doc.get(df.fieldname) or []:
				markiert = bool(markiert_feld and row.get(MARKIERT_FELD))
				for f in felder:
					if row.get(f.fieldname):
						label = f"{_(df.label or df.fieldname)} {row.idx}"
						werte.append((row.get(f.fieldname), label, markiert))
	return werte


def _eintrag(
	bilder: dict, url: str | None, datei, quelle: str, fallback_creation=None, markiert: bool = False
) -> None:
	if not _url_erlaubt(url):
		return
	# Bild oder nicht entscheidet die Ziel-URL (was der Browser bei „Original öffnen“ lädt),
	# nicht der angezeigte Dateiname - zusätzlich der Typ der File-Zeile (E-11)
	if not ist_bild(url, datei and datei.file_type):
		return
	file_name = (datei and datei.file_name) or _dateiname_aus_url(url)
	if url in bilder:
		# Feldwert zu einem schon gelisteten Anhang: Feldbezeichnung ist aussagekräftiger
		if quelle != _("Anhang"):
			bilder[url]["quelle"] = quelle
		bilder[url]["markiert"] = bilder[url]["markiert"] or markiert
		return
	thumbnail = datei and datei.thumbnail_url
	bilder[url] = {
		"name": datei and datei.name,
		"file_url": url,
		"thumbnail_url": thumbnail if _url_erlaubt(thumbnail) else url,
		"file_name": file_name,
		"creation": (datei and datei.creation) or fallback_creation,
		"is_private": bool(datei and datei.is_private) if datei else url.startswith("/private/"),
		"quelle": quelle,
		"markiert": markiert,
	}


def _url_erlaubt(url: str | None) -> bool:
	"""Nur Datei-URLs der Site: ``/files/``, ``/private/files/`` und der Download von
	``frappe_s3_attachment``. Keine externen URLs (kein Einbinden fremder Hosts), kein
	``javascript:``/``data:``, keine Backslashes, Steuerzeichen oder ``..``-Segmente."""
	if not url or not isinstance(url, str) or len(url) > 2000:
		return False
	if "\\" in url or any(ord(c) < 32 or ord(c) == 127 for c in url):
		return False
	pfad = unquote(urlparse(url).path)
	if "/../" in pfad + "/" or "/./" in pfad + "/":
		return False
	return url.startswith(ERLAUBTE_PRAEFIXE)


def _dateiname_aus_url(url: str) -> str:
	teile = urlparse(url)
	name = (parse_qs(teile.query).get("file_name") or [""])[0]
	return name or os.path.basename(teile.path)


S3_DOWNLOAD = "/api/method/frappe_s3_attachment."


def endung_ziel(url: str | None) -> str:
	"""Endung der Datei, die unter ``url`` ausgeliefert wird: Pfad ohne Query/Fragment; beim
	S3-Download (``generate_file``) der Parameter ``file_name``, nach dem der Fork Typ und
	inline/attachment wählt."""
	if not url:
		return ""
	teile = urlparse(url)
	pfad = unquote(teile.path)
	if pfad.startswith(S3_DOWNLOAD):
		name = (parse_qs(teile.query).get("file_name") or [""])[0]
	else:
		name = os.path.basename(pfad)
	return name.rsplit(".", 1)[1].lower() if "." in name else ""


def ist_bild(url: str | None, file_type: str | None = None) -> bool:
	"""Bild nur, wenn die Ziel-URL eine erlaubte Endung hat und - falls bekannt - auch der Typ
	der File-Zeile; der angezeigte Dateiname zählt nicht (``foto.jpg`` → ``x.svg`` bleibt draußen)."""
	if endung_ziel(url) not in BILD_ENDUNGEN:
		return False
	return not file_type or file_type.lower() in BILD_ENDUNGEN
