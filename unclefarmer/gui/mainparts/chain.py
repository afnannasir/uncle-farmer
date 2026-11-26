"""Run, Character chain, on the main window: several characters farm one after the other in one game
(engine/chain.py has the rows, the day's marks and what a run's end means).

One chain at a time, on the game selected when it starts. A step: the next row (chain.next_row); the
game's character logged out first when it isn't that row's (the setting menu's Switch account, on a
bot of its own connection, as `wait` does with no run going); then the row's run, which logs the
row's character in by itself (the saved account, as a schedule does). A run's end (_done) tells the
chain what comes next. Stop, or Stop every bot, ends the chain too, between two characters as well.

The chain's game is the chain's: no other run starts in it while the chain goes (Start, a schedule,
a carry-on, `start` by private message), but the carried-on run of its own row after a lost game,
which becomes the row's again. Every delayed step belongs to one chain: a chain stopped and started
again isn't driven by the old one's steps. A watch every TICK_MS moves on from a step that never
came (a logout that never answered, a run that never started).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import wx

from ... import daily, settings
from ...engine import autopilot, chain, logout
from ...i18n import _
from ...link import AgentError, AgentLink
from ..autopilot import plan_text
from .base import FrameParts, Item

if TYPE_CHECKING:
    from ...engine.runner import Run

STEP_MS = 3000                 # between one character's end and the next step
TICK_MS = 10000                # the chain's watch
WAIT_MOST = 12 * 60.0          # seconds a lost game's carried-on run may take to show up
LOGOUT_MOST = 90.0             # seconds a logout may take before the chain gives up
START_MOST = 60.0              # seconds a step may go without the row's run showing up
NOWORLD_WAIT = 8.0             # seconds, after Switch account, for the game's connection to close
MISSING_MOST = 3               # game list readings without the chain's game before it is given up


class ChainMixin(FrameParts):
    chain_state: chain.State | None = None
    chain_timer: wx.Timer | None = None
    chain_starting: bool = False   # the chain's own start() call: its game may be used

    # ---------------------------------------------------------------- the window
    def open_chain(self) -> None:
        """Run, Character chain: its rows and end; Start the chain starts it on the selected game."""
        from ...engine import plan as plans
        from ..chain import ChainDialog
        characters = [key for key, _email in settings.automatic_login_profiles()]
        with ChainDialog(self, characters, plans.names()) as d:
            answer = d.ShowModal()
        if answer == wx.ID_YES:
            self.say(self.start_chain())

    # ---------------------------------------------------------------- the chain's game
    def chain_owns(self, pid: int | None, profile: str | None = None) -> bool:
        """The game is a going chain's, and this start isn't the chain's own (nor the carried-on run
        of its row): another run mustn't take it."""
        st = self.chain_state
        if st is None or pid is None or pid != st.pid or self.chain_starting:
            return False
        own_row = st.waiting and st.current is not None and profile is not None \
            and settings.same_character(profile, st.current.profile)
        return not own_row

    def chain_adopt(self, run: Run) -> None:
        """A run just started (RunsMixin.start): the chain's own, or its row's carried-on run after a
        lost game (possibly in another game, opened again) - it becomes the row's run."""
        st = self.chain_state
        if st is None or st.current is None:
            return
        if self.chain_starting or (st.waiting and settings.same_character(run.profile or "", st.current.profile)):
            run.in_chain = True
            st.pid, st.waiting = run.pid, 0.0
            st.phase, st.since = "run", self.clock.time()

    # ---------------------------------------------------------------- starting and stopping
    def start_chain(self, pid: int | None = None) -> str:
        """Start the saved chain on the selected game (pid). What was done, as one line."""
        rows, end = chain.load()
        if not rows:
            return _("The character chain is empty: add characters in Run, Character chain first.")
        if self.chain_state is not None:
            return _("A character chain is already going.")
        pid = self.selected_pid() if pid is None else pid
        if pid is None:
            return _("No game with the agent is running.")
        if pid in self.runs:
            return _("Stop the bot first.")
        st = self.chain_state = chain.State(pid, rows, end)
        st.since = self.clock.time()
        if self.chain_timer is None:
            self.chain_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, lambda e: self.chain_tick(), self.chain_timer)
        self.chain_timer.Start(TICK_MS)
        self.say(_("Character chain: {n} characters, one after the other", n=len(rows)))
        self.chain_later(st, 0)
        return _("Starting the character chain.")

    def chain_later(self, st: chain.State, ms: int = STEP_MS) -> None:
        """The next step of this chain, later - not of another one started since."""
        def step() -> None:
            if self.chain_state is st:
                self.chain_step()
        if ms:
            wx.CallLater(ms, step)
        else:
            wx.CallAfter(step)

    def cancel_chain(self, why: str | None = None) -> bool:
        """End the chain (the run going, if any, is stopped by its own Stop). True if one was going."""
        if self.chain_state is None:
            return False
        self.chain_state = None
        if self.chain_timer is not None:
            self.chain_timer.Stop()
        self.say(why or _("The character chain stopped."))
        return True

    def chain_active(self, pid: int) -> bool:
        st = self.chain_state
        return st is not None and st.pid == pid

    def chain_next(self, pid: int) -> str | None:
        """`next` by private message: this character is done, the next one comes. None when no
        chain runs on that game, else the answer."""
        st = self.chain_state
        run = self.runs.get(pid)
        if st is None or st.pid != pid or st.phase != "run" or run is None or not run.in_chain:
            return None
        st.advance = True
        run.stop()
        return _("Moving on to the next character of the chain.")

    # ---------------------------------------------------------------- one step
    def chain_step(self) -> None:
        st = self.chain_state
        if st is None or self.destroyed or st.phase == "logout":
            return
        _end, blocked = daily.plan(settings.load_app())
        if blocked:
            self.chain_over(_("The character chain is over: the day's farming is over (the drop time resets at "
                              "{reset}).", reset=blocked))
            return
        st.advance = False
        row = chain.next_row(st.rows, chain.done_today(), st.failed)
        if row is None:
            self.chain_over()
            return
        self.refresh_games()
        game = next(((pid, name) for pid, name, _where in self.games if pid == st.pid), None)
        if game is None:
            st.missing += 1          # a slow answer right after a login or logout: read again first
            if st.missing >= MISSING_MOST:
                self.cancel_chain(_("The character chain stopped: its game isn't running any more."))
            else:
                self.chain_later(st)
            return
        st.missing = 0
        if st.pid in self.runs:
            self.cancel_chain(_("The character chain stopped: another bot is running in its game."))
            return
        st.current, st.since = row, self.clock.time()
        if game[1] and not settings.is_profile_of(game[1], row.profile):
            st.phase = "logout"
            self.say(_("Character chain: logging {old} out for {new}", old=settings.profile_display(game[1]),
                       new=settings.profile_display(row.profile)))
            self.chain_log_out(st, game[1])
            return
        item = self.chain_item(row)
        if item is None:
            st.failed[row.profile] = chain.TRIES
            self.say(_("Character chain: {name} has nothing to run (its plan or its Autopilot day isn't there) - "
                       "left out today", name=settings.profile_display(row.profile)))
            self.chain_later(st)
            return
        st.phase = "run"
        self.chain_starting = True
        try:
            answer = self.start(st.pid, item, remote=True, profile=row.profile)
        finally:
            self.chain_starting = False
        run = self.runs.get(st.pid)
        if run is None or not run.in_chain:
            st.failed[row.profile] = st.failed.get(row.profile, 0) + 1
            st.phase = ""
            self.say(_("Character chain: {name} didn't start: {why}", name=settings.profile_display(row.profile),
                       why=answer))
            self.chain_later(st)
            return
        self.say(_("Character chain: {name}, {what}", name=settings.profile_display(row.profile),
                   what=self.item_label(item)))

    def chain_item(self, row: chain.Row) -> Item | None:
        """What a row runs: its plan, or its character's Autopilot day (as Run, Autopilot starts it)."""
        if row.do == "plan":
            item: Item = ("plan", row.plan)
            return item if item in self.items else None
        p = settings.load_profile(row.profile)
        chosen = str(p.get("autopilot_plan") or "")
        kind, _sep, name = chosen.partition(":")
        try:
            day = autopilot.item_for(p, settings.profile_display(row.profile),
                                     plan_text(("plan", name)) if kind == "plan" and name else None)
        except OSError:                  # the day's script couldn't be written
            return None
        if day is None:
            return None
        self.fill_runs()                 # a day script written just now
        return day if day in self.items else None

    def chain_log_out(self, st: chain.State, key: str) -> None:
        """The game's character out to the login screen, on a bot of its own connection; the
        chain hears back in any case (chain_logged_out)."""
        pid = st.pid

        def work() -> None:
            why: str | None = None
            try:
                from ...engine.bot import Bot
                with AgentLink(pid=pid, timeout=5) as link:
                    if link.send("arm") != "armed":
                        why = _("the game wouldn't take orders")
                    else:
                        b = Bot(link, settings.load_profile(key), self.chain_log, threading.Event(), clock=self.clock)
                        b.profile_key = key
                        why = logout.log_out(b)
                        if why is None:
                            gone_world(link, self.clock.time, self.clock.sleep)
            except Exception as e:      # Stopped, a game lost or frozen meanwhile: said, never lost
                why = str(e) or e.__class__.__name__
            finally:
                wx.CallAfter(self.chain_logged_out, st, why)
        threading.Thread(target=work, name="chain-logout", daemon=True).start()

    def chain_log(self, line: str, speak: bool = False) -> None:
        wx.CallAfter(self.say, line)

    def chain_logged_out(self, st: chain.State, why: str | None) -> None:
        if self.chain_state is not st or self.destroyed:
            return
        st.phase = ""
        if why is not None:
            self.cancel_chain(_("The character chain stopped: logging out didn't work ({why})", why=why))
            return
        self.chain_later(st)

    # ---------------------------------------------------------------- a run's end
    def chain_run_ended(self, run: Run) -> None:
        """From _done: the run of the chain's current row ended (or its carried-on run did)."""
        st = self.chain_state
        if st is None or st.current is None or not run.in_chain:
            return
        st.pid = run.pid
        what = chain.after_run(st, run.stop_event.is_set(), run.ended_by_itself, run.game_lost, run.drop_spare,
                               run.end_kind)
        if what == "wait" and not self.chain_revived(st.current.profile):
            # revive won't open it again (turned off, three times today, an account it doesn't know)
            st.failed[st.current.profile] = st.failed.get(st.current.profile, 0) + 1
            what = "next"
        st.phase = ""
        if what == "stop":
            self.cancel_chain()
        elif what == "over":
            self.chain_over(_("The character chain is over: the day's farming is over."))
        elif what == "wait":
            st.waiting = self.clock.time()
            self.say(_("Character chain: waiting for {name}'s game to come back",
                       name=settings.profile_display(st.current.profile)))
        else:
            self.chain_later(st)

    def chain_revived(self, key: str) -> bool:
        """revive (CarryOnMixin) is opening this character's game again, or carrying its run on."""

        queue: list[Any] = list(getattr(self, "carry_queue", []) or [])
        return (any(settings.same_character(str(w[0]), key) for w in queue)
                or any(settings.same_character(k, key) for k in getattr(self, "carry_closing", set[str]()) or ()))

    def chain_tick(self) -> None:
        """The chain's watch: a step that never came is moved on from."""
        st = self.chain_state
        if st is None or self.destroyed:
            return
        now = self.clock.time()
        if st.waiting:
            if now - st.waiting > WAIT_MOST or (st.current is not None and not self.chain_revived(st.current.profile)
                                                 and now - st.waiting > 60):
                st.waiting = 0.0
                if st.current is not None:
                    st.failed[st.current.profile] = st.failed.get(st.current.profile, 0) + 1
                st.phase = ""
                self.chain_step()
            return
        if st.phase == "logout" and now - st.since > LOGOUT_MOST:
            self.cancel_chain(_("The character chain stopped: logging out didn't answer"))
        elif st.phase == "run" and st.pid not in self.runs and now - st.since > START_MOST:
            st.phase = ""                # the row's run never showed up, or its end was never heard
            if st.current is not None:
                st.failed[st.current.profile] = st.failed.get(st.current.profile, 0) + 1
            self.chain_step()

    def chain_over(self, why: str | None = None) -> None:
        """Every row done today (or the day's farming over): stay logged in, or close the game."""
        st = self.chain_state
        if st is None:
            return
        done = chain.done_today()
        left = [settings.profile_display(r.profile) for r in st.rows if r.profile not in done]
        self.cancel_chain(why or (_("The character chain is over: every character has farmed today.") if not left else
                                  _("The character chain is over. These characters couldn't farm today: {names}",
                                    names=", ".join(left))))
        if st.end != "close":
            return
        if st.pid in self.runs:          # a bot the player started meanwhile: the game is theirs
            self.say(_("Character chain: the game stays open, a bot is running in it"))
            return
        try:
            with AgentLink(pid=st.pid, timeout=5) as link:
                link.send("arm")
                link.send("closegame")
            self.say(_("Character chain: the game is closed"))
        except (AgentError, OSError) as e:
            self.say(_("Character chain: couldn't close the game ({err})", err=e))


def gone_world(link: AgentLink, now: Callable[[], float], sleep: Callable[[float], None]) -> None:
    """After Switch account: until the game's connection has closed ("noworld", about 5 s after the
    character left), so the next login starts on a closed connection; NOWORLD_WAIT at most."""
    end = now() + NOWORLD_WAIT
    while now() < end:
        try:
            got = link.json("mem state")
        except (AgentError, OSError, ValueError):
            return
        if str(got.get("error", "")).startswith("noworld"):
            return
        sleep(0.5)
