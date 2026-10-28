"""Konfiguration des Site-Templates laden, zusammenführen und prüfen.

Eine Vorlage ist eine JSON-Datei. Mit ``"extends": "<vorlage>"`` erbt sie von einer
anderen Vorlage im Verzeichnis ``templates/`` (Objekte werden rekursiv
zusammengeführt, Listen und Werte ersetzt). Schlüssel, die mit ``_`` beginnen, sind
Kommentare. Werte, die mit ``PLATZHALTER`` beginnen, gelten als unbekannt: das
Template schreibt sie nicht und meldet sie im Bericht.

Bewusst ohne Frappe-Abhängigkeit, damit die Logik ohne Site testbar bleibt.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

TEMPLATE_DIR = Path(__file__).parent / "templates"
PLACEHOLDER_PREFIX = "PLATZHALTER"
SUPPORTED_CHARTS = ("SKR03", "SKR04")


class SiteTemplateConfigError(ValueError):
	"""Ungültige Template-Konfiguration."""


def deep_merge(base: dict, override: dict) -> dict:
	"""``override`` rekursiv über ``base`` legen (beide bleiben unverändert)."""
	result = copy.deepcopy(base)
	for key, value in override.items():
		if isinstance(value, dict) and isinstance(result.get(key), dict):
			result[key] = deep_merge(result[key], value)
		else:
			result[key] = copy.deepcopy(value)
	return result


def strip_comments(value: Any) -> Any:
	"""Kommentar-Schlüssel (``_…``) entfernen."""
	if isinstance(value, dict):
		return {k: strip_comments(v) for k, v in value.items() if not str(k).startswith("_")}
	if isinstance(value, list):
		return [strip_comments(v) for v in value]
	return value


def is_placeholder(value: Any) -> bool:
	return isinstance(value, str) and value.strip().upper().startswith(PLACEHOLDER_PREFIX)


def find_placeholders(value: Any, path: str = "") -> list[str]:
	"""Pfade aller Platzhalter-Werte, z. B. ``company.tax_id``."""
	found: list[str] = []
	if isinstance(value, dict):
		for key, item in value.items():
			found += find_placeholders(item, f"{path}.{key}" if path else str(key))
	elif isinstance(value, list):
		for index, item in enumerate(value):
			found += find_placeholders(item, f"{path}[{index}]")
	elif is_placeholder(value):
		found.append(path)
	return found


def _template_path(name: str) -> Path:
	path = TEMPLATE_DIR / f"{name}.json"
	if not path.is_file():
		raise SiteTemplateConfigError(f"Vorlage {name!r} nicht gefunden ({path})")
	return path


def _read(path: Path, seen: tuple[Path, ...] = ()) -> dict:
	path = path.resolve()
	if path in seen:
		raise SiteTemplateConfigError(f"Zirkuläres extends: {' -> '.join(str(p) for p in (*seen, path))}")
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
	except json.JSONDecodeError as exc:
		raise SiteTemplateConfigError(f"{path}: kein gültiges JSON ({exc})") from exc
	if not isinstance(data, dict):
		raise SiteTemplateConfigError(f"{path}: Objekt erwartet")
	parent = data.pop("extends", None)
	if parent:
		data = deep_merge(_read(_template_path(parent), (*seen, path)), data)
	return data


def load_config(
	config: dict | None = None, config_file: str | None = None, template: str | None = None
) -> dict:
	"""Genau eine Quelle: ``config`` (dict), ``config_file`` (Pfad) oder ``template`` (Name in templates/)."""
	sources = [s for s in (config, config_file, template) if s]
	if len(sources) != 1:
		raise SiteTemplateConfigError("Genau eines von config, config_file oder template angeben")

	if config is not None:
		data = copy.deepcopy(config)
		parent = data.pop("extends", None)
		if parent:
			data = deep_merge(_read(_template_path(parent)), data)
	elif config_file:
		data = _read(Path(config_file))
	else:
		data = _read(_template_path(template))

	data = strip_comments(data)
	validate_config(data)
	return data


def validate_config(data: dict) -> None:
	company = data.get("company") or {}
	missing = [k for k in ("company_name", "abbr", "country", "default_currency") if not company.get(k)]
	if missing:
		raise SiteTemplateConfigError(f"company: Pflichtfelder fehlen: {', '.join(missing)}")
	for key in ("company_name", "abbr"):
		if is_placeholder(company[key]):
			raise SiteTemplateConfigError(f"company.{key} darf kein Platzhalter sein")

	chart = company.get("chart_of_accounts")
	if chart not in SUPPORTED_CHARTS:
		raise SiteTemplateConfigError(
			f"company.chart_of_accounts muss {' oder '.join(SUPPORTED_CHARTS)} sein, nicht {chart!r}"
		)

	start = company.get("fiscal_year_start", "01-01")
	try:
		month, day = (int(p) for p in start.split("-"))
		if not (1 <= month <= 12 and 1 <= day <= 28):
			raise ValueError
	except ValueError:
		raise SiteTemplateConfigError(
			f"company.fiscal_year_start im Format MM-TT (Tag ≤ 28), nicht {start!r}"
		)

	sets = data.get("permission_sets") or {}
	for role, spec in (data.get("roles") or {}).items():
		for doctype, perm in (spec.get("permissions") or {}).items():
			if isinstance(perm, str) and perm not in sets:
				raise SiteTemplateConfigError(
					f"roles.{role}.permissions.{doctype}: unbekanntes Rechte-Set {perm!r}"
				)

	for series in (data.get("naming_series") or {}).values():
		if not series.get("series"):
			raise SiteTemplateConfigError("naming_series: jeder Eintrag braucht 'series'")
