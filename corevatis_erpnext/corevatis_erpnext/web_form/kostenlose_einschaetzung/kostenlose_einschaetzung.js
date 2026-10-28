// Web Form „Kostenlose Einschätzung“ (Aufgabe 1.2).
// Achtung: Frappe rendert diese Datei als Jinja-Template - keine doppelten geschweiften
// Klammern und keine Klammer-Prozent-/Klammer-Raute-Folgen verwenden.
//
// Ergänzt das Standardformular um Fotos (Base64 im accept-Aufruf,
// kein upload_file für Gäste), Datenschutz-Zustimmung und Honeypot. Die Prüfung erfolgt
// zusätzlich serverseitig (corevatis_erpnext.webform.einschaetzung).
// Im iframe meldet die Seite ihre Höhe per postMessage an die einbettende Seite.

frappe.ready(function () {
	const MAX_FOTOS = 3;
	const MAX_BYTES = 10 * 1024 * 1024;
	const MAX_TOTAL_BASE64 = 23 * 1024 * 1024; // Frappe: 25 MB je Anfrage
	const SHRINK_ABOVE = 1.5 * 1024 * 1024;
	const MAX_EDGE = 2560;
	const MSG_HEIGHT = "corevatis:einschaetzung:height";
	const MSG_SUBMITTED = "corevatis:einschaetzung:submitted";

	const wf = frappe.web_form;
	if (!wf || !wf.fields_dict) return;

	const fotos = [];
	let pending = 0;

	const $ = window.jQuery;
	const $input = $("#cv_fotos_input");
	const $liste = $(".cv-foto-liste");
	const $fotoFehler = $(".cv-foto-fehler");

	function formatSize(bytes) {
		return (bytes / 1024 / 1024).toFixed(1).replace(".", ",") + " MB";
	}

	function isAllowed(file) {
		const name = (file.name || "").toLowerCase();
		return (
			["image/jpeg", "image/png", "image/heic", "image/heif"].includes(file.type) ||
			/\.(jpe?g|png|heic|heif)$/.test(name)
		);
	}

	function readAsDataURL(blob) {
		return new Promise(function (resolve, reject) {
			const reader = new FileReader();
			reader.onload = function () {
				resolve(reader.result);
			};
			reader.onerror = reject;
			reader.readAsDataURL(blob);
		});
	}

	// Verkleinert auf höchstens MAX_EDGE Pixel Kantenlänge (JPEG 85 %). Entfernt dabei auch
	// EXIF-Daten (z. B. GPS). HEIC klappt nur, wo der Browser HEIC dekodiert (Safari).
	function shrink(file) {
		return new Promise(function (resolve, reject) {
			const url = URL.createObjectURL(file);
			const img = new Image();
			img.onload = function () {
				const scale = Math.min(1, MAX_EDGE / Math.max(img.naturalWidth, img.naturalHeight));
				const canvas = document.createElement("canvas");
				canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
				canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
				canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
				URL.revokeObjectURL(url);
				canvas.toBlob(
					function (blob) {
						blob ? resolve(blob) : reject(new Error("toBlob"));
					},
					"image/jpeg",
					0.85
				);
			};
			img.onerror = function () {
				URL.revokeObjectURL(url);
				reject(new Error("decode"));
			};
			img.src = url;
		});
	}

	async function prepare(file) {
		let blob = file;
		let name = file.name || "foto";
		const heic = /\.(heic|heif)$/i.test(name) || /heic|heif/.test(file.type);
		if (heic || file.size > SHRINK_ABOVE) {
			try {
				const small = await shrink(file);
				if (heic || small.size < file.size) {
					blob = small;
					name = name.replace(/\.[^.]+$/, "") + ".jpg";
				}
			} catch (e) {
				// Browser kann das Format nicht dekodieren (HEIC außerhalb von Safari): Original senden
			}
		}
		if (blob.size > MAX_BYTES) {
			throw new Error(__("{0} ist größer als 10 MB.", [file.name]));
		}
		return { name: name, size: blob.size, data: await readAsDataURL(blob) };
	}

	function renderFotos() {
		$liste.empty();
		fotos.forEach(function (foto, idx) {
			const $li = $('<li class="cv-foto"></li>');
			$li.append($('<img alt="">').attr("src", foto.data));
			$li.append($('<span class="cv-foto-name"></span>').text(foto.name + " (" + formatSize(foto.size) + ")"));
			$('<button type="button" class="btn btn-xs btn-default">Entfernen</button>')
				.on("click", function () {
					fotos.splice(idx, 1);
					renderFotos();
				})
				.appendTo($li);
			$liste.append($li);
		});
		$input.prop("disabled", fotos.length >= MAX_FOTOS);
		postHeight();
	}

	$input.on("change", async function () {
		$fotoFehler.text("");
		const files = Array.from(this.files || []);
		this.value = "";
		const frei = MAX_FOTOS - fotos.length;
		if (files.length > frei) {
			$fotoFehler.text(__("Bitte höchstens {0} Fotos hochladen.", [MAX_FOTOS]));
		}
		pending += 1;
		try {
			for (const file of files.slice(0, Math.max(0, frei))) {
				if (!isAllowed(file)) {
					$fotoFehler.text(__("{0}: bitte nur JPG, PNG oder HEIC.", [file.name]));
					continue;
				}
				try {
					fotos.push(await prepare(file));
				} catch (e) {
					$fotoFehler.text(e.message || __("Ein Foto konnte nicht gelesen werden."));
				}
			}
		} finally {
			pending -= 1;
			renderFotos();
		}
	});

	wf.validate = function () {
		let ok = true;
		$(".cv-datenschutz-fehler").text("");

		const datenschutz = $("#cv_datenschutz").is(":checked");
		if (!datenschutz) {
			ok = false;
			$(".cv-datenschutz-fehler").text(__("Bitte stimmen Sie der Datenschutzerklärung zu."));
		}

		if (pending > 0) {
			ok = false;
			$fotoFehler.text(__("Die Fotos werden noch vorbereitet – bitte einen Moment warten."));
		}
		const total = fotos.reduce(function (sum, foto) {
			return sum + foto.data.length;
		}, 0);
		if (total > MAX_TOTAL_BASE64) {
			ok = false;
			$fotoFehler.text(__("Die Fotos sind zusammen zu groß. Bitte ein Foto entfernen."));
		}

		wf.doc.cv_datenschutz = datenschutz ? 1 : 0;
		wf.doc.cv_hp = $("#cv_hp").val() || "";
		wf.doc.cv_fotos = fotos.map(function (foto) {
			return { name: foto.name, data: foto.data };
		});
		postHeight();
		return ok;
	};

	// Einbettung (WordPress): Höhe an die einbettende Seite melden
	const inFrame = (function () {
		try {
			return window.self !== window.top;
		} catch (e) {
			return true;
		}
	})();

	function postHeight() {
		if (!inFrame) return;
		const height = Math.ceil(document.body.getBoundingClientRect().height);
		window.parent.postMessage({ type: MSG_HEIGHT, height: height }, "*");
	}

	if (inFrame) {
		document.body.classList.add("cv-embed");
		if (window.ResizeObserver) {
			new ResizeObserver(postHeight).observe(document.body);
		}
		window.addEventListener("load", postHeight);
		postHeight();
	}

	wf.events.on("after_save", function () {
		if (inFrame) {
			window.parent.postMessage({ type: MSG_SUBMITTED }, "*");
			postHeight();
		}
	});
});
