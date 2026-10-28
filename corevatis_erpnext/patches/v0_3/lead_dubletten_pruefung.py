"""Review Epic 1, E-2: „Lead-Dubletten per E-Mail erlauben“ wieder ausschalten.

Patch ``v0_2.webform_einschaetzung`` hatte die ERPNext-Einstellung site-weit eingeschaltet, damit
das Web Form erneute Anfragen derselben Adresse annimmt. Das Formular hängt solche Anfragen jetzt
an den offenen Lead (``webform.einschaetzung.anfrage_anhaengen``); die Dublettenprüfung für Desk,
Import und E-Mail gilt wieder (ERPNext-Standard: aus). Einmalig - wer die Einstellung danach
bewusst einschaltet, behält sie.
"""

import frappe


def execute():
	frappe.db.set_single_value("CRM Settings", "allow_lead_duplication_based_on_emails", 0)
