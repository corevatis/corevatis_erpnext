// Nachkalkulation (Phase 2, P2.1): Soll aus der Kalkulation gegen Ist aus Zeiten und Material je Projekt
// und Verfahren. Server: nachkalkulation.py (corevatis_erpnext.nachkalkulation.uebersicht).
frappe.query_reports["Nachkalkulation"] = {
	filters: [
		{ fieldname: "projekt", label: __("Projekt"), fieldtype: "Link", options: "Project" },
		{ fieldname: "firma", label: __("Firma"), fieldtype: "Link", options: "Company" },
		{ fieldname: "von", label: __("Ist von"), fieldtype: "Date" },
		{ fieldname: "bis", label: __("Ist bis"), fieldtype: "Date" },
		{ fieldname: "verfahren", label: __("Verfahren"), fieldtype: "Data" },
		{ fieldname: "nur_offene", label: __("Nur laufende Projekte"), fieldtype: "Check", default: 0 },
	],
	onload(report) {
		const o = frappe.route_options || {};
		if (o.projekt) {
			report.set_filter_value("projekt", o.projekt);
			frappe.route_options = null;
		}
		report.page.add_inner_button(__("Mengenregeln nachjustieren"), () =>
			frappe.set_route("query-report", "Mengenregeln nachjustieren")
		);
	},
	formatter(value, row, column, data, default_formatter) {
		const esc = frappe.utils.escape_html;
		if (!data) return default_formatter(value, row, column, data);
		if (column.fieldname === "ampel") {
			const farbe = { rot: "red", gelb: "orange", "grün": "green", grau: "gray" }[data.ampel] || "gray";
			const text = { rot: __("über Plan"), gelb: __("knapp"), "grün": __("im Plan"), grau: __("ohne Ist") }[data.ampel] || "";
			return `<span class="indicator-pill ${farbe}" title="${esc(text)}">&nbsp;</span>`;
		}
		if (column.fieldname === "bezeichnung") {
			const text = esc(value || "");
			return data.indent ? `<span style="padding-left:18px">${text}</span>` : `<b>${text}</b>`;
		}
		if (column.fieldname === "verbrauch" && data.verbrauch != null) {
			const klasse = data.verbrauch > 115 ? "text-danger" : data.verbrauch > 100 ? "text-warning" : "";
			return `<span class="${klasse}">${esc(frappe.format(value, column, {}, data))}</span>`;
		}
		if (column.fieldname === "marge_ist" && data.marge_ist < 0) {
			return `<span class="text-danger">${esc(frappe.format(value, column, {}, data))}</span>`;
		}
		if (column.fieldtype === "Data" && typeof value === "string") {
			return esc(value);
		}
		return default_formatter(value, row, column, data);
	},
};
