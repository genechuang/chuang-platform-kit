#!/usr/bin/env python3
"""clock: the zone, and the three ways a time is written or cut for a person (Gene's rules, 2026-10-07 PT).

Pinned:
  - human_day: 10/5/26 from a date, a datetime or a YYYY-MM-DD string (the positive case first);
  - human_time: Pacific with the year, `Wed 10/7/26 7:00 AM`, with ` PT` on request; 03:00Z is still the 7th in Pasadena;
    Pacific midnight is 12:00 AM; a naive datetime is UTC; another zone through `tz=` or CHUANG_PLATFORM_TZ;
  - tz_label: PT in summer (PDT) and winter (PST), ET for New York, a non-US zone keeps its own name;
  - day_of: a UTC stamp cut at the zone's midnight, across the November fall-back, and a zoneless stamp left as it is.

Run:  python -B tests/test-clock.py
"""
import os
import sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'src'))
os.environ.pop('CHUANG_PLATFORM_TZ', None)

from chuang_platform_kit import clock as C  # noqa: E402

UTC = timezone.utc  # utc-ok: the fixed instants below are written in UTC and converted by the code under test
fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        fails.append(label)


print('human_day')
check('from a date', C.human_day(date(2026, 10, 5)), '10/5/26')
check('from a datetime', C.human_day(datetime(2026, 1, 2, 9, 0, tzinfo=UTC)), '1/2/26')   # utc-ok: a fixed test instant
check('from a YYYY-MM-DD string', C.human_day('2026-12-31'), '12/31/26')

print('human_time')
check('Pacific with the year', C.human_time(datetime(2026, 10, 7, 14, 0, tzinfo=UTC)), 'Wed 10/7/26 7:00 AM')   # utc-ok: fixed instant
check('no weekday, with the zone word', C.human_time(datetime(2026, 10, 7, 14, 0, tzinfo=UTC), weekday=False, tz_word=True), '10/7/26 7:00 AM PT')  # utc-ok: fixed instant
check('03:00Z is still the 7th in Pasadena', C.human_time(datetime(2026, 10, 8, 3, 0, tzinfo=UTC)), 'Wed 10/7/26 8:00 PM')   # utc-ok: fixed instant
check('Pacific midnight is 12:00 AM', C.human_time(datetime(2026, 10, 7, 7, 0, tzinfo=UTC)), 'Wed 10/7/26 12:00 AM')   # utc-ok: fixed instant
check('noon', C.human_time(datetime(2026, 10, 7, 19, 5, tzinfo=UTC)), 'Wed 10/7/26 12:05 PM')   # utc-ok: fixed instant
check('a naive datetime is UTC', C.human_time(datetime(2026, 10, 7, 14, 0)), 'Wed 10/7/26 7:00 AM')
check('another zone by tz=', C.human_time(datetime(2026, 10, 7, 14, 0, tzinfo=UTC), tz=ZoneInfo('America/New_York'), tz_word=True), 'Wed 10/7/26 10:00 AM ET')  # utc-ok: fixed instant
os.environ['CHUANG_PLATFORM_TZ'] = 'Europe/London'
check('CHUANG_PLATFORM_TZ is honoured', C.human_time(datetime(2026, 10, 7, 14, 0, tzinfo=UTC), tz_word=True), 'Wed 10/7/26 3:00 PM BST')   # utc-ok: fixed instant
os.environ.pop('CHUANG_PLATFORM_TZ', None)

print('tz_label')
check('PT in summer (PDT)', C.tz_label(datetime(2026, 7, 1, tzinfo=UTC)), 'PT')   # utc-ok: fixed instant
check('PT in winter (PST)', C.tz_label(datetime(2026, 12, 1, tzinfo=UTC)), 'PT')   # utc-ok: fixed instant
check('ET for New York', C.tz_label(datetime(2026, 7, 1, tzinfo=UTC), tz=ZoneInfo('America/New_York')), 'ET')   # utc-ok: fixed instant
check('a non-US zone keeps its own name', C.tz_label(datetime(2026, 1, 1, tzinfo=UTC), tz=ZoneInfo('Europe/Paris')), 'CET')   # utc-ok: fixed instant

print('day_of')
check('a UTC evening stamp is the day before in Pasadena', C.day_of('2026-10-06T01:30:00Z'), '2026-10-05')
check('exactly Pacific midnight', C.day_of('2026-10-06T07:00:00Z'), '2026-10-06')
check('after the 11/1/26 fall-back it is PST (UTC-8): 11:30 PM the 1st', C.day_of('2026-11-02T07:30:00Z'), '2026-11-01')
check('08:00Z on 11/2 is the 2nd', C.day_of('2026-11-02T08:00:00Z'), '2026-11-02')
check('an explicit offset', C.day_of('2026-10-06T01:30:00+00:00'), '2026-10-05')
check('a zoneless stamp is already a local day', C.day_of('2026-10-05T23:59:00'), '2026-10-05')
check('a bare date', C.day_of('2026-10-05'), '2026-10-05')
check('empty stays empty', C.day_of(''), '')
check('another zone by tz=', C.day_of('2026-10-06T01:30:00Z', tz=ZoneInfo('Asia/Tokyo')), '2026-10-06')

print(f'\n{len(fails)} failure(s)')
sys.exit(1 if fails else 0)
