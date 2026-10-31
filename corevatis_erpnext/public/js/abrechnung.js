// Corevatis – Abrechnung (Aufgaben 7.1/7.2): „Abschlag stellen“ und „Schlussrechnung erstellen“ an
// Projekt und Auftrag, Rechnungsformular mit Zustandstext und „Rechnung freigeben“.
// Server: corevatis_erpnext.abrechnung (Rolle Büro); Rechenlogik im Kern corevatis.engine.invoice.
frappe.provide("corevatis_erpnext.abrechnung");

(function () {
	const A = corevatis_erpnext.abrechnung;
	const METHODE = "corevatis_erpnext.abrechnung.";
	const ROLLEN = ["Büro", "System Manager", "Administrator"];
	const GRUPPE = __("Abrechnung");
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
	const geld = (w, waehrung) => format_currency(w || 0, waehrung || frappe.boot.sysdefaults.currency);
	const zahl = (w, n = 2) => format_number(w || 0, null, n);
	const prozent = (w) => (Math.round((w || 0) * 100) / 100).toLocaleString(frappe.boot.lang || "de");
	const darf = () => ROLLEN.some((r) => frappe.user.has_role(r));

	function link(doctype, name, label) {
		return corevatis_erpnext.beleg_link(doctype, name, label);
	}

	function hinweise(liste) {
		return (liste || [])
			.filter(Boolean)
			.map((h) => `<div class="alert alert-warning small">${esc(h)}</div>`)
			.join("");
	}

	function args_fuer(frm) {
		return frm.doctype === "Project"
			? { project: frm.doc.name }
			: frm.doctype === "Sales Order"
			  ? { sales_order: frm.doc.name }
			  : frm.doctype === "Abschlagsplan"
			    ? { sales_order: frm.doc.auftrag }
			    : { sales_order: frm.doc.cv_auftrag };
	}

	// --- Rechnung freigeben (Submit in der Sprache des Betriebs) --------------------------------
	A.freigeben = function (frm) {
		const art = frm.doc.cv_schlussrechnung ? __("Schlussrechnung") : __("Abschlagsrechnung");
		frappe.confirm(
			__(
				"Die {0} wird verbindlich gebucht und zählt am Auftrag als abgerechnet. Danach das PDF an den Kunden schicken.",
				[art]
			),
			() => frm.savesubmit()
		);
	};

	// --- Abschlag stellen ---------------------------------------------------------------------
	function zeilen_tabelle(v) {
		const plan = v.plan;
		const zeilen = plan ? plan.zeilen : (v.vorlage && v.vorlage.zeilen) || [];
		if (!zeilen.length) return "";
		const waehrung = frappe.boot.sysdefaults.currency;
		const rows = zeilen
			.map((z, i) => {
				const status = plan ? __(z.status) : __("Vorlage");
				const betrag = plan ? geld(z.brutto, waehrung) : "";
				const rechnung = z.rechnung ? link("Sales Invoice", z.rechnung) : "";
				const anteil = z.prozent ? `${prozent(z.prozent)} %` : geld(z.betrag, waehrung);
				return `<tr><td>${z.nr || i + 1}</td><td>${esc(z.bezeichnung || "")}</td><td class="text-right">${anteil}</td><td>${esc(
					z.faellig_bei || ""
				)}</td><td class="text-right">${betrag}</td><td>${status}</td><td>${rechnung}</td></tr>`;
			})
			.join("");
		return `<table class="table table-sm small"><thead><tr><th>${__("Nr.")}</th><th>${__("Bezeichnung")}</th><th class="text-right">${__(
			"Anteil"
		)}</th><th>${__("Fällig")}</th><th class="text-right">${__("Brutto")}</th><th>${__("Status")}</th><th>${__("Rechnung")}</th></tr></thead><tbody>${rows}</tbody></table>`;
	}

	A.abschlag_dialog = function (frm) {
		if (!darf()) {
			frappe.msgprint(__("Abschlagsrechnungen stellt nur das Büro."));
			return;
		}
		frappe.xcall(METHODE + "abschlag_vorschlag", args_fuer(frm)).then((v) => {
			if (v.schlussrechnung) {
				frappe.msgprint({
					title: __("Abschlag stellen"),
					indicator: "orange",
					message: __("Zum Auftrag gibt es schon die Schlussrechnung {0} - keine weiteren Abschläge.", [
						link("Sales Invoice", v.schlussrechnung.name),
					]),
				});
				return;
			}
			const n = v.naechste || (v.vorlage && v.vorlage.zeilen && v.vorlage.zeilen[0]) || {};
			const a = v.auftrag || {};
			const kopf = `<p>${__("Auftrag {0} ({1}): Auftragssumme {2} netto, {3} brutto.", [
				link("Sales Order", a.name),
				esc(a.kunde || ""),
				geld(a.netto),
				geld(a.brutto),
			])}${
				v.plan
					? " " + __("Bisher gestellt: {0} % ({1} netto).", [prozent(v.plan.gestellt_prozent), geld(v.plan.gestellt_netto)])
					: " " + __("Noch kein Abschlagsplan - er entsteht aus der Vorlage „{0}“.", [esc((v.vorlage || {}).name || "")])
			}</p>${zeilen_tabelle(v)}`;
			const d = new frappe.ui.Dialog({
				title: __("Abschlag stellen"),
				size: "large",
				fields: [
					{ fieldtype: "HTML", fieldname: "kopf", options: kopf },
					{ fieldtype: "Section Break", label: __("Dieser Abschlag") },
					{
						fieldtype: "Select",
						fieldname: "art",
						label: __("Berechnung"),
						options: [
							{ value: "prozent", label: __("Prozent der Auftragssumme") },
							{ value: "betrag", label: __("Fester Betrag (netto)") },
						],
						default: n.betrag && !n.prozent ? "betrag" : "prozent",
					},
					{
						fieldtype: "Percent",
						fieldname: "prozent",
						label: __("Prozent"),
						default: n.prozent || null,
						depends_on: "eval:doc.art=='prozent'",
					},
					{
						fieldtype: "Currency",
						fieldname: "betrag",
						label: __("Betrag (netto)"),
						default: n.betrag || null,
						depends_on: "eval:doc.art=='betrag'",
					},
					{ fieldtype: "Column Break" },
					{ fieldtype: "Data", fieldname: "bezeichnung", label: __("Bezeichnung"), default: n.bezeichnung || "" },
					{
						fieldtype: "Select",
						fieldname: "faellig_bei",
						label: __("Fällig"),
						options: (v.faellig_bei || []).join("\n"),
						default: n.faellig_bei || "bei Baufortschritt",
					},
					{ fieldtype: "Date", fieldname: "datum", label: __("Datum"), default: n.datum || null, depends_on: "eval:doc.faellig_bei=='Datum'" },
					{ fieldtype: "Section Break" },
					{
						fieldtype: "Check",
						fieldname: "bestaetigen",
						label: __("Auch über die Auftragssumme hinaus stellen (nur mit Hinweis an den Kunden)"),
						default: 0,
					},
					{
						fieldtype: "HTML",
						fieldname: "fuss",
						options: `<p class="small text-muted">${esc(
							__("Es entsteht eine Rechnung im Entwurf mit einer Position „Abschlagsrechnung Nr. … zum Auftrag …“. Danach prüfen und oben „Rechnung freigeben“.")
						)}</p>`,
					},
				],
				primary_action_label: __("Abschlag anlegen"),
				primary_action(values) {
					const args = { ...args_fuer(frm), bestaetigen: values.bestaetigen ? 1 : 0 };
					if (n.name) args.zeile = n.name;
					if (values.art === "betrag") args.betrag = values.betrag;
					else args.prozent = values.prozent;
					args.bezeichnung = values.bezeichnung;
					args.faellig_bei = values.faellig_bei;
					if (values.datum) args.datum = values.datum;
					d.hide();
					frappe
						.call({ method: METHODE + "abschlag_stellen", args, freeze: true, freeze_message: __("Abschlagsrechnung wird angelegt …") })
						.then((r) => {
							const m = r.message || {};
							frappe.msgprint({
								title: __("Abschlagsrechnung angelegt"),
								indicator: "green",
								message:
									hinweise(m.warnungen) +
									`<p>${esc(m.text)}</p><p>${__("Netto {0}, USt {1}, brutto {2}. Gestellt einschließlich dieser: {3} %.", [
										geld(m.netto),
										geld(m.steuer),
										geld(m.brutto),
										prozent((m.kumuliert || {}).prozent),
									])}</p>`,
								primary_action: {
									label: __("Rechnung öffnen"),
									action() {
										frappe.hide_msgprint();
										frappe.set_route("Form", "Sales Invoice", m.rechnung);
									},
								},
							});
							frm.reload_doc();
						});
				},
			});
			d.show();
		});
	};

	// --- Schlussrechnung erstellen ------------------------------------------------------------
	A.schlussrechnung_dialog = function (frm) {
		if (!darf()) {
			frappe.msgprint(__("Schlussrechnungen stellt nur das Büro."));
			return;
		}
		frappe.xcall(METHODE + "schlussrechnung_vorschlag", args_fuer(frm)).then((v) => {
			if (v.schlussrechnung) {
				frappe.msgprint({
					title: __("Schlussrechnung"),
					indicator: "orange",
					message: __("Zum Auftrag gibt es schon die Schlussrechnung {0}.", [link("Sales Invoice", v.schlussrechnung.name)]),
				});
				return;
			}
			if ((v.entwuerfe || []).length) {
				frappe.msgprint({
					title: __("Schlussrechnung"),
					indicator: "orange",
					message: __("Abschlagsrechnung {0} ist noch im Entwurf: erst freigeben oder löschen.", [
						v.entwuerfe.map((n) => link("Sales Invoice", n)).join(", "),
					]),
				});
				return;
			}
			const a = v.auftrag || {};
			const abn = v.abnahme || {};
			let abnahme_text = "";
			if (abn.abgenommen) {
				abnahme_text = `<span class="indicator-pill green">${__("Abgenommen am {0}", [frappe.datetime.str_to_user(abn.datum)])}</span>`;
			} else if (abn.sperre && v.projekt) {
				abnahme_text = `<span class="indicator-pill orange">${__("Noch nicht abgenommen")}</span> <span class="small text-muted">${esc(
					abn.darf_uebersteuern
						? __("Schlussrechnung vor der Abnahme nur mit Begründung (Übersteuerung).")
						: __("Zuerst das Abnahmeprotokoll abschließen; nur ein System Manager kann vorher freigeben.")
				)}</span>`;
			}
			const abschlaege = (v.abschlaege || [])
				.map(
					(z) =>
						`<tr><td>${z.nr}</td><td>${link("Sales Invoice", z.name)}</td><td>${esc(frappe.datetime.str_to_user(z.datum))}</td><td class="text-right">${geld(
							z.netto
						)}</td><td class="text-right">${geld(z.brutto)}</td><td>${z.bezahlt ? __("bezahlt") : __("offen")}</td></tr>`
				)
				.join("");
			const kopf = `<p>${__("Auftrag {0} ({1}): Auftragssumme {2} netto.", [link("Sales Order", a.name), esc(a.kunde || ""), geld(a.netto)])} ${abnahme_text}</p>${
				abschlaege
					? `<p class="small"><b>${__("Abzüglich Abschlagsrechnungen")}</b></p><table class="table table-sm small"><thead><tr><th>${__(
							"Nr."
					  )}</th><th>${__("Rechnung")}</th><th>${__("Datum")}</th><th class="text-right">${__("Netto")}</th><th class="text-right">${__(
							"Brutto"
					  )}</th><th></th></tr></thead><tbody>${abschlaege}</tbody></table>`
					: `<p class="small text-muted">${__("Keine Abschlagsrechnungen - die Schlussrechnung umfasst die ganze Leistung.")}</p>`
			}`;
			const positionen = (v.positionen || []).map((p) => ({
				so_detail: p.so_detail,
				pos: p.idx,
				item_code: p.item_code,
				bezeichnung: p.bezeichnung,
				einheit: p.einheit,
				menge_auftrag: p.menge_auftrag,
				menge_ist: p.menge_ist,
				einzelpreis: p.einzelpreis,
				lohnanteil: p.lohnanteil,
				hinweis: p.hinweis,
			}));
			const d = new frappe.ui.Dialog({
				title: __("Schlussrechnung erstellen"),
				size: "extra-large",
				fields: [
					{ fieldtype: "HTML", fieldname: "kopf", options: kopf },
					{
						fieldtype: "Table",
						fieldname: "positionen",
						label: __("Auftragspositionen mit Ist-Mengen"),
						cannot_add_rows: true,
						cannot_delete_rows: true,
						in_place_edit: true,
						data: positionen,
						get_data: () => positionen,
						fields: [
							{ fieldtype: "Int", fieldname: "pos", label: __("Pos."), in_list_view: 1, columns: 1, read_only: 1 },
							{ fieldtype: "Data", fieldname: "bezeichnung", label: __("Leistung"), in_list_view: 1, columns: 3, read_only: 1 },
							{ fieldtype: "Float", fieldname: "menge_auftrag", label: __("Auftrag"), in_list_view: 1, columns: 1, read_only: 1 },
							{ fieldtype: "Float", fieldname: "menge_ist", label: __("Ist-Menge"), in_list_view: 1, columns: 1 },
							{ fieldtype: "Data", fieldname: "einheit", label: __("Einheit"), in_list_view: 1, columns: 1, read_only: 1 },
							{ fieldtype: "Currency", fieldname: "einzelpreis", label: __("Einzelpreis"), in_list_view: 1, columns: 1, read_only: 1 },
							{ fieldtype: "Currency", fieldname: "lohnanteil", label: __("Lohnanteil (netto)"), in_list_view: 1, columns: 1 },
							{ fieldtype: "Data", fieldname: "hinweis", label: __("Hinweis"), in_list_view: 1, columns: 1, read_only: 1 },
							{ fieldtype: "Data", fieldname: "so_detail", label: "so_detail", hidden: 1 },
						],
					},
					{ fieldtype: "Section Break", label: __("Nachträge (zusätzliche Leistungen)") },
					{
						fieldtype: "Table",
						fieldname: "nachtraege",
						label: __("Nachträge"),
						data: [],
						get_data: () => [],
						fields: [
							{ fieldtype: "Link", fieldname: "item_code", options: "Item", label: __("Artikel"), in_list_view: 1, columns: 2, reqd: 1 },
							{ fieldtype: "Data", fieldname: "bezeichnung", label: __("Bezeichnung"), in_list_view: 1, columns: 3 },
							{ fieldtype: "Float", fieldname: "menge", label: __("Menge"), in_list_view: 1, columns: 1 },
							{ fieldtype: "Data", fieldname: "einheit", label: __("Einheit"), in_list_view: 1, columns: 1 },
							{ fieldtype: "Currency", fieldname: "einzelpreis", label: __("Einzelpreis"), in_list_view: 1, columns: 1 },
							{ fieldtype: "Currency", fieldname: "lohnanteil", label: __("Lohnanteil (netto)"), in_list_view: 1, columns: 1 },
						],
					},
					{ fieldtype: "Section Break" },
					{
						fieldtype: "Check",
						fieldname: "uebersteuern",
						label: __("Schlussrechnung ohne Abnahme (Übersteuerung, nur System Manager)"),
						default: 0,
						hidden: abn.abgenommen || !abn.sperre || !v.projekt ? 1 : 0,
					},
					{
						fieldtype: "Small Text",
						fieldname: "grund",
						label: __("Begründung"),
						depends_on: "eval:doc.uebersteuern",
					},
					{
						fieldtype: "HTML",
						fieldname: "fuss",
						options: `<p class="small text-muted">${esc(
							__(
								"Lohnanteil nach § 35a EStG: aus der Kalkulation (Minuten × Stundensatz) vorbelegt, je Position änderbar. Es entsteht eine Rechnung im Entwurf: Auftragspositionen mit Ist-Mengen, Mehrmengen als eigene Zeile, Nachträge, abzüglich der Abschlagsrechnungen."
							)
						)}</p>`,
					},
				],
				primary_action_label: __("Schlussrechnung anlegen"),
				primary_action(values) {
					const pos = {};
					for (const z of values.positionen || []) {
						pos[z.so_detail] = { menge_ist: z.menge_ist, lohnanteil: z.lohnanteil == null || z.lohnanteil === "" ? null : z.lohnanteil };
					}
					const args = {
						...args_fuer(frm),
						positionen: JSON.stringify(pos),
						nachtraege: JSON.stringify((values.nachtraege || []).filter((n) => n.item_code)),
						uebersteuern: values.uebersteuern ? 1 : 0,
						grund: values.grund || null,
					};
					d.hide();
					frappe
						.call({ method: METHODE + "schlussrechnung_erstellen", args, freeze: true, freeze_message: __("Schlussrechnung wird angelegt …") })
						.then((r) => {
							const m = r.message || {};
							const s = m.summen || {};
							const zusammenfassung = s.rest
								? `<p>${__("Leistung {0} netto, abzüglich {1} Abschlagsrechnung(en) {2} netto: zu zahlen {3} netto, {4} USt, {5} brutto. Lohnanteil {6} brutto (§ 35a EStG).", [
										geld(s.leistung.netto),
										m.abschlaege,
										geld(s.abschlaege.netto),
										geld(s.rest.netto),
										geld(s.rest.steuer),
										geld(s.rest.brutto),
										geld(s.lohnanteil.brutto),
								  ])}</p>`
								: "";
							frappe.msgprint({
								title: m.bereits_vorhanden ? __("Schlussrechnung vorhanden") : __("Schlussrechnung angelegt"),
								indicator: m.bereits_vorhanden ? "orange" : "green",
								message: hinweise(m.warnungen) + zusammenfassung,
								primary_action: {
									label: __("Rechnung öffnen"),
									action() {
										frappe.hide_msgprint();
										frappe.set_route("Form", "Sales Invoice", m.rechnung);
									},
								},
							});
							frm.reload_doc();
						});
				},
			});
			d.show();
		});
	};

	// --- Knöpfe an Projekt, Auftrag, Abschlagsplan ---------------------------------------------
	function knoepfe(frm) {
		if (!darf()) return;
		frm.add_custom_button(__("Abschlag stellen"), () => A.abschlag_dialog(frm), GRUPPE);
		frm.add_custom_button(__("Schlussrechnung erstellen"), () => A.schlussrechnung_dialog(frm), GRUPPE);
	}

	frappe.ui.form.on("Project", {
		refresh(frm) {
			if (!frm.is_new() && frm.doc.sales_order) knoepfe(frm);
		},
	});
	frappe.ui.form.on("Sales Order", {
		refresh(frm) {
			if (frm.doc.docstatus === 1) knoepfe(frm);
		},
	});
	frappe.ui.form.on("Abschlagsplan", {
		refresh(frm) {
			if (!frm.is_new() && frm.doc.auftrag) knoepfe(frm);
		},
	});

	// --- Rechnungsformular ---------------------------------------------------------------------
	const BALLAST_CREATE = ["Payment Request", "Invoice Discounting", "Maintenance Schedule", "Subscription", "Delivery Note"];

	function ballast_entfernen(frm) {
		for (const label of BALLAST_CREATE) {
			try {
				frm.remove_custom_button(__(label), __("Create"));
			} catch (e) {
				// Knopf nicht vorhanden
			}
		}
		for (const gruppe of ["Get Items From", "View"]) {
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
		try {
			frm.remove_custom_button(__("Fetch Timesheet"));
		} catch (e) {
			// nicht vorhanden
		}
	}

	frappe.ui.form.on("Sales Invoice", {
		refresh(frm) {
			if (frm.is_new()) return;
			const token = `${frm.doc.name}/${frm.doc.modified}`;
			frm.__cv_ab_token = token;
			frappe.xcall(METHODE + "zustand", { sales_invoice: frm.doc.name }).then((z) => {
				if (!z || frm.__cv_ab_token !== token || z.art === "rechnung") return;
				frm.dashboard.clear_headline && frm.dashboard.clear_headline();
				frm.set_intro(esc(z.text), z.farbe || "blue");
				if (darf()) {
					for (const ms of [0, 300, 1200]) setTimeout(() => ballast_entfernen(frm), ms);
				}
				if (z.aktion && z.aktion.key === "freigeben" && frm.doc.docstatus === 0 && !frm.is_dirty()) {
					frm.page.set_primary_action(z.aktion.label, () => A.freigeben(frm));
				}
				if (z.auftrag) {
					frm.add_custom_button(__("Auftrag"), () => frappe.set_route("Form", "Sales Order", z.auftrag), __("Öffnen"));
				}
				if (z.plan) {
					frm.add_custom_button(__("Abschlagsplan"), () => frappe.set_route("Form", "Abschlagsplan", z.plan), __("Öffnen"));
				}
			});
		},
	});
})();
