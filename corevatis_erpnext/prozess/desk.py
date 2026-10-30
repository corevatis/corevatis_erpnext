"""Eine Oberfläche für den Betrieb (Durchstich, Brüche D1 bis D3).

Frappe v16 zeigt je Modul eine eigene Seitenleiste („Shell“) und wechselt sie, sobald ein Beleg
geöffnet wird, den die Seitenleiste auf dem Bildschirm nicht auflistet (``shell_can_show`` im
Desk). Für Büro, Meister und Monteur gilt deshalb:

1. **Seitenleiste „Betrieb“** (Standard-Sidebar des Moduls, ``sidebar/betrieb/betrieb.json``):
   listet alle Belege des Handwerksprozesses, auch die aus ERPNext (Lead, Event, Quotation, …)
   und aus dem Kern (Objekt, Befund, Aufmaß, …). Damit bleibt jeder Klick in dieser Leiste.
2. **Andere Module gesperrt** (``User.block_modules``): Für Benutzer mit einer Fachrolle und ohne
   System Manager werden alle Module außer „Corevatis ERPNext“ blockiert. Das versteckt die
   übrigen Shells (CRM, Vertrieb, Projekte, Buchhaltung, Kern-App) im Dock, nimmt aber keine
   Rechte an Belegen; die bleiben über die Leiste „Betrieb“ erreichbar.
3. **Kachel „ERPNext“** auf ``/desk`` nur für System Manager (``frappe.apps.get_apps`` überschrieben).
4. **Startseite je Rolle** (``Role.home_page``): Büro und Meister in „Vorgänge“, Monteure in
   „Meine Baustellen“.

Alles idempotent, läuft in ``after_migrate`` und beim Speichern eines Benutzers.
"""

from __future__ import annotations

import frappe

MODULE = "Corevatis ERPNext"
SICHTBARE_MODULE = (MODULE,)
ROLLEN_FACH = ("Büro", "Meister", "Monteur")
ROLLEN_VOLL = ("System Manager",)
HOME_PAGES = {
	"Büro": "/desk/betrieb/vorgänge",
	"Meister": "/desk/betrieb/vorgänge",
	"Monteur": "/desk/betrieb/meine-baustellen",
}


@frappe.whitelist()
def get_apps() -> list[dict]:
	"""Kacheln auf ``/desk``: wie Frappe, aber ohne „ERPNext“ für Benutzer ohne System Manager."""
	from frappe.apps import get_apps as frappe_get_apps

	apps = frappe_get_apps()
	if frappe.session.user == "Administrator" or _voll(frappe.get_roles()):
		return apps
	return [a for a in apps if a.get("name") != "erpnext"]


def _voll(rollen) -> bool:
	"""Rollen, die das ganze System sehen sollen (unabhängig vom angemeldeten Benutzer)."""
	return any(r in rollen for r in ROLLEN_VOLL)


def gewuenschte_sperren(rollen) -> list[str] | None:
	"""Module, die der Benutzer nicht im Dock sehen soll; ``None`` = nichts anfassen."""
	rollen = set(rollen)
	if _voll(rollen) or not rollen & set(ROLLEN_FACH):
		return None
	module = frappe.get_all("Module Def", pluck="name", order_by="name asc")
	return [m for m in module if m not in SICHTBARE_MODULE]


def user_validate(doc, method=None) -> None:
	"""``User.validate``: Modulsperren passend zu den Rollen setzen (nur System User)."""
	if doc.get("user_type") != "System User" or doc.name in ("Administrator", "Guest"):
		return
	sperren = gewuenschte_sperren(r.role for r in doc.get("roles", []))
	if sperren is None:
		return
	vorhanden = sorted(r.module for r in doc.get("block_modules", []))
	if vorhanden == sorted(sperren):
		return
	doc.set("block_modules", [{"module": m} for m in sperren])


def ensure_desk() -> None:
	ensure_home_pages()
	ensure_block_modules_bestand()


def ensure_home_pages() -> None:
	for rolle, home in HOME_PAGES.items():
		if not frappe.db.exists("Role", rolle):
			continue
		if frappe.db.get_value("Role", rolle, "home_page") != home:
			frappe.db.set_value("Role", rolle, "home_page", home, update_modified=False)


def ensure_block_modules_bestand() -> None:
	"""Bestehende Benutzer mit Fachrolle nachziehen (einmal je ``migrate``)."""
	namen = frappe.get_all(
		"Has Role",
		filters={"parenttype": "User", "role": ["in", list(ROLLEN_FACH)]},
		pluck="parent",
		distinct=True,
	)
	for name in sorted(set(namen)):
		if not frappe.db.exists("User", name):
			continue
		doc = frappe.get_doc("User", name)
		vorher = sorted(r.module for r in doc.get("block_modules", []))
		user_validate(doc)
		if sorted(r.module for r in doc.get("block_modules", [])) != vorher:
			doc.flags.ignore_permissions = True
			doc.save()


def boot_session(bootinfo) -> None:
	"""Boot-Daten des Desks: die Kachel „ERPNext“ (Finanz-Dashboard, Module) sehen nur System
	Manager; Benutzer mit Fachrolle starten in „Betrieb“."""
	if frappe.session.user == "Administrator" or _voll(frappe.get_roles()):
		return
	for app in bootinfo.get("app_data") or []:
		if app.get("app_name") == "erpnext":
			app["on_apps_screen"] = False
