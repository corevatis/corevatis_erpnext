// Abwesenheit (P1.4, P2.5): Art und Notiz liegen auf Feldrecht Stufe 1 (nur Büro). Der Meister sieht alle
// Einträge des Betriebs, die Art fremder Einträge kommt nicht vom Server - die Liste zeigt „Abwesend“.
frappe.listview_settings["Abwesenheit"] = {
	formatters: {
		art(value) {
			return value ? __(value) : __("Abwesend");
		},
	},
};
