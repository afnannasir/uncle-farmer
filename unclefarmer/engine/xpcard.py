"""Experience cards: one running while we farm, paused while we are away from the monsters.

What the game does (hand recordings 2026-11-11):
  - The cards are bag items, "<n>x experience acceleration card" for n = 1.3, 1.5, 1.75, 2, 2.5
    and 3 (a 1.3x shows on the character sheet as "30% EXP bonus"); using one is the ordinary use
    of a bag item (OUT op=18 with its uid), and it runs for 30 minutes.
  - While one runs, the character sheet carries an entry "30% EXP bonus; press to pause" with
    "28 minutes 55 seconds remaining"; a paused one reads "Paused; press to resume". No entry: no
    card. Agent 60 reads it into the sheet (exp_bonus, exp_paused, exp_seconds).
  - Pressing the entry pauses or resumes the card: both send the same one packet, which agent 60's
    `xpbonus pause|resume` sends only when a fresh sheet shows the card the other way. The game
    asks "are you sure" before a pause because a pause costs one minute of the card, and a pause
    is invalid in a dungeon.

So, with the Character settings' ticks:
  - xp_card_name: on the farm, a card is kept running: a paused one is resumed, and when none runs
    a card of the kind the player chose is used - that kind only, never a stronger or weaker one
    (the strong ones are kept for when the player wants them). None of it in the bag: farming goes
    on without a card. Empty: no card is ever used (a paused one is still resumed with the pause
    tick).
  - xp_card_pause: a running card is paused whenever a farming run travels to another map (to
    town for potions or repairs, back after a death, home at the end) and when the run ends; it
    runs again once farming starts on the farm (with either tick on). Never in a dungeon, and not
    with less than MIN_LEFT seconds left: the pause would cost more than it keeps.
Whatever fails is said once and tried again after a rest; it never turns itself off for the run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import atlas
from ..i18n import _

if TYPE_CHECKING:
    from .bot import Bot

AGENT = 60                # the agent that reads the card off the sheet and sends `xpbonus`
CARD = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*x\s+experience acceleration card\s*$", re.I)
LOOK_EVERY = 20.0         # seconds between looks at the card while farming
FRESH = 10                # seconds old a sheet may be for a pause or resume (the agent takes 15)
MIN_LEFT = 90             # seconds: a card this close to its end is not paused (a pause costs 60)
WAIT = 3.0                # seconds the sheet has to show a change
REST = 60.0               # seconds before trying again after something failed


@dataclass
class Cards:
    """One run's experience card handling (b.xpcard)."""
    off: str = ""                 # why it can't work in this game ("" while it can)
    next_look: float = 0.0
    rest_until: float = 0.0
    used: int = 0                 # cards started this run
    paused: int = 0               # pauses this run
    resumed: int = 0
    said: set[str] = field(default_factory=lambda: set())   # reasons already logged once


def state(b: Bot) -> Cards:
    st: Cards | None = getattr(b, "xpcard", None)
    if st is None:
        st = Cards()
        b.xpcard = st
    return st


def wanted(p: dict[str, Any]) -> tuple[str, bool]:
    """(the card to keep running, "" for none; pause it while away) from the Character settings."""
    name = p.get("xp_card_name") or ""
    return (name.strip() if isinstance(name, str) else ""), bool(p.get("xp_card_pause", False))


def strength(name: str) -> float | None:
    """2.0 for "2x experience acceleration card", None for anything else."""
    m = CARD.match(name or "")
    return float(m.group(1)) if m else None


def cards_in(rows: list[dict[str, Any]]) -> list[tuple[float, str, int]]:
    """(strength, name, count) of the experience cards in a bag listing, weakest first (the order
    the Character settings list them in)."""
    out: dict[str, tuple[float, str, int]] = {}
    for r in rows:
        name = str(r.get("name") or "")
        s = strength(name)
        n = int(r.get("count") or 0)
        if s is None or n <= 0:
            continue
        key = name.strip().lower()
        had = out.get(key)
        out[key] = (s, name.strip(), n + (had[2] if had else 0))
    return sorted(out.values(), key=lambda c: (c[0], c[1]))


def card_of(sheet: dict[str, Any] | None) -> dict[str, Any] | None:
    """{"bonus", "paused", "seconds"} of the card on the sheet ({"bonus": 0} when none runs), or
    None when this sheet can't tell (no sheet, or an agent older than AGENT)."""
    if not sheet or "exp_bonus" not in sheet:
        return None
    left = int(sheet.get("exp_seconds", -1))
    if left >= 0 and not sheet.get("exp_paused"):
        left = max(0, left - int(sheet.get("age", 0)))   # a running card's time went on since the sheet
    return {"bonus": int(sheet.get("exp_bonus") or 0), "paused": bool(sheet.get("exp_paused")), "seconds": left}


def describe(card: dict[str, Any]) -> str:
    s = max(0, int(card.get("seconds", 0)))
    return _("{bonus}% more experience, {m} min {s} s left", bonus=card["bonus"], m=s // 60, s=s % 60)


def _once(b: Bot, st: Cards, key: str, line: str, speak: bool = False) -> None:
    if key not in st.said:
        st.said.add(key)
        b.log(line, speak=speak)


def _usable(b: Bot, st: Cards) -> bool:
    if st.off:
        return False
    version = int(getattr(b, "agent_version", 0) or 0)
    if version and version < AGENT:
        st.off = "old agent"
        b.log(_("experience cards need the game's agent {need} or newer (this game has {have}) - "
                "restart the game so the new agent loads", need=AGENT, have=version), speak=True)
        return False
    return True


def _sheet(b: Bot, fresh: bool) -> dict[str, Any] | None:
    """The last sheet, or a new one when fresh is asked and it is older than FRESH seconds."""
    sheet = b.refresh_sheet()
    if fresh and (not sheet or sheet.get("age", 999) > FRESH):
        sheet = b.refresh_sheet(force=True)
    return sheet


def in_dungeon(b: Bot) -> bool:
    return bool(getattr(b, "in_instance", False)) or atlas.kind(b.map_id()) == "dungeon"


def on_farm(b: Bot, now: float | None = None) -> None:
    """Between fights on the farm: a paused card runs again, and with a card chosen (xp_card_name)
    a new one of that kind is used when none runs. Looks every LOOK_EVERY seconds at most."""
    keep, pause = wanted(b.p)
    if not (keep or pause):
        return
    st = state(b)
    now = b.clock.time() if now is None else now
    if now < st.next_look or now < st.rest_until or not _usable(b, st):
        return
    st.next_look = now + LOOK_EVERY
    card = card_of(_sheet(b, fresh=False))
    if card is None:
        _once(b, st, "unknown", _("can't read the experience card from the character sheet yet"))
        return
    if card["bonus"] > 0:
        if card["paused"]:
            _toggle(b, st, "resume", _("back on the farm"))
        return
    if keep:
        _use(b, st, keep)


def away(b: Bot, why: str) -> None:
    """Leaving the monsters (why: where to): pause a running card, with xp_card_pause on."""
    if not wanted(b.p)[1]:
        return
    st = state(b)
    if b.clock.time() < st.rest_until or not _usable(b, st) or in_dungeon(b):
        return
    card = card_of(_sheet(b, fresh=False))
    if card is None or card["bonus"] <= 0 or card["paused"]:
        return                     # nothing running (the sheet after every kill says so)
    card = card_of(_sheet(b, fresh=True))
    if card is None or card["bonus"] <= 0 or card["paused"]:
        return
    if card["seconds"] < MIN_LEFT:
        return                     # the pause would cost most of what is left
    _toggle(b, st, "pause", why)
    st.next_look = 0.0             # back on the farm, it runs again at the first look


def _toggle(b: Bot, st: Cards, want: str, why: str) -> bool:
    """Pause or resume, then the sheet read until it shows it. True when it did."""
    if not _sheet(b, fresh=True):
        return False
    reply = b.a.send("xpbonus " + want)
    if reply != "sent":
        st.rest_until = b.clock.time() + REST
        if "already" in reply:
            st.next_look = 0.0     # the sheet changed under us (the player pressed it): look again
            return False
        _once(b, st, "toggle " + reply, _("couldn't {what} the experience card: {why} - trying again in a minute",
                                         what=_("pause") if want == "pause" else _("resume"), why=reply))
        return False
    paused = want == "pause"
    end = b.clock.time() + WAIT
    card: dict[str, Any] | None = None
    while b.clock.time() < end:
        b.sleep(0.4)
        card = card_of(b.refresh_sheet(force=True))
        if card is not None and card["bonus"] > 0 and card["paused"] == paused:
            break
    else:
        st.rest_until = b.clock.time() + REST
        _once(b, st, "unconfirmed " + want, _("the character sheet didn't show the experience card {what} - "
                                              "looking again in a minute",
                                              what=_("paused") if paused else _("running")))
        return False
    assert card is not None
    if paused:
        st.paused += 1
        b.log(_("experience card paused ({card}): {why}", card=describe(card), why=why))
    else:
        st.resumed += 1
        b.log(_("experience card running again ({card}): {why}", card=describe(card), why=why))
    return True


def _use(b: Bot, st: Cards, chosen: str) -> bool:
    """A card of the chosen kind from the bag, then the sheet read until it runs. True when it does.
    Only that kind: a stronger card in the bag is never used in its place."""
    want = chosen.strip().lower()
    found = [c for c in cards_in(b.bag_rows()) if c[1].lower() == want]
    if not found:
        _once(b, st, "none", _("no {card} in the bag - farming on without an experience card", card=chosen),
              speak=True)
        st.rest_until = b.clock.time() + 10 * REST        # a card bought or traded in comes into use later
        return False
    st.said.discard("none")
    _power, name, count = found[0]
    if not _sheet(b, fresh=True):
        return False
    if not b.use_item(name):
        st.rest_until = b.clock.time() + REST
        return False
    end = b.clock.time() + WAIT
    card: dict[str, Any] | None = None
    while b.clock.time() < end:
        b.sleep(0.4)
        card = card_of(b.refresh_sheet(force=True))
        if card is not None and card["bonus"] > 0:
            break
    else:
        st.rest_until = b.clock.time() + REST
        _once(b, st, "unused " + name, _("used a {card}, but the character sheet shows no experience card running - "
                                         "trying again in a minute", card=name))
        return False
    assert card is not None
    st.used += 1
    b.log(_("experience card started: {card} ({left} more in the bag; {now})", card=name, left=count - 1,
            now=describe(card)))
    return True
