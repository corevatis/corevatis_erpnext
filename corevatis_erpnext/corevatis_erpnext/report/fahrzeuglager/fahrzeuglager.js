// Fahrzeuglager (Phase 2, P2.2): Bestand auf dem Fahrzeug eines Monteurs gegen den Bedarf seiner Baustellen;
// Knopf „Fahrzeug beladen“ bucht die Umlagerung aus dem Hauptlager. Server: corevatis_erpnext.materialwirtschaft.
frappe.query_reports["Fahrzeuglager"] = {
	filters: [
		{
			fieldname: "mitarbeiter",
			label: __("Mitarbeiter"),
			fieldtype: "Link",
			options: "Employee",
			get_query: () => ({ filters: { status: "Active" } }),
		},
		{ fieldname: "von", label: __("Baustellen von"), fieldtype: "Date", default: frappe.datetime.get_today() },
		{ fieldname: "bis", label: __("Baustellen bis"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), 7) },
	],
	onload(report) {
		// Dialog aus materialwirtschaft.bundle.js: auf Berichtsseiten nicht vorgeladen, daher nachladen
		report.page.add_inner_button(__("Fahrzeug beladen"), () => {
			const f = report.get_values ? report.get_values() : {};
			frappe.require("materialwirtschaft.bundle.js", () =>
				corevatis_erpnext.materialwirtschaft.beladen_dialog(f.mitarbeiter, f.von, f.bis, () => report.refresh())
			);
		});
		report.page.add_inner_button(__("Nachbestellen"), () => frappe.set_route("query-report", "Nachbestellen"));
	},
	formatter(value, row, column, data, default_formatter) {
		const formatiert = default_formatter(value, row, column, data);
		if (!data) return formatiert;
		if (column.fieldname === "fehlt" && typeof value === "number" && value > 0) {
			return `<span class="text-danger">${formatiert}</span>`;
		}
		if (column.fieldname === "vorschlag" && typeof value === "number" && value > 0) {
			return `<b>${formatiert}</b>`;
		}
		if (column.fieldtype === "Data" && typeof value === "string") {
			return frappe.utils.escape_html(value);
		}
		return formatiert;
	},
};
