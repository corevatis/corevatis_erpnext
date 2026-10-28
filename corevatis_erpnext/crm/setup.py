"""Custom Fields der CRM-Aufgaben 1.3/1.5 am Lead (idempotent, aus
``setup.install.after_install``/``after_migrate`` nach den Feldern aus 1.1).

Die fachlichen Lead-Felder (Objekttyp, Bauteil, Symptome, Objektadresse, Fotos,
Anfragestatus) legt ``corevatis_erpnext.anfrage`` an (1.1/1.4). Hier entstehen nur:

- ``cv_termin`` (Link auf Event) nach ``cv_verlustgrund``: aktueller Besichtigungstermin (1.3)
- ``cv_section_galerie`` + ``cv_galerie`` (HTML) nach ``cv_foto_3``: Foto-Galerie (1.5)

Labels sind englische Quelltexte, die deutsche Oberfläche kommt aus ``translations/de.csv``.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

MODULE = "Corevatis ERPNext"


def lead_fields() -> list[dict]:
	fields = [
		{
			"fieldname": "cv_termin",
			"fieldtype": "Link",
			"options": "Event",
			"label": "Inspection Appointment",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": "cv_verlustgrund",
			"description": "Set by the button “Schedule Appointment”.",
		},
		{
			"fieldname": "cv_section_galerie",
			"fieldtype": "Section Break",
			"label": "Photo Gallery",
			"insert_after": "cv_foto_3",
		},
		{
			"fieldname": "cv_galerie",
			"fieldtype": "HTML",
			"label": "Photo Gallery",
			"insert_after": "cv_section_galerie",
		},
	]
	for field in fields:
		field.setdefault("module", MODULE)
	return fields


def ensure_custom_fields() -> None:
	create_custom_fields({"Lead": lead_fields()}, update=True)
