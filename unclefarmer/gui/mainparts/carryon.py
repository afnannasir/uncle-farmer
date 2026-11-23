"""Carrying on the runs that didn't end (engine/resume.py): offered once Uncle Farmer has started,
then each one started in its character's game - a game opened and logged in to when none is, as a
schedule does - and given CARRY_MOST seconds to get going before it is given up."""

from __future__ import annotations

from typing import Any

import wx

from ... import gameinstall, settings
from ...engine import resume
from ...engine.runner import Run
from ...i18n import _
from .. import agentfix
from ..carryon import CarryOnDialog
from .base import FrameParts, Item

CARRY_TICK_MS = 10_000     # how often the runs waiting to carry on are tried again
CARRY_MOST = 300.0         # seconds one may wait for its game before it is given up
OFFER_AFTER_MS = 2_000     # the window settles (its game list read) before the offer
Waiting = tuple[str, dict[str, Any], Item, float]    # (character, saved place, plan or script, since)


class CarryOnMixin(FrameParts):
    carry_queue: list[Waiting]
    carry_timer: wx.Timer | None = None

    def start_carry_on(self) -> None:
        self.carry_queue = []
        wx.CallLater(OFFER_AFTER_MS, self.offer_carry_on)

    def carry_on_rows(self) -> list[tuple[str, dict[str, Any], Item]]:
        """Today's unfinished runs whose plan or script is still there (the others are forgotten)."""
        out: list[tuple[str, dict[str, Any], Item]] = []
        for key, row in resume.pending():
            item = resume.item_of(row)
            if item is None or item not in self.items:
                resume.clear(key)
                continue
            out.append((key, row, item))
        return out

    def offer_carry_on(self) -> None:
        if self.destroyed:
            return
        if self.updating or not self.IsEnabled():
            wx.CallLater(OFFER_AFTER_MS, self.offer_carry_on)
            return
        rows = self.carry_on_rows()
        if not rows:
            return
        lines = [_("{what} for {name}", what=self.item_label(item), name=settings.profile_display(key))
                 for key, _row, item in rows]
        if not CarryOnDialog.carry_on(self, lines, resume.COUNTDOWN):
            for key, _row, _item in rows:
                resume.clear(key)
            self.say(_("The interrupted runs won't carry on."))
            return
        self.carry_on(rows)

    def carry_on(self, rows: list[tuple[str, dict[str, Any], Item]]) -> None:
        """Start these runs where they were, as soon as each one's game is there."""
        now = self.clock.time()
        self.carry_queue += [(key, row, item, now) for key, row, item in rows]
        if self.carry_timer is None:
            self.carry_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, lambda e: self.carry_tick(), self.carry_timer)
        if not self.carry_timer.IsRunning():
            self.carry_timer.Start(CARRY_TICK_MS)
        self.carry_tick()

    def carry_tick(self) -> None:
        if self.destroyed or getattr(self, "updating", False):
            return
        now = self.clock.time()
        left: list[Waiting] = []
        for key, row, item, since in self.carry_queue:
            why = self.carry_one(key, row, item)
            if why is None:
                continue
            if now - since >= CARRY_MOST:
                self.say(_("Couldn't carry on {what} for {name}: {why}", what=self.item_label(item),
                           name=settings.profile_display(key), why=why))
                resume.clear(key)
                continue
            left.append((key, row, item, since))
        self.carry_queue = left
        if not left and self.carry_timer is not None:
            self.carry_timer.Stop()

    def carry_one(self, key: str, row: dict[str, Any], item: Item) -> str | None:
        """Start one where it was: None once it runs, else why it still waits."""
        pid, waiting = self.automatic_login_window(key)
        if pid is None:
            return waiting or _("{name} isn't logged in to one available game", name=settings.profile_display(key))
        busy = self.runs.get(pid)
        if busy is not None:
            if busy.title == item[1] and settings.same_character(busy.profile or "", key):
                return None              # already going again (started by hand)
            return _("its game is busy with {what}", what=busy.title)
        answer = self.start(pid, item, remote=True, profile=key, resume_row=row)
        run = self.runs.get(pid)
        if run is None or run.title != item[1]:
            return answer or _("it didn't start")
        return None

    # ------------------------------------------------------------ a game lost mid-run
    def revive(self, run: Run) -> None:
        """A plan or script that was going lost its game (it closed, froze, or logged our character
        out for good): the game is closed if it is still there, opened again, logged in to, and the
        run carries on where it was - at most resume.RESTARTS_MOST times a game day per character,
        and only with the setting on. Never after Stop or when Uncle Farmer is closing."""
        if (self.destroyed or not getattr(run, "game_lost", False) or run.app_closing or not run.keeps_place()):
            return
        # A run can report the lost connection before the intentional kill worker calls Stop.
        if run.stop_event.is_set() or run.pid in self.killing_games:
            resume.clear(run.profile)
            return
        if not self.s.get("restart_lost_games", True):
            return
        key = run.profile
        item: Item = (run.kind, run.title)
        if not key or item not in self.items or (run.script is None and run.resume_row is None):
            return                # it never got going: nothing to carry on
        name = settings.profile_display(key)
        if not any(settings.same_character(known, key) for known, _email in settings.automatic_login_profiles()):
            self.say(_("{name}'s game was lost, and the bot can't log in to it by itself: log in to it once "
                       "by hand so its account is known.", name=name))
            return
        if resume.restarts_today(key) >= resume.RESTARTS_MOST:
            self.say(_("{name}'s game was lost again. It was opened again {n} times today already, so the "
                       "run ends here.", name=name, n=resume.RESTARTS_MOST))
            resume.clear(key)
            return
        n = resume.note_restart(key)
        row = resume.get(key) or {"item": "%s:%s" % item, "text": resume.fingerprint(run.text)}
        self.say(_("{name}'s game was lost: opening it again and carrying on where the run was "
                   "({n} of {most} today).", name=name, n=n, most=resume.RESTARTS_MOST))
        self._reopen(run, key, row, item)

    def back_on_line(self, run: Run, key: str) -> bool:
        """A run lost through the game's connection whose game has that character in its world
        again (logging back in worked as the run was ending). A frozen or closed game is opened
        again whatever it shows."""
        if not getattr(run, "lost_connection", False):
            return False
        self.refresh_games()
        return any(pid == run.pid and name and settings.is_profile_of(name, key)
                   for pid, name, _where in self.games)

    def _reopen(self, run: Run, key: str, row: dict[str, Any], item: Item) -> None:
        """The lost game closed first when it is still running (frozen, or showing a world it lost),
        then the run is carried on: its character's game is opened and logged in to."""
        def then(_left: list[int]) -> None:
            if not self.destroyed:
                self.refresh_games()
                self.carry_on([(key, row, item)])
        if self.back_on_line(run, key):
            # its connection came back while the run was ending (a few minutes without internet):
            # closing it now would only throw that login away, and land the character among the
            # monsters again a minute later
            self.say(_("{name}'s game is back on line by itself: carrying on in it",
                       name=settings.profile_display(key)))
            then([])
            return
        if run.pid in {pid for pid, _folder in gameinstall.running_games()}:
            agentfix.close_then([run.pid], then, force=True)
        else:
            then([])
