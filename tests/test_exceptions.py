"""The exception policy: a best-effort action may ignore a failure, but never a Stopped, a
Respawned or an AgentError - the run has to stop, start over, or report the lost agent. And an
ignored failure is logged once per kind: silence is what let the old broad catches hide corners."""

import os
import threading

import pytest

from unclefarmer import applog
from unclefarmer.engine import follow, keyslots, runner as R
from unclefarmer.engine.botparts import dialogs
from unclefarmer.engine.botparts.base import Respawned, Stopped, note_failure
from unclefarmer.engine.botparts.fighting import FightingMixin
from unclefarmer.engine.botparts.travel import TravelMixin
from unclefarmer.engine.botparts.walking import WalkingMixin
from unclefarmer.link import AgentError


class Bot(FightingMixin, WalkingMixin, TravelMixin):
    def __init__(self):
        self.lines = []
        self.p = {}
        self.failure_notes = set()

    def log(self, message, speak=False):
        self.lines.append(message)


def test_note_failure_re_raises_in_test_mode():
    with pytest.raises(ValueError, match="hidden bug"):
        note_failure(Bot(), "reading", ValueError("hidden bug"))


def test_note_failure_logs_once_per_kind(allow_ignored_failures):
    b = Bot()
    note_failure(b, "reading", ValueError("x"))
    note_failure(b, "reading", ValueError("y"))
    note_failure(b, "reading", KeyError("z"))
    assert len(b.lines) == 2
    assert "carrying on" in b.lines[0]


@pytest.mark.parametrize("exc", [Stopped("stop"), Respawned(), AgentError("gone")])
def test_a_control_exception_is_never_swallowed_by_clear_shot(monkeypatch, exc):
    b = Bot()

    def boom(mid):
        raise exc

    monkeypatch.setattr("unclefarmer.engine.botparts.fighting.atlas.model", boom)
    with pytest.raises(type(exc)):
        b.clear_shot({"x": 1, "y": 1}, {"x": 2, "y": 2}, mid=5)


def test_clear_shot_logs_an_unknown_map_once_and_still_shoots(monkeypatch, allow_ignored_failures):
    b = Bot()

    def boom(mid):
        raise KeyError("no geometry")

    monkeypatch.setattr("unclefarmer.engine.botparts.fighting.atlas.model", boom)
    assert b.clear_shot({"x": 1, "y": 1}, {"x": 2, "y": 2}, mid=5) is True
    assert b.clear_shot({"x": 1, "y": 1}, {"x": 2, "y": 2}, mid=5) is True
    assert len(b.lines) == 1


def test_has_hearthstone_re_raises_a_lost_agent():
    b = Bot()
    b.hotkey_of = lambda name: None

    def boom():
        raise AgentError("gone")

    b.bag_rows = boom
    with pytest.raises(AgentError):
        b.has_hearthstone()


def test_npc_cells_re_raises_a_stopped():
    b = Bot()

    def boom():
        raise Stopped("stop")

    b.snap = boom
    with pytest.raises(Stopped):
        b.npc_cells((1, 1))


def test_walk_dist_re_raises_a_stopped(monkeypatch):
    b = Bot()
    monkeypatch.setattr("unclefarmer.engine.botparts.walking.atlas.model",
                        lambda mid: (_ for _ in ()).throw(Stopped("stop")))
    snap = {"self": {"x": 1, "y": 1}, "map": {"id": 5}}
    with pytest.raises(Stopped):
        b.walk_dist(snap, {"x": 2, "y": 2})


def test_keyslots_arrange_re_raises_a_stopped_even_without_the_stop_event():
    class KB:
        p = {"fill_empty_keys": True}
        in_fight = False
        stop_event = threading.Event()

        def dialog(self):
            raise Stopped("the game can't be read", final=True)

        def log(self, message, speak=False):
            pass

    with pytest.raises(Stopped):
        keyslots.arrange(KB())


def test_follow_locate_re_raises_a_lost_agent():
    class FB:
        def locate(self, name):
            raise AgentError("gone")

        def log(self, message, speak=False):
            pass

    with pytest.raises(AgentError):
        follow._locate(FB(), "Friend")


def test_follow_locate_logs_a_shape_error_and_says_not_found(allow_ignored_failures):
    lines = []

    class FB:
        def locate(self, name):
            raise ValueError("odd reply")

        def log(self, message, speak=False):
            lines.append(message)

    assert follow._locate(FB(), "Friend") is None
    assert len(lines) == 1 and "not found" not in lines[0]


def test_a_lost_agent_while_setting_phrases_is_not_swallowed(monkeypatch):
    from unclefarmer import gamekeys, phrases
    monkeypatch.setattr(phrases, "read", lambda *a: {})
    monkeypatch.setattr(phrases, "known", lambda *a: True)
    monkeypatch.setattr(phrases, "wanted", lambda cls: ["/6"])
    monkeypatch.setattr(phrases, "what", lambda code: "x")
    monkeypatch.setattr(gamekeys, "phrase_key_list", lambda: ["Shift+%d" % i for i in range(1, 13)])

    class B:
        p = {}

        def log(self, message, speak=False):
            pass

        class a:
            @staticmethod
            def send(line):
                raise AgentError("gone")

    with pytest.raises(AgentError):
        phrases.arrange(B(), "folder", 1, "Hunter", True)


def test_stats_re_raise_a_stopped_reading():
    from unclefarmer import stats
    b = Bot()
    b.bag_rows = lambda: []
    b.map_id = lambda: 1
    b.me = lambda: (_ for _ in ()).throw(Stopped("stop"))
    with pytest.raises(Stopped):
        stats.Tally("Hero", "Wolves").update(b, force=True)


def test_applog_write_does_not_raise_on_a_broken_log(monkeypatch, tmp_path):
    broken = tmp_path / "logs"
    broken.write_text("not a folder", encoding="utf-8")
    monkeypatch.setattr(applog.paths, "LOGS", str(broken))
    monkeypatch.setattr(applog, "_warned", False)
    applog.write("line")                 # must not raise


def test_applog_crash_keeps_the_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(applog.paths, "LOGS", str(tmp_path))
    try:
        raise ValueError("boom")
    except ValueError:
        applog.crash("Title", "Hero")
    text = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "Title (Hero)" in text
    assert "Traceback" in text and "ValueError: boom" in text


def test_note_dialog_rotates_the_log(monkeypatch, tmp_path):
    monkeypatch.setattr(applog.paths, "LOGS", str(tmp_path))
    monkeypatch.setattr(applog, "_max_bytes", 10)
    monkeypatch.setattr(applog, "_backups", 1)
    path = os.path.join(str(tmp_path), "dialogs.log")
    with open(path, "w", encoding="utf-8") as f:
        f.write("x" * 50)

    class DB(dialogs.DialogsMixin):
        character = "Hero"

        def map_id(self):
            return 5

        def log(self, message, speak=False):
            pass

    dialogs.DialogsMixin.note_dialog(
        DB(), {"top": {"row": 0, "col": 0, "rows": [[{"text": "hi"}]]}}, "why")
    assert os.path.exists(path + ".1")
    assert "hi" in open(path, encoding="utf-8").read()


def test_guard_logs_a_failed_finish(monkeypatch, tmp_path, allow_ignored_failures):
    monkeypatch.setattr(applog.paths, "LOGS", str(tmp_path))
    logs = []
    run = R.Run("", "Test", 1, lambda line, speak, who: logs.append(line), lambda *a: None)
    run.character = "Hero"

    class B:
        def defend(self):
            raise RuntimeError("stuck")

        def finish(self):
            raise RuntimeError("stuck")

    run.guard(B())
    assert any("couldn't finish safely" in line for line in logs)
    assert any("couldn't stay on guard either" in line for line in logs)
