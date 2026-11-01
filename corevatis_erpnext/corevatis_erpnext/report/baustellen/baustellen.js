// Büro-Dashboard „Baustellen“ (5.9, Handwerker#43): je laufendem Projekt Soll/Ist, Material, Fortschritt,
// nächster Termin, Meldungen/Mängel und Ampel. Server: baustellen.py (corevatis_erpnext.baustellen.uebersicht).
// Kein Bundle nötig: die Knöpfe wechseln nur die Seite (Berichtsseiten laden kein doctype_js).
(function () {
	// laufende Woche Montag bis Sonntag (wie die Kennzahl „Stunden diese Woche“)
	const heute = frappe.datetime.get_today();
	const wochentag = (frappe.datetime.str_to_obj(heute).getDay() + 6) % 7;
	const woche_von = frappe.datetime.add_days(heute, -wochentag);
	const woche_bis = frappe.datetime.add_days(woche_von, 6);

	frappe.query_reports["Baustellen"] = {
		filters: [
			{ fieldname: "von", label: __("Ist von"), fieldtype: "Date", default: woche_von },
			{ fieldname: "bis", label: __("Ist bis"), fieldtype: "Date", default: woche_bis },
			{ fieldname: "meister", label: __("Meister"), fieldtype: "Link", options: "User" },
			{ fieldname: "status", label: __("Status"), fieldtype: "Select", options: ["", "offen", "in Arbeit"] },
			{ fieldname: "ampel", label: __("Ampel"), fieldtype: "Select", options: ["", "rot oder gelb", "rot"] },
		],
		onload(report) {
			report.page.add_inner_button(__("Nachkalkulation"), () => {
				frappe.route_options = { nur_offene: 1 };
				frappe.set_route("query-report", "Nachkalkulation");
			});
			report.page.add_inner_button(__("Plantafel"), () => frappe.set_route("plantafel"));
		},
		formatter(value, row, column, data, default_formatter) {
			const esc = frappe.utils.escape_html;
			if (!data) return default_formatter(value, row, column, data);
			if (column.fieldname === "ampel") {
				const farbe = { rot: "red", gelb: "orange", "grün": "green" }[data.ampel] || "gray";
				return `<span class="indicator-pill ${farbe}">${esc(__(data.ampel || ""))}</span>`;
			}
			if (column.fieldname === "abweichung" && data.abweichung != null) {
				const klasse = data.abweichung > 15 ? "text-danger" : data.abweichung > 5 ? "text-warning" : "";
				const zeichen = data.abweichung > 0 ? "+" : "";
				return `<span class="${klasse}">${esc(zeichen + String(data.abweichung))} %</span>`;
			}
			if (column.fieldname === "fortschritt" && data.fortschritt != null) {
				return `${esc(String(Math.round(data.fortschritt)))} %`;
			}
			if (column.fieldname === "naechster_termin" && data.ueberfaellig) {
				return `<span class="text-danger">${default_formatter(value, row, column, data)}</span>`;
			}
			if (column.fieldname === "nachkalkulation" && data.projekt) {
				const ziel = `/app/query-report/Nachkalkulation?projekt=${encodeURIComponent(data.projekt)}`;
				return `<a href="${esc(ziel)}">${esc(__("Nachkalkulation"))}</a>`;
			}
			if (column.fieldtype === "Data" && typeof value === "string") {
				return esc(value);
			}
			return default_formatter(value, row, column, data);
		},
	};
})();
