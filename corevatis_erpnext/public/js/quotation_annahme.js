// Corevatis – Angebot annehmen/ablehnen (Aufgabe 4.4) und Nachfassen (Aufgabe 4.5)
// Server: corevatis_erpnext.angebot.annahme, corevatis_erpnext.angebot.nachfassen (Rollen Büro/Meister)
frappe.provide("corevatis_erpnext.angebot");

(function () {
	const ANNAHME = "corevatis_erpnext.angebot.annahme.";
	const NACHFASSEN = "corevatis_erpnext.angebot.nachfassen.";
	const ROLLEN = ["Büro", "Meister", "System Manager", "Administrator"];
	const GRUPPE = __("Angebot");
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));

	function link(doctype, name) {
		return name ? `<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}">${esc(name)}</a>` : "-";
	}

	function hinweise(liste) {
		return (liste || [])
			.filter(Boolean)
			.map((h) => `<div class="alert alert-warning small">${esc(h)}</div>`)
			.join("");
	}

	corevatis_erpnext.angebot.annehmen_dialog = function (frm) {
		const d = new frappe.ui.Dialog({
			title: __("Angebot annehmen"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "info",
					options: `<p class="small text-muted">${esc(
						__(
							"Legt den Kunden (aus der Anfrage), den Auftrag (AB-) ohne Eventual- und Alternativpositionen und das Projekt an und setzt die Anfrage auf „Gewonnen“."
						)
					)}</p>`,
				},
				{ fieldtype: "Date", fieldname: "auftragsdatum", label: __("Auftragsdatum / Projektstart"), reqd: 1, default: frappe.datetime.get_today() },
				{
					fieldtype: "Date",
					fieldname: "lieferdatum",
					label: __("Liefertermin / geplantes Projektende"),
					reqd: 1,
					default: frappe.datetime.add_days(frappe.datetime.get_today(), 14),
				},
				{ fieldtype: "Check", fieldname: "einreichen", label: __("Auftrag direkt einreichen"), default: 1 },
			],
			primary_action_label: __("Annehmen"),
			primary_action(values) {
				d.hide();
				frappe
					.call({
						method: ANNAHME + "angebot_annehmen",
						args: { quotation: frm.doc.name, ...values },
						freeze: true,
						freeze_message: __("Auftrag und Projekt werden angelegt …"),
					})
					.then((r) => {
						const m = r.message || {};
						const titel = m.bereits_angenommen ? __("Angebot war bereits angenommen") : __("Angebot angenommen");
						frappe.msgprint({
							title: titel,
							indicator: m.warnungen && m.warnungen.length ? "orange" : "green",
							message:
								hinweise(m.warnungen) +
								`<table class="table table-sm small">
									<tr><td>${esc(__("Kunde"))}</td><td>${link("Customer", m.kunde)}${m.kunde_neu ? " (" + esc(__("neu")) + ")" : ""}</td></tr>
									<tr><td>${esc(__("Auftrag"))}</td><td>${link("Sales Order", m.auftrag)} ${m.auftrag_docstatus === 1 ? esc(__("eingereicht")) : esc(__("Entwurf"))}</td></tr>
									<tr><td>${esc(__("Projekt"))}</td><td>${link("Project", m.projekt)}</td></tr>
									<tr><td>${esc(__("Anfrage"))}</td><td>${link("Lead", m.lead)} ${esc(m.lead_status || "")}</td></tr>
								</table>`,
						});
						frm.reload_doc();
					});
			},
		});
		d.show();
	};

	corevatis_erpnext.angebot.ablehnen_dialog = async function (frm) {
		const r = await frappe.call({ method: ANNAHME + "verlustgruende" });
		const d = new frappe.ui.Dialog({
			title: __("Angebot ablehnen"),
			fields: [
				{ fieldtype: "Select", fieldname: "grund", label: __("Grund"), reqd: 1, options: ["", ...(r.message || [])] },
				{ fieldtype: "Small Text", fieldname: "details", label: __("Details (optional)") },
			],
			primary_action_label: __("Als verloren markieren"),
			primary_action(values) {
				d.hide();
				frappe
					.call({ method: ANNAHME + "angebot_ablehnen", args: { quotation: frm.doc.name, ...values }, freeze: true })
					.then((res) => {
						const m = res.message || {};
						frappe.msgprint({
							title: __("Angebot abgelehnt"),
							indicator: m.warnungen && m.warnungen.length ? "orange" : "red",
							message:
								hinweise(m.warnungen) +
								`<p>${esc(__("Angebot"))} ${esc(m.angebot)}: ${esc(m.status)}<br>${esc(__("Anfrage"))} ${link("Lead", m.lead)}: ${esc(m.lead_status || "-")}</p>`,
						});
						frm.reload_doc();
					});
			},
		});
		d.show();
	};

	corevatis_erpnext.angebot.nachfassen = function (frm) {
		frappe.call({ method: NACHFASSEN + "nachfassen", args: { quotation: frm.doc.name }, freeze: true }).then((r) => {
			const m = r.message || {};
			const d = new frappe.ui.Dialog({
				title: __("Nachfassen: {0}", [frm.doc.name]),
				size: "large",
				fields: [
					{ fieldtype: "HTML", fieldname: "hinweise" },
					{ fieldtype: "Data", fieldname: "betreff", label: __("Betreff"), read_only: 1, default: m.betreff },
					{ fieldtype: "Small Text", fieldname: "text", label: __("Text zum Kopieren (E-Mail, WhatsApp)"), default: m.text },
				],
				primary_action_label: __("Text kopieren"),
				primary_action() {
					frappe.utils.copy_to_clipboard(d.get_value("text"));
				},
			});
			const info = [
				__("Nachfass-Aufgabe ({0}. Nachfassen): {1}", [m.zyklus, (m.todos || []).join(", ")]),
				m.email_queue ? __("Follow-up-Mail in der E-Mail-Warteschlange: {0} (ohne E-Mail-Konto wird nichts versendet)", [m.email_queue]) : "",
			]
				.filter(Boolean)
				.map((h) => `<div class="alert alert-info small">${esc(h)}</div>`)
				.join("");
			d.fields_dict.hinweise.$wrapper.html(info + hinweise(m.warnungen));
			d.show();
			frm.reload_doc();
		});
	};

	frappe.ui.form.on("Quotation", {
		refresh(frm) {
			if (frm.doc.docstatus !== 1 || !frappe.user.has_role(ROLLEN)) return;
			const status = frm.doc.status;
			if (["Open", "Replied", "Partially Ordered", "Ordered", "Expired"].includes(status)) {
				// eigener Button (nicht im Menü), damit „ein Klick“ sichtbar ist
				frm.add_custom_button(__("Angebot annehmen"), () => corevatis_erpnext.angebot.annehmen_dialog(frm));
			}
			if (["Open", "Replied", "Expired"].includes(status)) {
				frm.add_custom_button(__("Angebot ablehnen"), () => corevatis_erpnext.angebot.ablehnen_dialog(frm), GRUPPE);
				frm.add_custom_button(__("Nachfassen"), () => corevatis_erpnext.angebot.nachfassen(frm), GRUPPE);
			}
		},
	});
})();
