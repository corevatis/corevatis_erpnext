// Zahlungseingang (7.5): Gutschriften des Kontoauszugs mit Zuordnungsvorschlag. Server: zahlungseingang.py;
// Dialoge in public/js/mahnung_zahlung.bundle.js (corevatis_erpnext.abrechnung.kontoauszug_dialog, zuordnen_dialog, ignorieren).
frappe.query_reports["Zahlungseingang"] = {
	filters: [
		{ fieldname: "bankkonto", label: __("Bankkonto"), fieldtype: "Link", options: "Bank Account", get_query: () => ({ filters: { is_company_account: 1 } }) },
		{ fieldname: "mit_ignorierten", label: __("Ignorierte anzeigen"), fieldtype: "Check", default: 0 },
	],
	onload(report) {
		frappe.require("mahnung_zahlung.bundle.js", () => {
			report.page.set_primary_action(__("Kontoauszug einlesen"), () =>
				corevatis_erpnext.abrechnung.kontoauszug_dialog(() => report.refresh())
			);
			report.page.add_inner_button(__("Offene Posten"), () => frappe.set_route("query-report", "Offene Posten"));
		});
	},
	formatter(value, row, column, data, default_formatter) {
		const formatiert = default_formatter(value, row, column, data);
		if (!data) return formatiert;
		const esc = frappe.utils.escape_html;
		if (column.fieldname === "aktion" && data.buchung) {
			const name = encodeURIComponent(data.buchung);
			if (data.ignoriert) {
				return `<button class="btn btn-xs btn-default" onclick="corevatis_erpnext.abrechnung.ignorieren(decodeURIComponent('${name}'), 1, () => frappe.query_report.refresh()); return false;">${esc(__("Wieder öffnen"))}</button>`;
			}
			const klasse = data.rechnung ? "btn-primary" : "btn-default";
			return (
				`<button class="btn btn-xs ${klasse}" onclick="corevatis_erpnext.abrechnung.zuordnen_dialog(decodeURIComponent('${name}'), () => frappe.query_report.refresh()); return false;">${esc(__("Zuordnen"))}</button> ` +
				`<button class="btn btn-xs btn-default" onclick="corevatis_erpnext.abrechnung.ignorieren(decodeURIComponent('${name}'), 0, () => frappe.query_report.refresh()); return false;">${esc(__("Ignorieren"))}</button>`
			);
		}
		if (column.fieldname === "sicherheit" && data.sicherheit) {
			const farbe = { sicher: "green", wahrscheinlich: "blue", "möglich": "orange" }[data.sicherheit] || "gray";
			return `<span class="indicator-pill ${farbe}">${esc(data.sicherheit)}</span>`;
		}
		// Freitext aus dem Kontoauszug (Zahler, Verwendungszweck, Notiz) und Kundennamen nur maskiert -
		// Frappes Data-Formatter der Berichte maskiert nicht (Security-Review)
		if (column.fieldtype === "Data" && typeof value === "string") {
			return esc(value);
		}
		return formatiert;
	},
};
