"""Aufgabe 4.4: Angebotsannahme - Kunde aus Lead, Auftrag (AB-), Projekt, Ablehnung, Rechte."""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, getdate, today

from corevatis_erpnext.angebot import annahme, textbausteine
from corevatis_erpnext.tests import _angebot as td
from corevatis_erpnext.tests import _parallel
from corevatis_erpnext.tests._helpers import make_user

METHODE_ANNEHMEN = "/api/method/corevatis_erpnext.angebot.annahme.angebot_annehmen"

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
			("Quotation", "cv_lead"),
			("Sales Order", "cv_lead"),
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
		# Nachprüfung mvp-test: Anfrage am Angebot und im Spiegel am Auftrag, obwohl ERPNext das
		# Angebot beim Anlegen des Kunden schon umgestellt hat (Customer.update_lead_status)
		self.assertEqual(frappe.db.get_value("Quotation", self.angebot.name, "cv_lead"), self.lead.name)
		self.assertEqual(auftrag.cv_lead, self.lead.name)

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

	# --- Review Epic 4 ----------------------------------------------------------------------

	def _zaehler(self, angebot: str) -> tuple[int, int]:
		auftraege = annahme.auftraege_zum_angebot(angebot)
		projekte = frappe.db.count("Project", {"sales_order": ["in", auftraege]}) if auftraege else 0
		return len(auftraege), projekte

	def test_parallel_annehmen_ein_auftrag(self):
		"""A-1: zwei gleichzeitige Anfragen (Doppelklick/REST-Wiederholung) über die WSGI-App -
		genau ein Auftrag und ein Projekt; die zweite meldet „bereits angenommen“.

		Beide Anfragen warten nach der Anmeldung (Lese-Snapshot angelegt) aufeinander; die erste
		hält die Sperre 1,5 s. Ohne neue Transaktion vor der Sperre sähe die zweite den Auftrag
		der ersten nicht und legte einen zweiten an.
		"""
		token = _parallel.api_token(BUERO)
		frappe.db.commit()  # zweite Verbindung sieht nur bestätigte Daten
		schranke, langsam = _parallel.gleichzeitig(annahme, "pruefe_rolle", "auftrag_anlegen")
		with schranke, langsam:
			antworten = _parallel.parallel_post(
				METHODE_ANNEHMEN, {"quotation": self.angebot.name, "einreichen": 1}, token
			)
		frappe.db.rollback()  # neuer Snapshot im Test-Thread
		self.assertEqual(
			[a.status_code for a in antworten], [200, 200], [a.get_data(as_text=True) for a in antworten]
		)
		nachrichten = [a.json["message"] for a in antworten]
		self.assertEqual(sorted(m["bereits_angenommen"] for m in nachrichten), [False, True])
		self.assertEqual(len({m["auftrag"] for m in nachrichten}), 1)
		self.assertEqual(len({m["projekt"] for m in nachrichten}), 1)
		self.assertEqual(self._zaehler(self.angebot.name), (1, 1))
		self.assertEqual(frappe.db.count("Customer", {"lead_name": self.lead.name}), 1)

	def test_sperre_ist_erster_zugriff_auf_das_angebot(self):
		"""A-1 (Reihenfolge): ``FOR UPDATE`` auf Quotation vor jedem anderen Lesen des Angebots,
		Dublettenprüfung auf ``Sales Order Item`` als Locking Read."""
		abfragen = []
		original = frappe.db.sql

		def mitschreiben(query, *args, **kwargs):
			abfragen.append(str(query))
			return original(query, *args, **kwargs)

		frappe.set_user(BUERO)
		try:
			with patch.object(frappe.db, "sql", side_effect=mitschreiben):
				annahme.angebot_annehmen(self.angebot.name)
		finally:
			frappe.set_user("Administrator")
		angebot = [q for q in abfragen if "`tabQuotation`" in q]
		self.assertTrue(angebot)
		self.assertIn("for update", angebot[0].lower(), angebot[0])
		dublette = next(q for q in abfragen if "`tabSales Order Item`" in q and "prevdoc_docname" in q)
		self.assertIn("for update", dublette.lower())

	def test_riegel_gegen_zweiten_auftrag(self):
		"""A-1 (fachlicher Riegel): sieht die Dublettenprüfung den vorhandenen Auftrag nicht
		(veralteter Snapshot, nachgestellt), bricht die Annahme nach dem Anlegen ab."""
		erst = self._annehmen(einreichen=0)
		frappe.db.savepoint("cv_riegel")
		with patch.object(annahme, "bestehender_auftrag", return_value=None):
			with self.assertRaises(frappe.DuplicateEntryError):
				self._annehmen(einreichen=0)
		frappe.db.rollback(save_point="cv_riegel")
		self.assertEqual(annahme.auftraege_zum_angebot(self.angebot.name), [erst["auftrag"]])

	def test_kundendublette_gleiche_email(self):
		"""A-3: Kunde mit Kontakt derselben E-Mail wird übernommen, kein zweiter Kunde."""
		email = self.lead.email_id
		kunde = frappe.get_doc(
			{"doctype": "Customer", "customer_name": "Familie Feucht Bestand", "customer_type": "Individual"}
		)
		kunde.customer_group = annahme._kundengruppe(None)
		kunde.insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "Bestand",
				"email_ids": [{"email_id": email, "is_primary": 1}],
				"links": [{"link_doctype": "Customer", "link_name": kunde.name}],
			}
		).insert(ignore_permissions=True)
		vorher = frappe.db.count("Customer")
		r = self._annehmen()
		self.assertEqual((r["kunde"], r["kunde_neu"]), (kunde.name, False))
		self.assertEqual(frappe.db.count("Customer"), vorher)
		self.assertTrue(any(kunde.name in w and email in w for w in r["warnungen"]), r["warnungen"])
		# Objektanschrift hängt jetzt auch am Bestandskunden, der Lead bleibt am Angebot
		adressen = annahme._verknuepft("Address", "Customer", kunde.name)
		self.assertTrue(frappe.db.exists("Address", {"name": ["in", adressen], "pincode": "48143"}))
		angebot = frappe.get_doc("Quotation", self.angebot.name)
		self.assertEqual((angebot.party_name, angebot.cv_lead), (kunde.name, self.lead.name))
		self.assertEqual(annahme.lead_zum_angebot(angebot), self.lead.name)
		self.assertEqual(frappe.db.get_value("Lead", self.lead.name, "cv_status"), "Gewonnen")

	def test_kundendublette_name_und_plz(self):
		"""A-3: gleicher Name und PLZ in der Anschrift des Kunden → Bestandskunde."""
		kunde = frappe.get_doc(
			{"doctype": "Customer", "customer_name": self.lead.lead_name, "customer_type": "Individual"}
		)
		kunde.customer_group = annahme._kundengruppe(None)
		kunde.insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": kunde.name,
				"address_type": "Billing",
				"address_line1": "Altbau 1",
				"pincode": "48143",
				"city": "Münster",
				"country": "Germany",
				"links": [{"link_doctype": "Customer", "link_name": kunde.name}],
			}
		).insert(ignore_permissions=True)
		r = self._annehmen()
		self.assertEqual((r["kunde"], r["kunde_neu"]), (kunde.name, False))
		self.assertTrue(any("48143" in w for w in r["warnungen"]), r["warnungen"])
		# andere PLZ: kein Treffer
		lead = td.lead_mit_angebot_status(
			first_name=self.lead.first_name,
			last_name=self.lead.last_name,
			cv_objekt_adresse="Am Deich 2, 26789 Leer",
		)
		self.assertEqual(
			annahme.bestehender_kunde(lead.name, td.angebot(lead.name, zusatz=False)), (None, None)
		)

	def test_cv_lead_nur_vom_server(self):
		"""Security-Review: ``cv_lead`` aus Formular/REST wird verworfen; ein fremder Lead in
		``cv_lead`` zählt bei der Annahme nicht; ohne Schreibrecht am Lead keine Annahme."""
		fremd = td.lead_mit_angebot_status()
		entwurf = frappe.copy_doc(self.angebot)
		entwurf.docstatus = 0
		entwurf.cv_lead = fremd.name
		entwurf.insert(ignore_permissions=True)
		self.assertIsNone(frappe.db.get_value("Quotation", entwurf.name, "cv_lead"))
		entwurf.reload()
		entwurf.cv_lead = fremd.name
		entwurf.save(ignore_permissions=True)
		self.assertIsNone(frappe.db.get_value("Quotation", entwurf.name, "cv_lead"))

		# direkt in der DB (z. B. Altbestand): Lead gehört nicht zum Kunden → nicht benutzt
		kunde = frappe.get_doc(
			{"doctype": "Customer", "customer_name": "Kunde ohne Lead", "customer_type": "Individual"}
		)
		kunde.customer_group = annahme._kundengruppe(None)
		kunde.insert(ignore_permissions=True)
		angebot = td.angebot(kunde.name, quotation_to="Customer", zusatz=False)
		frappe.db.set_value("Quotation", angebot.name, "cv_lead", fremd.name, update_modified=False)
		angebot.reload()
		self.assertIsNone(annahme.lead_zum_angebot(angebot))
		annahme.angebot_annehmen(angebot.name)
		self.assertEqual(frappe.db.get_value("Lead", fremd.name, "cv_status"), "Angebot")
		self.assertFalse(frappe.db.get_value("Lead", fremd.name, "cv_projekt"))

		original = frappe.has_permission

		def ohne_lead_recht(doctype, ptype="read", *args, **kwargs):
			if doctype == "Lead" and ptype == "write":
				if kwargs.get("throw") or (len(args) > 2 and args[2]):
					raise frappe.PermissionError
				return False
			return original(doctype, ptype, *args, **kwargs)

		with patch("frappe.has_permission", side_effect=ohne_lead_recht):
			with self.assertRaises(frappe.PermissionError):
				self._annehmen()
		self.assertFalse(annahme.bestehender_auftrag(self.angebot.name))

	def test_projekt_nur_fuer_corevatis_angebote(self):
		"""A-5: gewöhnliches Angebot (ohne Aufmaß) → Auftrag ohne Projekt; mit ``projekt_anlegen``
		bzw. aus dem Aufmaß → Projekt; ERPNext-Weg ohne Aufmaß → kein Projekt."""
		self.assertTrue(annahme.ist_corevatis_angebot(self.angebot))
		ohne = td.angebot(self.lead.name, zusatz=False, corevatis=False)
		self.assertFalse(annahme.ist_corevatis_angebot(ohne))
		r = annahme.angebot_annehmen(ohne.name)
		self.assertTrue(frappe.db.exists("Sales Order", r["auftrag"]))
		self.assertEqual((r["projekt"], r["projekt_neu"]), (None, False))
		self.assertFalse(frappe.db.get_value("Project", {"sales_order": r["auftrag"]}))
		# nachträglich auf Wunsch
		r2 = annahme.angebot_annehmen(ohne.name, projekt_anlegen=1)
		self.assertTrue(r2["projekt_neu"])
		self.assertEqual(frappe.db.get_value("Project", r2["projekt"], "sales_order"), r["auftrag"])

		from erpnext.selling.doctype.quotation.quotation import make_sales_order

		lead2 = td.lead_mit_angebot_status()
		weiteres = td.angebot(lead2.name, zusatz=False, corevatis=False)
		auftrag = make_sales_order(weiteres.name)
		auftrag.delivery_date = add_days(today(), 7)
		for z in auftrag.items:
			z.delivery_date = auftrag.delivery_date
		auftrag.insert(ignore_permissions=True)
		auftrag.submit()
		self.assertFalse(frappe.db.get_value("Project", {"sales_order": auftrag.name}))

	def test_eventualposition_ist_kein_ersatz_fuer_die_zeile_davor(self):
		"""A-2: nach dem Einreichen hat die Normalposition vor der Eventualposition kein
		``has_alternative_item``; der ERPNext-Weg mit Auswahl der Eventualposition übernimmt die
		Normalposition trotzdem (vorher fiel sie stillschweigend weg)."""
		from erpnext.selling.doctype.quotation.quotation import make_sales_order

		angebot = frappe.get_doc("Quotation", self.angebot.name)
		haupt, eventual, _alternativ = angebot.items
		self.assertEqual((haupt.has_alternative_item, eventual.is_alternative), (0, 1))
		self.assertEqual(textbausteine.saetze_ohne_ersatz(angebot), [])
		self.assertEqual(flt(angebot.net_total), 500.0)  # Summe ohne Eventual-/Alternativposition

		frappe.flags.args = {"selected_items": [{"name": eventual.name}]}
		try:
			auftrag = make_sales_order(angebot.name)
		finally:
			frappe.flags.pop("args", None)
		self.assertEqual(
			sorted(z.item_code for z in auftrag.items), sorted([td.ARTIKEL, td.ARTIKEL_EVENTUAL])
		)
		# echte ERPNext-Alternativen (ohne Corevatis-Kennzeichen) behalten ihre Bedeutung
		doc = frappe.copy_doc(self.angebot)
		doc.docstatus = 0
		doc.items[1].cv_eventualposition = 0
		doc.items[2].cv_alternativposition = 0
		doc.insert(ignore_permissions=True)
		doc.submit()
		self.assertEqual(doc.items[0].has_alternative_item, 1)

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
