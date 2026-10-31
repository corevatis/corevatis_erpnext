"""Abwesenheit ohne Änderungsverfolgung (P2.5, Review): vorhandene Versionen enthielten Art und Notiz, die
Meister seit corevatis_erpnext#89 nicht sehen dürfen - sie werden entfernt."""

import frappe


def execute():
	frappe.db.delete("Version", {"ref_doctype": "Abwesenheit"})
