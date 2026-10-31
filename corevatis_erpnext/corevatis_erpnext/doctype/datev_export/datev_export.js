// DATEV-Export (7.4): Zeitraum vorbelegen (Vormonat), „Export erstellen“, Dateien herunterladen
frappe.ui.form.on("DATEV Export", {
	onload(frm) {
		if (!frm.is_new()) return;
		if (!frm.doc.firma) {
			const firma =
				frappe.defaults.get_user_default("Company") || frappe.defaults.get_global_default("company");
			if (firma) frm.set_value("firma", firma);
		}
		if (!frm.doc.von) zeitraum_setzen(frm, 1);
	},

	refresh(frm) {
		frm.add_custom_button(__("Vormonat"), () => zeitraum_setzen(frm, 1), __("Zeitraum"));
		frm.add_custom_button(__("Laufender Monat"), () => zeitraum_setzen(frm, 0), __("Zeitraum"));
		frm.add_custom_button(
			__("Vorletzter Monat"),
			() => zeitraum_setzen(frm, 2),
			__("Zeitraum")
		);

		if (frm.is_new()) {
			frm.set_intro(
				__("Firma und Zeitraum wählen, speichern - dann oben „Export erstellen“. Der Export enthält Rechnungen, Gutschriften und Zahlungen des Zeitraums."),
				"blue"
			);
			return;
		}
		if (frm.doc.status === "Erstellt") {
			frm.set_intro(
				__("Export erstellt am {0}. Die drei Dateien unten dem Steuerberater übergeben (Buchungsstapel in DATEV importieren, Belegbilder zu den Buchungen). Für eine Wiederholung einen neuen Export anlegen.", [
					frappe.datetime.str_to_user(frm.doc.erstellt_am),
				]),
				"green"
			);
			dateien_anbieten(frm);
			return;
		}
		if (frm.doc.status === "Fehlgeschlagen") {
			frm.set_intro(
				__("Die Formatprüfung hat Fehler gemeldet (siehe Protokoll). Ursache beheben und noch einmal „Export erstellen“."),
				"red"
			);
		} else {
			frm.set_intro(__("Zeitraum und Einstellungen prüfen, dann „Export erstellen“."), "blue");
		}
		frm.page.set_primary_action(__("Export erstellen"), () => export_erstellen(frm));
	},
});

function zeitraum_setzen(frm, monate_zurueck) {
	if (frm.doc.status === "Erstellt") {
		frappe.show_alert({ message: __("Dieser Export ist schon erstellt - neuen Export anlegen."), indicator: "orange" });
		return;
	}
	const m = moment().subtract(monate_zurueck, "month");
	frm.set_value("von", m.clone().startOf("month").format("YYYY-MM-DD"));
	frm.set_value("bis", m.clone().endOf("month").format("YYYY-MM-DD"));
}

function dateien_anbieten(frm) {
	const dateien = [
		["datei_buchungsstapel", __("Buchungsstapel (CSV)")],
		["datei_debitoren", __("Debitoren (CSV)")],
		["datei_belegbilder", __("Belegbilder (ZIP)")],
	];
	for (const [feld, label] of dateien) {
		if (frm.doc[feld]) {
			frm.add_custom_button(label, () => window.open(frm.doc[feld]), __("Herunterladen"));
		}
	}
}

function export_erstellen(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Bitte zuerst speichern."));
		return;
	}
	frappe.call({
		method: "corevatis_erpnext.datev.export_erstellen",
		args: { name: frm.doc.name },
		freeze: true,
		freeze_message: __("DATEV-Export wird erstellt (Buchungsstapel, Debitoren, Belegbilder) …"),
		callback(r) {
			frm.reload_doc();
			if (!r.message) return;
			const ok = r.message.ok;
			frappe.msgprint({
				title: ok ? __("DATEV-Export erstellt") : __("DATEV-Export fehlgeschlagen"),
				indicator: ok ? "green" : "red",
				message: `<pre style="white-space: pre-wrap">${frappe.utils.escape_html(r.message.zusammenfassung || "")}</pre>`,
			});
		},
	});
}
