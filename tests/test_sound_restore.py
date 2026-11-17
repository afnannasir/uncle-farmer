"""Saved rules are restored once per game, without overwriting later script choices."""
from typing import Any

from unclefarmer import soundrestore


def test_restore_on_new_game_and_configuration_change(monkeypatch: Any) -> None:
    config = {"sound_silencer": [{"id": 101, "name": "Step", "muted": True}]}
    calls: list[int] = []

    class Thread:
        def __init__(self, target: Any, **kwargs: Any): self.target = target
        def start(self) -> None: self.target()

    class Link:
        def __init__(self, pid: int, **kwargs: Any): self.pid = pid
        def __enter__(self) -> "Link": return self
        def __exit__(self, *args: Any) -> None: pass

    monkeypatch.setattr(soundrestore.threading, "Thread", Thread)
    monkeypatch.setattr(soundrestore.settings, "load_app", lambda: config)
    monkeypatch.setattr(soundrestore, "AgentLink", Link)
    def apply(link: Link) -> None:
        calls.append(link.pid)
    monkeypatch.setattr(soundrestore.silencer, "apply_saved", apply)
    restore = soundrestore.SoundRestore()
    restore.sync([1])
    restore.sync([1])
    assert calls == [1]
    restore.sync([1, 2])
    assert calls == [1, 2]
    config["sound_silencer"][0]["muted"] = False
    restore.sync([1, 2])
    assert calls == [1, 2, 1, 2]
    restore.close()
    restore.sync([3])
    assert calls == [1, 2, 1, 2]


def test_corrupt_and_absent_configuration_does_not_unmute(monkeypatch: Any) -> None:
    class Thread:
        def __init__(self, target: Any, **kwargs: Any): self.target = target
        def start(self) -> None: self.target()
    monkeypatch.setattr(soundrestore.threading, "Thread", Thread)
    def forbidden(*args: Any, **kwargs: Any) -> None: raise AssertionError("must not touch audio")
    monkeypatch.setattr(soundrestore, "AgentLink", forbidden)
    restore = soundrestore.SoundRestore()
    for config in ({}, {"sound_silencer": "bad"}, {"sound_silencer": [{"id": -1}]}):
        monkeypatch.setattr(soundrestore.settings, "load_app", lambda: config)
        restore.sync([1])
        assert not restore.lock.locked()
