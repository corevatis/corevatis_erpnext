// Leiste „Betrieb“ (Durchstich D1): Links im Desk tragen immer die Shell, in der der Benutzer
// steht. Ein rohes `/app/<doctype>/<name>` lädt die Seite neu und landet in der Shell des Moduls
// (CRM, Vertrieb, Kern-App) – genau das Springen, das wir abgestellt haben.
frappe.provide("corevatis_erpnext");

corevatis_erpnext.shell_slug = function () {
	const r = frappe.router;
	const shell = r && r.current_shell;
	if (shell && typeof r.shell_slug === "function") return r.shell_slug(shell);
	return "betrieb";
};

/** URL eines Belegs innerhalb der aktuellen Shell (Standard „Betrieb“). */
corevatis_erpnext.beleg_url = function (doctype, name) {
	return `/desk/${corevatis_erpnext.shell_slug()}/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}`;
};

/** Link-HTML auf einen Beleg; Text ist escaped. */
corevatis_erpnext.beleg_link = function (doctype, name, text) {
	if (!name) return "-";
	const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
	return `<a href="${corevatis_erpnext.beleg_url(doctype, name)}">${esc(text == null ? name : text)}</a>`;
};

// Nachrichten am Vorgang (P1.6): Ungelesen-Zähler am Eintrag „Nachrichten“ der Leiste „Betrieb“ und im
// Fenstertitel. Server: corevatis_erpnext.nachrichten.ungelesen_gesamt (0 ohne Kern). Sofort beim
// socket.io-Ereignis „corevatis_nachricht“ des Kerns (Handwerker#85), sonst alle 60 s, bei jedem
// Seitenwechsel und wenn der Tab wieder sichtbar wird; der Eintrag wird über seinen Link auf die Kern-Page gefunden.
corevatis_erpnext.nachrichten_badge = (function () {
	let gestartet = false;
	let letzter = null;

	function setzen(n) {
		document.querySelectorAll('a[href$="/nachrichten"], a[href*="/nachrichten?"]').forEach((a) => {
			let b = a.querySelector(".cv-na-sidebar-badge");
			if (!n) {
				if (b) b.remove();
				return;
			}
			if (!b) {
				b = document.createElement("span");
				b.className = "cv-na-sidebar-badge";
				b.style.cssText =
					"margin-left:auto;background:var(--red-500,#e03636);color:#fff;border-radius:10px;padding:0 6px;font-size:11px;font-weight:600;line-height:18px";
				a.appendChild(b);
			}
			b.textContent = String(n);
		});
		const titel = document.title.replace(/^\(\d+\) /, "");
		document.title = n ? `(${n}) ${titel}` : titel;
		letzter = n;
	}

	function laden() {
		if (document.hidden || !frappe.session || !frappe.session.user || frappe.session.user === "Guest") return;
		frappe
			.xcall("corevatis_erpnext.nachrichten.ungelesen_gesamt")
			.then((n) => setzen(cint(n)))
			.catch(() => {});
	}

	return {
		start() {
			if (gestartet) return;
			gestartet = true;
			$(document).on("page-change", () => setTimeout(() => (letzter === null ? laden() : setzen(letzter)), 300));
			$(document).on("visibilitychange", () => {
				if (!document.hidden) laden();
			});
			if (frappe.realtime && frappe.realtime.on) {
				frappe.realtime.on("corevatis_nachricht", frappe.utils.debounce(laden, 500));
			}
			setInterval(laden, 60000);
			setTimeout(laden, 1500);
		},
		laden,
	};
})();

$(document).ready(() => corevatis_erpnext.nachrichten_badge.start());

// Desk-Navigation (Handwerker docs/desk-navigation.md): Einträge der Leiste „Betrieb“ je Rolle.
// Der Server legt in `frappe.boot.cv_navigation[shell]` die Schlüssel der Einträge ab, die der
// Benutzer nicht sehen soll (corevatis_erpnext.prozess.navigation). Sie werden nur beim Zeichnen
// weggelassen und bleiben in `frappe.boot.module_sidebars`, damit Frappe geöffnete Belege weiter in
// der Shell „Betrieb“ hält (`shell_can_show` liest diese Liste). Leere Gruppen zeichnet Frappe nicht.
// Eingehängt wird an `nest_section_items`, das `setup()` nach dem Ereignis `sidebar_setup` aufruft –
// auch nach dem Neuladen der Leiste (Seitenleisten-Editor, Arbeitsbereich gespeichert).
corevatis_erpnext.navigation = {
	ausgeblendet(shell) {
		const je_shell = (frappe.boot && frappe.boot.cv_navigation) || {};
		return new Set(je_shell[shell] || []);
	},
	einhaengen(sidebar) {
		if (!sidebar || sidebar.__cv_navigation || typeof sidebar.nest_section_items !== "function") return;
		sidebar.__cv_navigation = true;
		const original = sidebar.nest_section_items;
		sidebar.nest_section_items = function () {
			const ergebnis = original.apply(this, arguments);
			const aus = corevatis_erpnext.navigation.ausgeblendet(this.current_module);
			if (aus.size && Array.isArray(this.sidebar_items)) {
				const sichtbar = (item) => !aus.has(item.key);
				this.sidebar_items = this.sidebar_items.filter(sichtbar);
				this.sidebar_items.forEach((item) => {
					if (Array.isArray(item.nested_items)) item.nested_items = item.nested_items.filter(sichtbar);
				});
			}
			return ergebnis;
		};
	},
};

$(document).on("sidebar_setup", (e, daten) => corevatis_erpnext.navigation.einhaengen(daten && daten.sidebar));
