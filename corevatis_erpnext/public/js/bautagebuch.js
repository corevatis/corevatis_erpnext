// Corevatis – Bautagebuch am Projekt (Phase 3 P3.2): Knopf „Bautagebuch“ mit Dialog Zeitraum →
// erzeugen/aktualisieren (Kern corevatis.bautagebuch.erzeugen) und danach die Liste „Bautagebuch Tag“
// gefiltert aufs Projekt; „PDF in die Mappe“ für den Zeitraum. Ohne Kern (kein Doctype) kein Knopf;
// Rechte und Eingaben prüft der Server, der Knopf erscheint nur für Büro, Meister und System Manager.
frappe.provide("corevatis_erpnext.bautagebuch");

(function () {
	const B = corevatis_erpnext.bautagebuch;
	const DOCTYPE = "Bautagebuch Tag";
	const KERN = "corevatis.bautagebuch.";
	const ROLLEN = ["Büro", "Meister", "System Manager"];
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));

	B.darf = () => ROLLEN.some((r) => frappe.user.has_role(r));

	B.liste = function (projekt) {
		frappe.route_options = { projekt: projekt };
		frappe.set_route("List", DOCTYPE);
	};

	B.dialog = function (frm) {
		const projekt = frm.doc.name;
		const d = new frappe.ui.Dialog({
			title: __("Bautagebuch"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p class="text-muted small">${esc(
						__("Je Tag mit Tagesrapport, Zeitbuchung oder Meldung entsteht ein Eintrag mit Wetter, Anwesenheit, Leistungen und Vorkommnissen. Ohne Beginn ab dem ersten Rapport.")
					)}</p>`,
				},
				{ fieldtype: "Date", fieldname: "von", label: __("Von") },
				{ fieldtype: "Column Break" },
				{ fieldtype: "Date", fieldname: "bis", label: __("Bis"), default: frappe.datetime.get_today() },
			],
			primary_action_label: __("Erzeugen / aktualisieren"),
			primary_action(werte) {
				frappe
					.call({
						method: KERN + "erzeugen",
						args: { projekt: projekt, von: werte.von || null, bis: werte.bis || null },
						freeze: true,
						freeze_message: __("Bautagebuch wird erzeugt …"),
					})
					.then((r) => {
						const e = r && r.message;
						if (!e) return;
						d.hide();
						frappe.show_alert(
							{
								message: __("Bautagebuch: {0} Tage ({1} neu, {2} aktualisiert)", [e.tage, e.angelegt, e.aktualisiert]),
								indicator: "green",
							},
							8
						);
						B.liste(projekt);
					});
			},
			secondary_action_label: __("PDF in die Mappe"),
			secondary_action() {
				const werte = d.get_values(true) || {};
				frappe
					.call({
						method: KERN + "projekt_pdf_in_mappe",
						args: { projekt: projekt, von: werte.von || null, bis: werte.bis || null },
						freeze: true,
						freeze_message: __("PDF wird erstellt …"),
					})
					.then((r) => {
						const p = r && r.message;
						if (!p) return;
						d.hide();
						frappe.show_alert(
							{
								message: __("PDF liegt in der Mappe: {0} ({1}, Version {2})", [esc(p.datei), esc(p.ordner || "–"), p.version]),
								indicator: "green",
							},
							8
						);
						frm.reload_doc();
					});
			},
		});
		d.show();
	};

	frappe.ui.form.on("Project", {
		refresh(frm) {
			if (frm.is_new() || !B.darf() || !frappe.model.can_read(DOCTYPE)) return;
			frm.add_custom_button(__("Bautagebuch"), () => B.dialog(frm));
		},
	});
})();
