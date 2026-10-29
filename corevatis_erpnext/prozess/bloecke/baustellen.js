// Block „Meine Baustellen heute“ (Startseite Monteur im Desk): Arbeitspakete (Task) und
// Aufgaben (ToDo) des Benutzers. Daten: corevatis_erpnext.prozess.uebersicht.meine_baustellen.
(function () {
	const inhalt = root_element.querySelector(".cv-mb-inhalt");
	const esc = (s) => frappe.utils.escape_html(s === null || s === undefined ? "" : String(s));
	const route = (el) => frappe.set_route("Form", el.dataset.doctype, el.dataset.name);

	frappe
		.xcall("corevatis_erpnext.prozess.uebersicht.meine_baustellen")
		.then((d) => {
			const tasks = d.tasks || [];
			const todos = d.todos || [];
			if (!tasks.length && !todos.length) {
				inhalt.innerHTML = esc(__("Für heute ist nichts eingeplant."));
				return;
			}
			inhalt.classList.remove("text-muted");
			const gruppen = {};
			tasks.forEach((t) => {
				const key = t.project || "";
				(gruppen[key] = gruppen[key] || { name: t.project, titel: t.project_name, tasks: [] }).tasks.push(t);
			});
			let html = Object.values(gruppen)
				.map(
					(g) => `<div class="cv-mb-projekt">
					${
						g.name
							? `<div class="cv-mb-projekt-name" data-doctype="Project" data-name="${esc(g.name)}">${esc(
									g.titel || g.name
							  )}</div>`
							: `<div class="cv-mb-projekt-name">${esc(__("Ohne Projekt"))}</div>`
					}
					${g.tasks
						.map(
							(t) => `<div class="cv-mb-eintrag" data-doctype="Task" data-name="${esc(t.name)}">
							<span>${esc(t.subject)}</span><span class="cv-mb-klein">${esc(__(t.status))} · ${esc(
								Math.round(t.progress || 0)
							)} %</span></div>`
						)
						.join("")}</div>`
				)
				.join("");
			if (todos.length) {
				html += `<div class="cv-mb-abschnitt">${esc(__("Meine Aufgaben"))}</div><div class="cv-mb-projekt">${todos
					.map(
						(t) => `<div class="cv-mb-eintrag" data-doctype="ToDo" data-name="${esc(t.name)}">
						<span>${esc(t.description)}</span><span class="cv-mb-klein">${esc(
							t.date ? frappe.datetime.str_to_user(t.date) : ""
						)}</span></div>`
					)
					.join("")}</div>`;
			}
			inhalt.innerHTML = html;
			inhalt.querySelectorAll("[data-doctype][data-name]").forEach((el) => el.addEventListener("click", () => route(el)));
		})
		.catch(() => {
			inhalt.innerHTML = esc(__("Die Baustellen konnten nicht geladen werden."));
		});
})();
