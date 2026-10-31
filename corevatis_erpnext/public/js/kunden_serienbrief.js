// Serienbrief (P2.6, Handwerker#89): Kundenliste → Auswahl → Aktion „Serienbrief“ → Baustein und Betreff →
// ein PDF (Server corevatis_erpnext.textbausteine.serienbrief.erstellen). Über doctype_list_js eingebunden.
(function () {
	frappe.listview_settings["Customer"] = frappe.listview_settings["Customer"] || {};
	const einstellungen = frappe.listview_settings["Customer"];
	const vorher = einstellungen.onload;
	einstellungen.onload = function (listview) {
		if (vorher) vorher(listview);
		if (!frappe.user.has_role(["Büro", "System Manager"])) return;
		listview.page.add_action_item(__("Serienbrief"), () => serienbrief(listview));
	};

	function serienbrief(listview) {
		const kunden = listview.get_checked_items(true);
		if (!kunden.length) {
			frappe.msgprint(__("Bitte zuerst Kunden in der Liste auswählen."));
			return;
		}
		const d = new frappe.ui.Dialog({
			title: __("Serienbrief an {0} Kunden", [kunden.length]),
			fields: [
				{
					fieldname: "baustein",
					fieldtype: "Link",
					label: __("Textbaustein (Anschreiben)"),
					options: "Terms and Conditions",
					reqd: 1,
					get_query: () => ({ filters: { cv_art: "Anschreiben", disabled: 0 } }),
				},
				{ fieldname: "betreff", fieldtype: "Data", label: __("Betreff"), reqd: 1 },
				{
					fieldname: "firma",
					fieldtype: "Link",
					label: __("Firma (Briefkopf)"),
					options: "Company",
					default: frappe.defaults.get_user_default("Company"),
				},
				{
					fieldname: "hinweis",
					fieldtype: "HTML",
					options: `<p class="text-muted small">${frappe.utils.escape_html(
						__("Je Kunde eine Seite mit Briefkopf, Anschrift, Datum, Betreff und Text; Platzhalter wie [[Anrede]] werden je Kunde eingesetzt. Kein E-Mail-Versand.")
					)}</p>`,
				},
			],
			primary_action_label: __("PDF erstellen"),
			primary_action(werte) {
				d.get_primary_btn().prop("disabled", true);
				frappe
					.xcall("corevatis_erpnext.textbausteine.serienbrief.erstellen", {
						kunden,
						baustein: werte.baustein,
						betreff: werte.betreff,
						firma: werte.firma || null,
					})
					.then((r) => {
						d.hide();
						const esc = frappe.utils.escape_html;
						let text = __("{0} Briefe erstellt.", [r.anzahl]);
						if (r.ausgelassen && r.ausgelassen.length) {
							text += " " + __("Ohne Leserecht ausgelassen: {0}", [esc(r.ausgelassen.join(", "))]);
						}
						frappe.msgprint({
							title: __("Serienbrief"),
							indicator: "green",
							message: `${text} <a href="${esc(r.file_url)}" target="_blank" rel="noopener">${esc(__("PDF öffnen"))}</a>`,
						});
						window.open(r.file_url, "_blank");
					})
					.finally(() => d.get_primary_btn().prop("disabled", false));
			},
		});
		d.show();
	}
})();
