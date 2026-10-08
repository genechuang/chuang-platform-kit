#!/usr/bin/env python3
"""timecheck: the scanner for UTC days in disguise (Gene, 2026-10-07 PT: in code, every calendar day is a Pacific day).

Pinned, on source written to temp files:
  - a clean module has no findings (the positive case);
  - each forbidden form is caught: date.today(), datetime.today(), a zoneless datetime.now(), utcnow(), a UTC midnight,
    a .date() off a UTC instant, a fixed timedelta(hours=7);
  - a UTC use with no reason is a finding; the same line with `# utc-ok:` or `# local-ok:` passes, forbidden forms included;
  - strings, comments and docstrings are not code: `datetime.now()` inside them is never a finding;
  - an import line is not a finding; .venv and node_modules are skipped; a file that will not parse is still scanned by line;
  - main() prints one line per finding and "N finding(s)" and exits 1 on findings, 0 on none;
  - the kit's own src/ passes its own scanner.

Run:  python -B tests/test-timecheck.py
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

from chuang_platform_kit import timecheck as T  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


def rules(source: str) -> list:
    return [rule for _, rule, _ in T.check_source(source)]


print('clean code')
check('a clean module', rules('from datetime import datetime\nfrom zoneinfo import ZoneInfo\nnow = datetime.now(ZoneInfo("America/Los_Angeles"))\nday = now.date()\n'), [])

print('each forbidden form')
check('date.today()', rules('d = date.today()\n'), ['date.today(): a UTC day on a server'])
check('datetime.today()', rules('d = datetime.today()\n'), ['datetime.today(): a UTC day on a server'])
check('zoneless now()', rules('n = datetime.now()\n'), ['datetime.now() with no zone'])
check('utcnow()', rules('n = datetime.utcnow()\n'), ['utcnow()'])
check('a UTC midnight', rules('m = datetime.combine(d, time.min, tzinfo=timezone.utc)\n'), ['a UTC midnight'])
check('a day off a UTC instant', rules('d = datetime.now(timezone.utc).date()\n'), ['a day taken from a UTC instant'])
check('a day off a UTC instant, UTC spelling', rules('d = datetime.now(UTC).date()\n'), ['a day taken from a UTC instant'])
check('a fixed Pacific offset', rules('pt = now - timedelta(hours=7)\n'), ['a fixed Pacific offset (DST breaks it)'])
check('one finding per line, the forbidden one first', rules('d = datetime.now(UTC).date()  # x\n'), ['a day taken from a UTC instant'])

print('UTC needs a reason')
check('now(UTC) with no reason', rules('n = datetime.now(UTC)\n'), ['UTC with no utc-ok: / local-ok: reason'])
check('tzinfo=timezone.utc with no reason', rules('d = datetime(2026, 1, 1, tzinfo=timezone.utc)\n'), ['UTC with no utc-ok: / local-ok: reason'])
check('astimezone(UTC) with no reason', rules('u = d.astimezone(UTC)\n'), ['UTC with no utc-ok: / local-ok: reason'])
check('utc-ok passes', rules('n = datetime.now(UTC)  # utc-ok: an instant for the record\n'), [])
check('local-ok passes', rules('n = datetime.now()  # local-ok: the desktop clock, shown to the person at the keyboard\n'), [])
check('a reason passes a forbidden form too', rules('n = datetime.utcnow()   # utc-ok: an expiry instant\n'), [])
check('an import line is not a finding', rules('from datetime import UTC, datetime, timezone\nimport datetime\n'), [])

print('strings and comments are not code')
check('a docstring mentioning datetime.now()', rules('def f():\n    """Never call datetime.now() or date.today() here."""\n    return 1\n'), [])
check('a comment mentioning utcnow()', rules('x = 1  # not datetime.utcnow(), that would be wrong\n'), [])
check('a string literal', rules('msg = "datetime.now() is a UTC day in disguise"\n'), [])
check('an f-string', rules('msg = f"{x} date.today() looks like code"\n'), [])
check('an expression inside an f-string is code', rules('msg = f"today is {date.today()}"\n'), ['date.today(): a UTC day on a server'])
check('a multi-line string', rules('doc = """\nnever date.today()\nor utcnow()\n"""\nx = 1\n'), [])
check('the real call beside a string', rules('log("date.today() is bad", date.today())\n'), ['date.today(): a UTC day on a server'])

print('files and the CLI')
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / 'ok.py').write_text('from zoneinfo import ZoneInfo\n', encoding='utf-8')
    (root / 'bad.py').write_text('x = 1\nd = date.today()\n', encoding='utf-8')
    (root / '.venv').mkdir()
    (root / '.venv' / 'lib.py').write_text('d = date.today()\n', encoding='utf-8')
    (root / 'node_modules').mkdir()
    (root / 'node_modules' / 'lib.py').write_text('d = utcnow()\n', encoding='utf-8')
    (root / 'broken.py').write_text('def f(:\n    d = date.today()\n', encoding='utf-8')
    found = T.scan([root])
    check('the bad file and the unparseable one are found, the vendored ones skipped',
          [(Path(f.path).name, f.line) for f in found], [('bad.py', 2), ('broken.py', 2)])
    check('a finding prints as path:line: rule: text', str(found[0]).endswith('bad.py:2: date.today(): a UTC day on a server: d = date.today()'), True)
    out = io.StringIO()
    with redirect_stdout(out):
        rc = T.main([str(root)])
    check('main exits 1 on findings and counts them', (rc, out.getvalue().strip().splitlines()[-1]), (1, '2 finding(s)'))
    out = io.StringIO()
    with redirect_stdout(out):
        rc = T.main([str(root / 'ok.py')])
    check('main exits 0 on a clean file', (rc, out.getvalue().strip()), (0, '0 finding(s)'))

print("the kit's own tree")
own = T.scan([os.path.join(ROOT, 'src'), os.path.join(ROOT, 'tests')])
check("src/ and tests/ pass the kit's own scanner", [str(f) for f in own], [])

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
