// Textbausteine (P2.6, Handwerker#89): Platzhalter-Hilfe und Vorschau am Formular „Terms and Conditions“.
// Server: corevatis_erpnext.textbausteine (platzhalter_katalog, vorschau). Über doctype_js eingebunden.
frappe.ui.form.on("Terms and Conditions", {
	refresh(frm) {
		const esc = frappe.utils.escape_html;
		const hilfe = frm.fields_dict.cv_platzhalter_hilfe;
		if (hilfe) {
			frappe.xcall("corevatis_erpnext.textbausteine.platzhalter_katalog").then((liste) => {
				const zeilen = liste
					.map((p) => `<tr><td style="white-space:nowrap"><code>[[${esc(p.name)}]]</code></td><td>${esc(p.beschreibung)}</td></tr>`)
					.join("");
				hilfe.$wrapper.html(
					`<details class="cv-tb-hilfe"><summary class="text-muted small">${esc(__("Platzhalter"))} (${liste.length}): ${esc(
						__("im Text als [[Name]] schreiben, der Beleg setzt die Werte ein")
					)}</summary><table class="table table-sm table-borderless small mt-2"><tbody>${zeilen}</tbody></table></details>`
				);
			});
		}
		if (frm.is_new()) return;
		frm.add_custom_button(__("Vorschau"), () => {
			frappe.xcall("corevatis_erpnext.textbausteine.vorschau", { name: frm.doc.name }).then((r) => {
				const d = new frappe.ui.Dialog({ title: __("Vorschau: {0}", [frm.doc.title]), size: "large" });
				const beleg = r.beleg ? __("Beispiel: {0}", [esc(r.beleg)]) : __("Kein Beispielbeleg gefunden, Platzhalter bleiben leer.");
				const werte = (r.werte || [])
					.map((w) => `<tr><td><code>[[${esc(w.name)}]]</code></td><td>${esc(w.wert || "")}</td></tr>`)
					.join("");
				d.$body.html(
					`<p class="text-muted small">${beleg}</p><div class="cv-tb-vorschau border rounded p-3">${r.html || ""}</div>` +
						(werte ? `<table class="table table-sm small mt-3"><tbody>${werte}</tbody></table>` : "")
				);
				d.show();
			});
		});
	},
});
