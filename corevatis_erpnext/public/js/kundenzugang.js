// Corevatis – Kundenzugang (Phase 2 P2.7): Link zum Kundenportal an Anfrage, Kunde, Projekt und Angebot.
// Server: corevatis_erpnext.kundenportal (Rollen Büro/System Manager). Der Klartext-Link kommt nur beim
// Erzeugen zurück und wird hier einmal angezeigt (Kopieren, QR-Code); gespeichert ist nur sein Hash.
// Die Datei steht in doctype_js mehrerer Doctypes; sie richtet sich nur beim ersten Laden ein.
frappe.provide("corevatis_erpnext.kundenportal");

(function () {
	const K = corevatis_erpnext.kundenportal;
	if (K._eingerichtet) return;
	K._eingerichtet = true;

	const METHODE = "corevatis_erpnext.kundenportal.";
	const ROLLEN = ["Büro", "System Manager", "Administrator"];
	const DOCTYPES = ["Lead", "Customer", "Project", "Quotation"];
	const esc = (s) => frappe.utils.escape_html(s === null || s === undefined ? "" : String(s));
	const datum = (w) => (w ? frappe.datetime.str_to_user(String(w).split(" ")[0]) : "");

	K.darf = function () {
		return frappe.session.user === "Administrator" || (frappe.user_roles || []).some((r) => ROLLEN.includes(r));
	};

	function zeile(z) {
		const stand = z.widerrufen
			? `<span class="indicator-pill gray">${esc(__("widerrufen"))} ${esc(datum(z.widerrufen_am))}</span>`
			: `<span class="indicator-pill green">${esc(__("gültig bis"))} ${esc(datum(z.gueltig_bis))}</span>`;
		const nutzung = z.letzte_nutzung
			? __("{0} Aufrufe, zuletzt {1}", [z.aufrufe || 0, frappe.datetime.str_to_user(z.letzte_nutzung)])
			: __("noch nicht aufgerufen");
		const knopf = z.widerrufen
			? ""
			: `<button class="btn btn-xs btn-default cv-kz-widerrufen" data-name="${esc(z.name)}">${esc(__("Widerrufen"))}</button>`;
		return `<tr><td>${esc(z.name)}<br><span class="text-muted small">${esc(__("angelegt {0}", [datum(z.creation)]))}</span></td>
			<td>${stand}<br><span class="text-muted small">${esc(nutzung)}${z.uploads ? ", " + esc(__("{0} Fotos", [z.uploads])) : ""}</span></td>
			<td class="text-right">${knopf}</td></tr>`;
	}

	function uebersicht_html(u) {
		const zeilen = (u.zugaenge || []).map(zeile).join("");
		const tabelle = zeilen
			? `<table class="table table-sm small" style="margin-top:8px"><tbody>${zeilen}</tbody></table>`
			: `<p class="text-muted small" style="margin-top:8px">${esc(__("Noch kein Zugang für diesen Vorgang."))}</p>`;
		return `<p>${esc(__("Vorgang"))}: ${corevatis_erpnext.beleg_link ? corevatis_erpnext.beleg_link(u.anker_doctype, u.anker) : esc(u.anker)}
			${u.kunde_name ? " · " + esc(u.kunde_name) : ""}</p>
			<p class="small text-muted">${esc(
				__(
					"Der Kunde öffnet seinen Vorgang über einen Link ohne Anmeldung: Termin bestätigen, Angebot mit Unterschrift annehmen, Fotos schicken, Rechnungen und freigegebene Dokumente sehen. „Neuen Link erzeugen“ ersetzt den bisherigen Link; der Link ist {0} Tage gültig und wird nur einmal angezeigt.",
					[u.gueltig_tage || 90]
				)
			)}</p>${tabelle}`;
	}

	function link_html(r) {
		return `<div class="alert alert-warning small">${esc(
			__("Diesen Link jetzt kopieren und dem Kunden selbst zuschicken (E-Mail, WhatsApp) oder den QR-Code zeigen. Er wird nicht gespeichert und nicht noch einmal angezeigt. Bitte wie ein Passwort behandeln.")
		)}</div>
		<div class="input-group" style="margin-bottom:10px">
			<input type="text" class="form-control cv-kz-link" readonly value="${esc(r.url)}">
			<span class="input-group-btn"><button class="btn btn-default cv-kz-kopieren">${esc(__("Kopieren"))}</button></span>
		</div>
		<p class="small text-muted">${esc(__("Gültig bis {0}", [datum(r.gueltig_bis)]))} · ${esc(r.name)}</p>
		${r.qr_svg ? `<div class="cv-kz-qr" style="max-width:220px">${r.qr_svg}</div>` : ""}`;
	}

	K.link_anzeigen = function (r, d) {
		d.set_title(__("Link zum Kundenportal"));
		d.fields_dict.inhalt.$wrapper.html(link_html(r));
		d.set_primary_action(__("Fertig"), () => d.hide());
		d.fields_dict.inhalt.$wrapper.find(".cv-kz-kopieren").on("click", function () {
			const feld = d.fields_dict.inhalt.$wrapper.find(".cv-kz-link")[0];
			feld.select();
			const text = feld.value;
			const fertig = () => frappe.show_alert({ message: __("Link kopiert"), indicator: "green" });
			if (navigator.clipboard && navigator.clipboard.writeText) {
				navigator.clipboard.writeText(text).then(fertig, () => document.execCommand("copy") && fertig());
			} else if (document.execCommand("copy")) {
				fertig();
			}
		});
	};

	K.neu = function (doctype, name, d) {
		return frappe
			.call({ method: METHODE + "zugang_anlegen", args: { doctype, name }, freeze: true })
			.then((r) => {
				if (r.message) K.link_anzeigen(r.message, d);
			});
	};

	K.dialog = function (doctype, name, frm) {
		frappe.call({ method: METHODE + "uebersicht", args: { doctype, name } }).then((r) => {
			const u = r.message || {};
			const d = new frappe.ui.Dialog({
				title: __("Kundenzugang"),
				fields: [{ fieldtype: "HTML", fieldname: "inhalt" }],
				primary_action_label: __("Neuen Link erzeugen"),
				primary_action: () => K.neu(doctype, name, d),
			});
			d.fields_dict.inhalt.$wrapper.html(uebersicht_html(u));
			d.fields_dict.inhalt.$wrapper.on("click", ".cv-kz-widerrufen", function () {
				const zugang = $(this).attr("data-name");
				frappe.confirm(__("Zugang {0} widerrufen? Der Link funktioniert danach nicht mehr.", [zugang]), () => {
					frappe.call({ method: METHODE + "zugang_widerrufen", args: { name: zugang } }).then(() => {
						d.hide();
						if (frm && frm.doctype === "Kundenzugang") frm.reload_doc();
						else K.dialog(doctype, name, frm);
					});
				});
			});
			d.show();
		});
	};

	K.knopf = function (frm) {
		if (frm.is_new() || !K.darf()) return;
		frm.add_custom_button(__("Kundenzugang"), () => K.dialog(frm.doctype, frm.doc.name, frm));
	};

	for (const dt of DOCTYPES) {
		frappe.ui.form.on(dt, {
			refresh(frm) {
				K.knopf(frm);
			},
		});
	}

	// Formular des Zugangs selbst: Link neu erzeugen (ersetzt diesen Zugang), widerrufen
	frappe.ui.form.on("Kundenzugang", {
		refresh(frm) {
			if (frm.is_new()) {
				frm.set_intro(
					__("Beim Speichern wird das Token erzeugt und der Link einmalig angezeigt. Einfacher: Knopf „Kundenzugang“ an Anfrage, Kunde, Projekt oder Angebot."),
					"blue"
				);
				return;
			}
			frm.set_intro(
				frm.doc.widerrufen
					? __("Dieser Zugang ist widerrufen; der Link funktioniert nicht mehr.")
					: __("Der Link selbst ist nicht gespeichert (nur sein Hash). Verloren? „Neuen Link erzeugen“ ersetzt diesen Zugang."),
				frm.doc.widerrufen ? "orange" : "blue"
			);
			if (!K.darf()) return;
			frm.add_custom_button(__("Neuen Link erzeugen"), () => {
				const d = new frappe.ui.Dialog({
					title: __("Link zum Kundenportal"),
					fields: [{ fieldtype: "HTML", fieldname: "inhalt" }],
				});
				d.show();
				K.neu(frm.doc.vorgang_doctype, frm.doc.vorgang, d).then(() => frm.reload_doc());
			});
			if (!frm.doc.widerrufen) {
				frm.add_custom_button(__("Widerrufen"), () => {
					frappe.confirm(__("Zugang {0} widerrufen? Der Link funktioniert danach nicht mehr.", [frm.doc.name]), () =>
						frappe
							.call({ method: METHODE + "zugang_widerrufen", args: { name: frm.doc.name } })
							.then(() => frm.reload_doc())
					);
				});
			}
		},
	});
})();
