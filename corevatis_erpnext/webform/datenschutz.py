"""Platzhalterseite ``/datenschutz`` für das Web Form „Kostenlose Einschätzung“ (1.2).

Der Link der Datenschutz-Checkbox zeigt ohne ``cv_webform_datenschutz_url`` auf
``/datenschutz`` der Site. Im Pilotbetrieb gibt es noch keine Datenschutzerklärung
(Entscheidung des Auftraggebers); damit der Link nicht ins Leere (404) läuft, legt die
App dort eine deutlich gekennzeichnete Entwurfsseite an.

Idempotent und zurückhaltend:

- nur wenn der Link auf die Standardroute zeigt (keine eigene ``cv_webform_datenschutz_url``),
- nur wenn unter der Route noch keine Web Page existiert - eine vorhandene (auch im Desk
  bearbeitete oder durch die echte Erklärung ersetzte) Seite bleibt unangetastet.
"""

import frappe

ROUTE = "datenschutz"
TITEL = "Datenschutz (Entwurf)"
HINWEIS = "Entwurf – nicht rechtsverbindlich, im Pilotbetrieb ohne Datenschutzerklärung"  # noqa: RUF001 (Gedankenstrich gewollt)

INHALT = f"""<div class="cv-datenschutz-entwurf">
<p class="alert alert-warning"><strong>{HINWEIS}.</strong></p>
<p>Diese Seite ist ein Platzhalter. Die Datenschutzerklärung des Betriebs wird vor dem
Echtbetrieb ergänzt; bis dahin werden über das Formular „Kostenlose Einschätzung“
übermittelte Angaben ausschließlich zur Bearbeitung der Anfrage verwendet.</p>
</div>
"""


def ensure_datenschutz_seite() -> str | None:
	"""Legt die Entwurfsseite an, falls nötig. Gibt den Namen der neuen Web Page zurück, sonst None."""
	from corevatis_erpnext.webform.einschaetzung import datenschutz_url

	if datenschutz_url() != f"/{ROUTE}":
		return None
	if frappe.db.exists("Web Page", {"route": ROUTE}):
		return None
	doc = frappe.get_doc(
		{
			"doctype": "Web Page",
			"title": TITEL,
			"route": ROUTE,
			"published": 1,
			"show_title": 1,
			"content_type": "HTML",
			"main_section_html": INHALT,
			"meta_title": TITEL,
			"meta_description": HINWEIS,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name
