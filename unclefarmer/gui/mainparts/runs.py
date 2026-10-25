"""What to run (the Plans and scripts list) and running it: start, stop, pause, errands, follow."""


import wx

from ... import settings
from ...engine import plan as plans
from ...engine.runner import Run
from ...engine.script import check as check_script
from ...i18n import _
from ..common import error, save_file
from ..plan import plan_text
from .base import FrameParts, foreground_pid


class RunsMixin(FrameParts):
    # ------------------------------------------------------------ what to run
    def item_label(self, item):
        kind, name = item
        return name if kind == "plan" else _("{name} (script)", name=name)

    def fill_runs(self, select=None):
        self.items = [("plan", n) for n in plans.names()] + [("script", n) for n in plans.script_names()]
        labels = [self.item_label(it) for it in self.items]
        self.what.Set(labels or [_("No plans yet")])
        self.plan_list.Set(labels)
        want = select or (tuple(self.s.get("last_run", "").split(":", 1)) if self.s.get("last_run") else None)
        i = self.items.index(want) if want in self.items else 0
        self.what.SetSelection(i)
        if self.items:
            self.plan_list.SetSelection(self.items.index(select) if select in self.items else
                                        max(0, min(self.plan_list.GetSelection(), len(self.items) - 1)))
        self.show_summary()

    def selected_item(self):
        """The plan chosen to run on the Bot tab."""
        i = self.what.GetSelection()
        return self.items[i] if 0 <= i < len(self.items) else None

    def listed_item(self):
        """The plan or script selected on the Plans tab."""
        i = self.plan_list.GetSelection()
        return self.items[i] if 0 <= i < len(self.items) else None

    def show_summary(self):
        item = self.listed_item()
        if not item:
            self.summary.ChangeValue(_("No plans yet. Press New plan to make one."))
            return
        try:
            if item[0] == "plan":
                text = plan_text(plans.load(item[1]))
            else:
                with open(plans.script_path(item[1]), encoding="utf-8") as f:
                    text = f.read()
        except (OSError, ValueError) as e:
            text = _("Couldn't open it: {err}", err=e)
        self.summary.ChangeValue(text)

    def on_plan_key(self, evt):
        k = evt.GetKeyCode()
        if k in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.edit_selected()
        elif k == wx.WXK_DELETE:
            self.delete_selected()
        else:
            evt.Skip()

    def item_text(self, item):
        kind, name = item
        if kind == "plan":
            return plans.to_script(plans.load(name))
        with open(plans.script_path(name), encoding="utf-8") as f:
            return f.read()

    # ------------------------------------------------------------ running
    def update_buttons(self):
        """The buttons follow the selected game: another game can be started while one runs."""
        pid = self.selected_pid()
        on = pid in self.runs
        self.start_btn.Enable(pid is not None and not on and bool(self.items))
        self.stop_btn.Enable(on)
        self.sync_pause()
        self.what.Enable(not on and bool(self.items))
        if on:
            item = ("plan" if self.runs[pid].kind == "plan" else "script", self.runs[pid].title)
            if item in self.items:
                self.what.SetSelection(self.items.index(item))

    def is_paused(self, pid):
        run = self.runs.get(pid)
        return bool(run and run.bot is not None and run.bot.paused.is_set())

    def sync_pause(self):
        """The Pause button follows the selected game's run: Pause while it runs, Go on while paused
        (a private message can pause and resume it too)."""
        pid = self.selected_pid()
        run = self.runs.get(pid)
        label = _("Go &on") if self.is_paused(pid) else _("&Pause")
        if self.pause_btn.GetLabel() != label:
            self.pause_btn.SetLabel(label)
        self.pause_btn.Enable(bool(run and run.bot is not None))

    def toggle_pause(self, pid=None):
        """Pause the run where it is - keys let go, the run not ended - or let a paused one go on.
        While paused the game is the player's: their own Shift, Ctrl and Alt work in it again."""
        pid = self.selected_pid() if pid is None else pid
        run = self.runs.get(pid)
        if not run or run.bot is None:
            self.say(_("The bot isn't running."))
            return
        if run.bot.paused.is_set():
            run.bot.paused.clear()
            self.say(_("Going on"), run)
        else:
            run.bot.paused.set()
            self.say(_("Paused"), run)
        self.sync_pause()
        self.label_games()

    def toggle(self):
        """The Pause key: the game window in front if it is one of ours, else the selected game."""
        pid = foreground_pid()
        if pid not in {g[0] for g in self.games}:
            pid = self.selected_pid()
        if pid in self.runs:
            self.stop(pid)
        else:
            self.start(pid)

    def start(self, pid=None, item=None, remote=False):
        """Start a bot. remote: asked by private message - no message boxes; returns what to answer."""
        def fail(text):
            if remote:
                return text
            error(self, text)
        given = item
        item = item or self.selected_item()
        if pid is None:
            pid = self.selected_pid()
            if pid is None:
                self.refresh_games()
                pid = self.selected_pid()
        if pid is None:
            self.say(_("No game with the agent is running."))
            return
        if pid in self.runs:
            return _("Already running.")
        if pid != self.selected_pid() and not given:
            # started from inside a game window: run what that character ran last, if anything
            name = next((g[1] for g in self.games if g[0] == pid), None)
            last = self.s.get("last_runs", {}).get(name or "")
            if last and tuple(last.split(":", 1)) in self.items:
                item = tuple(last.split(":", 1))
        if not item:
            self.say(_("Make a plan first, in the Plans tab."))
            return _("There's no plan to run.")
        try:
            text = self.item_text(item)
        except (OSError, ValueError) as e:
            return fail(_("Couldn't open it: {err}", err=e))
        problem = check_script(text)
        if problem:
            return fail(problem)
        self.s["last_run"] = "%s:%s" % item
        name = next((g[1] for g in self.games if g[0] == pid), None)
        if name:
            self.s.setdefault("last_runs", {})[name] = "%s:%s" % item
        save_file(self, settings.save_app, self.s)      # a failed save must not stop the start
        run = Run(text, item[1], pid, self.on_log, self.on_done, clock=self.clock)
        run.locate = self.locate
        run.last_seen = self.last_seen
        run.kind = item[0]
        run.character = name        # for the log until the run reads it from the game itself
        self.runs[pid] = run
        self.last_line[pid] = _("Starting")
        self.label_games()
        self.update_buttons()
        run.start()
        self.poll()
        return _("Starting {what}.", what=item[1])

    def run_errand(self, text, title, walk=False):
        """A one-off job for the selected game, run like a script: a tool from the Tools menu, or
        the walking assistant (walk)."""
        pid = self.selected_pid()
        if pid is None:
            self.say(_("No game with the agent is running."))
            return
        if pid in self.runs:
            self.say(_("Stop the bot first."))
            return
        run = Run(text, title, pid, self.on_log, self.on_done, walk=walk, clock=self.clock)
        run.locate = self.locate
        run.last_seen = self.last_seen
        run.kind = "errand"
        run.character = self.selected_character()
        self.runs[pid] = run
        self.last_line[pid] = _("Starting")
        self.label_games()
        self.update_buttons()
        run.start()

    def errand_for(self, pid, text, title, tell=None, follows=None):
        """A job asked for by private message, run like the walking assistant (stays where it ends);
        tell(text) answers the player when it ends by itself."""
        if pid in self.runs:
            return _("Busy - send stop first.")
        run = Run(text, title, pid, self.on_log, self.on_done, walk=True, clock=self.clock)
        run.locate = self.locate
        run.last_seen = self.last_seen
        run.tell = tell
        run.kind = "errand"
        run.follows = follows
        run.character = self.character(pid)
        self.runs[pid] = run
        self.last_line[pid] = _("Starting")
        self.label_games()
        self.update_buttons()
        run.start()
        return None

    def follow_for(self, pid, leader, tell=None):
        """'follow' from a trusted player: this game's character follows them. A run already going
        is stopped first, and following starts once it has ended. Returns what to answer."""
        run = self.runs.get(pid)
        if run and getattr(run, "follows", None) == leader.lower():
            return _("Already following you.")
        if run:
            run.stop()
            self._when_stopped(pid, lambda: self._start_follow(pid, leader, tell))
            return _("Stopping {what}, then following you.", what=run.title)
        return self._start_follow(pid, leader, tell)

    def _when_stopped(self, pid, then, tries=0):
        if self.destroyed:
            return
        if pid in self.runs and tries < 240:          # a run can take a while to end safely
            wx.CallLater(500, self._when_stopped, pid, then, tries + 1)
        elif pid not in self.runs:
            then()
        else:
            self.say(_("The bot didn't stop in time, so it isn't following you."))

    def _start_follow(self, pid, leader, tell=None):
        # an errand that stays where it ends: no daily stop, no results, no end place
        busy = self.errand_for(pid, 'follow "%s"' % leader, _("following {name}", name=leader), tell,
                               follows=leader.lower())
        return busy or _("Following you.")

    def unfollow(self, pid):
        run = self.runs.get(pid)
        if not run or not getattr(run, "follows", None):
            return _("I'm not following anyone.")
        run.stop()
        return _("Stopped following.")

    def stop(self, pid=None):
        run = self.runs.get(self.selected_pid() if pid is None else pid)
        if run:
            self.say(_("Stopping"), run)
            run.stop()

    def stop_all(self):
        if not self.runs:
            self.say(_("No bot is running."))
            return
        self.say(_("Stopping every bot"))
        for run in list(self.runs.values()):
            run.stop()

    def tagged(self):
        """With more than one game, every log line says which character it is about."""
        return len(self.games) > 1 or len(self.runs) > 1
