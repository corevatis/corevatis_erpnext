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
