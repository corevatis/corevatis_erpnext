// Corevatis – Foto-Galerie für Desk-Formulare (Aufgabe 1.5)
//
// corevatis_erpnext.galerie(frm, {fieldname}) zeigt alle Bilder des Datensatzes als
// Thumbnails (Feldwerte Attach/Attach Image inkl. Kindtabellen und File-Anhänge,
// Server: corevatis_erpnext.crm.galerie.get_bilder). Klick öffnet eine Lightbox mit
// Vor/Zurück (Tasten ←/→, Wischen), Esc/Klick daneben schließt.
//
// Ziel ist das HTML-Feld `fieldname` (Standard cv_galerie); fehlt es, erscheint die
// Galerie als Abschnitt im Formular-Dashboard. Fotos mit Markierung aus der App (Befund Foto,
// `markiert`, P3.1) tragen das Kennzeichen „markiert“. Für andere Doctypes (z. B. Objekt aus 2.1)
// diese Datei zusätzlich in hooks.doctype_js eintragen und im refresh aufrufen.
frappe.provide("corevatis_erpnext");

(function () {
	const STYLE_ID = "cv-galerie-style";
	const CSS = `
.cv-galerie { margin: 4px 0 12px; }
.cv-galerie-leer { color: var(--text-muted); font-size: var(--text-sm); }
.cv-galerie-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 8px; }
.cv-galerie-thumb { position: relative; padding: 0; border: 1px solid var(--border-color); border-radius: var(--border-radius-md, 8px);
	background: var(--subtle-fg, #f4f5f6); overflow: hidden; aspect-ratio: 1 / 1; cursor: zoom-in; width: 100%; }
.cv-galerie-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.cv-galerie-thumb span { position: absolute; left: 0; right: 0; bottom: 0; padding: 2px 6px; font-size: 11px; color: #fff;
	background: linear-gradient(transparent, rgba(0,0,0,.65)); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; text-align: left; }
.cv-galerie-thumb:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.cv-galerie-thumb .cv-galerie-markiert { position: absolute; top: 4px; left: 4px; right: auto; bottom: auto; padding: 1px 6px;
	border-radius: 9px; background: #d93025; color: #fff; font-size: 10px; font-weight: 600; line-height: 16px; }
.cv-lightbox { position: fixed; inset: 0; z-index: 1060; background: rgba(0,0,0,.92); display: flex; flex-direction: column;
	height: 100vh; height: 100dvh; touch-action: pan-y; user-select: none; }
.cv-lightbox-bild { flex: 1; min-height: 0; display: flex; align-items: center; justify-content: center; padding: 8px 56px; }
.cv-lightbox-bild img { max-width: 100%; max-height: 100%; object-fit: contain; }
.cv-lightbox-fuss { color: #eee; padding: 8px 16px calc(12px + env(safe-area-inset-bottom)); font-size: 13px; display: flex;
	gap: 12px; justify-content: space-between; align-items: center; flex-wrap: wrap; }
.cv-lightbox-fuss a { color: #fff; text-decoration: underline; }
.cv-lightbox button { position: absolute; background: rgba(255,255,255,.12); color: #fff; border: 0; border-radius: 50%;
	width: 48px; height: 48px; font-size: 26px; line-height: 48px; text-align: center; cursor: pointer; padding: 0; }
.cv-lightbox button:hover, .cv-lightbox button:focus-visible { background: rgba(255,255,255,.28); outline: none; }
.cv-lightbox .cv-zu { top: calc(8px + env(safe-area-inset-top)); right: 8px; }
.cv-lightbox .cv-vor, .cv-lightbox .cv-zurueck { top: 50%; transform: translateY(-50%); }
.cv-lightbox .cv-zurueck { left: 4px; }
.cv-lightbox .cv-vor { right: 4px; }
@media (max-width: 576px) { .cv-lightbox-bild { padding: 8px 0; } .cv-galerie-grid { grid-template-columns: repeat(3, 1fr); } }
`;

	function style() {
		if (document.getElementById(STYLE_ID)) return;
		const el = document.createElement("style");
		el.id = STYLE_ID;
		el.textContent = CSS;
		document.head.appendChild(el);
	}

	function el(tag, attrs, text) {
		const node = document.createElement(tag);
		Object.entries(attrs || {}).forEach(([k, v]) => {
			if (v !== undefined && v !== null) node.setAttribute(k, v);
		});
		if (text !== undefined) node.textContent = text;
		return node;
	}

	// Gleiche Liste wie crm/galerie.py BILD_ENDUNGEN: kein SVG (aktiver Inhalt auf der
	// Site-Origin), „Original öffnen“ nur für diese Typen (Review Epic 1, E-11)
	const BILD_ENDUNGEN = new Set(["jpg", "jpeg", "png", "gif", "webp", "heic"]);

	// Entscheidend ist nur die Ziel-URL (Pfad ohne Query/Fragment; beim S3-Download der
	// Parameter file_name, nach dem der Server Typ und inline/attachment wählt) - nicht der
	// angezeigte Dateiname. Gleiche Regel wie crm/galerie.py endung_ziel.
	function endung_ziel(file_url) {
		let url;
		try {
			url = new URL(String(file_url || ""), window.location.origin);
		} catch (e) {
			return "";
		}
		if (url.origin !== window.location.origin) return "";
		let name = decodeURIComponent(url.pathname);
		if (name.startsWith("/api/method/frappe_s3_attachment.")) {
			name = url.searchParams.get("file_name") || "";
		} else {
			name = name.split("/").pop();
		}
		const teile = name.split(".");
		return teile.length > 1 ? teile.pop().toLowerCase() : "";
	}

	function erlaubt(bild) {
		return BILD_ENDUNGEN.has(endung_ziel(bild.file_url));
	}

	function datum(bild) {
		return bild.creation ? frappe.datetime.str_to_user(bild.creation) : "";
	}

	function unterschrift(bild) {
		return [bild.file_name, datum(bild), bild.quelle, bild.markiert ? __("markiert") : ""]
			.filter(Boolean)
			.join(" · ");
	}

	function ziel(frm, fieldname, label) {
		const field = frm.fields_dict[fieldname];
		if (field && field.$wrapper) {
			return field.$wrapper.get(0);
		}
		// Fallback ohne HTML-Feld: eigener Abschnitt im Dashboard
		frm.__cv_galerie_section =
			frm.__cv_galerie_section && document.body.contains(frm.__cv_galerie_section.get(0))
				? frm.__cv_galerie_section
				: frm.dashboard.add_section("<div></div>", label);
		frm.dashboard.show();
		return frm.__cv_galerie_section.find(".section-body").get(0) || frm.__cv_galerie_section.get(0);
	}

	function lightbox(bilder, start) {
		style();
		let index = start;
		const box = el("div", { class: "cv-lightbox", role: "dialog", "aria-modal": "true", "aria-label": __("Foto") });
		const flaeche = el("div", { class: "cv-lightbox-bild" });
		const img = el("img", { alt: "" });
		flaeche.appendChild(img);
		const fuss = el("div", { class: "cv-lightbox-fuss" });
		const text = el("span");
		const original = el("a", { target: "_blank", rel: "noopener" }, __("Original öffnen"));
		fuss.append(text, original);
		const zu = el("button", { class: "cv-zu", type: "button", "aria-label": __("Schließen") }, "×");
		const zurueck = el("button", { class: "cv-zurueck", type: "button", "aria-label": __("Vorheriges Foto") }, "‹");
		const vor = el("button", { class: "cv-vor", type: "button", "aria-label": __("Nächstes Foto") }, "›");
		box.append(flaeche, fuss, zu);
		if (bilder.length > 1) box.append(zurueck, vor);

		function zeige(i) {
			index = (i + bilder.length) % bilder.length;
			const bild = bilder[index];
			img.src = bild.file_url;
			img.alt = bild.file_name || "";
			if (erlaubt(bild)) {
				original.href = bild.file_url;
				original.hidden = false;
			} else {
				original.removeAttribute("href");
				original.hidden = true;
			}
			text.textContent = `${index + 1} / ${bilder.length} · ${unterschrift(bild)}`;
		}
		function schliessen() {
			document.removeEventListener("keydown", taste, true);
			document.body.style.overflow = ueberlauf;
			box.remove();
		}
		function taste(e) {
			if (e.key === "Escape") schliessen();
			else if (e.key === "ArrowRight") zeige(index + 1);
			else if (e.key === "ArrowLeft") zeige(index - 1);
			else return;
			e.preventDefault();
			e.stopPropagation();
		}
		zu.addEventListener("click", schliessen);
		vor.addEventListener("click", () => zeige(index + 1));
		zurueck.addEventListener("click", () => zeige(index - 1));
		flaeche.addEventListener("click", (e) => {
			if (e.target === flaeche) schliessen();
		});
		let x0 = null;
		box.addEventListener("touchstart", (e) => (x0 = e.touches[0].clientX), { passive: true });
		box.addEventListener("touchend", (e) => {
			if (x0 === null || bilder.length < 2) return;
			const dx = e.changedTouches[0].clientX - x0;
			x0 = null;
			if (Math.abs(dx) > 40) zeige(index + (dx < 0 ? 1 : -1));
		});

		const ueberlauf = document.body.style.overflow;
		document.body.style.overflow = "hidden";
		document.addEventListener("keydown", taste, true);
		document.body.appendChild(box);
		zeige(index);
		zu.focus();
	}

	function zeichnen(container, alle) {
		const bilder = alle.filter(erlaubt);
		container.replaceChildren();
		const wrap = el("div", { class: "cv-galerie" });
		if (!bilder.length) {
			wrap.appendChild(el("div", { class: "cv-galerie-leer" }, __("Noch keine Fotos – Bilder als Anhang hochladen.")));
			container.appendChild(wrap);
			return;
		}
		const grid = el("div", { class: "cv-galerie-grid" });
		bilder.forEach((bild, i) => {
			const knopf = el("button", { type: "button", class: "cv-galerie-thumb", title: unterschrift(bild) });
			const img = el("img", { src: bild.thumbnail_url || bild.file_url, alt: bild.file_name || "", loading: "lazy" });
			knopf.append(img, el("span", {}, datum(bild)));
			// P3.1: Foto aus der App mit Markierung (Kreis, Pfeil, Maß, Text, Pins) - das Bild enthält sie schon
			if (bild.markiert) knopf.appendChild(el("span", { class: "cv-galerie-markiert" }, __("markiert")));
			knopf.addEventListener("click", () => lightbox(bilder, i));
			grid.appendChild(knopf);
		});
		wrap.appendChild(grid);
		container.appendChild(wrap);
	}

	corevatis_erpnext.galerie = function (frm, opts) {
		opts = Object.assign({ fieldname: "cv_galerie", label: __("Fotos") }, opts || {});
		style();
		if (frm.is_new()) {
			const field = frm.fields_dict[opts.fieldname];
			if (field && field.$wrapper) field.$wrapper.empty();
			return Promise.resolve([]);
		}
		const container = ziel(frm, opts.fieldname, opts.label);
		const docname = frm.docname;
		if (!container.childElementCount) {
			container.appendChild(el("div", { class: "cv-galerie-leer" }, __("Fotos werden geladen …")));
		}
		return frappe
			.call({
				method: "corevatis_erpnext.crm.galerie.get_bilder",
				args: { doctype: frm.doctype, name: docname },
			})
			.then((r) => {
				if (frm.docname !== docname) return [];
				const bilder = r.message || [];
				zeichnen(container, bilder);
				// Ergänzungen je Doctype (z. B. Pin-Nummern am Befund, P3.3)
				if (opts.nachher) opts.nachher(container, bilder);
				return bilder;
			});
	};

	// Neu zeichnen nach Uploads/Änderungen an Anhangsfeldern (entprellt, je Formular einmal eingehängt)
	corevatis_erpnext.galerie_einhaengen = function (frm, opts) {
		const neu = frappe.utils.debounce(() => corevatis_erpnext.galerie(frm, opts), 300);
		frm.__cv_galerie_neu = neu;
		if (frm.attachments && !frm.attachments.__cv_galerie) {
			const refresh = frm.attachments.refresh.bind(frm.attachments);
			frm.attachments.refresh = function () {
				const result = refresh(...arguments);
				frm.__cv_galerie_neu && frm.__cv_galerie_neu();
				return result;
			};
			frm.attachments.__cv_galerie = true;
		}
		const registriert = (corevatis_erpnext.__galerie_felder = corevatis_erpnext.__galerie_felder || {});
		if (!registriert[frm.doctype]) {
			registriert[frm.doctype] = true;
			const handler = {};
			frm.meta.fields
				.filter((df) => ["Attach", "Attach Image"].includes(df.fieldtype))
				.forEach((df) => {
					handler[df.fieldname] = (f) => f.__cv_galerie_neu && f.__cv_galerie_neu();
				});
			if (Object.keys(handler).length) frappe.ui.form.on(frm.doctype, handler);
		}
		neu();
	};
})();
