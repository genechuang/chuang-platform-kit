"""The kit's time zone, in one place, and the three ways a time is written or cut for a person.

The modules that stamp a date (email_notify's dated subjects) read it here.
It is CHUANG_PLATFORM_TZ when set, else America/Los_Angeles: every host so far
runs on Pacific time, and a kit that defaulted to UTC would have moved each
host's dated subjects by a day at the first midnight. A host that wants
another zone sets the variable; a caller that has its own zone object passes
it in, the functions take `tz=`.

Gene's two rules (shared CLAUDE.md, 2026-10-07 PT), as functions:
  - every written-out date carries its year: `human_day` -> "10/7/26", `human_time` -> "Wed 10/7/26 7:00 AM PT";
  - in code, every calendar day is a day in the kit's zone, never a UTC day: `day_of` cuts a machine timestamp
    ("2026-10-06T01:30:00Z") at the zone's midnight ("2026-10-05" in Pasadena). Servers run UTC clocks, so a UTC
    stamp sliced with [:10] is a UTC day in disguise; `timecheck` is the scanner that finds those.
"""
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = 'America/Los_Angeles'


def zone():
    """The configured zone as a tzinfo (zoneinfo; works with datetime.now(tz))."""
    return ZoneInfo(os.environ.get('CHUANG_PLATFORM_TZ') or DEFAULT_TZ)


US_LABELS = {'PST': 'PT', 'PDT': 'PT', 'MST': 'MT', 'MDT': 'MT', 'CST': 'CT', 'CDT': 'CT', 'EST': 'ET', 'EDT': 'ET',
             'AKST': 'AKT', 'AKDT': 'AKT', 'HST': 'HT', 'HDT': 'HT'}
"""How people write a US zone whatever the season: PDT and PST are both "PT". Other zones keep their own name (BST stays
BST: British Summer Time is not "BT")."""


def tz_label(d: datetime | None = None, tz=None) -> str:
    """The short label people write for the zone at instant `d` (now when omitted): "PT" for Pacific in either season,
    the same for the other US zones, any other zone's own abbreviation as it is."""
    tz = tz or zone()
    name = (d or datetime.now(timezone.utc)).astimezone(tz).tzname() or ''  # utc-ok: an instant, only to ask the zone its name now
    return US_LABELS.get(name, name)


def human_day(day) -> str:
    """A day written for a person, with its year: 10/5/26. Takes a date, a datetime (its date), or a YYYY-MM-DD string."""
    if isinstance(day, datetime):
        day = day.date()
    elif isinstance(day, str):
        day = date.fromisoformat(day[:10])
    return f'{day.month}/{day.day}/{day:%y}'


def human_time(d: datetime, weekday: bool = True, tz_word: bool = False, tz=None) -> str:
    """A moment written for a person in the kit's zone, with the year: `Wed 10/7/26 7:00 AM`, plus ` PT` when `tz_word`.
    A naive datetime is taken as UTC: machine sources (Gmail, Cloud Logging, a database) hand out UTC without saying so.
    Built by hand: strftime's `%-d` / `%#d` are not portable between Windows and Linux."""
    tz = tz or zone()
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)  # utc-ok: a naive datetime from a machine source is UTC
    local = d.astimezone(tz)
    clock = f'{local.hour % 12 or 12}:{local:%M} {"AM" if local.hour < 12 else "PM"}'
    return (f'{local:%a} ' if weekday else '') + f'{human_day(local)} {clock}' + (f' {tz_label(local, tz)}' if tz_word else '')


def day_of(stamp: str, tz=None) -> str:
    """The calendar day (YYYY-MM-DD) in the kit's zone of an ISO timestamp from a machine: "2026-10-06T01:30:00Z" is
    2026-10-05 in Pasadena, and slicing it with [:10] would call it the 6th. A stamp with no zone is already a local day
    and comes back as its first ten characters; an empty stamp comes back empty."""
    if not stamp:
        return ''
    d = datetime.fromisoformat(stamp)
    if d.tzinfo is None:
        return stamp[:10]
    return d.astimezone(tz or zone()).strftime('%Y-%m-%d')
