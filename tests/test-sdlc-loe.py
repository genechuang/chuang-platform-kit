#!/usr/bin/env python3
"""chuang_sdlc.loe: a ticket's level of effort, estimated and actual.

Pinned (the positive case first): the log's lines parsed, malformed ones skipped; working minutes charged to the earlier
line's tickets, split between two, capped, nothing after a stop or a none, None for a ticket never named; the delegates'
time: a ticket built wholly by a delegate gets the agent's time, one built by hand is unchanged to the minute, two agents
in parallel and an agent beside the session count once, an agent on a two-ticket delegation is split between them and
adds nothing to a ticket the session works alone beside it, a delegated line naming none counts nothing, an agent with no
tool_use_id falls back to its session's newest line, the two new shapes parsed and a four-field line skipped; the factor
by span with the whole set's fallback and none under five; calibrate's rounding and floor; hours and words; text in the
kit's zone with its year, with and without the delegates' clause, the unmeasured case naming the host's start command;
record writing the estimate, the elapsed from the first start, the actual, the delegated minutes and the count, skipping
a commit it has, and the board's Actual.

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

print("the delegates' time")


def agent(name, tool, start, stop, every=60, session='s'):
    return '\n'.join(f'{t} {session} agent {name} {tool}' for t in range(start, stop + 1, every))


log = L.parse('1000 s 20 delegated tu1\n' + agent('a1', 'tu1', 1000, 2200) + '\n2200 s stop')
check("the positive case: a ticket built wholly by a delegate gets the agent's time (300 s own, 1200 s of the agent's, "
      "the 300 that overlap counted once)", L.minutes(log, [20]), {20: {'active': 20, 'delegated': 15}})
check('active_minutes is its active', L.active_minutes(log, [20, 99]), {20: 20, 99: None})
check('a ticket built by hand is unchanged to the minute, nothing delegated', L.minutes(lines, [12, 14, 99]),
      {12: {'active': 3, 'delegated': 0}, 14: {'active': 1, 'delegated': 0}, 99: None})
log = L.parse('0 s 30 delegated tA\n0 s 30 delegated tB\n' + agent('a', 'tA', 0, 600) + '\n' + agent('b', 'tB', 0, 600) + '\n600 s stop')
check('two agents in parallel count once: 600 s of agents, 300 of them beside the session', L.minutes(log, [30]),
      {30: {'active': 10, 'delegated': 5}})
log = L.parse('0 s 40 delegated tC\n' + '\n'.join(f'{t} s 40' for t in range(60, 601, 60)) + '\n' + agent('c', 'tC', 0, 600) + '\n660 s stop')
check("an agent beside the session's own work on the ticket counts once", L.minutes(log, [40]), {40: {'active': 11, 'delegated': 0}})
log = L.parse('0 s 12,14 delegated tu\n' + agent('a', 'tu', 0, 3240) + '\n240 s stop')
check("an agent on a two-ticket delegation is split between them as a session's gap is (240 s own and 3240 of the "
      "agent's: 120 + 1500 s each, the 240 beside the session counted once; the 11/8/26 review)", L.minutes(log, [12, 14]),
      {12: {'active': 27, 'delegated': 25}, 14: {'active': 27, 'delegated': 25}})
log = L.parse('0 s 12,14 delegated tu\n' + '\n'.join(f'{t} s 12' for t in range(30, 631, 60)) + '\n' + agent('a', 'tu', 0, 630, every=30) + '\n690 s stop')
check("beside the session's own full share on #12 the agent's half share adds nothing; #14 gets its half (15 s own, 300 of the agent's)",
      L.minutes(log, [12, 14]), {12: {'active': 11, 'delegated': 0}, 14: {'active': 5, 'delegated': 5}})
log = L.parse('0 s 50\n60 s none delegated tD\n' + agent('d', 'tD', 60, 660) + '\n700 s stop')
check('an agent whose delegated line named none counts nothing', L.minutes(log, [50]), {50: {'active': 1, 'delegated': 0}})
log = L.parse('0 s 60\n' + agent('e', '-', 10, 610) + '\n1000 s stop')
check("an agent with no tool_use_id falls back to its session's newest ticket line (600 s, 290 beside the session)",
      L.minutes(log, [60]), {60: {'active': 10, 'delegated': 5}})
log = L.parse('0 s 70\n100 s stop\n' + agent('f', '-', 200, 800, every=600))
check("one whose session's newest line is a stop counts nothing", L.minutes(log, [70]), {70: {'active': 2, 'delegated': 0}})
log = L.parse('0 other 80 delegated tG\n' + agent('g', 'tG', 500, 1100, every=600, session='s') + '\n100 other 80\n1100 other stop')
check("the delegated line is matched by tool_use_id across sessions (400 s of the other session's own, the agent's one "
      "interval capped at 300)", L.minutes(log, [80]), {80: {'active': 12, 'delegated': 5}})
check('parse: the two new shapes, a four-field line and a delegated line with no tickets skipped',
      L.parse('5 s 12,14 delegated toolu_1\n6 s agent ag1 toolu_1\n7 s agent ag2 -\n8 s none delegated toolu_2\n'
              '9 s 12 extra\n10 s 12 delegated\n11 s x delegated toolu_3\n12 s 12 delegated toolu_4 more'),
      [(5, 's', ('delegated', 'toolu_1', (12, 14))), (6, 's', ('agent', 'ag1', 'toolu_1')), (7, 's', ('agent', 'ag2', '-')),
       (8, 's', ('delegated', 'toolu_2', ()))])

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
check("text with the delegates' minutes, after the work and before the difference", L.text(dict(rec, delegated_min=45)),
      'estimated 2h, actual 1h 10m of work, 45m of it by delegates (-42%), 3h 20m elapsed, from the first start 11/2/26 '
      '12:50 PM PT to the last commit 11/2/26 4:10 PM PT (2 commits)')
check('delegated_min of 0 or None says nothing of delegates',
      (L.text(dict(rec, delegated_min=0)) == L.text(rec), L.text(dict(rec, delegated_min=None)) == L.text(rec)), (True, True))
check('an actual that is the elapsed (no working time) says nothing of delegates either',
      L.text({'estimate_min': 60, 'actual_min': 50, 'delegated_min': 20}), 'estimated 1h, actual 50m (-17%) (1 commit)')
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
got = L.record({'tickets': [7], 'at': at, 'key': 'k1', 'active': {'7': 25}, 'delegated': {'7': 10}}, st, lambda n: 60,
               lambda n, h: actuals.append((n, h)) or True, records='loe', starts='starts')
doc = st.docs[('loe', '7')]
check('the positive case: the record written', (doc['estimate_min'], doc['actual_min'], doc['elapsed_min'], doc['commits']),
      (60, 25, 60, 1))
check("the board's Actual", actuals, [(7, '0.42')])
check('its line', got[0].startswith('tickets: #7 LOE so far: estimated 1h, actual 25m of work, 10m of it by delegates'), True)
check("the event's delegated minutes kept", doc['delegated_min'], 10)
again = L.record({'tickets': [7], 'at': at, 'key': 'k1'}, st, lambda n: 60, lambda n, h: True, records='loe', starts='starts')
check('the same commit again writes nothing', (again, st.docs[('loe', '7')]['commits']),
      (['tickets: #7 LOE recorded already for this commit'], 1))
L.record({'tickets': [7], 'at': at + 600, 'key': 'k2'}, st, lambda n: None, lambda n, h: False, records='loe', starts='starts')
doc = st.docs[('loe', '7')]
check('a second commit: no working time is the elapsed, the estimate kept, counted', (doc['actual_min'], doc['estimate_min'], doc['commits']),
      (70, 60, 2))
check('an event with no delegated minutes keeps None', doc['delegated_min'], None)
try:
    L.record({'tickets': [7], 'at': at, 'key': 'k3'}, st, lambda n: None, lambda n, h: True)
    check('record with no collections named is refused (the kit names none)', 'written', 'ValueError')
except ValueError:
    check('record with no collections named is refused (the kit names none)', True, True)

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
