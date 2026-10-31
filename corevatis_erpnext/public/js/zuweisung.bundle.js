// Corevatis – Zuweisung Mitarbeiter ↔ Arbeitspaket (Aufgabe 5.2)
// Server: corevatis_erpnext.planung.zuweisung (Büro/Meister). Zuweisung = ToDo auf die Task.
// Genutzt vom Wochenplan (Report, frappe.require) und vom Task-Formular (Abschnitt „Zuweisung“).
frappe.provide("corevatis_erpnext.planung");

(function () {
	// einmal laden (Formular-Skript und frappe.require im Wochenplan)
	if (corevatis_erpnext.planung.zuweisung_dialog) {
		return;
	}
	const METHODE = "corevatis_erpnext.planung.zuweisung.";
	const ROLLEN = ["Büro", "Meister", "System Manager", "Administrator"];
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));

	corevatis_erpnext.planung.darf_planen = () => ROLLEN.some((r) => frappe.user.has_role(r));

	function hinweise(liste) {
		(liste || []).filter(Boolean).forEach((h) => frappe.show_alert({ message: esc(h), indicator: "orange" }, 10));
	}

	function vorgaenger_html(stand) {
		const offen = stand.vorgaenger_offen || [];
		if (!offen.length) {
			return "";
		}
		const zeilen = offen
			.map(
				(v) =>
					`<li>${corevatis_erpnext.beleg_link("Task", v.task)} ${esc(v.titel)} – ${esc(__(v.status))}${
						v.ende ? " " + __("bis") + " " + esc(frappe.datetime.str_to_user(v.ende)) : ""
					}</li>`
			)
			.join("");
		return `<div class="alert alert-warning small cv-vorgaenger">${__("Vorgänger noch nicht fertig")}:<ul class="mb-0">${zeilen}</ul></div>`;
	}

	function zuweisungen_html(stand, mit_entfernen) {
		const liste = stand.zugewiesen || [];
		if (!liste.length) {
			return `<p class="text-muted small">${__("Noch niemand zugewiesen.")}</p>`;
		}
		const zeilen = liste
			.map((z) => {
				const status = z.status === "Open" ? __("offen") : __("erledigt");
				const knopf =
					mit_entfernen && z.status === "Open" && z.employee
						? `<button class="btn btn-xs btn-default cv-entfernen" data-employee="${esc(z.employee)}">${__("Entfernen")}</button>`
						: "";
				return `<tr><td>${esc(z.employee_name)}</td><td>${esc(status)}</td><td>${corevatis_erpnext.beleg_link("ToDo", z.todo)}</td><td class="text-right">${knopf}</td></tr>`;
			})
			.join("");
		return `<table class="table table-sm small mb-2"><thead><tr><th>${__("Mitarbeiter")}</th><th>${__("Status")}</th><th>${__("Aufgabe")}</th><th></th></tr></thead><tbody>${zeilen}</tbody></table>`;
	}

	function laden(task) {
		return frappe.call({ method: METHODE + "zuweisungen", args: { task } }).then((r) => r.message || {});
	}

	Object.assign(corevatis_erpnext.planung, { laden, vorgaenger_html, zuweisungen_html });

	corevatis_erpnext.planung.zuweisen = function (task, employee) {
		return frappe
			.call({ method: METHODE + "zuweisen", args: { task, employee }, freeze: true })
			.then((r) => {
				const m = r.message || {};
				frappe.show_alert({
					message: m.neu ? __("{0} zugewiesen", [esc(m.employee_name)]) : __("Keine Änderung"),
					indicator: m.neu ? "green" : "blue",
				});
				hinweise(m.hinweise);
				return m;
			});
	};

	corevatis_erpnext.planung.entfernen = function (task, employee) {
		return frappe
			.call({ method: METHODE + "zuweisung_entfernen", args: { task, employee }, freeze: true })
			.then((r) => {
				const m = r.message || {};
				frappe.show_alert({
					message: m.entfernt ? __("Zuweisung entfernt") : __("Keine Änderung"),
					indicator: m.entfernt ? "green" : "blue",
				});
				hinweise(m.hinweise);
				return m;
			});
	};

	// Dialog „Mitarbeiter zuweisen“ (Wochenplan): Dropdown der planbaren Mitarbeiter, aktuelle
	// Zuweisungen mit „Entfernen“, Hinweis auf Vorgänger, die noch nicht fertig sind (5.1-Kette)
	corevatis_erpnext.planung.zuweisung_dialog = function (task, nach_aenderung) {
		laden(task).then((stand) => {
			const d = new frappe.ui.Dialog({
				title: __("Mitarbeiter zuweisen: {0}", [esc(stand.titel || task)]),
				fields: [
					{ fieldname: "info", fieldtype: "HTML" },
					{
						fieldname: "employee",
						fieldtype: "Select",
						label: __("Mitarbeiter"),
						options: [{ value: "", label: "" }].concat(stand.mitarbeiter || []),
						reqd: 1,
					},
				],
				primary_action_label: __("Zuweisen"),
				primary_action(werte) {
					corevatis_erpnext.planung.zuweisen(task, werte.employee).then(() => {
						d.hide();
						nach_aenderung && nach_aenderung();
					});
				},
			});
			const zeichnen = (s) => {
				const kopf = `<p class="small">${esc(s.projekt || "")} · ${
					s.start ? esc(frappe.datetime.str_to_user(s.start)) : ""
				} – ${s.ende ? esc(frappe.datetime.str_to_user(s.ende)) : ""}</p>`;
				d.fields_dict.info.$wrapper.html(kopf + vorgaenger_html(s) + zuweisungen_html(s, true));
			};
			zeichnen(stand);
			d.fields_dict.info.$wrapper.on("click", ".cv-entfernen", function () {
				corevatis_erpnext.planung.entfernen(task, $(this).attr("data-employee")).then(() => {
					laden(task).then(zeichnen);
					nach_aenderung && nach_aenderung();
				});
			});
			if (stand.geschlossen) {
				d.set_df_property("employee", "read_only", 1);
				d.get_primary_btn().prop("disabled", true);
			}
			d.show();
		});
	};
})();
