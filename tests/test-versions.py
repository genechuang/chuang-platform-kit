#!/usr/bin/env python3
"""Each distribution's `__version__` equals its pyproject.toml version: chuang-platform-kit (pyproject.toml,
src/chuang_platform_kit/__init__.py) and chuang-sdlc (sdlc/pyproject.toml, sdlc/src/chuang_sdlc/__init__.py).

Why: sdlc-v0.7.0 was tagged with pyproject at 0.7.0 and `__version__` still at 0.6.0 (read 11/8/26, SMADPickleBot
#1129's map), so a reader of `__version__` would have called a current install stale; `importlib.metadata.version()`
is what a host compares pins against, and this test keeps the module string honest beside it.

Run:  python -B tests/test-versions.py
"""
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def pyproject_version(path):
    with open(path, encoding='utf-8') as f:
        m = re.search(r'^version\s*=\s*"([^"]+)"', f.read(), re.M)
    return m.group(1) if m else None


def module_version(path):
    with open(path, encoding='utf-8') as f:
        m = re.search(r"^__version__\s*=\s*'([^']+)'", f.read(), re.M)
    return m.group(1) if m else None


kit = (pyproject_version(os.path.join(ROOT, 'pyproject.toml')), module_version(os.path.join(ROOT, 'src', 'chuang_platform_kit', '__init__.py')))
check(f'chuang-platform-kit: __version__ {kit[1]} is pyproject.toml\'s {kit[0]}', kit[1], kit[0])
sdlc = (pyproject_version(os.path.join(ROOT, 'sdlc', 'pyproject.toml')), module_version(os.path.join(ROOT, 'sdlc', 'src', 'chuang_sdlc', '__init__.py')))
check(f'chuang-sdlc: __version__ {sdlc[1]} is sdlc/pyproject.toml\'s {sdlc[0]}', sdlc[1], sdlc[0])
check('both versions were read', (kit[0] is not None, sdlc[0] is not None), (True, True))

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
