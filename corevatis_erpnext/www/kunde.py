"""Kundenseite ``/kunde/<token>`` (P2.7, Handwerker#91) - ohne Anmeldung, der Link ist der Zugang.

``hooks.website_route_rules`` legt das Token in ``frappe.form_dict.token``. Unbekannte, widerrufene und
abgelaufene Tokens (und zu viele Versuche) ergeben dieselbe 404-Seite (``zugang.anfrage_pruefen``).
Alles Fachliche steht in ``corevatis_erpnext.kundenportal``; die Vorlage ``kunde.html`` zeigt es.
"""

import frappe
from frappe import _

from corevatis_erpnext.kundenportal import vorgang as V
from corevatis_erpnext.kundenportal import zugang as Z

no_cache = 1
sitemap = 0


def get_context(context):
	token = frappe.form_dict.get("token")
	z = Z.anfrage_pruefen(token)
	if not z:
		raise frappe.PageDoesNotExistError
	Z.nutzung(z)
	# Seitenaufruf ist GET: den Zähler ausdrücklich festschreiben
	frappe.db.commit()
	daten = V.daten(z)
	context.no_cache = 1
	context.show_sidebar = False
	context.no_breadcrumbs = True
	context.token = token
	context.daten = daten
	context.title = _("Ihr Vorgang bei {0}").format(daten["firma"].get("anzeige") or _("uns"))
	context.datenschutz_url = "/datenschutz"
	context.api = "/api/method/corevatis_erpnext.kundenportal.aktionen."
	return context
