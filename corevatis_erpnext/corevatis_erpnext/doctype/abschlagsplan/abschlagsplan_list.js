frappe.listview_settings["Abschlagsplan"] = {
	add_fields: ["status", "gestellt_prozent"],
	get_indicator(doc) {
		const farbe = { Offen: "orange", "Teilweise gestellt": "blue", "Vollständig gestellt": "green", Schlussrechnung: "green" }[doc.status] || "gray";
		return [__(doc.status), farbe, "status,=," + doc.status];
	},
};
