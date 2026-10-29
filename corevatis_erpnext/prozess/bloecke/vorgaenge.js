// Block „Vorgänge“ im Arbeitsbereich (Prozessleiste): Zähler je Schritt und die dringendsten
// Vorgänge. Daten: corevatis_erpnext.prozess.uebersicht.block_vorgaenge (Rechte des Benutzers).
// root_element = Shadow Root des Blocks (frappe.create_shadow_element).
(function () {
	const box = root_element.querySelector(".cv-vg");
	const liste = root_element.querySelector(".cv-vg-liste");
	const schritte = root_element.querySelector(".cv-vg-schritte");
	const esc = (s) => frappe.utils.escape_html(s === null || s === undefined ? "" : String(s));
	const farben = { rot: "red", gelb: "orange", "grün": "green", grau: "gray" };
	const bericht = (optionen) => {
		frappe.route_options = optionen || {};
		frappe.set_route("query-report", "Vorgangsliste");
	};
	box.querySelector(".cv-vg-alle").addEventListener("click", () => bericht({}));

	frappe
		.xcall("corevatis_erpnext.prozess.uebersicht.block_vorgaenge", { limit: 12 })
		.then((d) => {
			schritte.innerHTML = (d.schritte || [])
				.map(
					(s) =>
						`<a class="cv-vg-schritt" data-schritt="${esc(s.label)}"><span class="cv-vg-zahl">${esc(
							s.anzahl
						)}</span><span>${esc(__(s.label))}</span></a>`
				)
				.join("");
			schritte.querySelectorAll(".cv-vg-schritt").forEach((a) =>
				a.addEventListener("click", () => bericht({ schritt: a.dataset.schritt }))
			);
			const zeilen = d.zeilen || [];
			if (!zeilen.length) {
				liste.innerHTML = esc(__("Keine offenen Vorgänge."));
				return;
			}
			liste.classList.remove("text-muted");
			liste.innerHTML = `<table class="cv-vg-tabelle"><thead><tr>
				<th></th><th>${esc(__("Kunde / Objekt"))}</th><th>${esc(__("Schritt"))}</th>
				<th class="cv-vg-weg">${esc(__("Nächster Schritt"))}</th><th class="cv-vg-weg">${esc(__("Alter"))}</th>
				</tr></thead><tbody>${zeilen
					.map(
						(z) => `<tr class="cv-vg-zeile" data-lead="${esc(z.vorgang)}">
						<td><span class="indicator-pill ${farben[z.ampel] || "gray"}" title="${esc(z.ampel_text)}">${esc(
							z.ampel
						)}</span></td>
						<td>${esc(z.kunde)}<div class="cv-vg-klein">${esc(z.objekt_adresse)}</div></td>
						<td>${esc(z.schritt)}<div class="cv-vg-klein">${esc(z.ampel === "rot" ? z.ampel_text : "")}</div></td>
						<td class="cv-vg-weg">${esc(z.naechster_schritt)}</td>
						<td class="cv-vg-weg">${esc(__("{0} Tage", [z.alter_tage]))}</td></tr>`
					)
					.join("")}</tbody></table>`;
			if (d.gesamt > zeilen.length) {
				liste.insertAdjacentHTML(
					"beforeend",
					`<div class="cv-vg-klein" style="margin-top:8px">${esc(
						__("{0} von {1} Vorgängen – alle unter „Alle Vorgänge“.", [zeilen.length, d.gesamt])
					)}</div>`
				);
			}
			liste.querySelectorAll(".cv-vg-zeile").forEach((tr) =>
				tr.addEventListener("click", () => frappe.set_route("Form", "Lead", tr.dataset.lead))
			);
		})
		.catch(() => {
			liste.innerHTML = esc(__("Die Vorgänge konnten nicht geladen werden."));
		});
})();
