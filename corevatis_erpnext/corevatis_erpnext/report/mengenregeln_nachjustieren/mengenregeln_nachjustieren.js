// Mengenregeln nachjustieren (Phase 2, P2.1, Lernschleife): Angebotsmenge gegen Ist-Menge der Schlussrechnungen
// je Leistung und Mengenregel, Zeitfaktor je Verfahren. Server: mengenregeln_nachjustieren.py.
frappe.query_reports["Mengenregeln nachjustieren"] = {
	filters: [
		{ fieldname: "firma", label: __("Firma"), fieldtype: "Link", options: "Company" },
		{ fieldname: "von", label: __("Schlussrechnungen von"), fieldtype: "Date" },
		{ fieldname: "bis", label: __("Schlussrechnungen bis"), fieldtype: "Date" },
		{ fieldname: "verfahren", label: __("Verfahren"), fieldtype: "Data" },
	],
	onload(report) {
		report.page.add_inner_button(__("Nachkalkulation"), () => frappe.set_route("query-report", "Nachkalkulation"));
	},
	formatter(value, row, column, data, default_formatter) {
		const esc = frappe.utils.escape_html;
		if (!data) return default_formatter(value, row, column, data);
		if (column.fieldname === "vorschlag") {
			const passt = value === __("passt");
			return `<span class="${passt ? "text-muted" : "text-warning"}">${esc(value || "")}</span>`;
		}
		if ((column.fieldname === "mengenfaktor" || column.fieldname === "zeitfaktor") && value != null) {
			const klasse = Math.abs(value - 1) >= 0.05 ? "text-warning" : "";
			return `<span class="${klasse}">${esc(frappe.format(value, column, {}, data))}</span>`;
		}
		if (column.fieldtype === "Data" && typeof value === "string") {
			return esc(value);
		}
		return default_formatter(value, row, column, data);
	},
};
