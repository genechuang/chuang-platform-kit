#!/usr/bin/env python3
"""The skills a compacted or resumed session carries, checked against the newest installed copy (gene-hooks 1.28.0;
SMADPickleBot #1665).

Why: on 11/16/26 10:01 PM PT a session told "Start handoff" followed its account-handover skill as its context held it,
version 0.8.18, loaded days earlier and re-stated after a compaction, while 0.8.31 was installed: it gave Gene the steps
of a process retired two weeks before. skill-current.lib.sh (1.20.0) checks a skill only when the Skill tool loads it; a
compaction re-states the skills it saw without a Skill call, in an `invoked_skills` attachment of the transcript, so
nothing compared them.

    stale-skills.py <transcript.jsonl>

reads the transcript's last TAIL_BYTES, takes the newest `invoked_skills` attachment, and for each skill whose content
names its copy's folder ("Base directory for this skill: <cache>/<marketplace>/<plugin>/<version>/skills/<name>")
prints one line when a newer version folder beside it has the same skill. It prints nothing when every copy is the
newest, when no attachment is found or the file cannot be read, and always exits 0. Standard library only; reads files
and writes none.
"""
import json
import os
import re
import sys

TAIL_BYTES = 16 * 1024 * 1024
BASE = re.compile(r'Base directory for this skill: ([^\r\n]+)')
VERSION = re.compile(r'^[0-9]+(\.[0-9]+)*$')


def vkey(v: str) -> tuple:
    return tuple(int(p) for p in v.split('.'))


def tail_lines(path: str) -> list:
    """The transcript's last TAIL_BYTES as lines, the first (cut) one dropped."""
    with open(path, 'rb') as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        data = f.read()
    lines = data.decode('utf-8', errors='replace').splitlines()
    return lines[1:] if size > TAIL_BYTES else lines


def invoked(lines: list) -> list:
    """The skills of the newest `invoked_skills` attachment, [] when there is none."""
    for line in reversed(lines):
        if '"invoked_skills"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        att = entry.get('attachment') or {}
        if att.get('type') == 'invoked_skills':
            return [s for s in att.get('skills') or [] if isinstance(s, dict)]
    return []


def stale(skill: dict) -> str:
    """One line when the skill's copy is older than the newest installed one of its plugin, else ''."""
    m = BASE.search(str(skill.get('content') or ''))
    if not m:
        return ''
    folder = os.path.normpath(m.group(1).strip())
    name = os.path.basename(folder)
    skills_dir = os.path.dirname(folder)
    if os.path.basename(skills_dir) != 'skills':
        return ''
    ver_dir = os.path.dirname(skills_dir)
    ver, plug = os.path.basename(ver_dir), os.path.dirname(ver_dir)
    if not VERSION.match(ver) or not os.path.isdir(plug):
        return ''
    versions = [v for v in os.listdir(plug) if VERSION.match(v)]
    newest = max(versions, key=vkey) if versions else ver
    if vkey(newest) <= vkey(ver) or not os.path.isfile(os.path.join(plug, newest, 'skills', name, 'SKILL.md')):
        return ''
    label = skill.get('path') or name
    return f'{label} is the {ver} copy your context carries; {newest} is installed'


def main(argv: list) -> int:
    if not argv:
        return 0
    try:
        found = [line for line in (stale(s) for s in invoked(tail_lines(argv[0]))) if line]
    except (OSError, ValueError):
        return 0
    for line in found:
        print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
