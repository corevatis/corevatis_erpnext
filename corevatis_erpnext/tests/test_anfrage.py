"""Aufgabe 1.1: Gewerkefelder am Lead, Symptom-Abgleich, Workspace „Anfragen“."""

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from corevatis_erpnext.anfrage import ensure_anfrage_setup
from corevatis_erpnext.anfrage.custom_fields import (
	BAUTEILE,
	DRINGLICHKEITEN,
	FOTO_FIELDS,
	OBJEKTTYPEN,
	SYMPTOM_FIELDS,
	lead_fields,
)
from corevatis_erpnext.anfrage.lead import build_symptome, split_symptome

# Schnittstelle für 1.2-1.5 und die Mobile-API: Feldname -> Feldtyp
EXPECTED = {
	"cv_objekttyp": "Select",
	"cv_bauteil": "Select",
	"cv_symptome": "Small Text",
	"cv_dringlichkeit": "Select",
	"cv_objekt_adresse": "Small Text",
	"cv_beschreibung": "Small Text",
	"cv_quelle": "Data",
	"cv_foto_1": "Attach Image",
	"cv_foto_2": "Attach Image",
	"cv_foto_3": "Attach Image",
	"cv_section_gewerke": "Section Break",
	**{fieldname: "Check" for fieldname in SYMPTOM_FIELDS},
}


def _options(fieldname: str) -> list[str]:
	return [o for o in (frappe.get_meta("Lead").get_field(fieldname).options or "").split("\n") if o]


def _new_lead(**values):
	doc = frappe.get_doc({"doctype": "Lead", "first_name": "Test", "last_name": "Anfrage", **values})
	return doc.insert(ignore_permissions=True)


class TestSymptomText(UnitTestCase):
	def test_split_known_and_extra(self):
		known, extra = split_symptome("Schimmel\nfeuchte wände, Risse im Putz;Schimmel\n\n")
		self.assertEqual(known, ["cv_symptom_schimmel", "cv_symptom_feuchte_waende"])
		self.assertEqual(extra, ["Risse im Putz"])

	def test_build_keeps_standard_order(self):
		text = build_symptome({"cv_symptom_geruch", "cv_symptom_feuchte_waende"}, ["Risse im Putz"])
		self.assertEqual(text, "feuchte Wände\nGeruch\nRisse im Putz")


class TestLeadFields(IntegrationTestCase):
	def test_fields_present(self):
		meta = frappe.get_meta("Lead")
		for fieldname, fieldtype in EXPECTED.items():
			field = meta.get_field(fieldname)
			self.assertIsNotNone(field, fieldname)
			self.assertEqual(field.fieldtype, fieldtype, fieldname)

	def test_options(self):
		self.assertEqual(_options("cv_objekttyp"), list(OBJEKTTYPEN))
		self.assertEqual(_options("cv_bauteil"), list(BAUTEILE))
		self.assertEqual(_options("cv_dringlichkeit"), list(DRINGLICHKEITEN))
		self.assertEqual(frappe.get_meta("Lead").get_field("cv_dringlichkeit").default, "Normal")

	def test_custom_fields_belong_to_module(self):
		names = [f"Lead-{field['fieldname']}" for field in lead_fields()]
		modules = set(frappe.get_all("Custom Field", {"name": ["in", names]}, pluck="module"))
		self.assertEqual(modules, {"Corevatis ERPNext"})
		self.assertEqual(frappe.db.count("Custom Field", {"name": ["in", names]}), len(names))

	def test_mandatory_only_in_desk(self):
		meta = frappe.get_meta("Lead")
		for fieldname in ("cv_bauteil", "cv_symptome"):
			field = meta.get_field(fieldname)
			self.assertFalse(field.reqd, fieldname)
			self.assertEqual(field.mandatory_depends_on, "eval:1", fieldname)
		# wie ein Web-Form-/API-Lead: ohne Bauteil und Symptome speicherbar
		lead = _new_lead(cv_quelle="Web Form")
		self.assertEqual(lead.cv_dringlichkeit, "Normal")
		self.assertFalse(lead.cv_symptome)

	def test_idempotent(self):
		filters = {"dt": "Lead", "fieldname": ["like", "cv_%"]}
		before = frappe.get_all("Custom Field", filters, ["name", "modified"], order_by="name")
		ensure_anfrage_setup()
		ensure_anfrage_setup()
		after = frappe.get_all("Custom Field", filters, ["name", "modified"], order_by="name")
		self.assertEqual(before, after)
		self.assertEqual(frappe.db.count("Role", {"name": ["in", ["Büro", "Meister"]]}), 2)

	def test_field_order(self):
		fieldnames = [df.fieldname for df in frappe.get_meta("Lead").fields]
		section = fieldnames.index("cv_section_gewerke")
		self.assertLess(fieldnames.index("request_type"), fieldnames.index("cv_section_anfrage"))
		self.assertLess(section, fieldnames.index("cv_bauteil"))
		self.assertLess(fieldnames.index("cv_symptome"), fieldnames.index(FOTO_FIELDS[0]))
		self.assertLess(fieldnames.index("cv_foto_3"), fieldnames.index("contact_info_tab"))


class TestSymptomSync(IntegrationTestCase):
	def test_checkboxes_fill_text(self):
		lead = _new_lead(cv_symptom_schimmel=1, cv_symptom_feuchte_waende=1)
		self.assertEqual(lead.cv_symptome, "feuchte Wände\nSchimmel")

	def test_text_sets_checkboxes(self):
		lead = _new_lead(cv_symptome="Salzausblühungen, Wasser nach Regen, Riss")
		self.assertEqual(lead.cv_symptom_salzausbluehungen, 1)
		self.assertEqual(lead.cv_symptom_wasser_nach_regen, 1)
		self.assertEqual(lead.cv_symptom_schimmel, 0)
		self.assertEqual(lead.cv_symptome, "Salzausblühungen\nWasser nach Regen\nRiss")

	def test_unchecking_wins_over_old_text(self):
		lead = _new_lead(cv_symptom_schimmel=1, cv_symptom_geruch=1)
		lead.cv_symptom_geruch = 0
		lead.save(ignore_permissions=True)
		self.assertEqual(lead.cv_symptome, "Schimmel")

	def test_text_change_via_api(self):
		lead = _new_lead(cv_symptom_schimmel=1)
		lead.cv_symptome = "Geruch"
		lead.save(ignore_permissions=True)
		self.assertEqual((lead.cv_symptom_schimmel, lead.cv_symptom_geruch), (0, 1))


class TestWorkspace(IntegrationTestCase):
	def test_workspace_anfragen(self):
		self.assertTrue(frappe.db.exists("Workspace", "Anfragen"))
		ws = frappe.get_doc("Workspace", "Anfragen")
		self.assertEqual(ws.module, "Corevatis ERPNext")
		self.assertTrue(ws.public)
		self.assertEqual({row.role for row in ws.roles}, {"Büro", "Meister", "System Manager"})
		# Befunde mvp-test („kein CRM“): vollwertiger Einstieg mit Kennzahlen und Links
		from corevatis_erpnext.anfrage.setup import KENNZAHLEN

		karten = {row.number_card_name for row in ws.number_cards}
		self.assertEqual(karten, {k["name"] for k in KENNZAHLEN})
		for karte in karten:
			self.assertTrue(frappe.db.exists("Number Card", karte), karte)
		ziele = {link.link_to for link in ws.links if link.type == "Link"}
		for doctype in (
			"Lead",
			"Opportunity",
			"Prospect",
			"Customer",
			"Contact",
			"Address",
			"Event",
			"Campaign",
		):
			self.assertIn(doctype, ziele)
		shortcuts = {(s.link_to or s.url, s.doc_view or "") for s in ws.shortcuts}
		self.assertIn(("Lead", "List"), shortcuts)
		self.assertIn(("Lead", "New"), shortcuts)
		self.assertIn(("Event", "Calendar"), shortcuts)
		self.assertTrue(any(s.type == "URL" and "web-form" in s.url for s in ws.shortcuts))
		labels = {s.label for s in ws.shortcuts}
		blocks = frappe.parse_json(ws.content)
		self.assertEqual(
			{b["data"]["shortcut_name"] for b in blocks if b["type"] == "shortcut"},
			labels,
		)

	def test_translations(self):
		from frappe.translate import get_translations_from_csv

		translations = get_translations_from_csv("de", "corevatis_erpnext")
		self.assertEqual(translations.get("Building Component"), "Bauteil")
		self.assertEqual(translations.get("Trade Checklist"), "Gewerke-Checkliste")
		for fieldname in EXPECTED:
			label = frappe.get_meta("Lead").get_field(fieldname).label
			if label:
				self.assertIn(label, translations, label)
