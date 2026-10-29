// Corevatis – Angebot aus dem Aufmaß (Aufgabe 4.1)
// Server: corevatis_erpnext.angebot.generator.erzeuge_angebot (Rollen Büro/Meister)
// Das Formular „Aufmass“ gehört zu corevatis; dieses Skript hängt nur die Schaltflächen an.
(function () {
	const METHODE = "corevatis_erpnext.angebot.generator.erzeuge_angebot";
	const ROLLEN = ["Büro", "Meister", "System Manager", "Administrator"];

	function hinweise(r) {
		const liste = (r.warnungen || []).filter(Boolean);
		if (!liste.length) return;
		frappe.msgprint({
			title: __("Hinweise zum Angebot"),
			indicator: "orange",
			message: liste.map((w) => `<div>${frappe.utils.escape_html(w)}</div>`).join(""),
		});
	}

	function erzeugen(frm, werte, neu_erzeugen) {
		return frappe
			.call({
				method: METHODE,
				args: {
					aufmass: frm.doc.name,
					schema: werte.schema || null,
					erschwernis_prozent: werte.erschwernis_prozent || null,
					neu_erzeugen: neu_erzeugen ? 1 : 0,
				},
				freeze: true,
				freeze_message: __("Angebot wird kalkuliert …"),
			})
			.then((r) => {
				const m = r.message || {};
				if (!m.neu) {
					frappe.confirm(
						__("Zu diesem Aufmaß gibt es schon das Angebot {0}. Den Entwurf mit neuer Kalkulation ersetzen?", [
							m.angebot,
						]),
						() => erzeugen(frm, werte, true),
						() => frappe.set_route("Form", "Quotation", m.angebot)
					);
					return;
				}
				frappe.show_alert({
					message: __("Angebot {0}: {1} Positionen, netto {2}", [
						m.angebot,
						m.positionen,
						format_currency(m.netto),
					]),
					indicator: "green",
				});
				hinweise(m);
				frappe.set_route("Form", "Quotation", m.angebot);
			});
	}

	function dialog(frm) {
		const d = new frappe.ui.Dialog({
			title: __("Angebot erzeugen"),
			fields: [
				{
					fieldtype: "Link",
					fieldname: "schema",
					label: __("Kalkulationsschema"),
					options: "Kalkulationsschema",
					description: __("Leer: Standardschema"),
					get_query: () => ({ filters: { aktiv: 1 } }),
				},
				{
					fieldtype: "Percent",
					fieldname: "erschwernis_prozent",
					label: __("Erschwernis (%)"),
					description: __("Leer: Wert des Schemas"),
				},
			],
			primary_action_label: __("Erzeugen"),
			primary_action(werte) {
				d.hide();
				erzeugen(frm, werte, false);
			},
		});
		d.show();
	}

	frappe.ui.form.on("Aufmass", {
		refresh(frm) {
			if (frm.is_new() || !ROLLEN.some((r) => frappe.user.has_role(r))) return;
			frm.add_custom_button(__("Angebot erzeugen"), () => dialog(frm), __("Erstellen"));
			frappe.db.get_value("Quotation", { cv_aufmass: frm.doc.name, docstatus: ["<", 2] }, "name").then((r) => {
				const name = r && r.message && r.message.name;
				if (name) {
					frm.add_custom_button(__("Angebot {0}", [name]), () => frappe.set_route("Form", "Quotation", name));
				}
			});
		},
	});
})();
