"""Laufende Zeitbuchung eines Mitarbeiters (Start ohne Stopp, Aufgabe 3.9).

Name = Employee: höchstens eine laufende Buchung je Mitarbeiter. Angelegt und entfernt von
``corevatis_erpnext.zeiterfassung`` (Mobile-API ``zeit_start``/``zeit_stopp``); beim Stopp
entsteht daraus eine Zeile im Timesheet des Tages. Im Desk nur zur Ansicht und zum Aufräumen
(z. B. vergessener Stopp: Büro löscht den Lauf und bucht die Zeit im Timesheet).
"""

from frappe.model.document import Document


class ZeitbuchungLauf(Document):
	pass
