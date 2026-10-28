// Corevatis – Besichtigungstermin am Lead (Aufgabe 1.3)
// Server: corevatis_erpnext.crm.termin (Rollen Büro/Meister)
frappe.provide("corevatis_erpnext.termin");

(function () {
	const API = "corevatis_erpnext.crm.termin.";
	const ROLLEN = ["Büro", "Meister", "System Manager", "Administrator"];

	function textdatei(name, text) {
		const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = name;
		document.body.appendChild(a);
		a.click();
		setTimeout(() => {
			URL.revokeObjectURL(a.href);
			a.remove();
		}, 0);
	}

	corevatis_erpnext.termin.bestaetigung_dialog = function (frm, inhalt, hinweise) {
		const empfaenger = inhalt.empfaenger || frm.doc.email_id;
		const d = new frappe.ui.Dialog({
			title: __("Terminbestätigung"),
			size: "large",
			fields: [
				{ fieldtype: "HTML", fieldname: "hinweise" },
				{ fieldtype: "Data", fieldname: "betreff", label: __("Betreff"), read_only: 1, default: inhalt.betreff },
				{
					fieldtype: "Small Text",
					fieldname: "text",
					label: __("Text zum Kopieren (WhatsApp, SMS, E-Mail)"),
					default: inhalt.text,
				},
				{ fieldtype: "Section Break", label: __("Vorschau"), collapsible: 1 },
				{ fieldtype: "HTML", fieldname: "vorschau" },
			],
			primary_action_label: __("Text kopieren"),
			primary_action() {
				frappe.utils.copy_to_clipboard(d.get_value("text"));
			},
			secondary_action_label: __("Als Textdatei speichern"),
			secondary_action() {
				textdatei(`Terminbestaetigung-${frm.doc.name}.txt`, `${inhalt.betreff}\n\n${d.get_value("text")}`);
			},
		});
		const liste = (hinweise || []).filter(Boolean);
		if (liste.length) {
			d.fields_dict.hinweise.$wrapper.html(
				liste.map((h) => `<div class="alert alert-info small">${frappe.utils.escape_html(h)}</div>`).join("")
			);
		}
		// HTML stammt aus dem Email Template mit serverseitig escapten Werten
		d.fields_dict.vorschau.$wrapper.html(`<div class="small">${inhalt.html || ""}</div>`);
		if (empfaenger) {
			d.add_custom_action(__("In E-Mail-Warteschlange legen"), () => {
				frappe
					.call({ method: API + "bestaetigung_senden", args: { lead: frm.doc.name }, freeze: true })
					.then((r) => {
						frappe.show_alert({
							message: __("In der E-Mail-Warteschlange: {0}", [r.message || "-"]),
							indicator: "green",
						});
						frm.reload_doc();
					});
			});
		}
		d.show();
		return d;
	};

	corevatis_erpnext.termin.anlegen_dialog = async function (frm) {
		const r = await frappe.call({ method: API + "meister_liste" });
		const meister = r.message || [];
		if (!meister.length) {
			frappe.msgprint(__("Es gibt keinen aktiven Benutzer mit der Rolle Meister."));
			return;
		}
		const email = frm.doc.email_id;
		const d = new frappe.ui.Dialog({
			title: __("Besichtigungstermin anlegen"),
			fields: [
				{ fieldtype: "Datetime", fieldname: "beginn", label: __("Datum und Uhrzeit"), reqd: 1 },
				{
					fieldtype: "Select",
					fieldname: "dauer",
					label: __("Dauer"),
					options: [
						{ value: "30", label: __("30 Minuten") },
						{ value: "45", label: __("45 Minuten") },
						{ value: "60", label: __("1 Stunde") },
						{ value: "90", label: __("1,5 Stunden") },
						{ value: "120", label: __("2 Stunden") },
						{ value: "180", label: __("3 Stunden") },
					],
					default: "60",
					reqd: 1,
				},
				{
					fieldtype: "Select",
					fieldname: "meister",
					label: __("Meister"),
					options: meister.map((m) => ({ value: m.name, label: m.full_name || m.name })),
					default: meister.length === 1 ? meister[0].name : (meister.find((m) => m.name === frappe.session.user) || {}).name,
					reqd: 1,
				},
				{ fieldtype: "Small Text", fieldname: "notiz", label: __("Notiz für den Meister (intern)") },
				{
					fieldtype: "Check",
					fieldname: "bestaetigung",
					label: __("Terminbestätigung in die E-Mail-Warteschlange legen"),
					default: email ? 1 : 0,
					read_only: email ? 0 : 1,
					description: email
						? __("an {0} – ohne eingerichtetes E-Mail-Konto wird nichts versendet, der Text steht danach zum Kopieren bereit.", [
								frappe.utils.escape_html(email),
						  ])
						: __("Der Lead hat keine E-Mail-Adresse – die Bestätigung bitte manuell übermitteln."),
				},
			],
			primary_action_label: __("Termin anlegen"),
			primary_action(values) {
				frappe
					.call({
						method: API + "termin_anlegen",
						args: Object.assign({ lead: frm.doc.name }, values),
						freeze: true,
						freeze_message: __("Termin wird angelegt …"),
					})
					.then((res) => {
						const m = res.message || {};
						d.hide();
						frm.reload_doc();
						const hinweise = [__("Termin {0} angelegt.", [m.event])];
						if (m.abgesagt) hinweise.push(__("Vorheriger Termin {0} wurde abgesagt.", [m.abgesagt]));
						if (m.status) hinweise.push(__("Status: {0}", [m.status]));
						if (m.email_queue) hinweise.push(__("Bestätigung in der E-Mail-Warteschlange ({0}).", [m.email_queue]));
						corevatis_erpnext.termin.bestaetigung_dialog(
							frm,
							Object.assign({ empfaenger: m.empfaenger }, m.bestaetigung || {}),
							hinweise.concat(m.warnungen || [])
						);
					});
			},
		});
		d.show();
	};

	corevatis_erpnext.termin.bestaetigung_anzeigen = function (frm) {
		frappe.call({ method: API + "bestaetigung_anzeigen", args: { lead: frm.doc.name } }).then((r) => {
			if (r.message) corevatis_erpnext.termin.bestaetigung_dialog(frm, r.message);
		});
	};

	frappe.ui.form.on("Lead", {
		refresh(frm) {
			if (frm.is_new() || !frappe.user.has_role(ROLLEN)) return;
			frm.add_custom_button(__("Termin anlegen"), () => corevatis_erpnext.termin.anlegen_dialog(frm));
			if (frm.doc.cv_termin) {
				frm.add_custom_button(__("Bestätigung anzeigen/kopieren"), () =>
					corevatis_erpnext.termin.bestaetigung_anzeigen(frm)
				);
			}
		},
	});
})();
