// Wochenplan (Aufgabe 5.2): Mitarbeiter × Arbeitstage zweier Wochen. Server: wochenplan.py
// Zuweisen per Dialog (public/js/zuweisung.js): Klick auf „Zuweisen …“ in einer Arbeitspaket-Zeile.
(function () {
	const montag = (datum) => {
		const d = frappe.datetime.str_to_obj(datum || frappe.datetime.get_today());
		const versatz = (d.getDay() + 6) % 7;
		return frappe.datetime.add_days(frappe.datetime.obj_to_str(d), -versatz);
	};
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));

	function verschieben(report, tage) {
		const von = report.get_filter_value("von") || montag();
		report.set_filter_value("von", tage === null ? montag() : frappe.datetime.add_days(montag(von), tage));
	}

	frappe.query_reports["Wochenplan"] = {
		filters: [
			{ fieldname: "von", label: __("Woche ab"), fieldtype: "Date", default: montag(), reqd: 1 },
			{ fieldname: "projekt", label: __("Projekt"), fieldtype: "Link", options: "Project" },
			// Select statt Link: Büro/Meister lesen Employee nicht (Site-Template), Optionen vom Server
			{ fieldname: "mitarbeiter", label: __("Mitarbeiter"), fieldtype: "Select", options: [""] },
		],
		onload(report) {
			frappe.require("/assets/corevatis_erpnext/js/zuweisung.js");
			report.page.add_inner_button(__("Vorherige Woche"), () => verschieben(report, -7), __("Woche"));
			report.page.add_inner_button(__("Diese Woche"), () => verschieben(report, null), __("Woche"));
			report.page.add_inner_button(__("Nächste Woche"), () => verschieben(report, 7), __("Woche"));
			frappe.call("corevatis_erpnext.planung.zuweisung.mitarbeiter").then((r) => {
				const feld = report.get_filter("mitarbeiter");
				feld.df.options = [{ value: "", label: "" }].concat(r.message || []);
				feld.refresh();
			});
			$(report.page.main).on("click", ".cv-zuweisen", function (e) {
				e.preventDefault();
				const task = $(this).attr("data-task");
				frappe.require("/assets/corevatis_erpnext/js/zuweisung.js", () =>
					corevatis_erpnext.planung.zuweisung_dialog(task, () => report.refresh())
				);
			});
		},
		formatter(value, row, column, data, default_formatter) {
			if (!data) {
				return default_formatter(value, row, column, data);
			}
			const tag = column.fieldname && column.fieldname.startsWith("d_");
			if ((tag || column.fieldname === "summe") && !value) {
				// Arbeitspaket ohne Sollstunden an seinen Tagen: 0 zeigen, sonst leer
				return tag && data.indent === 1 && value === 0 ? `<span class="text-muted">0</span>` : "";
			}
			if (column.fieldname === "aktion") {
				return data.typ === "task" && data.task
					? `<a href="#" class="cv-zuweisen" data-task="${esc(data.task)}">${__("Zuweisen …")}</a>`
					: "";
			}
			let formatiert = default_formatter(value, row, column, data);
			if (data.indent === 0 && (tag || column.fieldname === "bezeichnung")) {
				formatiert = `<b>${formatiert}</b>`;
			}
			if (tag && (data.ueberlast || []).includes(column.fieldname)) {
				return `<span class="cv-ueberlast" style="color: var(--red-600); font-weight: 700" title="${esc(
					__("Tageslast über der Tagesarbeitszeit")
				)}">${formatiert} !</span>`;
			}
			if (column.fieldname === "bezeichnung" && data.typ === "task" && data.titel) {
				return `<span title="${esc(data.titel)}">${formatiert}</span>`;
			}
			return formatiert;
		},
	};
})();
