"""The sound window polls off-thread and never starts or stops farming."""
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("wx")
import wx

from unclefarmer.gui.sounds import KEEP, LiveSoundsPanel, SoundWorker, event_text


def status() -> dict[str, Any]:
    return dict(hooked=True, reason="", watch="all", mute="5", observed=4, dropped=1, live_muted=0, unsupported=0)


class Link:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.closed = False

    def json(self, line: str) -> dict[str, Any]:
        self.commands.append(line)
        if line.startswith("sounds "):
            if self.commands.count("sounds 0") == 1:
                return dict(now=0, lost=False, events=[])
            return dict(now=1, lost=False, events=[dict(seq=1, id=5, x=2, y=3, t=0, muted=True)])
        return status()

    def send(self, line: str) -> str:
        raise AssertionError("Sound reader must not send game keys")

    def close(self) -> None:
        self.closed = True


def test_worker_applies_and_polls_using_its_own_connection_then_closes() -> None:
    link = Link()
    deliveries: list[tuple[str, Any]] = []
    def receive(kind: str, data: Any) -> None:
        deliveries.append((kind, data))
        if kind == "events":
            worker.stop()
    worker = SoundWorker(42, receive, lambda: link)
    assert worker.apply("listen", "1;5-7")
    worker.start()
    worker.join(3)
    assert not worker.is_alive()
    assert link.closed
    assert link.commands == ["soundstatus", "sounds 0", "soundlisten all", "soundlisten 1,5-7", "sounds 0", "soundstatus", "soundlisten off"]
    assert [kind for kind, _data in deliveries] == ["initial", "applied", "events", "status"]
    assert not worker.apply("watch", "all")


def test_worker_reports_connection_failure() -> None:
    deliveries: list[tuple[str, Any]] = []
    def connect() -> Link:
        raise OSError("game closed")
    worker = SoundWorker(42, lambda kind, data: deliveries.append((kind, data)), connect)
    worker.run()
    assert deliveries == [("disconnected", "game closed")]


def test_event_text_does_not_claim_missing_coordinates_or_actual_playback() -> None:
    text = event_text(dict(id=5, x=None, y=None, muted=False))
    assert "unavailable" in text and "sound request" in text


@pytest.fixture
def frame():
    app = wx.GetApp() or wx.App(False)
    parent = wx.Frame(None)
    calls: list[int] = []
    window = LiveSoundsPanel(parent, 42, calls.append, start_worker=False)
    yield window, calls
    window.Destroy()
    parent.Destroy()
    app.ProcessPendingEvents()


def test_live_rows_are_bounded_and_selection_adds_exact_id_to_draft(frame: Any) -> None:
    window, calls = frame
    window.receive("initial", status())
    window.receive("events", dict(lost=True, events=[dict(seq=i+1,id=i+1,x=2,y=3,muted=False) for i in range(KEEP+10)]))
    assert window.events.GetCount() == KEEP
    window.events.SetSelection(0)
    window.add_selected()
    assert calls == [11]
    assert not hasattr(window, "mute_apply")
    assert not window.worker.apply("mute", "all")


def test_closing_stops_listener_and_ignores_late_events(frame: Any) -> None:
    window, _calls = frame
    window.finish()
    window.finish()
    assert window.worker.stopped.is_set()
    before = window.status.GetValue()
    window.receive("disconnected", "late callback")
    assert window.status.GetValue() == before
    assert window.worker.commands.empty()
