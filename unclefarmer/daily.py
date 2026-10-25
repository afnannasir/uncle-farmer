"""The farming day: every run ends at a fixed time of the computer's clock, before the game's day
turns over.

The day's public area drop time resets at 00:00 GMT (05:00 in Pakistan, for one). A bot that farms
across that moment starts spending the next day's drop time. So, whatever a plan or script says
and whatever the bot is doing at that moment, a run stops at the daily stop time and ends where its
plan says; and between that time and the reset no run starts.

The stop time is the player's ("HH:MM", the computer's own clock) or, by default, the reset minus
STOP_BEFORE minutes: leaving the monsters takes a while, and the drop timer keeps running a few
minutes after reaching safety.
"""

import datetime as dt
import re

STOP_BEFORE = 10      # minutes before the reset, for the default stop time
NEAR_RESET = 120      # a stop time this close before the reset blocks starting until the reset


def reset_local(now=None):
    """The next 00:00 GMT, as a local datetime (naive, the computer's own clock)."""
    now = now or dt.datetime.now().astimezone()
    utc = now.astimezone(dt.timezone.utc)
    nxt = (utc + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.astimezone(now.tzinfo).replace(tzinfo=None)


def game_day(now=None):
    """The game's day as "YYYY-MM-DD": the date at 00:00 GMT, when its daily limits start again."""
    now = now or dt.datetime.now().astimezone()
    return now.astimezone(dt.timezone.utc).date().isoformat()


def reset_clock():
    """The reset as "HH:MM" on this computer's clock."""
    return reset_local().strftime("%H:%M")


def default_clock():
    return (reset_local() - dt.timedelta(minutes=STOP_BEFORE)).strftime("%H:%M")


def valid(text):
    return bool(re.match(r"^([01]\d|2[0-3]):[0-5]\d$", (text or "").strip()))


def stop_clock(app):
    """The daily stop time in force ("HH:MM"), or None when it is turned off."""
    if not app.get("daily_stop", True):
        return None
    t = (app.get("daily_stop_at") or "").strip()
    return t if valid(t) else default_clock()


def _at(day, clock):
    h, m = map(int, clock.split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def plan(app, now=None):
    """(the next stop as a local datetime or None, why a run may not start now or None)."""
    clock = stop_clock(app)
    if not clock:
        return None, None
    now = now or dt.datetime.now()
    today = _at(now, clock)
    last = today if today <= now else today - dt.timedelta(days=1)
    nxt = last + dt.timedelta(days=1)
    reset_after_last = reset_local((last + dt.timedelta(seconds=1)).astimezone())
    if now < reset_after_last and (reset_after_last - last).total_seconds() <= NEAR_RESET * 60:
        return nxt, reset_after_last.strftime("%H:%M")
    return nxt, None
