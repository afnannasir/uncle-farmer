"""Log out for a while, then back in: a trusted player's `wait` by private message.

Someone else may need the account, or the character should be out of the game for a while. The
bot logs the character out the way the player does (the setting menu's Switch account: the key
W_OPTION, F1 by default, then that entry), waits at the login screen, then logs the same character
back in with the game's saved account (the agent's autologin, as a run started at the login screen
does) and the run goes on where it was. The wait counts as a pause: nothing in it is the run's.

Seen live (2026-11-24): the setting menu lists "switch account" just before "quit"; once it is
chosen the game has no character at once (`mem`: "noplayer: id 0") and the connection is closed
5 s later ("noworld"); after the login, the character list is a menu, then the world is back on
the map it was left on.

Stopped from Uncle Farmer during the wait (Stop), the character stays logged out: the player is
at the computer. A login that fails TRIES times, a minute apart, ends the run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .. import gamekeys, settings
from ..i18n import _
from .botparts.base import Stopped

if TYPE_CHECKING:
    from .bot import Bot

DEFAULT_MINUTES = 30
MOST_MINUTES = 999
SWITCH = r"^(switch account|cambiar de cuenta)"
MENU_WAIT = 3.0          # seconds for the setting menu to open
GONE_WAIT = 15.0         # seconds for the character to leave the game after Switch account
LOGIN_WAIT = 90.0        # seconds one login may take (the agent's own steps take 30 s each)
TRIES = 5                # logins tried after the wait ...
TRY_GAP = 60.0           # ... this many seconds apart
KEEPALIVE = 60.0         # the agent drops a connection quiet for 120 s
REPORT_EVERY = 10        # minutes between two "still waiting" lines

Tell = Callable[[str], None]


def account(bot: Bot) -> tuple[str, int, str] | None:
    """(saved account, character id, server) to log back in with, or None when one isn't known."""
    key = getattr(bot, "profile_key", None) or ""
    email = settings.load_profile(key).get("login_email") if key else None
    entity_id, server = settings.profile_id(key), settings.profile_server(key)
    if not isinstance(email, str) or not email or not entity_id or not server:
        return None
    return email, int(entity_id), server


def gone(answer: dict[str, Any]) -> bool:
    """Whether `mem state` says the character has left the game (logged out, connection closed)."""
    why = str(answer.get("error") or "")
    return not answer.get("ok") and (why.startswith("noplayer: id 0 ") or why.startswith("noworld"))


def log_out(bot: Bot) -> str | None:
    """The setting menu's Switch account. None once the character has left the game, else why not."""
    bot.close_dialogs()
    bot.tap_key(gamekeys.option_key())
    end = bot.clock.time() + MENU_WAIT
    while not bot.dialog().get("open"):
        if bot.clock.time() > end:
            return _("the setting menu didn't open")
        bot.sleep(0.2)
    if bot.choose(SWITCH) is None:
        return _("the setting menu has no Switch account")
    end = bot.clock.time() + GONE_WAIT
    while bot.clock.time() < end:
        if gone(bot.a.json("mem state")):
            return None
        bot.clock.wait(bot.stop_event, 0.25)
    return _("the character didn't leave the game")


def log_in(bot: Bot, who: tuple[str, int, str]) -> str | None:
    """The saved account's own login, back into the same character. None once it is in, else why not."""
    email, entity_id, server = who
    link = bot.a
    if link.send("arm") != "armed":
        return _("the game refused the login request")
    reply = link.json("autologin %s %s %s" % (email, entity_id, server))
    if not reply.get("ok"):
        return str(reply.get("error") or _("automatic login could not start"))
    end = bot.clock.time() + LOGIN_WAIT
    while bot.clock.time() < end:
        if bot.clock.wait(bot.stop_event, 0.25):
            return _("stopped by you")
        state = link.json("autologinstatus")
        if state.get("running"):
            continue
        if state.get("status") != "ready":
            return str(state.get("status") or _("automatic login stopped"))
        me = link.json("mem self")
        if me.get("id") != entity_id:
            return _("another character came in")
        return None
    return _("the login took too long")


def hold(bot: Bot, minutes: int, tell: Tell) -> bool:
    """Wait at the login screen. False when Uncle Farmer's Stop ended the wait."""
    end = bot.clock.time() + minutes * 60
    asked = said = bot.clock.time()
    done = 0
    while bot.clock.time() < end:
        if bot.clock.wait(bot.stop_event, min(1.0, max(0.0, end - bot.clock.time()))):
            return False
        now = bot.clock.time()
        if now - asked >= KEEPALIVE:
            asked = now
            bot.a.send("version")
        if now - said >= REPORT_EVERY * 60:
            said = now
            done += REPORT_EVERY
            tell(_("{done} of {minutes} minutes waited", done=done, minutes=minutes))
    return True


def wait_out(bot: Bot, minutes: int, asked_by: str, tell: Tell) -> None:
    """Log out, wait minutes, log back in. tell: a line for the log (and the one who asked, before
    the logout and after the login). Raises Stopped when the character can't come back."""
    who = account(bot)
    if who is None:
        tell(_("I don't know this character's saved account, so I couldn't log back in. "
               "Log in to it once with Uncle Farmer open first."))
        return
    count: Callable[[bool], None] | None = getattr(bot, "_count_pause", None)
    if count is not None:
        count(True)                 # read the bag and sheet while the character is still in
    bot.let_go()
    why = log_out(bot)
    if why:
        if count is not None:
            count(False)
        tell(_("Couldn't log out: {why}", why=why))
        return
    bot.log(_("logged out for {minutes} minutes, as {name} asked", minutes=minutes, name=asked_by), speak=True)
    if not hold(bot, minutes, bot.log):
        raise Stopped(_("stopped by you during the wait: the character stays logged out"), final=True)
    bot.log(_("the wait is over: logging back in"), speak=True)
    for attempt in range(1, TRIES + 1):
        why = log_in(bot, who)
        if why is None:
            break
        bot.log(_("logging back in didn't work ({n} of {most}): {why}", n=attempt, most=TRIES, why=why))
        if attempt == TRIES or bot.clock.wait(bot.stop_event, TRY_GAP):
            raise Stopped(_("Could not log back in after the wait, so the run is stopping."), final=True)
    after: Callable[[], None] | None = getattr(bot, "after_login", None)
    if after is not None:
        after()                     # the run's keys, guards and muted speech, as after a reconnect
    bot.resumed_at = bot.clock.time()
    bot.__dict__["_snap_at"] = 0.0
    if count is not None:
        count(False)
    tell(_("Back in after {minutes} minutes.", minutes=minutes))


def minutes_in(rest: str, default: int) -> int | None:
    """The minutes asked for: "wait 45" is 45, "wait" the setting; None when it isn't 1 to 999."""
    word = rest.strip().lower().removesuffix("min").removesuffix("minutes").strip()
    if not word:
        return default if 1 <= default <= MOST_MINUTES else DEFAULT_MINUTES
    if not word.isdigit() or not 1 <= int(word) <= MOST_MINUTES:
        return None
    return int(word)
