frappe.listview_settings["DATEV Export"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		const farben = { Entwurf: "orange", Erstellt: "green", Fehlgeschlagen: "red" };
		return [__(doc.status), farben[doc.status] || "gray", "status,=," + doc.status];
	},
};
