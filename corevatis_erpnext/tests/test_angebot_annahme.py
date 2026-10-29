"""Aufgabe 4.4: Angebotsannahme - Kunde aus Lead, Auftrag (AB-), Projekt, Ablehnung, Rechte."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, getdate, today

from corevatis_erpnext.angebot import annahme
from corevatis_erpnext.tests import _angebot as td
from corevatis_erpnext.tests._helpers import make_user

BUERO = "cv-test-buero@example.com"
MEISTER = "cv-test-meister@example.com"
MONTEUR = "cv-test-monteur-annahme@example.com"


class TestAngebotAnnahme(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(BUERO, "Bea", ["Büro", "Sales User"], "Büro")
		make_user(MEISTER, "Max", ["Meister", "Sales User"], "Meister")
		make_user(MONTEUR, "Moritz", ["Monteur"], "Monteur")
		td.ensure_felder_41()
		td.ensure_artikel()

	def setUp(self):
		frappe.set_user("Administrator")
		self.lead = td.lead_mit_angebot_status()
		self.angebot = td.angebot(self.lead.name, cv_titel="Kellerabdichtung Musterfrau")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _annehmen(self, user=BUERO, **kwargs):
		frappe.set_user(user)
		try:
			return annahme.angebot_annehmen(self.angebot.name, **kwargs)
		finally:
			frappe.set_user("Administrator")

	# --- Felder -----------------------------------------------------------------------------

	def test_felder(self):
		for doctype, feld in (
			("Project", "cv_objekt"),
			("Project", "cv_aufmass"),
			("Project", "cv_lead"),
			("Project", "cv_angebot"),
			("Lead", "cv_projekt"),
			("ToDo", "cv_nachfass_zyklus"),
			# Spiegel der Angebotsfelder (4.1) am Auftrag, gleiche Namen
			("Sales Order", "cv_titel"),
			("Sales Order Item", "cv_eventualposition"),
			("Sales Order Item", "cv_alternativposition"),
		):
			with self.subTest(doctype=doctype, feld=feld):
				self.assertTrue(frappe.get_meta(doctype).has_field(feld))
		self.assertEqual(frappe.get_meta("Project").get_field("cv_objekt").fieldtype, "Data")
		quelle = frappe.get_meta("Quotation Item").get_field("cv_eventualposition")
		spiegel = frappe.get_meta("Sales Order Item").get_field("cv_eventualposition")
		self.assertEqual((spiegel.fieldtype, spiegel.options), (quelle.fieldtype, quelle.options))
		self.assertTrue(frappe.db.exists("Project Type", annahme.PROJEKTTYP))
		self.assertTrue(frappe.db.exists("Quotation Lost Reason", "Preis"))
		self.assertIn(
			"corevatis_erpnext.angebot.annahme.auftrag_eingereicht",
			frappe.get_hooks("doc_events")["Sales Order"]["on_submit"],
		)

	# --- Annahme ----------------------------------------------------------------------------

	def test_annehmen_legt_kunde_auftrag_projekt_an(self):
		start, ende = today(), add_days(today(), 10)
		r = self._annehmen(auftragsdatum=start, lieferdatum=ende, einreichen=1)
		self.assertFalse(r["bereits_angenommen"])
		self.assertTrue(r["kunde_neu"] and r["auftrag_neu"] and r["projekt_neu"])
		self.assertEqual(r["warnungen"], [])

		# Kunde aus dem Lead, mit Anschrift aus der Objektadresse und Kontakt
		kunde = frappe.get_doc("Customer", r["kunde"])
		self.assertEqual(kunde.lead_name, self.lead.name)
		adressen = frappe.get_all(
			"Dynamic Link",
			filters={"parenttype": "Address", "link_doctype": "Customer", "link_name": kunde.name},
			pluck="parent",
		)
		self.assertEqual(len(adressen), 1)
		adresse = frappe.get_doc("Address", adressen[0])
		self.assertEqual(
			(adresse.address_line1, adresse.pincode, adresse.city), ("Kellerweg 7", "48143", "Münster")
		)
		self.assertTrue(adresse.has_link("Lead", self.lead.name))
		self.assertEqual(kunde.customer_primary_address, adresse.name)
		self.assertTrue(
			frappe.get_all(
				"Dynamic Link",
				filters={"parenttype": "Contact", "link_doctype": "Customer", "link_name": kunde.name},
			)
		)
		# Angebot zeigt jetzt auf den Kunden (ERPNext-Verhalten), Lead bleibt über lead_name erreichbar
		angebot = frappe.get_doc("Quotation", self.angebot.name)
		self.assertEqual((angebot.quotation_to, angebot.party_name), ("Customer", kunde.name))

		# Auftrag: AB-Nummernkreis, ohne Eventual-/Alternativposition, Steuer übernommen, cv_* gespiegelt
		auftrag = frappe.get_doc("Sales Order", r["auftrag"])
		self.assertEqual(auftrag.docstatus, 1)
		self.assertTrue(auftrag.name.startswith(f"AB-{getdate().year}-"), auftrag.name)
		self.assertEqual(auftrag.customer, kunde.name)
		self.assertEqual([z.item_code for z in auftrag.items], [td.ARTIKEL])
		self.assertEqual(auftrag.items[0].prevdoc_docname, self.angebot.name)
		self.assertEqual(getdate(auftrag.transaction_date), getdate(start))
		self.assertEqual(getdate(auftrag.delivery_date), getdate(ende))
		self.assertEqual(auftrag.taxes_and_charges, self.angebot.taxes_and_charges)
		self.assertEqual(flt(auftrag.net_total), 500.0)
		if self.angebot.taxes_and_charges:
			self.assertEqual(len(auftrag.taxes), len(self.angebot.taxes))
			self.assertEqual(flt(auftrag.total_taxes_and_charges), 95.0)
		self.assertEqual(auftrag.cv_titel, "Kellerabdichtung Musterfrau")

		# Projekt
		projekt = frappe.get_doc("Project", r["projekt"])
		# project_name ist eindeutig - andere Tests dieser Klasse haben dasselbe Objekt (Zusatz „(AB-…)“)
		self.assertTrue(
			projekt.project_name.startswith(f"Kellerweg 7, 48143 Münster{annahme.TRENNER}Innenabdichtung"),
			projekt.project_name,
		)
		self.assertEqual(
			(projekt.customer, projekt.sales_order, projekt.status, projekt.project_type),
			(kunde.name, auftrag.name, "Open", "Sanierung"),
		)
		self.assertEqual(getdate(projekt.expected_start_date), getdate(start))
		self.assertEqual(getdate(projekt.expected_end_date), getdate(ende))
		self.assertEqual((projekt.cv_lead, projekt.cv_angebot), (self.lead.name, self.angebot.name))
		self.assertEqual(auftrag.project, projekt.name)

		# Lead: Gewonnen über den Workflow, Projekt verknüpft
		lead = frappe.get_doc("Lead", self.lead.name)
		self.assertEqual(lead.cv_status, "Gewonnen")
		self.assertEqual(lead.cv_projekt, projekt.name)
		self.assertEqual(r["lead_status"], "Gewonnen")

		# Angebot: Eventual-/Alternativposition offen, trotzdem „Ordered“ (nicht „Partially Ordered“)
		self.assertEqual(frappe.db.get_value("Quotation", self.angebot.name, "status"), "Ordered")

	def test_zweiter_aufruf_idempotent(self):
		erst = self._annehmen()
		zaehler = (
			frappe.db.count("Customer", {"lead_name": self.lead.name}),
			frappe.db.count("Sales Order Item", {"prevdoc_docname": self.angebot.name}),
			frappe.db.count("Project", {"cv_angebot": self.angebot.name}),
		)
		zweit = self._annehmen()
		self.assertTrue(zweit["bereits_angenommen"])
		self.assertFalse(zweit["kunde_neu"] or zweit["auftrag_neu"] or zweit["projekt_neu"])
		self.assertEqual(
			(zweit["kunde"], zweit["auftrag"], zweit["projekt"]),
			(erst["kunde"], erst["auftrag"], erst["projekt"]),
		)
		self.assertEqual(
			zaehler,
			(
				frappe.db.count("Customer", {"lead_name": self.lead.name}),
				frappe.db.count("Sales Order Item", {"prevdoc_docname": self.angebot.name}),
				frappe.db.count("Project", {"cv_angebot": self.angebot.name}),
			),
		)
		self.assertEqual(zaehler, (1, 1, 1))

	def test_entwurf_und_spaeter_einreichen(self):
		r = self._annehmen(einreichen=0)
		self.assertEqual(r["auftrag_docstatus"], 0)
		self.assertTrue(frappe.db.exists("Project", r["projekt"]))
		# Entwurf: Angebot gilt als angenommen (kein zweiter Auftrag), ERPNext-Status noch offen
		r2 = self._annehmen(einreichen=1)
		self.assertEqual((r2["auftrag"], r2["projekt"]), (r["auftrag"], r["projekt"]))
		self.assertFalse(r2["projekt_neu"])
		self.assertEqual(frappe.db.get_value("Sales Order", r["auftrag"], "docstatus"), 1)

	def test_bestehender_kunde_wird_benutzt(self):
		kunde = frappe.get_doc(
			{"doctype": "Customer", "customer_name": "Bestandskunde Annahme", "customer_type": "Individual"}
		)
		kunde.customer_group = annahme._kundengruppe(None)
		kunde.insert(ignore_permissions=True)
		angebot = td.angebot(kunde.name, quotation_to="Customer", zusatz=False)
		frappe.set_user(BUERO)
		r = annahme.angebot_annehmen(angebot.name)
		frappe.set_user("Administrator")
		self.assertEqual(r["kunde"], kunde.name)
		self.assertFalse(r["kunde_neu"])
		self.assertIsNone(r["lead"])
		self.assertEqual(frappe.db.get_value("Quotation", angebot.name, "status"), "Ordered")

	def test_meister_darf_annehmen_lead_bleibt_mit_warnung(self):
		# Workflow 1.4: „Als gewonnen markieren“ nur Büro - Auftrag entsteht trotzdem
		r = self._annehmen(user=MEISTER)
		self.assertTrue(frappe.db.exists("Sales Order", r["auftrag"]))
		self.assertTrue(frappe.db.exists("Project", r["projekt"]))
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_status"), "Angebot")
		self.assertEqual(len(r["warnungen"]), 1, r["warnungen"])

	def test_lead_aus_besichtigt(self):
		frappe.db.set_value("Lead", self.lead.name, "cv_status", "Besichtigt", update_modified=False)
		r = self._annehmen()
		self.assertEqual(r["lead_status"], "Gewonnen")
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_status"), "Gewonnen")

	def test_monteur_403(self):
		frappe.set_user(MONTEUR)
		try:
			with self.assertRaises(frappe.PermissionError):
				annahme.angebot_annehmen(self.angebot.name)
			with self.assertRaises(frappe.PermissionError):
				annahme.angebot_ablehnen(self.angebot.name, "Preis")
		finally:
			frappe.set_user("Administrator")
		self.assertFalse(annahme.bestehender_auftrag(self.angebot.name))

	def test_entwurf_wird_abgelehnt(self):
		entwurf = frappe.copy_doc(self.angebot)
		entwurf.docstatus = 0
		entwurf.insert(ignore_permissions=True)
		frappe.set_user(BUERO)
		try:
			with self.assertRaises(frappe.ValidationError):
				annahme.angebot_annehmen(entwurf.name)
		finally:
			frappe.set_user("Administrator")

	def test_projektname_eindeutig(self):
		self.assertEqual(annahme.projekt_name("Neu Weg 1 - Test", "AB-X"), "Neu Weg 1 - Test")
		r = self._annehmen()
		name = frappe.db.get_value("Project", r["projekt"], "project_name")
		self.assertEqual(annahme.projekt_name(name, "AB-2099-00001"), f"{name} (AB-2099-00001)")

	def test_erpnext_weg_auftrag_bekommt_projekt(self):
		"""„Erstellen → Auftrag“ (ERPNext) und Einreichen: Hook legt das Projekt an."""
		from erpnext.selling.doctype.quotation.quotation import make_sales_order

		auftrag = make_sales_order(self.angebot.name)
		auftrag.delivery_date = add_days(today(), 7)
		for z in auftrag.items:
			z.delivery_date = auftrag.delivery_date
		auftrag.insert(ignore_permissions=True)
		self.assertFalse(frappe.db.get_value("Project", {"sales_order": auftrag.name}))
		auftrag.submit()
		projekt = frappe.db.get_value("Project", {"sales_order": auftrag.name})
		self.assertTrue(projekt)
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_status"), "Gewonnen")
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_projekt"), projekt)
		# Annahme danach: meldet den vorhandenen Auftrag und das Projekt
		r = self._annehmen()
		self.assertTrue(r["bereits_angenommen"])
		self.assertEqual((r["auftrag"], r["projekt"]), (auftrag.name, projekt))

	# --- Ablehnung --------------------------------------------------------------------------

	def test_ablehnen_setzt_angebot_und_lead(self):
		frappe.set_user(BUERO)
		r = annahme.angebot_ablehnen(self.angebot.name, "Preis", "Wettbewerber 20 % günstiger")
		frappe.set_user("Administrator")
		angebot = frappe.get_doc("Quotation", self.angebot.name)
		self.assertEqual(angebot.status, "Lost")
		self.assertEqual(angebot.order_lost_reason, "Wettbewerber 20 % günstiger")
		self.assertEqual([z.lost_reason for z in angebot.lost_reasons], ["Preis"])
		lead = frappe.get_doc("Lead", self.lead.name)
		self.assertEqual((lead.cv_status, lead.cv_verlustgrund), ("Verloren", "Preis"))
		self.assertEqual((r["status"], r["lead_status"], r["warnungen"]), ("Lost", "Verloren", []))
		# zweiter Aufruf: nichts doppelt
		frappe.set_user(BUERO)
		r2 = annahme.angebot_ablehnen(self.angebot.name, "Preis")
		frappe.set_user("Administrator")
		self.assertTrue(r2["bereits_abgelehnt"])
		self.assertEqual(len(frappe.get_doc("Quotation", self.angebot.name).lost_reasons), 1)
		# angenommen werden kann es nicht mehr
		with self.assertRaises(frappe.ValidationError):
			annahme.angebot_annehmen(self.angebot.name)

	def test_ablehnen_ohne_grund(self):
		frappe.set_user(BUERO)
		try:
			for grund in ("", "Wetter"):
				with self.assertRaises(frappe.MandatoryError):
					annahme.angebot_ablehnen(self.angebot.name, grund)
		finally:
			frappe.set_user("Administrator")
		self.assertNotEqual(frappe.db.get_value("Quotation", self.angebot.name, "status"), "Lost")

	def test_ablehnen_nach_annahme_verboten(self):
		self._annehmen(einreichen=0)
		frappe.set_user(BUERO)
		try:
			with self.assertRaises(frappe.ValidationError):
				annahme.angebot_ablehnen(self.angebot.name, "Preis")
		finally:
			frappe.set_user("Administrator")

	def test_lead_mit_weiterem_offenem_angebot_bleibt_offen(self):
		zweites = td.angebot(self.lead.name, zusatz=False)
		frappe.set_user(BUERO)
		r = annahme.angebot_ablehnen(self.angebot.name, "Preis")
		frappe.set_user("Administrator")
		self.assertEqual(r["status"], "Lost")
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_status"), "Angebot")
		self.assertIn(zweites.name, r["warnungen"][0])

	def test_erpnext_verloren_dialog_setzt_lead(self):
		"""ERPNext „Als verloren festlegen“ (declare_enquiry_lost) - Lead folgt mit Grund."""
		annahme.ensure_verlustgruende()
		angebot = frappe.get_doc("Quotation", self.angebot.name)
		angebot.declare_enquiry_lost([{"lost_reason": "Wettbewerber"}], [], detailed_reason="anderer Betrieb")
		lead = frappe.get_doc("Lead", self.lead.name)
		self.assertEqual((lead.cv_status, lead.cv_verlustgrund), ("Verloren", "Wettbewerber"))

	# --- Bausteine --------------------------------------------------------------------------

	def test_zerlege_anschrift(self):
		self.assertEqual(
			annahme.zerlege_anschrift("Kellerweg 7, 48143 Münster"),
			{"strasse": "Kellerweg 7", "plz": "48143", "ort": "Münster"},
		)
		self.assertEqual(
			annahme.zerlege_anschrift("Familie Muster\nAm Bach 3a\n48149 Münster-Mitte"),
			{"strasse": "Am Bach 3a", "plz": "48149", "ort": "Münster-Mitte"},
		)
		self.assertEqual(annahme.zerlege_anschrift("Münster"), {})
		self.assertEqual(annahme.zerlege_anschrift(""), {})
