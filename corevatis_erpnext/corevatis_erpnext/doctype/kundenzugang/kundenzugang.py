"""Kundenzugang (P2.7): Zugang zum Kundenportal per Link, siehe ``corevatis_erpnext.kundenportal.zugang``.

Angelegt wird er normalerweise über den Dialog „Kundenzugang“ an Anfrage, Kunde, Projekt oder Angebot
(``kundenportal.zugang_anlegen``), der den Klartext-Link einmal zeigt. Legt jemand den Datensatz direkt
im Formular (oder per REST) an, erzeugt ``validate`` das Token und ``after_insert`` zeigt den Link einmalig
als Meldung.

Security-Review: Anker, Kundenname und Token-Hash bestimmt **immer** der Server - mitgeschickte Werte werden
verworfen (sonst könnte ein Benutzer mit Schreibrecht per REST einen fremden Vorgang als Anker oder einen
selbst gewählten Hash setzen). Der anlegende Benutzer braucht Leserecht am Vorgang - ohne Ausnahme, auch
nicht über Dokument-Flags. Das Token erzeugt ``validate`` selbst (``zugang.token_neu``); der Klartext liegt
nur im nicht gespeicherten Attribut ``_token_klar`` des Objekts, das ``zugang.erzeugen`` nach ``insert()``
einmalig ausliest. Nach dem Anlegen sind Vorgang, Anker und Hash unveränderlich, die Zähler starten bei 0.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, getdate, now_datetime, today

from corevatis_erpnext.kundenportal import zugang

UNVERAENDERLICH = ("vorgang_doctype", "vorgang", "anker_doctype", "anker", "token_hash")


class Kundenzugang(Document):
	def validate(self):
		if self.vorgang_doctype not in zugang.VORGANG_DOCTYPES:
			frappe.throw(_("Für {0} gibt es keinen Kundenzugang.").format(_(self.vorgang_doctype or "")))
		if not self.vorgang or not frappe.db.exists(self.vorgang_doctype, self.vorgang):
			frappe.throw(
				_("{0} {1} nicht gefunden.").format(_(self.vorgang_doctype), self.vorgang),
				frappe.DoesNotExistError,
			)
		if self.is_new():
			self._neu()
		else:
			self._unveraenderlich()
		if self.anker_doctype not in zugang.ANKER_DOCTYPES:
			frappe.throw(
				_("Zu {0} lässt sich kein Vorgang ermitteln.").format(self.vorgang), zugang.ZugangFehler
			)
		if not self.gueltig_bis:
			self.gueltig_bis = add_days(today(), zugang.GUELTIG_TAGE)
		if getdate(self.gueltig_bis) > getdate(add_days(today(), zugang.GUELTIG_TAGE_MAX)):
			frappe.throw(
				_("Gültigkeit zwischen 1 und {0} Tagen wählen.").format(zugang.GUELTIG_TAGE_MAX),
				zugang.ZugangFehler,
			)
		if cint(self.widerrufen) and not self.widerrufen_am:
			self.widerrufen_am = now_datetime()
		if not cint(self.widerrufen):
			self.widerrufen_am = None

	def _neu(self) -> None:
		"""Anlegen: Leserecht am Vorgang, Anker und Kundenname vom Server, Token nur serverseitig, Zähler 0."""
		frappe.has_permission(self.vorgang_doctype, "read", self.vorgang, throw=True)
		self.anker_doctype, self.anker = zugang.anker_fuer(self.vorgang_doctype, self.vorgang)
		self.kunde_name = zugang.kunden_name(self.anker_doctype, self.anker)
		# Klartext nie von außen (weder Feld noch Flag noch Attribut): immer frisch erzeugen, nur Hash speichern
		self._token_klar = zugang.token_neu()
		self.token_hash = zugang.token_hash(self._token_klar)
		self.aufrufe = 0
		self.uploads = 0
		self.letzte_nutzung = None

	def _unveraenderlich(self) -> None:
		alt = self.get_doc_before_save()
		if not alt:
			return
		for feld in UNVERAENDERLICH:
			if (self.get(feld) or None) != (alt.get(feld) or None):
				frappe.throw(
					_("{0} lässt sich nach dem Anlegen nicht ändern.").format(_(self.meta.get_label(feld))),
					frappe.ValidationError,
				)

	def after_insert(self):
		token = getattr(self, "_token_klar", None)
		if token and not self.flags.get("cv_stumm"):
			link = zugang.url(token)
			frappe.msgprint(
				_(
					"Link zum Kundenportal (wird nur jetzt angezeigt, bitte kopieren und wie ein Passwort behandeln):<br><code>{0}</code>"
				).format(frappe.utils.escape_html(link)),
				title=_("Kundenzugang angelegt"),
				indicator="green",
			)
