// Stundenkonto (Phase 2 P2.5): Ansicht „Mitarbeiter“ (eine Zeile je Mitarbeiter) und „Tage“ (Monatsübersicht).
// Klick auf „Tage“ in einer Zeile wechselt die Ansicht für diesen Mitarbeiter.
frappe.query_reports["Stundenkonto"] = {
	filters: [
		{ fieldname: "monat", label: __("Monat (beliebiger Tag)"), fieldtype: "Date", default: frappe.datetime.month_start(), reqd: 1 },
		{ fieldname: "mitarbeiter", label: __("Mitarbeiter"), fieldtype: "Link", options: "Employee" },
		{
			fieldname: "ansicht",
			label: __("Ansicht"),
			fieldtype: "Select",
			options: ["Mitarbeiter", "Tage"],
			default: "Mitarbeiter",
		},
	],
	onload(report) {
		// Link „Tage“ je Zeile: einmal delegiert gebunden (kein Inline-onclick mit Mitarbeiternamen)
		report.page.wrapper.on("click", "a.cv-sk-tage", (ev) => {
			ev.preventDefault();
			frappe.query_report.set_filter_value({ mitarbeiter: ev.currentTarget.dataset.mitarbeiter, ansicht: "Tage" });
		});
	},
	formatter(value, row, column, data, default_formatter) {
		const formatiert = default_formatter(value, row, column, data);
		if (!data) return formatiert;
		const esc = frappe.utils.escape_html;
		if (column.fieldname === "aktion" && data.mitarbeiter) {
			return `<a href="#" class="cv-sk-tage" data-mitarbeiter="${esc(data.mitarbeiter)}">${esc(__("Tage"))}</a>`;
		}
		if (["saldo", "saldo_jahr", "laufend"].includes(column.fieldname) && typeof value === "number" && value !== 0) {
			return `<span class="${value < 0 ? "text-danger" : "text-success"}">${formatiert}</span>`;
		}
		if (column.fieldname === "urlaub_rest" && typeof value === "number" && value < 0) {
			return `<span class="text-danger">${formatiert}</span>`;
		}
		if (column.fieldname === "wochentag" && (data.wochenende || data.frei)) {
			return `<span class="text-muted">${esc(String(value || ""))}</span>`;
		}
		if (column.fieldtype === "Data" && typeof value === "string") {
			return esc(value);
		}
		return formatiert;
	},
};
