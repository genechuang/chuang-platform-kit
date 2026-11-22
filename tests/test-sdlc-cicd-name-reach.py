#!/usr/bin/env python3
"""chuang_sdlc.cicd.name_reach: which names of a host's package a deploy runs and which names a push changed
(SMADPickleBot #2160, moved from its pipeline/shared/name_reach.py).

Pinned (the positive case first): an entry's reach follows the names it uses through the package (`from pkg.a import
f`, `pkg.b.g`, a relative import inside the package), with each reached module's INIT, and not a name it never uses;
a module object used as a value is ALL; changed_names sees a changed body, a new and a removed name and changed start-up
code, never a docstring edit, and ALL for a side that does not parse; hit meets them; the package's name is the host's.

Run:  python -B tests/test-sdlc-cicd-name-reach.py
"""
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))

from chuang_sdlc.cicd import name_reach as N  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


root = tempfile.mkdtemp(prefix='name-reach-')
pkg = os.path.join(root, 'acme_core')
os.makedirs(pkg)
files = {
    'a.py': 'from .c import helper\n\ndef f():\n    return helper()\n\ndef unused():\n    return 1\n',
    'b.py': 'X = 1\n\ndef g():\n    return X\n',
    'c.py': 'import logging\nlogging.basicConfig()\n\ndef helper():\n    return 2\n\ndef other():\n    return 3\n',
    'd.py': 'def h():\n    return 4\n',
}
for name, text in files.items():
    with open(os.path.join(pkg, name), 'w', encoding='utf-8') as f:
        f.write(text)
entry = os.path.join(root, 'main.py')
with open(entry, 'w', encoding='utf-8') as f:
    f.write('from acme_core.a import f\nimport acme_core.b\nfrom acme_core import d as dee\n\nf()\nacme_core.b.g()\nkeep = dee\n')

reach = N.uses([entry], pkg, 'acme_core')
check("uses, the positive case: the names the entry reaches through the host's package, each module's INIT, and not "
      "a name it never uses; a module object kept as a value is ALL",
      (sorted(reach['a']), sorted(reach['b']), sorted(reach['c']), reach['d']),
      (['__init__', 'f', 'helper'], ['X', '__init__', 'g'], ['__init__', 'helper', 'logging'], N.ALL))   # INIT uses logging
check("uses: another package's name walks nothing",
      N.uses([entry], pkg, 'other_core'), {})
check("changed_names: a changed body, a new and a removed name, start-up code; never a docstring; ALL when unparsable",
      (N.changed_names('def f():\n    return 1\n', 'def f():\n    return 2\n'),
       N.changed_names('def f():\n    return 1\n', 'def g():\n    return 1\n'),
       N.changed_names('X = 1\n', 'X = 1\nprint(X)\n'),
       N.changed_names('def f():\n    """old"""\n    return 1\n', 'def f():\n    """new"""\n    return 1\n'),
       N.changed_names('def f(:\n', 'x = 1\n'), N.changed_names(None, 'x = 1\n')),
      ({'f'}, {'f', 'g'}, {N.INIT}, set(), N.ALL, {'x'}))
check("hit: a changed name the deploy reaches, ALL either side; none when the module is not reached or nothing changed",
      (N.hit(reach, 'a', {'f'}), N.hit(reach, 'a', {'unused'}), N.hit(reach, 'd', {'h'}), N.hit(reach, 'a', N.ALL),
       N.hit(reach, 'zz', {'f'}), N.hit(reach, 'a', set())),
      (True, False, True, True, False, False))

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
