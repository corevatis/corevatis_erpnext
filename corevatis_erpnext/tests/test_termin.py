"""Aufgabe 1.3: Termin aus dem Lead, Terminbestätigung, Email Queue, Rechte."""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from corevatis_erpnext.crm import termin
from corevatis_erpnext.tests._helpers import make_lead, make_user

BUERO = "cv-test-buero@example.com"
MEISTER = "cv-test-meister@example.com"
OHNE_ROLLE = "cv-test-vertrieb@example.com"


class TestTermin(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(BUERO, "Bea", ["Büro", "Sales User"], "Büro")
		make_user(MEISTER, "Max", ["Meister", "Sales User"], "Meister")
		make_user(OHNE_ROLLE, "Vera", ["Sales User"], "Vertrieb")

	def setUp(self):
		frappe.set_user("Administrator")
		# CI-Bench ohne gebaute Assets (--skip-assets): E-Mail-CSS für frappe.sendmail fehlt
		css = patch("frappe.utils.jinja_globals.bundled_asset", return_value="/assets/fehlt/email.css")
		css.start()
		self.addCleanup(css.stop)
		self.lead = make_lead(mobile_no="0251 123456")
		self.adresse = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": "Objekt Musterfrau",
				"address_type": "Billing",
				"address_line1": "Kellerweg 7",
				"pincode": "48143",
				"city": "Münster",
				"country": "Germany",
				"links": [{"link_doctype": "Lead", "link_name": self.lead.name}],
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")

	def _anlegen(self, user=BUERO, **kwargs):
		frappe.set_user(user)
		args = {
			"lead": self.lead.name,
			"beginn": "2030-03-05 09:30:00",
			"dauer": 90,
			"meister": MEISTER,
			"notiz": "Zugang über die Kellertür hinten",
		}
		args.update(kwargs)
		return termin.termin_anlegen(**args)

	def test_fixture_email_template(self):
		template = frappe.get_doc("Email Template", termin.EMAIL_TEMPLATE)
		self.assertEqual(template.reference_doctype, "Lead")
		for platzhalter in ("kunde", "datum", "uhrzeit", "meister", "objekt_adresse", "firma"):
			self.assertIn("{{ %s }}" % platzhalter, template.subject + template.response)

	def test_custom_field_cv_termin(self):
		df = frappe.get_meta("Lead").get_field("cv_termin")
		self.assertIsNotNone(df)
		self.assertEqual((df.fieldtype, df.options), ("Link", "Event"))
		self.assertEqual(frappe.get_meta("Lead").get_field("cv_galerie").fieldtype, "HTML")

	def test_event_angelegt_und_verknuepft(self):
		result = self._anlegen()
		event = frappe.get_doc("Event", result["event"])
		self.assertEqual(event.starts_on, datetime(2030, 3, 5, 9, 30))
		self.assertEqual(event.ends_on, datetime(2030, 3, 5, 11, 0))
		self.assertEqual(event.event_type, "Private")
		self.assertEqual(event.status, "Open")
		self.assertEqual(
			[(p.reference_doctype, p.reference_docname) for p in event.event_participants],
			[("Lead", self.lead.name)],
		)
		# keine Kundendaten/Notizen im Event, nur Verweis auf den Lead und PLZ/Ort
		self.assertIn(f"/app/lead/{self.lead.name}", event.description)
		for geheim in ("Kellertür", "Kellerweg", "0251"):
			self.assertNotIn(geheim, event.description)
			self.assertNotIn(geheim, event.location or "")
		self.assertEqual(event.location, "48143 Münster")
		todos = frappe.get_all(
			"ToDo",
			filters={"reference_type": "Event", "reference_name": event.name, "status": "Open"},
			fields=["allocated_to", "description"],
		)
		self.assertEqual([t.allocated_to for t in todos], [MEISTER])
		self.assertIn("Kellertür", todos[0].description)
		# Private, freigegeben für Meister und Büro, nicht für andere Desk-Benutzer
		self.assertTrue(frappe.has_permission("Event", "read", doc=event.name, user=MEISTER))
		self.assertTrue(frappe.has_permission("Event", "write", doc=event.name, user=MEISTER))
		self.assertTrue(frappe.has_permission("Event", "read", doc=event.name, user=BUERO))
		self.assertFalse(frappe.has_permission("Event", "read", doc=event.name, user=OHNE_ROLLE))
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_termin"), event.name)

	def test_template_rendert(self):
		self._anlegen()
		inhalt = termin.bestaetigung_anzeigen(self.lead.name)
		self.assertEqual(inhalt["betreff"], "Ihr Besichtigungstermin am Dienstag, 05.03.2030 um 09:30 Uhr")
		text = inhalt["text"]
		self.assertTrue(text.startswith("Guten Tag Erika Musterfrau,"), text)
		self.assertIn("Dienstag, 05.03.2030, 09:30 Uhr (Dauer ca. 1 Stunde 30 Minuten)", text)
		self.assertIn("Kellerweg 7\n48143 Münster", text)
		self.assertIn("Max Meister", text)
		self.assertNotIn("Kellertür", text)  # interne Notiz nicht beim Kunden
		self.assertNotIn("<", text)
		firma = termin.firma(self.lead)
		if firma:
			self.assertIn(firma, text)
		self.assertIn("Kellerweg 7<br>48143 Münster", inhalt["html"])

	def test_werte_werden_escaped(self):
		# Lead-Namen mit < > lehnt ERPNext ab (Contact-Name); &, Anführungszeichen und Jinja nicht
		lead = make_lead(first_name='Kai & "Söhne"', last_name="{{ 7*7 }}")
		frappe.set_user(BUERO)
		result = termin.termin_anlegen(lead.name, "2030-03-06 08:00:00", MEISTER, bestaetigung=0)
		self.assertIsNone(result["email_queue"])
		self.assertIn("Kai &amp; &quot;Söhne&quot;", result["bestaetigung"]["html"])
		self.assertIn('Kai & "Söhne"', result["bestaetigung"]["text"])
		self.assertIn("{{ 7*7 }}", result["bestaetigung"]["text"])
		self.assertNotIn("49", result["bestaetigung"]["text"])

	def test_email_queue_eintrag(self):
		result = self._anlegen()
		self.assertTrue(result["email_queue"])
		queue = frappe.get_doc("Email Queue", result["email_queue"])
		self.assertEqual(queue.status, "Not Sent")
		self.assertEqual((queue.reference_doctype, queue.reference_name), ("Lead", self.lead.name))
		self.assertEqual([r.recipient for r in queue.recipients], [self.lead.email_id])
		# erneut in die Warteschlange (Button im Bestätigungsdialog)
		frappe.set_user(MEISTER)
		zweite = termin.bestaetigung_senden(self.lead.name)
		self.assertTrue(zweite)
		self.assertNotEqual(zweite, result["email_queue"])

	def test_ohne_email_keine_queue(self):
		lead = make_lead(email_id=None)
		frappe.set_user(BUERO)
		result = termin.termin_anlegen(lead.name, "2030-03-06 08:00:00", MEISTER)
		self.assertIsNone(result["email_queue"])
		self.assertTrue(result["warnungen"])
		self.assertTrue(result["bestaetigung"]["text"])

	def test_meister_darf_anlegen(self):
		result = self._anlegen(user=MEISTER)
		self.assertTrue(frappe.db.exists("Event", result["event"]))

	def test_fehlende_rolle(self):
		with self.assertRaises(frappe.PermissionError):
			self._anlegen(user=OHNE_ROLLE)
		frappe.set_user(OHNE_ROLLE)
		with self.assertRaises(frappe.PermissionError):
			termin.bestaetigung_anzeigen(self.lead.name)
		with self.assertRaises(frappe.PermissionError):
			termin.meister_liste()

	def test_meister_liste(self):
		frappe.set_user(BUERO)
		namen = [m["name"] for m in termin.meister_liste()]
		self.assertIn(MEISTER, namen)
		self.assertNotIn(BUERO, namen)

	def test_ungueltiger_meister_und_dauer(self):
		with self.assertRaises(frappe.ValidationError):
			self._anlegen(meister=BUERO)
		with self.assertRaises(frappe.ValidationError):
			self._anlegen(dauer=5)

	def test_neuer_termin_sagt_alten_ab(self):
		erster = self._anlegen()
		zweiter = self._anlegen(beginn="2030-03-12 14:00:00")
		self.assertEqual(zweiter["abgesagt"], erster["event"])
		self.assertEqual(frappe.db.get_value("Event", erster["event"], "status"), "Cancelled")
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_termin"), zweiter["event"])
		self.assertFalse(
			frappe.get_all(
				"ToDo",
				filters={"reference_type": "Event", "reference_name": erster["event"], "status": "Open"},
			)
		)

	def test_ohne_workflow_kein_statuswechsel(self):
		with patch("frappe.model.workflow.get_workflow_name", return_value=None):
			status_vorher = frappe.db.get_value("Lead", self.lead.name, "status")
			result = self._anlegen()
		self.assertIsNone(result["status"])
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "status"), status_vorher)

	def test_workflow_uebergang_nach_termin(self):
		uebergaenge = [
			frappe._dict(action="Ablehnen", next_state="Verloren"),
			frappe._dict(action="Termin vereinbaren", next_state="Termin"),
		]
		with (
			patch("frappe.model.workflow.get_workflow_name", return_value="Lead-Status"),
			patch("frappe.model.workflow.get_transitions", return_value=uebergaenge),
			patch("frappe.model.workflow.apply_workflow") as apply,
		):
			status, warnung = termin.status_nach_termin(self.lead.name)
		self.assertEqual((status, warnung), ("Termin", None))
		self.assertEqual(apply.call_args.args[1], "Termin vereinbaren")

	def test_workflow_fehler_wird_gemeldet(self):
		with (
			patch("frappe.model.workflow.get_workflow_name", return_value="Lead-Status"),
			patch(
				"frappe.model.workflow.get_transitions",
				return_value=[frappe._dict(action="Termin", next_state="Termin vereinbart")],
			),
			patch(
				"frappe.model.workflow.apply_workflow",
				side_effect=frappe.ValidationError("Pflichtfeld fehlt"),
			),
		):
			status, warnung = termin.status_nach_termin(self.lead.name)
		self.assertIsNone(status)
		self.assertIn("Pflichtfeld fehlt", warnung)

	def test_fremdes_event_in_cv_termin_bleibt_unberuehrt(self):
		anderer = make_lead()
		frappe.set_user(BUERO)
		fremd = termin.termin_anlegen(anderer.name, "2030-04-01 10:00:00", MEISTER, bestaetigung=0)["event"]
		frappe.set_user("Administrator")
		frappe.db.set_value("Lead", self.lead.name, "cv_termin", fremd)
		result = self._anlegen()
		self.assertIsNone(result["abgesagt"])
		self.assertEqual(frappe.db.get_value("Event", fremd, "status"), "Open")
		self.assertTrue(
			frappe.get_all(
				"ToDo", filters={"reference_type": "Event", "reference_name": fremd, "status": "Open"}
			)
		)
		# Anzeige nutzt nur Termine dieses Leads
		frappe.db.set_value("Lead", self.lead.name, "cv_termin", fremd)
		frappe.set_user(BUERO)
		self.assertEqual(termin.bestaetigung_anzeigen(self.lead.name)["event"], result["event"])

	def test_fremdes_event_ohne_eigenen_termin(self):
		anderer = make_lead()
		frappe.set_user(BUERO)
		fremd = termin.termin_anlegen(anderer.name, "2030-04-01 10:00:00", MEISTER, bestaetigung=0)["event"]
		frappe.set_user("Administrator")
		frappe.db.set_value("Lead", self.lead.name, "cv_termin", fremd)
		frappe.set_user(BUERO)
		with self.assertRaises(frappe.ValidationError):
			termin.bestaetigung_anzeigen(self.lead.name)

	def test_cv_termin_nicht_per_speichern_setzbar(self):
		eigener = self._anlegen()["event"]
		frappe.set_user("Administrator")
		lead = frappe.get_doc("Lead", self.lead.name)
		lead.flags.ignore_mandatory = True
		lead.cv_termin = None
		with self.assertRaises(frappe.PermissionError):
			lead.save()
		lead.reload()
		lead.flags.ignore_mandatory = True
		lead.notes_html = None
		lead.save()  # andere Änderungen bleiben möglich
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_termin"), eigener)

	def test_anzeigen_ohne_event_recht(self):
		self._anlegen()
		frappe.set_user("Administrator")
		andere_buero = make_user("cv-test-buero2@example.com", "Ben", ["Sales User"], "Später")
		frappe.get_doc("User", andere_buero).add_roles("Büro")  # erst nach dem Termin Büro
		frappe.set_user(andere_buero)
		with self.assertRaises(frappe.PermissionError):
			termin.bestaetigung_anzeigen(self.lead.name)

	def test_workflow_neu_nach_termin(self):
		# echter Workflow „Lead Anfrage“ aus 1.4 (cv_status)
		from frappe.model.workflow import get_workflow_name

		if not get_workflow_name("Lead"):
			self.skipTest("kein aktiver Lead-Workflow")
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_status"), "Neu")
		result = self._anlegen()
		self.assertEqual(result["status"], "Termin")
		self.assertEqual(result["warnungen"], [])
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_status"), "Termin")
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_termin"), result["event"])

	def test_fremde_adresse_wird_nicht_aufgeloest(self):
		anderer = make_lead()
		fremd = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": "Fremd",
				"address_type": "Billing",
				"address_line1": "Geheimgasse 1",
				"pincode": "10115",
				"city": "Berlin",
				"country": "Germany",
				"links": [{"link_doctype": "Lead", "link_name": anderer.name}],
			}
		).insert(ignore_permissions=True)
		if not frappe.get_meta("Lead").has_field("cv_objekt_adresse"):
			self.skipTest("cv_objekt_adresse (1.1) fehlt")
		frappe.db.set_value("Lead", self.lead.name, "cv_objekt_adresse", fremd.name)
		lead = frappe.get_doc("Lead", self.lead.name)
		text = termin.objekt_adresse(lead)
		self.assertEqual(text, fremd.name)
		self.assertNotIn("Geheimgasse", text)
		# eigene, verknüpfte Adresse wird aufgelöst
		frappe.db.set_value("Lead", self.lead.name, "cv_objekt_adresse", self.adresse.name)
		self.assertEqual(
			termin.objekt_adresse(frappe.get_doc("Lead", self.lead.name)), "Kellerweg 7\n48143 Münster"
		)
		# Freitext aus 1.1 bleibt Text
		frappe.db.set_value("Lead", self.lead.name, "cv_objekt_adresse", "Am Hang 3\n48155 Münster")
		self.assertEqual(
			termin.objekt_adresse(frappe.get_doc("Lead", self.lead.name)), "Am Hang 3\n48155 Münster"
		)
