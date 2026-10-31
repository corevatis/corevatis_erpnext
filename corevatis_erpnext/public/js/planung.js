// Corevatis – Arbeitspakete und Materialbedarf aus dem Auftrag (Aufgabe 5.1)
// Server: corevatis_erpnext.planung (Rollen Büro/Meister), an Auftrag (eingereicht) und Projekt;
// am Projekt Zustandstext, ein Hauptknopf und die Arbeitspakete (corevatis_erpnext.planung.projekt.zustand)
frappe.provide("corevatis_erpnext.planung");

(function () {
	const PLANUNG = "corevatis_erpnext.planung.";
	const ROLLEN = ["Büro", "Meister", "System Manager", "Administrator"];
	const GRUPPE = __("Planung");
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));

	function link(doctype, name, label) {
		return corevatis_erpnext.beleg_link(doctype, name, label);
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

	// --- Baustellenakte (Kern 3.12, corevatis.api.v1.baustellenakte) ---------------------------
	const AKTE = "corevatis.api.v1.baustellenakte";
	const TYP = {
		zeit: __("Zeit"),
		foto: __("Foto"),
		rapport: __("Tagesrapport"),
		meldung: __("Meldung"),
		material: __("Material"),
		kommentar: __("Kommentar"),
		aufgabe: __("Arbeitspaket"),
	};

	corevatis_erpnext.planung.baustellenakte = function (frm) {
		const projekt = frm.doctype === "Project" ? frm.doc.name : frm.doc.project;
		frappe
			.call({ method: AKTE, type: "GET", args: { projekt, limit: 200 }, freeze: true })
			.then((r) => {
				const antwort = r.message || {};
				if (!antwort.ok) {
					const fehler = (antwort.error && antwort.error.message) || __("Die Baustellenakte ist nicht verfügbar.");
					frappe.msgprint({ title: __("Baustellenakte"), indicator: "orange", message: esc(fehler) });
					return;
				}
				const d = antwort.data || {};
				const k = d.kennzahlen || {};
				const kopf = Object.keys(k)
					.map((key) => `<span class="indicator-pill gray" style="margin:0 4px 4px 0">${esc(key)}: ${esc(k[key])}</span>`)
					.join("");
				const zeilen = (d.items || [])
					.map(
						(e) =>
							`<tr><td class="text-nowrap">${esc(e.zeitpunkt ? frappe.datetime.str_to_user(e.zeitpunkt) : e.datum || "")}</td><td>${esc(
								TYP[e.typ] || e.typ
							)}</td><td><b>${esc(e.titel)}</b>${e.text ? `<div class="text-muted small">${esc(e.text)}</div>` : ""}</td><td>${esc(
								e.benutzer_name || ""
							)}</td></tr>`
					)
					.join("");
				frappe.msgprint({
					title: __("Baustellenakte {0}", [projekt]),
					wide: true,
					message:
						`<div>${kopf}</div>` +
						(zeilen
							? `<table class="table table-sm small"><thead><tr><th>${__("Wann")}</th><th>${__("Art")}</th><th>${__("Was")}</th><th>${__("Wer")}</th></tr></thead><tbody>${zeilen}</tbody></table>`
							: `<p class="text-muted">${esc(__("Noch keine Einträge: Zeiten, Fotos, Rapporte und Meldungen der Monteure erscheinen hier."))}</p>`),
				});
			})
			.catch(() => {
				frappe.msgprint({
					title: __("Baustellenakte"),
					indicator: "orange",
					message: esc(__("Die Baustellenakte kommt aus der Kern-App Corevatis und ist hier nicht verfügbar.")),
				});
			});
	};

	// --- Projekt verständlich (Rückmeldung 31.10., Muster Angebot D8) ---------------------------
	// Zustandstext oben, genau ein Hauptknopf je Zustand, Arbeitspakete im Formular, ERPNext-Menüs weg
	function css() {
		if (document.getElementById("cv-arbeitspakete-css")) return;
		const style = document.createElement("style");
		style.id = "cv-arbeitspakete-css";
		style.textContent = `
.cv-ap { margin: 0 0 var(--margin-md, 12px); padding: 10px 12px; border: 1px solid var(--border-color);
	border-radius: var(--border-radius-md, 8px); background: var(--fg-color); }
.cv-ap-kopf { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.cv-ap-kopf .cv-ap-titel { font-weight: 600; }
.cv-ap-kopf .cv-ap-zahlen { color: var(--text-muted); font-size: var(--text-sm, 12px); }
.cv-ap table { margin-bottom: 0; }
.cv-ap td, .cv-ap th { vertical-align: top; }
@media (max-width: 767px) { .cv-ap .cv-ap-termin { display: none; } }
`;
		document.head.appendChild(style);
	}

	function termin(t) {
		if (!t.start) return "";
		const von = frappe.datetime.str_to_user(t.start);
		const bis = t.ende ? frappe.datetime.str_to_user(t.ende) : "";
		return bis ? `${von} – ${bis}` : von;
	}

	function status_pill(status) {
		const farbe = { Open: "orange", Working: "blue", "Pending Review": "purple", Overdue: "red", Completed: "green" }[status] || "gray";
		return `<span class="indicator-pill ${farbe}">${esc(__(status))}</span>`;
	}

	function arbeitspakete_anzeigen(frm, z) {
		const wrapper = $(frm.layout && frm.layout.wrapper);
		if (!wrapper.length) return;
		let el = wrapper.children(".cv-ap");
		if (!el.length) {
			el = $('<div class="cv-ap"></div>');
			const leiste = wrapper.children(".cv-pl");
			leiste.length ? leiste.after(el) : wrapper.prepend(el);
		}
		css();
		const k = z.kennzahlen || {};
		const pakete = z.arbeitspakete || [];
		const zeilen = pakete
			.map((t) => {
				const wer = (t.zugewiesen || [])
					.map((p) => esc(p.name) + (p.erledigt ? ` <span class="text-muted">(${esc(__("erledigt"))})</span>` : ""))
					.join(", ");
				return `<tr>
					<td>${link("Task", t.name, t.verfahren ? __(t.verfahren === "Sonstiges" ? "Allgemeine Leistungen" : t.verfahren) : t.titel)}
						<div class="text-muted small">${esc(t.titel)}</div></td>
					<td class="text-right">${zahl(t.sollstunden)} h</td>
					<td class="cv-ap-termin">${esc(termin(t))}</td>
					<td>${wer || `<span class="text-muted">${esc(__("niemand"))}</span>`}</td>
					<td>${status_pill(t.status)}</td></tr>`;
			})
			.join("");
		const tabelle = zeilen
			? `<table class="table table-sm small"><thead><tr><th>${__("Verfahren")}</th><th class="text-right">${__("Soll")}</th><th class="cv-ap-termin">${__(
					"Termin"
			  )}</th><th>${__("Zugewiesen an")}</th><th>${__("Status")}</th></tr></thead><tbody>${zeilen}</tbody></table>`
			: `<p class="text-muted small" style="margin:0">${esc(__("Noch keine Arbeitspakete."))}</p>`;
		el.html(`<div class="cv-ap-kopf"><span class="cv-ap-titel">${esc(__("Arbeitspakete"))}</span>
			<span class="cv-ap-zahlen">${esc(
				__("{0} Arbeitspakete, {1} zugewiesen, {2} Zeitbuchungen", [k.arbeitspakete || 0, k.zugewiesen || 0, k.zeitbuchungen || 0])
			)}</span></div>${tabelle}`);
	}

	function aktion_ausfuehren(frm, key) {
		if (key === "arbeitspakete_anlegen") {
			aufrufen(frm, "tasks_erzeugen", { project: frm.doc.name }, __("Arbeitspakete werden angelegt …"), ergebnis_arbeitspakete);
		} else if (key === "arbeitspaket_anlegen") {
			frappe.new_doc("Task", { project: frm.doc.name });
		} else if (key === "wochenplan") {
			// Plantafel (P1.4) mit Projektfilter - der Wochenplan 5.2 ist darin aufgegangen
			frappe.set_route("plantafel", { projekt: frm.doc.name, ansicht: "woche" });
		} else if (key === "baustellenakte") {
			corevatis_erpnext.planung.baustellenakte(frm);
		}
	}

	function erpnext_menues_entfernen(frm) {
		// ERPNext „Aktionen“ (Projekt duplizieren, Kosten aktualisieren, Status) und „Ansicht“
		// (Gantt, Kanban) verwirren den Betrieb; „+“ in den Verbindungen nur für Aufgaben
		for (const gruppe of ["Actions", "View"]) {
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
		frm.dashboard &&
			frm.dashboard.links_area &&
			frm.dashboard.links_area.body.find(".btn-new").each((i, b) => {
				if ($(b).attr("data-doctype") !== "Task") $(b).addClass("hidden");
			});
	}

	function projekt_zustand(frm) {
		const token = `${frm.doc.name}/${frm.doc.modified}`;
		frm.__cv_ap_token = token;
		frappe.xcall(PLANUNG + "projekt.zustand", { project: frm.doc.name }).then((z) => {
			if (!z || frm.__cv_ap_token !== token) return;
			frm.dashboard.clear_headline && frm.dashboard.clear_headline();
			frm.set_intro(esc(z.text), z.farbe || "blue");
			arbeitspakete_anzeigen(frm, z);
			const haupt = z.aktion && z.aktion.key;
			if (haupt && !frm.is_dirty()) {
				frm.page.set_primary_action(z.aktion.label, () => aktion_ausfuehren(frm, haupt));
			}
			if (!z.darf_planen) return;
			// Weitere Schritte im Menü „Planung“ (nicht der Hauptknopf)
			if (frm.doc.sales_order) {
				frm.add_custom_button(
					__("Materialbedarf erzeugen"),
					() => aufrufen(frm, "materialbedarf_erzeugen", { project: frm.doc.name }, __("Materialbedarf wird ermittelt …"), ergebnis_materialbedarf),
					GRUPPE
				);
			}
			if (haupt !== "wochenplan") {
				frm.add_custom_button(__("Wochenplan öffnen"), () => aktion_ausfuehren(frm, "wochenplan"), GRUPPE);
			}
			if (haupt !== "baustellenakte" && (z.arbeitspakete || []).length) {
				frm.add_custom_button(__("Baustellenakte"), () => aktion_ausfuehren(frm, "baustellenakte"), GRUPPE);
			}
		});
	}

	// ERPNext-Fertigung am Auftrag (Rückmeldung 31.10.): „Erstellen → Arbeitsauftrag“ führte zu
	// „Herstellmenge … Bruchteil der Maßeinheit Nos“ und Pflichtlagern - Arbeit der Monteure sind die
	// Arbeitspakete am Projekt (planung.fertigung lehnt Arbeitsaufträge zu Aufträgen mit Projekt ab)
	const FERTIGUNG = [
		"Work Order",
		"Production Plan",
		"Request for Raw Materials",
		"Material Request",
		"Project",
		"Subcontracting Inward Order",
		"Maintenance Visit",
		"Maintenance Schedule",
	];

	function fertigung_entfernen(frm) {
		for (const label of FERTIGUNG) {
			try {
				frm.remove_custom_button(__(label), __("Create"));
			} catch (e) {
				// Button nicht vorhanden
			}
		}
	}

	frappe.ui.form.on("Sales Order", {
		refresh(frm) {
			if (frm.doc.docstatus === 1) {
				corevatis_erpnext.planung.buttons(frm, { sales_order: frm.doc.name });
				if (ROLLEN.some((r) => frappe.user.has_role(r))) {
					// ERPNext hängt die Buttons im Controller-refresh und teils nach Ajax-Antworten an
					for (const ms of [0, 300, 1200]) setTimeout(() => fertigung_entfernen(frm), ms);
				}
			}
		},
	});

	frappe.ui.form.on("Project", {
		refresh(frm) {
			if (frm.is_new()) {
				$(frm.layout && frm.layout.wrapper).children(".cv-ap").remove();
				return;
			}
			if (ROLLEN.some((r) => frappe.user.has_role(r))) {
				for (const ms of [0, 300, 1200]) setTimeout(() => erpnext_menues_entfernen(frm), ms);
			}
			projekt_zustand(frm);
		},
	});
})();
