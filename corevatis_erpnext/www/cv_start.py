"""Startkachel „Vorgänge“ (/cv_start): leitet je Rolle auf den passenden Arbeitsbereich weiter.

Frappe v16 zeigt je App nur den ersten Eintrag aus ``add_to_apps_screen`` (``frappe.boot``,
``apps[0]``); eine zweite Kachel „Meine Baustellen“ für Monteure erschien deshalb nie (Abnahme
mvp-test). Die eine Kachel führt hierher: Büro, Meister und System Manager auf „Vorgänge“,
Monteure auf „Meine Baustellen“, alle anderen auf den Desk.
"""

import frappe

from corevatis_erpnext.prozess.setup import ziel_startseite

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/cv_start"
	else:
		frappe.local.flags.redirect_location = ziel_startseite()
	raise frappe.Redirect
