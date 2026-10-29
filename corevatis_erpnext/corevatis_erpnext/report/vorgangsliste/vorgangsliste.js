// Vorgangsliste (Prozessleiste): offene Vorgänge mit Ampel. Server: vorgangsliste.py
frappe.query_reports["Vorgangsliste"] = {
	filters: [
		{
			fieldname: "schritt",
			label: __("Schritt"),
			fieldtype: "Select",
			options: ["", "Anfrage", "Termin", "Befund/Aufmaß", "Angebot", "Auftrag", "Ausführung", "Abnahme", "Rechnung"],
		},
		{ fieldname: "verantwortlich", label: __("Verantwortlich"), fieldtype: "Link", options: "User" },
		{ fieldname: "ampel", label: __("Ampel"), fieldtype: "Select", options: ["", "rot", "gelb", "grün"] },
		{ fieldname: "nur_offene", label: __("Nur offene Vorgänge"), fieldtype: "Check", default: 1 },
	],
	formatter(value, row, column, data, default_formatter) {
		const formatiert = default_formatter(value, row, column, data);
		if (column.fieldname === "ampel" && data && data.ampel) {
			const farbe = { rot: "red", gelb: "orange", "grün": "green", grau: "gray" }[data.ampel] || "gray";
			return `<span class="indicator-pill ${farbe}" title="${frappe.utils.escape_html(
				data.ampel_text || ""
			)}">${frappe.utils.escape_html(data.ampel)}</span>`;
		}
		return formatiert;
	},
};
