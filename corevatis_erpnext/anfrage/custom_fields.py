"""Custom Fields am ERPNext-Doctype ``Lead`` (Aufgabe 1.1).

Die Feldnamen sind Schnittstelle für Web Form (1.2), Termin (1.3), Workflow (1.4),
Foto-Galerie (1.5) und die Mobile-API - nicht umbenennen. Labels sind englische
Quelltexte, die deutsche Oberfläche kommt aus ``translations/de.csv``; die
Select-Werte sind deutsche Daten (so stehen sie in der Datenbank und in der API).

Pflichtfelder: Bauteil und Symptome sind nur im Desk Pflicht
(``mandatory_depends_on``, wird nur im Formular ausgewertet). Leads aus dem Web Form,
der Mobile-API oder dem E-Mail-Eingang werden nicht abgewiesen; die Vorqualifizierung
holt das Büro beim ersten Speichern im Desk nach.
"""

MODULE = "Corevatis ERPNext"

OBJEKTTYPEN = ("EFH", "MFH", "Gewerbe", "Öffentlich", "Sonstiges")
BAUTEILE = ("Keller außen", "Keller innen", "Sockel", "EG/Wand", "Dach", "Bad", "Sonstiges")
DRINGLICHKEITEN = ("Normal", "Dringend", "Notfall")

# Aufgabe 1.4: eigener Anfragestatus (Workflow „Lead Anfrage“), unabhängig von Lead.status
ANFRAGE_STATUS = ("Neu", "Termin", "Besichtigt", "Angebot", "Gewonnen", "Verloren")
STATUS_VERLOREN = "Verloren"
VERLUSTGRUENDE = ("Preis", "kein Bedarf", "Wettbewerber", "keine Rückmeldung", "Sonstiges")

# (Checkbox-Feld, englisches Label, Wert in cv_symptome) - Reihenfolge = Anzeige
SYMPTOME = (
	("cv_symptom_feuchte_waende", "Damp Walls", "feuchte Wände"),
	("cv_symptom_schimmel", "Mould", "Schimmel"),
	("cv_symptom_salzausbluehungen", "Salt Efflorescence", "Salzausblühungen"),
	("cv_symptom_wasser_nach_regen", "Water after Rain", "Wasser nach Regen"),
	("cv_symptom_geruch", "Musty Odour", "Geruch"),
	("cv_symptom_sonstiges", "Other Symptom", "Sonstiges"),
)
SYMPTOM_FIELDS = tuple(fieldname for fieldname, _label, _value in SYMPTOME)
FOTO_FIELDS = ("cv_foto_1", "cv_foto_2", "cv_foto_3")

# im Desk Pflicht (Formular), serverseitig nicht
DESK_MANDATORY = "eval:1"


def _select(*values: str, blank: bool = True) -> str:
	return "\n".join((("",) if blank else ()) + values)


def lead_fields() -> list[dict]:
	"""Custom Fields am Lead in Formular-Reihenfolge (``insert_after`` verkettet)."""
	fields: list[dict] = [
		# Anfragestatus (1.4) im Kopf neben dem ERPNext-Status; gesetzt nur über den
		# Workflow „Lead Anfrage“ (Aktionen, Kanban), deshalb im Formular schreibgeschützt
		{
			"fieldname": "cv_status",
			"fieldtype": "Select",
			"label": "Inquiry Status",
			"options": _select(*ANFRAGE_STATUS, blank=False),
			"default": ANFRAGE_STATUS[0],
			"read_only": 1,
			"no_copy": 1,
			"in_standard_filter": 1,
			"insert_after": "status",
		},
		{
			"fieldname": "cv_verlustgrund",
			"fieldtype": "Select",
			"label": "Loss Reason",
			"options": _select(*VERLUSTGRUENDE),
			"depends_on": f"eval:doc.cv_status=='{STATUS_VERLOREN}'",
			"mandatory_depends_on": f"eval:doc.cv_status=='{STATUS_VERLOREN}'",
			"no_copy": 1,
			"in_standard_filter": 1,
			"insert_after": "cv_status",
		},
		# Abschnitt „Anfrage“ nach den Kopfdaten (request_type ist das letzte Feld vor
		# „Contact Info“)
		{
			"fieldname": "cv_section_anfrage",
			"fieldtype": "Section Break",
			"label": "Inquiry",
			"insert_after": "request_type",
		},
		{
			"fieldname": "cv_beschreibung",
			"fieldtype": "Small Text",
			"label": "Problem Description",
			"insert_after": "cv_section_anfrage",
		},
		{
			"fieldname": "cv_objekt_adresse",
			"fieldtype": "Small Text",
			"label": "Property Address",
			"description": "Address of the property, if different from the customer address",
			"insert_after": "cv_beschreibung",
		},
		{
			"fieldname": "cv_column_anfrage",
			"fieldtype": "Column Break",
			"insert_after": "cv_objekt_adresse",
		},
		{
			"fieldname": "cv_dringlichkeit",
			"fieldtype": "Select",
			"label": "Urgency",
			"options": _select(*DRINGLICHKEITEN, blank=False),
			"default": "Normal",
			"in_list_view": 1,
			"in_standard_filter": 1,
			"insert_after": "cv_column_anfrage",
		},
		{
			"fieldname": "cv_quelle",
			"fieldtype": "Data",
			"label": "Inquiry Source",
			"description": "e.g. Web Form, Telefon, WhatsApp, Empfehlung, Google",
			"in_standard_filter": 1,
			"insert_after": "cv_dringlichkeit",
		},
		# Abschnitt „Gewerke-Checkliste“: Vorqualifizierung
		{
			"fieldname": "cv_section_gewerke",
			"fieldtype": "Section Break",
			"label": "Trade Checklist",
			"insert_after": "cv_quelle",
		},
		{
			"fieldname": "cv_objekttyp",
			"fieldtype": "Select",
			"label": "Property Type",
			"options": _select(*OBJEKTTYPEN),
			"in_standard_filter": 1,
			"insert_after": "cv_section_gewerke",
		},
		{
			"fieldname": "cv_bauteil",
			"fieldtype": "Select",
			"label": "Building Component",
			"options": _select(*BAUTEILE),
			"mandatory_depends_on": DESK_MANDATORY,
			"in_standard_filter": 1,
			"insert_after": "cv_objekttyp",
		},
		{
			"fieldname": "cv_column_symptome",
			"fieldtype": "Column Break",
			"label": "Symptoms",
			"insert_after": "cv_bauteil",
		},
	]

	previous = "cv_column_symptome"
	for fieldname, label, _value in SYMPTOME:
		fields.append(
			{"fieldname": fieldname, "fieldtype": "Check", "label": label, "insert_after": previous}
		)
		previous = fieldname

	fields += [
		{
			"fieldname": "cv_column_symptome_text",
			"fieldtype": "Column Break",
			"insert_after": previous,
		},
		{
			"fieldname": "cv_symptome",
			"fieldtype": "Small Text",
			"label": "Symptoms",
			"description": "One symptom per line; filled from the checkboxes, free text allowed",
			"mandatory_depends_on": DESK_MANDATORY,
			"insert_after": "cv_column_symptome_text",
		},
		# Abschnitt „Fotos“: drei Attach-Image-Felder, die das Web Form (1.2) direkt
		# befüllen kann (Web Forms laden nur in Attach-Felder des Hauptdokuments hoch)
		{
			"fieldname": "cv_section_fotos",
			"fieldtype": "Section Break",
			"label": "Photos",
			"insert_after": "cv_symptome",
		},
	]

	previous = "cv_section_fotos"
	for index, fieldname in enumerate(FOTO_FIELDS, start=1):
		if index > 1:
			column = f"cv_column_foto_{index}"
			fields.append({"fieldname": column, "fieldtype": "Column Break", "insert_after": previous})
			previous = column
		fields.append(
			{
				"fieldname": fieldname,
				"fieldtype": "Attach Image",
				"label": f"Photo {index}",
				"insert_after": previous,
			}
		)
		previous = fieldname

	for field in fields:
		field.setdefault("module", MODULE)
	return fields


def custom_fields() -> dict[str, list[dict]]:
	return {"Lead": lead_fields()}
