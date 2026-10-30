// Corevatis – Prozessleiste im Formularkopf (Lead, Objekt, Aufmaß, Befund, Angebot, Auftrag,
// Projekt, Rechnung): Anfrage → Termin → Befund/Aufmaß → Angebot → Auftrag → Ausführung →
// Abnahme → Rechnung. Server: corevatis_erpnext.prozess.status (Rechte je Datensatz).
// Jeder Schritt springt zu seinem Beleg oder bietet die nächste Aktion an; die Aktionen sind die
// vorhandenen Dialoge (Termin anlegen 1.3, Angebot erzeugen 4.1, Angebot annehmen 4.4).
// Die Datei steht in doctype_js mehrerer Doctypes; sie richtet sich nur beim ersten Laden ein.
frappe.provide("corevatis_erpnext.prozess");

(function () {
	const P = corevatis_erpnext.prozess;
	if (P._eingerichtet) return;
	P._eingerichtet = true;

	const METHODE = "corevatis_erpnext.prozess.status";
	const DOCTYPES = [
		"Lead",
		"Objekt",
		"Aufmass",
		"Befund",
		"Quotation",
		"Sales Order",
		"Project",
		"Abnahmeprotokoll",
		"Sales Invoice",
	];
	const SYMBOL = { erledigt: "✓", aktiv: "●", offen: "○", entfaellt: "–", verloren: "✕" };
	const AMPEL = { rot: "red", gelb: "orange", "grün": "green", grau: "gray" };
	const esc = (s) => frappe.utils.escape_html(s === null || s === undefined ? "" : String(s));

	function css() {
		if (document.getElementById("cv-prozessleiste-css")) return;
		const style = document.createElement("style");
		style.id = "cv-prozessleiste-css";
		style.textContent = `
.cv-pl { margin: 0 0 var(--margin-md, 12px); padding: 10px 12px; border: 1px solid var(--border-color);
	border-radius: var(--border-radius-md, 8px); background: var(--fg-color); }
.cv-pl-kopf { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 8px;
	font-size: var(--text-sm); color: var(--text-muted); }
.cv-pl-kopf .cv-pl-titel { font-weight: 600; color: var(--text-color); }
.cv-pl-kopf .cv-pl-naechste { margin-left: auto; }
.cv-pl-kette { display: flex; flex-wrap: wrap; gap: 4px; align-items: stretch; }
.cv-pl-schritt { display: flex; flex-direction: column; min-width: 92px; flex: 1 1 92px; padding: 6px 8px;
	border-radius: var(--border-radius, 6px); border: 1px solid var(--border-color); background: var(--control-bg);
	color: var(--text-muted); font-size: var(--text-xs, 11px); text-align: left; cursor: default; line-height: 1.3; }
.cv-pl-schritt.cv-pl-klickbar { cursor: pointer; }
.cv-pl-schritt.cv-pl-klickbar:hover { border-color: var(--gray-500, #98a1a9); }
.cv-pl-schritt .cv-pl-label { font-size: var(--text-sm, 12px); font-weight: 600; color: var(--text-color);
	white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cv-pl-schritt { overflow: hidden; }
.cv-pl-schritt .cv-pl-text { margin-top: 2px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box;
	-webkit-line-clamp: 2; -webkit-box-orient: vertical; }
.cv-pl-schritt.cv-pl-erledigt { background: var(--bg-green, #e4f5e9); border-color: transparent; }
.cv-pl-schritt.cv-pl-erledigt .cv-pl-label { color: var(--text-on-green, #16794c); }
.cv-pl-schritt.cv-pl-aktuell { background: var(--bg-blue, #e8f2fd); border-color: var(--blue-500, #2490ef);
	box-shadow: 0 0 0 1px var(--blue-500, #2490ef) inset; }
.cv-pl-schritt.cv-pl-aktuell .cv-pl-label { color: var(--text-on-blue, #1366ae); }
.cv-pl-schritt.cv-pl-verloren { background: var(--bg-red, #fff0f0); border-color: transparent; }
.cv-pl-schritt.cv-pl-verloren .cv-pl-label { color: var(--text-on-red, #cc2929); }
.cv-pl-schritt.cv-pl-entfaellt { opacity: 0.6; }
.cv-pl-schritt.cv-pl-gesperrt { opacity: 0.55; }
.cv-pl-schritt .cv-pl-aktion { margin-top: 4px; color: var(--text-on-blue, #1366ae); font-weight: 600; }
.cv-pl-schritt.cv-pl-hier { outline: 2px dashed var(--gray-400, #c0c6cc); outline-offset: 1px; }
@media (max-width: 767px) { .cv-pl-schritt { min-width: 70px; flex-basis: 70px; } .cv-pl-schritt .cv-pl-text { display: none; } }
`;
		document.head.appendChild(style);
	}

	function container(frm) {
		const wrapper = $(frm.layout && frm.layout.wrapper);
		if (!wrapper.length) return null;
		let el = wrapper.children(".cv-pl");
		if (!el.length) {
			el = $('<div class="cv-pl"></div>');
			wrapper.prepend(el);
		}
		return el;
	}

	function pfad(name) {
		return (name || "").split(".").reduce((obj, key) => (obj ? obj[key] : undefined), window);
	}

	function im_formular(frm, js) {
		const fn = pfad(js);
		if (typeof fn === "function") {
			fn(frm);
		} else {
			frappe.msgprint(__("Diese Aktion steht im Formular {0} nicht zur Verfügung.", [__(frm.doctype)]));
		}
	}

	// Aktion ausführen: neues Formular, Dialog im aktuellen Formular oder - nach dem Sprung in das
	// Zielformular - dort (Vormerkung, siehe vorgemerkt_ausfuehren)
	P.aktion_ausfuehren = function (frm, aktion) {
		if (!aktion) return;
		if (aktion.neu) {
			frappe.new_doc(aktion.neu, aktion.werte || {});
			return;
		}
		if (frm && frm.doctype === aktion.doctype && frm.doc.name === aktion.name) {
			im_formular(frm, aktion.js);
			return;
		}
		P._vorgemerkt = { doctype: aktion.doctype, name: aktion.name, js: aktion.js, zeit: Date.now() };
		frappe.set_route("Form", aktion.doctype, aktion.name);
	};

	function vorgemerkt_ausfuehren(frm) {
		const v = P._vorgemerkt;
		if (!v || v.doctype !== frm.doctype || v.name !== frm.doc.name) return;
		P._vorgemerkt = null;
		if (Date.now() - v.zeit > 60000) return;
		// nach den übrigen refresh-Handlern (sie stellen die Dialoge bereit)
		setTimeout(() => im_formular(frm, v.js), 300);
	}

	P.einreichen = function (frm) {
		frm.savesubmit();
	};

	// Arbeitspakete aus dem Auftrag (5.1, corevatis_erpnext.planung.tasks_erzeugen)
	P.arbeitspakete_erzeugen = function (frm) {
		frappe
			.call({
				method: "corevatis_erpnext.planung.tasks_erzeugen",
				args: { project: frm.doc.name },
				freeze: true,
				freeze_message: __("Arbeitspakete werden angelegt …"),
			})
			.then((r) => {
				const m = r.message || {};
				const hinweise = (m.hinweise || []).filter(Boolean);
				frappe.show_alert({
					message: __("{0} Arbeitspaket(e)", [(m.arbeitspakete || []).length]),
					indicator: (m.arbeitspakete || []).length ? "green" : "orange",
				});
				if (hinweise.length) {
					frappe.msgprint(hinweise.map((h) => `<div>${esc(h)}</div>`).join(""));
				}
				frm.reload_doc();
			});
	};

	P.rechnung_aus_auftrag = function (frm) {
		frappe.model.open_mapped_doc({
			method: "erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice",
			frm: frm,
		});
	};

	function schritt_klick(frm, schritt) {
		if (schritt.gesperrt) return;
		const belege = schritt.belege || [];
		const hier = belege.find((b) => b.doctype === frm.doctype && b.name === frm.doc.name);
		if (schritt.aktion && (hier || !belege.length)) {
			P.aktion_ausfuehren(frm, schritt.aktion);
			return;
		}
		if (hier) return;
		if (belege.length === 1) {
			frappe.set_route("Form", belege[0].doctype, belege[0].name);
			return;
		}
		if (belege.length > 1) {
			// mehrere Belege (z. B. Objekt und Aufmaß, zwei Angebote): Auswahl
			const d = new frappe.ui.Dialog({ title: __(schritt.label), fields: [{ fieldtype: "HTML", fieldname: "liste" }] });
			d.fields_dict.liste.$wrapper.html(
				belege
					.map(
						(b) =>
							`<div style="padding:6px 0">${corevatis_erpnext.beleg_link(b.doctype, b.name, `${__(b.doctype)} ${b.name}`)} <span class="text-muted">${esc(b.info)}</span></div>`
					)
					.join("") +
					(schritt.aktion
						? `<div style="padding-top:10px"><button class="btn btn-sm btn-primary cv-pl-dialog-aktion">${esc(
								schritt.aktion.label
						  )}</button></div>`
						: "")
			);
			d.fields_dict.liste.$wrapper.find("a").on("click", () => d.hide());
			d.fields_dict.liste.$wrapper.find(".cv-pl-dialog-aktion").on("click", () => {
				d.hide();
				P.aktion_ausfuehren(frm, schritt.aktion);
			});
			d.show();
			return;
		}
		if (schritt.aktion) P.aktion_ausfuehren(frm, schritt.aktion);
	}

	function render(frm, el, daten) {
		const schritte = daten.schritte || [];
		const kette = schritte
			.map((s, i) => {
				const hier = (s.belege || []).some((b) => b.doctype === frm.doctype && b.name === frm.doc.name);
				const klickbar = !s.gesperrt && ((s.belege || []).length || s.aktion);
				const klassen = [
					"cv-pl-schritt",
					`cv-pl-${s.status}`,
					s.aktuell ? "cv-pl-aktuell" : "",
					s.gesperrt ? "cv-pl-gesperrt" : "",
					hier ? "cv-pl-hier" : "",
					klickbar ? "cv-pl-klickbar" : "",
				].join(" ");
				const titel = s.gesperrt ? __("Keine Berechtigung") : s.text || "";
				return `<div class="${klassen}" data-index="${i}" title="${esc(titel)}">
					<span class="cv-pl-label">${esc(SYMBOL[s.status] || "")} ${esc(__(s.label))}</span>
					<span class="cv-pl-text">${esc(s.gesperrt ? __("keine Berechtigung") : s.text)}</span>
					${s.aktion && !s.gesperrt ? `<span class="cv-pl-aktion">${esc(s.aktion.label)} →</span>` : ""}
				</div>`;
			})
			.join("");
		const ampel = daten.ampel || {};
		const vorgang = daten.vorgang || {};
		const naechste = daten.naechste_aktion;
		el.html(`
			<div class="cv-pl-kopf">
				<span class="cv-pl-titel">${esc(__("Vorgang"))}${vorgang.titel ? ": " + esc(vorgang.titel) : ""}</span>
				${
					ampel.farbe
						? `<span class="indicator-pill ${AMPEL[ampel.farbe] || "gray"}">${esc(ampel.text || ampel.farbe)}</span>`
						: ""
				}
				${
					naechste
						? `<button class="btn btn-xs btn-primary cv-pl-naechste">${esc(__("Nächster Schritt"))}: ${esc(
								naechste.label
						  )}</button>`
						: ""
				}
			</div>
			<div class="cv-pl-kette">${kette}</div>`);
		el.find(".cv-pl-schritt.cv-pl-klickbar").on("click", function () {
			schritt_klick(frm, schritte[cint(this.dataset.index)]);
		});
		el.find(".cv-pl-naechste").on("click", () => P.aktion_ausfuehren(frm, naechste));
	}

	P.render = function (frm) {
		if (frm.is_new()) {
			$(frm.layout && frm.layout.wrapper).children(".cv-pl").remove();
			return;
		}
		css();
		const el = container(frm);
		if (!el) return;
		const token = `${frm.doctype}/${frm.doc.name}/${frm.doc.modified}`;
		frm.__cv_pl_token = token;
		frappe
			.xcall(METHODE, { doctype: frm.doctype, name: frm.doc.name })
			.then((daten) => {
				if (frm.__cv_pl_token !== token) return;
				render(frm, el, daten || {});
				vorgemerkt_ausfuehren(frm);
			})
			.catch(() => el.remove());
	};

	for (const doctype of DOCTYPES) {
		frappe.ui.form.on(doctype, { refresh: (frm) => P.render(frm) });
	}
})();
