// Textbausteine (P2.6, Handwerker#89): Im Feld „Textbaustein“ (tc_name) der Belege nur Bausteine der Art
// Zahlungsbedingungen oder Sonstiges anbieten (nicht Vorbemerkung, Gewährleistung, Mahntext, Anschreiben).
(function () {
	const NICHT = ["Vorbemerkung", "Gewährleistung", "Schlusstext", "Mahntext", "Anschreiben"];
	for (const doctype of ["Quotation", "Sales Order", "Sales Invoice"]) {
		frappe.ui.form.on(doctype, {
			onload(frm) {
				if (!frm.fields_dict.tc_name) return;
				frm.set_query("tc_name", () => ({
					filters: { selling: 1, disabled: 0, cv_art: ["not in", NICHT] },
				}));
			},
		});
	}
})();
