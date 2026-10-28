"""Prozess 2 „Vor-Ort-Termin“ (Epic 2): Verbindung der ERPNext-Belege mit den Doctypes
der Kern-App ``corevatis`` (Objekt, Befund, Aufmaß, Scan, Leistungsregel).

Kopplung nur über Doctype-Namen (Zeichenketten) und die whitelisted API von ``corevatis``,
kein Python-Import (import-linter). ``corevatis`` ist keine Pflicht-App dieses Adapters:
Felder, die auf Kern-Doctypes zeigen, entstehen nur, wenn der Kern installiert ist.
"""
