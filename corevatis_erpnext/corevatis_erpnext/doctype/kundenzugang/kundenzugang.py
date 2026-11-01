"""Kundenzugang (P2.7): Zugang zum Kundenportal per Link, siehe ``corevatis_erpnext.kundenportal.zugang``.

Angelegt wird er normalerweise über den Dialog „Kundenzugang“ an Anfrage, Kunde, Projekt oder Angebot
(``kundenportal.zugang_anlegen``), der den Klartext-Link einmal zeigt. Legt jemand den Datensatz direkt
im Formular an, erzeugt ``validate`` das Token und ``after_insert`` zeigt den Link einmalig als Meldung.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, now_datetime, today

from corevatis_erpnext.kundenportal import zugang


class Kundenzugang(Document):
	def validate(self):
		if self.vorgang_doctype not in zugang.VORGANG_DOCTYPES:
			frappe.throw(_("Für {0} gibt es keinen Kundenzugang.").format(_(self.vorgang_doctype or "")))
		if not frappe.db.exists(self.vorgang_doctype, self.vorgang):
			frappe.throw(
				_("{0} {1} nicht gefunden.").format(_(self.vorgang_doctype), self.vorgang),
				frappe.DoesNotExistError,
			)
		if not self.anker_doctype or not self.anker:
			self.anker_doctype, self.anker = zugang.anker_fuer(self.vorgang_doctype, self.vorgang)
		if not self.kunde_name:
			self.kunde_name = zugang.kunden_name(self.anker_doctype, self.anker)
		if not self.gueltig_bis:
			self.gueltig_bis = add_days(today(), zugang.GUELTIG_TAGE)
		if self.is_new() and not self.token_hash:
			token = zugang.token_neu()
			self.token_hash = zugang.token_hash(token)
			self.flags.cv_token = token
		if cint(self.widerrufen) and not self.widerrufen_am:
			self.widerrufen_am = now_datetime()
		if not cint(self.widerrufen):
			self.widerrufen_am = None

	def after_insert(self):
		token = self.flags.pop("cv_token", None)
		if token and not self.flags.get("cv_stumm"):
			link = zugang.url(token)
			frappe.msgprint(
				_(
					"Link zum Kundenportal (wird nur jetzt angezeigt, bitte kopieren und wie ein Passwort behandeln):<br><code>{0}</code>"
				).format(frappe.utils.escape_html(link)),
				title=_("Kundenzugang angelegt"),
				indicator="green",
			)
