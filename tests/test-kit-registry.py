#!/usr/bin/env python3
"""scripts/build-kit-registry.py: KIT-REGISTRY.md, the kit's function registry, generated from its docstrings
(SMADPickleBot #971, Gene 11/5/26: "Perhaps it needs a registry/manifest").

Pinned (the positive case first): the file is current with the code; every module has its section; a public function,
class and method each have a row, a private one none; the undocumented public names only ever get fewer
(MAX_UNDOCUMENTED, the count on 11/5/26).

Run:  python -B tests/test-kit-registry.py
"""
import glob
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
spec = importlib.util.spec_from_file_location('build_kit_registry', os.path.join(ROOT, 'scripts', 'build-kit-registry.py'))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)

# The public names with no docstring when the registry began (11/5/26). Lower it as docstrings are written; never raise it.
MAX_UNDOCUMENTED = 49

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


text = B.render()
current = open(B.OUT, encoding='utf-8').read() if os.path.exists(B.OUT) else ''
check("the positive case: KIT-REGISTRY.md is current (run python scripts/build-kit-registry.py)", current == text, True)

modules = sorted(os.path.basename(p)[:-3] for p in glob.glob(os.path.join(B.PKG, '*.py')) if not p.endswith('__init__.py'))
check("every module has its section", [m for m in modules if f"## `{m}`" not in text], [])

sample = '''"""A module. More."""
def visible(a, b=1, *rest, key=None, **kw):
    """Does a thing.

    More detail."""
def _hidden():
    """Never listed."""
class Box:
    """A box."""
    def open(self, lid):
        """Opens it."""
    def _seal(self):
        pass
'''
with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False, encoding='utf-8') as f:
    f.write(sample)
doc, rows = B.module_rows(f.name)
os.unlink(f.name)
check("  public functions, classes and methods have rows, private ones none; defaults a bare '='",
      (doc, rows), ('A module. More.', [('visible(a, b=, *rest, key=, **kw)', 'Does a thing.'), ('class Box', 'A box.'),
                                         ('Box.open(lid)', 'Opens it.')]))

undocumented = text.count(B.NO_DOC)
check(f"  the undocumented public names only get fewer ({undocumented} now, at most {MAX_UNDOCUMENTED})",
      undocumented <= MAX_UNDOCUMENTED, True)

print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
