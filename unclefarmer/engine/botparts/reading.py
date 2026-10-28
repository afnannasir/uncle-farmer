"""Reading the game, waiting, the stop flag, phase timing and the chores players ask for.

Part of the Bot class (engine/bot.py), which puts these mixins together; methods call each other
through self whatever part they live in.
"""

from __future__ import annotations

import contextlib
import queue
import time
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any

from ... import gamemem, jsonio
from ...errors import carry_on
from ...i18n import _
from .base import (GameBroken, Stopped)
from ...clock import Flag, StopFlag
from ...link import GameLink
from .parts import as_bot, Parts

if TYPE_CHECKING:
    from ..bot import Bot

AGENT_HEALTH = 36       # from here the agent counts the game's out-of-memory errors (`health`)
HEALTH_EVERY = 10.0     # seconds between health reads while running
MEMORY_EVERY = 1800.0   # seconds between the game-memory lines in the log
MEMORY_LOW_MB = 200     # address space left when the game is close to freezing: said once


class ReadingMixin(Parts):
    # Agent errors that only mean the game is in the middle of something (a map loading, a list
    # changing under the reader): worth a few seconds' patience before the run is given up.
    TRANSIENT = ("busy:", "noplayer:", "noworld: entity map")

    PHASES = ("search", "approach", "select", "fight", "rest", "town")

    held: set[str]
    guarding: bool
    daily_stop_at: float
    daily_done: bool
    daily_reset: str
    _speech_at: float
    last_me: dict[str, Any] | None
    _phases: list[list[float | str]]
    kills: int
    started: float
    doing_request: bool
    requests: "queue.Queue[Callable[[Bot], object]]"
    agent_state: bool
    resumed_at = 0.0      # when a pause last ended (check)
    broken = None         # why the game stopped working, once it has (GameBroken)
    _health_at = 0.0
    _memory_at = 0.0
    _memory_logged = 0.0
    _memory_warned = False

    @property
    def _game(self) -> GameLink:
        return self.a

    @property
    def _pause(self) -> Flag:
        return self.paused

    @property
    def _stop(self) -> StopFlag:
        return self.stop_event

    resumed_at: float = 0.0      # when a pause last ended (check)

    _snap_at: float = 0.0
    _snap_self: dict[str, Any] | None = None
    _snap_map_id: int | None = None

    def check(self) -> None:
        if self._stop.is_set():
            raise Stopped(_("stopped by you"), final=True)
        self.check_game()
        if self.daily_stop_at and not self.daily_done and self.clock.time() >= self.daily_stop_at:
            # The farming day is over, whatever we are doing: once, then the ending runs
            # (defend, then the plan's end place) without this firing again.
            self.daily_done = True
            raise Stopped(_("it's {time} - the day's farming is over (the drop time resets at {reset})",
                            time=time.strftime("%H:%M"), reset=self.daily_reset))
        if self._pause.is_set():
            walking = set(self.held)
            self.let_go()
            # Paused, the game is the player's again: their own Shift, Ctrl and Alt count in it
            # (the run's guard keeps them out only while the bot presses keys).
            guarded = getattr(self, "guarding", False) and self._game.send("guardmods off") == "off"
            # ... and so is its speech: a run mutes the game, and the player can't play it silent
            talking = self._game.send("speech") == "off" and self._game.send("speech on") == "on"
            self._count_pause(True)
            self.log(_("paused"))
            try:
                while self._pause.is_set():
                    if self.clock.wait(self._stop, 0.3):
                        raise Stopped(_("stopped by you"), final=True)
            finally:
                if guarded:
                    self._game.send("guardmods on")
                if talking and self._game.send("speech") == "on":
                    self._game.send("speech off")      # muted again, as it was (unless the player muted it)
                self._speech_at = 0.0              # the points key's speech check reads it afresh
            self._count_pause(False)
            self.log(_("going on"))
            # the walk we were in goes on: its keys down again, and its stuck and no-damage clocks
            # (walking, fighting) count from now, not from before the pause
            self.resumed_at = self.clock.time()
            for k in walking:
                self.hold(k)

    def _count_pause(self, paused: bool) -> None:
        """Paused, the game is the player's: what they kill, pick up or earn by hand is not the
        run's. The results stop counting (a bag and sheet reading on the way in and out), and on
        the way out the casts and deaths the game reported meanwhile are read and dropped, so the
        kill counter never takes them for ours."""
        tally = getattr(self, "tally", None)
        try:
            if not paused:
                tracker = getattr(self, "kill_tracker", None)
                me: dict[str, Any] = getattr(self, "last_me", None) or {}
                if tracker is not None and "id" in me:
                    tracker.update(me["id"])
            if tally is not None:
                (tally.pause if paused else tally.resume)(self)
        except Exception as e:          # a reading is a convenience; never stop a run for it
            carry_on(e, "the results around a pause")
            if tally is not None:
                tally.switch(None, None if paused else tally.before_pause)

    def check_game(self, force: bool = False) -> None:
        """Every few seconds (at once with force, as at a stuck): has the game run out of memory?
        Then the game is frozen, not the character stuck, and the run ends for good (GameBroken).
        Every minute the game's address space is read: into the log every half hour, and said
        once when little is left."""
        if self.broken:
            raise GameBroken(self.broken)
        now = self.clock.time()
        if not force and now - self._health_at < HEALTH_EVERY:
            return
        self._health_at = now
        health: dict[str, Any] = {}
        if getattr(self, "agent_version", 0) >= AGENT_HEALTH:
            health = self.a.json("health")
            if health.get("ok") and health.get("oom"):
                self.broken = _("the game ran out of memory and stopped working ({where}) - restart Mist World",
                                where=health.get("oom_where") or "?")
                raise GameBroken(self.broken)
        if now - self._memory_at < 60:
            return
        try:
            pid = getattr(self.a, "pid", None)
            space = gamemem.address_space(pid) if isinstance(pid, int) else None
        except Exception:      # a reading that fails must never stop a run
            space = None
        if not space:
            return
        used, limit = space
        if not self._memory_warned and limit - used < MEMORY_LOW_MB:
            self._memory_warned = True
            self.log(_("the game has only {left} MB of memory left - it may freeze soon; restart it when you can",
                       left=limit - used), speak=True)
        if now - self._memory_logged >= MEMORY_EVERY:
            self._memory_logged = now
            threads = health.get("threads")
            self.log(_("game memory: {used} of {limit} MB in use", used=used, limit=limit)
                     + (_(", {n} threads", n=threads) if threads else ""))
        self._memory_at = now

    def wait_keys_free(self) -> None:
        """While any of the game's text boxes is open (static Win.w != 0, e.g. mode 5 = a chat
        line), its keyboard handler drops every key we press. Players open a chat line on purpose
        to park the game's keyboard hook, so the bot closes it with Escape.
        Mode 3 (the "how many?" box) is ours to type into, so it doesn't count."""
        told, escapes = False, 0
        while True:
            s = self._game.json("input")
            mode = s.get("mode", 0) if s.get("ok") else 0
            if not s.get("open") or mode == 3:
                return
            escapes += 1
            if escapes <= 3 and self._game.send("typeesc").startswith("escaped"):
                self.sleep(0.2)
                continue
            if not told:   # an agent older than v7 can't press Escape there
                self.log(_("a text box is open in the game (chat?) - the game ignores keys until "
                           "you close it with Escape"), speak=True)
                told = True
            self.sleep(1.0)

    def close_text_box(self) -> bool:
        """A text box that opened while keys were held down (a chat line, mid-walk): they stopped
        working with no press of ours to find out, so the walk looks stuck. True when one was open
        and is closed now - then it was the box, not a wall, and the walk just goes on (the long-run
        simulator caught every such box costing a stuck, a step aside and a place on the stuck ladder)."""
        s = self._game.json("input")
        if not s.get("ok") or not s.get("open") or s.get("mode", 0) == 3:
            return False
        self.wait_keys_free()
        return True

    def sleep(self, seconds: float) -> None:
        end = self.clock.time() + seconds
        while self.clock.time() < end:
            self.check()
            self.clock.wait(self._stop, min(0.1, max(0.0, end - self.clock.time())))
        self.check()

    def _read(self, line: str, good: Callable[[dict[str, Any]], object]) -> dict[str, Any]:
        end = self.clock.time() + 5.0
        while True:
            r = self._game.json(line)
            if good(r):
                return r
            why = (r.get("error") if jsonio.is_object(r) else None) or "?"
            if not why.startswith(self.TRANSIENT) or self.clock.time() > end:
                raise Stopped(_("the game can't be read: {why}", why=why), final=True)
            self.sleep(0.25)

    def snap(self) -> dict[str, Any]:
        s = self._read("mem state" if getattr(self, "agent_state", False) else "mem",
                       lambda s: s.get("ok"))
        self._snap_at = self.clock.time()
        self._snap_self = s.get("self")
        map_info = s.get("map")
        map_data: dict[str, Any] = map_info if jsonio.is_object(map_info) else {}
        self._snap_map_id = int(map_data["id"]) if isinstance(map_data.get("id"), int) else None
        return s

    def me(self) -> dict[str, Any]:
        if self._snap_self is not None and self.clock.time() - self._snap_at < 0.15:
            return self._snap_self
        s = self._read("mem self", lambda s: "error" not in s)
        self.last_me = s
        return s

    def map_id(self) -> int:
        if self._snap_map_id is not None and self.clock.time() - self._snap_at < 0.15:
            return self._snap_map_id
        return self._read("mem map", lambda m: "error" not in m)["id"]

    def settled_map(self, seconds: float = 15) -> int:
        """The map once a map change has finished. Mid-teleport the game reports map 0 and has no
        player in its entity map (in testing the hearthstone read as "home: 0", then
        "noplayer: id ... not in the entity map" stopped the run)."""
        end = self.clock.time() + seconds
        while self.clock.time() < end:
            m = self._game.json("mem map")
            if "error" not in m and m.get("id"):
                s = self._game.json("mem self")
                if "error" not in s:
                    self.last_me = s
                    return m["id"]
            self.sleep(0.3)
        self.me()                 # still unreadable: stop with the game's own reason
        return self.map_id()

    def minutes(self) -> float:
        return (self.clock.time() - self.started) / 60.0

    def run_requests(self) -> None:
        """Do what was asked by private message (messages.py), between steps: never while a
        walking key is held, never inside another request."""
        if self.doing_request or self.held:
            return
        while True:
            try:
                job = self.requests.get_nowait()
            except queue.Empty:
                return
            self.doing_request = True
            try:
                job(as_bot(self))
            finally:
                self.doing_request = False

    @contextlib.contextmanager
    def timed(self, phase: str) -> Generator[None, None, None]:
        """Count the time spent inside under phase for the hourly timing line. A phase inside
        another (a fight on the way to a monster) counts as itself, not twice."""
        if self.timing is None:
            yield
            return
        frame: list[float | str] = [phase, self.clock.time(), 0.0]
        self._phases.append(frame)
        try:
            yield
        finally:
            self._phases.pop()
            spent = self.clock.time() - float(frame[1])
            self.timing[phase] = self.timing.get(phase, 0.0) + spent - float(frame[2])
            if self._phases:
                self._phases[-1][2] = float(self._phases[-1][2]) + spent

    def timing_report(self, final: bool = False) -> None:
        """Once an hour while farming (and when farming ends): where the time went - searching,
        walking up to monsters, selecting them, fighting, resting, trips to town - to see what
        holds the kill rate down."""
        t = self.timing
        if t is None:
            return
        now = self.clock.time()
        total = now - t["_since"]
        if total < (60 if final else 3600):
            return
        kills = self.kills - t["_kills"]
        names = {"search": _("searching"), "approach": _("walking to monsters"), "select": _("selecting"),
                 "fight": _("fighting"), "rest": _("resting"), "town": _("town and travel"),
                 "other": _("other")}
        spent = {k: t.get(k, 0.0) for k in self.PHASES}
        spent["other"] = max(0.0, total - sum(spent.values()))
        text = ", ".join("%s %d%%" % (names[k], round(100 * v / total)) for k, v in spent.items() if v >= 1)
        self.log(_("last {minutes} min: {kills} kills ({rate} an hour), {secs:.1f} s a kill - {parts}",
                   minutes=int(total / 60), kills=kills, rate=int(kills * 3600 / total),
                   secs=total / kills if kills else total, parts=text))
        # Contribution is what a night is judged by: the kill counter counts shared kills too
        # (since 2026-10-16), so it can rise while what we earn falls.
        was, got = t.get("_contribution", -1), self.contribution_now()
        if was >= 0 and got >= was:
            self.log(_("last {minutes} min: contribution +{n} ({rate} an hour)",
                       minutes=int(total / 60), n=got - was, rate=int((got - was) * 3600 / total)))
        self.timing = {"_since": now, "_kills": self.kills, "_contribution": got}

    def contribution_now(self) -> int:
        """The character sheet's contribution at its last reading, or -1 when there is none yet."""
        sheet: dict[str, Any] = getattr(getattr(self, "tally", None), "sheet", None) or {}
        return int(sheet.get("contribution", -1))

    def ui(self) -> dict[str, Any]:
        u = self._game.json("mem ui")
        return {} if "error" in u else u
