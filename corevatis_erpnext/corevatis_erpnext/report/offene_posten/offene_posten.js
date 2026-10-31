// Offene Posten (7.5): offene Rechnungen mit Mahnstufe und Mahnvorschlag. Server: offene_posten.py;
// „Mahnen“ je Zeile über corevatis_erpnext.abrechnung.mahnen_dialog (public/js/mahnung_zahlung.js).
frappe.query_reports["Offene Posten"] = {
	filters: [
		{ fieldname: "kunde", label: __("Kunde"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "nur_ueberfaellig", label: __("Nur überfällige"), fieldtype: "Check", default: 0 },
		{ fieldname: "nur_mahnbar", label: __("Nur mit Mahnvorschlag"), fieldtype: "Check", default: 0 },
	],
	onload(report) {
		frappe.require("/assets/corevatis_erpnext/js/mahnung_zahlung.js", () => {
			report.page.add_inner_button(__("Zahlungseingang"), () => frappe.set_route("query-report", "Zahlungseingang"));
		});
	},
	formatter(value, row, column, data, default_formatter) {
		const formatiert = default_formatter(value, row, column, data);
		if (!data) return formatiert;
		const esc = frappe.utils.escape_html;
		if (column.fieldname === "aktion" && data.rechnung) {
			const name = encodeURIComponent(data.rechnung);
			const label = data.vorschlag ? __("Mahnen") : __("Mahnen …");
			const klasse = data.vorschlag ? "btn-primary" : "btn-default";
			return `<button class="btn btn-xs ${klasse}" onclick="corevatis_erpnext.abrechnung.mahnen_dialog(decodeURIComponent('${name}'), () => frappe.query_report.refresh()); return false;">${esc(label)}</button>`;
		}
		if (column.fieldname === "tage_ueberfaellig" && data.tage_ueberfaellig > 0) {
			return `<span class="text-danger">${esc(String(data.tage_ueberfaellig))}</span>`;
		}
		if (column.fieldname === "offen" && data.teilbezahlt) {
			return `${formatiert} <span class="indicator-pill orange small">${esc(__("teilbezahlt"))}</span>`;
		}
		// Kundennamen und Hinweise nur maskiert (Security-Review, wie Zahlungseingang)
		if (column.fieldtype === "Data" && typeof value === "string") {
			return esc(value);
		}
		return formatiert;
	},
};
