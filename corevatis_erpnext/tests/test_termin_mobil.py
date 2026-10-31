"""Durchstich A10: Termin aus der Anfrage in der App - Sync-Handler ``termin_anlegen``
(``crm.termin_mobil``): Rechte (Büro/Meister, Monteur nicht, Schreibrecht), Payload-Prüfung,
Termin wie im Desk ohne Kundenmail, Workflow „Termin vereinbaren“, Umplanung, Antwort mit
Terminzeile/Anfrage/Bestätigung, Überschneidung als ``CONFLICT`` mit ``trotzdem``.

Die Idempotenz über das Sync-Journal liegt im Kern (``corevatis.api.sync_schema``, dort getestet);
hier wird geprüft, dass ein Aufruf genau einen Termin anlegt.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from corevatis_erpnext.crm import termin, termin_mobil
from corevatis_erpnext.tests._helpers import make_lead, make_user

BUERO = "cv-mobil-buero@example.com"
MEISTER = "cv-mobil-meister@example.com"
MEISTER2 = "cv-mobil-meister2@example.com"
MONTEUR = "cv-mobil-monteur@example.com"


def mutation(payload: dict, op: str = "create", **extra) -> dict:
	m = {
		"idempotency_key": str(uuid.uuid4()),
		"client_id": f"termin-neu-{uuid.uuid4().hex[:8]}",
		"entity": termin_mobil.ENTITAET,
		"op": op,
		"payload": payload,
	}
	m.update(extra)
	return m


def in_tagen(tage: int, stunde: int = 9) -> str:
	return (
		(now_datetime() + timedelta(days=tage))
		.replace(hour=stunde, minute=30, second=0)
		.strftime("%Y-%m-%d %H:%M:%S")
	)


class TestTerminMobil(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(BUERO, "Bea", ["Büro", "Sales User"], "Büro")
		make_user(MEISTER, "Max", ["Meister", "Sales User"], "Meister")
		make_user(MEISTER2, "Moritz", ["Meister", "Sales User"], "Zweitmeister")
		make_user(MONTEUR, "Manfred", ["Monteur"], "Monteur")

	def setUp(self):
		frappe.set_user("Administrator")
		# Doppelbuchungsschutz (D14) hat eigene Tests (test_terminplanung, hier test_ueberschneidung…);
		# die übrigen Tests buchen denselben Meister absichtlich mehrfach zur gleichen Zeit
		self._konflikte = patch("corevatis_erpnext.planung.terminplanung.konflikte", return_value=[])
		self._konflikte.start()
		self._konflikte_aktiv = True
		self.addCleanup(self._konflikte_stoppen)
		self.lead = make_lead(mobile_no="0251 123456", cv_objekt_adresse="Kellerweg 7\n48143 Münster")

	def _konflikte_stoppen(self):
		if self._konflikte_aktiv:
			self._konflikte.stop()
			self._konflikte_aktiv = False

	def tearDown(self):
		frappe.set_user("Administrator")

	def _anlegen(self, user=BUERO, op="create", **payload):
		frappe.set_user(user)
		daten = {"lead": self.lead.name, "beginn": in_tagen(3), "dauer": 90, "meister": MEISTER}
		daten.update(payload)
		for k in [k for k, v in daten.items() if v is ...]:
			del daten[k]
		return termin_mobil.termin_anlegen(mutation(daten, op=op), "test-device")

	def _events(self, lead=None) -> list[str]:
		return frappe.get_all(
			"Event Participants",
			filters={"reference_doctype": "Lead", "reference_docname": lead or self.lead.name},
			pluck="parent",
		)

	def test_hook_registriert(self):
		handler = frappe.get_hooks("corevatis_sync_handlers").get("termin_anlegen")
		self.assertEqual(handler, ["corevatis_erpnext.crm.termin_mobil.termin_anlegen"])

	def test_buero_legt_termin_fuer_meister_an(self):
		mails = frappe.db.count("Email Queue", {"reference_name": self.lead.name})
		result = self._anlegen(notiz="Zugang über die Kellertür hinten")
		self.assertEqual(result["status"], "applied", result)
		event = frappe.get_doc("Event", result["server_id"])
		self.assertEqual(result["_ref"], ("Event", event.name))
		self.assertEqual(result["server_version"], event.modified.strftime(termin_mobil.VERSION_FORMAT))
		self.assertEqual(str(event.starts_on), in_tagen(3))
		self.assertEqual(event.ends_on - event.starts_on, timedelta(minutes=90))
		self.assertEqual((event.event_type, event.status), ("Private", "Open"))
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_termin"), event.name)
		# Zuweisung an den Meister mit der internen Notiz, keine Kundenmail
		todos = frappe.get_all(
			"ToDo",
			filters={"reference_type": "Event", "reference_name": event.name, "status": "Open"},
			fields=["allocated_to", "description"],
		)
		self.assertEqual([t.allocated_to for t in todos], [MEISTER])
		self.assertIn("Kellertür", todos[0].description)
		self.assertEqual(frappe.db.count("Email Queue", {"reference_name": self.lead.name}), mails)
		# Antwort: Terminzeile mit Meister, Anfrage, Bestätigung zum Teilen
		data = result["data"]
		self.assertEqual(data["termin"], event.name)
		self.assertEqual(
			(data["beginn"], data["status"], data["lead"]), (in_tagen(3), "Open", self.lead.name)
		)
		self.assertEqual(data["ende"], str(event.ends_on))
		self.assertEqual(data["lead_name"], "Erika Musterfrau")
		self.assertEqual(data["adresse"], "Kellerweg 7\n48143 Münster")
		self.assertEqual(
			(data["meister"], data["meister_name"]), (MEISTER, frappe.utils.get_fullname(MEISTER))
		)
		self.assertFalse(data["eigener"])  # Büro hat den Termin nicht selbst
		self.assertIsNone(data["abgesagt"])
		self.assertTrue(data["bestaetigung"]["betreff"].startswith("Ihr Besichtigungstermin am "))
		self.assertTrue(data["bestaetigung"]["text"].startswith("Guten Tag Erika Musterfrau,"))
		self.assertNotIn("Kellertür", data["bestaetigung"]["text"])
		self.assertEqual(data["anfrage"]["name"], self.lead.name)
		self.assertEqual(data["anfrage"]["cv_termin"], event.name)
		self.assertEqual(data["anfrage"]["termin"], in_tagen(3))
		details = data["anfrage"]["termin_details"]
		self.assertEqual((details["name"], details["meister"]), (event.name, MEISTER))
		self.assertEqual(details["meister_name"], frappe.utils.get_fullname(MEISTER))
		self.assertTrue(data["anfrage"]["version"])
		if "corevatis" in frappe.get_installed_apps():
			self.assertEqual((data["phase"], data["laufend"]), ("anstehend", True))
		else:
			self.assertEqual(
				(data["phase"], data["laufend"], data["aufmass"], data["angebot"]), (None, True, None, None)
			)

	def test_workflow_neu_nach_termin(self):
		from frappe.model.workflow import get_workflow_name

		if not get_workflow_name("Lead"):
			self.skipTest("kein aktiver Lead-Workflow")
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_status"), "Neu")
		result = self._anlegen(user=MEISTER)
		self.assertEqual(result["status"], "applied", result)
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_status"), "Termin")
		self.assertEqual(result["data"]["lead_status"], "Termin")
		self.assertEqual(result["data"]["anfrage"]["cv_status"], "Termin")
		self.assertEqual(result["data"]["hinweise"], [])
		self.assertTrue(result["data"]["eigener"])  # Meister legt seinen eigenen Termin an

	def test_umplanung_sagt_vortermin_ab_status_bleibt(self):
		erster = self._anlegen(user=MEISTER)
		status = frappe.db.get_value("Lead", self.lead.name, "cv_status")
		zweiter = self._anlegen(user=BUERO, beginn=in_tagen(5, 14), meister=MEISTER2)
		self.assertEqual(zweiter["status"], "applied", zweiter)
		self.assertEqual(zweiter["data"]["abgesagt"], erster["server_id"])
		self.assertEqual(frappe.db.get_value("Event", erster["server_id"], "status"), "Cancelled")
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_termin"), zweiter["server_id"])
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_status"), status)
		self.assertEqual(zweiter["data"]["meister"], MEISTER2)
		self.assertEqual(zweiter["data"]["anfrage"]["termin_details"]["meister"], MEISTER2)
		self.assertEqual(len(self._events()), 2)

	def test_monteur_darf_keinen_termin_anlegen(self):
		result = self._anlegen(user=MONTEUR)
		self.assertEqual(result["status"], "rejected")
		self.assertEqual(result["error"]["code"], "PERMISSION_DENIED")
		self.assertEqual(result["error"]["details"], {"grund": "keine_rolle"})
		self.assertEqual(self._events(), [])
		self.assertIsNone(frappe.db.get_value("Lead", self.lead.name, "cv_termin"))

	def test_ohne_schreibrecht_an_der_anfrage(self):
		with patch.object(termin_mobil.frappe, "has_permission", return_value=False):
			result = self._anlegen(user=MEISTER)
		self.assertEqual((result["status"], result["error"]["code"]), ("rejected", "PERMISSION_DENIED"))
		self.assertEqual(self._events(), [])

	def test_payload_pruefung(self):
		faelle = (
			({"op": "update"}, "VALIDATION_ERROR", None),
			({"lead": ...}, "VALIDATION_ERROR", None),
			({"lead": "CRM-LEAD-9999-99999"}, "NOT_FOUND", None),
			({"beginn": "morgen früh"}, "VALIDATION_ERROR", "beginn_ungueltig"),
			({"beginn": ...}, "VALIDATION_ERROR", "beginn_ungueltig"),
			({"beginn": in_tagen(-3)}, "VALIDATION_ERROR", "zeitpunkt_vergangen"),
			({"beginn": in_tagen(900)}, "VALIDATION_ERROR", "zeitpunkt_zu_weit"),
			({"dauer": 5}, "VALIDATION_ERROR", "dauer_ungueltig"),
			({"dauer": 900}, "VALIDATION_ERROR", "dauer_ungueltig"),
			({"dauer": "eine Stunde"}, "VALIDATION_ERROR", "dauer_ungueltig"),
			({"meister": BUERO}, "VALIDATION_ERROR", "meister_ungueltig"),
			({"meister": MONTEUR}, "VALIDATION_ERROR", "meister_ungueltig"),
			({"meister": ...}, "VALIDATION_ERROR", "meister_ungueltig"),
			({"notiz": "x" * (termin_mobil.NOTIZ_MAX + 1)}, "VALIDATION_ERROR", "notiz_zu_lang"),
		)
		for aenderung, code, grund in faelle:
			with self.subTest(aenderung=aenderung):
				op = aenderung.pop("op", "create")
				result = self._anlegen(user=BUERO, op=op, **aenderung)
				self.assertEqual(result["status"], "rejected", result)
				self.assertEqual(result["error"]["code"], code, result)
				self.assertEqual((result["error"].get("details") or {}).get("grund"), grund, result)
		self.assertEqual(self._events(), [])
		# Grenzen: gestern (innerhalb eines Tages) und ohne Dauer (60 min) sind gültig
		result = self._anlegen(
			user=BUERO, beginn=(now_datetime() - timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S"), dauer=...
		)
		self.assertEqual(result["status"], "applied", result)
		event = frappe.get_doc("Event", result["server_id"])
		self.assertEqual(event.ends_on - event.starts_on, timedelta(minutes=60))

	def test_mutation_legt_genau_einen_termin_an(self):
		result = self._anlegen(user=MEISTER)
		self.assertEqual(result["status"], "applied", result)
		self.assertEqual(self._events(), [result["server_id"]])

	def test_ueberschneidung_als_conflict_und_trotzdem(self):
		"""Doppelbuchungsschutz (D14): derselbe Meister zur selben Zeit bei einer anderen Anfrage →
		``rejected``/``CONFLICT`` mit Klartext und ``konflikte``; mit ``trotzdem`` wird angelegt (Vermerk)."""
		self._konflikte_stoppen()
		andere = make_lead(first_name="Nachbar", cv_objekt_adresse="Kellerweg 9\n48143 Münster")
		frappe.set_user(BUERO)
		# eigene Uhrzeit: andere Tests buchen den Meister (ohne Konfliktprüfung) um 09:30
		erster = termin.termin_anlegen(andere.name, in_tagen(6, 7), MEISTER, 90, bestaetigung=0)
		abgelehnt = self._anlegen(user=BUERO, beginn=in_tagen(6, 7))
		self.assertEqual(abgelehnt["status"], "rejected", abgelehnt)
		self.assertEqual(abgelehnt["error"]["code"], "CONFLICT")
		details = abgelehnt["error"]["details"]
		self.assertEqual(details["grund"], "ueberschneidung")
		self.assertEqual(details["meister"], MEISTER)
		[k] = details["konflikte"]
		self.assertEqual((k["meister"], k["doctype"], k["name"]), (MEISTER, "Event", erster["event"]))
		self.assertEqual(k["von"], in_tagen(6, 7))
		self.assertTrue(abgelehnt["error"]["message"].startswith("Terminüberschneidung: "))
		self.assertIn(k["text"], abgelehnt["error"]["message"])
		self.assertNotIn("<", abgelehnt["error"]["message"])
		self.assertNotIn("konflikte", frappe.local.response)
		self.assertEqual(self._events(), [])
		self.assertIsNone(frappe.db.get_value("Lead", self.lead.name, "cv_termin"))
		# Büro übersteuert: trotzdem anlegen, Vermerk am Termin
		angelegt = self._anlegen(user=BUERO, beginn=in_tagen(6, 7), trotzdem=True)
		self.assertEqual(angelegt["status"], "applied", angelegt)
		self.assertEqual(self._events(), [angelegt["server_id"]])
		vermerke = frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "Event",
				"reference_name": angelegt["server_id"],
				"comment_type": "Info",
			},
			pluck="content",
		)
		self.assertTrue(any("Trotz Überschneidung" in v for v in vermerke), vermerke)
		# derselbe Meister zu einer freien Zeit: kein Konflikt
		frei = self._anlegen(user=BUERO, beginn=in_tagen(6, 16))
		self.assertEqual(frei["status"], "applied", frei)
		self.assertEqual(frei["data"]["abgesagt"], angelegt["server_id"])

	def test_umplanung_eigener_termin_ist_kein_konflikt(self):
		self._konflikte_stoppen()
		erster = self._anlegen(user=MEISTER, beginn=in_tagen(7, 7))
		self.assertEqual(erster["status"], "applied", erster)
		# dieselbe Zeit, derselbe Meister, dieselbe Anfrage: der eigene Termin wird ersetzt
		zweiter = self._anlegen(user=MEISTER, beginn=in_tagen(7, 7), dauer=60)
		self.assertEqual(zweiter["status"], "applied", zweiter)
		self.assertEqual(zweiter["data"]["abgesagt"], erster["server_id"])

	def test_andere_validierungsfehler_gehen_an_den_kern(self):
		# fachliche Frappe-Fehler aus termin_anlegen setzt der Kern auf den Savepoint zurück (rejected)
		with patch.object(
			termin_mobil.termin, "termin_anlegen", side_effect=frappe.ValidationError("kaputt")
		):
			with self.assertRaises(frappe.ValidationError):
				self._anlegen(user=MEISTER)
