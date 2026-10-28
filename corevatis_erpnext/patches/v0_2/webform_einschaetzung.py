"""Web Form „Kostenlose Einschätzung“ (1.2) auf bestehenden Sites: UTM Source „Website“.

Setzte bis 0.2 zusätzlich „Lead-Dubletten per E-Mail erlauben“; das nimmt
``v0_3.lead_dubletten_pruefung`` zurück (Review Epic 1, E-2)."""


def execute():
	from corevatis_erpnext.webform.einschaetzung import setup

	setup()
