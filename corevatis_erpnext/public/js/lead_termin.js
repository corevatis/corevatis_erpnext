// Corevatis – Besichtigungstermin am Lead (Aufgabe 1.3, Terminplanung Durchstich D14)
// Server: corevatis_erpnext.crm.termin (Rollen Büro/Meister); Vorschläge und Konfliktprüfung:
// corevatis_erpnext.planung.terminplanung (freie Zeiten je Meister mit Fahrzeit, Doppelbuchungsschutz).
frappe.provide("corevatis_erpnext.termin");

(function () {
	const API = "corevatis_erpnext.crm.termin.";
	const API_PLANUNG = "corevatis_erpnext.planung.terminplanung.";
	const ROLLEN = ["Büro", "Meister", "System Manager", "Administrator"];
	const DAUER_OPTIONEN = [30, 45, 60, 90, 120, 180];
	const ANZAHL_VORSCHLAEGE = 5;
	const esc = (s) => frappe.utils.escape_html(s === null || s === undefined ? "" : String(s));

	function css() {
		if (document.getElementById("cv-tp-css")) return;
		const style = document.createElement("style");
		style.id = "cv-tp-css";
		style.textContent = `
.cv-tp-kopf { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: baseline; margin-bottom: 6px; }
.cv-tp-titel { font-weight: 600; }
.cv-tp-liste { display: flex; flex-direction: column; gap: 4px; }
.cv-tp-zeile { display: flex; align-items: center; gap: 8px; padding: 6px 10px; border: 1px solid var(--border-color);
	border-radius: var(--border-radius, 6px); cursor: pointer; background: var(--control-bg); font-size: var(--text-sm); }
.cv-tp-zeile:hover { border-color: var(--gray-500, #98a1a9); }
.cv-tp-zeile.cv-tp-wunsch { background: var(--bg-green, #e4f5e9); border-color: transparent; }
.cv-tp-zeile.cv-tp-gewaehlt { box-shadow: 0 0 0 2px var(--blue-500, #2490ef) inset; }
.cv-tp-zeile .cv-tp-text { flex: 1; }
.cv-tp-hinweis { margin-top: 4px; }
.cv-tp-konflikt ul { margin: 4px 0 6px 18px; padding: 0; }
.cv-tp-frei { color: var(--text-on-green, #16794c); }
`;
		document.head.appendChild(style);
	}

	function textdatei(name, text) {
		const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = name;
		document.body.appendChild(a);
		a.click();
		setTimeout(() => {
			URL.revokeObjectURL(a.href);
			a.remove();
		}, 0);
	}

	corevatis_erpnext.termin.bestaetigung_dialog = function (frm, inhalt, hinweise) {
		const empfaenger = inhalt.empfaenger || frm.doc.email_id;
		const d = new frappe.ui.Dialog({
			title: __("Terminbestätigung"),
			size: "large",
			fields: [
				{ fieldtype: "HTML", fieldname: "hinweise" },
				{ fieldtype: "Data", fieldname: "betreff", label: __("Betreff"), read_only: 1, default: inhalt.betreff },
				{
					fieldtype: "Small Text",
					fieldname: "text",
					label: __("Text zum Kopieren (WhatsApp, SMS, E-Mail)"),
					default: inhalt.text,
				},
				{ fieldtype: "Section Break", label: __("Vorschau"), collapsible: 1 },
				{ fieldtype: "HTML", fieldname: "vorschau" },
			],
			primary_action_label: __("Text kopieren"),
			primary_action() {
				frappe.utils.copy_to_clipboard(d.get_value("text"));
			},
			secondary_action_label: __("Als Textdatei speichern"),
			secondary_action() {
				textdatei(`Terminbestaetigung-${frm.doc.name}.txt`, `${inhalt.betreff}\n\n${d.get_value("text")}`);
			},
		});
		const liste = (hinweise || []).filter(Boolean);
		if (liste.length) {
			d.fields_dict.hinweise.$wrapper.html(
				liste.map((h) => `<div class="alert alert-info small">${esc(h)}</div>`).join("")
			);
		}
		// HTML stammt aus dem Email Template mit serverseitig escapten Werten
		d.fields_dict.vorschau.$wrapper.html(`<div class="small">${inhalt.html || ""}</div>`);
		if (empfaenger) {
			d.add_custom_action(__("In E-Mail-Warteschlange legen"), () => {
				frappe
					.call({ method: API + "bestaetigung_senden", args: { lead: frm.doc.name }, freeze: true })
					.then((r) => {
						frappe.show_alert({
							message: __("In der E-Mail-Warteschlange: {0}", [r.message || "-"]),
							indicator: "green",
						});
						frm.reload_doc();
					});
			});
		}
		d.show();
		return d;
	};

	// --- Dauer ---------------------------------------------------------------------------

	function dauer_label(minuten) {
		if (minuten < 60) return __("{0} Minuten", [minuten]);
		if (minuten === 60) return __("1 Stunde");
		if (minuten % 60 === 0) return __("{0} Stunden", [minuten / 60]);
		return __("{0} Stunden", [(minuten / 60).toFixed(1).replace(".", ",")]);
	}

	function dauer_optionen(dauer) {
		const werte = DAUER_OPTIONEN.slice();
		if (dauer && !werte.includes(dauer)) werte.push(dauer);
		werte.sort((a, b) => a - b);
		return werte.map((v) => ({ value: String(v), label: dauer_label(v) }));
	}

	// --- Vorschläge (D14) ----------------------------------------------------------------

	function vorschlaege_laden(state) {
		const d = state.dialog;
		const $w = d.fields_dict.vorschlaege.$wrapper;
		$w.html(`<div class="text-muted small">${esc(__("Freie Termine werden berechnet …"))}</div>`);
		const kennung = `${state.frm.doc.name}|${d.get_value("dauer") || ""}`;
		state.vorschlag_kennung = kennung;
		return frappe
			.call({
				method: API_PLANUNG + "vorschlaege",
				args: { lead: state.frm.doc.name, dauer: d.get_value("dauer") || null },
			})
			.then((r) => {
				if (state.vorschlag_kennung !== kennung) return;
				state.vorschlaege = r.message || {};
				// Standarddauer aus den Einstellungen übernehmen, solange der Benutzer nichts gewählt hat
				const dauer = state.vorschlaege.dauer;
				if (dauer && !state.dauer_geaendert && String(dauer) !== String(d.get_value("dauer"))) {
					state.stumm = true;
					d.set_df_property("dauer", "options", dauer_optionen(dauer));
					d.set_value("dauer", String(dauer)).then(() => {
						state.stumm = false;
					});
				}
				vorschlaege_rendern(state);
			})
			.catch(() => {
				$w.html(`<div class="text-muted small">${esc(__("Freie Termine konnten nicht berechnet werden."))}</div>`);
			});
	}

	function vorschlaege_rendern(state) {
		const d = state.dialog;
		const daten = state.vorschlaege || {};
		const $w = d.fields_dict.vorschlaege.$wrapper;
		const liste = (daten.vorschlaege || []).slice(0, ANZAHL_VORSCHLAEGE);
		const wunsch = (daten.wunsch && daten.wunsch.text) || "";
		let html = `<div class="cv-tp-kopf"><span class="cv-tp-titel">${esc(__("Freie Termine"))}</span>`;
		html += `<span class="text-muted small">${esc(__("Wunsch des Kunden"))}: ${
			wunsch ? `<b>${esc(wunsch)}</b>` : esc(__("keine Angabe"))
		}</span></div>`;
		if (liste.length) {
			html +=
				`<div class="cv-tp-liste">` +
				liste
					.map(
						(v, i) => `<div class="cv-tp-zeile${v.wunsch ? " cv-tp-wunsch" : ""}" data-index="${i}" title="${esc(
							__("Übernimmt Datum, Uhrzeit, Dauer und Meister")
						)}">
						<span class="cv-tp-text">${esc(v.text)}</span>
						${v.wunsch ? `<span class="indicator-pill green">${esc(__("Wunsch"))}</span>` : ""}
					</div>`
					)
					.join("") +
				`</div>`;
		} else {
			html += `<div class="text-muted small">${esc(__("Keine freien Zeiten gefunden."))}</div>`;
		}
		(daten.hinweise || []).forEach((h) => {
			html += `<div class="cv-tp-hinweis text-muted small">${esc(h)}</div>`;
		});
		$w.html(html);
		$w.find(".cv-tp-zeile").on("click", function () {
			const v = liste[parseInt(this.dataset.index, 10)];
			if (!v) return;
			state.stumm = true;
			Promise.all([
				d.set_value("meister", v.meister),
				d.set_value("dauer", String(daten.dauer || d.get_value("dauer"))),
				d.set_value("beginn", v.beginn),
			]).then(() => {
				state.stumm = false;
				konflikt_pruefen(state, true);
			});
			$w.find(".cv-tp-zeile").removeClass("cv-tp-gewaehlt");
			$(this).addClass("cv-tp-gewaehlt");
		});
	}

	// --- Doppelbuchungsschutz (D14) ------------------------------------------------------

	function konflikt_pruefen(state, sofort) {
		if (state.stumm) return;
		const d = state.dialog;
		const beginn = d.get_value("beginn");
		const meister = d.get_value("meister");
		const $k = d.fields_dict.konflikt.$wrapper;
		clearTimeout(state.timer);
		if (!beginn || !meister) {
			$k.empty();
			state.konflikte = [];
			return;
		}
		state.timer = setTimeout(
			() => {
				const kennung = `${beginn}|${meister}|${d.get_value("dauer") || ""}`;
				state.konflikt_kennung = kennung;
				frappe
					.call({
						method: API_PLANUNG + "pruefen",
						args: { lead: state.frm.doc.name, beginn, meister, dauer: d.get_value("dauer") || null },
					})
					.then((r) => {
						if (state.konflikt_kennung !== kennung) return;
						state.konflikte = (r.message && r.message.konflikte) || [];
						konflikt_rendern(state);
					});
			},
			sofort ? 0 : 400
		);
	}

	function konflikt_rendern(state) {
		const d = state.dialog;
		const $k = d.fields_dict.konflikt.$wrapper;
		const liste = state.konflikte || [];
		if (!liste.length) {
			$k.html(`<div class="cv-tp-frei small">${esc(__("Keine Überschneidung mit der Planung des Meisters."))}</div>`);
			d.set_df_property("trotzdem", "hidden", 1);
			d.set_value("trotzdem", 0);
			return;
		}
		$k.html(
			`<div class="alert alert-warning cv-tp-konflikt small"><b>${esc(__("Überschneidung"))}:</b><ul>${liste
				.map((k) => `<li>${esc(k.text)}</li>`)
				.join("")}</ul>${esc(__("Anderen Vorschlag wählen oder unten „Trotzdem anlegen“ anhaken."))}</div>`
		);
		d.set_df_property("trotzdem", "hidden", 0);
	}

	// --- Dialog „Termin anlegen“ ---------------------------------------------------------

	corevatis_erpnext.termin.anlegen_dialog = async function (frm) {
		const r = await frappe.call({ method: API + "meister_liste" });
		const meister = r.message || [];
		if (!meister.length) {
			frappe.msgprint(__("Es gibt keinen aktiven Benutzer mit der Rolle Meister."));
			return;
		}
		css();
		const email = frm.doc.email_id;
		const state = { frm, dialog: null, vorschlaege: null, konflikte: [], stumm: false, dauer_geaendert: false };
		const pruefen = () => state.dialog && konflikt_pruefen(state);
		const d = new frappe.ui.Dialog({
			title: __("Besichtigungstermin anlegen"),
			size: "large",
			fields: [
				{ fieldtype: "HTML", fieldname: "vorschlaege" },
				{ fieldtype: "Section Break", label: __("Termin") },
				{
					fieldtype: "Datetime",
					fieldname: "beginn",
					label: __("Datum und Uhrzeit"),
					reqd: 1,
					onchange: pruefen,
				},
				{
					fieldtype: "Select",
					fieldname: "dauer",
					label: __("Dauer"),
					options: dauer_optionen(60),
					default: "60",
					reqd: 1,
					onchange() {
						if (!state.dialog || state.stumm) return;
						state.dauer_geaendert = true;
						konflikt_pruefen(state);
						vorschlaege_laden(state);
					},
				},
				{ fieldtype: "Column Break" },
				{
					fieldtype: "Select",
					fieldname: "meister",
					label: __("Meister"),
					// Frappe rendert Select-Labels als HTML (frappe.ui.form.add_options → .html()):
					// Benutzernamen escapen (Review Epic 1, E-5)
					options: meister.map((m) => ({ value: m.name, label: frappe.utils.escape_html(m.full_name || m.name) })),
					default: meister.length === 1 ? meister[0].name : (meister.find((m) => m.name === frappe.session.user) || {}).name,
					reqd: 1,
					onchange: pruefen,
				},
				{ fieldtype: "Small Text", fieldname: "notiz", label: __("Notiz für den Meister (intern)") },
				{ fieldtype: "Section Break" },
				{ fieldtype: "HTML", fieldname: "konflikt" },
				{
					fieldtype: "Check",
					fieldname: "trotzdem",
					label: __("Trotzdem anlegen – die Überschneidung wird am Termin vermerkt"),
					hidden: 1,
				},
				{
					fieldtype: "Check",
					fieldname: "bestaetigung",
					label: __("Terminbestätigung in die E-Mail-Warteschlange legen"),
					default: email ? 1 : 0,
					read_only: email ? 0 : 1,
					description: email
						? __("an {0} – ohne eingerichtetes E-Mail-Konto wird nichts versendet, der Text steht danach zum Kopieren bereit.", [
								frappe.utils.escape_html(email),
						  ])
						: __("Der Lead hat keine E-Mail-Adresse – die Bestätigung bitte manuell übermitteln."),
				},
			],
			primary_action_label: __("Termin anlegen"),
			primary_action(values) {
				if ((state.konflikte || []).length && !cint(values.trotzdem)) {
					frappe.msgprint({
						title: __("Terminüberschneidung"),
						indicator: "orange",
						message: __("Bitte einen anderen Vorschlag wählen oder „Trotzdem anlegen“ anhaken."),
					});
					return;
				}
				frappe
					.call({
						method: API + "termin_anlegen",
						args: Object.assign({ lead: frm.doc.name }, values),
						freeze: true,
						freeze_message: __("Termin wird angelegt …"),
						error(res) {
							// Server lehnt wegen Überschneidung ab (TerminKonflikt): Haken anbieten
							if (res && res.exc_type === "TerminKonflikt") {
								d.set_df_property("trotzdem", "hidden", 0);
								konflikt_pruefen(state, true);
							}
						},
					})
					.then((res) => {
						const m = res.message || {};
						d.hide();
						frm.reload_doc();
						const hinweise = [__("Termin {0} angelegt.", [m.event])];
						if (m.abgesagt) hinweise.push(__("Vorheriger Termin {0} wurde abgesagt.", [m.abgesagt]));
						if (m.status) hinweise.push(__("Status: {0}", [m.status]));
						if ((m.konflikte || []).length) {
							hinweise.push(
								__("Trotz Überschneidung angelegt: {0}", [m.konflikte.map((k) => k.text).join(" ")])
							);
						}
						if (m.email_queue) hinweise.push(__("Bestätigung in der E-Mail-Warteschlange ({0}).", [m.email_queue]));
						corevatis_erpnext.termin.bestaetigung_dialog(
							frm,
							Object.assign({ empfaenger: m.empfaenger }, m.bestaetigung || {}),
							hinweise.concat(m.warnungen || [])
						);
					});
			},
		});
		state.dialog = d;
		d.show();
		vorschlaege_laden(state);
	};

	corevatis_erpnext.termin.bestaetigung_anzeigen = function (frm) {
		frappe.call({ method: API + "bestaetigung_anzeigen", args: { lead: frm.doc.name } }).then((r) => {
			if (r.message) corevatis_erpnext.termin.bestaetigung_dialog(frm, r.message);
		});
	};

	frappe.ui.form.on("Lead", {
		refresh(frm) {
			if (frm.is_new() || !frappe.user.has_role(ROLLEN)) return;
			frm.add_custom_button(__("Termin anlegen"), () => corevatis_erpnext.termin.anlegen_dialog(frm));
			if (frm.doc.cv_termin) {
				frm.add_custom_button(__("Bestätigung anzeigen/kopieren"), () =>
					corevatis_erpnext.termin.bestaetigung_anzeigen(frm)
				);
			}
		},
	});
})();
