"""Gleichzeitige Anfragen über die WSGI-App (Review Epic 4, A-1/T-1).

Jede Anfrage läuft in einem eigenen Thread mit eigenem ``frappe.local`` und eigener
Datenbankverbindung - wie zwei Worker auf der Site (Doppelklick, REST-Wiederholung). Die
Testdaten müssen dafür bestätigt sein (``frappe.db.commit()``), sonst sieht die zweite
Verbindung sie nicht.
"""

import threading
import time
from unittest.mock import patch

import frappe
from frappe.utils import get_test_client


def api_token(user: str) -> str:
	"""``Authorization``-Header mit neuem API-Schlüssel für ``user`` (wie ``frappe.tests.test_api``)."""
	doc = frappe.get_doc("User", user)
	secret = frappe.generate_hash(length=15)
	if not doc.api_key:
		doc.api_key = frappe.generate_hash(length=15)
	doc.api_secret = secret
	doc.save(ignore_permissions=True)
	return f"token {doc.api_key}:{secret}"


def gleichzeitig(modul, schranke_vor: str, langsam: str, anzahl: int = 2, pause: float = 1.5):
	"""Patches für eine sichere Überlappung: Alle Anfragen warten nach der Anmeldung (Lese-Snapshot
	schon angelegt) an ``modul.<schranke_vor>`` aufeinander; ``modul.<langsam>`` hält danach die
	Sperre ``pause`` Sekunden, bevor die erste Anfrage bestätigt."""
	schranke = threading.Barrier(anzahl, timeout=60)
	vorher = getattr(modul, schranke_vor)
	original = getattr(modul, langsam)

	def warten(*args, **kwargs):
		ergebnis = vorher(*args, **kwargs)
		schranke.wait()
		return ergebnis

	def verzoegert(*args, **kwargs):
		ergebnis = original(*args, **kwargs)
		time.sleep(pause)
		return ergebnis

	return (
		patch.object(modul, schranke_vor, side_effect=warten),
		patch.object(modul, langsam, side_effect=verzoegert),
	)


def parallel_post(path: str, data: dict, token: str, anzahl: int = 2) -> list:
	"""``anzahl`` gleichzeitige POST-Anfragen; Rückgabe: Antworten (werkzeug ``TestResponse``)."""
	site = frappe.local.site
	antworten: list = [None] * anzahl
	fehler: list = []

	def run(i: int):
		try:
			client = get_test_client(use_cookies=False)
			antworten[i] = client.post(path, data=data, headers={"Authorization": token})
		except Exception as e:  # im Haupt-Thread erneut auslösen
			fehler.append(e)

	with patch("frappe.app.get_site_name", return_value=site):
		threads = [threading.Thread(target=run, args=(i,)) for i in range(anzahl)]
		for t in threads:
			t.start()
		for t in threads:
			t.join(timeout=180)
	if fehler:
		raise fehler[0]
	return antworten
