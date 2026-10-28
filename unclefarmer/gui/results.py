"""Results: what a character earned in a run, today, this week, or ever.

A read-only text, one fact per line, so a screen reader can walk it line by line. It follows a
running bot by itself (every 10 seconds) and speaks only when asked. Farming and the jobs that
aren't farming (Gem Maker, Equipment Enhancer, chores) are shown apart: shards made into gems are
no loot, and the time a job takes is no farming time.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

import wx

from .. import speech, stats
from ..clock import CLOCK, Clock
from ..i18n import _
from ..stats import Tally
from .common import Dialog, Form, copy_text

PERIODS = ("run", "today", "week", "month", "all")
SHOW = (stats.FARMING, stats.TOOL)


class ResultsDialog(Dialog):
    def __init__(self, parent: wx.Window, character: str | None, live: Callable[[str], Tally | None]) -> None:
        """live(character) -> the stats.Tally of a bot running as that character, or None."""
        super().__init__(parent, title=_("Results"), size=wx.Size(640, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.live = live
        self.clock: Clock = getattr(parent, "clock", CLOCK)
        names = stats.characters()
        if character and character not in names:
            names.insert(0, character)
        self.names = names or [_("nobody yet")]
        root = wx.BoxSizer(wx.VERTICAL)
        form = Form(self)
        self.who = form.choice(_("&Character:"), self.names,
                               self.names.index(character) if character in self.names else 0)
        self.when = form.choice(_("&Period:"), [_("This run (or the last one)"), _("Today"),
                                                _("Last 7 days"), _("Last 30 days"), _("All time")], 0)
        self.what = form.choice(_("S&how:"), [_("Farming"), _("Gem Maker, Equipment Enhancer and chores")], 0)
        root.Add(form.grid, 0, wx.EXPAND | wx.ALL, 10)
        root.Add(wx.StaticText(self, label=_("&Results:")), 0, wx.LEFT, 10)
        self.text = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP)
        self.text.SetName(_("Results"))
        root.Add(self.text, 1, wx.EXPAND | wx.ALL, 10)

        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in ((_("&Speak"), self.on_speak), (_("C&opy"), self.on_copy),
                               (_("Open the &folder"), self.on_folder)):
            b = wx.Button(self, label=label)
            b.Bind(wx.EVT_BUTTON, handler)
            row.Add(b, 0, wx.RIGHT, 6)
        close = wx.Button(self, wx.ID_CLOSE, _("Close"))
        close.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        row.Add(close)
        self.SetEscapeId(wx.ID_CLOSE)
        root.Add(row, 0, wx.ALL, 10)
        self.SetSizer(root)

        self.who.Bind(wx.EVT_CHOICE, lambda e: self.show())
        self.when.Bind(wx.EVT_CHOICE, lambda e: self.show())
        self.what.Bind(wx.EVT_CHOICE, lambda e: self.show())
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self.show(keep_place=True), self.timer)
        self.timer.Start(10000)
        self.Bind(wx.EVT_WINDOW_DESTROY, lambda e: self.timer.Stop())
        self.show()
        self.text.SetFocus()

    def result(self) -> tuple[str | None, str]:
        """(text, what to speak) for the chosen character, period and kind."""
        name = self.names[max(0, self.who.GetSelection())]
        period = PERIODS[max(0, self.when.GetSelection())]
        kind = SHOW[max(0, self.what.GetSelection())]
        tally = self.live(name)
        live: list[dict[str, Any]] = [r for r in tally.results() if r["kind"] == kind] if tally else []
        now = self.clock.time()
        if period == "run":
            if tally and live:
                title = _("{name}, this run ({what}, running)", name=name, what=tally.title)
                return self.texts(kind, live, title, single=True)
            past = stats.runs(name, kind=kind)
            if past:
                title = _("{name}, last run ({what}, started {when})", name=name,
                          what=past[-1].get("run"), when=past[-1].get("start"))
                return self.texts(kind, [r for r in past if r["start"] == past[-1]["start"]], title, single=True)
            return None, _("{name}: no runs yet", name=name)
        since = {"today": time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")),
                 "week": now - 7 * 86400, "month": now - 30 * 86400, "all": None}[period]
        results = stats.runs(name, since, kind=kind) + live
        label = {"today": _("today"), "week": _("the last 7 days"), "month": _("the last 30 days"),
                 "all": _("everything")}[period]
        if not results:
            return None, _("{name}, {period}: no runs", name=name, period=label)
        return self.texts(kind, results, _("{name}, {period}", name=name, period=label))

    @staticmethod
    def texts(kind: str, results: list[dict[str, Any]], title: str, single: bool = False) -> tuple[str, str]:
        """(the report, a line to speak). Farming adds up into one text; the jobs get one each."""
        if kind == stats.FARMING:
            r = results[-1] if single else stats.add_up(results)
            return stats.report(r, title), title + ". " + stats.short(r)
        return (stats.report_tools(results, title),
                title + ". " + "; ".join(stats.short(r) for r in results[-3:]))

    def show(self, keep_place: bool = False) -> None:
        text, _spoken = self.result()
        text = text or _spoken
        if text == self.text.GetValue():
            return
        where = self.text.GetInsertionPoint()
        self.text.ChangeValue(text)
        # a live refresh must not throw the reader back to the top
        self.text.SetInsertionPoint(min(where, self.text.GetLastPosition()) if keep_place else 0)

    def on_speak(self, _evt: wx.Event) -> None:
        _text, spoken = self.result()
        speech.speak(spoken)

    def on_copy(self, _evt: wx.Event) -> None:
        if copy_text(self.text.GetValue()):
            speech.speak(_("Copied"))

    def on_folder(self, _evt: wx.Event) -> None:
        os.makedirs(stats.RESULTS, exist_ok=True)
        os.startfile(stats.RESULTS)
