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

	const handlers = {};
	for (const [fieldname] of SYMPTOME) {
		handlers[fieldname] = rebuild;
	}
	frappe.ui.form.on("Lead", handlers);
})();
