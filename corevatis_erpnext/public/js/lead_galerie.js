// Corevatis – Foto-Galerie am Lead (Aufgabe 1.5), Funktion aus galerie.js
frappe.ui.form.on("Lead", {
	refresh(frm) {
		corevatis_erpnext.galerie_einhaengen(frm, { fieldname: "cv_galerie", label: __("Fotos") });
	},
});
