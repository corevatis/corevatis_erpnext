// Corevatis – Lead-Formular aufräumen (Prozessleiste, Befunde mvp-test Nr. 21)
// - ERPNext-Menü „Aktion“ (nur „Zu potenziellem Kunden hinzufügen“) und „Erstellen → Potenzieller
//   Kunde“ entfallen: Privatkunden haben keine Prospects, das Workflow-Menü „Aktionen“ bleibt allein.
// - Workflow-Aktion „Termin vereinbaren“ öffnet den Dialog „Termin anlegen“ (1.3) statt nur den
//   Status zu setzen; termin_anlegen schaltet den Workflow danach selbst auf „Termin“.
// Die ausgeblendeten Felder und den eingeklappten Abschnitt setzt prozess.setup (Property Setter).
(function () {
	const AKTION_TERMIN = "Termin vereinbaren";

	function erpnext_menues_entfernen(frm) {
		frm.remove_custom_button(__("Add to Prospect"), __("Action"));
		frm.remove_custom_button(__("Prospect"), __("Create"));
	}

	frappe.ui.form.on("Lead", {
		refresh(frm) {
			// ERPNext legt die Schaltflächen im Controller nach den Form-Handlern an
			setTimeout(() => erpnext_menues_entfernen(frm), 0);
		},
		before_workflow_action(frm) {
			if (frm.selected_workflow_action !== AKTION_TERMIN) return;
			const ns = window.corevatis_erpnext && window.corevatis_erpnext.termin;
			const termin = ns && ns.anlegen_dialog;
			if (typeof termin !== "function") return;
			frappe.dom.unfreeze();
			frm.selected_workflow_action = null;
			termin(frm);
			// Promise bleibt offen: Frappes apply_workflow läuft nicht, den Übergang macht termin_anlegen
			return new Promise(() => {});
		},
	});
})();
