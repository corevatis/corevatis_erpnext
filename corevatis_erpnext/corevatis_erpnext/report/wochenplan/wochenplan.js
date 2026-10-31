// Wochenplan (Aufgabe 5.2) ist in der Plantafel (Phase 1 P1.4) aufgegangen: eine Seite statt zwei.
// Der Report bleibt als Daten-/REST-Quelle (wochenplan.py → planung.wochenplan.plan) und als Ziel
// alter Links („Wochenplan öffnen“ am Projekt, Prozessleiste); im Desk leitet er mit seinen Filtern
// (Woche ab, Projekt, Mitarbeiter) auf die Seite „plantafel“ um.
(function () {
	const montag = (datum) => {
		const d = frappe.datetime.str_to_obj(datum || frappe.datetime.get_today());
		const versatz = (d.getDay() + 6) % 7;
		return frappe.datetime.add_days(frappe.datetime.obj_to_str(d), -versatz);
	};

	frappe.query_reports["Wochenplan"] = {
		filters: [
			{ fieldname: "von", label: __("Woche ab"), fieldtype: "Date", default: montag(), reqd: 1 },
			{ fieldname: "projekt", label: __("Projekt"), fieldtype: "Link", options: "Project" },
			{ fieldname: "mitarbeiter", label: __("Mitarbeiter"), fieldtype: "Data" },
		],
		onload(report) {
			const o = Object.assign({}, frappe.route_options || {});
			frappe.route_options = null;
			const wert = (name) => {
				try {
					return report.get_filter_value(name, false);
				} catch (e) {
					return null;
				}
			};
			// der Report selbst rendert nichts mehr (sonst verbraucht refresh() die Routenoptionen)
			report.refresh = () => Promise.resolve();
			frappe.set_route("plantafel", {
				von: montag(o.von || wert("von") || frappe.datetime.get_today()),
				projekt: o.projekt || wert("projekt") || "",
				mitarbeiter: o.mitarbeiter || wert("mitarbeiter") || "",
				ansicht: "woche",
			});
		},
	};
})();
