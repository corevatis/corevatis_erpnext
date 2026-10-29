"""Nachprüfung mvp-test (Patch angebot_steuern_druck): Standard-Preisliste auch als Site-Default.

``angebot_steuern_druck`` setzte ``Selling/Buying Settings`` per ``set_single_value``; der
Site-Default ``selling_price_list``/``buying_price_list``, den das Desk-Formular liest, blieb
auf der ERPNext-Vorgabe. Neue Angebote zogen weiter „Standard Selling“ (0,00 €).
"""

from corevatis_erpnext.katalog.importer import standard_preislisten


def execute():
	for message in standard_preislisten():
		print(message)
