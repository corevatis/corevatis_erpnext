// Abschlagsplan (7.1): Listen- und Formularhilfen; „Abschlag stellen“ kommt aus public/js/abrechnung.js
frappe.ui.form.on("Abschlagsplan", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.auftrag) {
			frm.add_custom_button(__("Auftrag"), () => frappe.set_route("Form", "Sales Order", frm.doc.auftrag), __("Öffnen"));
		}
		if (!frm.is_new() && frm.doc.projekt) {
			frm.add_custom_button(__("Projekt"), () => frappe.set_route("Form", "Project", frm.doc.projekt), __("Öffnen"));
		}
	},
});
