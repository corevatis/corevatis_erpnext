#!/usr/bin/env python3
"""Lizenzliste der Python-Abhängigkeiten (THIRD_PARTY_LICENSES.md) und Copyleft-Prüfung.

Läuft in der CI nach dem Bench-Setup mit pip-licenses aus dem Runner-Python
gegen das Bench-venv:

  licenses.py --python ~/frappe-bench/env/bin/python --app corevatis \\
              --out THIRD_PARTY_LICENSES.md [--forbid-copyleft]
  licenses.py --selftest

Die Liste hat zwei Teile:
1. die eigenen (direkten und transitiven) Abhängigkeiten der App — ohne die
   Frappe-Apps selbst (frappe, erpnext, ...) und ohne deren Abhängigkeitsbaum;
2. die vollständige Laufzeitumgebung der Bench (Frappe v16 + installierte Apps).

``--forbid-copyleft`` (proprietäre App) lässt den Lauf scheitern, wenn eine
eigene Abhängigkeit unter GPL/AGPL steht (LGPL, MPL usw. sind erlaubt) oder
keine Lizenzangabe hat.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys

STRONG_COPYLEFT = re.compile(r"\bA?GPL|GNU (Affero )?General Public|Affero", re.I)
WEAK = re.compile(r"\bLGPL|Lesser|Library General", re.I)
# Frappe-Apps: eigene Lizenzbewertung, nicht Teil der Abhängigkeiten der App
FRAPPE_APPS = {"frappe", "erpnext", "payments", "hrms", "raven", "press"}

# Abhängigkeitsbaum im Bench-venv bestimmen (läuft mit dem Bench-Python)
TREE_SNIPPET = r"""
import importlib.metadata as md, json, re, sys
app, skip = sys.argv[1], set(sys.argv[2].split(","))
norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
seen, todo = set(), [app]
while todo:
    name = todo.pop()
    try:
        reqs = md.requires(name) or []
    except md.PackageNotFoundError:
        continue
    for req in reqs:
        if "extra ==" in req:
            continue
        dep = norm(re.match(r"[A-Za-z0-9._-]+", req).group(0))
        if dep in skip or dep in seen:
            continue
        seen.add(dep)
        todo.append(dep)
print(json.dumps(sorted(seen)))
"""


def norm(name: str) -> str:
	return re.sub(r"[-_.]+", "-", name).lower()


def classify(license_text: str) -> str:
	"""'copyleft' (GPL/AGPL), 'unknown' oder 'ok'."""
	text = (license_text or "").strip()
	if not text or text.upper() == "UNKNOWN":
		return "unknown"
	# Mehrfachlizenzen ("MIT; GPL" / "MIT OR GPL") gelten als ok, wenn eine Option frei ist
	options = [o.strip() for o in re.split(r";|\s+OR\s+|\s+or\s+", text) if o.strip()]
	if any(not STRONG_COPYLEFT.search(o) or WEAK.search(o) for o in options):
		return "ok"
	return "copyleft"


def pip_licenses(python: str) -> list[dict]:
	out = subprocess.run(
		[
			sys.executable,
			"-m",
			"piplicenses",
			"--python",
			python,
			"--from=mixed",
			"--format=json",
			"--with-urls",
		],
		check=True,
		capture_output=True,
		text=True,
	).stdout
	return json.loads(out)


def app_dependencies(python: str, app: str) -> set[str]:
	out = subprocess.run(
		[python, "-c", TREE_SNIPPET, app, ",".join(sorted(FRAPPE_APPS))],
		check=True,
		capture_output=True,
		text=True,
	).stdout
	return set(json.loads(out))


def table(rows: list[dict]) -> list[str]:
	lines = ["| Paket | Version | Lizenz | URL |", "|---|---|---|---|"]
	for r in sorted(rows, key=lambda r: r["Name"].lower()):
		lic = (r.get("License") or "UNKNOWN").replace("|", "/")
		lines.append(f"| {r['Name']} | {r['Version']} | {lic} | {r.get('URL') or ''} |")
	return lines


def selftest() -> int:
	cases = {
		"MIT": "ok",
		"GNU General Public License v3 (GPLv3)": "copyleft",
		"GPL-3.0-only": "copyleft",
		"AGPL-3.0-or-later": "copyleft",
		"GNU Lesser General Public License v3 (LGPLv3)": "ok",
		"LGPL-2.1-or-later": "ok",
		"MIT OR GPL-2.0": "ok",
		"Mozilla Public License 2.0 (MPL 2.0)": "ok",
		"UNKNOWN": "unknown",
		"": "unknown",
	}
	failed = [(k, v, classify(k)) for k, v in cases.items() if classify(k) != v]
	for text, want, got in failed:
		print(f"FAIL classify({text!r}) = {got}, erwartet {want}")
	print("selftest ok" if not failed else "selftest fehlgeschlagen")
	return 1 if failed else 0


def main() -> int:
	ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	ap.add_argument("--python")
	ap.add_argument("--app")
	ap.add_argument("--version", default="")
	ap.add_argument("--out", default="THIRD_PARTY_LICENSES.md")
	ap.add_argument("--forbid-copyleft", action="store_true")
	ap.add_argument("--selftest", action="store_true")
	args = ap.parse_args()
	if args.selftest:
		return selftest()
	if not (args.python and args.app):
		ap.error("--python und --app sind Pflicht")

	packages = pip_licenses(args.python)
	own_names = app_dependencies(args.python, args.app)
	own = [p for p in packages if norm(p["Name"]) in own_names]
	apps = [p for p in packages if norm(p["Name"]) in FRAPPE_APPS | {norm(args.app)}]

	problems = []
	for p in own:
		verdict = classify(p.get("License", ""))
		if verdict != "ok":
			problems.append(f"{p['Name']} {p['Version']}: {p.get('License') or 'keine Angabe'} ({verdict})")

	now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
	lines = [
		f"# Drittanbieter-Lizenzen - {args.app} {args.version}".rstrip(),
		"",
		f"Automatisch erzeugt von der CI ({now}) mit pip-licenses aus der Bench-Umgebung (Frappe v16, Python 3.14).",
		"",
		"## Frappe-Apps in der Bench",
		"",
		*table(apps),
		"",
		f"## Eigene Abhängigkeiten von `{args.app}` (direkt und transitiv, ohne Frappe-Apps)",
		"",
		*(table(own) if own else ["Keine - die App hängt nur am Frappe Framework."]),
		"",
	]
	if args.forbid_copyleft:
		lines += [
			"Prüfung: keine GPL/AGPL-Lizenz und keine fehlende Lizenzangabe unter den eigenen Abhängigkeiten - "
			+ ("**verletzt**: " + "; ".join(problems) if problems else "erfüllt."),
			"",
		]
	lines += [
		"## Vollständige Laufzeitumgebung der Bench",
		"",
		*table([p for p in packages if p not in apps]),
		"",
	]
	with open(args.out, "w", encoding="utf-8") as fh:
		fh.write("\n".join(lines))
	print(f"{args.out}: {len(packages)} Pakete, {len(own)} eigene Abhängigkeiten von {args.app}")
	for problem in problems:
		print(f"Lizenzproblem: {problem}")
	if args.forbid_copyleft and problems:
		print("Copyleft-Prüfung fehlgeschlagen")
		return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
