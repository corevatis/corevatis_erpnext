// Corevatis – Arbeitspakete und Materialbedarf aus dem Auftrag (Aufgabe 5.1)
// Server: corevatis_erpnext.planung (Rollen Büro/Meister), an Auftrag (eingereicht) und Projekt
frappe.provide("corevatis_erpnext.planung");

(function () {
	const PLANUNG = "corevatis_erpnext.planung.";
	const ROLLEN = ["Büro", "Meister", "System Manager", "Administrator"];
	const GRUPPE = __("Planung");
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));

	function link(doctype, name) {
		return name ? `<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}">${esc(name)}</a>` : "-";
	}

	function hinweise(liste) {
		return (liste || [])
			.filter(Boolean)
			.map((h) => `<div class="alert alert-warning small">${esc(h)}</div>`)
			.join("");
	}

	function zahl(wert) {
		return format_number(wert || 0, null, 2);
	}

	function datum(wert) {
		return wert ? frappe.datetime.str_to_user(wert) : "";
	}

	function ergebnis_arbeitspakete(m) {
		const zeilen = (m.arbeitspakete || [])
			.map(
				(t) =>
					`<tr><td>${link("Task", t.name)}</td><td>${esc(t.titel)}</td><td class="text-right">${zahl(t.sollstunden)}</td><td>${esc(datum(t.start))}</td><td>${esc(datum(t.ende))}</td></tr>`
			)
			.join("");
		const tabelle = zeilen
			? `<table class="table table-sm small"><thead><tr><th>${__("Aufgabe")}</th><th>${__("Arbeitspaket")}</th><th class="text-right">${__("Sollstunden")}</th><th>${__("Beginn")}</th><th>${__("Ende")}</th></tr></thead><tbody>${zeilen}</tbody></table>
			   <p class="small">${__("Summe Sollstunden")}: ${zahl(m.sollstunden)} h · ${__("Projekt")}: ${link("Project", m.projekt)}</p>`
			: "";
		frappe.msgprint({
			title: m.neu ? __("Arbeitspakete erzeugt") : __("Arbeitspakete"),
			indicator: m.neu ? "green" : "orange",
			message: hinweise(m.hinweise) + tabelle,
		});
	}

	function ergebnis_materialbedarf(m) {
		const zeilen = (m.materialien || [])
			.map(
				(z) =>
					`<tr><td>${esc(z.item_code)}</td><td class="text-right">${zahl(z.netto)}</td><td class="text-right">${zahl(z.bedarf)}</td><td class="text-right">${z.bestand == null ? "" : zahl(z.bestand)}</td><td class="text-right">${zahl(z.menge)}</td><td>${esc(z.uom)}</td></tr>`
			)
			.join("");
		const tabelle = zeilen
			? `<table class="table table-sm small"><thead><tr><th>${__("Material")}</th><th class="text-right">${__("aus Stückliste")}</th><th class="text-right">${__("mit Verschnitt")}</th><th class="text-right">${__("Bestand")}</th><th class="text-right">${__("angefordert")}</th><th>${__("Einheit")}</th></tr></thead><tbody>${zeilen}</tbody></table>`
			: "";
		const kopf = m.material_request
			? `<p>${__("Materialanforderung")}: ${link("Material Request", m.material_request)} (${esc(__(m.typ || ""))}, ${esc(m.lager || "")})</p>`
			: "";
		frappe.msgprint({
			title: m.neu ? __("Materialbedarf erzeugt") : __("Materialbedarf"),
			indicator: m.neu ? "green" : "orange",
			message: hinweise(m.hinweise) + kopf + tabelle,
		});
	}

	function aufrufen(frm, methode, args, meldung, anzeigen) {
		frappe
			.call({ method: PLANUNG + methode, args, freeze: true, freeze_message: meldung })
			.then((r) => {
				anzeigen(r.message || {});
				frm.reload_doc();
			});
	}

	corevatis_erpnext.planung.buttons = function (frm, args) {
		if (!ROLLEN.some((r) => frappe.user.has_role(r))) {
			return;
		}
		frm.add_custom_button(
			__("Arbeitspakete erzeugen"),
			() => aufrufen(frm, "tasks_erzeugen", args, __("Arbeitspakete werden angelegt …"), ergebnis_arbeitspakete),
			GRUPPE
		);
		frm.add_custom_button(
			__("Materialbedarf erzeugen"),
			() => aufrufen(frm, "materialbedarf_erzeugen", args, __("Materialbedarf wird ermittelt …"), ergebnis_materialbedarf),
			GRUPPE
		);
	};

	frappe.ui.form.on("Sales Order", {
		refresh(frm) {
			if (frm.doc.docstatus === 1) {
				corevatis_erpnext.planung.buttons(frm, { sales_order: frm.doc.name });
			}
		},
	});

	frappe.ui.form.on("Project", {
		refresh(frm) {
			if (!frm.is_new() && frm.doc.sales_order) {
				corevatis_erpnext.planung.buttons(frm, { project: frm.doc.name });
			}
		},
	});
})();
