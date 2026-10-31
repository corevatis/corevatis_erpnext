// Corevatis – Task-Formular: Abschnitt „Zuweisung“ (Aufgabe 5.2)
// Dropdown der planbaren Mitarbeiter, Zuweisungen (ToDos) mit „Entfernen“, Hinweis auf Vorgänger.
// Bibliothek: public/js/zuweisung.bundle.js (vorher geladen, doctype_js)
(function () {
	const p = corevatis_erpnext.planung;

	// Task-Formular: Abschnitt „Zuweisung“ (HTML-Feld cv_zuweisung) mit Dropdown und ToDos
	function formular(frm) {
		const feld = frm.fields_dict.cv_zuweisung;
		if (!feld) {
			return;
		}
		if (frm.is_new() || !p.darf_planen()) {
			feld.$wrapper.empty();
			return;
		}
		p.laden(frm.doc.name).then((stand) => {
			const $w = feld.$wrapper.empty();
			$w.append(p.vorgaenger_html(stand) + p.zuweisungen_html(stand, true));
			if (!stand.geschlossen) {
				const $zeile = $(`<div class="d-flex align-items-end" style="gap: var(--margin-sm)"><div class="cv-auswahl" style="min-width: 260px"></div></div>`).appendTo($w);
				const auswahl = frappe.ui.form.make_control({
					parent: $zeile.find(".cv-auswahl"),
					df: {
						fieldname: "cv_mitarbeiter_auswahl",
						fieldtype: "Select",
						label: __("Mitarbeiter zuweisen"),
						options: [{ value: "", label: "" }].concat(stand.mitarbeiter || []),
					},
					render_input: true,
				});
				$(`<button class="btn btn-sm btn-primary mb-3">${__("Zuweisen")}</button>`)
					.appendTo($zeile)
					.on("click", () => {
						const employee = auswahl.get_value();
						if (!employee) {
							frappe.show_alert({ message: __("Bitte einen Mitarbeiter wählen."), indicator: "orange" });
							return;
						}
						p.zuweisen(frm.doc.name, employee).then(() => frm.reload_doc());
					});
			}
			$w.off("click.cv").on("click.cv", ".cv-entfernen", function () {
				p.entfernen(frm.doc.name, $(this).attr("data-employee")).then(() => frm.reload_doc());
			});
		});
	}

	frappe.ui.form.on("Task", {
		refresh: formular,
	});
})();
