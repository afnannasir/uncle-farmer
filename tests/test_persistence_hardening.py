# pyright: reportPrivateUsage=false
# White-box tests: they reach the modules' locks, caches and the run's end (leading-underscore names).
"""Reliability of the app's own files under failure and concurrency (round 2 of the hardening).

Every test here injects the failure it is about - a Windows sharing violation, a full disk during
the write or the flush, a failed rename, an interruption before the rename - or runs real threads
against one file, held together with barriers and events rather than sleeps where the order
matters. What each must show: the old file stays whole, nothing unreadable is taken for empty and
saved over, two writers never lose each other's change, and the run and the scheduler stay usable
when a save fails.
"""

from __future__ import annotations

import datetime as dt
import errno
import glob
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from conftest import Clock, as_kind, as_parts
from unclefarmer import dungeons, gameinstall, jsonio, paths, schedule, settings, silencer, stats, travel
from unclefarmer.engine import bagrules, chores, runner, survey, town
from unclefarmer.engine.botparts.travel import TravelMixin

real_open = open


def busy() -> OSError:
    """A Windows sharing violation (another program has the file open): PermissionError, winerror 32."""
    return OSError(errno.EACCES, "The process cannot access the file", None, 32)


def disk_full() -> OSError:
    return OSError(errno.ENOSPC, "No space left on device")


class Interrupted(BaseException):
    """The process going away mid-save (a kill, a power cut): not an Exception, nothing handles it."""


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jsonio, "RETRY_WAITS", (0.0, 0.0, 0.0), raising=False)


def deny_reads(monkeypatch: pytest.MonkeyPatch, path: str | Path, times: int | None = None,
               error: Callable[[], OSError] = busy) -> list[int]:
    """open() of path for reading fails (times times, None = always); returns [failures so far]."""
    target = os.path.normcase(os.path.abspath(str(path)))
    count = [0]

    def opener(file: Any, mode: str = "r", *a: Any, **k: Any) -> Any:
        if (isinstance(file, (str, os.PathLike)) and "r" in mode
                and os.path.normcase(os.path.abspath(os.fspath(file))) == target  # pyright: ignore[reportUnknownArgumentType]
                and (times is None or count[0] < times)):
            count[0] += 1
            raise error()
        opened: Any = real_open(cast(Any, file), mode, *a, **k)
        return opened

    monkeypatch.setattr("builtins.open", opener)
    return count


def temp_files(folder: Path) -> list[str]:
    return sorted(os.path.basename(p) for p in glob.glob(str(folder / "*.tmp")))


def run_threads(targets: list[Callable[[], object]], timeout: float = 20.0) -> list[BaseException]:
    """Run each target on its own thread; the errors they raised. Fails if one never ends (a deadlock)."""
    errors: list[BaseException] = []

    def wrap(fn: Callable[[], object]) -> Callable[[], None]:
        def go() -> None:
            try:
                fn()
            except BaseException as e:   # collected for the test to judge
                errors.append(e)
        return go

    threads = [threading.Thread(target=wrap(t), daemon=True) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
        assert not t.is_alive(), "a thread never finished: deadlock"
    return errors


def slowed(monkeypatch: pytest.MonkeyPatch, owner: object, name: str, pause: float = 0.01) -> None:
    """owner.name, but each call lingers a moment after reading: an unlocked read-change-save
    then loses updates almost every time."""
    real: Callable[..., Any] = getattr(owner, name)

    def slow(*a: Any, **k: Any) -> Any:
        got = real(*a, **k)
        time.sleep(pause)
        return got

    monkeypatch.setattr(owner, name, slow)


# ===================================================================== jsonio: the shared helpers
def test_read_tells_missing_from_unreadable_from_broken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "x.json"
    assert jsonio.read(str(p), {"empty": True}) == {"empty": True}
    with pytest.raises(FileNotFoundError):
        jsonio.read(str(p))
    p.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        jsonio.read(str(p), {})
    p.write_text('{"a": 1}', encoding="utf-8")
    deny_reads(monkeypatch, p)
    with pytest.raises(PermissionError):
        jsonio.read(str(p), {})             # never the default for a file that is there


def test_read_waits_out_a_passing_sharing_violation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "x.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    failures = deny_reads(monkeypatch, p, times=2)
    assert jsonio.read(str(p)) == {"a": 1}
    assert failures == [2]


def test_read_does_not_retry_a_lasting_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "x.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    failures = deny_reads(monkeypatch, p, error=lambda: OSError(errno.EACCES, "denied", None, 5))
    with pytest.raises(PermissionError):
        jsonio.read(str(p))
    assert failures == [1]                  # access denied is not a passing error: one try


@pytest.mark.parametrize("where", ["write", "fsync", "replace", "interrupted"])
def test_save_text_leaves_the_old_file_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    p = tmp_path / "x.json"
    p.write_text('{"old": true}', encoding="utf-8")
    if where == "write":
        real_fdopen = cast(Callable[..., Any], os.fdopen)

        def fdopen(fd: int, *a: Any, **k: Any) -> Any:
            f: Any = real_fdopen(fd, *a, **k)

            def full(_text: str) -> int:
                raise disk_full()
            f.write = full
            return f
        monkeypatch.setattr(jsonio.os, "fdopen", fdopen)
        expected: type[BaseException] = OSError
    elif where == "fsync":
        def fsync(_fd: int) -> None:
            raise disk_full()
        monkeypatch.setattr(jsonio.os, "fsync", fsync)
        expected = OSError
    elif where == "replace":
        def replace(*_a: object) -> None:
            raise OSError(errno.EACCES, "denied", None, 5)
        monkeypatch.setattr(jsonio.os, "replace", replace)
        expected = OSError
    else:
        def stop(_fd: int) -> None:
            raise Interrupted()
        monkeypatch.setattr(jsonio.os, "fsync", stop)
        expected = Interrupted
    with pytest.raises(expected):
        jsonio.save(str(p), {"new": True})
    assert p.read_text(encoding="utf-8") == '{"old": true}'
    assert temp_files(tmp_path) == []


def test_save_text_tries_a_busy_replace_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "x.json"
    p.write_text("{}", encoding="utf-8")
    real_replace = os.replace
    tries = [0]

    def replace(src: str, dst: str) -> None:
        tries[0] += 1
        if tries[0] < 3:
            raise busy()
        real_replace(src, dst)

    monkeypatch.setattr(jsonio.os, "replace", replace)
    jsonio.save(str(p), {"saved": 1})
    assert jsonio.read(str(p)) == {"saved": 1} and tries == [3]
    assert temp_files(tmp_path) == []


def test_lock_for_is_one_lock_per_file(tmp_path: Path) -> None:
    a = jsonio.lock_for(str(tmp_path / "x.json"))
    assert jsonio.lock_for(str(tmp_path / "." / "x.json")) is a
    assert jsonio.lock_for(str(tmp_path / "X.JSON")) is (a if os.name == "nt" else jsonio.lock_for(str(tmp_path / "X.JSON")))
    assert jsonio.lock_for(str(tmp_path / "y.json")) is not a


# ===================================================================== 1. the game's options file
def vm_folder(root: Path, text: bytes) -> tuple[Path, Path]:
    folder = root / "game"
    folder.mkdir()
    (folder / "mw.exe").write_bytes(b"MZ")
    vm = folder / gameinstall.VMOPTIONS
    vm.write_bytes(text)
    return folder, vm


@pytest.mark.parametrize("where", ["write", "fsync", "replace", "interrupted"])
@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_a_failed_options_write_keeps_the_game_file_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                          where: str, action: str) -> None:
    original = b"-Xmx2g\r\n-XX:+UseG1GC\r\n"
    if action == "uninstall":
        original += (gameinstall.agent_line() + "\r\n").encode()
    folder, vm = vm_folder(tmp_path, original)
    if where == "write":
        def save_text(_path: str, _text: str, **_k: object) -> None:
            raise disk_full()           # jsonio.save_text failed while writing its temporary file
        monkeypatch.setattr(gameinstall.jsonio, "save_text", save_text)
        expected: type[BaseException] = OSError
    elif where == "fsync":
        def fsync(_fd: int) -> None:
            raise disk_full()
        monkeypatch.setattr(jsonio.os, "fsync", fsync)
        expected = OSError
    elif where == "replace":
        def replace(*_a: object) -> None:
            raise OSError(errno.EACCES, "denied", None, 5)
        monkeypatch.setattr(jsonio.os, "replace", replace)
        expected = OSError
    else:
        def stop(_fd: int) -> None:
            raise Interrupted()
        monkeypatch.setattr(jsonio.os, "fsync", stop)
        expected = Interrupted
    with pytest.raises(expected):
        getattr(gameinstall, action)(str(folder))
    assert vm.read_bytes() == original
    assert temp_files(folder) == []


def test_install_then_uninstall_is_repeatable_and_keeps_the_first_backup(tmp_path: Path) -> None:
    original = b"-Xmx2g\r\n-XX:+UseG1GC\r\n"
    folder, vm = vm_folder(tmp_path, original)
    backup = folder / (gameinstall.VMOPTIONS + ".bak-unclefarmer")
    for _round in range(3):
        gameinstall.install(str(folder))
        gameinstall.install(str(folder))
        assert vm.read_bytes() == original + (gameinstall.agent_line() + "\r\n").encode()
        assert gameinstall.status(str(folder)) == "installed"
        gameinstall.uninstall(str(folder))
        gameinstall.uninstall(str(folder))
        assert vm.read_bytes() == original           # the player's own lines and CRLF ends, as they were
        assert gameinstall.status(str(folder)) == "missing"
    assert backup.read_bytes() == original           # made once, never overwritten
    assert temp_files(folder) == []


def test_a_failed_backup_copy_leaves_no_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder, vm = vm_folder(tmp_path, b"-Xmx2g\n")

    def copyfile(_src: str, dst: str) -> None:
        Path(dst).write_bytes(b"-Xm")              # cut short
        raise disk_full()

    monkeypatch.setattr(gameinstall.shutil, "copyfile", copyfile)
    with pytest.raises(OSError):
        gameinstall.install(str(folder))
    assert not (folder / (gameinstall.VMOPTIONS + ".bak-unclefarmer")).exists()
    assert vm.read_bytes() == b"-Xmx2g\n"
    assert temp_files(folder) == []


# ===================================================================== 2, 3. learned dungeon entrances
def entrance(n: int) -> dict[str, Any]:
    return {"name": "Cave %d" % n, "map": 100 + n}


@pytest.fixture
def learned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "_learned_entrances.json"
    monkeypatch.setattr(dungeons, "LEARNED", str(path))
    monkeypatch.setattr(dungeons, "_last_learned", {}, raising=False)
    return path


def test_learning_an_entrance_saves_it(learned: Path) -> None:
    assert dungeons.learn(entrance(1), "Circle 1", 10.4, 20.6)
    assert jsonio.read(str(learned)) == {"cave 1|101": {"npc": "Circle 1", "at": [10, 21]}}


@pytest.mark.parametrize("where", ["fsync", "replace"])
def test_a_failed_learn_keeps_every_earlier_entrance(learned: Path, monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    for n in range(3):
        assert dungeons.learn(entrance(n), "Circle %d" % n, n, n)
    before = learned.read_bytes()

    def fail(*_a: object) -> None:
        raise disk_full() if where == "fsync" else OSError(errno.EACCES, "denied", None, 5)
    monkeypatch.setattr(jsonio.os, where, fail)
    assert dungeons.learn(entrance(9), "Circle 9", 9, 9) is False      # said, not claimed
    assert learned.read_bytes() == before
    assert temp_files(learned.parent) == []


def test_an_unreadable_learned_file_is_never_saved_over(learned: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for n in range(3):
        dungeons.learn(entrance(n), "Circle %d" % n, n, n)
    before = learned.read_bytes()
    deny_reads(monkeypatch, learned)
    assert dungeons.learn(entrance(9), "Circle 9", 9, 9) is False
    monkeypatch.setattr("builtins.open", real_open)
    assert learned.read_bytes() == before             # not one entry written over three


def test_a_moment_the_learned_file_cant_be_read_plans_with_the_last_reading(learned: Path,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    dungeons.learn(entrance(1), "Circle 1", 1, 1)
    assert dungeons._learned() != {}
    deny_reads(monkeypatch, learned)
    assert dungeons._learned() == {"cave 1|101": {"npc": "Circle 1", "at": [1, 1]}}


def test_concurrent_learning_keeps_every_entrance(learned: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slowed(monkeypatch, dungeons, "_read_learned")
    gate = threading.Barrier(8)

    def learn(n: int) -> Callable[[], object]:
        def go() -> object:
            gate.wait()
            return dungeons.learn(entrance(n), "Circle %d" % n, n, n)
        return go

    assert run_threads([learn(n) for n in range(8)]) == []
    assert sorted(jsonio.read(str(learned))) == sorted("cave %d|%d" % (n, 100 + n) for n in range(8))
    assert temp_files(learned.parent) == []


def test_a_reader_during_a_save_sees_the_old_or_the_new_file_whole(learned: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dungeons.learn(entrance(1), "Circle 1", 1, 1)
    in_save, release = threading.Event(), threading.Event()
    real_replace = os.replace

    def held_replace(src: str, dst: str) -> None:
        in_save.set()
        assert release.wait(10)
        real_replace(src, dst)

    monkeypatch.setattr(jsonio.os, "replace", held_replace)
    seen: list[dict[str, Any]] = []
    writer = threading.Thread(target=lambda: dungeons.learn(entrance(2), "Circle 2", 2, 2), daemon=True)
    writer.start()
    assert in_save.wait(10)
    reader = threading.Thread(target=lambda: seen.append(dungeons._learned()), daemon=True)
    reader.start()
    reader.join(0.2)
    assert reader.is_alive()                          # waits for the save instead of reading half of it
    release.set()
    writer.join(10)
    reader.join(10)
    assert sorted(seen[0]) == ["cave 1|101", "cave 2|102"]


def test_a_reader_waiting_on_a_failed_save_gets_the_old_file(learned: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dungeons.learn(entrance(1), "Circle 1", 1, 1)
    in_save, release = threading.Event(), threading.Event()

    def failing_replace(*_a: object) -> None:
        in_save.set()
        assert release.wait(10)
        raise disk_full()

    monkeypatch.setattr(jsonio.os, "replace", failing_replace)
    seen: list[dict[str, Any]] = []
    results: list[bool] = []
    writer = threading.Thread(target=lambda: results.append(dungeons.learn(entrance(2), "Circle 2", 2, 2)), daemon=True)
    writer.start()
    assert in_save.wait(10)
    reader = threading.Thread(target=lambda: seen.append(dungeons._learned()), daemon=True)
    reader.start()
    release.set()
    writer.join(10)
    reader.join(10)
    assert results == [False]
    assert seen == [{"cave 1|101": {"npc": "Circle 1", "at": [1, 1]}}]


def test_each_reader_only_ever_sees_more_entrances(learned: Path) -> None:
    stop = threading.Event()
    per_reader: list[list[int]] = [[], []]

    def reader(i: int) -> Callable[[], None]:
        def go() -> None:
            while not stop.is_set():
                per_reader[i].append(len(dungeons._learned()))
        return go

    def write() -> None:
        try:
            for n in range(40):
                assert dungeons.learn(entrance(n), "Circle %d" % n, n, n)
        finally:
            stop.set()

    assert run_threads([reader(0), reader(1), write]) == []
    assert len(jsonio.read(str(learned))) == 40
    for sizes in per_reader:
        assert all(b >= a for a, b in zip(sizes, sizes[1:])), "an entrance vanished for a reader"


# ===================================================================== 4. learned item weights
@pytest.fixture
def weights_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "_learned_weights.json"
    monkeypatch.setattr(bagrules, "WEIGHTS", str(path))
    monkeypatch.setattr(bagrules, "_last_weights", {}, raising=False)
    return path


@pytest.mark.parametrize("threads", [2, 8])
def test_concurrent_weight_learning_keeps_every_weight(weights_file: Path, monkeypatch: pytest.MonkeyPatch,
                                                       threads: int) -> None:
    jsonio.save(str(weights_file), {"old item": 1.5})
    slowed(monkeypatch, bagrules, "_read_weights")
    gate = threading.Barrier(threads)
    answers: list[float | None] = []

    def learn(n: int) -> Callable[[], object]:
        def go() -> object:
            gate.wait()
            answers.append(bagrules.learn_weight("Item %d" % n, 100.0, 100.0 - n - 1, 1))
            return None
        return go

    assert run_threads([learn(n) for n in range(threads)]) == []
    assert None not in answers
    want = {"old item": 1.5, **{"item %d" % n: float(n + 1) for n in range(threads)}}
    assert jsonio.read(str(weights_file)) == want
    assert bagrules.weights() == want


def test_a_weight_that_couldnt_be_saved_isnt_reported_and_the_rest_stay(weights_file: Path,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    jsonio.save(str(weights_file), {"old item": 1.5})

    def fsync(_fd: int) -> None:
        raise disk_full()
    monkeypatch.setattr(jsonio.os, "fsync", fsync)
    assert bagrules.learn_weight("New", 10.0, 8.0, 1) is None
    assert jsonio.read(str(weights_file)) == {"old item": 1.5}


def test_an_unreadable_weights_file_is_not_saved_over(weights_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jsonio.save(str(weights_file), {"old item": 1.5})
    assert bagrules.weights() == {"old item": 1.5}
    deny_reads(monkeypatch, weights_file)
    assert bagrules.learn_weight("New", 10.0, 8.0, 1) is None
    assert bagrules.weights() == {"old item": 1.5}       # the last good reading meanwhile
    monkeypatch.setattr("builtins.open", real_open)
    assert jsonio.read(str(weights_file)) == {"old item": 1.5}


# ===================================================================== 5. settings: unreadable is not broken
@pytest.fixture
def clean_unreadable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "_unreadable", set[str]())


def test_broken_settings_go_aside_and_the_defaults_are_used(clean_unreadable: None) -> None:
    Path(paths.SETTINGS).write_text("{broken", encoding="utf-8")
    assert settings.load_app()["language"] == settings.APP_DEFAULTS["language"]
    assert Path(paths.SETTINGS + ".corrupt").read_text(encoding="utf-8") == "{broken"


def test_missing_settings_give_the_defaults_and_write_nothing(clean_unreadable: None) -> None:
    assert settings.load_app()["daily_stop"] is True
    assert not os.path.exists(paths.SETTINGS)


def test_settings_read_through_a_passing_sharing_violation(clean_unreadable: None, monkeypatch: pytest.MonkeyPatch) -> None:
    jsonio.save(paths.SETTINGS, {"language": "ur", "daily_stop_at": "05:00"})
    failures = deny_reads(monkeypatch, paths.SETTINGS, times=2)
    s = settings.load_app()
    assert s["language"] == "ur" and s["daily_stop_at"] == "05:00" and failures == [2]
    assert not os.path.exists(paths.SETTINGS + ".corrupt")


@pytest.mark.parametrize("error", [busy, lambda: PermissionError(errno.EACCES, "denied")])
def test_settings_that_cant_be_read_are_never_moved_aside_or_saved_over(clean_unreadable: None,
                                                                         monkeypatch: pytest.MonkeyPatch,
                                                                         error: Callable[[], OSError]) -> None:
    jsonio.save(paths.SETTINGS, {"language": "ur", "daily_stop_at": "05:00"})
    before = Path(paths.SETTINGS).read_bytes()
    deny_reads(monkeypatch, paths.SETTINGS, error=error)
    s = settings.load_app()
    assert s["language"] == settings.APP_DEFAULTS["language"]     # defaults for this reading only
    assert not os.path.exists(paths.SETTINGS + ".corrupt")
    s["speak"] = False
    with pytest.raises(OSError, match="nothing was saved over it"):
        settings.save_app(s)
    with pytest.raises(OSError, match="nothing was saved over it"):
        settings.update_app({"speak": False})
    with pytest.raises(OSError, match="nothing was saved over it"):
        settings.save_app({"language": "en"})                     # a plain dict neither
    monkeypatch.setattr("builtins.open", real_open)
    assert Path(paths.SETTINGS).read_bytes() == before


def test_settings_read_while_locked_save_only_what_was_changed_once_readable(clean_unreadable: None,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    jsonio.save(paths.SETTINGS, {"language": "ur", "daily_stop_at": "05:00"})
    deny_reads(monkeypatch, paths.SETTINGS)
    s = settings.load_app()                       # the defaults, while another program holds the file
    monkeypatch.setattr("builtins.open", real_open)
    s["speak"] = False                            # the one thing the player changed
    settings.save_app(s)
    saved = jsonio.read(paths.SETTINGS)
    assert saved["language"] == "ur" and saved["daily_stop_at"] == "05:00" and saved["speak"] is False


def test_a_profile_that_cant_be_read_is_never_saved_over(clean_unreadable: None, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.save_profile("Hero", dict(settings.PROFILE_DEFAULTS, hp_below=33))
    path = settings.profile_path("Hero")
    before = Path(path).read_bytes()
    deny_reads(monkeypatch, path)
    p = settings.load_profile("Hero")
    assert p["hp_below"] == settings.PROFILE_DEFAULTS["hp_below"]
    assert not os.path.exists(path + ".corrupt")
    p["hearth_home"] = "Somewhere"
    with pytest.raises(OSError):
        settings.save_profile("Hero", p)
    monkeypatch.setattr("builtins.open", real_open)
    assert Path(path).read_bytes() == before
    settings.save_profile("Hero", p)              # readable again: only the change goes on
    again = settings.load_profile("Hero")
    assert again["hp_below"] == 33 and again["hearth_home"] == "Somewhere"


def test_a_profile_read_through_a_passing_sharing_violation(clean_unreadable: None, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.save_profile("Hero", dict(settings.PROFILE_DEFAULTS, hp_below=33))
    deny_reads(monkeypatch, settings.profile_path("Hero"), times=2)
    assert settings.load_profile("Hero")["hp_below"] == 33


# ===================================================================== 6. settings: one writer can't undo another
def test_the_silencer_and_the_window_both_keep_their_settings(clean_unreadable: None) -> None:
    jsonio.save(paths.SETTINGS, {"daily_stop_at": "05:00"})
    window = settings.load_app()
    settings.update_app({silencer.KEY: [{"id": 7, "name": "Fireball", "muted": True}]})   # on its thread
    window["daily_stop_at"] = "06:30"
    settings.save_app(window)
    saved = settings.load_app()
    assert saved["daily_stop_at"] == "06:30"
    assert saved[silencer.KEY] == [{"id": 7, "name": "Fireball", "muted": True}]
    window["speak"] = False                       # a later save still doesn't put the old sound list back
    settings.save_app(window)
    assert settings.load_app()[silencer.KEY] == [{"id": 7, "name": "Fireball", "muted": True}]


class SilentLink:
    def json(self, line: str) -> Any:
        return {"hooked": True, "reason": ""}

    def send(self, line: str) -> str:
        return "ok"


def test_a_silencer_save_mid_way_doesnt_undo_a_newer_daily_stop(clean_unreadable: None,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    # The silencer used to read the whole file, then save it: a daily_stop_at saved by the window
    # in between was put back to the old value. Now its write holds the settings lock, and the
    # window's save, waiting on it, goes onto the file the silencer left.
    jsonio.save(paths.SETTINGS, {"daily_stop_at": "05:00"})
    window = settings.load_app()
    in_save, go = threading.Event(), threading.Event()
    real_save = settings.json.save

    def held(path: str, obj: Any, **k: Any) -> None:
        if threading.current_thread().name == "silencer":
            in_save.set()
            assert go.wait(10)
        real_save(path, obj, **k)

    monkeypatch.setattr(settings.json, "save", held)
    def no_apply(_link: object, _text: str) -> None:
        return None

    monkeypatch.setattr(silencer, "_apply", no_apply)
    sil = threading.Thread(target=lambda: silencer.save([{"id": 9, "name": "Boom", "muted": True}],
                                                        as_kind(silencer.JsonLink, SilentLink())),
                           name="silencer", daemon=True)
    sil.start()
    assert in_save.wait(10)
    window["daily_stop_at"] = "06:30"
    gui = threading.Thread(target=lambda: settings.save_app(window), daemon=True)
    gui.start()
    gui.join(0.2)
    assert gui.is_alive()                         # waits for the silencer's save to finish
    go.set()
    sil.join(10)
    gui.join(10)
    saved = settings.load_app()
    assert saved["daily_stop_at"] == "06:30"
    assert [r["id"] for r in saved[silencer.KEY]] == [9]


def test_many_settings_writers_at_once_lose_nothing(clean_unreadable: None, monkeypatch: pytest.MonkeyPatch) -> None:
    changes: list[dict[str, Any]] = [{"daily_stop_at": "04:00"}, {"after_stop": "close_game"}, {"shutdown_delay": 90},
                                     {"log_level": "DEBUG"}, {"log_max_mb": 9}, {"log_backups": 2},
                                     {"skip_version": "9.9"}, {"results_every": 15}]
    slowed(monkeypatch, settings, "_load_app")
    window = settings.load_app()
    gate = threading.Barrier(len(changes) + 1)

    def writer(change: dict[str, Any]) -> Callable[[], object]:
        def go() -> object:
            gate.wait()
            return settings.update_app(change)
        return go

    def gui() -> None:
        gate.wait()
        window["speak"] = False
        settings.save_app(window)

    assert run_threads([writer(c) for c in changes] + [gui]) == []
    saved = settings.load_app()
    for c in changes:
        for k, v in c.items():
            assert saved[k] == v, k
    assert saved["speak"] is False


def test_a_failed_settings_write_reports_it_and_changes_nothing(clean_unreadable: None,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    jsonio.save(paths.SETTINGS, {"daily_stop_at": "05:00"})
    before = Path(paths.SETTINGS).read_bytes()

    def fsync(_fd: int) -> None:
        raise disk_full()
    monkeypatch.setattr(jsonio.os, "fsync", fsync)
    with pytest.raises(OSError):
        settings.update_app({"daily_stop_at": "07:00"})
    window = settings.load_app()
    window["daily_stop_at"] = "07:00"
    with pytest.raises(OSError):
        settings.save_app(window)
    assert Path(paths.SETTINGS).read_bytes() == before
    monkeypatch.undo()
    settings.save_app(window)                     # the change still counts as unsaved, and goes on now
    assert settings.load_app()["daily_stop_at"] == "07:00"


# ===================================================================== 7. the chores' done list
def test_concurrent_marks_all_land(monkeypatch: pytest.MonkeyPatch) -> None:
    chores.mark("Hero", "first", "before")
    slowed(monkeypatch, chores, "_done_today")
    gate = threading.Barrier(8)

    def mark(n: int) -> Callable[[], object]:
        def go() -> object:
            gate.wait()
            chores.put("Hero", "chore %d" % n, {"n": n})
            return None
        return go

    assert run_threads([mark(n) for n in range(8)]) == []
    done = chores.done_today("Hero")
    assert set(done) == {"first"} | {"chore %d" % n for n in range(8)}
    assert temp_files(Path(chores._ledger("Hero")).parent) == []


def test_marks_never_share_a_temporary_file(monkeypatch: pytest.MonkeyPatch) -> None:
    used: list[str] = []
    real_mkstemp = cast(Callable[..., tuple[int, str]], jsonio.tempfile.mkstemp)

    def mkstemp(*a: Any, **k: Any) -> tuple[int, str]:
        fd, name = real_mkstemp(*a, **k)
        used.append(name)
        return fd, name

    monkeypatch.setattr(jsonio.tempfile, "mkstemp", mkstemp)
    gate = threading.Barrier(4)

    def mark(n: int) -> Callable[[], object]:
        def go() -> object:
            gate.wait()
            chores.put("Hero", "chore %d" % n, n)
            return None
        return go

    assert run_threads([mark(n) for n in range(4)]) == []
    assert len(used) == 4 and len(set(used)) == 4


def test_a_failed_mark_keeps_the_earlier_ones_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    chores.mark("Hero", "bank", "done")
    path = Path(chores._ledger("Hero"))
    before = path.read_bytes()

    def replace(*_a: object) -> None:
        raise OSError(errno.EACCES, "denied", None, 5)
    monkeypatch.setattr(jsonio.os, "replace", replace)
    with pytest.raises(OSError):
        chores.put("Hero", "gang", "done")
    assert path.read_bytes() == before
    assert temp_files(path.parent) == []


def test_an_unreadable_done_list_is_not_saved_over(monkeypatch: pytest.MonkeyPatch) -> None:
    chores.mark("Hero", "bank", "done")
    chores.mark("Hero", "gang", "done")
    path = Path(chores._ledger("Hero"))
    before = path.read_bytes()
    deny_reads(monkeypatch, path)
    with pytest.raises(OSError):
        chores.put("Hero", "pets", "done")
    monkeypatch.setattr("builtins.open", real_open)
    assert path.read_bytes() == before
    assert set(chores.done_today("Hero")) == {"bank", "gang"}


# ===================================================================== 8. schedules
T0 = dt.datetime(2026, 11, 2, 5, 0)


def daily_at(time_text: str = "06:00") -> schedule.Schedule:
    return {"item": "plan:Wolves", "character": "Hero", "repeat": schedule.DAILY, "time": time_text, "enabled": True}


class Starts:
    def __init__(self, answer: tuple[str, str] = (schedule.STARTED, "in the game")) -> None:
        self.answer = answer
        self.calls: list[dt.datetime] = []

    def __call__(self, s: schedule.Schedule, due: dt.datetime) -> tuple[str, str]:
        self.calls.append(due)
        return self.answer


def failing_saves(monkeypatch: pytest.MonkeyPatch, error: Callable[[], OSError]) -> list[int]:
    """schedule.save fails while the list's value is [1]; returns that switch."""
    switch = [1]
    real = schedule.save

    def save(items: list[schedule.Schedule], path: str = schedule.SCHEDULES) -> None:
        if switch[0]:
            raise error()
        real(items, path)

    monkeypatch.setattr(schedule, "save", save)
    return switch


@pytest.mark.parametrize("error", [disk_full, busy])
def test_an_occurrence_whose_save_failed_runs_at_the_next_tick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                               error: Callable[[], OSError]) -> None:
    sch, start = schedule.Scheduler(str(tmp_path / "schedules.json")), Starts()
    s = sch.add(daily_at("06:00"), T0)
    switch = failing_saves(monkeypatch, error)
    due = dt.datetime(2026, 11, 2, 6, 0)
    with pytest.raises(OSError):
        sch.tick(due, start)
    assert start.calls == []                          # not started without its stamp on disk
    assert s["handled"] == schedule.stamp(T0)         # ... and not taken for done in memory either
    switch[0] = 0
    sch.tick(due + dt.timedelta(minutes=1), start)
    assert start.calls == [due]                       # the next tick runs it, once
    sch.tick(due + dt.timedelta(minutes=2), start)
    assert start.calls == [due]
    on_disk = schedule.read(sch.path)[0]
    assert on_disk["handled"] == schedule.stamp(due)
    assert [h["outcome"] for h in on_disk["history"]] == [schedule.STARTED]


def test_a_failed_save_after_a_start_keeps_what_happened_and_writes_it_next(tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    sch = schedule.Scheduler(str(tmp_path / "schedules.json"))
    s = sch.add(daily_at("06:00"), T0)
    due = dt.datetime(2026, 11, 2, 6, 0)
    switch = [0]
    real = schedule.save

    def save(items: list[schedule.Schedule], path: str = schedule.SCHEDULES) -> None:
        if switch[0]:
            raise disk_full()
        real(items, path)

    monkeypatch.setattr(schedule, "save", save)

    def start(sched: schedule.Schedule, when: dt.datetime) -> tuple[str, str]:
        switch[0] = 1                                 # the disk fills up while the run starts
        return schedule.STARTED, "in the game"

    with pytest.raises(OSError):
        sch.tick(due, start)
    assert sch.dirty and [h["outcome"] for h in s["history"]] == [schedule.STARTED]   # never undone: it ran
    later = Starts()
    with pytest.raises(OSError):
        sch.tick(due + dt.timedelta(minutes=1), later)    # still full: nothing starts meanwhile
    switch[0] = 0
    sch.tick(due + dt.timedelta(minutes=2), later)
    assert later.calls == [] and not sch.dirty
    assert [h["outcome"] for h in schedule.read(sch.path)[0]["history"]] == [schedule.STARTED]


def test_a_missed_note_whose_save_failed_is_written_once_at_the_next_tick(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    sch, start = schedule.Scheduler(str(tmp_path / "schedules.json")), Starts()
    s = sch.add(daily_at("06:00"), T0)
    switch = failing_saves(monkeypatch, disk_full)
    late = dt.datetime(2026, 11, 3, 9, 0)             # a day of occurrences missed, today's well past
    with pytest.raises(OSError):
        sch.tick(late, start)
    assert s.get("history") == [] and s["handled"] == schedule.stamp(T0)
    switch[0] = 0
    sch.tick(late, start)
    assert [h["outcome"] for h in s["history"]] == ["missed"]
    assert [h["outcome"] for h in schedule.read(sch.path)[0]["history"]] == ["missed"]


def test_concurrent_ticks_start_an_occurrence_exactly_once(tmp_path: Path) -> None:
    sch, start = schedule.Scheduler(str(tmp_path / "schedules.json")), Starts()
    sch.add(daily_at("06:00"), T0)
    due = dt.datetime(2026, 11, 2, 6, 0)
    gate = threading.Barrier(6)

    def tick() -> object:
        gate.wait()
        return sch.tick(due, start)

    assert run_threads([tick] * 6) == []
    assert start.calls == [due]
    assert [h["outcome"] for h in schedule.read(sch.path)[0]["history"]] == [schedule.STARTED]


def test_an_unreadable_schedules_file_is_never_saved_over(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "schedules.json"
    first = schedule.Scheduler(str(path))
    first.add(daily_at("06:00"), T0)
    first.add(daily_at("07:00"), T0)
    before = path.read_bytes()
    deny_reads(monkeypatch, path)
    sch = schedule.Scheduler(str(path))
    assert sch.items == [] and sch.unread is not None
    with pytest.raises(OSError):
        sch.add(daily_at("08:00"), T0)                 # an empty list saved there would lose both
    monkeypatch.setattr("builtins.open", real_open)
    assert path.read_bytes() == before
    sch.tick(T0 + dt.timedelta(minutes=1), Starts())   # readable again: the file's schedules count
    assert sorted(s["time"] for s in sch.items) == ["06:00", "07:00", "08:00"]


# ===================================================================== 9. a run's profile and the player's edits
def test_a_run_learning_a_field_keeps_the_players_edit_made_meanwhile(clean_unreadable: None) -> None:
    settings.save_profile("Hero", dict(settings.PROFILE_DEFAULTS, hp_below=40))
    bot_p = settings.load_profile("Hero")             # the run's copy, from its start
    window = settings.load_profile("Hero")
    window["hp_below"] = 25                           # the player edits while the bot runs
    settings.save_profile("Hero", window)
    bot_p["hearth_home"] = "Mountain Square"          # then the bot learns its hearthstone home
    settings.save_profile("Hero", bot_p)
    p = settings.load_profile("Hero")
    assert p["hp_below"] == 25 and p["hearth_home"] == "Mountain Square"
    window["attack_gap"] = 0.9                        # and the window's next save keeps the bot's
    settings.save_profile("Hero", window)
    p = settings.load_profile("Hero")
    assert p["hearth_home"] == "Mountain Square" and p["attack_gap"] == 0.9 and p["hp_below"] == 25


class HearthBot:
    """Just what TravelMixin.learn_hearth_home uses."""

    def __init__(self, p: dict[str, Any], save: Callable[[dict[str, Any]], None]) -> None:
        self.p = p
        self.save_profile = save
        self.lines: list[str] = []
        name, (self.mid, self.x, self.y) = next(iter(travel.SQUARES.items()))
        self.square = name

    def map_id(self) -> int:
        return self.mid

    def me(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y}

    def log(self, line: str, **_k: object) -> None:
        self.lines.append(line)


def test_the_hearthstone_home_a_run_learns_doesnt_revert_the_players_edits(clean_unreadable: None) -> None:
    settings.save_profile("Hero", dict(settings.PROFILE_DEFAULTS, hp_below=40, chase_range=30))
    bot = HearthBot(settings.load_profile("Hero"), lambda data: settings.save_profile("Hero", data))
    window = settings.load_profile("Hero")
    window["hp_below"], window["chase_range"] = 20, 55
    settings.save_profile("Hero", window)
    TravelMixin.learn_hearth_home(as_kind(TravelMixin, bot))
    p = settings.load_profile("Hero")
    assert p["hearth_home"] == bot.square
    assert p["hp_below"] == 20 and p["chase_range"] == 55


def test_run_only_overrides_are_never_saved(clean_unreadable: None) -> None:
    settings.save_profile("Hero", dict(settings.PROFILE_DEFAULTS, points_key="T"))
    p = settings.load_profile("Hero")
    settings.set_not_saved(p, "points_key", "")            # an old agent: off for this run alone
    p["hearth_home"] = "Mountain Square"
    settings.save_profile("Hero", p)
    saved = settings.load_profile("Hero")
    assert saved["points_key"] == "T" and saved["hearth_home"] == "Mountain Square"


def test_bot_and_window_saving_the_profile_at_once_both_land(clean_unreadable: None,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    settings.save_profile("Hero", dict(settings.PROFILE_DEFAULTS))
    slowed(monkeypatch, settings, "_load_profile")
    copies = [settings.load_profile("Hero") for _ in range(6)]
    fields = [("hearth_home", "Square A"), ("hp_below", 21), ("attack_gap", 0.8), ("chase_range", 44),
              ("gem_fees", {"ruby": 5}), ("crystal_recipes", {"fire 1": {"shards": 3, "fee": 9}})]
    gate = threading.Barrier(len(copies))

    def save(i: int) -> Callable[[], object]:
        def go() -> object:
            key, value = fields[i]
            copies[i][key] = value
            gate.wait()
            settings.save_profile("Hero", copies[i])
            return None
        return go

    assert run_threads([save(i) for i in range(len(copies))]) == []
    p = settings.load_profile("Hero")
    for key, value in fields:
        assert p[key] == value, key


def test_a_plain_dict_profile_is_still_written_whole(clean_unreadable: None) -> None:
    settings.save_profile("Hero", dict(settings.PROFILE_DEFAULTS, hp_below=40, hearth_home="A"))
    settings.save_profile("Hero", dict(settings.PROFILE_DEFAULTS, hp_below=10))   # an import, say
    p = settings.load_profile("Hero")
    assert p["hp_below"] == 10 and p["hearth_home"] == settings.PROFILE_DEFAULTS["hearth_home"]


# ===================================================================== 10. the run's end
class EndTally:
    """A Tally whose save fails as told."""

    def __init__(self, error: Exception | None) -> None:
        self.real = stats.Tally("Hero", "Wolves", clock=Clock())
        self.error = error
        self.drop_left = None
        self.saves = 0

    def finish(self, bot: object = None) -> None:
        self.real.finish()

    def save(self) -> None:
        self.saves += 1
        if self.error is not None:
            raise self.error
        self.real.save()

    def results(self) -> list[dict[str, Any]]:
        return self.real.results()


def ended_run(error: Exception | None, done: Callable[[str, runner.Run], None] | None = None) -> tuple[runner.Run, list[str], list[str]]:
    lines: list[str] = []
    finished: list[str] = []
    running = {1: "the run"}

    def on_done(reason: str, run: runner.Run) -> None:
        finished.append(reason)
        running.pop(1)                     # the window's list: Start is free again
        if done is not None:
            done(reason, run)

    run = runner.Run("", "Wolves", 1, lambda line, speak, r: lines.append(line), on_done)
    run.tally = as_kind(stats.Tally, EndTally(error))
    return run, lines, finished


def test_a_run_that_saves_its_results_ends_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path / "results"))
    run, _lines, finished = ended_run(None)
    run._end({}, "finished")
    assert finished == ["finished"] and run.end_failures == []
    run._end({}, "again")                          # never twice
    assert finished == ["finished"]


@pytest.mark.parametrize("error", [OSError("results folder gone"), disk_full()])
def test_a_failed_results_save_still_ends_the_run(error: OSError) -> None:
    run, lines, finished = ended_run(error)
    with pytest.raises(OSError) as raised:          # in tests carry_on raises: the failure stays visible
        run._end({}, "finished")
    assert raised.value is error
    assert finished == ["finished"]                # on_done ran: the run is off the list, Start works again
    assert [w for w, _e in run.end_failures] == ["saving the results"]
    assert any("couldn't save the results" in line for line in lines)
    assert any("Results of" in line for line in lines)   # still reported from memory


def test_a_failed_results_save_ends_the_run_quietly_in_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNCLEFARMER_TESTING", raising=False)
    run, _lines, finished = ended_run(disk_full())
    run._end({}, "finished")                       # no exception out of the run's thread
    assert finished == ["finished"] and run.end_failures[0][0] == "saving the results"


def test_a_failing_on_done_doesnt_hide_the_results_failure() -> None:
    def broken(_reason: str, _run: runner.Run) -> None:
        raise RuntimeError("window gone")

    save_error = disk_full()
    run, _lines, finished = ended_run(save_error, broken)
    with pytest.raises(ExceptionGroup) as raised:
        run._end({}, "finished")
    assert raised.value.exceptions[0] is save_error
    assert isinstance(raised.value.exceptions[1], RuntimeError)
    assert finished == ["finished"]


def test_a_crash_on_the_way_out_still_calls_on_done(monkeypatch: pytest.MonkeyPatch) -> None:
    run, _lines, finished = ended_run(None)

    def broken(*_a: object) -> None:
        raise RuntimeError("bug on the way out")

    monkeypatch.setattr(run, "_last_readings", broken)
    with pytest.raises(RuntimeError):
        run._end({}, "finished")
    assert finished == ["finished"]


def test_results_that_failed_to_save_can_be_saved_later(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path / "results"))
    tally = stats.Tally("Hero", "Wolves", clock=Clock())
    tally.parts[tally.main].kills = 3
    real_fsync = os.fsync
    switch = [1]

    def fsync(fd: int) -> None:
        if switch[0]:
            raise disk_full()
        real_fsync(fd)

    monkeypatch.setattr(stats.os, "fsync", fsync)
    with pytest.raises(OSError):
        tally.save()
    path = tmp_path / "results" / "Hero.jsonl"
    assert path.read_text(encoding="utf-8") == ""   # no half record left behind
    switch[0] = 0
    tally.save()
    tally.save()                                    # once only
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


# ===================================================================== 11. results read while the run adds to them
class StatBot:
    def __init__(self) -> None:
        self.kills = 0
        self.deaths = 0
        self.n = 0

    def bag_rows(self) -> list[dict[str, Any]]:
        self.n += 1
        return [{"name": "Item %d" % i, "count": 1} for i in range(self.n % 200)]

    def me(self) -> dict[str, Any]:
        return {"sheet": None}

    def map_id(self) -> int:
        return 1503


def test_results_can_be_read_while_the_run_updates_them() -> None:
    tally = stats.Tally("Hero", "Wolves", clock=Clock())
    bot = StatBot()
    stop = threading.Event()
    reads = [0]

    def run() -> None:
        try:
            for i in range(400):
                bot.kills += 1
                tally.update(as_parts(bot), force=True)
                tally.moved("sold", "Item %d" % i, 1)
                if i % 25 == 0:
                    tally.switch(None, "job %d" % i)        # a new part appears
                    tally.parts["job %d" % i].changes["Gem %d" % i] = 1
                    tally.switch(None, stats.FARMING)
                tally.combat.observe([{"caster": 1, "skill": "Skill %d" % i, "hits": [[2, 0, 5]]}], 1)
        finally:
            stop.set()

    def read() -> None:
        while not stop.is_set():
            for r in tally.results():
                assert r["kills"] >= 0 and isinstance(r["items"], dict)
            stats.short(tally.result(), tally.drop_left)
            reads[0] += 1

    assert run_threads([run, read, read]) == []
    assert reads[0] > 0
    assert tally.result()["kills"] == 400


def test_a_result_is_a_snapshot_that_later_changes_dont_reach() -> None:
    tally = stats.Tally("Hero", "Wolves", clock=Clock())
    tally.combat.observe([{"caster": 1, "skill": "Arrow", "hits": [[2, 0, 5]]}], 1)
    tally.moved("sold", "Pelt", 2)
    first = tally.result()
    tally.moved("sold", "Pelt", 3)
    tally.combat.observe([{"caster": 1, "skill": "Arrow", "hits": [[2, 0, 7]]}], 1)
    tally.combat.note_kill("Wolf")
    assert first["moved"] == {"sold": {"Pelt": 2}}
    assert first["combat"]["skills"]["Arrow"]["damage"] == 5
    assert first["combat"]["last_kill"] is None
    again = tally.result()
    assert again["moved"] == {"sold": {"Pelt": 5}} and again["combat"]["skills"]["Arrow"]["damage"] == 12


# ===================================================================== A. learned landings
@pytest.fixture
def landings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(travel, "_landings", None)
    def unknown(mid: int, exit_i: int) -> tuple[int, int]:
        return mid, exit_i

    monkeypatch.setattr(travel, "unknown_landing", unknown)
    yield Path(travel._landings_path())
    travel._landings = None
    travel._static = None
    travel._plan_cache.clear()


def test_a_landing_learned_by_another_game_meanwhile_is_kept(landings: Path) -> None:
    assert travel.landings() == {}                 # read at the session's start: nothing
    jsonio.save(str(landings), {"7|1": [5, 5]})     # another of our games learns one
    assert travel.learn_landing(9, 2, 9, (10.2, 11.7))
    assert jsonio.read(str(landings)) == {"7|1": [5, 5], "9|2": [10, 12]}


def test_concurrent_landings_all_land(landings: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slowed(monkeypatch, travel, "_read_landings")
    gate = threading.Barrier(6)

    def learn(n: int) -> Callable[[], object]:
        def go() -> object:
            gate.wait()
            return travel.learn_landing(100 + n, n, 100 + n, (n, n))
        return go

    assert run_threads([learn(n) for n in range(6)]) == []
    assert sorted(jsonio.read(str(landings))) == sorted("%d|%d" % (100 + n, n) for n in range(6))


def test_an_unreadable_landings_file_is_kept_and_the_landing_used_for_the_session(landings: Path,
                                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    jsonio.save(str(landings), {"7|1": [5, 5]})
    assert travel.landings() == {(7, 1): (5, 5)}
    before = landings.read_bytes()
    deny_reads(monkeypatch, landings)
    assert travel.learn_landing(9, 2, 9, (10, 12))
    monkeypatch.setattr("builtins.open", real_open)
    assert landings.read_bytes() == before
    assert travel.landing(9, 2) == (10, 12)


def test_a_failed_landing_save_keeps_the_file(landings: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jsonio.save(str(landings), {"7|1": [5, 5]})
    before = landings.read_bytes()

    def replace(*_a: object) -> None:
        raise disk_full()
    monkeypatch.setattr(jsonio.os, "replace", replace)
    assert travel.learn_landing(9, 2, 9, (10, 12))
    assert landings.read_bytes() == before and temp_files(landings.parent) == []


# ===================================================================== B. shard prices
SHARDS = '{\n  "shards": {\n    "Fire Shard": {"price": 100, "makes": []},\n    "Ice Shard": {"price": 200, "makes": []}\n  },\n  "markets": {}\n}\n'


def test_two_markets_saving_prices_at_once_both_land(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    (tmp_path / town.SHARDS_FILE).write_text(SHARDS, encoding="utf-8")
    slowed(monkeypatch, town.json, "read_text")
    gate = threading.Barrier(2)
    changed: list[list[tuple[str, int, int]]] = []

    def save(prices: dict[str, int]) -> Callable[[], object]:
        def go() -> object:
            gate.wait()
            changed.append(town.save_shard_prices(prices))
            return None
        return go

    assert run_threads([save({"Fire Shard": 111}), save({"Ice Shard": 222})]) == []
    data = jsonio.read(str(tmp_path / town.SHARDS_FILE))
    assert data["shards"]["Fire Shard"]["price"] == 111 and data["shards"]["Ice Shard"]["price"] == 222
    assert sorted(len(c) for c in changed) == [1, 1]


def test_a_failed_price_save_keeps_the_old_prices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    (tmp_path / town.SHARDS_FILE).write_text(SHARDS, encoding="utf-8")

    def fsync(_fd: int) -> None:
        raise disk_full()
    monkeypatch.setattr(jsonio.os, "fsync", fsync)
    with pytest.raises(OSError):
        town.save_shard_prices({"Fire Shard": 111})
    assert (tmp_path / town.SHARDS_FILE).read_text(encoding="utf-8") == SHARDS
    assert temp_files(tmp_path) == []


# ===================================================================== C. survey records
def record(path: Path) -> survey.Record:
    r = survey.Record.__new__(survey.Record)
    r.mid, r.path = 1503, str(path)
    r.data = {"map": 1503, "npcs": {}, "swept": False}
    return r


def test_concurrent_survey_saves_never_collide(tmp_path: Path) -> None:
    rec = record(tmp_path / "1503.json")
    gate = threading.Barrier(8)

    def save() -> object:
        gate.wait()
        rec.save()
        return None

    assert run_threads([save] * 8) == []
    assert jsonio.read(rec.path)["map"] == 1503
    assert temp_files(tmp_path) == []


def test_a_failed_survey_save_keeps_the_last_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rec = record(tmp_path / "1503.json")
    rec.save()
    before = Path(rec.path).read_bytes()
    rec.data["swept"] = True

    def replace(*_a: object) -> None:
        raise OSError(errno.EACCES, "denied", None, 5)
    monkeypatch.setattr(jsonio.os, "replace", replace)
    with pytest.raises(OSError):
        rec.save()
    assert Path(rec.path).read_bytes() == before
    assert temp_files(tmp_path) == []
