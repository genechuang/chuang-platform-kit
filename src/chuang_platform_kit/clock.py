"""The kit's time zone, in one place.

The modules that stamp a date (email_notify's dated subjects) read it here.
It is CHUANG_PLATFORM_TZ when set, else America/Los_Angeles: every host so far
runs on Pacific time, and a kit that defaulted to UTC would have moved each
host's dated subjects by a day at the first midnight. A host that wants
another zone sets the variable; a caller that has its own zone object passes
it in, the functions take `tz=`.
"""
import os
from zoneinfo import ZoneInfo

DEFAULT_TZ = 'America/Los_Angeles'


def zone():
    """The configured zone as a tzinfo (zoneinfo; works with datetime.now(tz))."""
    return ZoneInfo(os.environ.get('CHUANG_PLATFORM_TZ') or DEFAULT_TZ)
