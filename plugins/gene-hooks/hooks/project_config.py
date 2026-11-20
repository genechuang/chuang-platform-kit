#!/usr/bin/env python3
"""A project's own facts for the hooks, read from its committed `.claude/gene-hooks.toml` (1.38.0; SMADPickleBot
#1810: the hooks came "from 3 different places", so each moves into this plugin, and what one needs to know of a
project, where its mail script or its ticket tool is, the project says once in that file; the plugin names no project).

    [mail]
    script = "scripts/session-mail.py"      # a session's own mail pull: `python <script> wait --for "<name>"`
    [tickets]
    script = "scripts/tickets.py"           # `python <script> start <numbers>`

A path is relative to the project's root, the folder holding `.claude/`. A project with no file, or no such key, gets
'' and the hook that asked does nothing and says nothing. Standard library only: tomllib where the box's Python has it
(3.11), else the small reader below, which knows `[section]`, `key = "text"`, `key = true|false` and `#` comments, all
this file uses. Nothing here raises: a hook blocks only on purpose.
"""
import os

NAME = os.path.join('.claude', 'gene-hooks.toml')


def root(cwd: str) -> str:
    """The first of `cwd` and its parents holding `.claude/gene-hooks.toml`, else ''."""
    here = os.path.abspath(cwd or '.')
    while True:
        if os.path.isfile(os.path.join(here, NAME)):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return ''
        here = parent


def parse(text: str) -> dict:
    """The file's tables as {section: {key: value}} (keys before any section under ''); a line it cannot read is
    skipped, never an error."""
    try:
        import tomllib
        return tomllib.loads(text)
    except Exception:   # noqa: BLE001 - no tomllib (Python < 3.11) or a file tomllib refuses: the small reader
        pass
    out, section = {}, ''
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line[1:-1].strip()
            out.setdefault(section, {})
            continue
        key, eq, value = line.partition('=')
        if not eq:
            continue
        value = value.strip()
        if value[:1] in ('"', "'"):
            end = value.find(value[0], 1)
            if end < 0:
                continue
            got = value[1:end]
        else:
            word = value.split('#', 1)[0].strip()
            got = True if word == 'true' else False if word == 'false' else word
        (out.setdefault(section, {}) if section else out)[key.strip()] = got
    return out


def read(cwd: str) -> tuple:
    """(the project's root, its config) for the folder a hook event names; ('', {}) for a project with no file or one
    that cannot be read."""
    top = root(cwd)
    if not top:
        return '', {}
    try:
        with open(os.path.join(top, NAME), encoding='utf-8') as f:
            return top, parse(f.read())
    except OSError:
        return '', {}


def script(cwd: str, section: str, key: str = 'script') -> tuple:
    """(the project's root, the absolute path of the script its `[section]` names) when the config names one and the
    file is there; ('', '') otherwise."""
    top, cfg = read(cwd)
    table = cfg.get(section) if isinstance(cfg, dict) else None
    rel = table.get(key) if isinstance(table, dict) else None
    if not top or not isinstance(rel, str) or not rel.strip():
        return '', ''
    path = os.path.normpath(os.path.join(top, rel.strip()))
    return (top, path) if os.path.isfile(path) else ('', '')
