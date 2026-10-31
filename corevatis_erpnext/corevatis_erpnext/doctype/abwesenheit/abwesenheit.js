// Abwesenheit (P1.4, P2.5): bei Urlaub den Resturlaub des Jahres vor und nach dem Eintrag zeigen.
frappe.ui.form.on("Abwesenheit", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.art === undefined) {
			// Feldrecht Stufe 1: Art und Notiz sieht nur das Büro; der Meister sieht „Abwesend“
			frm.set_intro(__("Abwesend. Art und Notiz der Abwesenheit sieht nur das Büro."), "blue");
			return;
		}
		frm.trigger("urlaub_hinweis");
	},
	mitarbeiter(frm) {
		frm.trigger("urlaub_hinweis");
	},
	art(frm) {
		frm.trigger("urlaub_hinweis");
	},
	von(frm) {
		frm.trigger("urlaub_hinweis");
	},
	bis(frm) {
		frm.trigger("urlaub_hinweis");
	},
	ganztags(frm) {
		frm.trigger("urlaub_hinweis");
	},
	von_uhrzeit(frm) {
		frm.trigger("urlaub_hinweis");
	},
	bis_uhrzeit(frm) {
		frm.trigger("urlaub_hinweis");
	},
	urlaub_hinweis(frm) {
		const d = frm.doc;
		if (d.art !== "Urlaub" || !d.mitarbeiter || !d.von) {
			frm.set_intro("");
			return;
		}
		// Antworten können sich überholen (von, dann bis): nur die letzte Anfrage darf den Hinweis setzen
		const lauf = (frm.cv_hinweis_lauf = (frm.cv_hinweis_lauf || 0) + 1);
		frappe
			.xcall("corevatis_erpnext.mitarbeiter.urlaub_hinweis", {
				mitarbeiter: d.mitarbeiter,
				von: d.von,
				bis: d.bis || d.von,
				ganztags: d.ganztags ? 1 : 0,
				von_uhrzeit: d.von_uhrzeit,
				bis_uhrzeit: d.bis_uhrzeit,
				name: d.__islocal ? null : d.name,
			})
			.then((k) => {
				if (lauf !== frm.cv_hinweis_lauf) return;
				if (!k || k.anspruch === undefined) return;
				const z = (n) => frappe.format(n, { fieldtype: "Float", precision: 1 });
				const text = __("Urlaub {0}: Anspruch {1} Tage, genommen {2}, geplant {3}, Rest {4}; dieser Eintrag {5} Tage, danach {6}.", [
					k.jahr,
					z(k.anspruch + k.uebertrag),
					z(k.genommen),
					z(k.geplant),
					z(k.rest),
					z(k.tage_eintrag),
					z(k.rest_nachher),
				]);
				frm.set_intro(text, k.rest_nachher < 0 ? "orange" : "blue");
			})
			.catch(() => frm.set_intro(""));
	},
});
