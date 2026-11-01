// Corevatis – Foto-Galerie am Befund (Aufgabe 3.7): Fotos der App (Tabelle „Fotos“ und
// Anhänge) als Thumbnails mit Vollbild, Funktion aus galerie.js (1.5). Befund kommt aus
// corevatis; ohne Kern lädt diese Datei nie. Kein HTML-Feld am Befund → Abschnitt im Dashboard.
//
// P3.3: Fotos, die als Pin auf einem Plan liegen, tragen die Pin-Nummer („Pin 3“); liegt der
// Befund selbst als Pin auf Plänen, steht darunter „Auf Plan …“ mit Link zum Plan. Die Nummern
// liefert der Kern (corevatis.plaene.desk.befund_pins, nur Pläne mit Leserecht).
(function () {
	const STYLE_ID = "cv-befund-pins-style";
	const CSS = `
.cv-galerie-thumb .cv-galerie-pin { position: absolute; top: 4px; right: 4px; left: auto; bottom: auto; padding: 1px 6px;
	border-radius: 9px; background: #1a73e8; color: #fff; font-size: 10px; font-weight: 600; line-height: 16px; width: auto; }
.cv-befund-plaene { margin: -4px 0 12px; font-size: var(--text-sm); color: var(--text-muted); }
.cv-befund-plaene a { margin-right: 10px; }
`;

	function stil() {
		if (!document.getElementById(STYLE_ID)) {
			const el = document.createElement("style");
			el.id = STYLE_ID;
			el.textContent = CSS;
			document.head.appendChild(el);
		}
	}

	function text(m) {
		return __("Pin {0}", [m.nummer]);
	}

	function plan_link(m) {
		const a = document.createElement("a");
		a.href = "/app/plan/" + encodeURIComponent(m.plan);
		a.textContent = `${m.plan_titel} · ${text(m)}`;
		return a;
	}

	function pins_zeigen(frm, container, bilder) {
		if (!frappe.model.can_read("Plan")) return;
		const docname = frm.docname;
		frappe
			.call({ method: "corevatis.plaene.desk.befund_pins", args: { befund: docname } })
			.then((r) => {
				if (frm.docname !== docname || !r.message) return;
				stil();
				const fotos = r.message.fotos || {};
				// Thumbnail-Quelle → Datei-URL (die Galerie zeigt thumbnail_url oder file_url)
				const urls = {};
				bilder.forEach((b) => {
					urls[b.thumbnail_url || b.file_url] = b.file_url;
				});
				container.querySelectorAll(".cv-galerie-thumb").forEach((knopf) => {
					const img = knopf.querySelector("img");
					const marken = img && fotos[urls[img.getAttribute("src")]];
					if (!marken || !marken.length) return;
					const badge = document.createElement("span");
					badge.className = "cv-galerie-pin";
					badge.textContent = marken.map(text).join(", ");
					badge.title = marken.map((m) => `${m.plan_titel} · ${text(m)}`).join("\n");
					knopf.appendChild(badge);
				});
				const ganze = r.message.befund || [];
				if (ganze.length) {
					const zeile = document.createElement("div");
					zeile.className = "cv-befund-plaene";
					zeile.append(__("Auf Plan") + ": ");
					ganze.forEach((m) => zeile.appendChild(plan_link(m)));
					container.appendChild(zeile);
				}
			});
	}

	frappe.ui.form.on("Befund", {
		refresh(frm) {
			corevatis_erpnext.galerie_einhaengen(frm, {
				fieldname: "cv_galerie",
				label: __("Fotos"),
				nachher: (container, bilder) => pins_zeigen(frm, container, bilder),
			});
		},
		after_save(frm) {
			frm.__cv_galerie_neu && frm.__cv_galerie_neu();
		},
	});
})();
