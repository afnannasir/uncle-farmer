"""Run, Character chain, on the main window: several characters farm one after the other in one game
(engine/chain.py has the rows, the day's marks and what a run's end means).

One chain at a time, on the game selected when it starts. A step: the next row (chain.next_row); the
game's character logged out first when it isn't that row's (the setting menu's Switch account, on a
bot of its own connection, as `wait` does with no run going); then the row's run, which logs the
row's character in by itself (the saved account, as a schedule does). A run's end (_done) tells the
chain what comes next. Stop, or Stop every bot, ends the chain too, between two characters as well.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

import wx

from ... import settings
from ...engine import autopilot, chain, logout
from ...i18n import _
from ...link import AgentError, AgentLink
from ..autopilot import plan_text
from .base import FrameParts, Item

if TYPE_CHECKING:
    from ...engine.runner import Run

STEP_MS = 3000                 # between one character's end and the next step
TICK_MS = 10000                # the chain's watch: a lost game opened again, a step that never came
WAIT_MOST = 6 * 60.0           # seconds a lost game's carried-on run may take to show up


class ChainMixin(FrameParts):
    chain_state: chain.State | None = None
    chain_timer: wx.Timer | None = None

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
        self.chain_state = chain.State(pid, rows, end)
        if self.chain_timer is None:
            self.chain_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, lambda e: self.chain_tick(), self.chain_timer)
        self.chain_timer.Start(TICK_MS)
        self.say(_("Character chain: {n} characters, one after the other", n=len(rows)))
        wx.CallAfter(self.chain_step)
        return _("Starting the character chain.")

    def cancel_chain(self, why: str | None = None) -> bool:
        """End the chain (the run going, if any, is stopped by its own Stop). True if one was going."""
        if self.chain_state is None:
            return False
        self.chain_state = None
        if self.chain_timer is not None:
            self.chain_timer.Stop()
        self.say(why or _("The character chain stopped."))
        return True

    def chain_next(self, pid: int) -> str | None:
        """`next` by private message: this character is done, the next one comes. None when no
        chain runs on that game, else the answer."""
        st = self.chain_state
        if st is None or st.pid != pid or st.phase != "run" or pid not in self.runs:
            return None
        st.advance = True
        self.runs[pid].stop()
        return _("Moving on to the next character of the chain.")

    # ---------------------------------------------------------------- one step
    def chain_step(self) -> None:
        st = self.chain_state
        if st is None or self.destroyed:
            return
        row = chain.next_row(st.rows, chain.done_today(), st.failed)
        if row is None:
            self.chain_over()
            return
        self.refresh_games()
        game = next(((pid, name) for pid, name, _where in self.games if pid == st.pid), None)
        if game is None:
            self.cancel_chain(_("The character chain stopped: its game isn't running any more."))
            return
        st.current = row
        if game[1] and not settings.is_profile_of(game[1], row.profile):
            st.phase = "logout"
            self.say(_("Character chain: logging {old} out for {new}", old=settings.profile_display(game[1]),
                       new=settings.profile_display(row.profile)))
            self.chain_log_out(st.pid, game[1])
            return
        item = self.chain_item(row)
        if item is None:
            st.failed[row.profile] = chain.TRIES
            self.say(_("Character chain: {name} has nothing to run (its plan or its Autopilot day isn't there) - "
                       "left out today", name=settings.profile_display(row.profile)))
            wx.CallLater(STEP_MS, self.chain_step)
            return
        st.phase = "run"
        answer = self.start(st.pid, item, remote=True, profile=row.profile)
        run = self.runs.get(st.pid)
        if run is None:
            st.failed[row.profile] = st.failed.get(row.profile, 0) + 1
            self.say(_("Character chain: {name} didn't start: {why}", name=settings.profile_display(row.profile),
                       why=answer))
            wx.CallLater(STEP_MS, self.chain_step)
            return
        run.in_chain = True
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
        day = autopilot.item_for(p, settings.profile_display(row.profile),
                                 plan_text(("plan", name)) if kind == "plan" and name else None)
        if day is None:
            return None
        self.fill_runs()                 # a day script written just now
        return day if day in self.items else None

    def chain_log_out(self, pid: int, key: str) -> None:
        """The game's character out to the login screen, on a bot of its own connection."""
        def work() -> None:
            why: str | None
            try:
                from ...engine.bot import Bot
                with AgentLink(pid=pid, timeout=5) as link:
                    if link.send("arm") != "armed":
                        why = _("the game wouldn't take orders")
                    else:
                        b = Bot(link, settings.load_profile(key), self.chain_log, threading.Event(), clock=self.clock)
                        b.profile_key = key
                        why = logout.log_out(b)
            except (AgentError, OSError, ValueError) as e:
                why = str(e)
            wx.CallAfter(self.chain_logged_out, why)
        threading.Thread(target=work, name="chain-logout", daemon=True).start()

    def chain_log(self, line: str, speak: bool = False) -> None:
        wx.CallAfter(self.say, line)

    def chain_logged_out(self, why: str | None) -> None:
        st = self.chain_state
        if st is None or self.destroyed:
            return
        if why is not None:
            self.cancel_chain(_("The character chain stopped: logging out didn't work ({why})", why=why))
            return
        st.phase = ""
        wx.CallLater(STEP_MS, self.chain_step)

    # ---------------------------------------------------------------- a run's end
    def chain_run_ended(self, run: Run) -> None:
        """From _done: the run of the chain's current row ended (or its carried-on run did)."""
        st = self.chain_state
        if st is None or st.current is None or st.phase != "run" or not getattr(run, "in_chain", False):
            return
        if not settings.same_character(run.profile or "", st.current.profile):
            return
        st.pid = run.pid
        what = chain.after_run(st, run.stop_event.is_set(), run.ended_by_itself, run.game_lost, run.drop_spare)
        if what == "stop":
            self.cancel_chain()
        elif what == "wait":
            st.waiting = self.clock.time()
            self.say(_("Character chain: waiting for {name}'s game to come back",
                       name=settings.profile_display(st.current.profile)))
        else:
            st.phase = ""
            wx.CallLater(STEP_MS, self.chain_step)

    def chain_tick(self) -> None:
        """A lost game's carried-on run: it becomes the chain's row again; one that never comes
        leaves the row for this chain."""
        st = self.chain_state
        if st is None or not st.waiting or st.current is None:
            return
        for pid, run in self.runs.items():
            if settings.same_character(run.profile or "", st.current.profile):
                run.in_chain = True
                st.pid, st.waiting = pid, 0.0
                return
        if self.clock.time() - st.waiting > WAIT_MOST:
            st.waiting = 0.0
            st.failed[st.current.profile] = st.failed.get(st.current.profile, 0) + 1
            st.phase = ""
            self.chain_step()

    def chain_over(self) -> None:
        """Every row done today: stay logged in, or close the game, as the chain says."""
        st = self.chain_state
        if st is None:
            return
        done = chain.done_today()
        left = [settings.profile_display(r.profile) for r in st.rows if r.profile not in done]
        self.cancel_chain(_("The character chain is over: every character has farmed today.") if not left else
                          _("The character chain is over. These characters couldn't farm today: {names}",
                            names=", ".join(left)))
        if st.end != "close":
            return
        try:
            with AgentLink(pid=st.pid, timeout=5) as link:
                link.send("arm")
                link.send("closegame")
            self.say(_("Character chain: the game is closed"))
        except (AgentError, OSError) as e:
            self.say(_("Character chain: couldn't close the game ({err})", err=e))

    def chain_summary(self) -> list[dict[str, Any]]:
        """For the window: each row with whether it is done today."""
        rows, _end = chain.load()
        done = chain.done_today()
        return [{"row": r, "done": r.profile in done} for r in rows]
