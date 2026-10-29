// Corevatis – Foto-Galerie am Befund (Aufgabe 3.7): Fotos der App (Tabelle „Fotos“ und
// Anhänge) als Thumbnails mit Vollbild, Funktion aus galerie.js (1.5). Befund kommt aus
// corevatis; ohne Kern lädt diese Datei nie. Kein HTML-Feld am Befund → Abschnitt im Dashboard.
frappe.ui.form.on("Befund", {
	refresh(frm) {
		corevatis_erpnext.galerie_einhaengen(frm, { fieldname: "cv_galerie", label: __("Fotos") });
	},
	after_save(frm) {
		frm.__cv_galerie_neu && frm.__cv_galerie_neu();
	},
});
