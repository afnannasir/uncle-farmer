"""Scheduled plans, scripts, tools and trigger sequences: one shared timer and run lifecycle.

A scheduled plan or script is started by start(), the Start button's own path, in the game its
character is logged in to; schedule.Scheduler decides when, and keeps it from running twice.
"""

from __future__ import annotations

import datetime as dt

import wx

from ... import applog, schedule, settings, stats
from ...errors import carry_on
from ...engine import plan as plans, triggers
from ...engine.runner import Run
from ...i18n import _
from ..schedules import SchedulesDialog
from .base import FrameParts, profile_of

TICK_MS = 20_000          # how often the schedules are looked at


class SchedulingMixin(FrameParts):
    scheduler: schedule.Scheduler
    schedule_timer: wx.Timer

    def start_schedules(self) -> None:
        self.scheduler = schedule.Scheduler()
        self.schedule_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self.schedule_tick(), self.schedule_timer)
        self.schedule_timer.Start(TICK_MS)
        applog.bootup.note("schedules: %d, looked at every %d s" % (len(self.scheduler.items), TICK_MS // 1000))
        wx.CallAfter(self.schedule_tick)     # times missed while the app was closed are written down now

    @staticmethod
    def now() -> dt.datetime:
        return dt.datetime.now()

    def schedule_tick(self) -> None:
        if self.destroyed or getattr(self, "updating", False):
            return
        try:
            told = self.scheduler.tick(self.now(), self.start_scheduled)
        except OSError as e:          # the file couldn't be written: say so, try again next time
            self.say(_("Couldn't save the schedules: {err}", err=e))
            return
        for _s, line in told:
            self.say(line)

    def scheduled_items(self) -> list[tuple[str, str]]:
        return ([("plan", n) for n in plans.names()] + [("script", n) for n in plans.script_names()]
                + [("tool", job) for job, _label in triggers.TOOLS]
                + [("trigger", stage) for stage in triggers.STAGES])

    def start_scheduled(self, s: schedule.Schedule, due: dt.datetime) -> tuple[str, str]:
        """Start one due occurrence: (schedule.STARTED | WAIT | FAILED, what happened)."""
        item = schedule.item_of(s)
        if item not in self.scheduled_items():
            return schedule.FAILED, _("the {kind} {name} doesn't exist any more",
                                      kind=_("plan") if item[0] == "plan" else _("script"), name=item[1])
        who = s["character"].strip().casefold()
        candidates = [profile for profile, _email in settings.automatic_login_profiles()
                      if who in (profile.casefold(), settings.profile_display(profile).casefold(),
                                 settings.profile_name(profile).casefold())]
        target = candidates[0] if len(candidates) == 1 else None
        if target:
            pid, waiting = self.automatic_login_window(target)
            if pid is None:
                return schedule.WAIT, waiting
        else:                                    # older schedules stored a plain character name
            pid = next((g[0] for g in self.games if settings.profile_name(g[1]).casefold() == who), None)
        if pid is None:
            return schedule.WAIT, _("{who} isn't logged in to one available game", who=s["character"])
        busy = self.runs.get(pid)
        if busy is not None:
            return schedule.WAIT, _("{who}'s game is busy with {what}", who=s["character"], what=busy.title)
        answer = self.start(pid, item, remote=True, profile=target,
                            schedule_mark=(s["id"], schedule.stamp(due))) or ""
        run = self.runs.get(pid)
        if run is None or run.title != item[1]:
            return schedule.FAILED, answer or _("it didn't start")
        run.schedule = (s["id"], schedule.stamp(due))
        return schedule.STARTED, _("in {who}'s game", who=s["character"])

    def schedule_ended(self, run: Run, reason: str) -> None:
        """A scheduled run has ended: its history says how."""
        mark: tuple[str, str] | None = getattr(run, "schedule", None)
        if mark is None:
            return
        if getattr(run, "failed", False) or reason.startswith(_("unexpected error: {err}", err="").rstrip()):
            outcome = schedule.FAILED
        elif run.ended_by_itself:
            outcome = "finished"
        else:
            outcome = "stopped"
        tally = getattr(run, "tally", None)
        detail = reason
        if tally is not None:
            try:
                detail += "; " + stats.short(tally.result(), tally.drop_left)
            except Exception as e:    # the results are a convenience here; the end is still written down
                carry_on(e, "the results of a scheduled run")
        try:
            self.scheduler.ended(mark[0], mark[1], outcome, detail, self.now())
        except OSError as e:
            self.say(_("Couldn't save the schedules: {err}", err=e))

    def open_autopilot(self) -> None:
        """Run, Autopilot: the logged-in character's day (Before farming, a plan, After farming),
        saved, and started on its game when the player says so."""
        from ..autopilot import AutopilotDialog, sync_schedule
        from ..common import error
        from .editing import active_profile
        name = active_profile(self)
        pid = self.selected_pid()
        if not name or pid is None:
            error(self, _("Log in to the character in the game first."))
            return
        with AutopilotDialog(self, name, list(self.items), settings.last_run_of(self.s, name)) as d:
            code = d.ShowModal()
            item, p = d.item, d.p
        if code not in (wx.ID_OK, wx.ID_YES):
            return
        # "every day at": the window's own daily schedule, made, changed or taken away to match
        try:
            sid = sync_schedule(self.scheduler, p, name, item, self.now())
        except ValueError as e:
            error(self, str(e))
            sid = str(p.get("autopilot_schedule") or "")
        if sid != str(p.get("autopilot_schedule") or ""):
            p["autopilot_schedule"] = sid
            settings.save_profile(name, p)
        self.fill_runs()                 # a script the window wrote shows in the Plans tab
        if code == wx.ID_YES and item is not None:
            if not settings.is_profile_of(profile_of(self.snapshot(pid)), name):
                # the day set up is this character's: never started on whoever came in meanwhile
                self.say(_("{name} isn't logged in on that game any more - nothing was started.",
                           name=settings.profile_display(name)))
                return
            self.start(pid, item)

    def open_schedules(self) -> None:
        names = sorted({profile for profile, _email in settings.automatic_login_profiles()} |
                       {g[1] for g in self.games if g[1]} | set(stats.characters()), key=settings.profile_display)
        with SchedulesDialog(self, self.scheduler, self.scheduled_items(), self.item_label, names, self.now) as d:
            d.ShowModal()
