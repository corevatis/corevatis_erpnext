// Plantafel (Phase 1 P1.4, Issue corevatis/Handwerker#79): Mitarbeiter × Tage mit Terminen,
// Arbeitspaketen, Abwesenheiten, Feiertagen und Fahrzeugen; Verschieben per Ziehen, Druck.
// Server: corevatis_erpnext.planung.plantafel (tafel, arbeitspaket_verschieben, fahrzeug_zuordnen,
// reihenfolge_speichern, drucken) und corevatis_erpnext.crm.termin.termin_verschieben.
// data-Attribute (für Prüfungen per Playwright): table.cv-pt[data-von][data-bis][data-ansicht],
// tr.cv-pt-zeile[data-mitarbeiter][data-user], td.cv-pt-zelle[data-mitarbeiter][data-datum][data-frei],
// .cv-pt-eintrag[data-doctype][data-name][data-art][data-konflikt][data-fahrzeug], tr.cv-pt-offen.
frappe.provide("corevatis_erpnext.plantafel");

frappe.pages["plantafel"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Plantafel"), single_column: true });
	wrapper.plantafel = new corevatis_erpnext.plantafel.Tafel(page, wrapper);
};

frappe.pages["plantafel"].on_page_show = function (wrapper) {
	if (wrapper.plantafel) wrapper.plantafel.routen_optionen();
};

(function () {
	const API = "corevatis_erpnext.planung.plantafel.";
	const API_TERMIN = "corevatis_erpnext.crm.termin.";
	const ROLLEN_PLANER = ["Büro", "Meister", "System Manager", "Administrator"];
	const ARTEN = { Termin: "termin", Arbeitspaket: "arbeitspaket", Abwesenheit: "abwesenheit" };
	const ABWESENHEIT = { Urlaub: "urlaub", Krank: "krank", Schulung: "schulung", Sonstiges: "sonstiges" };
	const esc = (s) => frappe.utils.escape_html(s === null || s === undefined ? "" : String(s));
	const SPEICHER = "cv_plantafel";

	function montag(datum) {
		const d = frappe.datetime.str_to_obj(datum || frappe.datetime.get_today());
		const versatz = (d.getDay() + 6) % 7;
		return frappe.datetime.add_days(frappe.datetime.obj_to_str(d), -versatz);
	}

	function uhrzeit(zeit) {
		// "2026-11-03 09:00:00" → "09:00"
		return (zeit || "").slice(11, 16);
	}

	function datum_kurz(datum) {
		const d = frappe.datetime.str_to_obj(datum);
		return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}.`;
	}

	function css() {
		if (document.getElementById("cv-pt-css")) return;
		const style = document.createElement("style");
		style.id = "cv-pt-css";
		style.textContent = `
.cv-pt-wrap { overflow-x: auto; padding-bottom: 8px; }
.cv-pt-kopf { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; margin: 4px 0 8px; font-size: var(--text-sm); }
.cv-pt-kopf .indicator-pill { margin: 0; }
table.cv-pt { width: 100%; border-collapse: separate; border-spacing: 0; table-layout: fixed; font-size: var(--text-sm); }
table.cv-pt th, table.cv-pt td { border: 1px solid var(--border-color); vertical-align: top; padding: 4px 6px; }
table.cv-pt th { background: var(--subtle-fg, var(--control-bg)); font-weight: 600; position: sticky; top: 0; z-index: 1; }
table.cv-pt th.cv-pt-name, table.cv-pt td.cv-pt-name { width: 150px; font-weight: 600; background: var(--fg-color); }
table.cv-pt td.cv-pt-name .cv-pt-eigene { color: var(--text-muted); font-weight: normal; font-size: var(--text-xs); }
table.cv-pt th.cv-pt-heute { box-shadow: inset 0 -3px 0 var(--blue-500, #2490ef); }
table.cv-pt th.cv-pt-frei, table.cv-pt td.cv-pt-frei { background: var(--bg-orange, #fff3e0); }
table.cv-pt th.cv-pt-we, table.cv-pt td.cv-pt-we { background: var(--subtle-fg, var(--control-bg)); }
table.cv-pt td.cv-pt-abwesend { background: var(--bg-gray, #f3f3f3); }
table.cv-pt td.cv-pt-zelle { min-height: 70px; height: 70px; position: relative; transition: background 0.1s; }
table.cv-pt td.cv-pt-ziel { background: var(--bg-blue, #e8f1fb); outline: 2px dashed var(--blue-500, #2490ef); outline-offset: -2px; }
table.cv-pt.cv-pt-monat th, table.cv-pt.cv-pt-monat td { padding: 2px 3px; font-size: var(--text-xs); }
table.cv-pt.cv-pt-monat th.cv-pt-name, table.cv-pt.cv-pt-monat td.cv-pt-name { width: 110px; }
table.cv-pt.cv-pt-monat td.cv-pt-zelle { min-height: 44px; height: 44px; }
.cv-pt-feiertag { display: block; font-weight: normal; font-size: var(--text-xs); color: var(--orange-700, #b25e09); }
.cv-pt-last { position: absolute; top: 2px; right: 4px; font-size: var(--text-xs); color: var(--text-muted); }
.cv-pt-last.cv-pt-ueberlast { color: var(--red-600); font-weight: 700; }
.cv-pt-raster { position: relative; height: 5px; margin: 10px 0 5px; background: var(--border-color); border-radius: 3px; }
.cv-pt-block { position: absolute; top: 0; height: 5px; border-radius: 3px; background: var(--gray-600); }
.cv-pt-block.cv-pt-art-termin { background: var(--blue-500, #2490ef); }
.cv-pt-block.cv-pt-art-arbeitspaket { background: var(--green-500, #29a845); }
.cv-pt-block.cv-pt-art-abwesenheit { background: var(--gray-500); }
.cv-pt-eintrag { position: relative; margin: 0 0 4px; padding: 3px 6px 3px 8px; border-radius: var(--border-radius, 6px);
	border-left: 4px solid var(--gray-500); background: var(--control-bg); cursor: grab; line-height: 1.3; }
.cv-pt-eintrag[draggable="false"] { cursor: pointer; }
.cv-pt-eintrag:hover { box-shadow: 0 0 0 1px var(--gray-400) inset; }
.cv-pt-eintrag.cv-pt-ziehen { opacity: 0.5; }
.cv-pt-art-termin { border-left-color: var(--blue-500, #2490ef); background: var(--bg-blue, #e8f1fb); }
.cv-pt-art-arbeitspaket { border-left-color: var(--green-500, #29a845); background: var(--bg-green, #e4f5e9); }
.cv-pt-art-abwesenheit { border-left-color: var(--gray-500); background: var(--bg-gray, #f3f3f3); color: var(--text-muted); }
.cv-pt-art-abwesenheit.cv-pt-abw-krank { border-left-color: var(--red-500, #e03636); }
.cv-pt-art-abwesenheit.cv-pt-abw-urlaub { border-left-color: var(--yellow-500, #e8b400); }
.cv-pt-art-abwesenheit.cv-pt-abw-schulung { border-left-color: var(--purple-500, #8e44ad); }
.cv-pt-eintrag[data-konflikt="1"] { border-left-color: var(--red-600) !important; background: var(--bg-red, #fde9e9) !important; }
.cv-pt-zeit { font-weight: 600; }
.cv-pt-titel { word-break: break-word; }
.cv-pt-klein { color: var(--text-muted); font-size: var(--text-xs); word-break: break-word; }
.cv-pt-marker { position: absolute; top: 2px; right: 4px; font-size: var(--text-xs); font-weight: 700; border-radius: 8px; padding: 0 5px; }
.cv-pt-marker.cv-pt-m-konflikt { background: var(--red-600); color: #fff; }
.cv-pt-marker.cv-pt-m-hinweis { background: var(--orange-500, #f39c12); color: #fff; right: 26px; }
.cv-pt-fahrzeug { display: inline-block; padding: 0 5px; border-radius: 8px; background: var(--fg-color); border: 1px solid var(--border-color); font-size: var(--text-xs); }
.cv-pt-monat .cv-pt-eintrag { padding: 1px 4px 1px 6px; margin-bottom: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cv-pt-monat .cv-pt-klein, .cv-pt-monat .cv-pt-raster, .cv-pt-monat .cv-pt-marker, .cv-pt-monat .cv-pt-last { display: none; }
.cv-pt-leer { color: var(--text-muted); padding: 24px; text-align: center; }
.cv-pt-legende { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 8px; font-size: var(--text-xs); color: var(--text-muted); }
.cv-pt-legende span::before { content: ""; display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; background: var(--gray-500); }
.cv-pt-legende .cv-pt-l-termin::before { background: var(--blue-500, #2490ef); }
.cv-pt-legende .cv-pt-l-arbeitspaket::before { background: var(--green-500, #29a845); }
.cv-pt-legende .cv-pt-l-abwesenheit::before { background: var(--gray-500); }
.cv-pt-legende .cv-pt-l-konflikt::before { background: var(--red-600); }
.cv-pt-legende .cv-pt-l-frei::before { background: var(--bg-orange, #fff3e0); border: 1px solid var(--orange-300, #f3b57a); }
.cv-pt-reihe { display: flex; align-items: center; gap: 8px; padding: 4px 0; border-bottom: 1px solid var(--border-color); }
.cv-pt-reihe .cv-pt-reihe-name { flex: 1; }
`;
		document.head.appendChild(style);
	}

	class Tafel {
		constructor(page, wrapper) {
			this.page = page;
			this.wrapper = wrapper;
			this.daten = null;
			this.stumm = false;
			this.planer = ROLLEN_PLANER.some((r) => frappe.user.has_role(r));
			css();
			this.$body = $('<div class="cv-pt-body"></div>').appendTo(page.main);
			this.gespeichert = this.lesen_speicher();
			this.felder();
			this.knoepfe();
			frappe.require("zuweisung.bundle.js");
			this.laden();
		}

		lesen_speicher() {
			try {
				return JSON.parse(localStorage.getItem(SPEICHER) || "{}") || {};
			} catch (e) {
				return {};
			}
		}

		merken() {
			try {
				localStorage.setItem(
					SPEICHER,
					JSON.stringify({ ansicht: this.f.ansicht.get_value(), wochenende: cint(this.f.wochenende.get_value()) })
				);
			} catch (e) {
				// Speicher nicht verfügbar
			}
		}

		felder() {
			const neu = () => {
				if (this.stumm) return;
				this.merken();
				this.laden();
			};
			this.f = {};
			this.f.von = this.page.add_field({
				fieldname: "von",
				label: __("Woche ab"),
				fieldtype: "Date",
				default: montag(),
				change: neu,
			});
			this.f.ansicht = this.page.add_field({
				fieldname: "ansicht",
				label: __("Ansicht"),
				fieldtype: "Select",
				options: [
					{ value: "woche", label: __("Woche") },
					{ value: "monat", label: __("Monat") },
				],
				default: this.gespeichert.ansicht === "monat" ? "monat" : "woche",
				change: neu,
			});
			this.f.wochenende = this.page.add_field({
				fieldname: "wochenende",
				label: __("Sa/So zeigen"),
				fieldtype: "Check",
				default: this.gespeichert.wochenende ? 1 : 0,
				change: neu,
			});
			if (this.planer) {
				this.f.projekt = this.page.add_field({
					fieldname: "projekt",
					label: __("Projekt"),
					fieldtype: "Link",
					options: "Project",
					change: neu,
				});
				// Select statt Link: Büro/Meister lesen Employee nicht (Site-Template); Optionen vom Server
				this.f.mitarbeiter = this.page.add_field({
					fieldname: "mitarbeiter",
					label: __("Mitarbeiter"),
					fieldtype: "Select",
					options: [{ value: "", label: "" }],
					change: neu,
				});
				frappe.call("corevatis_erpnext.planung.zuweisung.mitarbeiter").then((r) => {
					this.f.mitarbeiter.df.options = [{ value: "", label: "" }].concat(r.message || []);
					this.f.mitarbeiter.refresh();
				});
			}
		}

		knoepfe() {
			const p = this.page;
			p.add_inner_button(__("Zurück"), () => this.blaettern(-1), __("Zeitraum"));
			p.add_inner_button(__("Heute"), () => this.blaettern(0), __("Zeitraum"));
			p.add_inner_button(__("Weiter"), () => this.blaettern(1), __("Zeitraum"));
			if (this.planer) {
				p.set_primary_action(__("Wochenplan drucken"), () => this.drucken(), "printer");
				p.add_menu_item(__("Abwesenheit eintragen"), () => this.abwesenheit_dialog());
				p.add_menu_item(__("Fahrzeug anlegen"), () => frappe.new_doc("Fahrzeug"));
				p.add_menu_item(__("Reihenfolge der Mitarbeiter"), () => this.reihenfolge_dialog());
				p.add_menu_item(__("Alle Abwesenheiten"), () => frappe.set_route("List", "Abwesenheit"));
				p.add_menu_item(__("Alle Fahrzeuge"), () => frappe.set_route("List", "Fahrzeug"));
			}
			p.add_menu_item(__("Aktualisieren"), () => this.laden());
		}

		routen_optionen() {
			const o = frappe.route_options;
			if (!o || !Object.keys(o).length) return;
			frappe.route_options = null;
			this.stumm = true;
			if (o.von) this.f.von.set_value(montag(o.von));
			if (o.ansicht && ["woche", "monat"].includes(o.ansicht)) this.f.ansicht.set_value(o.ansicht);
			if (this.f.projekt && o.projekt !== undefined) this.f.projekt.set_value(o.projekt || "");
			if (this.f.mitarbeiter && o.mitarbeiter !== undefined) this.f.mitarbeiter.set_value(o.mitarbeiter || "");
			setTimeout(() => {
				this.stumm = false;
				this.laden();
			}, 50);
		}

		blaettern(richtung) {
			const monat = this.f.ansicht.get_value() === "monat";
			let von = this.f.von.get_value() || frappe.datetime.get_today();
			if (richtung === 0) {
				von = frappe.datetime.get_today();
			} else if (monat) {
				von = frappe.datetime.add_months(von, richtung);
			} else {
				von = frappe.datetime.add_days(montag(von), 7 * richtung);
			}
			this.f.von.set_value(monat ? von : montag(von));
		}

		args() {
			return {
				von: this.f.von.get_value() || frappe.datetime.get_today(),
				ansicht: this.f.ansicht.get_value() || "woche",
				wochenende: cint(this.f.wochenende.get_value()),
				projekt: this.f.projekt ? this.f.projekt.get_value() || null : null,
				mitarbeiter: this.f.mitarbeiter ? this.f.mitarbeiter.get_value() || null : null,
			};
		}

		laden() {
			const args = this.args();
			const token = JSON.stringify(args);
			this.token = token;
			this.$body.addClass("cv-pt-laedt");
			return frappe
				.xcall(API + "tafel", args)
				.then((daten) => {
					if (this.token !== token) return;
					this.daten = daten;
					this.render();
				})
				.catch(() => {
					this.$body.html(`<div class="cv-pt-leer">${esc(__("Die Plantafel konnte nicht geladen werden."))}</div>`);
				})
				.finally(() => this.$body.removeClass("cv-pt-laedt"));
		}

		// --- Rendern ---------------------------------------------------------------------------

		render() {
			const d = this.daten;
			if (!d) return;
			const z = d.zusammenfassung || {};
			const pill = (text, farbe) => `<span class="indicator-pill ${farbe}">${esc(text)}</span>`;
			let kopf = `<div class="cv-pt-kopf">`;
			kopf += `<b>${esc(d.ansicht === "monat" ? __("Monat") : __("Woche"))} ${esc(
				frappe.datetime.str_to_user(d.von)
			)} – ${esc(frappe.datetime.str_to_user(d.bis))}</b>`;
			kopf += pill(__("{0} Mitarbeiter", [z.mitarbeiter || 0]), "blue");
			kopf += pill(__("{0} Einsätze", [z.einsaetze || 0]), "blue");
			kopf += pill(__("{0} nicht zugewiesen", [z.nicht_zugewiesen || 0]), z.nicht_zugewiesen ? "orange" : "green");
			kopf += pill(__("{0} Konflikte", [z.konflikte || 0]), z.konflikte ? "red" : "green");
			if (z.fahrzeit_hinweise) kopf += pill(__("{0} Fahrzeit-Hinweise", [z.fahrzeit_hinweise]), "orange");
			if (z.abwesenheiten) kopf += pill(__("{0} Abwesenheiten", [z.abwesenheiten]), "gray");
			if (d.schreibgeschuetzt) kopf += `<span class="text-muted">${esc(__("Nur Ansicht: eigene Zeile"))}</span>`;
			(d.hinweise || []).forEach((h) => {
				kopf += `<span class="text-muted">${esc(h)}</span>`;
			});
			kopf += `</div>`;

			const tage = d.tage || [];
			let thead = `<tr><th class="cv-pt-name">${esc(__("Mitarbeiter"))}</th>`;
			for (const t of tage) {
				const klassen = ["cv-pt-tag"];
				if (t.feiertag) klassen.push("cv-pt-frei");
				if (t.wochenende) klassen.push("cv-pt-we");
				if (t.heute) klassen.push("cv-pt-heute");
				thead += `<th class="${klassen.join(" ")}" data-datum="${esc(t.datum)}">${esc(t.wochentag)} ${esc(
					datum_kurz(t.datum)
				)}${t.feiertag ? `<span class="cv-pt-feiertag">${esc(t.feiertag)}</span>` : ""}</th>`;
			}
			thead += `</tr>`;

			let tbody = "";
			for (const zeile of d.zeilen || []) {
				tbody += this.zeile_html(zeile, tage);
			}
			if (!d.schreibgeschuetzt && !d.mitarbeiter) {
				tbody += this.zeile_html(
					{
						mitarbeiter: "",
						user: "",
						name: __("Nicht zugewiesen"),
						eintraege: d.nicht_zugewiesen || [],
						last: {},
						ueberlast: [],
						abwesend: {},
						offen: true,
					},
					tage
				);
			}
			if (!(d.zeilen || []).length) {
				tbody += `<tr><td colspan="${tage.length + 1}" class="cv-pt-leer">${esc(
					__("Keine planbaren Mitarbeiter (aktiver Mitarbeiter mit Benutzer und Rolle Monteur oder Meister).")
				)}</td></tr>`;
			}
			const legende = `<div class="cv-pt-legende">
				<span class="cv-pt-l-termin">${esc(__("Besichtigungstermin"))}</span>
				<span class="cv-pt-l-arbeitspaket">${esc(__("Arbeitspaket"))}</span>
				<span class="cv-pt-l-abwesenheit">${esc(__("Abwesenheit (Urlaub, Krank, Schulung, Sonstiges)"))}</span>
				<span class="cv-pt-l-konflikt">${esc(__("Konflikt (Überschneidung, Fahrzeug doppelt)"))}</span>
				<span class="cv-pt-l-frei">${esc(__("Feiertag"))}</span>
				<span class="text-muted">${esc(
					d.schreibgeschuetzt
						? __("Verschieben ist dem Büro und den Meistern vorbehalten.")
						: __("Ziehen: Einsatz auf einen anderen Tag oder Mitarbeiter. Klick: Fahrzeug, Zuweisung, Öffnen.")
				)}</span></div>`;
			this.$body.html(
				kopf +
					`<div class="cv-pt-wrap"><table class="cv-pt${d.ansicht === "monat" ? " cv-pt-monat" : ""}" data-von="${esc(
						d.von
					)}" data-bis="${esc(d.bis)}" data-ansicht="${esc(d.ansicht)}"><thead>${thead}</thead><tbody>${tbody}</tbody></table></div>` +
					legende
			);
			this.ereignisse();
		}

		zeile_html(zeile, tage) {
			const d = this.daten;
			const beginn = this.minuten(d.arbeitsbeginn);
			const dauer = Math.max((d.tagesarbeitszeit || 8) * 60, 60);
			let html = `<tr class="cv-pt-zeile${zeile.offen ? " cv-pt-offen" : ""}" data-mitarbeiter="${esc(
				zeile.mitarbeiter || ""
			)}" data-user="${esc(zeile.user || "")}">`;
			html += `<td class="cv-pt-name">${esc(zeile.name)}${
				zeile.eigene ? `<div class="cv-pt-eigene">${esc(__("(ich)"))}</div>` : ""
			}</td>`;
			for (const t of tage) {
				const klassen = ["cv-pt-zelle"];
				if (t.feiertag) klassen.push("cv-pt-frei");
				if (t.wochenende) klassen.push("cv-pt-we");
				const abwesend = (zeile.abwesend || {})[t.datum];
				if (abwesend) klassen.push("cv-pt-abwesend");
				const last = (zeile.last || {})[t.datum];
				const ueberlast = (zeile.ueberlast || []).includes(t.datum);
				let inhalt = "";
				if (!zeile.offen && last) {
					inhalt += `<span class="cv-pt-last${ueberlast ? " cv-pt-ueberlast" : ""}" title="${esc(
						__("Tageslast aus Arbeitspaketen (Sollstunden ÷ Zugewiesene)")
					)}">${esc(format_number(last, null, 1))} h${ueberlast ? " !" : ""}</span>`;
				}
				const segmente = [];
				for (const e of zeile.eintraege || []) {
					for (const s of e.segmente || []) {
						if (s.datum === t.datum) segmente.push({ e, s });
					}
				}
				segmente.sort((a, b) => (a.s.von_min - b.s.von_min) || (a.e.von < b.e.von ? -1 : 1));
				if (d.ansicht !== "monat") {
					inhalt += `<div class="cv-pt-raster" title="${esc(__("Arbeitszeit {0} + {1} h", [d.arbeitsbeginn, d.tagesarbeitszeit]))}">`;
					for (const { e, s } of segmente) {
						const von = Math.max(0, Math.min(100, ((s.von_min - beginn) / dauer) * 100));
						const bis = Math.max(0, Math.min(100, ((s.bis_min - beginn) / dauer) * 100));
						if (bis > von) {
							inhalt += `<span class="cv-pt-block cv-pt-art-${ARTEN[e.art] || "sonst"}" style="left:${von}%;width:${
								bis - von
							}%"></span>`;
						}
					}
					inhalt += `</div>`;
				}
				for (const { e, s } of segmente) {
					inhalt += this.eintrag_html(e, s, zeile);
				}
				html += `<td class="${klassen.join(" ")}" data-mitarbeiter="${esc(zeile.mitarbeiter || "")}" data-user="${esc(
					zeile.user || ""
				)}" data-datum="${esc(t.datum)}" data-frei="${t.feiertag ? 1 : 0}">${inhalt}</td>`;
			}
			return html + `</tr>`;
		}

		eintrag_html(e, s, zeile) {
			const d = this.daten;
			const klassen = ["cv-pt-eintrag", `cv-pt-art-${ARTEN[e.art] || "sonst"}`];
			if (e.art === "Abwesenheit") klassen.push(`cv-pt-abw-${ABWESENHEIT[e.abwesenheit_art] || "sonstiges"}`);
			const konflikt = (e.konflikte || []).length ? 1 : 0;
			const hinweis = (e.hinweise || []).length ? 1 : 0;
			const ziehbar = !d.schreibgeschuetzt && e.verschiebbar && e.art !== "Abwesenheit";
			const zeit = s.ganztags ? __("ganztags") : `${s.von}–${s.bis}`;
			const klein = [e.projekt_name, e.ort, e.fahrzeug_name ? "" : null].filter(Boolean);
			const titel = [e.zeit, e.titel, e.untertitel, e.projekt_name, e.ort]
				.concat(e.fahrzeug_name ? [__("Fahrzeug {0}", [e.fahrzeug_name])] : [])
				.concat(e.konflikte || [])
				.concat(e.hinweise || [])
				.filter(Boolean)
				.join("\n");
			let html = `<div class="${klassen.join(" ")}" draggable="${ziehbar ? "true" : "false"}" data-doctype="${esc(
				e.doctype
			)}" data-name="${esc(e.name)}" data-art="${esc(e.art)}" data-von="${esc(e.von)}" data-bis="${esc(
				e.bis
			)}" data-mitarbeiter="${esc(zeile.mitarbeiter || "")}" data-user="${esc(zeile.user || "")}" data-konflikt="${konflikt}" data-hinweis="${hinweis}" data-fahrzeug="${esc(
				e.fahrzeug || ""
			)}" data-lesbar="${e.lesbar ? 1 : 0}" title="${esc(titel)}">`;
			html += `<span class="cv-pt-zeit">${esc(zeit)}</span> <span class="cv-pt-titel">${esc(e.titel)}</span>`;
			if (klein.length || e.fahrzeug_name) {
				html += `<div class="cv-pt-klein">${klein.map(esc).join(" · ")}${
					e.fahrzeug_name
						? `${klein.length ? " · " : ""}<span class="cv-pt-fahrzeug" title="${esc(__("Fahrzeug"))}">${esc(e.fahrzeug_name)}</span>`
						: ""
				}</div>`;
			}
			if (konflikt) html += `<span class="cv-pt-marker cv-pt-m-konflikt" title="${esc((e.konflikte || []).join("\n"))}">!</span>`;
			if (hinweis) html += `<span class="cv-pt-marker cv-pt-m-hinweis" title="${esc((e.hinweise || []).join("\n"))}">⚠</span>`;
			return html + `</div>`;
		}

		minuten(uhr) {
			const [h, m] = String(uhr || "07:00").split(":").map((x) => parseInt(x, 10) || 0);
			return h * 60 + m;
		}

		// --- Ereignisse: Ziehen, Klick -------------------------------------------------------

		ereignisse() {
			const $t = this.$body.find("table.cv-pt");
			const me = this;
			$t.on("dragstart", ".cv-pt-eintrag[draggable='true']", function (ev) {
				const el = this;
				const quelle = {
					doctype: el.dataset.doctype,
					name: el.dataset.name,
					art: el.dataset.art,
					von: el.dataset.von,
					mitarbeiter: el.dataset.mitarbeiter,
					user: el.dataset.user,
				};
				ev.originalEvent.dataTransfer.effectAllowed = "move";
				ev.originalEvent.dataTransfer.setData("text/plain", JSON.stringify(quelle));
				$(el).addClass("cv-pt-ziehen");
				me.quelle = quelle;
			});
			$t.on("dragend", ".cv-pt-eintrag", function () {
				$(this).removeClass("cv-pt-ziehen");
				$t.find(".cv-pt-ziel").removeClass("cv-pt-ziel");
				me.quelle = null;
			});
			$t.on("dragover", "td.cv-pt-zelle", function (ev) {
				if (!me.quelle || me.daten.schreibgeschuetzt) return;
				ev.preventDefault();
				ev.originalEvent.dataTransfer.dropEffect = "move";
				$(this).addClass("cv-pt-ziel");
			});
			$t.on("dragleave", "td.cv-pt-zelle", function () {
				$(this).removeClass("cv-pt-ziel");
			});
			$t.on("drop", "td.cv-pt-zelle", function (ev) {
				ev.preventDefault();
				$(this).removeClass("cv-pt-ziel");
				let quelle = me.quelle;
				try {
					quelle = JSON.parse(ev.originalEvent.dataTransfer.getData("text/plain")) || quelle;
				} catch (e) {
					// kein JSON
				}
				me.quelle = null;
				if (!quelle) return;
				const ziel = { mitarbeiter: this.dataset.mitarbeiter || "", user: this.dataset.user || "", datum: this.dataset.datum };
				me.verschieben(quelle, ziel, 0);
			});
			$t.on("click", ".cv-pt-eintrag", function (ev) {
				ev.preventDefault();
				const el = this;
				const zeile = (me.daten.zeilen || []).find((z) => (z.mitarbeiter || "") === (el.dataset.mitarbeiter || ""));
				const liste = zeile ? zeile.eintraege : me.daten.nicht_zugewiesen || [];
				const eintrag = (liste || []).find((e) => e.doctype === el.dataset.doctype && e.name === el.dataset.name);
				if (eintrag) me.eintrag_dialog(eintrag, el.dataset.mitarbeiter || "");
			});
		}

		verschieben(quelle, ziel, trotzdem) {
			const gleicher_tag = (quelle.von || "").slice(0, 10) === ziel.datum;
			const gleicher_mann = (quelle.mitarbeiter || "") === (ziel.mitarbeiter || "");
			if (gleicher_tag && gleicher_mann) return;
			let aufruf;
			if (quelle.doctype === "Event") {
				if (!ziel.user) {
					frappe.msgprint(__("Ein Besichtigungstermin braucht einen Meister - bitte auf eine Mitarbeiter-Zeile ziehen."));
					return;
				}
				aufruf = {
					method: API_TERMIN + "termin_verschieben",
					args: { termin: quelle.name, beginn: `${ziel.datum} ${uhrzeit(quelle.von)}:00`, meister: ziel.user, trotzdem },
				};
			} else if (quelle.doctype === "Task") {
				aufruf = {
					method: API + "arbeitspaket_verschieben",
					args: {
						task: quelle.name,
						datum: ziel.datum,
						mitarbeiter: ziel.mitarbeiter || null,
						von_mitarbeiter: quelle.mitarbeiter || null,
						freigeben: !ziel.mitarbeiter && quelle.mitarbeiter ? 1 : 0,
						trotzdem,
					},
				};
			} else {
				return;
			}
			frappe
				.call(
					Object.assign(aufruf, {
						silent: true,
						freeze: true,
						freeze_message: __("Wird verschoben …"),
						error: (r) => this.konflikt_dialog(r, () => this.verschieben(quelle, ziel, 1), __("Trotzdem verschieben")),
					})
				)
				.then((r) => {
					const m = (r && r.message) || null;
					if (!m) return;
					frappe.show_alert({ message: quelle.doctype === "Event" ? __("Termin verschoben") : __("Arbeitspaket verschoben"), indicator: "green" }, 5);
					(m.hinweise || []).forEach((h) => frappe.show_alert({ message: esc(h), indicator: "orange" }, 10));
					this.laden();
					if (quelle.doctype === "Event" && m.bestaetigung) this.bestaetigung(m);
				});
		}

		konflikt_dialog(r, trotzdem, knopf) {
			if (!r || r.exc_type !== "TerminKonflikt") {
				this.fehler_anzeigen(r);
				return;
			}
			let liste = r.konflikte || [];
			if (!liste.length && r._server_messages) {
				try {
					liste = JSON.parse(r._server_messages).map((m) => ({ text: frappe.utils.strip_html(JSON.parse(m).message || m) }));
				} catch (e) {
					liste = [];
				}
			}
			const d = new frappe.ui.Dialog({
				title: __("Überschneidung"),
				fields: [{ fieldtype: "HTML", fieldname: "liste" }],
				primary_action_label: knopf,
				primary_action() {
					d.hide();
					trotzdem();
				},
				secondary_action_label: __("Abbrechen"),
			});
			d.fields_dict.liste.$wrapper.html(
				`<div class="alert alert-warning small"><ul class="mb-0">${liste
					.map((k) => `<li>${esc(k.text)}</li>`)
					.join("")}</ul></div><p class="small">${esc(__("Der Konflikt wird am Beleg vermerkt, wenn Sie trotzdem fortfahren."))}</p>`
			);
			d.show();
		}

		fehler_anzeigen(r) {
			let text = "";
			if (r && r._server_messages) {
				try {
					text = JSON.parse(r._server_messages)
						.map((m) => frappe.utils.strip_html(JSON.parse(m).message || m))
						.join(" ");
				} catch (e) {
					text = "";
				}
			}
			frappe.msgprint({ title: __("Nicht möglich"), indicator: "red", message: esc(text || __("Die Änderung wurde nicht gespeichert.")) });
		}

		bestaetigung(m) {
			// Text der Kundenbestätigung neu (wird nicht automatisch gesendet) - Dialog aus lead_termin.bundle.js
			frappe.require("lead_termin.bundle.js", () => {
				const frm = { doc: { name: m.lead, email_id: m.empfaenger }, reload_doc() {} };
				const hinweise = [__("Termin {0} verschoben auf {1}.", [m.event, frappe.datetime.str_to_user(m.beginn)])].concat(m.hinweise || []);
				if ((m.konflikte || []).length) hinweise.push(__("Trotz Überschneidung verschoben: {0}", [m.konflikte.map((k) => k.text).join(" ")]));
				corevatis_erpnext.termin.bestaetigung_dialog(frm, Object.assign({ empfaenger: m.empfaenger }, m.bestaetigung || {}), hinweise);
			});
		}

		// --- Dialoge -------------------------------------------------------------------------

		eintrag_dialog(e, mitarbeiter) {
			const d = this.daten;
			const schreib = !d.schreibgeschuetzt && e.art !== "Abwesenheit";
			const zeilen = [
				[__("Art"), e.art === "Abwesenheit" ? __("Abwesenheit {0}", [e.titel]) : __(e.art)],
				[__("Zeit"), e.zeit],
				[__("Was"), e.titel + (e.untertitel ? ` – ${e.untertitel}` : "")],
				[__("Projekt"), e.projekt_name],
				[__("Ort"), e.ort],
				[__("Zugewiesen"), (e.zugewiesen || []).map((u) => frappe.user.full_name(u)).join(", ")],
			].filter((z) => z[1]);
			let info = `<table class="table table-sm small mb-2"><tbody>${zeilen
				.map((z) => `<tr><td class="text-muted" style="width:30%">${esc(z[0])}</td><td>${esc(z[1])}</td></tr>`)
				.join("")}</tbody></table>`;
			(e.konflikte || []).forEach((k) => (info += `<div class="alert alert-danger small">${esc(k)}</div>`));
			(e.hinweise || []).forEach((h) => (info += `<div class="alert alert-warning small">${esc(h)}</div>`));
			const fields = [{ fieldtype: "HTML", fieldname: "info" }];
			if (schreib) {
				fields.push({
					fieldtype: "Select",
					fieldname: "fahrzeug",
					label: __("Fahrzeug"),
					options: [{ value: "", label: __("(kein Fahrzeug)") }].concat(
						(d.fahrzeuge || []).map((f) => ({ value: f.value, label: frappe.utils.escape_html(f.label) }))
					),
					default: e.fahrzeug || "",
				});
			}
			const dialog = new frappe.ui.Dialog({
				title: e.art === "Abwesenheit" ? __("Abwesenheit") : e.art === "Termin" ? __("Besichtigungstermin") : __("Arbeitspaket"),
				fields,
				primary_action_label: schreib ? __("Fahrzeug speichern") : __("Schließen"),
				primary_action: (werte) => {
					if (!schreib) {
						dialog.hide();
						return;
					}
					this.fahrzeug_zuordnen(e, werte.fahrzeug || "", 0, dialog);
				},
			});
			dialog.fields_dict.info.$wrapper.html(info);
			if (e.lesbar) {
				dialog.add_custom_action(__("Öffnen"), () => {
					dialog.hide();
					frappe.set_route("Form", e.doctype, e.name);
				});
			}
			if (schreib && e.doctype === "Task") {
				dialog.add_custom_action(__("Mitarbeiter zuweisen …"), () => {
					dialog.hide();
					frappe.require("zuweisung.bundle.js", () =>
						corevatis_erpnext.planung.zuweisung_dialog(e.name, () => this.laden())
					);
				});
			}
			dialog.show();
		}

		fahrzeug_zuordnen(e, fahrzeug, trotzdem, dialog) {
			if ((e.fahrzeug || "") === (fahrzeug || "")) {
				dialog && dialog.hide();
				return;
			}
			frappe
				.call({
					method: API + "fahrzeug_zuordnen",
					args: { doctype: e.doctype, name: e.name, fahrzeug: fahrzeug || null, trotzdem },
					silent: true,
					freeze: true,
					error: (r) => this.konflikt_dialog(r, () => this.fahrzeug_zuordnen(e, fahrzeug, 1, dialog), __("Trotzdem zuordnen")),
				})
				.then((r) => {
					if (!r || !r.message) return;
					dialog && dialog.hide();
					frappe.show_alert({ message: fahrzeug ? __("Fahrzeug {0} zugeordnet", [esc(r.message.fahrzeug_name)]) : __("Fahrzeug entfernt"), indicator: "green" }, 5);
					this.laden();
				});
		}

		abwesenheit_dialog(vorgabe) {
			const leute = (this.daten && this.daten.zeilen) || [];
			const d = new frappe.ui.Dialog({
				title: __("Abwesenheit eintragen"),
				fields: [
					{
						fieldtype: "Select",
						fieldname: "mitarbeiter",
						label: __("Mitarbeiter"),
						options: [{ value: "", label: "" }].concat(
							leute.map((z) => ({ value: z.mitarbeiter, label: frappe.utils.escape_html(z.name) }))
						),
						reqd: 1,
						default: (vorgabe && vorgabe.mitarbeiter) || "",
					},
					{
						fieldtype: "Select",
						fieldname: "art",
						label: __("Art"),
						options: ["Urlaub", "Krank", "Schulung", "Sonstiges"].map((a) => ({ value: a, label: __(a) })),
						default: "Urlaub",
						reqd: 1,
					},
					{ fieldtype: "Column Break" },
					{ fieldtype: "Date", fieldname: "von", label: __("Von"), reqd: 1, default: (vorgabe && vorgabe.datum) || this.f.von.get_value() },
					{ fieldtype: "Date", fieldname: "bis", label: __("Bis"), reqd: 1, default: (vorgabe && vorgabe.datum) || this.f.von.get_value() },
					{ fieldtype: "Check", fieldname: "ganztags", label: __("Ganztags"), default: 1 },
					{ fieldtype: "Time", fieldname: "von_uhrzeit", label: __("Von Uhrzeit"), depends_on: "eval:!doc.ganztags" },
					{ fieldtype: "Time", fieldname: "bis_uhrzeit", label: __("Bis Uhrzeit"), depends_on: "eval:!doc.ganztags" },
					{ fieldtype: "Section Break" },
					{ fieldtype: "Small Text", fieldname: "notiz", label: __("Notiz") },
				],
				primary_action_label: __("Eintragen"),
				primary_action: (w) => {
					frappe.db
						.insert({
							doctype: "Abwesenheit",
							mitarbeiter: w.mitarbeiter,
							art: w.art,
							von: w.von,
							bis: w.bis,
							ganztags: cint(w.ganztags),
							von_uhrzeit: w.ganztags ? null : w.von_uhrzeit,
							bis_uhrzeit: w.ganztags ? null : w.bis_uhrzeit,
							notiz: w.notiz,
						})
						.then((doc) => {
							d.hide();
							frappe.show_alert({ message: __("Abwesenheit {0} eingetragen", [doc.name]), indicator: "green" }, 5);
							this.laden();
						});
				},
			});
			d.show();
		}

		reihenfolge_dialog() {
			const leute = ((this.daten && this.daten.zeilen) || []).map((z) => ({ value: z.mitarbeiter, label: z.name }));
			const d = new frappe.ui.Dialog({
				title: __("Reihenfolge der Mitarbeiter"),
				fields: [{ fieldtype: "HTML", fieldname: "liste" }],
				primary_action_label: __("Speichern"),
				primary_action: () => {
					frappe
						.xcall(API + "reihenfolge_speichern", { mitarbeiter: leute.map((l) => l.value) })
						.then(() => {
							d.hide();
							this.laden();
						});
				},
			});
			const zeichnen = () => {
				d.fields_dict.liste.$wrapper.html(
					leute
						.map(
							(l, i) => `<div class="cv-pt-reihe" data-index="${i}"><span class="cv-pt-reihe-name">${esc(l.label)}</span>
							<button class="btn btn-xs btn-default cv-pt-hoch" ${i === 0 ? "disabled" : ""} title="${esc(__("nach oben"))}">▲</button>
							<button class="btn btn-xs btn-default cv-pt-runter" ${i === leute.length - 1 ? "disabled" : ""} title="${esc(
								__("nach unten")
							)}">▼</button></div>`
						)
						.join("")
				);
			};
			zeichnen();
			d.fields_dict.liste.$wrapper.on("click", ".cv-pt-hoch, .cv-pt-runter", function () {
				const i = parseInt($(this).closest(".cv-pt-reihe").attr("data-index"), 10);
				const j = $(this).hasClass("cv-pt-hoch") ? i - 1 : i + 1;
				if (j < 0 || j >= leute.length) return;
				[leute[i], leute[j]] = [leute[j], leute[i]];
				zeichnen();
			});
			d.show();
		}

		drucken() {
			const a = this.args();
			const url =
				"/api/method/" +
				API +
				"drucken?" +
				$.param({ von: a.von, wochenende: a.wochenende, projekt: a.projekt || "" });
			window.open(frappe.urllib.get_full_url(url), "_blank");
		}
	}

	corevatis_erpnext.plantafel.Tafel = Tafel;
})();
