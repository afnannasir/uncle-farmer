"""Sound IDs and positions from one game's agent; all I/O stays on one worker."""
from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Mapping
from typing import Any

import wx

from .. import sounds
from ..i18n import _
from ..link import AgentLink, OpenLink
from .common import labeled

KEEP = 200


class SoundWorker(threading.Thread):
    """One connection per window, independent of the farming connection. No game keys."""

    def __init__(self, pid: int, deliver: Callable[[str, Any], None],
                 connect: Callable[[], OpenLink] | None = None) -> None:
        super().__init__(name="sound-monitor", daemon=True)
        self.deliver = deliver
        self.connect = connect or (lambda: AgentLink(pid=pid, timeout=2))
        self.stopped = threading.Event()
        self.wake = threading.Event()
        self.commands: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=1)

    def apply(self, action: str, text: str) -> bool:
        if self.stopped.is_set() or action != "listen":
            return False
        try:
            self.commands.put_nowait((action, text))
        except queue.Full:
            return False
        self.wake.set()
        return True

    def stop(self) -> None:
        self.stopped.set()
        self.wake.set()

    def run(self) -> None:
        link: OpenLink | None = None
        try:
            link = self.connect()
            self.deliver("initial", sounds.status(link))
            after = sounds.events(link, 0)["now"]
            sounds.listen(link, "all")
            while not self.stopped.is_set():
                self.wake.clear()
                try:
                    _action, text = self.commands.get_nowait()
                except queue.Empty:
                    pass
                else:
                    try:
                        result = sounds.listen(link, text)
                        self.deliver("applied", result)
                    except (ValueError, RuntimeError, OSError) as e:
                        self.deliver("apply_error", str(e))
                if self.stopped.is_set():
                    break
                batch = sounds.events(link, after)
                after = batch["now"]
                self.deliver("events", batch)
                self.deliver("status", sounds.status(link))
                self.wake.wait(0.5)
        except (ValueError, RuntimeError, OSError) as e:
            self.deliver("disconnected", str(e))
        finally:
            if link is not None:
                try:
                    sounds.listen(link, "off")
                except (ValueError, RuntimeError, OSError):
                    pass
                link.close()


def event_text(event: Mapping[str, Any]) -> str:
    disposition = _("muted request") if event["muted"] else _("sound request")
    return _("ID {id}, X {x}, Y {y}: {state}", id=event["id"],
             x=event["x"] if event["x"] is not None else _("unavailable"),
             y=event["y"] if event["y"] is not None else _("unavailable"), state=disposition)


def status_text(status: dict[str, Any]) -> str:
    state = _("Connected") if status["hooked"] else _("Hook unavailable: {reason}", reason=status["reason"])
    return _("{state}. Active mute IDs: {mute}. Observed: {observed}; blocked: {dropped}; "
             "live muted: {live}; unsupported: {unsupported}.", state=state,
             mute=status["mute"], observed=status["observed"], dropped=status["dropped"],
             live=status["live_muted"], unsupported=status["unsupported"])


class LiveSoundsPanel(wx.Panel):
    """Discovery only. Adding an event edits the parent's draft, never live muting."""
    def __init__(self, parent: wx.Window, pid: int, add_sound: Callable[[int], None],
                 *, start_worker: bool = True) -> None:
        super().__init__(parent)
        self.closed = False
        self.add_sound = add_sound
        self.rows: list[dict[str, Any]] = []
        box = wx.BoxSizer(wx.VERTICAL)
        help_text = wx.StaticText(self, label=_(
            "Listen for game sound requests, select an ID, then add it to Saved Sounds. "
            "This tab does not change muting. Save changes on the Saved Sounds tab."))
        help_text.Wrap(620)
        box.Add(help_text, 0, wx.EXPAND | wx.BOTTOM, 8)
        self.listening = wx.CheckBox(self, label=_("Listen for sounds"))
        self.listening.SetValue(True)
        box.Add(self.listening, 0, wx.BOTTOM, 8)
        self.status = labeled(self, box, _("Status:"), lambda w: wx.TextCtrl(
            w, value=_("Connecting..."), style=wx.TE_READONLY | wx.TE_MULTILINE))
        self.events = labeled(self, box, _("Recent sound &requests (latest 200):"), lambda w: wx.ListBox(w), 1)
        self.add_button = wx.Button(self, label=_("Add selected to Saved Sounds"))
        self.add_button.Disable()
        box.Add(self.add_button, 0, wx.TOP, 8)
        self.SetSizer(box)
        self.events.Bind(wx.EVT_LISTBOX, lambda e: self.add_button.Enable(not self.closed))
        self.add_button.Bind(wx.EVT_BUTTON, lambda e: self.add_selected())
        self.listening.Bind(wx.EVT_CHECKBOX, self.set_listening)
        self.Bind(wx.EVT_WINDOW_DESTROY, self.on_destroy)
        self.worker = SoundWorker(pid, self.enqueue)
        if start_worker:
            self.worker.start()

    def set_listening(self, event: wx.CommandEvent) -> None:
        if not self.worker.apply("listen", "all" if self.listening.GetValue() else "off"):
            self.listening.SetValue(not self.listening.GetValue())
            self.status.SetValue(_("The listener is busy or disconnected. Try again."))

    def add_selected(self) -> None:
        index = self.events.GetSelection()
        if not self.closed and 0 <= index < len(self.rows):
            self.add_sound(self.rows[index]["id"])

    def enqueue(self, kind: str, data: Any) -> None:
        if not self.closed:
            wx.CallAfter(self.receive, kind, data)

    def receive(self, kind: str, data: Any) -> None:
        if self.closed:
            return
        if kind in ("initial", "status", "applied"):
            self.status.SetValue(status_text(data))
        elif kind == "events":
            if data["lost"]:
                self.status.SetValue(_("Some sound requests were missed because the event buffer filled."))
            if data["events"]:
                selected = self.events.GetSelection()
                selected_sequence = self.rows[selected].get("seq") if 0 <= selected < len(self.rows) else None
                self.rows = (self.rows + list(data["events"]))[-KEEP:]
                self.events.Set([event_text(row) for row in self.rows])
                if selected_sequence is not None:
                    for i, row in enumerate(self.rows):
                        if row.get("seq") == selected_sequence:
                            self.events.SetSelection(i)
                            break
                self.add_button.Enable(self.events.GetSelection() != wx.NOT_FOUND)
        else:
            self.status.SetValue(_("Sound controls: {error}", error=data))
            if kind == "disconnected":
                self.listening.Disable()

    def finish(self) -> None:
        if not self.closed:
            self.closed = True
            self.worker.stop()

    def on_destroy(self, event: wx.WindowDestroyEvent) -> None:
        if event.GetEventObject() is self:
            self.finish()
        event.Skip()
