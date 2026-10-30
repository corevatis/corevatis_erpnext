// Corevatis – Angebot annehmen/ablehnen (Aufgabe 4.4) und Nachfassen (Aufgabe 4.5)
// Server: corevatis_erpnext.angebot.annahme, corevatis_erpnext.angebot.nachfassen (Rollen Büro/Meister)
frappe.provide("corevatis_erpnext.angebot");

(function () {
	const ANNAHME = "corevatis_erpnext.angebot.annahme.";
	const NACHFASSEN = "corevatis_erpnext.angebot.nachfassen.";
	const ROLLEN = ["Büro", "Meister", "System Manager", "Administrator"];
	const GRUPPE = __("Angebot");
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));

	function link(doctype, name) {
		return corevatis_erpnext.beleg_link(doctype, name);
	}

	function hinweise(liste) {
		return (liste || [])
			.filter(Boolean)
			.map((h) => `<div class="alert alert-warning small">${esc(h)}</div>`)
			.join("");
	}

	// Review Epic 4 A-5: Projekt automatisch nur für Angebote aus dem Aufmaß
	const ist_corevatis_angebot = (doc) => !!(doc.cv_erzeugt_aus_aufmass || doc.cv_aufmass);
	// Review Epic 4 A-2: Eventual-/Alternativpositionen (Corevatis-Kennzeichen)
	const hat_kennzeichen = (doc) => (doc.items || []).some((z) => z.cv_eventualposition || z.cv_alternativposition);

	corevatis_erpnext.angebot.annehmen_dialog = function (frm) {
		const corevatis = ist_corevatis_angebot(frm.doc);
		const d = new frappe.ui.Dialog({
			title: __("Angebot annehmen"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "info",
					options: `<p class="small text-muted">${esc(
						__(
							"Legt den Kunden (aus der Anfrage), den Auftrag (AB-) ohne Eventual- und Alternativpositionen und das Projekt an und setzt die Anfrage auf „Gewonnen“."
						)
					)}</p>`,
				},
				{ fieldtype: "Date", fieldname: "auftragsdatum", label: __("Auftragsdatum / Projektstart"), reqd: 1, default: frappe.datetime.get_today() },
				{
					fieldtype: "Date",
					fieldname: "lieferdatum",
					label: __("Liefertermin / geplantes Projektende"),
					reqd: 1,
					default: frappe.datetime.add_days(frappe.datetime.get_today(), 14),
				},
				{ fieldtype: "Check", fieldname: "einreichen", label: __("Auftrag direkt einreichen"), default: 1 },
				{
					fieldtype: "Check",
					fieldname: "projekt_anlegen",
					label: __("Projekt anlegen"),
					default: corevatis ? 1 : 0,
					read_only: corevatis ? 1 : 0,
					description: corevatis
						? __("Angebot aus dem Aufmaß: das Projekt wird immer angelegt.")
						: __("Angebot ohne Aufmaß (z. B. Materialverkauf): Projekt nur bei Bedarf."),
				},
			],
			primary_action_label: __("Annehmen"),
			primary_action(values) {
				d.hide();
				frappe
					.call({
						method: ANNAHME + "angebot_annehmen",
						args: { quotation: frm.doc.name, ...values },
						freeze: true,
						freeze_message: __("Auftrag und Projekt werden angelegt …"),
					})
					.then((r) => {
						const m = r.message || {};
						const titel = m.bereits_angenommen ? __("Angebot war bereits angenommen") : __("Angebot angenommen");
						frappe.msgprint({
							title: titel,
							indicator: m.warnungen && m.warnungen.length ? "orange" : "green",
							message:
								hinweise(m.warnungen) +
								`<table class="table table-sm small">
									<tr><td>${esc(__("Kunde"))}</td><td>${link("Customer", m.kunde)}${m.kunde_neu ? " (" + esc(__("neu")) + ")" : ""}</td></tr>
									<tr><td>${esc(__("Auftrag"))}</td><td>${link("Sales Order", m.auftrag)} ${m.auftrag_docstatus === 1 ? esc(__("eingereicht")) : esc(__("Entwurf"))}</td></tr>
									<tr><td>${esc(__("Projekt"))}</td><td>${link("Project", m.projekt)}</td></tr>
									<tr><td>${esc(__("Anfrage"))}</td><td>${link("Lead", m.lead)} ${esc(m.lead_status || "")}</td></tr>
								</table>`,
						});
						frm.reload_doc();
					});
			},
		});
		d.show();
	};

	corevatis_erpnext.angebot.ablehnen_dialog = async function (frm) {
		const r = await frappe.call({ method: ANNAHME + "verlustgruende" });
		const d = new frappe.ui.Dialog({
			title: __("Angebot ablehnen"),
			fields: [
				{ fieldtype: "Select", fieldname: "grund", label: __("Grund"), reqd: 1, options: ["", ...(r.message || [])] },
				{ fieldtype: "Small Text", fieldname: "details", label: __("Details (optional)") },
			],
			primary_action_label: __("Als verloren markieren"),
			primary_action(values) {
				d.hide();
				frappe
					.call({ method: ANNAHME + "angebot_ablehnen", args: { quotation: frm.doc.name, ...values }, freeze: true })
					.then((res) => {
						const m = res.message || {};
						frappe.msgprint({
							title: __("Angebot abgelehnt"),
							indicator: m.warnungen && m.warnungen.length ? "orange" : "red",
							message:
								hinweise(m.warnungen) +
								`<p>${esc(__("Angebot"))} ${esc(m.angebot)}: ${esc(m.status)}<br>${esc(__("Anfrage"))} ${link("Lead", m.lead)}: ${esc(m.lead_status || "-")}</p>`,
						});
						frm.reload_doc();
					});
			},
		});
		d.show();
	};

	corevatis_erpnext.angebot.nachfassen = function (frm) {
		frappe.call({ method: NACHFASSEN + "nachfassen", args: { quotation: frm.doc.name }, freeze: true }).then((r) => {
			const m = r.message || {};
			const d = new frappe.ui.Dialog({
				title: __("Nachfassen: {0}", [frm.doc.name]),
				size: "large",
				fields: [
					{ fieldtype: "HTML", fieldname: "hinweise" },
					{ fieldtype: "Data", fieldname: "betreff", label: __("Betreff"), read_only: 1, default: m.betreff },
					{ fieldtype: "Small Text", fieldname: "text", label: __("Text zum Kopieren (E-Mail, WhatsApp)"), default: m.text },
				],
				primary_action_label: __("Text kopieren"),
				primary_action() {
					frappe.utils.copy_to_clipboard(d.get_value("text"));
				},
			});
			const info = [
				__("Nachfass-Aufgabe ({0}. Nachfassen): {1}", [m.zyklus, (m.todos || []).join(", ")]),
				m.email_queue ? __("Follow-up-Mail in der E-Mail-Warteschlange: {0} (ohne E-Mail-Konto wird nichts versendet)", [m.email_queue]) : "",
			]
				.filter(Boolean)
				.map((h) => `<div class="alert alert-info small">${esc(h)}</div>`)
				.join("");
			d.fields_dict.hinweise.$wrapper.html(info + hinweise(m.warnungen));
			d.show();
			frm.reload_doc();
		});
	};

	// ERPNext „Erstellen → Auftrag“ bei Eventual-/Alternativpositionen: Auswahl-Dialog von ERPNext
	// behandelt sie als Ersatz für die Zeile davor - stattdessen „Angebot annehmen“ (Review Epic 4 A-2)
	function hinweis_annehmen(frm) {
		const text = __(
			"Dieses Angebot enthält Eventual- oder Alternativpositionen. Bitte den Auftrag über „Angebot annehmen“ anlegen: Er übernimmt alle Normalpositionen und lässt Eventual- und Alternativpositionen weg."
		);
		if (frappe.user.has_role(ROLLEN)) {
			frappe.confirm(`${esc(text)}<br><br>${esc(__("Jetzt „Angebot annehmen“ öffnen?"))}`, () =>
				corevatis_erpnext.angebot.annehmen_dialog(frm)
			);
		} else {
			frappe.msgprint({ title: __("Auftrag anlegen"), indicator: "orange", message: esc(text) });
		}
	}

	function erpnext_auftrag_umleiten(frm) {
		const cs = frm.cscript;
		if (!cs || cs.__cv_make_sales_order) return;
		// Rückfall, falls der Button doch sichtbar ist (eigener Button, andere Menüs)
		cs.__cv_make_sales_order = cs.make_sales_order;
		cs.make_sales_order = function () {
			if (hat_kennzeichen(frm.doc)) return hinweis_annehmen(frm);
			return cs.__cv_make_sales_order && cs.__cv_make_sales_order.apply(this, arguments);
		};
	}

	function entfernen_erpnext_menues(frm) {
		// Gruppen-Dropdowns (Positionen übernehmen aus, Erstellen, …) als Ganzes entfernen
		for (const gruppe of ["Get Items From", "Create", "Tools", "Action"]) {
			try {
				const $g = frm.page.get_inner_group_button(__(gruppe));
				if ($g && $g.length) {
					frm.page.destroy_group_dropdowns && frm.page.destroy_group_dropdowns($g);
					$g.remove();
				}
			} catch (e) {
				// Gruppe nicht vorhanden
			}
		}
		for (const label of ["Set as Lost", "Set as Open"]) {
			try {
				frm.remove_custom_button(__(label));
			} catch (e) {
				// Button nicht vorhanden
			}
		}
	}

	// Erklärt oben im Formular, was gerade passiert ist und was als Nächstes kommt
	function zustand_erklaeren(frm) {
		const d = frm.doc;
		const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
		let text = "";
		let farbe = "blue";
		if (d.docstatus === 0) {
			const herkunft = d.cv_aufmass
				? __("Angebotsentwurf aus der Besichtigung (Aufmaß {0}, erstellt von {1}).", [
						esc(d.cv_aufmass),
						esc(frappe.user_info(d.owner).fullname || d.owner),
				  ])
				: __("Angebotsentwurf.");
			text =
				herkunft +
				" " +
				__("Positionen und Preise prüfen, dann oben „Angebot freigeben“. Danach das PDF an den Kunden schicken; sagt er zu: „Vom Kunden angenommen“.");
		} else if (d.docstatus === 1 && ["Ordered", "Partially Ordered"].includes(d.status)) {
			text = __("Vom Kunden angenommen – Auftrag und Projekt sind angelegt (siehe Prozessleiste oben).");
			farbe = "green";
		} else if (d.docstatus === 1 && d.status === "Lost") {
			text = __("Vom Kunden abgelehnt.");
			farbe = "red";
		} else if (d.docstatus === 1) {
			text = __("Freigegeben am {0}, wartet auf die Antwort des Kunden: „Vom Kunden angenommen“ oder „Vom Kunden abgelehnt“ (Menü „Angebot“). Drucken über das Druckersymbol rechts oben.", [
				esc(frappe.datetime.str_to_user(d.transaction_date)),
			]);
		} else if (d.docstatus === 2) {
			text = __("Storniert.");
			farbe = "red";
		}
		if (text) {
			// Frappes Standardhinweis („Zum Freigeben oben klicken“) wäre der zweite blaue Balken
			frm.dashboard.clear_headline && frm.dashboard.clear_headline();
			frm.set_intro(text, farbe);
		}
	}

	frappe.ui.form.on("Quotation", {
		refresh(frm) {
			erpnext_auftrag_umleiten(frm);
			if (frm.doc.docstatus === 1 && hat_kennzeichen(frm.doc)) {
				// ERPNext fügt den Button im Controller-refresh nach diesem Handler hinzu
				setTimeout(() => frm.remove_custom_button(__("Sales Order"), __("Create")), 0);
				frm.dashboard.add_comment(
					__("Eventual-/Alternativpositionen: Auftrag nur über „Angebot annehmen“ (ERPNext „Erstellen → Auftrag“ ist ausgeblendet)."),
					"blue",
					true
				);
			}
			if (!frappe.user.has_role(ROLLEN)) return;
			// ERPNext-Menüs, die den Ablauf des Betriebs nur verwirren (Positionen übernehmen,
			// Erstellen → Auftrag/Rechnung/Abo, Als verloren markieren): unser Weg ist die Leiste
			// ERPNext hängt seine Gruppen teils verzögert an (nach Ajax-Antworten), deshalb mehrfach
			for (const ms of [0, 300, 1200]) setTimeout(() => entfernen_erpnext_menues(frm), ms);
			zustand_erklaeren(frm);
			const status = frm.doc.status;
			if (frm.doc.docstatus === 0) {
				// Ein Hauptknopf statt „Freigeben“ plus „Angebot freigeben“: der Standard-Knopf wird ersetzt
				if (frappe.perm.has_perm(frm.doctype, 0, "submit", frm.doc) && !frm.is_dirty()) {
					frm.page.set_primary_action(__("Angebot freigeben"), () => corevatis_erpnext.prozess.freigeben(frm));
				}
				return;
			}
			if (frm.doc.docstatus !== 1) return;
			if (["Open", "Replied", "Partially Ordered", "Ordered", "Expired"].includes(status)) {
				// eigener Button (nicht im Menü), damit „ein Klick“ sichtbar ist
				frm.add_custom_button(__("Vom Kunden angenommen"), () => corevatis_erpnext.angebot.annehmen_dialog(frm));
			}
			if (["Open", "Replied", "Expired"].includes(status)) {
				frm.add_custom_button(__("Vom Kunden abgelehnt"), () => corevatis_erpnext.angebot.ablehnen_dialog(frm), GRUPPE);
				frm.add_custom_button(__("Nachfassen"), () => corevatis_erpnext.angebot.nachfassen(frm), GRUPPE);
			}
		},
	});
})();
