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

BILD_ENDUNGEN = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "heic", "heif", "svg"}
ANHANG_FELDTYPEN = ("Attach", "Attach Image")
ERLAUBTE_PRAEFIXE = ("/files/", "/private/files/", "/api/method/frappe_s3_attachment.")


@frappe.whitelist()
def get_bilder(doctype: str, name: str) -> list[dict]:
	if not doctype or not name or not frappe.db.exists("DocType", doctype):
		frappe.throw(_("Unbekannter Datensatz."), frappe.DoesNotExistError)
	meta = frappe.get_meta(doctype)
	if meta.istable or meta.issingle:
		frappe.throw(_("Galerie nur für eigenständige Datensätze."))
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} {1} nicht gefunden.").format(_(doctype), name), frappe.DoesNotExistError)
	frappe.has_permission(doctype, "read", doc=name, throw=True)

	doc = frappe.get_doc(doctype, name)
	dateien = frappe.get_all(
		"File",
		filters={"attached_to_doctype": doctype, "attached_to_name": name, "is_folder": 0},
		fields=["name", "file_name", "file_url", "thumbnail_url", "is_private", "creation"],
		order_by="creation asc, name asc",
	)
	nach_url = {d.file_url: d for d in dateien if d.file_url}

	bilder: dict[str, dict] = {}
	for d in dateien:
		_eintrag(bilder, d.file_url, d, quelle=_("Anhang"))

	for url, quelle in _feldwerte(doc):
		# Metadaten nur aus Anhängen *dieses* Datensatzes, keine Suche in der ganzen File-Tabelle
		datei = nach_url.get(url)
		_eintrag(bilder, url, datei, quelle=quelle, fallback_creation=doc.creation)

	return sorted(bilder.values(), key=lambda b: (str(b["creation"] or ""), b["file_name"] or ""))


def _feldwerte(doc) -> list[tuple[str, str]]:
	werte = []
	for df in doc.meta.fields:
		if df.fieldtype in ANHANG_FELDTYPEN and doc.get(df.fieldname):
			werte.append((doc.get(df.fieldname), _(df.label or df.fieldname)))
		elif df.fieldtype in ("Table", "Table MultiSelect") and df.options:
			kind = frappe.get_meta(df.options)
			felder = [f for f in kind.fields if f.fieldtype in ANHANG_FELDTYPEN]
			for row in doc.get(df.fieldname) or []:
				for f in felder:
					if row.get(f.fieldname):
						label = f"{_(df.label or df.fieldname)} {row.idx}"
						werte.append((row.get(f.fieldname), label))
	return werte


def _eintrag(bilder: dict, url: str | None, datei, quelle: str, fallback_creation=None) -> None:
	if not _url_erlaubt(url):
		return
	file_name = (datei and datei.file_name) or _dateiname_aus_url(url)
	if not ist_bild(file_name, url):
		return
	if url in bilder:
		# Feldwert zu einem schon gelisteten Anhang: Feldbezeichnung ist aussagekräftiger
		if quelle != _("Anhang"):
			bilder[url]["quelle"] = quelle
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


def ist_bild(file_name: str | None, url: str | None = None) -> bool:
	for kandidat in (file_name, url and _dateiname_aus_url(url)):
		if kandidat and "." in kandidat:
			if kandidat.rsplit(".", 1)[1].lower() in BILD_ENDUNGEN:
				return True
	return False
