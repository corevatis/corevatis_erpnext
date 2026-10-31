// Corevatis – Mahnwesen und Zahlungseingang (Aufgabe 7.5): Dialoge „Mahnen“ (Rechnung, Offene Posten),
// „Kontoauszug einlesen“ und „Zuordnen“ (Zahlungseingang, Bank Transaction). Bibliothek: in doctype_js
// für Sales Invoice, Bank Transaction und Dunning, von den Script Reports per frappe.require geladen.
// Server: corevatis_erpnext.abrechnung (Rolle Büro); Beträge rechnet nur der Server.
frappe.provide("corevatis_erpnext.abrechnung");

(function () {
	const A = corevatis_erpnext.abrechnung;
	if (A._mahnung_zahlung) return;
	A._mahnung_zahlung = true;
	const METHODE = "corevatis_erpnext.abrechnung.";
	const ROLLEN = ["Büro", "System Manager", "Administrator"];
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
	const geld = (w, waehrung) => format_currency(w || 0, waehrung || frappe.boot.sysdefaults.currency);
	const datum = (d) => (d ? frappe.datetime.str_to_user(d) : "");
	const darf = () => ROLLEN.some((r) => frappe.user.has_role(r));
	const link = (doctype, name, label) =>
		corevatis_erpnext.beleg_link ? corevatis_erpnext.beleg_link(doctype, name, label) : `<a href="/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}">${esc(label || name)}</a>`;

	function fehlermeldung(titel) {
		return (r) => {
			// frappe.call zeigt die Servermeldung schon; hier nur für den Aufrufer
			return r;
		};
	}

	// --- Mahnen ---------------------------------------------------------------------------------
	A.mahnen_dialog = function (sales_invoice, nach) {
		if (!darf()) {
			frappe.msgprint(__("Mahnungen erstellt nur das Büro."));
			return;
		}
		frappe.xcall(METHODE + "mahnvorschlag", { sales_invoice }).then((v) => {
			const r = v.rechnung || {};
			const vs = v.vorschlag || {};
			const stand = v.stand;
			const stufen = (v.stufen || []).map((s) => ({ value: String(s.stufe), label: `${s.label}${s.gebuehr ? ` (${__("Gebühr")} ${geld(s.gebuehr, r.currency)})` : ""}` }));
			const kopf = `<p>${__("Rechnung {0} ({1}): {2} brutto, offen {3}, fällig am {4}.", [
				link("Sales Invoice", r.name),
				esc(r.kunde || ""),
				geld(r.brutto, r.currency),
				geld(r.offen, r.currency),
				esc(datum(r.faellig)),
			])}</p>
			<p>${stand ? __("Bisher: {0} ({1}) vom {2}.", [esc(stand.label || ""), link("Dunning", stand.mahnung), esc(datum(stand.datum))]) : __("Noch nicht gemahnt.")}</p>
			<p>${
				vs.stufe == null
					? `<span class="indicator-pill gray">${esc(vs.grund || __("Kein Vorschlag"))}</span>`
					: vs.moeglich
					  ? `<span class="indicator-pill orange">${esc(__("Vorschlag: {0}", [vs.label]))}</span> <span class="small text-muted">${esc(vs.grund || "")}</span>`
					  : `<span class="indicator-pill blue">${esc(__("Noch nicht fällig: {0}", [vs.grund || ""]))}</span>`
			}</p>`;
			const d = new frappe.ui.Dialog({
				title: __("Mahnen"),
				fields: [
					{ fieldtype: "HTML", fieldname: "kopf", options: kopf },
					{
						fieldtype: "Select",
						fieldname: "stufe",
						label: __("Mahnstufe"),
						options: stufen,
						default: vs.stufe != null ? String(vs.stufe) : stand && stand.stufe < 2 ? String(stand.stufe + 1) : "0",
						reqd: 1,
					},
					{
						fieldtype: "Check",
						fieldname: "bestaetigen",
						label: __("Auch abweichend vom Vorschlag bzw. vor Ablauf der Frist mahnen"),
						default: 0,
					},
					{
						fieldtype: "HTML",
						fieldname: "fuss",
						options: `<p class="small text-muted">${esc(
							__("Die Mahnung wird gebucht und steht zum Druck bereit („Mahnung Corevatis“); zahlbar bis {0}. Ein Versand per E-Mail erfolgt nicht.", [datum(v.zahlungsfrist)])
						)}</p>`,
					},
				],
				primary_action_label: __("Mahnung erstellen"),
				primary_action(values) {
					d.hide();
					frappe
						.call({
							method: METHODE + "mahnung_erstellen",
							args: { sales_invoice, stufe: values.stufe, bestaetigen: values.bestaetigen ? 1 : 0 },
							freeze: true,
							freeze_message: __("Mahnung wird erstellt …"),
						})
						.then((res) => {
							const m = res.message || {};
							frappe.msgprint({
								title: __("{0} erstellt", [m.label || __("Mahnung")]),
								indicator: "green",
								message: `<p>${esc(m.text || "")}</p>`,
								primary_action: {
									label: __("Mahnung drucken"),
									action() {
										frappe.hide_msgprint();
										frappe.set_route("print", "Dunning", m.mahnung);
									},
								},
							});
							if (typeof nach === "function") nach(m);
						});
				},
			});
			d.show();
		});
	};

	// --- Kontoauszug einlesen -------------------------------------------------------------------
	A.kontoauszug_dialog = function (nach) {
		if (!darf()) {
			frappe.msgprint(__("Kontoauszüge liest nur das Büro ein."));
			return;
		}
		const d = new frappe.ui.Dialog({
			title: __("Kontoauszug einlesen"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "hinweis",
					options: `<p class="small text-muted">${esc(
						__("CSV-Export der Bank (Sparkasse, VR-Bank; Trennzeichen und Spalten werden erkannt, eigenes Mapping in den Abrechnungs-Einstellungen) oder CAMT.053 als XML, höchstens 5 MB. Die Datei bleibt privat und wird nach dem Einlesen gelöscht; die Buchungen stehen danach als Bankbuchungen in der Liste.")
					)}</p>`,
				},
				{
					fieldtype: "Attach",
					fieldname: "datei",
					label: __("Kontoauszug (CSV oder CAMT-XML)"),
					reqd: 1,
					options: { restrictions: { allowed_file_types: [".csv", ".txt", ".xml"], max_file_size: 5 * 1024 * 1024 }, make_attachments_public: false },
				},
				{ fieldtype: "Link", fieldname: "bankkonto", label: __("Bankkonto"), options: "Bank Account", get_query: () => ({ filters: { is_company_account: 1 } }) },
				{ fieldtype: "Check", fieldname: "nur_gutschriften", label: __("Nur Zahlungseingänge (Gutschriften) einlesen"), default: 1 },
			],
			primary_action_label: __("Einlesen"),
			primary_action(values) {
				d.hide();
				frappe
					.call({
						method: METHODE + "kontoauszug_importieren",
						args: { datei: values.datei, bankkonto_name: values.bankkonto || null, nur_gutschriften: values.nur_gutschriften ? 1 : 0 },
						freeze: true,
						freeze_message: __("Kontoauszug wird eingelesen …"),
					})
					.then((res) => {
						const m = res.message || {};
						const mit = (m.vorschlaege || []).filter((v) => v.bester).length;
						const zeilen = [
							__("{0} Buchungen gelesen ({1}), {2} neu angelegt, {3} Dubletten übersprungen, {4} Abgänge übersprungen.", [
								m.gelesen || 0,
								(m.format || "").toUpperCase(),
								(m.angelegt || []).length,
								(m.dubletten || []).length,
								m.abgaenge || 0,
							]),
							(m.angelegt || []).length ? __("{0} der neuen Buchungen haben einen Zuordnungsvorschlag.", [mit]) : "",
							...(m.fehler || []).map((f) => `<span class="text-danger">${esc(f)}</span>`),
							...(m.hinweise || []).map((h) => `<span class="text-muted">${esc(h)}</span>`),
						].filter(Boolean);
						frappe.msgprint({ title: __("Kontoauszug eingelesen"), indicator: (m.fehler || []).length ? "orange" : "green", message: zeilen.map((z) => `<div>${z}</div>`).join("") });
						if (typeof nach === "function") nach(m);
					});
			},
		});
		d.show();
	};

	// --- Zuordnen -------------------------------------------------------------------------------
	function kandidaten_html(v) {
		if (!(v.kandidaten || []).length) {
			return `<p class="text-muted">${esc(__("Keine passende offene Rechnung gefunden - unten eine Rechnung wählen oder die Buchung ignorieren."))}</p>`;
		}
		const rows = v.kandidaten
			.map((k, i) => {
				const farbe = { sicher: "green", wahrscheinlich: "blue", "möglich": "orange" }[k.sicherheit] || "gray";
				return `<tr>
					<td><input type="radio" name="cv-kandidat" value="${esc(k.rechnung)}" data-betrag="${k.vorschlag_betrag}" ${i === 0 ? "checked" : ""}></td>
					<td>${link("Sales Invoice", k.rechnung)}<br><span class="small text-muted">${esc(k.art || "")}</span></td>
					<td>${esc(k.kunde_name || "")}</td>
					<td class="text-right">${geld(k.offen, v.currency)}</td>
					<td><span class="indicator-pill ${farbe}">${esc(__(k.sicherheit))}</span><br><span class="small text-muted">${esc((k.gruende || []).join("; "))}</span></td>
				</tr>`;
			})
			.join("");
		return `<table class="table table-sm small"><thead><tr><th></th><th>${__("Rechnung")}</th><th>${__("Kunde")}</th><th class="text-right">${__("Offen")}</th><th>${__("Sicherheit")}</th></tr></thead><tbody>${rows}</tbody></table>`;
	}

	A.zuordnen_dialog = function (bank_transaction, nach) {
		if (!darf()) {
			frappe.msgprint(__("Zahlungen ordnet nur das Büro zu."));
			return;
		}
		frappe.xcall(METHODE + "vorschlaege", { bank_transaction }).then((liste) => {
			const v = (liste || [])[0];
			if (!v) {
				frappe.msgprint(__("Die Buchung {0} ist nicht mehr offen.", [bank_transaction]));
				if (typeof nach === "function") nach();
				return;
			}
			const kopf = `<p><b>${__("Gutschrift {0} vom {1}", [geld(v.betrag, v.currency), esc(datum(v.datum))])}</b> ${
				v.gutschrift !== v.betrag ? `<span class="small text-muted">(${__("von {0}", [geld(v.gutschrift, v.currency)])})</span>` : ""
			}<br>${esc(v.zahler || "")}${v.iban ? ` · ${esc(v.iban)}` : ""}<br><span class="small">${esc(v.verwendungszweck || "")}</span></p>${kandidaten_html(v)}`;
			const bester = v.bester || {};
			const d = new frappe.ui.Dialog({
				title: __("Zahlung zuordnen"),
				size: "large",
				fields: [
					{ fieldtype: "HTML", fieldname: "kopf", options: kopf },
					{ fieldtype: "Section Break", label: __("Zuordnung") },
					{
						fieldtype: "Link",
						fieldname: "rechnung",
						label: __("Rechnung"),
						options: "Sales Invoice",
						reqd: 1,
						default: bester.rechnung || null,
						get_query: () => ({ filters: { docstatus: 1, outstanding_amount: [">", 0], is_return: 0 } }),
					},
					{ fieldtype: "Column Break" },
					{
						fieldtype: "Currency",
						fieldname: "betrag",
						label: __("Betrag (Teilzahlung möglich)"),
						default: bester.vorschlag_betrag != null ? bester.vorschlag_betrag : v.betrag,
						description: __("Höchstens der offene Betrag der Buchung und der Rechnung; der Server prüft."),
					},
				],
				primary_action_label: __("Zuordnen"),
				primary_action(values) {
					d.hide();
					frappe
						.call({
							method: METHODE + "zuordnen",
							args: { bank_transaction, sales_invoice: values.rechnung, betrag: values.betrag },
							freeze: true,
							freeze_message: __("Zahlung wird gebucht …"),
						})
						.then((res) => {
							const m = res.message || {};
							frappe.show_alert({ message: __("{0}: {1}", [m.rechnung, m.status === "bezahlt" ? __("bezahlt") : __("teilbezahlt")]), indicator: "green" });
							frappe.msgprint({ title: __("Zahlung zugeordnet"), indicator: "green", message: `<p>${esc(m.text || "")}</p>` });
							if (typeof nach === "function") nach(m);
						});
				},
				secondary_action_label: __("Ignorieren"),
				secondary_action() {
					d.hide();
					A.ignorieren(bank_transaction, 0, nach);
				},
			});
			d.show();
			// Auswahl in der Tabelle in die Felder übernehmen
			d.$wrapper.find("input[name=cv-kandidat]").on("change", function () {
				d.set_value("rechnung", this.value);
				d.set_value("betrag", parseFloat(this.dataset.betrag));
			});
		});
	};

	A.ignorieren = function (bank_transaction, aufheben, nach) {
		if (!darf()) return;
		if (aufheben) {
			frappe.call({ method: METHODE + "ignorieren", args: { bank_transaction, aufheben: 1 } }).then(() => {
				if (typeof nach === "function") nach();
			});
			return;
		}
		frappe.prompt(
			[{ fieldtype: "Small Text", fieldname: "grund", label: __("Notiz (warum keine Rechnung dahintersteht)") }],
			(values) => {
				frappe.call({ method: METHODE + "ignorieren", args: { bank_transaction, grund: values.grund || null } }).then(() => {
					frappe.show_alert({ message: __("Buchung ignoriert"), indicator: "blue" });
					if (typeof nach === "function") nach();
				});
			},
			__("Buchung ignorieren"),
			__("Ignorieren")
		);
	};

	// --- Formulare ------------------------------------------------------------------------------
	frappe.ui.form.on("Sales Invoice", {
		refresh(frm) {
			if (frm.is_new() || frm.doc.docstatus !== 1 || frm.doc.is_return || !(frm.doc.outstanding_amount > 0) || !darf()) return;
			frm.add_custom_button(__("Mahnen"), () => A.mahnen_dialog(frm.doc.name, () => frm.reload_doc()));
		},
	});

	frappe.ui.form.on("Bank Transaction", {
		refresh(frm) {
			if (frm.is_new() || frm.doc.docstatus !== 1 || !darf()) return;
			if (frm.doc.deposit > 0 && frm.doc.unallocated_amount > 0) {
				frm.add_custom_button(__("Rechnung zuordnen"), () => A.zuordnen_dialog(frm.doc.name, () => frm.reload_doc()));
				frm.add_custom_button(frm.doc.cv_ignoriert ? __("Wieder öffnen") : __("Ignorieren"), () =>
					A.ignorieren(frm.doc.name, frm.doc.cv_ignoriert ? 1 : 0, () => frm.reload_doc())
				);
			}
		},
	});

	frappe.ui.form.on("Dunning", {
		refresh(frm) {
			if (frm.is_new()) return;
			const stufe = { 0: __("Zahlungserinnerung"), 1: __("1. Mahnung"), 2: __("2. Mahnung") }[frm.doc.cv_mahnstufe];
			if (!stufe) return;
			const text =
				frm.doc.docstatus === 1
					? __("{0} vom {1} - zahlbar bis {2}. Zum Druck oben „Drucken“ (Mahnung Corevatis); kein Versand per E-Mail.", [
							stufe,
							datum(frm.doc.posting_date),
							datum(frm.doc.cv_zahlungsfrist),
					  ])
					: __("{0} - Entwurf. Prüfen und buchen, dann drucken.", [stufe]);
			frm.set_intro(esc(text), frm.doc.status === "Resolved" ? "green" : "blue");
		},
	});
})();
