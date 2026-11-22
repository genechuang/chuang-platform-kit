#!/usr/bin/env python3
"""chuang_sdlc.cicd.import_walk: a deploy's entry walked through a host's shared package (SMADPickleBot #2160, moved
from its pipeline/shared/check-function-deps.py).

Pinned (the positive case first): an entry's walk finds a third-party import a shared module makes at start-up, keeps a
lazy one apart, and leaves out the standard library, a sibling module, `_` names and the host's first-party names; reach
gives the package's file names with __init__.py; package_targets reads every import spelling; the facts are kept by
content (a second process parses nothing, a changed file is parsed afresh, another version's file is ignored, an
unwritable cache keeps nothing and clears `new`); norm, requirements, satisfied (by name, by the host's map, by a
namespace prefix) and unused (a runtime package never dead).

Run:  python -B tests/test-sdlc-cicd-import-walk.py
"""
import ast
import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc.cicd import import_walk as W  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


root = tempfile.mkdtemp(prefix='import-walk-')
pkg = os.path.join(root, 'acme_core')
fn = os.path.join(root, 'fn')
os.makedirs(pkg)
os.makedirs(fn)
files = {
    os.path.join(pkg, '__init__.py'): '',
    os.path.join(pkg, 'config.py'): 'import os\nimport pytz\nfrom . import clock\n\ndef zone():\n    import yaml\n    return yaml\n',
    os.path.join(pkg, 'clock.py'): 'import _thread\nfrom google.cloud import storage\n',
    os.path.join(pkg, 'unreached.py'): 'import numpy\n',
    os.path.join(fn, 'helper.py'): 'X = 1\n',
    os.path.join(fn, 'main.py'): ('import flask\nimport helper\nfrom acme_core.config import zone\nimport acme_core\n'
                                  "SCRIPT = 'scripts/run-me.py'\n"),
}
for path, text in files.items():
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
entry = os.path.join(fn, 'main.py')
kept, new = {}, {}


def file_facts(path):
    with open(path, 'rb') as f:
        return W.facts_of(f.read(), kept, new, 'acme_core')


top, lazy = W.walk(entry, pkg, file_facts, first_party=('acme_core', 'flask'))
check("walk, the positive case: a shared module's start-up import is module-level, a def's import lazy; the standard "
      "library, a sibling, `_` names and the host's first-party names are left out",
      (sorted(top), sorted(lazy)), (['google', 'pytz'], ['yaml']))

targets = lambda p: W.package_targets(ast.parse(open(p, encoding='utf-8').read()), 'acme_core')   # noqa: E731
check("reach: the package's file names the entry reaches, __init__.py always, never an unreached module",
      sorted(W.reach(entry, pkg, targets)), ['__init__.py', 'clock.py', 'config.py'])

spellings = ('from acme_core.a import x\nfrom .b import y\nfrom . import c\nfrom acme_core import d\nimport acme_core.e\n'
             'import acme_coreX.f\nfrom other.g import z\n')
check("package_targets: every spelling of the package's imports, never a lookalike package",
      sorted(W.package_targets(ast.parse(spellings), 'acme_core')), ['a', 'b', 'c', 'd', 'e'])
check("imports: a relative import is first-party; an import in a def is lazy",
      W.imports(ast.parse('import a.b\nfrom .x import y\ndef f():\n    from c import d\n')), ({'a'}, {'c'}))
check("compute_facts: the script names and path strings a walker reads",
      (W.compute_facts(files[entry], 'acme_core')['scripts'], 'scripts/run-me.py' in W.compute_facts(files[entry], 'acme_core')['strings'],
       W.string_facts(ast.parse("os.path.join(ROOT, 'scripts', 'x.py')\nprint('has space.txt')"))),
      (['scripts/run-me.py'], True, {'scripts', 'x.py', 'scripts/x.py'}))

cache = os.path.join(root, 'cache', 'facts.json')
os.makedirs(os.path.dirname(cache))
check("facts: parsed once into `new`, the walked files alone (main, config, clock)", len(new), 3)
first = dict(new)
W.save_facts(cache, 2, new, prefix='t-')
again_kept, again_new = W.load_facts(cache, 2), {}
with open(entry, 'rb') as f:
    W.facts_of(f.read(), again_kept, again_new, 'acme_core')
check("save_facts and load_facts: written by content and versioned; the next process parses nothing; `new` is cleared",
      (json.load(open(cache, encoding='utf-8'))['v'], sorted(again_kept) == sorted(first), again_new, new), (2, True, {}, {}))
W.facts_of(b'import changed\n', again_kept, again_new, 'acme_core')
check("facts: a changed file is parsed afresh; another version's cache file reads as empty; no path is {}",
      (len(again_new), W.load_facts(cache, 3), W.load_facts('', 2)), (1, {}, {}))
lost = {'k': {}}
W.save_facts(os.path.join(root, 'no', 'such', 'dir', 'f.json'), 2, lost)
check("save_facts: a cache that cannot be written keeps nothing and still clears `new`", lost, {})

big_new = {f'n{i}': {} for i in range(3)}
with open(cache, 'w', encoding='utf-8') as f:
    json.dump({'v': 2, 'files': {f'old{i}': {} for i in range(5)}}, f)
W.save_facts(cache, 2, big_new, kept={'old0': {}}, keep=4)
check("save_facts: past `keep`, only the entries seen this run are kept",
      sorted(json.load(open(cache, encoding='utf-8'))['files']), ['n0', 'n1', 'n2', 'old0'])

req = os.path.join(root, 'requirements.in')
with open(req, 'w', encoding='utf-8') as f:
    f.write('# a comment\nFoo_Bar>=1\n-r other.txt\nchuang-sdlc @ git+https://example.invalid/x@v1\n\nPyYAML\n')
check("requirements and norm: bare lower-case dashed names; comments and option lines skipped; a missing file is empty",
      (W.requirements(req), W.requirements(os.path.join(root, 'gone.txt'))), ({'foo-bar', 'chuang-sdlc', 'pyyaml'}, set()))
check("satisfied: by its own name, by the host's map, by a namespace prefix; not otherwise",
      (W.satisfied('pytz', {'pytz'}), W.satisfied('yaml', {'pyyaml'}, {'yaml': 'PyYAML'}),
       W.satisfied('google', {'google-cloud-storage'}, namespace_prefixes={'google': 'google-'}),
       W.satisfied('yaml', {'pyyaml'}), W.satisfied('google', {'protobuf'}, namespace_prefixes={'google': 'google-'})),
      (True, True, True, False, False))
check("unused: a direct package no reachable import names, never a runtime one",
      W.unused({'pytz', 'pyyaml', 'gunicorn', 'numpy'}, {'pytz', 'yaml'}, runtime={'gunicorn'},
               import_to_pkg={'yaml': 'PyYAML'}), ['numpy'])

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
