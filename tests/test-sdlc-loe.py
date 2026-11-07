#!/usr/bin/env python3
"""chuang_sdlc.loe: a ticket's level of effort, estimated and actual.

Pinned (the positive case first): the log's lines parsed, malformed ones skipped; working minutes charged to the earlier
line's tickets, split between two, capped, nothing after a stop or a none, None for a ticket never named; the factor by
span with the whole set's fallback and none under five; calibrate's rounding and floor; hours and words; text in the
kit's zone with its year, the unmeasured case naming the host's start command; record writing the estimate, the
elapsed from the first start, the actual and the count, skipping a commit it has, and the board's Actual.

Run:  python -B tests/test-sdlc-loe.py
"""
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'sdlc', 'src'))
os.environ.pop('CHUANG_PLATFORM_TZ', None)

from chuang_sdlc import loe as L  # noqa: E402

fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


print('the activity log')
lines = L.parse('1000 s1 12\n1060 s1 12\n1120 s1 12,14\n1240 s1 stop\n9000 s1 12\nbad line\n1000 s2 none\n1100 s2 x')
check('the positive case: parsed, malformed skipped', lines,
      [(1000, 's1', (12,)), (1060, 's1', (12,)), (1120, 's1', (12, 14)), (1240, 's1', 'stop'), (9000, 's1', (12,)),
       (1000, 's2', 'none')])
check('minutes: 60 + 60 + 120/2 s on #12 is 3, #14 1, a never-named ticket None',
      L.active_minutes(lines, [12, 14, 99]), {12: 3, 14: 1, 99: None})
check('a gap over the cap counts the cap', L.active_minutes(L.parse('0 a 5\n10000 a 5'), [5]), {5: 5})
check('nothing after stop', L.active_minutes(L.parse('0 a stop\n100 a 5'), [5]), {5: 0})
check('read_log of a missing file is []', L.read_log(os.path.join(ROOT, 'no-such.log')), [])

print('the factor')
recs = [{'estimate_raw_min': 120, 'actual_min': a} for a in (10, 20, 30, 40, 50)] + [{'estimate_min': 30, 'actual_min': 3}]
check('the positive case: the span median', L.factor(recs, 120), (0.25, 5, '1h to 4h'))
check("a span short of five takes the whole set's", L.factor(recs, 30)[1:], (6, 'all spans'))
check('under five records in all is no factor', L.factor(recs[:3], 120), (None, 3, ''))
check('band names', [L.band(m) for m in (60, 61, 240, 241)], ['up to 1h', '1h to 4h', '1h to 4h', 'over 4h'])
check('calibrate rounds to 5 and never under 5', (L.calibrate(240, 0.04), L.calibrate(60, 0.01), L.calibrate(100, 0.5)),
      (10, 5, 50))

print('words')
check('hours', [L.hours(30), L.hours(120), L.hours(100)], ['0.5', '2', '1.67'])
check('words', [L.words(45), L.words(70), L.words(120), L.words(600), L.words(0)], ['45m', '1h 10m', '2h', '1d 2h', '0m'])
at = int(datetime(2026, 11, 2, 20, 50, tzinfo=timezone.utc).timestamp())  # utc-ok: a fixed test instant
check('a stamp in the kit zone with its year', L.stamp(at), '11/2/26 12:50 PM PT')
rec = {'estimate_min': 120, 'actual_min': 70, 'active_min': 70, 'elapsed_min': 200, 'commits': 2,
       'started_epoch': at, 'committed_epoch': at + 12000}
check('text', L.text(rec), 'estimated 2h, actual 1h 10m of work (-42%), 3h 20m elapsed, from the first start 11/2/26 '
      '12:50 PM PT to the last commit 11/2/26 4:10 PM PT (2 commits)')
check('unmeasured names the start command', L.text({'estimate_min': 30}, start_hint='tool start N'),
      'estimated 30m, actual unmeasured (no working time logged and no `tool start N` before its first commit)')


class Store:
    def __init__(self, docs=None):
        self.docs = docs or {}

    def get(self, c, i):
        return self.docs.get((c, i))

    def set(self, c, i, d):
        self.docs[(c, i)] = d


print('record')
st = Store({('starts', '7'): {'first_epoch': at - 3600}})
actuals = []
got = L.record({'tickets': [7], 'at': at, 'key': 'k1', 'active': {'7': 25}}, st, lambda n: 60,
               lambda n, h: actuals.append((n, h)) or True, records='loe', starts='starts')
doc = st.docs[('loe', '7')]
check('the positive case: the record written', (doc['estimate_min'], doc['actual_min'], doc['elapsed_min'], doc['commits']),
      (60, 25, 60, 1))
check("the board's Actual", actuals, [(7, '0.42')])
check('its line', got[0].startswith('tickets: #7 LOE so far: estimated 1h, actual 25m of work'), True)
again = L.record({'tickets': [7], 'at': at, 'key': 'k1'}, st, lambda n: 60, lambda n, h: True, records='loe', starts='starts')
check('the same commit again writes nothing', (again, st.docs[('loe', '7')]['commits']),
      (['tickets: #7 LOE recorded already for this commit'], 1))
L.record({'tickets': [7], 'at': at + 600, 'key': 'k2'}, st, lambda n: None, lambda n, h: False, records='loe', starts='starts')
doc = st.docs[('loe', '7')]
check('a second commit: no working time is the elapsed, the estimate kept, counted', (doc['actual_min'], doc['estimate_min'], doc['commits']),
      (70, 60, 2))
try:
    L.record({'tickets': [7], 'at': at, 'key': 'k3'}, st, lambda n: None, lambda n, h: True)
    check('record with no collections named is refused (the kit names none)', 'written', 'ValueError')
except ValueError:
    check('record with no collections named is refused (the kit names none)', True, True)

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
