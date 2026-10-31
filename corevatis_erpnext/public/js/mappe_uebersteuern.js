// Corevatis – Dokumentenmappe (Epic D, D.5): Pflichtprüfung an Angebot und Rechnung übersteuern.
// Fehlt ein Pflichtdokument (z. B. das Abnahmeprotokoll vor der Rechnung), lehnt der Server das
// Speichern ab; das Büro übersteuert hier mit Begründung (cv_mappe_uebersteuert, cv_mappe_grund).
// Der Server prüft Rolle und Begründung (corevatis.mappe.pruefung.uebersteuerung_pruefen) und
// protokolliert die Übersteuerung in der Mappenprüfung.
frappe.provide("corevatis_erpnext.mappe");

(function () {
	const ROLLEN = ["Büro", "System Manager"];
	const esc = (s) => frappe.utils.escape_html(s === null || s === undefined ? "" : String(s));

	corevatis_erpnext.mappe.uebersteuern_dialog = function (frm) {
		const d = new frappe.ui.Dialog({
			title: __("Mappenprüfung übersteuern"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "hinweis",
					options: `<p>${esc(
						__(
							"Die Dokumentenmappe ist für diesen Schritt unvollständig. Mit einer Begründung lässt sich der Beleg trotzdem speichern; die Übersteuerung wird mit Ihrem Namen protokolliert."
						)
					)}</p>`,
				},
				{
					fieldtype: "Small Text",
					fieldname: "grund",
					label: __("Begründung"),
					reqd: 1,
					default: frm.doc.cv_mappe_grund || "",
				},
			],
			primary_action_label: __("Übersteuern und speichern"),
			primary_action(werte) {
				d.hide();
				frm.set_value("cv_mappe_uebersteuert", 1);
				frm.set_value("cv_mappe_grund", werte.grund);
				frm.save();
			},
		});
		d.show();
	};

	function knoepfe(frm) {
		if (frm.is_new() || frm.doc.docstatus !== 0 || !frm.fields_dict.cv_mappe_uebersteuert) return;
		if (!frappe.user.has_role(ROLLEN)) return;
		if (frm.doc.cv_mappe_uebersteuert) {
			frm.dashboard.add_comment(
				__("Mappenprüfung übersteuert von {0}: {1}", [
					esc(frm.doc.cv_mappe_uebersteuert_von || ""),
					esc(frm.doc.cv_mappe_grund || ""),
				]),
				"orange",
				true
			);
			frm.add_custom_button(
				__("Übersteuerung aufheben"),
				() => {
					frm.set_value("cv_mappe_uebersteuert", 0);
					frm.set_value("cv_mappe_grund", "");
					frm.save();
				},
				__("Mappe")
			);
		} else {
			frm.add_custom_button(__("Mappenprüfung übersteuern …"), () => corevatis_erpnext.mappe.uebersteuern_dialog(frm), __("Mappe"));
		}
	}

	for (const doctype of ["Quotation", "Sales Invoice"]) {
		frappe.ui.form.on(doctype, { refresh: knoepfe });
	}
})();
