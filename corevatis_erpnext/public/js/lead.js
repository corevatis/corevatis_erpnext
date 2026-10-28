// Lead (Aufgabe 1.1): Symptom-Checkboxen schreiben live in cv_symptome (ein Symptom
// je Zeile, Freitext bleibt erhalten). Serverseitig gleicht
// corevatis_erpnext.anfrage.lead.sync_symptome dieselbe Logik ab.
(() => {
	const SYMPTOME = [
		["cv_symptom_feuchte_waende", "feuchte Wände"],
		["cv_symptom_schimmel", "Schimmel"],
		["cv_symptom_salzausbluehungen", "Salzausblühungen"],
		["cv_symptom_wasser_nach_regen", "Wasser nach Regen"],
		["cv_symptom_geruch", "Geruch"],
		["cv_symptom_sonstiges", "Sonstiges"],
	];
	const KNOWN = new Set(SYMPTOME.map(([, value]) => value.toLocaleLowerCase()));

	function rebuild(frm) {
		const extra = (frm.doc.cv_symptome || "")
			.split(/[\n,;]+/)
			.map((line) => line.trim())
			.filter((line) => line && !KNOWN.has(line.toLocaleLowerCase()));
		const lines = SYMPTOME.filter(([fieldname]) => frm.doc[fieldname]).map(([, value]) => value);
		const text = [...lines, ...new Set(extra)].join("\n");
		if ((frm.doc.cv_symptome || "") !== text) {
			frm.set_value("cv_symptome", text);
		}
	}

	// 1.4: „Als verloren markieren“ fragt den Verlustgrund ab, bevor der Workflow
	// weiterschaltet (serverseitig Pflicht: anfrage.lead.validate_verlustgrund).
	const ACTION_VERLOREN = "Als verloren markieren";

	function ask_verlustgrund(frm) {
		if (frm.selected_workflow_action !== ACTION_VERLOREN || frm.doc.cv_verlustgrund) {
			return;
		}
		const options = frappe.meta.get_docfield("Lead", "cv_verlustgrund", frm.doc.name).options;
		// Workflow friert die Oberfläche vor diesem Trigger ein; für den Dialog lösen
		frappe.dom.unfreeze();
		return new Promise((resolve) => {
			let submitted = false;
			const dialog = frappe.prompt(
				[
					{
						fieldname: "cv_verlustgrund",
						fieldtype: "Select",
						label: __("Loss Reason"),
						options: options,
						reqd: 1,
					},
				],
				(values) => {
					submitted = true;
					frappe.dom.freeze();
					frappe.db
						.set_value("Lead", frm.doc.name, "cv_verlustgrund", values.cv_verlustgrund)
						.then(() => resolve())
						.catch(() => frappe.dom.unfreeze());
				},
				__("Loss Reason"),
				__("Mark as lost")
			);
			// Abbrechen: Aktion verwerfen (Promise bleibt offen, nichts wird gespeichert)
			dialog.onhide = () => {
				if (!submitted) {
					frm.selected_workflow_action = null;
				}
			};
		});
	}

	const handlers = { before_workflow_action: ask_verlustgrund };
	for (const [fieldname] of SYMPTOME) {
		handlers[fieldname] = rebuild;
	}
	frappe.ui.form.on("Lead", handlers);
})();
