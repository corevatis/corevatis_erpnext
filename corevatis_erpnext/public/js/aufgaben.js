// Corevatis – Aufgaben am Vorgang (Phase 3 P3.4, Handwerker#96).
// Knopf „Aufgabe“ an Anfrage, Auftrag und Projekt (Dialog: Titel, fällig, zuständig, Beschreibung),
// Knöpfe „Erledigt“/„Wieder öffnen“ am ToDo und die Liste „Aufgaben“ (ToDo-Liste in Handwerkersprache,
// Überfälliges rot). Die Aufgaben sind ToDos mit Kern-Feldern (App corevatis, corevatis.aufgaben); Anlegen
// und Erledigen über die Kern-Methoden corevatis.aufgaben.desk.* mit denselben Prüfungen wie in der App.
// Ohne Kern fehlt das Feld cv_quelle am ToDo: dann gibt es keinen Knopf und keine Anpassung der Liste.
// Die Datei steht in doctype_js (Lead, Sales Order, Project, ToDo) und doctype_list_js (ToDo).
frappe.provide("corevatis_erpnext.aufgaben");

(function () {
	const A = corevatis_erpnext.aufgaben;
	const METHODE = "corevatis.aufgaben.desk.";
	const ROLLEN = ["Büro", "Meister", "Monteur", "System Manager"];
	const VORGANG_DOCTYPES = ["Lead", "Sales Order", "Project"];
	const VORGANG_LABEL = { Lead: "Anfrage", "Sales Order": "Auftrag", Project: "Projekt" };
	const STATUS = {
		Open: [__("Offen"), "orange"],
		Closed: [__("Erledigt"), "green"],
		Cancelled: [__("Verworfen"), "gray"],
	};
	const esc = (s) => frappe.utils.escape_html(s === null || s === undefined ? "" : String(s));

	A.darf = function () {
		return frappe.session.user === "Administrator" || (frappe.user_roles || []).some((r) => ROLLEN.includes(r));
	};

	// Kern installiert: das ToDo hat die Kern-Felder (Meta wird bei Bedarf nachgeladen)
	A.kern_da = function () {
		return new Promise((resolve) => {
			frappe.model.with_doctype("ToDo", () => resolve(!!frappe.meta.get_docfield("ToDo", "cv_quelle")));
		});
	};

	A.ueberfaellig = function (doc) {
		return doc.status === "Open" && doc.date && doc.date < frappe.datetime.get_today();
	};

	function zustaendige_laden() {
		if (A._zustaendige) return Promise.resolve(A._zustaendige);
		return frappe
			.call({ method: METHODE + "zustaendige", type: "GET" })
			.then((r) => (A._zustaendige = r.message || []));
	}

	// Dialog „Aufgabe“. opts: { vorgang_doctype, vorgang, titel, nach_anlegen(r) }
	A.dialog = function (opts = {}) {
		zustaendige_laden().then((liste) => {
			const fest = !!(opts.vorgang_doctype && opts.vorgang);
			const fields = [
				{
					fieldname: "titel",
					fieldtype: "Data",
					label: __("Was ist zu tun?"),
					reqd: 1,
					length: 140,
					description: __("z. B. Dichtschlämme nachbestellen"),
				},
			];
			if (fest) {
				fields.push({
					fieldname: "vorgang_html",
					fieldtype: "HTML",
					options: `<p class="text-muted small">${esc(__(VORGANG_LABEL[opts.vorgang_doctype]))}: ${esc(
						opts.titel || opts.vorgang
					)}</p>`,
				});
			} else {
				fields.push(
					{
						fieldname: "vorgang_doctype",
						fieldtype: "Select",
						label: __("Vorgang"),
						options: [{ label: "", value: "" }].concat(
							VORGANG_DOCTYPES.map((d) => ({ label: __(VORGANG_LABEL[d]), value: d }))
						),
					},
					{
						fieldname: "vorgang",
						fieldtype: "Dynamic Link",
						label: __("Anfrage, Auftrag oder Projekt"),
						options: "vorgang_doctype",
						depends_on: "eval:doc.vorgang_doctype",
					}
				);
			}
			fields.push(
				{ fieldtype: "Section Break" },
				{ fieldname: "faellig", fieldtype: "Date", label: __("Fällig bis") },
				{
					fieldname: "prioritaet",
					fieldtype: "Select",
					label: __("Wichtigkeit"),
					options: [
						{ label: __("Niedrig"), value: "Niedrig" },
						{ label: __("Normal"), value: "Mittel" },
						{ label: __("Hoch"), value: "Hoch" },
					],
					default: "Mittel",
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "zustaendig",
					fieldtype: "Select",
					label: __("Wer kümmert sich?"),
					reqd: 1,
					options: [{ label: "", value: "" }].concat(
						liste.map((b) => ({ label: `${b.full_name} (${__(b.rolle)})`, value: b.name }))
					),
				},
				{ fieldtype: "Section Break" },
				{ fieldname: "beschreibung", fieldtype: "Small Text", label: __("Beschreibung") }
			);
			const d = new frappe.ui.Dialog({
				title: __("Aufgabe"),
				fields,
				primary_action_label: __("Aufgabe anlegen"),
				primary_action(v) {
					d.get_primary_btn().prop("disabled", true);
					frappe
						.call({
							method: METHODE + "anlegen",
							args: {
								titel: v.titel,
								zustaendig: v.zustaendig,
								vorgang_doctype: fest ? opts.vorgang_doctype : v.vorgang_doctype || "",
								vorgang: fest ? opts.vorgang : v.vorgang_doctype ? v.vorgang || "" : "",
								faellig: v.faellig || "",
								beschreibung: v.beschreibung || "",
								prioritaet: v.prioritaet || "",
							},
							freeze: true,
						})
						.then((r) => {
							d.hide();
							frappe.show_alert({ message: __("Aufgabe angelegt"), indicator: "green" });
							if (opts.nach_anlegen) opts.nach_anlegen(r.message || {});
						})
						.always(() => d.get_primary_btn().prop("disabled", false));
				},
			});
			d.show();
		});
	};

	A.erledigen = function (name, erledigt) {
		return frappe.call({
			method: METHODE + "erledigen",
			args: { aufgabe: name, erledigt: erledigt ? 1 : 0 },
			freeze: true,
		});
	};

	// Liste „Aufgaben“: Titel statt Beschreibung, Status als Pille (überfällig rot), Sprung zum Vorgang.
	// Frappe setzt listview_settings["ToDo"] in seiner Listendatei; die Datei hier läuft danach und
	// ergänzt die Einstellungen bei jedem Laden.
	function liste_einrichten() {
		const s = (frappe.listview_settings["ToDo"] = frappe.listview_settings["ToDo"] || {});
		if (s._cv_aufgaben) return;
		s._cv_aufgaben = true;
		s.add_fields = Array.from(
			new Set(
				(s.add_fields || []).concat([
					"cv_titel",
					"cv_quelle",
					"cv_vorgang_doctype",
					"cv_vorgang",
					"status",
					"date",
				])
			)
		);
		const onload_frappe = s.onload;
		s.onload = function (listview) {
			if (onload_frappe) onload_frappe(listview);
			A.kern_da().then((da) => {
				if (!da) return;
				listview.page.set_title(__("Aufgaben"));
				if (A.darf() && !listview.cv_aufgabe_knopf) {
					listview.cv_aufgabe_knopf = true;
					listview.page.add_inner_button(__("Neue Aufgabe"), () =>
						A.dialog({ nach_anlegen: () => listview.refresh() })
					);
				}
			});
		};
		s.get_indicator = function (doc) {
			if (A.ueberfaellig(doc)) return [__("Überfällig"), "red", "status,=,Open"];
			const st = STATUS[doc.status];
			return st ? [st[0], st[1], "status,=," + doc.status] : [__(doc.status), "gray", "status,=," + doc.status];
		};
		s.formatters = Object.assign({}, s.formatters, {
			description(value, df, doc) {
				return doc.cv_titel ? esc(doc.cv_titel) : value;
			},
			date(value, df, doc) {
				if (!value) return "";
				const text = esc(frappe.datetime.str_to_user(value));
				return A.ueberfaellig(doc) ? `<span class="text-danger bold">${text}</span>` : text;
			},
		});
		s.button = {
			show: (doc) => !!((doc.cv_vorgang_doctype && doc.cv_vorgang) || doc.reference_name),
			get_label: () => __("Öffnen"),
			get_description: (doc) =>
				doc.cv_vorgang
					? __("{0} {1} öffnen", [__(VORGANG_LABEL[doc.cv_vorgang_doctype] || doc.cv_vorgang_doctype), doc.cv_vorgang])
					: __("Open {0}", [`${__(doc.reference_type)}: ${doc.reference_name}`]),
			action: (doc) =>
				doc.cv_vorgang
					? frappe.set_route("Form", doc.cv_vorgang_doctype, doc.cv_vorgang)
					: frappe.set_route("Form", doc.reference_type, doc.reference_name),
		};
	}
	liste_einrichten();

	if (A._eingerichtet) return;
	A._eingerichtet = true;

	// Knopf „Aufgabe“ an Anfrage, Auftrag und Projekt
	function vorgang_titel(frm) {
		return frm.doc.lead_name || frm.doc.company_name || frm.doc.project_name || frm.doc.customer_name || frm.doc.name;
	}
	const am_vorgang = {
		refresh(frm) {
			if (frm.is_new() || frm.doc.docstatus === 2 || !A.darf()) return;
			A.kern_da().then((da) => {
				if (!da) return;
				frm.add_custom_button(
					__("Aufgabe"),
					() =>
						A.dialog({
							vorgang_doctype: frm.doctype,
							vorgang: frm.doc.name,
							titel: vorgang_titel(frm),
							nach_anlegen: () => {
								if (corevatis_erpnext.prozess && corevatis_erpnext.prozess.render) {
									corevatis_erpnext.prozess.render(frm);
								}
							},
						}),
					__("Create")
				);
			});
		},
	};
	VORGANG_DOCTYPES.forEach((dt) => frappe.ui.form.on(dt, am_vorgang));

	// ToDo-Formular: bei Aufgaben „Erledigt“/„Wieder öffnen“ statt Frappes „Close“/„Reopen“,
	// Sprung zum Vorgang, Hinweis auf die Quelle
	frappe.ui.form.on("ToDo", {
		refresh(frm) {
			if (frm.is_new() || !frm.doc.cv_quelle) return;
			frm.remove_custom_button(__("Close"));
			frm.remove_custom_button(__("Reopen"));
			frm.remove_custom_button(__("New"));
			if (frm.doc.cv_vorgang_doctype && frm.doc.cv_vorgang) {
				frm.add_custom_button(
					__("{0} öffnen", [__(VORGANG_LABEL[frm.doc.cv_vorgang_doctype] || frm.doc.cv_vorgang_doctype)]),
					() => frappe.set_route("Form", frm.doc.cv_vorgang_doctype, frm.doc.cv_vorgang)
				);
			}
			if (frm.doc.cv_quelle === "Meldung" && frm.doc.reference_name) {
				frm.set_intro(__("Aufgabe aus der Meldung {0} von der Baustelle.", [frm.doc.reference_name]), "blue");
			} else if (A.ueberfaellig(frm.doc)) {
				frm.set_intro(__("Überfällig seit {0}.", [frappe.datetime.str_to_user(frm.doc.date)]), "red");
			}
			if (frm.doc.status === "Cancelled") return;
			const erledigt = frm.doc.status === "Closed";
			frm.add_custom_button(
				erledigt ? __("Wieder öffnen") : __("Erledigt"),
				() => A.erledigen(frm.doc.name, !erledigt).then(() => frm.reload_doc()),
				null,
				erledigt ? "btn-default" : "btn-success"
			);
		},
	});
})();
