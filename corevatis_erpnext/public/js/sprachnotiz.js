// Corevatis – Sprachnotiz am Befund, an der Baustellenmeldung und an der Aufgabe (Phase 3 P3.5, Handwerker#97).
//
// Der Meister diktiert den Befund, der Monteur spricht Meldung oder Aufgabe ein; die App lädt die Aufnahme
// (m4a) als private Datei hoch (Kern corevatis, sprachnotiz_upload). Hier: Abschnitt „Sprachnotiz“ im
// Formular-Dashboard mit Audio-Player (Feld sprachnotiz bzw. cv_sprachnotiz am ToDo; bei der Büro-Aufgabe
// einer Meldung die Sprachnotiz der Meldung). Abgespielt werden nur Audiodateien der eigenen Site
// (relativer Pfad /private/files/…, /files/… oder S3-Download der Site); das Leserecht prüft der Server beim
// Laden der Datei. Doctypes und Felder kommen aus dem Kern: ohne Kern fehlt das Feld, es erscheint nichts.
frappe.provide("corevatis_erpnext.sprachnotiz");

(function () {
	const S = corevatis_erpnext.sprachnotiz;
	const FELD = { Befund: "sprachnotiz", Baustellenmeldung: "sprachnotiz", ToDo: "cv_sprachnotiz" };
	const MELDUNG = "Baustellenmeldung";
	// wie corevatis.vor_ort.raeume.AUDIO_ENDUNGEN
	const AUDIO = new Set(["m4a", "aac", "mp3", "wav", "caf", "ogg", "opus", "webm"]);
	const esc = (s) => frappe.utils.escape_html(s === null || s === undefined ? "" : String(s));

	// Pfad (mit Query) einer abspielbaren Audiodatei der eigenen Site, sonst null. Entscheidend ist das
	// Ziel (Pfad bzw. beim S3-Download der Parameter file_name), nicht ein angezeigter Name
	// (Muster galerie.js endung_ziel).
	S.pfad = function (file_url) {
		let url;
		try {
			url = new URL(String(file_url || ""), window.location.origin);
		} catch (e) {
			return null;
		}
		if (url.origin !== window.location.origin) return null;
		let name = decodeURIComponent(url.pathname);
		if (name.startsWith("/api/method/frappe_s3_attachment.")) {
			name = url.searchParams.get("file_name") || "";
		} else if (!name.startsWith("/private/files/") && !name.startsWith("/files/")) {
			return null;
		}
		const teile = name.split("/").pop().split(".");
		return teile.length > 1 && AUDIO.has(teile.pop().toLowerCase()) ? url.pathname + url.search : null;
	};

	function abschnitt(frm) {
		if (!frm.__cv_sprachnotiz || !document.body.contains(frm.__cv_sprachnotiz.get(0))) {
			frm.__cv_sprachnotiz = frm.dashboard.add_section("<div></div>", __("Sprachnotiz"));
		}
		frm.dashboard.show();
		return frm.__cv_sprachnotiz;
	}

	function entfernen(frm) {
		if (frm.__cv_sprachnotiz) frm.__cv_sprachnotiz.remove();
		frm.__cv_sprachnotiz = null;
	}

	S.zeigen = function (frm, file_url, hinweis) {
		const pfad = S.pfad(file_url);
		if (!pfad) {
			entfernen(frm);
			return;
		}
		const ziel = abschnitt(frm);
		const body = ziel.find(".section-body").get(0) || ziel.get(0);
		body.innerHTML =
			`<div class="cv-sprachnotiz" style="display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin:4px 0 8px">` +
			`<audio controls preload="none" style="max-width:100%;min-width:260px" src="${esc(pfad)}"></audio>` +
			`<a href="${esc(pfad)}" target="_blank" rel="noopener noreferrer">${esc(__("Herunterladen"))}</a>` +
			(hinweis ? `<span class="text-muted small">${esc(hinweis)}</span>` : "") +
			`</div>`;
	};

	// Sprachnotiz des Formulars: eigenes Feld; Büro-Aufgabe einer Meldung ohne eigene → die der Meldung
	S.laden = function (frm) {
		const feld = FELD[frm.doctype];
		if (frm.is_new() || !feld || !frappe.meta.get_docfield(frm.doctype, feld)) {
			entfernen(frm);
			return Promise.resolve();
		}
		const eigene = frm.doc[feld];
		if (eigene || frm.doctype !== "ToDo" || frm.doc.reference_type !== MELDUNG || !frm.doc.reference_name) {
			S.zeigen(frm, eigene, frm.doctype === "Befund" ? __("Aufnahme des Diktats aus der App") : "");
			return Promise.resolve();
		}
		const docname = frm.docname;
		return frappe.db
			.get_value(MELDUNG, frm.doc.reference_name, "sprachnotiz")
			.then((r) => {
				if (frm.docname !== docname) return;
				const url = r && r.message && r.message.sprachnotiz;
				S.zeigen(frm, url, __("Sprachnotiz der Meldung {0}", [frm.doc.reference_name]));
			})
			.catch(() => entfernen(frm));
	};

	Object.keys(FELD).forEach((doctype) => {
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				S.laden(frm);
			},
			[FELD[doctype]](frm) {
				S.laden(frm);
			},
		});
	});
})();
