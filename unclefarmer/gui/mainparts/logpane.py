"""The Log tab, what the runs say, and a run that ends."""

from __future__ import annotations

import subprocess

import wx

from ... import speech
from ...engine.botparts.parts import Where
from ...engine.runner import Run
from ...i18n import _
from ..common import ask, copy_text, error, modal
from .base import LOG_LIMIT, FrameParts


class LogMixin(FrameParts):
    # ------------------------------------------------------------ log
    def on_log(self, line: str, speak: bool = False, run: Run | None = None) -> None:
        wx.CallAfter(self._append, line, speak, run)

    def _append(self, line: str, speak: bool, run: Run | None = None) -> None:
        if self.destroyed:
            return
        if run is not None:
            self.last_line[run.pid] = line.split("  ", 1)[-1]
            if run.pid == self.selected_pid():
                self.fields["activity"].ChangeValue(self.last_line[run.pid])
        if run is not None and self.tagged():
            who = run.character or _("game {pid}", pid=run.pid)
            stamp, _sep, text = line.partition("  ")
            line = "%s  %s: %s" % (stamp, who, text) if text else "%s: %s" % (who, line)
        self.log_lines.append((line, bool(speak)))
        if len(self.log_lines) > LOG_LIMIT:
            del self.log_lines[:500]
            self.show_log()
        elif speak or self.log_filter.GetSelection() == 0:
            self.log_box.AppendText(line + "\n")
        if speak and self.s.get("speak", True):
            speech.speak(line.split("  ", 1)[-1])

    def show_log(self) -> None:
        important = self.log_filter.GetSelection() == 1
        self.log_box.ChangeValue("".join(t + "\n" for t, imp in self.log_lines if imp or not important))
        self.log_box.SetInsertionPointEnd()

    def copy_log(self) -> None:
        if copy_text(self.log_box.GetValue()):
            speech.speak(_("Copied"))

    def clear_log(self) -> None:
        if not self.log_lines:
            return
        if not ask(self, _("Clear the log? This can't be undone.")):
            return
        self.log_lines = []
        self.log_box.ChangeValue("")

    def on_done(self, reason: str, run: Run | None = None) -> None:
        wx.CallAfter(self._done, run)

    def _done(self, run: Run | None) -> None:
        if self.destroyed:
            return
        for pid, r in list(self.runs.items()):
            if r is run or (run is None and not r.is_alive()):
                place: Where | None = getattr(getattr(r, "bot", None), "start_place", None)
                if place and not getattr(r, "walk", False):
                    self.farm_starts[pid] = place      # where `home` goes once nothing runs
                del self.runs[pid]
                self.last_line.pop(pid, None)
        self.label_games()
        self.update_buttons()
        self.poll()
        if run is not None and run.ended_by_itself and run.farmed() and run.after_stop == "shutdown":
            self.shutdown_wanted = True
        if getattr(self, "shutdown_wanted", False):
            if self.runs:
                self.say(_("The computer shuts down once the other bots have finished."))
            else:
                self.shutdown_wanted = False
                self.shut_down()

    def shut_down(self) -> None:
        """Every bot has finished and the setting says shut down: Windows' own shutdown, forced
        (/f: no program can hold it up) after a countdown (/t), with the chance to cancel it
        (shutdown /a)."""
        secs = max(10, int(self.s.get("shutdown_delay", 60) or 60))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.run(["shutdown", "/s", "/f", "/t", str(secs)], creationflags=flags, check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            error(self, _("Couldn't shut the computer down: {err}", err=e))
            return
        self.say(_("Farming is over. The computer shuts down in {n} seconds.", n=secs))
        with modal(wx.MessageDialog(self, _("The computer shuts down in {n} seconds. Cancel the shutdown?", n=secs),
                              _("Shutting down"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)) as d:
            d.SetYesNoLabels(_("&Cancel the shutdown"), _("&Let it shut down"))
            if d.ShowModal() == wx.ID_YES:
                subprocess.run(["shutdown", "/a"], creationflags=flags)
                self.say(_("Shutdown cancelled."))

    def say(self, text: str, run: Run | None = None) -> None:
        self._append(text, True, run)
