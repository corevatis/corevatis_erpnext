"""Web Form „Kostenlose Einschätzung“ (1.2) auf bestehenden Sites: UTM Source „Website“,
Lead-Dubletten per E-Mail erlauben (einmalig, danach in den CRM-Einstellungen änderbar)."""


def execute():
	from corevatis_erpnext.webform.einschaetzung import setup

	setup()
