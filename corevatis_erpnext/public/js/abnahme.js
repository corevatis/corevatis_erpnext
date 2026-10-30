// Corevatis ERPNext – Abnahmeprotokoll (6.1): „Mangel-Aufgaben erzeugen“ holt fehlende Tasks nach
// (z. B. nach einem Fehler beim Abschluss); Sprung zu den Tasks des Protokolls.
frappe.ui.form.on("Abnahmeprotokoll", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Abgeschlossen") return;
		const maengel = frm.doc.maengel || [];
		if (maengel.some((m) => m.status !== "Behoben" && !m.task) && frappe.user.has_role(["Büro", "Meister", "System Manager"])) {
			frm.add_custom_button(__("Mangel-Aufgaben erzeugen"), () =>
				frappe
					.call({
						method: "corevatis_erpnext.abnahme.maengel_tasks_erzeugen",
						args: { protokoll: frm.doc.name },
						freeze: true,
					})
					.then((r) => {
						const tasks = (r.message && r.message.tasks) || [];
						frappe.show_alert({ message: __("{0} Mangel-Aufgabe(n)", [tasks.length]), indicator: "green" });
						frm.reload_doc();
					})
			);
		}
		if (maengel.some((m) => m.task)) {
			frm.add_custom_button(__("Mangel-Aufgaben"), () =>
				frappe.set_route("List", "Task", { cv_abnahmeprotokoll: frm.doc.name })
			);
		}
	},
});
