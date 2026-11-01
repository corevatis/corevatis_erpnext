// Corevatis – Materialwirtschaft (Phase 2, P2.2): Material bestellen (Materialanforderung → Bestellung),
// Lieferung buchen (Bestellung → Wareneingang), Fahrzeug beladen (Umlagerung), Mindestbestand.
// Server: corevatis_erpnext.materialwirtschaft (Rollen Büro/Meister). Die Datei steht in doctype_js
// mehrerer Doctypes und in den Berichten „Nachbestellen“/„Fahrzeuglager“; sie richtet sich einmal ein.
frappe.provide("corevatis_erpnext.materialwirtschaft");

(function () {
	const M = corevatis_erpnext.materialwirtschaft;
	if (M._eingerichtet) return;
	M._eingerichtet = true;

	const PFAD = "corevatis_erpnext.materialwirtschaft.";
	const ROLLEN = ["Büro", "Meister", "System Manager", "Administrator"];
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
	const zahl = (w) => format_number(w || 0, null, 2);
	const darf = () => ROLLEN.some((r) => frappe.user.has_role(r));

	function link(doctype, name, label) {
		if (corevatis_erpnext.beleg_link) return corevatis_erpnext.beleg_link(doctype, name, label);
		return `<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}">${esc(label || name)}</a>`;
	}

	function hinweise(liste) {
		return (liste || [])
			.filter(Boolean)
			.map((h) => `<div class="alert alert-warning small">${esc(h)}</div>`)
			.join("");
	}

	// Select-Optionen: Frappe setzt Labels per .html() ein - Namen (Mitarbeiter, Lieferant) escapen
	function lager_optionen(liste) {
		return (liste || []).map((l) => ({ value: l.warehouse, label: esc(l.label) }));
	}

	function entfernen(frm, labels, gruppe) {
		for (const label of labels) {
			try {
				frm.remove_custom_button(__(label), __(gruppe));
			} catch (e) {
				// nicht vorhanden
			}
		}
	}

	// --- Material bestellen (Materialanforderung) ---------------------------------------------------

	M.bestellen_dialog = function (material_request, danach) {
		frappe.xcall(PFAD + "bestellung_vorbereiten", { material_request }).then((v) => {
			if (!v.positionen.length) {
				const entwuerfe = (v.entwuerfe || []).map((n) => link("Purchase Order", n)).join(", ");
				frappe.msgprint({
					title: __("Material bestellen"),
					indicator: "orange",
					message: entwuerfe
						? `${esc(__("Alle Positionen stehen in Bestellungen, die noch nicht freigegeben sind:"))} ${entwuerfe}`
						: esc(__("Alle Positionen sind schon bestellt.")),
				});
				return;
			}
			const lieferanten = (v.lieferanten || []).map((l) => ({ value: l.name, label: esc(l.supplier_name || l.name) }));
			const d = new frappe.ui.Dialog({
				title: __("Material bestellen ({0})", [material_request]),
				size: "large",
				fields: [
					{
						fieldname: "lager",
						fieldtype: "Select",
						label: __("Lieferung an Lager"),
						options: lager_optionen(v.lager),
						default: v.standard_lager,
						reqd: 1,
						description: __("Hauptlager oder direkt das Fahrzeug eines zugewiesenen Monteurs."),
					},
					{ fieldname: "einreichen", fieldtype: "Check", label: __("Bestellung sofort freigeben"), default: 0 },
					{ fieldname: "hinweis", fieldtype: "HTML" },
					{
						fieldname: "positionen",
						fieldtype: "Table",
						label: __("Positionen"),
						cannot_add_rows: true,
						in_place_edit: true,
						data: v.positionen.map((p) => ({
							material_request_item: p.material_request_item,
							item_code: p.item_code,
							item_name: p.item_name,
							menge: p.menge,
							uom: p.uom,
							lieferant: p.lieferant,
							preis: p.preis,
						})),
						fields: [
							{ fieldname: "item_code", fieldtype: "Link", options: "Item", label: __("Artikel"), in_list_view: 1, read_only: 1, columns: 2 },
							{ fieldname: "item_name", fieldtype: "Data", label: __("Bezeichnung"), in_list_view: 1, read_only: 1, columns: 3 },
							{ fieldname: "menge", fieldtype: "Float", label: __("Menge"), in_list_view: 1, columns: 1 },
							{ fieldname: "uom", fieldtype: "Data", label: __("Einheit"), in_list_view: 1, read_only: 1, columns: 1 },
							{ fieldname: "lieferant", fieldtype: "Link", options: "Supplier", label: __("Lieferant"), in_list_view: 1, columns: 2 },
							{ fieldname: "preis", fieldtype: "Currency", label: __("Preis"), in_list_view: 1, read_only: 1, columns: 1 },
							{ fieldname: "material_request_item", fieldtype: "Data", hidden: 1 },
						],
					},
				],
				primary_action_label: __("Bestellen"),
				primary_action(werte) {
					const zeilen = (werte.positionen || [])
						.filter((z) => flt(z.menge) > 0)
						.map((z) => ({ material_request_item: z.material_request_item, lieferant: z.lieferant, menge: flt(z.menge) }));
					if (!zeilen.length) {
						frappe.msgprint(__("Bitte mindestens eine Position mit Menge angeben."));
						return;
					}
					if (zeilen.some((z) => !z.lieferant)) {
						frappe.msgprint(__("Bitte für jede Position einen Lieferanten wählen."));
						return;
					}
					frappe
						.call({
							method: PFAD + "bestellen",
							args: { material_request, zeilen, lager: werte.lager, einreichen: werte.einreichen ? 1 : 0 },
							freeze: true,
							freeze_message: __("Bestellung wird angelegt …"),
						})
						.then((r) => {
							d.hide();
							M.bestellung_ergebnis(r.message || {});
							if (danach) danach(r.message || {});
						});
				},
			});
			const ohne = v.positionen.filter((p) => !p.lieferant).length;
			d.fields_dict.hinweis.$wrapper.html(
				((v.entwuerfe || []).length
					? `<div class="alert alert-info small">${esc(__("Noch nicht freigegebene Bestellungen zu dieser Anforderung:"))} ${v.entwuerfe
							.map((n) => link("Purchase Order", n))
							.join(", ")} ${esc(__("- deren Mengen sind hier schon abgezogen."))}</div>`
					: "") +
				(ohne
					? `<div class="alert alert-warning small">${esc(__("{0} Position(en) ohne Standardlieferant - bitte Lieferant wählen.", [ohne]))}</div>`
					: "") +
					(lieferanten.length
						? ""
						: `<div class="alert alert-warning small">${esc(__("Noch kein Lieferant angelegt: Stammdaten → Lieferanten."))}</div>`) +
					(v.projekt ? `<p class="small text-muted">${esc(__("Projekt"))}: ${link("Project", v.projekt)}</p>` : "")
			);
			if (!v.darf_bestellen) {
				d.get_primary_btn().prop("disabled", true);
				d.fields_dict.hinweis.$wrapper.append(`<div class="alert alert-danger small">${esc(__("Keine Berechtigung, Bestellungen anzulegen (Rolle Büro)."))}</div>`);
			}
			d.show();
		});
	};

	M.bestellung_ergebnis = function (m) {
		const zeilen = (m.bestellungen || [])
			.map(
				(b) =>
					`<tr><td>${link("Purchase Order", b.name)}</td><td>${esc(b.lieferant_name)}</td><td class="text-right">${esc(b.positionen)}</td><td class="text-right">${format_currency(
						flt(b.netto)
					)}</td><td>${esc(b.docstatus === 1 ? __("freigegeben") : __("Entwurf"))}</td></tr>`
			)
			.join("");
		frappe.msgprint({
			title: __("Bestellung angelegt"),
			indicator: "green",
			message:
				hinweise(m.hinweise) +
				(zeilen
					? `<p>${esc(__("Lieferung an"))}: ${esc(m.lager)}</p><table class="table table-sm small"><thead><tr><th>${__("Bestellung")}</th><th>${__(
							"Lieferant"
					  )}</th><th class="text-right">${__("Positionen")}</th><th class="text-right">${__("Netto")}</th><th>${__("Stand")}</th></tr></thead><tbody>${zeilen}</tbody></table><p class="small">${esc(
							__("Nächster Schritt: Bestellung prüfen (Preise) und freigeben, dann an den Lieferanten geben.")
					  )}</p>`
					: ""),
		});
	};

	// --- Lieferung buchen (Bestellung) ---------------------------------------------------------------

	M.lieferung_dialog = function (purchase_order, danach) {
		frappe.xcall(PFAD + "lieferung_vorbereiten", { purchase_order }).then((v) => {
			if (!v.positionen.length) {
				frappe.msgprint({ title: __("Lieferung buchen"), indicator: "orange", message: esc(__("Diese Bestellung ist vollständig geliefert.")) });
				return;
			}
			const d = new frappe.ui.Dialog({
				title: __("Lieferung buchen ({0})", [purchase_order]),
				size: "large",
				fields: [
					{ fieldname: "lager", fieldtype: "Select", label: __("Lager"), options: lager_optionen(v.lager), default: v.standard_lager, reqd: 1 },
					{ fieldname: "col1", fieldtype: "Column Break" },
					{ fieldname: "lieferschein", fieldtype: "Data", label: __("Lieferschein-Nr. des Lieferanten") },
					{ fieldname: "sec1", fieldtype: "Section Break" },
					{
						fieldname: "scan",
						fieldtype: "Data",
						label: __("Barcode scannen"),
						description: __("Scan oder EAN eingeben: zählt die gelieferte Menge der Position um 1 hoch."),
					},
					{ fieldname: "einreichen", fieldtype: "Check", label: __("Sofort buchen (Bestand erhöht sich)"), default: 1 },
					{ fieldname: "hinweis", fieldtype: "HTML" },
					{
						fieldname: "positionen",
						fieldtype: "Table",
						label: __("Positionen"),
						cannot_add_rows: true,
						in_place_edit: true,
						data: v.positionen.map((p) => ({
							purchase_order_item: p.purchase_order_item,
							item_code: p.item_code,
							item_name: p.item_name,
							bestellt: p.bestellt,
							geliefert: p.geliefert,
							menge: p.offen,
							uom: p.uom,
							eans: (p.eans || []).join(","),
						})),
						fields: [
							{ fieldname: "item_code", fieldtype: "Link", options: "Item", label: __("Artikel"), in_list_view: 1, read_only: 1, columns: 2 },
							{ fieldname: "item_name", fieldtype: "Data", label: __("Bezeichnung"), in_list_view: 1, read_only: 1, columns: 3 },
							{ fieldname: "bestellt", fieldtype: "Float", label: __("Bestellt"), in_list_view: 1, read_only: 1, columns: 1 },
							{ fieldname: "geliefert", fieldtype: "Float", label: __("Schon geliefert"), in_list_view: 1, read_only: 1, columns: 1 },
							{ fieldname: "menge", fieldtype: "Float", label: __("Jetzt geliefert"), in_list_view: 1, columns: 2 },
							{ fieldname: "uom", fieldtype: "Data", label: __("Einheit"), in_list_view: 1, read_only: 1, columns: 1 },
							{ fieldname: "purchase_order_item", fieldtype: "Data", hidden: 1 },
							{ fieldname: "eans", fieldtype: "Data", hidden: 1 },
						],
					},
				],
				primary_action_label: __("Lieferung buchen"),
				primary_action(werte) {
					const zeilen = (werte.positionen || []).map((z) => ({ purchase_order_item: z.purchase_order_item, menge: flt(z.menge) }));
					if (!zeilen.some((z) => z.menge > 0)) {
						frappe.msgprint(__("Bitte mindestens eine gelieferte Menge angeben."));
						return;
					}
					frappe
						.call({
							method: PFAD + "lieferung_buchen",
							args: { purchase_order, zeilen, lager: werte.lager, einreichen: werte.einreichen ? 1 : 0, lieferschein: werte.lieferschein || null },
							freeze: true,
							freeze_message: __("Lieferung wird gebucht …"),
						})
						.then((r) => {
							d.hide();
							M.lieferung_ergebnis(r.message || {});
							if (danach) danach(r.message || {});
						});
				},
			});
			d.fields_dict.hinweis.$wrapper.html(
				`<p class="small"><b>${esc(__("Lieferant"))}:</b> ${esc(v.lieferant_name || v.lieferant)}</p>` +
					`<p class="small text-muted">${esc(__("Teillieferung: nur die gelieferte Menge eintragen, der Rest bleibt offen."))}</p>` +
					(v.projekt ? `<p class="small text-muted">${esc(__("Projekt"))}: ${link("Project", v.projekt)}</p>` : "")
			);
			// Scan: EAN in der Tabelle suchen (Barcodes kamen mit), sonst Server fragen
			d.fields_dict.scan.$input.on("change", () => {
				const ean = (d.get_value("scan") || "").trim();
				if (!ean) return;
				const tabelle = d.fields_dict.positionen;
				const treffer = (tabelle.df.data || []).find((z) => (z.eans || "").split(",").includes(ean) || z.item_code === ean);
				const uebernehmen = (zeile) => {
					zeile.menge = flt(zeile.menge) + 1;
					tabelle.grid.refresh();
					d.set_value("scan", "");
				};
				if (treffer) {
					uebernehmen(treffer);
					return;
				}
				frappe.xcall(PFAD + "ean_suchen", { ean }).then((a) => {
					const z = a && (tabelle.df.data || []).find((x) => x.item_code === a.item_code);
					if (z) uebernehmen(z);
					else frappe.show_alert({ message: __("Kein Artikel dieser Bestellung zu {0}.", [ean]), indicator: "orange" });
					d.set_value("scan", "");
				});
			});
			if (!v.darf_buchen) {
				d.get_primary_btn().prop("disabled", true);
				d.fields_dict.hinweis.$wrapper.append(`<div class="alert alert-danger small">${esc(__("Keine Berechtigung, Lieferungen zu buchen (Rolle Büro)."))}</div>`);
			}
			d.show();
		});
	};

	M.lieferung_ergebnis = function (m) {
		const zeilen = (m.positionen || [])
			.map(
				(p) =>
					`<tr><td>${esc(p.item_code)}</td><td class="text-right">${zahl(p.menge)} ${esc(p.uom)}</td><td>${esc(p.lager)}</td><td class="text-right">${zahl(
						p.bestand_nachher
					)}</td></tr>`
			)
			.join("");
		frappe.msgprint({
			title: m.docstatus === 1 ? __("Lieferung gebucht") : __("Lieferung als Entwurf angelegt"),
			indicator: m.docstatus === 1 ? "green" : "orange",
			message:
				`<p>${esc(__("Lieferung"))}: ${link("Purchase Receipt", m.lieferung)} · ${esc(__("Bestellung"))}: ${link("Purchase Order", m.purchase_order)} ${esc(
					m.vollstaendig ? __("(vollständig geliefert)") : __("({0} Position(en) noch offen)", [m.offen])
				)}</p>` +
				(zeilen
					? `<table class="table table-sm small"><thead><tr><th>${__("Artikel")}</th><th class="text-right">${__("Menge")}</th><th>${__("Lager")}</th><th class="text-right">${__(
							"Bestand danach"
					  )}</th></tr></thead><tbody>${zeilen}</tbody></table>`
					: ""),
		});
	};

	// --- Fahrzeug beladen ----------------------------------------------------------------------------

	M.beladen_dialog = function (employee, von, bis, danach) {
		frappe.xcall(PFAD + "fahrzeug_vorschlag", { employee: employee || null, von: von || null, bis: bis || null }).then((v) => {
			const zeilen = (v.zeilen || []).filter((z) => z.vorschlag > 0 || z.fehlt > 0);
			const d = new frappe.ui.Dialog({
				title: __("Fahrzeug beladen"),
				size: "large",
				fields: [
					{ fieldname: "hinweis", fieldtype: "HTML" },
					{
						fieldname: "positionen",
						fieldtype: "Table",
						label: __("Umlagerung Hauptlager → Fahrzeug"),
						cannot_add_rows: false,
						in_place_edit: true,
						data: zeilen.map((z) => ({ item_code: z.item_code, item_name: z.item_name, bedarf: z.bedarf, fahrzeug: z.fahrzeug, hauptlager: z.hauptlager, menge: z.vorschlag, uom: z.uom })),
						fields: [
							{ fieldname: "item_code", fieldtype: "Link", options: "Item", label: __("Artikel"), in_list_view: 1, columns: 2, get_query: () => ({ filters: { is_stock_item: 1, disabled: 0 } }) },
							{ fieldname: "item_name", fieldtype: "Data", label: __("Bezeichnung"), in_list_view: 1, read_only: 1, columns: 3 },
							{ fieldname: "bedarf", fieldtype: "Float", label: __("Bedarf"), in_list_view: 1, read_only: 1, columns: 1 },
							{ fieldname: "fahrzeug", fieldtype: "Float", label: __("Auf dem Fahrzeug"), in_list_view: 1, read_only: 1, columns: 1 },
							{ fieldname: "hauptlager", fieldtype: "Float", label: __("Im Hauptlager"), in_list_view: 1, read_only: 1, columns: 1 },
							{ fieldname: "menge", fieldtype: "Float", label: __("Beladen"), in_list_view: 1, columns: 1 },
							{ fieldname: "uom", fieldtype: "Data", label: __("Einheit"), in_list_view: 1, read_only: 1, columns: 1 },
						],
					},
				],
				primary_action_label: __("Beladen buchen"),
				primary_action(werte) {
					const positionen = (werte.positionen || []).filter((z) => z.item_code && flt(z.menge) > 0).map((z) => ({ item_code: z.item_code, menge: flt(z.menge) }));
					if (!positionen.length) {
						frappe.msgprint(__("Bitte mindestens eine Position mit Menge angeben."));
						return;
					}
					frappe
						.call({ method: PFAD + "fahrzeug_beladen", args: { employee: v.employee, zeilen: positionen, einreichen: 1 }, freeze: true, freeze_message: __("Umlagerung wird gebucht …") })
						.then((r) => {
							d.hide();
							const m = r.message || {};
							const tabelle = (m.positionen || [])
								.map((p) => `<tr><td>${esc(p.item_code)}</td><td class="text-right">${zahl(p.menge)} ${esc(p.uom)}</td><td class="text-right">${zahl(p.fahrzeug_nachher)}</td></tr>`)
								.join("");
							frappe.msgprint({
								title: __("Fahrzeug beladen"),
								indicator: "green",
								message: `<p>${esc(__("Materialbuchung"))}: ${link("Stock Entry", m.stock_entry)} (${esc(m.hauptlager)} → ${esc(m.fahrzeuglager)})</p><table class="table table-sm small"><thead><tr><th>${__(
									"Artikel"
								)}</th><th class="text-right">${__("Menge")}</th><th class="text-right">${__("Auf dem Fahrzeug danach")}</th></tr></thead><tbody>${tabelle}</tbody></table>`,
							});
							if (danach) danach(m);
						});
				},
			});
			d.fields_dict.hinweis.$wrapper.html(
				`<p class="small"><b>${esc(v.employee_name)}</b> · ${esc(v.hauptlager || "-")} → ${esc(v.fahrzeuglager)}</p>` +
					`<p class="small text-muted">${esc(__("Baustellen {0} bis {1}: {2}", [frappe.datetime.str_to_user(v.von), frappe.datetime.str_to_user(v.bis), (v.projekte || []).join(", ") || __("keine zugewiesen")]))}</p>` +
					hinweise(v.hinweise) +
					(zeilen.length ? "" : `<p class="small">${esc(__("Nichts fehlt - Zeilen lassen sich trotzdem ergänzen."))}</p>`)
			);
			if (!v.darf_beladen) {
				d.get_primary_btn().prop("disabled", true);
				d.fields_dict.hinweis.$wrapper.append(`<div class="alert alert-danger small">${esc(__("Keine Berechtigung, Materialbuchungen anzulegen."))}</div>`);
			}
			d.show();
		});
	};

	// --- Mindestbestand, Nachbestellen -------------------------------------------------------------

	M.mindestbestand_dialog = function (danach, vorgabe) {
		vorgabe = vorgabe || {};
		const d = new frappe.ui.Dialog({
			title: __("Mindestbestand setzen"),
			fields: [
				{ fieldname: "item_code", fieldtype: "Link", options: "Item", label: __("Artikel"), reqd: 1, default: vorgabe.item_code, get_query: () => ({ filters: { is_stock_item: 1, disabled: 0 } }) },
				{ fieldname: "warehouse", fieldtype: "Link", options: "Warehouse", label: __("Lager"), reqd: 1, default: vorgabe.warehouse, get_query: () => ({ filters: { is_group: 0, disabled: 0 } }) },
				{ fieldname: "mindestbestand", fieldtype: "Float", label: __("Mindestbestand"), reqd: 1, default: vorgabe.mindestbestand, description: __("Fällt die verfügbare Menge darunter, erscheint der Artikel unter „Nachbestellen“. 0 und 0 = Eintrag löschen.") },
				{ fieldname: "nachbestellmenge", fieldtype: "Float", label: __("Nachbestellmenge"), default: vorgabe.nachbestellmenge || 0, description: __("Menge je Nachbestellung; leer = Mindestbestand.") },
				{
					fieldname: "art",
					fieldtype: "Select",
					label: __("Art"),
					options: [
						{ value: "", label: __("automatisch (Fahrzeug: Umlagerung, sonst Bestellung)") },
						{ value: "Purchase", label: __("Bestellung beim Lieferanten") },
						{ value: "Transfer", label: __("Umlagerung aus dem Hauptlager") },
					],
					default: vorgabe.art || "",
				},
			],
			primary_action_label: __("Speichern"),
			primary_action(w) {
				frappe
					.call({ method: PFAD + "mindestbestand_setzen", args: { item_code: w.item_code, warehouse: w.warehouse, mindestbestand: w.mindestbestand, nachbestellmenge: w.nachbestellmenge || 0, art: w.art || null }, freeze: true })
					.then(() => {
						d.hide();
						frappe.show_alert({ message: __("Mindestbestand gespeichert."), indicator: "green" });
						if (danach) danach();
					});
			},
		});
		d.show();
	};

	M.nachbestellen_dialog = function (report) {
		const daten = (report.data || []).filter((z) => flt(z.nachbestellmenge) > 0);
		if (!daten.length) {
			frappe.msgprint({ title: __("Bedarf anlegen"), indicator: "orange", message: esc(__("Keine Zeile mit Nachbestellmenge.")) });
			return;
		}
		const d = new frappe.ui.Dialog({
			title: __("Bedarf anlegen"),
			size: "large",
			fields: [
				{ fieldname: "hinweis", fieldtype: "HTML" },
				{
					fieldname: "positionen",
					fieldtype: "Table",
					label: __("Positionen"),
					cannot_add_rows: true,
					in_place_edit: true,
					data: daten.map((z) => ({ item_code: z.item_code, item_name: z.item_name, lager: z.lager, menge: z.nachbestellmenge, uom: z.uom, art: z.art, art_text: z.art_text })),
					fields: [
						{ fieldname: "item_code", fieldtype: "Link", options: "Item", label: __("Artikel"), in_list_view: 1, read_only: 1, columns: 2 },
						{ fieldname: "item_name", fieldtype: "Data", label: __("Bezeichnung"), in_list_view: 1, read_only: 1, columns: 3 },
						{ fieldname: "lager", fieldtype: "Link", options: "Warehouse", label: __("Lager"), in_list_view: 1, read_only: 1, columns: 2 },
						{ fieldname: "menge", fieldtype: "Float", label: __("Menge"), in_list_view: 1, columns: 1 },
						{ fieldname: "uom", fieldtype: "Data", label: __("Einheit"), in_list_view: 1, read_only: 1, columns: 1 },
						{ fieldname: "art_text", fieldtype: "Data", label: __("Art"), in_list_view: 1, read_only: 1, columns: 1 },
						{ fieldname: "art", fieldtype: "Data", hidden: 1 },
					],
				},
			],
			primary_action_label: __("Materialanforderungen anlegen"),
			primary_action(w) {
				const zeilen = (w.positionen || []).filter((z) => flt(z.menge) > 0).map((z) => ({ item_code: z.item_code, lager: z.lager, menge: flt(z.menge), art: z.art }));
				frappe.call({ method: PFAD + "nachbestellung_anlegen", args: { zeilen }, freeze: true }).then((r) => {
					d.hide();
					const liste = ((r.message || {}).material_requests || [])
						.map((m) => `<li>${link("Material Request", m.name)}: ${esc(m.typ === "Material Transfer" ? __("Umlagerung") : __("Bestellung"))} → ${esc(m.lager)}, ${esc(m.positionen)} ${esc(__("Positionen"))}</li>`)
						.join("");
					frappe.msgprint({
						title: __("Bedarf angelegt"),
						indicator: "green",
						message: `<ul>${liste}</ul><p class="small">${esc(__("Nächster Schritt: Materialanforderung öffnen → „Material bestellen“ bzw. „Fahrzeug beladen“."))}</p>`,
					});
					report.refresh();
				});
			},
		});
		d.fields_dict.hinweis.$wrapper.html(`<p class="small text-muted">${esc(__("Je Lager und Art entsteht eine Materialanforderung (Entwurf)."))}</p>`);
		d.show();
	};

	// --- Formulare -----------------------------------------------------------------------------------

	const MR_WEG = ["Request for Quotation", "Supplier Quotation", "Work Order", "Pick List", "Purchase Order", "Material Transfer", "Material Transfer (In Transit)", "Issue Material"];
	const PO_WEG = ["Payment", "Payment Request", "Subscription", "Subcontracting Order", "Inter Company Sales Order"];

	function anforderung_zustand(frm) {
		const token = `${frm.doc.name}/${frm.doc.modified}`;
		frm.__cv_mw_token = token;
		frappe.xcall(PFAD + "anforderung_stand", { material_request: frm.doc.name }).then((z) => {
			if (!z || frm.__cv_mw_token !== token) return;
			const entwuerfe = (z.entwuerfe || []).map((n) => link("Purchase Order", n)).join(", ");
			if (z.offen) {
				frm.set_intro(
					esc(
						frm.doc.docstatus === 0
							? __("Materialbedarf (Entwurf): Mengen prüfen, dann „Material bestellen“ - das reicht den Bedarf ein und legt je Lieferant eine Bestellung an.")
							: __("Materialbedarf eingereicht: „Material bestellen“ legt je Lieferant eine Bestellung an.")
					) + (entwuerfe ? ` ${esc(__("Bestellentwürfe"))}: ${entwuerfe}` : ""),
					"blue"
				);
				if (!frm.is_dirty() && z.darf_bestellen) {
					frm.page.set_primary_action(__("Material bestellen"), () => M.bestellen_dialog(frm.doc.name, () => frm.reload_doc()));
				}
			} else if (entwuerfe) {
				frm.set_intro(`${esc(__("Alles in Bestellungen, die noch nicht freigegeben sind:"))} ${entwuerfe} ${esc(__("- dort Preise prüfen und freigeben."))}`, "orange");
			} else if (frm.doc.docstatus === 1) {
				frm.set_intro(esc(__("Alles bestellt - Lieferungen werden an der Bestellung gebucht.")), "green");
			}
		});
	}

	frappe.ui.form.on("Material Request", {
		refresh(frm) {
			if (frm.is_new() || !darf()) return;
			const typ = frm.doc.material_request_type;
			const offen = (frm.doc.items || []).some((z) => flt(z.stock_qty || z.qty) - Math.max(flt(z.ordered_qty), flt(z.received_qty)) > 0);
			if (frm.doc.docstatus < 2 && frm.doc.status !== "Stopped") {
				if (typ === "Purchase") {
					anforderung_zustand(frm);
				} else if (typ === "Material Transfer" && offen && frm.doc.docstatus === 1) {
					frm.set_intro(esc(__("Umlagerung angefordert: „Fahrzeug beladen“ erzeugt die Materialbuchung vom Hauptlager ins Fahrzeug.")), "blue");
					frm.add_custom_button(__("Fahrzeug beladen"), () =>
						frappe.model.open_mapped_doc({ method: "erpnext.stock.doctype.material_request.material_request.make_stock_entry", frm })
					);
				} else if (typ === "Material Transfer" && !offen && frm.doc.docstatus === 1) {
					frm.set_intro(esc(__("Umlagerung vollständig gebucht.")), "green");
				}
			}
			if (!frappe.user.has_role("System Manager")) {
				for (const ms of [0, 300, 1200]) setTimeout(() => entfernen(frm, MR_WEG, "Create"), ms);
			}
		},
	});

	frappe.ui.form.on("Purchase Order", {
		refresh(frm) {
			if (frm.is_new() || !darf()) return;
			if (frm.doc.docstatus === 0) {
				frm.set_intro(esc(__("Bestellung (Entwurf) bei {0}: Preise und Liefertermin prüfen, dann „Freigeben“ und an den Lieferanten geben.", [frm.doc.supplier_name || frm.doc.supplier])), "blue");
			} else if (frm.doc.docstatus === 1 && !["Closed", "Completed", "Delivered"].includes(frm.doc.status)) {
				const prozent = flt(frm.doc.per_received);
				// nach Positionen zählen: der ERPNext-Prozentwert ist mengengewichtet (0,7 m³ von 1.100 Einheiten = 0 %)
				const positionen = frm.doc.items || [];
				const voll = positionen.filter((z) => flt(z.qty) > 0 && flt(z.received_qty) >= flt(z.qty)).length;
				const teil = positionen.filter((z) => flt(z.received_qty) > 0 && flt(z.received_qty) < flt(z.qty)).length;
				frm.set_intro(
					esc(
						voll + teil > 0
							? __("Bestellung bei {0} freigegeben, {1} von {2} Positionen geliefert{3}. Weitere Lieferung über „Lieferung buchen“.", [
									frm.doc.supplier_name || frm.doc.supplier,
									voll,
									positionen.length,
									teil ? __(" ({0} teilweise)", [teil]) : "",
								])
							: __("Bestellung bei {0} freigegeben. Kommt die Ware: „Lieferung buchen“ (Lieferschein abgleichen, Lager wählen).", [frm.doc.supplier_name || frm.doc.supplier])
					),
					"blue"
				);
				if (prozent < 100 && !frm.is_dirty()) {
					frm.page.set_primary_action(__("Lieferung buchen"), () => M.lieferung_dialog(frm.doc.name, () => frm.reload_doc()));
				}
			} else if (frm.doc.docstatus === 1) {
				frm.set_intro(esc(__("Bestellung vollständig geliefert.")), "green");
			}
			if (!frappe.user.has_role("System Manager")) {
				for (const ms of [0, 300, 1200]) setTimeout(() => entfernen(frm, PO_WEG, "Create"), ms);
			}
		},
	});

	frappe.ui.form.on("Purchase Receipt", {
		refresh(frm) {
			if (frm.is_new() || !darf()) return;
			const bestellungen = [...new Set((frm.doc.items || []).map((z) => z.purchase_order).filter(Boolean))];
			const text =
				frm.doc.docstatus === 0
					? __("Lieferung (Entwurf) von {0}: Mengen mit dem Lieferschein abgleichen, dann „Buchen“ - der Bestand steigt.", [frm.doc.supplier_name || frm.doc.supplier])
					: __("Lieferung von {0} gebucht, Bestand im Lager {1} erhöht.", [frm.doc.supplier_name || frm.doc.supplier, frm.doc.set_warehouse || (frm.doc.items[0] || {}).warehouse || ""]);
			frm.set_intro(esc(text) + (bestellungen.length ? ` ${esc(__("Bestellung"))}: ${bestellungen.map((b) => link("Purchase Order", b)).join(", ")}` : ""), frm.doc.docstatus === 1 ? "green" : "blue");
		},
	});

	// Projekt: Knopf „Material bestellen“ im Menü „Planung“, wenn ein Bedarf offen ist
	frappe.ui.form.on("Project", {
		refresh(frm) {
			if (frm.is_new() || !darf()) return;
			frappe.xcall(PFAD + "projekt_material", { project: frm.doc.name }).then((m) => {
				const offen = (m.anforderungen || []).filter((a) => a.material_request_type === "Purchase" && a.offen > 0);
				if (offen.length) {
					frm.add_custom_button(
						__("Material bestellen"),
						() => M.bestellen_dialog(offen[0].name, () => frm.reload_doc()),
						__("Planung")
					);
				}
			});
		},
	});
})();
