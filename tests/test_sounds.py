"""Sound filters, protocol validation and cooperative script waits."""

from typing import Any

import pytest

from conftest import Clock, as_bot
from test_script import FakeBot, FakeLink
from unclefarmer import sounds
from unclefarmer.engine import script
from unclefarmer.engine.bot import Stopped
from unclefarmer.link import AgentError


def status_reply(**changes: Any) -> dict[str, Any]:
    return dict(hooked=True, reason="ready", watch="off", mute="off", observed=0, dropped=0,
                live_muted=0, unsupported=0) | changes


def event(seq: int, sound_id: int = 7, **changes: Any) -> dict[str, Any]:
    return dict(seq=seq, id=sound_id, x=1.5, y=2.5, t=123, muted=False) | changes


class SoundLink(FakeLink):
    def __init__(self, batches: list[dict[str, Any]] | None = None, **status: Any):
        super().__init__()
        self.commands: list[str] = []
        self.batches = list(batches or [])
        self.status = status_reply(**status)
        self.cursor = 0

    def json(self, line: str) -> Any:
        self.commands.append(line)
        if line.startswith("sounds "):
            if self.batches:
                batch = self.batches.pop(0)
                self.cursor = batch["now"]
                return batch
            return dict(now=self.cursor, events=[], lost=False)
        if line.startswith("sound"):
            return self.status.copy()
        return super().json(line)


class SoundBot(FakeBot):
    def __init__(self, link: SoundLink):
        super().__init__()
        self.a = link
        self.clock = Clock()
        self.checks = 0
        self.stop_at_check = 0

    def check(self) -> None:
        self.checks += 1
        if self.checks == self.stop_at_check:
            self.stop_event.set()
            raise Stopped("stopped")

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        self.clock.sleep(seconds)


@pytest.mark.parametrize(("text", "normalized", "size"), [
    ("1,5|7;10-20", "1,5,7,10-20", 14), ("1-5,3-7,8", "1-8", 8),
    (" ALL ", "all", 99999), ("off", "off", 0), ("none", "off", 0),
])
def test_filter_normalizes(text: str, normalized: str, size: int):
    ids = sounds.IdSet.parse(text)
    assert ids.text == normalized
    assert ids.size() == size
    assert ids.is_empty() == (size == 0)
    assert not ids.has(0) and not ids.has(100000)


@pytest.mark.parametrize("text", ["", "0", "100000", "4-2", "1,,2", "-1", "1.5", "all,1", "1\nsoundmute all"])
def test_bad_filter_rejected(text: str):
    with pytest.raises(ValueError):
        sounds.IdSet.parse(text)


def test_commands_normalized_before_send():
    link = SoundLink()
    sounds.watch(link, "1;2|3")
    sounds.mute(link, "NONE")
    sounds.listen(link, "7")
    assert link.commands == ["soundwatch 1-3", "soundmute off", "soundlisten 7"]


@pytest.mark.parametrize("reply", [[], {}, {"ok": False, "error": "unknown command"}, status_reply(observed=-1)])
def test_invalid_status_is_not_success(reply: Any):
    class Link:
        def json(self, line: str) -> Any:
            return reply
    with pytest.raises(AgentError):
        sounds.status(Link())


@pytest.mark.parametrize("row", [event(0), event(1, 0), event(1, x=float("nan")), event(1, muted="yes")])
def test_invalid_event_rejected(row: dict[str, Any]):
    with pytest.raises(AgentError):
        sounds.events(SoundLink([dict(now=1, events=[row], lost=False)]), 0)


def test_same_timestamp_events_survive_sequence_cursor():
    batch = sounds.events(SoundLink([dict(now=2, events=[event(1), event(2)], lost=False)]), 0)
    assert [e["seq"] for e in batch["events"]] == [1, 2]


@pytest.mark.parametrize("command", ["sound_wait off", "sound_wait 7 timeout=-1", "sound_wait 7 timeout=nan",
                                    "sound_watch 0", "sound_mute 9-2", "set sound_id = 5"])
def test_script_rejects_invalid_sound_command(command: str):
    with pytest.raises(script.ScriptError):
        script.parse(command)


def test_wait_ignores_history_filters_other_listeners_and_preserves_global_watch():
    link = SoundLink([
        dict(now=10, events=[event(10)], lost=False),
        dict(now=12, events=[event(11, 8), event(12, 7, muted=True)], lost=False),
    ], watch="500")
    bot = SoundBot(link)
    runner = script.Runner(as_bot(bot), "sound_wait 7 timeout=1")
    runner.run()
    assert runner.vars["sound_received"] is True
    assert runner.vars["sound_id"] == 7 and runner.vars["sound_muted"] is True
    assert runner.vars["sound_x"] == 1.5 and runner.vars["sound_positioned"] is True
    assert link.commands == ["sounds 0", "soundlisten 7", "sounds 10", "soundlisten off"]


def test_wait_timeout_is_cooperative_and_clears_previous_result():
    bot = SoundBot(SoundLink())
    runner = script.Runner(as_bot(bot), "sound_wait 7 timeout=0.25")
    runner.vars["sound_id"] = 99
    runner.run()
    assert runner.vars["sound_received"] is False and runner.vars["sound_id"] == 0
    assert bot.checks >= 3
    assert sum(call[1] for call in bot.calls if call[0] == "sleep") == pytest.approx(0.25)
    assert bot.a.commands[-1] == "soundlisten off"


def test_wait_releases_listener_on_stop():
    bot = SoundBot(SoundLink())
    bot.stop_at_check = 3
    with pytest.raises(Stopped):
        script.Runner(as_bot(bot), "sound_wait 7").run()
    assert bot.a.commands[-1] == "soundlisten off"


def test_wait_reports_buffer_loss():
    link = SoundLink([dict(now=1, events=[], lost=False), dict(now=600, events=[], lost=True)])
    with pytest.raises(Stopped, match="lost"):
        script.Runner(as_bot(SoundBot(link)), "sound_wait 7").run()
    assert link.commands[-1] == "soundlisten off"


def test_nonpositional_sound():
    link = SoundLink([dict(now=0, events=[], lost=False), dict(now=1, events=[event(1, x=None, y=None)], lost=False)])
    runner = script.Runner(as_bot(SoundBot(link)), "sound_wait 7")
    runner.run()
    assert runner.vars["sound_received"] is True
    assert runner.vars["sound_positioned"] is False and runner.vars["sound_x"] == 0


def test_unavailable_observer_releases_listener_and_stops():
    link = SoundLink(hooked=False, reason="ambiguous dispatcher")
    with pytest.raises(Stopped, match="ambiguous dispatcher"):
        script.Runner(as_bot(SoundBot(link)), "sound_wait 7").run()
    assert link.commands[-1] == "soundlisten off"


def test_global_script_commands_persist():
    link = SoundLink()
    script.Runner(as_bot(SoundBot(link)), "sound_watch 1-5\nsound_mute 7").run()
    assert link.commands == ["soundwatch 1-5", "soundmute 7"]
