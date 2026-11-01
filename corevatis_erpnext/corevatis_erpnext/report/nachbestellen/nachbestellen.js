// Nachbestellen (Phase 2, P2.2): Artikel unter Mindestbestand je Lager; Knopf „Bedarf anlegen“ legt
// Materialanforderungen an (Bestellung ins Lager oder Umlagerung aus dem Hauptlager), „Mindestbestand
// setzen“ pflegt den Mindestbestand je Artikel und Lager. Server: corevatis_erpnext.materialwirtschaft.
frappe.query_reports["Nachbestellen"] = {
	filters: [
		{ fieldname: "firma", label: __("Firma"), fieldtype: "Link", options: "Company" },
		{ fieldname: "lager", label: __("Lager"), fieldtype: "Link", options: "Warehouse", get_query: () => ({ filters: { is_group: 0 } }) },
		{ fieldname: "nur_unter", label: __("Nur unter Mindestbestand"), fieldtype: "Check", default: 1 },
	],
	onload(report) {
		// Dialoge aus materialwirtschaft.bundle.js: auf Berichtsseiten nicht vorgeladen, daher nachladen
		report.page.add_inner_button(__("Bedarf anlegen"), () =>
			frappe.require("materialwirtschaft.bundle.js", () => corevatis_erpnext.materialwirtschaft.nachbestellen_dialog(report))
		);
		report.page.add_inner_button(__("Mindestbestand setzen"), () =>
			frappe.require("materialwirtschaft.bundle.js", () =>
				corevatis_erpnext.materialwirtschaft.mindestbestand_dialog(() => report.refresh())
			)
		);
		report.page.add_inner_button(__("Fahrzeuglager"), () => frappe.set_route("query-report", "Fahrzeuglager"));
	},
	formatter(value, row, column, data, default_formatter) {
		const formatiert = default_formatter(value, row, column, data);
		if (!data) return formatiert;
		if (column.fieldname === "verfuegbar" && typeof value === "number" && data.unter) {
			return `<span class="text-danger">${formatiert}</span>`;
		}
		if (column.fieldname === "nachbestellmenge" && typeof value === "number" && value > 0) {
			return `<b>${formatiert}</b>`;
		}
		if (column.fieldtype === "Data" && typeof value === "string") {
			return frappe.utils.escape_html(value);
		}
		return formatiert;
	},
};
