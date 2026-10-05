"""pytest runs the hand-rolled suites: every tests/test-*.py is one item.

A suite is a script that prints one PASS/FAIL line per check and ends with
"N failure(s)"; it passes when it exits 0, prints that summary, and N is 0. A
suite that prints no summary is broken, not passing. The same rule as the
projects this kit came out of (SMAD PickleBot's scripts/run-tests.py), kept
here in forty lines so the kit depends on nothing of theirs.

    python -m pytest            # every suite
    python -m pytest -n auto    # one per core (pytest-xdist)
    python -m pytest -k redact
"""
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMMARY = re.compile(r'^(\d+) failure\(s\)', re.M)


class SuiteFile(pytest.File):
    def collect(self):
        yield SuiteItem.from_parent(self, name=self.path.name)


class SuiteItem(pytest.Item):
    def runtest(self):
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
                   PYTHONPATH=os.pathsep.join(p for p in (os.path.join(ROOT, 'src'), os.environ.get('PYTHONPATH', '')) if p))
        r = subprocess.run([sys.executable, '-B', str(self.path)], cwd=ROOT, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', env=env, timeout=60)
        out = (r.stdout or '') + (r.stderr or '')
        m = SUMMARY.search(out)
        if r.returncode != 0 or not m or int(m.group(1)) != 0:
            why = 'summary MISSING' if not m else f'{m.group(1)} failed' if int(m.group(1)) else f'exit {r.returncode}'
            tail = '\n'.join(l for l in out.splitlines() if 'FAIL' in l or 'Error' in l or 'Traceback' in l)[-2000:]
            raise SuiteFailed(f"{self.name}: {why} (exit {r.returncode})\n{tail or out[-1500:]}")

    def repr_failure(self, excinfo, style=None):
        return str(excinfo.value) if isinstance(excinfo.value, SuiteFailed) else super().repr_failure(excinfo, style)

    def reportinfo(self):
        return self.path, 0, f"suite {self.name}"


class SuiteFailed(Exception):
    pass


def pytest_collect_file(parent, file_path):
    if file_path.suffix == '.py' and file_path.name.startswith('test-') and file_path.parent.name == 'tests':
        return SuiteFile.from_parent(parent, path=file_path)
    return None
