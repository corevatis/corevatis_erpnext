"""Einrichtung der ERPNext-Seite einer Corevatis-Site (Site-Template, Aufgabe 0.4).

Einstiege für ``bench execute`` (Details: ``site_template.py``)::

    bench --site <site> execute corevatis_erpnext.setup.apply_site_template \\
        --kwargs '{"template": "huelshoff"}'          # oder {"config_file": "/pfad/vorlage.json"}
    bench --site <site> execute corevatis_erpnext.setup.verify_site_template \\
        --kwargs '{"template": "huelshoff"}'

Rechenlogik gehört nicht hierher, sondern in die Kern-App ``corevatis``.
"""


def apply_site_template(config=None, config_file=None, template=None, commit=True):
	from corevatis_erpnext.setup.site_template import apply_site_template as _apply

	return _apply(config=config, config_file=config_file, template=template, commit=commit)


def verify_site_template(config=None, config_file=None, template=None):
	from corevatis_erpnext.setup.site_template import verify_site_template as _verify

	return _verify(config=config, config_file=config_file, template=template)
