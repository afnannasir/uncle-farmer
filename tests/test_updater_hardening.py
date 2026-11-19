# pyright: reportPrivateUsage=false
"""Updater failure injection against the flat Nuitka release layout; no real app is touched."""
from __future__ import annotations

import http.client
from pathlib import Path
import shutil
import types
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast
if TYPE_CHECKING:
    from subprocess import Popen

import pytest

import build_exe
from unclefarmer import updater
from test_updater import Resp, fake_open, for_download, junction, release
from conftest import returning


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "installed"
    folder.mkdir()
    (folder / updater.EXE).write_bytes(b"old executable")
    monkeypatch.setattr(updater.paths, "APP", str(folder))
    monkeypatch.setattr(updater.paths, "LOGS", str(folder / "logs"))
    monkeypatch.setattr(updater, "UPDATE", str(folder / "update"))
    monkeypatch.setattr(updater, "NEW", str(folder / "update/new"))
    monkeypatch.setattr(updater, "RESULT", str(folder / "update/result.json"))
    return folder


def flat(folder: Path, marker: str = "new") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for pattern in build_exe.REQUIRED:
        target = folder / pattern.replace("*", "314")
        if pattern in updater.RUNTIME_DIRS:
            target = target / "__init__.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(marker.encode())
    updater.write_build(str(folder), "0.5.0", "master")
    return folder


def snapshot(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
            and p.relative_to(folder).parts[0] != updater.recovery.HERE}


@pytest.mark.parametrize("failure", ["copy", "replace"])
def test_flat_update_failure_restores_every_old_byte(app: Path, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    flat(app, "old")
    src = flat(tmp_path / "release")
    (app / "numpy/obsolete.pyd").write_bytes(b"old runtime")
    (app / "settings.json").write_bytes(b"private settings")
    before = snapshot(app)
    original_copy, original_replace = shutil.copy2, updater.recovery.move
    calls = 0

    def copy(source: Any, target: Any, **kwargs: Any) -> Any:
        nonlocal calls
        if failure == "copy":
            calls += 1
            if calls == 4:
                raise OSError("injected fourth copy failure")
        return original_copy(source, target, **kwargs)

    def replace(source: Any, target: Any) -> None:
        nonlocal calls
        if failure == "replace" and Path(target).parent == app:
            calls += 1
            if calls == 4:
                raise OSError("injected fourth replacement failure")
        original_replace(source, target)

    monkeypatch.setattr(updater.shutil, "copy2", copy)
    monkeypatch.setattr(updater.recovery, "move", replace)
    with pytest.raises((OSError, updater.UpdateError), match="fourth"):
        updater.apply(str(app), str(src), lambda text: None)
    assert snapshot(app) == before


def test_flat_success_replaces_runtime_preserves_data_and_commits_exe_last(app: Path, tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    flat(app, "old")
    src = flat(tmp_path / "release")
    (app / "numpy/obsolete.pyd").write_bytes(b"old runtime")
    (app / "obsolete.dll").write_bytes(b"old root runtime")
    (app / "agent/run").mkdir()
    (app / "agent/run/live").write_bytes(b"keep")
    (app / "data/game_texts.json").write_bytes(b"learned")
    (app / "settings.json").write_bytes(b"private")
    original = updater.recovery.move
    installed: list[str] = []

    def replace(source: Any, target: Any) -> None:
        if Path(target).parent == app:
            installed.append(Path(target).name)
        original(source, target)

    monkeypatch.setattr(updater.recovery, "move", replace)
    updater.apply(str(app), str(src), lambda text: None)
    assert not (app / "numpy/obsolete.pyd").exists()
    assert not (app / "obsolete.dll").exists()
    assert (app / "agent/run/live").read_bytes() == b"keep"
    assert (app / "data/game_texts.json").read_bytes() == b"learned"
    assert (app / "settings.json").read_bytes() == b"private"
    assert installed[-2:] == [updater.recovery.PAYLOAD, updater.BUILD_FILE]


@pytest.mark.parametrize("operation", ["download", "unpack", "claimed"])
def test_staging_junction_rejected_before_any_write(app: Path, tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    (target / "keep").write_bytes(b"unchanged")
    junction(app / "update", target)
    fake_open(monkeypatch, Resp(b"zip"))
    with pytest.raises(updater.UpdateError):
        if operation == "download":
            updater.download(for_download())
        elif operation == "unpack":
            updater.unpack(str(tmp_path / "missing.zip"))
        else:
            with updater.claimed("test"):
                pytest.fail("unsafe claim accepted")
    assert snapshot(target) == {"keep": b"unchanged"}


@pytest.mark.parametrize("body", ["[]", '"text"', "12", "null", "{}", "{broken"])
def test_result_json_shapes_never_crash_startup(app: Path, body: str) -> None:
    path = app / "update/result.json"
    path.parent.mkdir()
    path.write_text(body, encoding="utf-8")
    result = updater.take_result()
    assert result is not None and result["ok"] is False
    assert "unreadable" in result["error"]


@pytest.mark.parametrize(("asset_name", "state", "accepted"), [
    ("UncleFarmer-0.4.0.zip", "uploaded", False),
    ("UncleFarmer-0.5.0.zip", "starter", False),
    ("UncleFarmer-0.5.0.zip", "uploaded", True),
])
def test_stable_asset_must_match_tag_and_be_uploaded(asset_name: str, state: str, accepted: bool) -> None:
    data = release(tag_name="v0.5.0")
    data["assets"][0].update(name=asset_name, state=state)
    assert (updater._stable(data) is not None) is accepted


@pytest.mark.parametrize("header", ["wrong", "-1", "3.5"])
def test_invalid_content_length_is_an_update_error_and_cleans_part(
    app: Path, monkeypatch: pytest.MonkeyPatch, header: str,
) -> None:
    fake_open(monkeypatch, Resp(b"abc", {"Content-Length": header}))
    with pytest.raises(updater.UpdateError):
        updater.download(for_download())
    assert not list((app / "update").glob("*.part"))


@pytest.mark.parametrize("exception", [http.client.IncompleteRead(b"partial", 20), RuntimeError("unexpected read")])
def test_interrupted_download_cleans_partial_file(app: Path, monkeypatch: pytest.MonkeyPatch,
                                                exception: Exception) -> None:
    class Broken(Resp):
        def read(self, n: int | None = -1) -> bytes:
            raise exception
    fake_open(monkeypatch, Broken())
    with pytest.raises((updater.UpdateError, RuntimeError)):
        updater.download(for_download())
    assert not list((app / "update").glob("*.part"))


@pytest.mark.parametrize("arguments", [[], ["app"], ["app", "not-a-pid", "0.5.0"]])
def test_bad_apply_arguments_never_escape(app: Path, arguments: list[str]) -> None:
    updater.run_apply(arguments)


def test_apply_rejects_low_free_space_without_changing_files(app: Path, tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    flat(app, "old")
    src = flat(tmp_path / "release")
    before = snapshot(app)
    def no_space(path: Any) -> Any:
        return shutil._ntuple_diskusage(100, 100, 0)
    monkeypatch.setattr(updater.shutil, "disk_usage", no_space)
    with pytest.raises(updater.UpdateError, match="space"):
        updater.apply(str(app), str(src), lambda text: None)
    assert snapshot(app) == before


@pytest.mark.parametrize("folder_first", [True, False])
def test_file_folder_type_change(app: Path, tmp_path: Path, folder_first: bool) -> None:
    flat(app, "old")
    src = flat(tmp_path / "release")
    old, new = app / "changed", src / "changed"
    folder, file = (old, new) if folder_first else (new, old)
    folder.mkdir()
    (folder / "item").write_bytes(b"nested")
    file.write_bytes(b"plain")
    updater.apply(str(app), str(src), lambda text: None)
    assert old.is_file() if folder_first else old.is_dir()


def test_case_insensitive_protected_nested_directories(app: Path, tmp_path: Path) -> None:
    flat(app, "old")
    src = flat(tmp_path / "release")
    (app / "agent/Run").mkdir()
    (app / "agent/Run/port").write_bytes(b"live")
    (src / "agent").rename(src / "Agent")
    (src / "Agent/RUN").mkdir()
    (src / "Agent/RUN/port").write_bytes(b"release")
    updater.apply(str(app), str(src), lambda text: None)
    assert (app / "agent/Run/port").read_bytes() == b"live"


@pytest.mark.parametrize(("error", "alive"), [(5, True), (87, False), (123, True)])
def test_process_open_failure_is_not_proof_of_exit(monkeypatch: pytest.MonkeyPatch,
                                                  error: int, alive: bool) -> None:
    kernel = types.SimpleNamespace(OpenProcess=returning(0), GetLastError=returning(error))
    monkeypatch.setattr(updater.ctypes, "windll", types.SimpleNamespace(kernel32=kernel))
    assert updater._alive(424242) is alive


def test_process_wait_timeout_does_not_allow_copying(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = types.SimpleNamespace(OpenProcess=returning(42), WaitForSingleObject=returning(258),
                                   CloseHandle=returning(None))
    monkeypatch.setattr(updater.ctypes, "windll", types.SimpleNamespace(kernel32=kernel))
    monkeypatch.setattr(updater, "_alive", returning(True))
    with pytest.raises(updater.UpdateError, match="still running"):
        updater._wait_for_exit(424242, 0)


def test_locked_copy_obeys_one_deadline(app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "file"
    src.write_bytes(b"new")
    def locked(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("locked")
    monkeypatch.setattr(updater.shutil, "copy2", locked)
    with pytest.raises(updater.UpdateError, match="deadline|timed out"):
        updater._copy(str(src), str(app / "file"), lambda text: None, deadline=0)


def test_worker_unexpected_exception_always_completes_and_holds_claim(app: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("wx")
    from unclefarmer.gui.mainparts import updates
    from test_built_copy import Started, Window, REL
    frame = Window()
    completed: list[tuple[bool, Exception | None]] = []
    monkeypatch.setattr(updates.wx, "ProgressDialog", returning(object()))
    monkeypatch.setattr(updates.threading, "Thread", Started)
    def call_after(callback: Callable[..., Any], *args: Any) -> None:
        callback(*args)
    monkeypatch.setattr(updates.wx, "CallAfter", call_after)
    def boom(*args: Any) -> Any:
        raise RuntimeError("unexpected worker failure")
    monkeypatch.setattr(updater, "download", boom)
    def done(dlg: Any, rel: Any, folder: Any, error: Exception | None, *args: Any) -> None:
        completed.append((updater.busy() is not None, error))
        for claim in args:
            claim.__exit__(None, None, None)
    monkeypatch.setattr(frame, "_update_done", done, raising=False)
    updates.UpdatesMixin._download_update(frame, REL)  # type: ignore[arg-type]
    assert len(completed) == 1 and isinstance(completed[0][1], RuntimeError)
    assert completed[0][0]


@pytest.mark.parametrize("close_allowed", [False, True])
def test_paused_copier_never_activates_on_close_veto(app: Path, monkeypatch: pytest.MonkeyPatch,
                                                  close_allowed: bool) -> None:
    pytest.importorskip("wx")
    from unclefarmer.gui.mainparts import updates
    from test_built_copy import Window, REL
    frame = Window()
    events: list[str] = []
    def close() -> bool:
        events.append("close")
        frame.destroyed = close_allowed
        return close_allowed
    from test_built_copy import Started
    def call_after(callback: Callable[..., Any], *args: Any) -> None:
        callback(*args)
    # start_apply runs on a worker that hands back through wx.CallAfter: both run at once here
    monkeypatch.setattr(updates.threading, "Thread", Started)
    monkeypatch.setattr(updates.wx, "CallAfter", call_after)
    monkeypatch.setattr(frame, "Close", close, raising=False)
    handle = types.SimpleNamespace(cancel=lambda: events.append("cancel"))
    monkeypatch.setattr(updates.agentfix, "running_pids", returning([]))
    monkeypatch.setattr(updates, "error", returning(None))
    monkeypatch.setattr(updater, "start_apply", returning(handle))
    def schedule(h: Any) -> None:
        events.append("schedule")
    monkeypatch.setattr(updater, "schedule_apply", schedule, raising=False)
    dialog = types.SimpleNamespace(Destroy=lambda: events.append("destroy dialog"))
    frame._update_done(dialog, REL, "stage", None)
    assert events == ["destroy dialog", "close", "schedule" if close_allowed else "cancel"]


def test_game_started_during_download_blocks_application(app: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("wx")
    from unclefarmer.gui.mainparts import updates
    from test_built_copy import Window, REL
    frame = Window()
    monkeypatch.setattr(updates.agentfix, "running_pids", lambda: [123])
    monkeypatch.setattr(updates, "ask_ok", returning(False))
    monkeypatch.setattr(updates, "error", returning(None))
    def forbidden(*args: Any) -> Any:
        pytest.fail("a game is still open")
    monkeypatch.setattr(updater, "start_apply", forbidden)
    frame._update_done(types.SimpleNamespace(Destroy=lambda: None), REL, "stage", None)


@pytest.fixture
def copier(app: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[list[str]]]:
    executable = (app / updater.EXE).read_bytes()
    flat(app, "old")
    (app / updater.EXE).write_bytes(executable)
    updater.write_build(str(app), "0.4.0", "master")
    src = flat(app / "update/new/Uncle Farmer")
    monkeypatch.setattr(updater.sys, "executable", str(src / updater.EXE))
    monkeypatch.setattr(updater, "frozen", returning(True))
    monkeypatch.setattr(updater, "_wait_for_exit", returning(None))
    monkeypatch.setattr(updater, "_alive", returning(False))
    launches: list[list[str]] = []
    def launch(args: list[str], **kwargs: Any) -> Any:
        launches.append(args)
        return types.SimpleNamespace(pid=12345, poll=returning(None), terminate=returning(None))
    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    return src, launches


@pytest.mark.parametrize("arguments", [[], ["app"], ["app", "x", "0.5.0"], ["app", "4", "invalid"]])
def test_invalid_helper_arguments_report_failure_and_attempt_recovery(
    app: Path, copier: tuple[Path, list[list[str]]], arguments: list[str],
) -> None:
    updater.run_apply(arguments)
    result = updater.json.loads((app / "update/result.json").read_bytes())
    assert result["ok"] is False and result["error"]
    assert copier[1] == [[str(app / updater.EXE)]]


def test_source_execution_never_copies_python_environment(app: Path, copier: Any,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "frozen", returning(False))
    updater.run_apply([str(app), "4242", "0.5.0"])
    assert (app / updater.EXE).read_bytes() == b"old executable"
    assert "source" in updater.json.loads((app / "update/result.json").read_bytes())["error"]


def test_helper_rejects_other_destination_without_writing_there(app: Path, copier: Any, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / updater.EXE).write_bytes(b"unrelated")
    updater.run_apply([str(other), "4242", "0.5.0"])
    assert snapshot(other) == {updater.EXE: b"unrelated"}
    assert updater.json.loads((app / "update/result.json").read_bytes())["ok"] is False


def test_helper_checks_old_process_again_before_apply(app: Path, copier: Any,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "_alive", returning(True))
    updater.run_apply([str(app), "4242", "0.5.0"])
    assert (app / updater.EXE).read_bytes() == b"old executable"
    assert "still running" in updater.json.loads((app / "update/result.json").read_bytes())["error"]
    assert copier[1] == []


def test_result_write_failure_does_not_prevent_restart(app: Path, copier: Any,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    save = updater.json.save
    def fail_result(path: str, data: Any, **kwargs: Any) -> None:
        if path.endswith("result.json"):
            raise OSError("result disk failure")
        save(path, data, **kwargs)
    monkeypatch.setattr(updater.json, "save", fail_result)
    updater.run_apply([str(app), "4242", "0.5.0"])
    assert copier[1] == [[str(app / updater.EXE)]]
    assert "FAILED to write update result" in (app / "logs/update.log").read_text(encoding="utf-8")


def test_restart_failure_is_reported_without_escaping(app: Path, copier: Any,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    def failed(*args: Any, **kwargs: Any) -> Any:
        raise OSError("restart refused")
    monkeypatch.setattr(updater.subprocess, "Popen", failed)
    updater.run_apply([str(app), "4242", "0.5.0"])
    result = updater.json.loads((app / "update/result.json").read_bytes())
    assert result["ok"] is False and "restart refused" in result["error"]


def test_directory_creation_failure_still_attempts_restart(app: Path, copier: Any,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    makedirs = updater.os.makedirs
    def fail_update(path: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(path) == app / "update":
            raise OSError("cannot create staging directory")
        return makedirs(path, *args, **kwargs)
    monkeypatch.setattr(updater.os, "makedirs", fail_update)
    updater.run_apply([str(app), "4242", "0.5.0"])
    assert copier[1] == [[str(app / updater.EXE)]]
    assert "cannot create staging directory" in (app / "logs/update.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("mode", ["ready", "exit", "no signal", "wrong token", "popen error"])
def test_copier_startup_handshake(app: Path, copier: Any, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    src, _launches = copier
    stopped: list[bool] = []
    now = [0.0]
    def time_now() -> float:
        return now[0]
    def sleep(seconds: float) -> None:
        now[0] += seconds
    monkeypatch.setattr(updater.time, "monotonic", time_now)
    monkeypatch.setattr(updater.time, "sleep", sleep)
    process = types.SimpleNamespace(pid=12345, poll=returning(1 if mode == "exit" else None),
                                    terminate=lambda: stopped.append(True))
    def start(args: list[str], **kwargs: Any) -> Any:
        if mode == "popen error":
            raise OSError("could not launch")
        if mode in ("ready", "wrong token"):
            updater.json.save(str(app / "update/busy.json"),
                              dict(pid=12345, token=args[-1] if mode == "ready" else "wrong"))
        return process
    monkeypatch.setattr(updater.subprocess, "Popen", start)
    if mode == "ready":
        handle = updater.start_apply(str(src), "0.5.0")
        assert not (app / "update/apply.json").exists()
        handle.activate()
        assert updater.json.loads((app / "update/apply.json").read_bytes())["token"] == handle.token
    else:
        with pytest.raises(OSError):
            updater.start_apply(str(src), "0.5.0")
        assert not (app / "update/apply.json").exists()
        if mode in ("no signal", "wrong token"):
            assert stopped


def test_total_deadline_during_later_replacement_rolls_back(app: Path, tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    flat(app, "old")
    src = flat(tmp_path / "release")
    before = snapshot(app)
    now = [0.0]
    def time_now() -> float:
        return now[0]
    monkeypatch.setattr(updater.time, "monotonic", time_now)
    original = updater.recovery.move
    count = 0
    def replace(source: Any, target: Any) -> None:
        nonlocal count
        original(source, target)
        if Path(target).parent == app:
            count += 1
            if count == 3:
                now[0] = 20
    monkeypatch.setattr(updater.recovery, "move", replace)
    with pytest.raises(updater.UpdateError, match="timed out"):
        updater.apply(str(app), str(src), lambda text: None, deadline=10)
    assert snapshot(app) == before


def test_missing_runtime_or_build_version_never_modifies_install(app: Path, tmp_path: Path) -> None:
    src = flat(tmp_path / "release")
    (src / "python3314.dll").unlink()
    with pytest.raises(updater.UpdateError, match="required entry"):
        updater.apply(str(app), str(src), lambda text: None)
    assert (app / updater.EXE).read_bytes() == b"old executable"


@pytest.mark.parametrize("folder_first", [True, False])
def test_type_change_is_restored_after_a_later_failure(app: Path, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch, folder_first: bool) -> None:
    flat(app, "old")
    src = flat(tmp_path / "release")
    old, new = app / "changed", src / "changed"
    folder, file = (old, new) if folder_first else (new, old)
    folder.mkdir()
    (folder / "item").write_bytes(b"nested")
    file.write_bytes(b"plain")
    before = snapshot(app)
    original = updater.recovery.move
    failed = False
    def replace(source: Any, target: Any) -> None:
        nonlocal failed
        if Path(target) == app / updater.recovery.PAYLOAD and not failed:
            failed = True
            raise OSError("late failure after type change")
        original(source, target)
    monkeypatch.setattr(updater.recovery, "move", replace)
    with pytest.raises(OSError, match="late failure"):
        updater.apply(str(app), str(src), lambda text: None)
    assert snapshot(app) == before
    assert old.is_dir() is folder_first


def test_backup_exists_until_both_commit_files_are_installed(app: Path, tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    flat(app, "old")
    src = flat(tmp_path / "release")
    original = updater.recovery.move
    observed: list[bool] = []
    def replace(source: Any, target: Any) -> None:
        if Path(target) == app / updater.BUILD_FILE:
            old = Path(source).parent.parent / "old"
            observed.append((old / updater.recovery.PAYLOAD).read_bytes() == b"old")
        original(source, target)
    monkeypatch.setattr(updater.recovery, "move", replace)
    updater.apply(str(app), str(src), lambda text: None)
    assert observed == [True]
    assert not list(tmp_path.glob(".unclefarmer-update-*"))


@pytest.mark.parametrize("wait_status", [0, 0xFFFFFFFF])
def test_process_wait_checks_result_and_exit_status(monkeypatch: pytest.MonkeyPatch, wait_status: int) -> None:
    closed: list[bool] = []
    def close(handle: Any) -> None:
        closed.append(True)
    kernel = types.SimpleNamespace(OpenProcess=returning(42), WaitForSingleObject=returning(wait_status),
                                   CloseHandle=close)
    monkeypatch.setattr(updater.ctypes, "windll", types.SimpleNamespace(kernel32=kernel))
    monkeypatch.setattr(updater, "_alive", returning(False))
    if wait_status == 0:
        updater._wait_for_exit(4242, 0)
    else:
        with pytest.raises(updater.UpdateError, match="cannot check"):
            updater._wait_for_exit(4242, 0)
    assert closed == [True]


def test_multiple_locked_files_share_the_same_deadline(app: Path, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    flat(app, "old")
    src = flat(tmp_path / "release")
    before = snapshot(app)
    now = [0.0]
    attempted: set[str] = set()
    original = updater.shutil.copy2
    def clock() -> float:
        return now[0]
    def sleep(seconds: float) -> None:
        now[0] += seconds
    def copy(source: str, target: str, **kwargs: Any) -> Any:
        if source not in attempted:
            attempted.add(source)
            raise PermissionError("temporary scanner lock")
        return original(source, target, **kwargs)
    monkeypatch.setattr(updater.time, "monotonic", clock)
    monkeypatch.setattr(updater.time, "sleep", sleep)
    monkeypatch.setattr(updater.shutil, "copy2", copy)
    with pytest.raises(updater.UpdateError, match="timed out"):
        updater.apply(str(app), str(src), lambda text: None, deadline=7)
    assert len(attempted) >= 3 and snapshot(app) == before


def test_mixed_case_never_entries_are_not_installed(app: Path, tmp_path: Path) -> None:
    src = flat(tmp_path / "release")
    (app / "Profiles").mkdir()
    (app / "Profiles/hero.json").write_bytes(b"my profile")
    (app / "Settings.JSON").write_bytes(b"my settings")
    (src / "PROFILES").mkdir()
    (src / "PROFILES/hero.json").write_bytes(b"release profile")
    (src / "SETTINGS.json").write_bytes(b"release settings")
    updater.apply(str(app), str(src), lambda text: None)
    assert (app / "Profiles/hero.json").read_bytes() == b"my profile"
    assert (app / "Settings.JSON").read_bytes() == b"my settings"


def test_worker_failure_closes_progress_and_resets_state(app: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("wx")
    from unclefarmer.gui.mainparts import updates
    from test_built_copy import Started, Window, REL
    frame = Window()
    closed: list[bool] = []
    errors: list[str] = []
    dialog = types.SimpleNamespace(Destroy=lambda: closed.append(True))
    monkeypatch.setattr(updates.wx, "ProgressDialog", returning(dialog))
    monkeypatch.setattr(updates.threading, "Thread", Started)
    def call_after(callback: Callable[..., Any], *args: Any) -> None:
        callback(*args)
    def failed(*args: Any) -> Any:
        raise RuntimeError("unexpected download failure")
    def error(parent: Any, message: str, *args: Any) -> None:
        errors.append(message)
    monkeypatch.setattr(updates.wx, "CallAfter", call_after)
    monkeypatch.setattr(updates, "error", error)
    monkeypatch.setattr(updater, "download", failed)
    updates.UpdatesMixin._download_update(frame, REL)  # type: ignore[arg-type]
    assert not frame.updating and closed == [True]
    assert errors and "unexpected download failure" in errors[0]
    assert updater.busy() is None and updater._claims == 0


def test_claim_survives_successful_unpack_until_completion_callback(app: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("wx")
    from unclefarmer.gui.mainparts import updates
    from test_built_copy import Started, Window, REL
    frame = Window()
    callbacks: list[Callable[[], None]] = []
    monkeypatch.setattr(updates.wx, "ProgressDialog", returning(object()))
    monkeypatch.setattr(updates.threading, "Thread", Started)
    monkeypatch.setattr(updater, "download", returning("download.zip"))
    monkeypatch.setattr(updater, "unpack", returning("stage"))
    def queue(callback: Callable[..., Any], *args: Any) -> None:
        if callback.__name__ == "done":
            callbacks.append(lambda: callback(*args))
    def done(dlg: Any, rel: Any, folder: Any, err: Any, claim: Any) -> None:
        assert updater.clean(wait=0) is False
        claim.__exit__(None, None, None)
    monkeypatch.setattr(updates.wx, "CallAfter", queue)
    monkeypatch.setattr(frame, "_update_done", done, raising=False)
    updates.UpdatesMixin._download_update(frame, REL)  # type: ignore[arg-type]
    assert updater.busy() is not None and updater.clean(wait=0) is False
    assert len(callbacks) == 1
    callbacks[0]()
    assert updater._claims == 0 and updater.clean()


def test_copier_activation_failure_recovers_after_shutdown(app: Path, copier: Any) -> None:
    process = types.SimpleNamespace(poll=returning(1))
    handle = updater.ApplyProcess(cast("Popen[bytes]", process), str(app / "update"),
                                   str(app), "a" * 32, "0.5.0")
    updater.schedule_apply(handle)
    updater.finish_apply()
    assert copier[1] == [[str(app / updater.EXE)]]
    result = updater.json.loads((app / "update/result.json").read_bytes())
    assert result["ok"] is False and "exited" in result["error"]


@pytest.mark.parametrize("bad_version", ["0.4.0", "bad"])
def test_staged_build_metadata_must_match_offer(app: Path, copier: Any, bad_version: str) -> None:
    src, _ = copier
    updater.write_build(str(src), bad_version, "master")
    updater.run_apply([str(app), "4242", "0.5.0"])
    result = updater.json.loads((app / "update/result.json").read_bytes())
    assert result["ok"] is False and "build.json" in result["error"]
    assert (app / updater.EXE).read_bytes() == b"old executable"


def test_cleanup_rechecks_staging_after_waiting(app: Path, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    stage = app / "update"
    stage.mkdir()
    (stage / "busy.json").write_text(updater.json.dumps(dict(pid=12345, what="copy", since=updater.time.time())),
                                     encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"private")
    monkeypatch.setattr(updater, "_alive", returning(True))
    def wait(pid: int, seconds: float = 120) -> None:
        (stage / "busy.json").unlink()
        stage.rmdir()
        junction(stage, outside)
    monkeypatch.setattr(updater, "_wait_for_exit", wait)
    assert updater.clean(wait=1) is False
    assert snapshot(outside) == {"keep": b"private"}


def test_expired_helper_authorization_never_copies(app: Path, copier: Any,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]
    def clock() -> float:
        return now[0]
    def sleep(seconds: float) -> None:
        now[0] += seconds
    monkeypatch.setattr(updater.time, "monotonic", clock)
    monkeypatch.setattr(updater.time, "sleep", sleep)
    updater.run_apply([str(app), "4242", "0.5.0", "a" * 32])
    result = updater.json.loads((app / "update/result.json").read_bytes())
    assert result["ok"] is False and "authorize" in result["error"]
    assert (app / updater.EXE).read_bytes() == b"old executable"


def test_incomplete_rollback_retains_backup_and_never_launches_mixed_install(
    app: Path, copier: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(*args: Any, **kwargs: Any) -> Any:
        raise updater.RollbackError("rollback incomplete; backup retained for manual recovery")
    monkeypatch.setattr(updater, "apply", failed)
    updater.run_apply([str(app), "4242", "0.5.0"])
    result = updater.json.loads((app / "update/result.json").read_bytes())
    assert result["ok"] is False and "rollback incomplete" in result["error"]
    assert copier[1] == []


def test_failed_restore_keeps_old_runtime_backup(app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    flat(app, "old")
    src = flat(tmp_path / "release")
    original = updater.recovery.move
    failed = False
    def replace(source: Any, target: Any) -> None:
        nonlocal failed
        if Path(target) == app / updater.recovery.PAYLOAD and not failed:
            failed = True
            raise OSError("installation failed")
        if failed and Path(source).name.startswith("restore-") and Path(target) == app / "agent":
            raise OSError("filesystem also refuses restore")
        original(source, target)
    monkeypatch.setattr(updater.recovery, "move", replace)
    with pytest.raises(updater.RollbackError, match="backup retained"):
        updater.apply(str(app), str(src), lambda text: None)
    (backup,) = (app / updater.recovery.HERE).glob("*/old")
    assert (backup / "agent/unclefarmer-agent.jar").read_bytes() == b"old"
    assert (app / updater.EXE).read_bytes() == b"old"


def test_data_preserves_unshipped_learning_but_removes_known_obsolete_data(app: Path, tmp_path: Path) -> None:
    flat(app, "old")
    (app / "data/obsolete.json").write_bytes(b"old shipped data")
    updater.write_build(str(app), "0.4.0", "master")
    (app / "data/learned").mkdir()
    (app / "data/learned/custom.json").write_bytes(b"private learned data")
    src = flat(tmp_path / "release")
    updater.apply(str(app), str(src), lambda text: None)
    assert (app / "data/learned/custom.json").read_bytes() == b"private learned data"
    assert not (app / "data/obsolete.json").exists()


def test_required_dll_cannot_be_a_directory(app: Path, tmp_path: Path) -> None:
    src = flat(tmp_path / "release")
    (src / "python3314.dll").unlink()
    (src / "python3314.dll").mkdir()
    with pytest.raises(updater.UpdateError, match="required entry"):
        updater.apply(str(app), str(src), lambda text: None)


BOOTSTRAP = b"MZ native bootstrap " + updater.BOOTSTRAP_MARKER
OLD_FRONT_DOOR = b"MZ native front door " + updater.LEGACY_LAUNCHER_MARKER


def release_with_bootstrap(tmp_path: Path) -> Path:
    src = flat(tmp_path / "release")
    (src / updater.BOOTSTRAP).write_bytes(BOOTSTRAP)
    return src


def test_names_are_the_app_and_the_bootstrap() -> None:
    assert updater.EXE == updater.recovery.PAYLOAD == "UncleFarmer.exe"
    assert updater.BOOTSTRAP == updater.recovery.BOOTSTRAP == "bootstrap.exe"
    assert updater.BOOTSTRAP in updater.NEVER and updater.EXE.casefold() not in updater.NEVER
    assert updater.BOOTSTRAP in updater.recovery.PROTECTED and "unclefarmer.exe" not in updater.recovery.PROTECTED


def test_a_copy_without_a_bootstrap_gets_the_new_app_and_the_bootstrap(app: Path, tmp_path: Path) -> None:
    """A copy from before bootstrap.exe (1.1.0 and older): its Nuitka app is UncleFarmer.exe, and stays so."""
    flat(app, "old")
    (app / updater.BOOTSTRAP).unlink()
    src = release_with_bootstrap(tmp_path)
    updater.apply(str(app), str(src), lambda text: None)
    assert (app / updater.EXE).read_bytes() == b"new"
    assert (app / updater.BOOTSTRAP).read_bytes() == BOOTSTRAP
    assert not list((app / updater.recovery.HERE).glob("bootstrap-*.tmp"))


def test_the_front_door_layout_gets_its_app_back_as_unclefarmer_exe(app: Path, tmp_path: Path) -> None:
    """A dev copy of 2026-11-14: UncleFarmer.exe was the front door, UncleFarmerApp.exe the app."""
    flat(app, "old")
    (app / updater.BOOTSTRAP).unlink()
    (app / updater.EXE).write_bytes(OLD_FRONT_DOOR)
    (app / updater.LEGACY_APP).write_bytes(b"old nuitka app")
    src = release_with_bootstrap(tmp_path)
    updater.apply(str(app), str(src), lambda text: None)
    assert (app / updater.EXE).read_bytes() == b"new"
    assert (app / updater.BOOTSTRAP).read_bytes() == BOOTSTRAP
    assert not (app / updater.LEGACY_APP).exists()


def test_an_installed_bootstrap_is_never_replaced(app: Path, tmp_path: Path) -> None:
    flat(app, "old")
    mine = b"MZ an older build of the same protocol " + updater.BOOTSTRAP_MARKER
    (app / updater.BOOTSTRAP).write_bytes(mine)
    src = release_with_bootstrap(tmp_path)
    updater.apply(str(app), str(src), lambda text: None)
    assert (app / updater.BOOTSTRAP).read_bytes() == mine
    assert (app / updater.EXE).read_bytes() == b"new"


def test_a_rolled_back_update_installs_no_bootstrap(app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    flat(app, "old")
    (app / updater.BOOTSTRAP).unlink()
    (app / updater.EXE).write_bytes(b"old nuitka app")
    src = release_with_bootstrap(tmp_path)
    def fail(*args: object, **kwargs: object) -> None:
        raise updater.UpdateError("injected")
    monkeypatch.setattr(updater, "_install_tree", fail)
    with pytest.raises(updater.UpdateError):
        updater.apply(str(app), str(src), lambda text: None)
    assert (app / updater.EXE).read_bytes() == b"old nuitka app"
    assert not (app / updater.BOOTSTRAP).exists()


def test_a_bootstrap_that_cannot_be_installed_never_fails_the_update(app: Path, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    flat(app, "old")
    (app / updater.BOOTSTRAP).unlink()
    src = release_with_bootstrap(tmp_path)
    def refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError("held")
    monkeypatch.setattr(updater.shutil, "copyfile", refuse)
    lines: list[str] = []
    updater.apply(str(app), str(src), lines.append)
    assert (app / updater.EXE).read_bytes() == b"new"
    assert not (app / updater.BOOTSTRAP).exists()
    assert any("could not install bootstrap.exe" in line for line in lines)
    durable = updater.recovery.load(str(app))
    assert durable is not None and durable["state"] == "COMMITTED"


def test_the_built_bootstrap_carries_the_marker() -> None:
    source = (Path(updater.__file__).parent / "native" / "bootstrap.c").read_text(encoding="utf-8")
    assert '"%s"' % updater.BOOTSTRAP_MARKER.decode() in source and "__attribute__((used))" in source
    assert 'L"%s"' % updater.EXE in source


def test_the_app_is_too_large_to_pass_for_the_bootstrap(tmp_path: Path) -> None:
    big = tmp_path / "big.exe"
    big.write_bytes(b"\0" * (updater.BOOTSTRAP_MOST + 1) + updater.BOOTSTRAP_MARKER)
    assert not updater.is_bootstrap(str(big))
    assert not updater.is_bootstrap(str(tmp_path / "missing.exe"))


@pytest.mark.parametrize("with_bootstrap", [True, False])
def test_start_apply_hands_the_update_to_the_bootstrap(app: Path, copier: Any, monkeypatch: pytest.MonkeyPatch,
                                                       with_bootstrap: bool) -> None:
    src, _ = copier
    if with_bootstrap:
        (app / updater.BOOTSTRAP).write_bytes(BOOTSTRAP)
    seen: list[tuple[list[str], str]] = []
    def start(args: list[str], **kwargs: Any) -> Any:
        seen.append((args, kwargs["cwd"]))
        # bootstrap.exe's copier names it as its launcher; a copier run alone names itself
        signal: dict[str, Any] = dict(pid=999 if with_bootstrap else 12345, token=args[-1])
        if with_bootstrap:
            signal["launcher"] = 12345
        updater.json.save(str(app / "update/busy.json"), signal)
        return types.SimpleNamespace(pid=12345, poll=returning(None), terminate=returning(None))
    monkeypatch.setattr(updater.subprocess, "Popen", start)
    handle = updater.start_apply(str(src), "0.5.0")
    ((args, cwd),) = seen
    pid = str(updater.os.getpid())
    if with_bootstrap:
        assert args == [str(app / updater.BOOTSTRAP), "--update", str(src / updater.EXE), pid, "0.5.0", handle.token]
        assert cwd == str(app)
    else:
        assert args == [str(src / updater.EXE), "--apply-update", str(app), pid, "0.5.0", handle.token]
        assert cwd == str(src)


def test_a_copier_run_by_the_bootstrap_leaves_the_restart_to_it(app: Path, copier: Any) -> None:
    assert updater.run_apply([str(app), "4242", "0.5.0", "--bootstrap"]) == 0
    assert copier[1] == []          # bootstrap.exe starts the app
    assert updater.json.loads((app / "update/result.json").read_bytes())["ok"] is True
    assert (app / updater.EXE).read_bytes() == b"new"


def test_a_copier_run_by_the_bootstrap_says_so_in_its_signal(app: Path, copier: Any,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    signals: list[Any] = []
    def authorized(update: str, token: str) -> None:
        signals.append(updater.json.loads((Path(update) / "busy.json").read_bytes()))
    monkeypatch.setattr(updater, "_await_activation", authorized)
    monkeypatch.setattr(updater.os, "getppid", returning(777))
    assert updater.run_apply([str(app), "4242", "0.5.0", "a" * 32, "--bootstrap"]) == 0
    assert signals[0]["launcher"] == 777 and signals[0]["token"] == "a" * 32


@pytest.mark.parametrize("trouble", ["old app alive", "rollback incomplete"])
def test_the_bootstrap_is_told_not_to_restart(app: Path, copier: Any, monkeypatch: pytest.MonkeyPatch,
                                              trouble: str) -> None:
    arguments = [str(app), "4242", "0.5.0", "--bootstrap"]
    if trouble == "old app alive":
        monkeypatch.setattr(updater, "_alive", returning(True))
    elif trouble == "rollback incomplete":
        def failed(*args: Any, **kwargs: Any) -> Any:
            raise updater.RollbackError("rollback incomplete")
        monkeypatch.setattr(updater, "apply", failed)
    assert updater.run_apply(arguments) == updater.RESTART_WITHHELD
    assert copier[1] == []


def test_a_failed_update_still_lets_the_bootstrap_start_the_old_app(app: Path, copier: Any,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    def failed(*args: Any, **kwargs: Any) -> Any:
        raise updater.UpdateError("disk full")
    monkeypatch.setattr(updater, "apply", failed)
    assert updater.run_apply([str(app), "4242", "0.5.0", "--bootstrap"]) == 0
    assert "disk full" in updater.json.loads((app / "update/result.json").read_bytes())["error"]


def test_a_copier_run_alone_restarts_through_the_bootstrap_it_installed(app: Path, copier: Any) -> None:
    src, launches = copier
    (src / updater.BOOTSTRAP).write_bytes(BOOTSTRAP)
    (app / updater.BOOTSTRAP).unlink()
    assert updater.run_apply([str(app), "4242", "0.5.0"]) == 0
    assert (app / updater.BOOTSTRAP).read_bytes() == BOOTSTRAP
    assert launches == [[str(app / updater.BOOTSTRAP), "--relaunch", str(updater.os.getpid())]]


def test_a_legacy_recovery_runtime_is_renewed(app: Path) -> None:
    flat(app, "old")
    root = app / updater.recovery.HERE
    runtime = root / "runtime"
    runtime.mkdir(parents=True)
    (runtime / updater.EXE).write_bytes(OLD_FRONT_DOOR)
    (runtime / updater.LEGACY_APP).write_bytes(b"old nuitka app")
    updater.json.save(str(root / "runtime-manifest.json"), updater.recovery.fingerprint(str(runtime)))
    updater.ensure_recovery_runtime(str(app), str(app))
    assert (runtime / updater.EXE).read_bytes() == b"old"
    assert not (runtime / updater.LEGACY_APP).exists()
    assert not (runtime / updater.BOOTSTRAP).exists()
    assert not list(root.glob("runtime-preparing-*"))
    assert updater.json.loads((root / "runtime-manifest.json").read_bytes()) == updater.recovery.fingerprint(str(runtime))


def test_a_journal_from_the_front_door_layout_still_validates(app: Path) -> None:
    record = updater.recovery.begin(str(app), "0.4.0", "0.5.0")
    record["protected"] = [".recovery", "agent/run", "data/game_texts.json", "logs", "messages", "plans", "profiles",
                           "results", "schedules.json", "scripts", "settings.json", "survey", "unclefarmer.exe",
                           "update"]
    updater.recovery.save(str(app), record, "ROLLED_BACK", "legacy")
    loaded = updater.recovery.load(str(app))
    assert loaded is not None and loaded["state"] == "ROLLED_BACK"


def test_handshake_files_survive_a_reader_holding_them_open(app: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Found by the real-binary test: the app reads busy.json every 50 ms, and Windows refuses to
    replace a file while it is open, so the copier failed with Access is denied."""
    save = updater.json.save
    refusals = [PermissionError(5, "Access is denied")] * 3
    def busy_reader(path: str, data: Any, **kwargs: Any) -> None:
        if refusals:
            raise refusals.pop()
        save(path, data, **kwargs)
    monkeypatch.setattr(updater.json, "save", busy_reader)
    target = app / "busy.json"
    updater._save_shared(str(target), {"pid": 1})
    assert updater.json.loads(target.read_bytes()) == {"pid": 1} and not refusals
    refusals[:] = [PermissionError(5, "Access is denied")] * 1000
    with pytest.raises(PermissionError):
        updater._save_shared(str(target), {"pid": 2}, seconds=0.1)


def test_a_real_reader_does_not_break_the_handshake(app: Path) -> None:
    import threading
    target = app / "update" / "busy.json"
    target.parent.mkdir()
    updater.json.save(str(target), {"n": 0})
    stop = threading.Event()
    def poll() -> None:
        while not stop.is_set():
            try:
                with open(target, encoding="utf-8") as f:
                    f.read()
            except OSError:
                pass
    reader = threading.Thread(target=poll)
    reader.start()
    try:
        for n in range(200):
            updater._save_shared(str(target), {"n": n})
    finally:
        stop.set()
        reader.join()
    assert updater.json.loads(target.read_bytes()) == {"n": 199}
