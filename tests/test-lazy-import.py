#!/usr/bin/env python3
"""Regression tests for webhook/shared/lazy_import.py: imported on first use, never at import time.

WHY. On 2026-10-02 PT the CI test step took 52 s for 146 suites, each its own
Python process, and most of it was imports a suite never used:
`googleapiclient.discovery` and `google.cloud.firestore` both pull in
`google.api_core`, ~0.8 s on the desktop (its import-time dependency-version
check walks every installed distribution), and email_service.py, smad-sheets.py,
setup-gmail-watch.py and three of the Cloud Functions' main.py paid it at the
top. The same cost was every cold start's. They now load on first use; this
suite pins the three helpers and that those modules stay cheap to import.

Run:  python -B tests/test-lazy-import.py
"""
import importlib.util
import os
import sys
import types

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit.lazy_import import importable, lazy, lazy_attr, require  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


print("\n-- lazy_attr: a `from x import Y` that imports x on the first call or attribute read")
sys.modules.pop('colorsys', None)
rgb_to_hsv = lazy_attr('colorsys', 'rgb_to_hsv')
check("nothing is imported when the stand-in is made", 'colorsys' in sys.modules, False)
check("calling it calls the real function", rgb_to_hsv(1.0, 0.0, 0.0), (0.0, 1.0, 1.0))
check("and the module is imported by then", 'colorsys' in sys.modules, True)
OrderedDict = lazy_attr('collections', 'OrderedDict')
check("an attribute read reaches the real object (Credentials.from_...)",
      list(OrderedDict.fromkeys('ab')), ['a', 'b'])
check("a call constructs the real class", type(OrderedDict()).__name__, 'OrderedDict')
missing = lazy_attr('no_such_pkg_smad', 'build')
try:
    missing('gmail', 'v1')
    raised = None
except ModuleNotFoundError as e:
    raised = e.name
check("a missing module fails at the first call, not silently", raised, 'no_such_pkg_smad')
try:
    missing.from_authorized_user_file
    raised = None
except ModuleNotFoundError as e:
    raised = e.name
check("... and at the first attribute read", raised, 'no_such_pkg_smad')
gone = lazy_attr('json', 'no_such_name')
try:
    gone()
    raised = False
except AttributeError:
    raised = True
check("a name the module lacks fails at the call, as the from-import would have at import", raised, True)

print("\n-- lazy: a module stand-in (the requests / firestore shape)")
sys.modules.pop('fractions', None)
fractions = lazy('fractions')
check("nothing is imported when it is made", 'fractions' in sys.modules, False)
check("an attribute read imports it", str(fractions.Fraction(1, 2)), '1/2')
fractions.Fraction = 'fake'
check("a test's replacement of one attribute wins", fractions.Fraction, 'fake')

print("\n-- importable: is it installed, without importing it")
sys.modules.pop('wave', None)
check("an installed module", importable('json', 'wave'), True)
check("... is not imported by the check", 'wave' in sys.modules, False)
check("a missing top-level module", importable('json', 'no_such_pkg_smad'), False)
check("a missing parent package (find_spec raises)", importable('no_such_pkg_smad.sub'), False)
check("a missing submodule of an installed package", importable('email.no_such_sub_smad'), False)
sys.modules['smad_fake_mod'] = types.SimpleNamespace()
check("a test's stand-in in sys.modules counts as installed", importable('smad_fake_mod'), True)
sys.modules['smad_fake_mod'] = None
check("a None in sys.modules blocks it, as import does", importable('smad_fake_mod'), False)
del sys.modules['smad_fake_mod']

print("\n-- require: the fail-fast an eager import gave (`except ImportError: sys.exit(1)`)")
check("installed: returns quietly", require('json', 'email.mime.text'), None)
try:
    require('json', 'no_such_pkg_smad')
    raised = None
except ImportError as e:
    raised = str(e)
check("missing: ImportError naming only what is missing", raised, 'not installed: no_such_pkg_smad')

print("\n-- the kit's own modules pay for no Google library at import")
HEAVY = ('google.api_core', 'googleapiclient.discovery', 'google.cloud.firestore', 'google.cloud.pubsub_v1')
for mod in ('redact', 'cloud_logging', 'phone_utils', 'request_scope', 'whatsapp_message',
            'whatsapp_publisher', 'greenapi', 'email_notify', 'clock'):
    importlib.import_module(f'chuang_platform_kit.{mod}')
    check(f"chuang_platform_kit.{mod} imports none of {', '.join(HEAVY)}", [h for h in HEAVY if h in sys.modules], [])
# (Which host entry points stay light is each host's own check -- SMADPickleBot's
# tests/test-lazy-import.py loads its CLIs and functions and asserts the same.)

print()
print(f"{len(fails)} failure(s)" + (":" if fails else ""))
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
