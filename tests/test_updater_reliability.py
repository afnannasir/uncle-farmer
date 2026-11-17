# pyright: reportPrivateUsage=false
"""The updater's reliability fixes: cut GitHub answers, busy.json identity and cleanup, the
journal lock and its bounded replace, the journal's progress lists, the bootstrap hand-off,
literal install paths, version shapes, digests, archive limits and read-only files.

Everything runs in the test's own temporary folders; processes are real where identity matters."""
from __future__ import annotations

import ctypes
import http.client
import os
import stat
import subprocess
import sys
import threading
import types
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import updater, update_recovery as recovery
from conftest import failing, returning
from test_updater import Resp, fake_open, release
from test_updater_hardening import flat, snapshot

windows = pytest.mark.skipif(os.name != "nt", reason="Windows process identity and NTFS")
PYTHON = getattr(sys, "_base_executable", sys.executable)


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
    monkeypatch.setattr(updater, "LOG", str(folder / "logs/update.log"))
    monkeypatch.setattr(updater, "_claims", 0)
    return folder


def update_log(app: Path) -> str:
    path = app / "logs/update.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_busy(app: Path, **record: Any) -> Path:
    path = app / "update" / updater.BUSY
    path.parent.mkdir(parents=True, exist_ok=True)
    updater.json.save(str(path), {"what": "download", "since": updater.time.time(), **record})
    return path


def stamp_of(pid: int) -> int:
    """A running process's creation time, as update_recovery.owner_alive reads it."""
    kernel: Any = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    assert handle
    try:
        return recovery.process_stamp(handle)
    finally:
        kernel.CloseHandle(handle)


@pytest.fixture
def other() -> Iterator[subprocess.Popen[bytes]]:
    """Another live process (a sleeping Python), ended after the test."""
    child = subprocess.Popen([PYTHON, "-c", "import time; time.sleep(60)"])
    try:
        yield child
    finally:
        child.kill()
        child.wait()


def dead_pid() -> int:
    child = subprocess.Popen([PYTHON, "-c", "pass"])
    child.wait()
    return child.pid


# ------------------------------------------------------------------ 1. a cut answer from GitHub
class CutResp(Resp):
    """A response whose body stops early: http.client raises IncompleteRead while it is read."""

    def read(self, n: int | None = -1) -> bytes:
        raise http.client.IncompleteRead(b'{"tag_name": "v9', 120)


@pytest.mark.parametrize("problem", [http.client.IncompleteRead(b"{", 10), http.client.RemoteDisconnected("gone"),
                                     http.client.BadStatusLine("x")])
def test_a_cut_answer_is_an_update_error_with_its_cause(monkeypatch: pytest.MonkeyPatch,
                                                         problem: http.client.HTTPException) -> None:
    def cut(url: str, accept: str, timeout: float) -> Any:
        raise problem
    monkeypatch.setattr(updater, "_open", cut)
    with pytest.raises(updater.UpdateError) as caught:
        updater._ask("https://api.github.com/x", 1)
    assert caught.value.__cause__ is problem


def test_incomplete_read_while_the_json_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_open(monkeypatch, CutResp())
    with pytest.raises(updater.UpdateError) as caught:
        updater._ask("https://api.github.com/x", 1)
    assert isinstance(caught.value.__cause__, http.client.IncompleteRead)


@pytest.mark.parametrize("update_channel", ["master", "dev"])
def test_check_survives_a_cut_answer_and_logs_it(app: Path, monkeypatch: pytest.MonkeyPatch,
                                                 update_channel: str) -> None:
    monkeypatch.setattr(updater, "REPO", "owner/repo")
    fake_open(monkeypatch, CutResp())
    assert updater.check(update_channel=update_channel) is None      # nothing escapes the check
    assert "update check (%s) failed" % update_channel in update_log(app)


def test_the_background_check_thread_ends_normally_on_a_cut_answer(app: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "REPO", "owner/repo")
    fake_open(monkeypatch, CutResp())
    escaped: list[object] = []
    def hook(args: object) -> None:
        escaped.append(args)
    monkeypatch.setattr(threading, "excepthook", hook)
    worker = threading.Thread(target=updater.check)
    worker.start()
    worker.join(10)
    assert not worker.is_alive() and escaped == []


# ------------------------------------------------------------------ 2. start_apply off the window's thread
class Gate:
    """updater.start_apply on a worker: says which thread ran it, and can be held until let go."""

    def __init__(self, handle: Any = None, fails: Exception | None = None) -> None:
        self.handle = handle or types.SimpleNamespace(cancel=self.cancelled, process=types.SimpleNamespace(poll=returning(None)))
        self.fails = fails
        self.threads: list[threading.Thread] = []
        self.go = threading.Event()
        self.go.set()
        self.cancels = 0

    def cancelled(self) -> None:
        self.cancels += 1

    def __call__(self, folder: str, version: str) -> Any:
        self.threads.append(threading.current_thread())
        assert self.go.wait(10)
        if self.fails is not None:
            raise self.fails
        return self.handle


def Window() -> Any:
    """The update part of the main window, with the rest stood in; say() is written down."""
    pytest.importorskip("wx")
    from test_built_copy import Window as Base
    frame: Any = Base()                 # its say() writes into frame.said
    frame.preparing_update = False
    frame.pending_update = None
    return frame


@pytest.fixture
def ui(app: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """wx.CallAfter queues callbacks for the test to run as the window's thread would."""
    pytest.importorskip("wx")
    from unclefarmer.gui.mainparts import updates
    queued: list[tuple[Callable[..., Any], tuple[Any, ...], threading.Thread]] = []
    arrived = threading.Event()
    def call_after(callback: Callable[..., Any], *args: Any) -> None:
        queued.append((callback, args, threading.current_thread()))
        arrived.set()
    monkeypatch.setattr(updates.wx, "CallAfter", call_after)
    monkeypatch.setattr(updates.agentfix, "running_pids", returning([]))
    errors: list[str] = []
    def error(parent: object, message: str, *more: object) -> None:
        errors.append(message)
    monkeypatch.setattr(updates, "error", error)
    scheduled: list[Any] = []
    monkeypatch.setattr(updater, "schedule_apply", scheduled.append)
    yield {"queued": queued, "arrived": arrived, "errors": errors, "scheduled": scheduled}
    for thread in threading.enumerate():
        if thread.name.startswith("Thread") and thread is not threading.current_thread():
            thread.join(5)


def run_queued(ui: dict[str, Any]) -> None:
    assert ui["arrived"].wait(10), "the worker never handed its result back"
    callback, args, _thread = ui["queued"].pop(0)
    callback(*args)


def test_start_apply_runs_on_a_worker_and_reports_through_call_after(
        ui: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from test_built_copy import REL
    frame = Window()
    gate = Gate()
    monkeypatch.setattr(updater, "start_apply", gate)
    closed: list[bool] = []
    def close() -> bool:
        closed.append(True)
        frame.destroyed = True
        return True
    frame.Close = close
    frame._apply_downloaded_update(REL, "stage", None)
    run_queued(ui)
    assert gate.threads and gate.threads[0] is not threading.main_thread()
    assert ui["queued"] == [] and closed == [True]
    assert ui["scheduled"] == [gate.handle] and gate.cancels == 0
    assert not frame.updating and not frame.preparing_update and frame.pending_update is None


def test_the_window_stays_free_and_refuses_a_second_update_while_preparing(
        ui: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from test_built_copy import REL
    frame = Window()
    gate = Gate()
    gate.go.clear()                                   # start_apply hangs, like a slow copier start
    monkeypatch.setattr(updater, "start_apply", gate)
    monkeypatch.setattr(updater, "frozen", returning(True))
    frame.Close = returning(False)
    frame._apply_downloaded_update(REL, "stage", None)      # returns at once: the window is free
    assert frame.preparing_update and frame.updating
    assert frame.said and "Preparing the update" in frame.said[0]
    again = updater.claimed("update")
    again.__enter__()
    frame._apply_downloaded_update(REL, "stage", again)     # pressed again: nothing new starts
    assert frame.preparing_update and frame.updating      # and the one under way keeps its state
    assert updater._claims == 0                            # the second call's claim was let go
    frame.install_update(REL)
    assert frame.downloads == []
    gate.go.set()
    run_queued(ui)
    assert len(gate.threads) == 1
    assert gate.cancels == 1                          # closing was refused: the copier is cancelled
    assert not frame.preparing_update and not frame.updating


def test_a_start_apply_failure_reaches_the_error_path_and_releases_the_claim(
        ui: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from test_built_copy import REL
    frame = Window()
    monkeypatch.setattr(updater, "start_apply", Gate(fails=updater.UpdateError("copier did not start")))
    frame.Close = failing("the window must not close when the update can't start")
    claim = updater.claimed("update")
    claim.__enter__()
    frame._apply_downloaded_update(REL, "stage", claim)
    assert updater.busy() is not None                 # held while the worker prepares
    run_queued(ui)
    assert ui["errors"] and "copier did not start" in ui["errors"][0]
    assert updater._claims == 0 and updater.busy() is None
    assert not frame.updating and not frame.preparing_update


def test_a_window_closed_meanwhile_cancels_the_copier_without_wx(
        ui: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from test_built_copy import REL
    frame = Window()
    gate = Gate()
    gate.go.clear()
    monkeypatch.setattr(updater, "start_apply", gate)
    claim = updater.claimed("update")
    claim.__enter__()
    frame._apply_downloaded_update(REL, "stage", claim)
    frame.destroyed = True                            # Windows ended the session meanwhile
    gate.go.set()
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.name.startswith("Thread"):
            thread.join(10)
    assert ui["queued"] == [] and gate.cancels == 1
    assert updater._claims == 0 and ui["scheduled"] == []


@pytest.mark.parametrize("problem", [RuntimeError("wrapped C/C++ object has been deleted"),
                                     AssertionError("No wx.App created yet")])
def test_call_after_failing_because_the_app_is_gone_cancels_the_copier(
        ui: dict[str, Any], monkeypatch: pytest.MonkeyPatch, problem: Exception) -> None:
    from test_built_copy import REL
    from unclefarmer.gui.mainparts import updates
    frame = Window()
    gate = Gate()
    monkeypatch.setattr(updater, "start_apply", gate)
    def gone(*args: Any) -> None:
        raise problem
    monkeypatch.setattr(updates.wx, "CallAfter", gone)
    monkeypatch.setattr(updates.threading, "Thread", Started)
    frame._apply_downloaded_update(REL, "stage", None)
    assert gate.cancels == 1 and "not applied" in update_log(Path(updater.paths.APP))


def test_a_handle_that_arrives_after_the_window_went_cancels(ui: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from test_built_copy import REL
    frame = Window()
    gate = Gate()
    monkeypatch.setattr(updater, "start_apply", gate)
    frame._apply_downloaded_update(REL, "stage", None)
    assert ui["arrived"].wait(10)
    frame.destroyed = True                            # gone between the hand-back and its running
    run_queued(ui)
    assert gate.cancels == 1 and ui["scheduled"] == []


class Started:
    """threading.Thread run at once on start()."""

    def __init__(self, target: Any, daemon: bool = False, kwargs: dict[str, Any] | None = None) -> None:
        self.target, self.kwargs = target, kwargs or {}

    def start(self) -> None:
        self.target(**self.kwargs)


# ------------------------------------------------------------------ 3 + 4. busy.json: who, and cleanup
@windows
def test_the_written_record_names_this_process_and_its_start(app: Path) -> None:
    updater._mark_busy(updater.UPDATE, "download")
    record = updater.json.loads((app / "update" / updater.BUSY).read_bytes())
    assert record["pid"] == os.getpid() and record["stamp"] == recovery.current_stamp()
    assert updater._busy_record("update copier", token="t")["token"] == "t"


@windows
def test_same_pid_and_same_start_is_the_holder(app: Path, other: subprocess.Popen[bytes]) -> None:
    write_busy(app, pid=other.pid, stamp=stamp_of(other.pid))
    holder = updater.busy()
    assert holder is not None and holder["pid"] == other.pid


@windows
@pytest.mark.parametrize("off_by", [-1, 1, 10 ** 7])
def test_same_pid_but_another_start_is_a_reused_pid(app: Path, other: subprocess.Popen[bytes], off_by: int) -> None:
    write_busy(app, pid=other.pid, stamp=stamp_of(other.pid) + off_by)
    assert updater.busy() is None
    with updater.claimed("download"):                 # a reused PID never blocks a new update
        assert updater.busy() is not None


@windows
def test_a_dead_pid_is_nobody(app: Path) -> None:
    write_busy(app, pid=dead_pid(), stamp=1234)
    assert updater.busy() is None
    write_busy(app, pid=dead_pid())
    assert updater.busy() is None


@windows
def test_an_older_record_without_a_stamp_still_counts_while_its_process_runs(
        app: Path, other: subprocess.Popen[bytes]) -> None:
    write_busy(app, pid=other.pid)
    holder = updater.busy()
    assert holder is not None and holder["pid"] == other.pid
    with pytest.raises(updater.UpdateError, match="another update"):
        with updater.claimed("download"):
            pass


@windows
@pytest.mark.parametrize("stamp", ["123", True, -5, 0, 1.5, None, [1]])
def test_an_unreadable_stamp_falls_back_to_the_pid(app: Path, other: subprocess.Popen[bytes], stamp: object) -> None:
    write_busy(app, pid=other.pid, stamp=stamp)
    assert updater.busy() is not None                 # never taken for a leftover on a bad field
    write_busy(app, pid=dead_pid(), stamp=stamp)
    assert updater.busy() is None


@pytest.mark.parametrize("body", ["{broken", "[]", '"x"', "{}", '{"pid": "x", "since": 1}', '{"pid": 1}'])
def test_a_malformed_record_is_nobody(app: Path, body: str) -> None:
    path = app / "update" / updater.BUSY
    path.parent.mkdir(parents=True)
    path.write_text(body, encoding="utf-8")
    assert updater.busy() is None


@windows
def test_an_inspection_failure_counts_as_running(app: Path, monkeypatch: pytest.MonkeyPatch,
                                                 other: subprocess.Popen[bytes]) -> None:
    def denied(pid: int, stamp: int) -> bool:
        raise recovery.RecoveryError("cannot establish that the transaction owner exited")
    monkeypatch.setattr(recovery, "owner_alive", denied)
    write_busy(app, pid=other.pid, stamp=1)
    assert updater.busy() is not None


@windows
def test_clean_removes_a_leftover_record_of_this_process(app: Path) -> None:
    updater._mark_busy(updater.UPDATE, "download")        # a claim whose release couldn't remove it
    (app / "update/old.zip").write_bytes(b"x")
    assert updater._claims == 0 and updater.busy() is not None
    assert updater.clean() is True
    assert not (app / "update").exists()
    assert "leftover busy record" in update_log(app)


@windows
def test_clean_keeps_this_process_s_record_while_a_claim_is_open(app: Path) -> None:
    with updater.claimed("download"):
        (app / "update/half.zip.part").write_bytes(b"x")
        assert updater.clean() is False
        assert (app / "update" / updater.BUSY).exists() and (app / "update/half.zip.part").exists()
    assert updater.clean() is True


@windows
def test_clean_never_takes_another_live_process_s_folder(app: Path, other: subprocess.Popen[bytes]) -> None:
    write_busy(app, pid=other.pid, stamp=stamp_of(other.pid))
    (app / "update/new").mkdir()
    assert updater.clean() is False and (app / "update/new").exists()
    other.kill()
    other.wait()
    assert updater.clean() is True and not (app / "update").exists()


@windows
def test_clean_takes_a_dead_or_reused_holder_s_folder(app: Path, other: subprocess.Popen[bytes]) -> None:
    write_busy(app, pid=other.pid, stamp=stamp_of(other.pid) - 1)
    assert updater.clean() is True
    write_busy(app, pid=dead_pid(), stamp=99)
    assert updater.clean() is True


@windows
def test_clean_on_another_thread_never_interrupts_a_download(app: Path) -> None:
    with updater.claimed("download"):
        results: list[bool] = []
        worker = threading.Thread(target=lambda: results.append(updater.clean(wait=0)))
        worker.start()
        worker.join(10)
        assert results == [False] and (app / "update" / updater.BUSY).exists()


# ------------------------------------------------------------------ 5. the journal under its lock
@pytest.fixture
def ntfs_app(app: Path) -> Path:
    if os.name != "nt":
        pytest.skip("NTFS journal")
    recovery.ntfs(str(app))
    (app / "update").mkdir()
    (app / "update/old.zip").write_bytes(b"x")
    return app


def lock_is_free(app: Path) -> bool:
    try:
        with recovery.locked(str(app)):
            return True
    except recovery.RecoveryError:
        return False


def test_clean_defers_while_another_holds_the_recovery_lock(ntfs_app: Path) -> None:
    with recovery.locked(str(ntfs_app)):
        assert updater.clean() is False
    assert (ntfs_app / "update/old.zip").exists()
    assert "staging cleanup deferred: another updater or recovery process holds" in update_log(ntfs_app)
    assert updater.clean() is True and lock_is_free(ntfs_app)


def test_clean_defers_while_another_process_holds_it(ntfs_app: Path) -> None:
    script = ("import sys, time; sys.path[:0] = %r\n"
              "from unclefarmer import update_recovery as r\n"
              "with r.locked(%r):\n    print('held', flush=True); time.sleep(30)\n" % (sys.path, str(ntfs_app)))
    child = subprocess.Popen([PYTHON, "-c", script], stdout=subprocess.PIPE)
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == b"held"
        assert updater.clean() is False and (ntfs_app / "update/old.zip").exists()
    finally:
        child.kill()
        child.wait()
    assert updater.clean() is True


def test_clean_releases_the_lock_on_every_path(ntfs_app: Path) -> None:
    app = str(ntfs_app)
    with recovery.locked(app):
        record = recovery.begin(app, "0.4.0", "0.5.0", "DOWNLOADING")
    assert updater.clean() is False and lock_is_free(ntfs_app)      # unfinished: deferred
    assert "recovery is pending" in update_log(ntfs_app)
    with recovery.locked(app):
        recovery.save(app, record, "ROLLED_BACK", "test")
    journal = ntfs_app / recovery.HERE / recovery.JOURNAL
    journal.write_text("{torn", encoding="utf-8")
    (ntfs_app / recovery.HERE / (recovery.JOURNAL + ".prev")).write_text("{torn", encoding="utf-8")
    for plan in (ntfs_app / recovery.HERE).glob("*/plan.json"):
        plan.unlink()
    assert updater.clean() is False and lock_is_free(ntfs_app)      # unreadable: refused
    assert "staging cleanup refused" in update_log(ntfs_app)


def test_clean_with_no_journal_never_creates_the_recovery_folder(app: Path) -> None:
    (app / "update").mkdir()
    assert updater.clean() is True
    assert not (app / recovery.HERE).exists()


def test_concurrent_journal_saves_and_cleanups_never_see_a_torn_journal(ntfs_app: Path) -> None:
    app = str(ntfs_app)
    with recovery.locked(app):
        record = recovery.begin(app, "0.4.0", "0.5.0", "DOWNLOADING")
        recovery.save(app, record, "ROLLED_BACK", "start")
    stop = threading.Event()
    failures: list[BaseException] = []

    def writer() -> None:
        n = 0
        while not stop.is_set():
            try:
                with recovery.locked(app):
                    n += 1
                    recovery.save(app, record, "ROLLED_BACK", "save-%d" % n)
            except recovery.RecoveryError:
                pass                                   # the cleanup held the lock: try again
            except BaseException as error:             # anything else is a real failure
                failures.append(error)
                return
    thread = threading.Thread(target=writer)
    thread.start()
    try:
        results = [updater.clean() for _ in range(40)]
    finally:
        stop.set()
        thread.join(10)
    assert failures == [] and all(isinstance(r, bool) for r in results)
    assert "staging cleanup refused" not in update_log(ntfs_app)
    loaded = recovery.load(app)
    assert loaded is not None and loaded["state"] == "ROLLED_BACK"


def test_a_transient_replace_refusal_is_retried(ntfs_app: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = ntfs_app / "value.json"
    real = os.replace
    calls: list[str] = []
    def refuse_twice(source: str, destination: str) -> None:
        calls.append(source)
        if len(calls) <= 2:
            raise PermissionError(5, "Access is denied")
        real(source, destination)
    monkeypatch.setattr(recovery.os, "replace", refuse_twice)
    monkeypatch.setattr(recovery.time, "sleep", returning(None))
    recovery.atomic(str(target), {"n": 1}, envelope=False)
    assert len(calls) == 3 and updater.json.loads(target.read_bytes()) == {"n": 1}
    assert [p.name for p in ntfs_app.iterdir() if p.suffix == ".tmp"] == []


def test_a_lasting_replace_refusal_fails_and_keeps_the_old_journal(ntfs_app: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = str(ntfs_app)
    with recovery.locked(app):
        record = recovery.begin(app, "0.4.0", "0.5.0", "DOWNLOADING")
    calls: list[str] = []
    real = os.replace
    def refuse(source: str, destination: str) -> None:
        calls.append(source)
        raise PermissionError(5, "Access is denied")
    monkeypatch.setattr(recovery.os, "replace", refuse)
    monkeypatch.setattr(recovery.time, "sleep", returning(None))
    with pytest.raises(PermissionError):
        recovery.save(app, record, "EXTRACTING", "phase:EXTRACTING")
    assert len(calls) == recovery.REPLACE_TRIES                     # bounded
    monkeypatch.setattr(recovery.os, "replace", real)
    loaded = recovery.load(app)
    assert loaded is not None and loaded["state"] == "DOWNLOADING"   # the whole old one, never half
    assert [p.name for p in (ntfs_app / recovery.HERE).iterdir() if p.suffix == ".tmp"] == []


def test_a_torn_journal_is_never_accepted(ntfs_app: Path) -> None:
    app = str(ntfs_app)
    with recovery.locked(app):
        record = recovery.begin(app, "0.4.0", "0.5.0", "DOWNLOADING")
        recovery.save(app, record, "EXTRACTING", "phase")
    journal = ntfs_app / recovery.HERE / recovery.JOURNAL
    journal.write_bytes(journal.read_bytes()[:-20])
    loaded = recovery.load(app)
    assert loaded is not None and loaded["state"] == "DOWNLOADING"   # the previous copy, verified


# ------------------------------------------------------------------ 6. the journal's progress lists
DIGEST_OLD, DIGEST_NEW = {"": "0" * 64}, {"": "1" * 64}


@pytest.fixture
def journal(app: Path) -> dict[str, Any]:
    """A valid APPLYING record: a.dll (replaced, so backed up), new.dll (no old one, waiting)."""
    if os.name != "nt":
        pytest.skip("NTFS journal")
    with recovery.locked(str(app)):
        record = recovery.begin(str(app), "0.4.0", "0.5.0", "DOWNLOADING")
    record.update(state="APPLYING", entries=[{"name": "a.dll", "old": DIGEST_OLD, "new": DIGEST_NEW},
                                             {"name": "new.dll", "old": None, "new": DIGEST_NEW},
                                             {"name": "b.dll", "old": DIGEST_OLD, "new": None}],
                  backed_up=["a.dll"], replaced=["a.dll"], waiting=["new.dll", "b.dll"])
    return record


def check(record: dict[str, Any], app: Path) -> None:
    recovery.validate(updater.json.loads(updater.json.dumps(record)), str(app))


@pytest.mark.parametrize("change", [
    {"waiting": None}, {"backed_up": None}, {"replaced": None},                       # missing
    {"waiting": "new.dll"}, {"replaced": {"a.dll": 1}}, {"backed_up": 3},             # not lists
    {"waiting": ["new.dll", 5]}, {"replaced": ["../a.dll"]}, {"backed_up": ["a.dll", None]},  # bad names
    {"waiting": ["new.dll", "b.dll", "b.dll"]}, {"backed_up": ["a.dll", "a.dll"]},    # duplicates
    {"waiting": ["new.dll", "b.dll", "other.dll"]}, {"replaced": ["a.dll", "zzz"]},   # unknown entries
    {"waiting": ["A.DLL", "new.dll", "b.dll"], "replaced": []},                       # another spelling
    {"waiting": ["new.dll"]},                                                         # b.dll in neither
    {"waiting": ["a.dll", "new.dll", "b.dll"]},                                       # a.dll in both
    {"backed_up": ["a.dll", "new.dll"]},                                              # backup of nothing
    {"backed_up": []},                                                                # replaced, never backed up
])
def test_malformed_progress_is_a_recovery_error(app: Path, journal: dict[str, Any], change: dict[str, Any]) -> None:
    for key, value in change.items():
        if value is None:
            del journal[key]
        else:
            journal[key] = value
    with pytest.raises(recovery.RecoveryError):
        check(journal, app)


@pytest.mark.parametrize(("state", "backed_up", "replaced", "waiting"), [
    ("STAGED", [], [], ["a.dll", "new.dll", "b.dll"]),                     # just planned
    ("BACKING_UP", ["a.dll"], [], ["a.dll", "new.dll", "b.dll"]),          # backed up, not yet replaced
    ("APPLYING", ["a.dll"], ["a.dll"], ["new.dll", "b.dll"]),              # partly done
    ("APPLYING", ["a.dll"], ["a.dll", "new.dll"], ["b.dll"]),              # a new entry needs no backup
    ("VERIFYING", ["a.dll", "b.dll"], ["a.dll", "new.dll", "b.dll"], []),  # all replaced
    ("COMMITTED", ["a.dll", "b.dll"], ["a.dll", "new.dll", "b.dll"], []),
    ("ROLLING_BACK", ["a.dll"], ["a.dll"], ["new.dll", "b.dll"]),          # recovery, mid-way
    ("RECOVERY_REQUIRED", ["a.dll", "b.dll"], ["a.dll", "new.dll"], ["b.dll"]),
    ("ROLLED_BACK", ["a.dll"], ["a.dll"], ["new.dll", "b.dll"]),
])
def test_every_real_progress_state_validates(app: Path, journal: dict[str, Any], state: str, backed_up: list[str],
                                             replaced: list[str], waiting: list[str]) -> None:
    journal.update(state=state, backed_up=backed_up, replaced=replaced, waiting=waiting)
    check(journal, app)


def test_an_early_record_has_empty_progress(app: Path, journal: dict[str, Any]) -> None:
    journal.update(state="DOWNLOADING", entries=[], backed_up=[], replaced=[], waiting=[])
    check(journal, app)
    journal["waiting"] = ["a.dll"]
    with pytest.raises(recovery.RecoveryError):
        check(journal, app)


@windows
@pytest.mark.parametrize("fail_at", [None, "replacement-recorded:numpy", "backup-verified:UncleFarmer.exe"])
def test_every_journal_a_real_update_writes_reads_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                       fail_at: str | None) -> None:
    from test_update_recovery import release as make_release
    app = make_release(tmp_path / "app", "0.4.0", b"old")
    source = make_release(tmp_path / "release", "0.5.0", b"new")
    seen: list[str] = []
    def at(operation: str) -> None:
        if operation == fail_at:
            raise OSError("injected failure")
        if recovery.os.path.exists(os.path.join(str(app), recovery.HERE, recovery.JOURNAL)):
            loaded = recovery.load(str(app))
            assert loaded is not None
            seen.append(loaded["state"])
    monkeypatch.setattr(recovery, "checkpoint", at)
    if fail_at is None:
        updater.apply(str(app), str(source), lambda text: None)
        assert "COMMITTED" in seen
    else:
        with pytest.raises((OSError, updater.UpdateError)):
            updater.apply(str(app), str(source), lambda text: None)
        assert "ROLLED_BACK" in seen
    assert {"STAGED", "BACKING_UP", "APPLYING"} <= set(seen)


# ------------------------------------------------------------------ 7. the bootstrap hand-off
@pytest.fixture
def entry(app: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    from unclefarmer import __main__ as main_module, paths
    launched: list[list[str]] = []
    shown: list[str] = []
    def popen(args: list[str], **kwargs: object) -> None:
        launched.append(args)
    def box(owner: object, text: str, title: str, flags: int) -> None:
        shown.append(text)
    monkeypatch.setattr(subprocess, "Popen", popen)
    if os.name == "nt":
        monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", box)
    monkeypatch.setattr(paths, "APP", str(app))
    return {"launched": launched, "shown": shown, "module": [main_module]}


def hand_off(app: Path, entry: dict[str, list[Any]], recovered: bool = False) -> object:
    with pytest.raises(SystemExit) as stopped:
        entry["module"][0]._hand_to_bootstrap(str(app), recovered)
    return stopped.value.code


def test_a_current_bootstrap_is_handed_the_relaunch(app: Path, entry: dict[str, list[Any]]) -> None:
    (app / recovery.BOOTSTRAP).write_bytes(b"MZ..." + updater.BOOTSTRAP_MARKER + b"...")
    assert hand_off(app, entry) == 0
    assert entry["launched"] == [[str(app / recovery.BOOTSTRAP), "--relaunch", str(os.getpid())]]


@pytest.mark.parametrize("body", [None, b"MZ an older bootstrap", b"UNCLEFARMER-BOOTSTRAP-1",
                                  b"UNCLEFARMER-BOOTSTRAP-", b"unclefarmer-bootstrap-2", b""])
def test_a_missing_or_incompatible_bootstrap_is_never_started(app: Path, entry: dict[str, list[Any]],
                                                               body: bytes | None) -> None:
    if body is not None:
        (app / recovery.BOOTSTRAP).write_bytes(body)
    code = hand_off(app, entry)
    assert entry["launched"] == []                     # no --relaunch an old bootstrap could misread
    assert isinstance(code, str) and "bootstrap.exe" in code   # the recovery error path
    if os.name == "nt":
        assert entry["shown"] and "interrupted" in entry["shown"][0]


def test_a_bootstrap_folder_is_never_started(app: Path, entry: dict[str, list[Any]]) -> None:
    (app / recovery.BOOTSTRAP).mkdir()
    hand_off(app, entry)
    assert entry["launched"] == []


def test_right_after_a_recovery_even_a_current_bootstrap_is_not_asked_again(
        app: Path, entry: dict[str, list[Any]]) -> None:
    (app / recovery.BOOTSTRAP).write_bytes(updater.BOOTSTRAP_MARKER)
    hand_off(app, entry, recovered=True)
    assert entry["launched"] == []


# ------------------------------------------------------------------ 8. install paths are literal
@pytest.mark.parametrize("name", ["Uncle Farmer [old]", "a]b", "x[1", "Uncle Farmer", "plain", "[ab]", "q[!x]"])
def test_a_complete_install_in_any_folder_name_misses_nothing(tmp_path: Path, name: str) -> None:
    folder = flat(tmp_path / name)
    assert updater.missing_runtime(str(folder)) == []
    (folder / "data/atlas.json").unlink()
    assert updater.missing_runtime(str(folder)) == ["data/atlas.json"]


def test_a_bracket_folder_does_not_match_a_neighbour(tmp_path: Path) -> None:
    """"[ab]" read as a pattern would match a sibling folder "a": the escape makes it literal."""
    flat(tmp_path / "a")
    (tmp_path / "[ab]").mkdir()
    assert len(updater.missing_runtime(str(tmp_path / "[ab]"))) == len(updater.REQUIRED)


def test_a_release_in_a_bracket_folder_verifies(tmp_path: Path) -> None:
    folder = flat(tmp_path / "Uncle Farmer [2]")
    assert updater._verify_release(str(folder), "0.5.0")


# ------------------------------------------------------------------ 9. one version shape
@pytest.mark.parametrize(("version", "stable"), [
    ("1.2", False), ("1.2.3", True), ("1.2.3.4", False), ("1.2.3-dev.1", False), ("", False), ("v1.2.3", False),
    ("1.2.3 ", False), ("1.2.x", False), ("01.2.3", True), ("1.2.3-dev", False),
])
def test_stable_versions_are_exactly_three_numbers(version: str, stable: bool) -> None:
    assert updater.is_stable(version) is stable


@pytest.mark.parametrize(("version", "valid"), [
    ("1.2", False), ("1.2.3", True), ("1.2.3.4", False), ("1.2.3-dev.1", True), ("1.2.3-dev.x", False),
])
def test_the_copier_and_release_check_take_stable_or_dev(version: str, valid: bool) -> None:
    assert (updater.VERSION.fullmatch(version) is not None) is valid


@pytest.mark.parametrize(("tag", "offered"), [("v1.2.3", True), ("v1.2.3.4", False), ("v1.2", False),
                                              ("v1.2.3-dev.4", False)])
def test_master_offers_only_three_number_releases(tag: str, offered: bool) -> None:
    version = tag[1:]
    data = release(tag_name=tag)
    data["assets"][0]["name"] = "UncleFarmer-%s.zip" % version
    assert (updater._stable(data) is not None) is offered


@pytest.mark.parametrize(("version", "accepted"), [("1.2", False), ("1.2.3.4", False), ("1.2.3-dev.1", False),
                                                   ("v99.0.0", True), ("99.0.0", True), ("nonsense", False)])
def test_release_py_publishes_only_what_the_updater_offers(monkeypatch: pytest.MonkeyPatch, version: str,
                                                           accepted: bool) -> None:
    import release as release_tool
    options = release_tool.Options(version=version, notes=None, notes_file=None, draft=False, local=True,
                                   no_check=True, full=False)
    if accepted:
        got = release_tool._checked_version(options)
        assert got == version.lstrip("v") and updater.is_stable(got)
    else:
        with pytest.raises(SystemExit, match="three numbers"):
            release_tool._checked_version(options)


@pytest.mark.parametrize(("version", "ok"), [("0.5.0", True), ("0.5.0.1", False), ("0.5", False),
                                             ("0.5.0-dev.3", True)])
def test_release_verification_agrees(tmp_path: Path, version: str, ok: bool) -> None:
    folder = flat(tmp_path / "release")
    updater.write_build(str(folder), version, "master")
    if ok:
        updater._verify_release(str(folder), version)
    else:
        with pytest.raises(updater.UpdateError, match="build.json"):
            updater._verify_release(str(folder), version)


# ------------------------------------------------------------------ A. GitHub's digest
GOOD = "ab" * 32


@pytest.mark.parametrize(("asset", "sha"), [
    ({}, ""), ({"digest": None}, ""), ({"digest": ""}, ""),
    ({"digest": "sha256:" + GOOD}, GOOD), ({"digest": "sha256:" + GOOD.upper()}, GOOD),
])
def test_a_digest_is_read_or_absent(asset: dict[str, Any], sha: str) -> None:
    assert updater._digest({"name": "UncleFarmer-1.0.0.zip", **asset}) == sha


@pytest.mark.parametrize("digest", [123, ["sha256:" + GOOD], {"sha256": GOOD}, True, "sha256:xyz",
                                    "sha256:" + GOOD[:-1], "sha256:" + GOOD + "0", "sha512:" + "ab" * 64,
                                    "SHA256:" + GOOD, GOOD, " sha256:" + GOOD, "sha256:"])
def test_a_malformed_digest_is_never_skipped(digest: object) -> None:
    with pytest.raises(updater.UpdateError, match="checksum"):
        updater._digest({"name": "UncleFarmer-1.0.0.zip", "digest": digest})


def test_a_release_with_a_malformed_digest_is_not_offered(app: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "REPO", "owner/repo")
    data = release()
    data["assets"][0]["digest"] = 42
    fake_open(monkeypatch, lambda: Resp(updater.json.dumps(data).encode()))     # a fresh answer per ask
    with pytest.raises(updater.UpdateError):
        updater.latest()
    assert updater.check() is None and "checksum" in update_log(app)


# ------------------------------------------------------------------ B. archive limits
def archive(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def test_a_normal_archive_unpacks(app: Path, tmp_path: Path) -> None:
    zip_path = archive(tmp_path / "ok.zip", {"Uncle Farmer/" + updater.EXE: b"exe", "Uncle Farmer/data/a.json": b"{}"})
    folder = updater.unpack(str(zip_path))
    assert Path(folder, updater.EXE).read_bytes() == b"exe"


@pytest.mark.parametrize(("limit", "value", "message"), [
    ("UNPACK_MOST_FILES", 2, "too many entries"),
    ("UNPACK_MOST_FILE", 1000, "too large"),
    ("UNPACK_MOST_BYTES", 1500, "unpacks to too much"),
])
def test_an_oversized_archive_is_refused_before_anything_is_written(
        app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str, value: int, message: str) -> None:
    monkeypatch.setattr(updater, limit, value)
    zip_path = archive(tmp_path / "bomb.zip", {"Uncle Farmer/" + updater.EXE: b"e" * 900,
                                               "Uncle Farmer/a.bin": b"\0" * 1200, "Uncle Farmer/b.bin": b"b"})
    with pytest.raises(updater.UpdateError, match=message):
        updater.unpack(str(zip_path))
    assert list((app / "update/new").rglob("*")) == []


def test_an_archive_the_disk_has_no_room_for_is_refused(app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    zip_path = archive(tmp_path / "big.zip", {"Uncle Farmer/" + updater.EXE: b"exe" * 1000})
    monkeypatch.setattr(updater.shutil, "disk_usage", returning(types.SimpleNamespace(free=updater.SPACE_MARGIN)))
    with pytest.raises(updater.UpdateError, match="free space"):
        updater.unpack(str(zip_path))
    assert list((app / "update/new").rglob("*")) == []


def test_the_limits_leave_a_real_release_plenty_of_room() -> None:
    assert updater.UNPACK_MOST_FILES >= 100 * 85 and updater.UNPACK_MOST_BYTES >= 10 * 100 * 2 ** 20


# ------------------------------------------------------------------ D. read-only files
def read_only(path: Path) -> None:
    os.chmod(path, stat.S_IREAD)


@pytest.fixture
def writable_after(tmp_path: Path) -> Iterator[None]:
    yield
    for root, _dirs, files in os.walk(tmp_path):
        for name in files:
            os.chmod(os.path.join(root, name), stat.S_IREAD | stat.S_IWRITE)


@pytest.fixture
def installed(tmp_path: Path, writable_after: None) -> tuple[Path, Path]:
    if os.name != "nt":
        pytest.skip("NTFS")
    from test_update_recovery import release as make_release
    app = make_release(tmp_path / "app", "0.4.0", b"old")
    (app / "agent/run").mkdir()
    (app / "agent/run/port").write_bytes(b"private agent state")
    (app / "data/game_texts.json").write_bytes(b"private learned data")
    return app, make_release(tmp_path / "release", "0.5.0", b"new")


@pytest.mark.parametrize("preserved", ["data/game_texts.json", "agent/run/port", "python3314.dll"])
def test_a_read_only_file_stops_the_update_before_anything_changes(installed: tuple[Path, Path], preserved: str) -> None:
    app, source = installed
    read_only(app / preserved)
    before = snapshot(app)
    with pytest.raises(updater.UpdateError, match="read-only") as caught:
        updater.apply(str(app), str(source), lambda text: None)
    assert os.path.normpath(preserved) in str(caught.value)
    assert snapshot(app) == before and recovery.load(str(app)) is None   # no journal was begun
    assert not os.access(app / preserved, os.W_OK)                       # the attribute is the player's


def test_a_read_only_file_the_update_never_touches_does_not_stop_it(installed: tuple[Path, Path]) -> None:
    app, source = installed
    (app / "my notes.txt").write_bytes(b"the player's own")
    read_only(app / "my notes.txt")
    updater.apply(str(app), str(source), lambda text: None)
    loaded = recovery.load(str(app))
    assert loaded is not None and loaded["state"] == "COMMITTED"
    assert (app / "my notes.txt").read_bytes() == b"the player's own" and not os.access(app / "my notes.txt", os.W_OK)


def test_once_writable_the_update_keeps_the_preserved_data(installed: tuple[Path, Path]) -> None:
    app, source = installed
    read_only(app / "data/game_texts.json")
    with pytest.raises(updater.UpdateError):
        updater.apply(str(app), str(source), lambda text: None)
    os.chmod(app / "data/game_texts.json", stat.S_IREAD | stat.S_IWRITE)
    updater.apply(str(app), str(source), lambda text: None)
    loaded = recovery.load(str(app))
    assert loaded is not None and loaded["state"] == "COMMITTED"
    assert (app / "data/game_texts.json").read_bytes() == b"private learned data"
    assert (app / "agent/run/port").read_bytes() == b"private agent state"


def test_the_app_says_so_before_it_starts_a_copier(installed: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    app, source = installed
    monkeypatch.setattr(updater.paths, "APP", str(app))
    monkeypatch.setattr(updater.paths, "LOGS", str(app / "logs"))
    monkeypatch.setattr(updater, "UPDATE", str(app / "update"))
    monkeypatch.setattr(updater, "NEW", str(app / "update/new"))
    staged = app / "update/new/Uncle Farmer"
    staged.parent.mkdir(parents=True)
    import shutil
    shutil.copytree(source, staged)
    monkeypatch.setattr(updater, "frozen", returning(True))
    monkeypatch.setattr(updater.subprocess, "Popen", failing("no copier may start"))
    read_only(app / "data/game_texts.json")
    with pytest.raises(updater.UpdateError, match="game_texts.json"):
        updater.start_apply(str(staged), "0.5.0")
    assert not (app / recovery.HERE / "runtime").exists()


def test_the_rescue_copy_is_not_made_from_read_only_files(installed: tuple[Path, Path]) -> None:
    app, _source = installed
    read_only(app / "agent/run/port")
    with pytest.raises(updater.UpdateError, match="read-only"):
        updater.ensure_recovery_runtime(str(app), str(app))
    leftovers = [p.name for p in (app / recovery.HERE).iterdir() if p.name.startswith("runtime")]
    assert leftovers == []


def test_read_only_leftovers_of_an_older_attempt_never_block_later_updates(installed: tuple[Path, Path]) -> None:
    app, source = installed
    with recovery.locked(str(app)):
        record = recovery.begin(str(app), "0.4.0", "0.5.0", "DOWNLOADING")
        work = Path(record["backup"]).parent
        (work / "new/data").mkdir(parents=True)
        (work / "new/data/game_texts.json").write_bytes(b"copy")
        read_only(work / "new/data/game_texts.json")              # what an older version left
        recovery.save(str(app), record, "ROLLED_BACK", "preparation-failed")
    preparing = app / recovery.HERE / "runtime-preparing-old"
    preparing.mkdir()
    (preparing / "x.dll").write_bytes(b"x")
    read_only(preparing / "x.dll")
    updater.apply(str(app), str(source), lambda text: None)       # begin() cleans the old work first
    assert not work.exists()
    updater.ensure_recovery_runtime(str(app), str(app))
    assert not preparing.exists()


def test_a_hard_linked_read_only_file_is_refused_not_made_writable(installed: tuple[Path, Path], tmp_path: Path) -> None:
    app, _source = installed
    outside = tmp_path / "players-file.txt"
    outside.write_bytes(b"mine")
    read_only(outside)
    work = app / recovery.HERE / "0123456789abcdef0123456789abcdef"
    work.mkdir(parents=True)
    os.link(outside, work / "linked.txt")
    with pytest.raises(recovery.RecoveryError, match="hard-linked"):
        recovery.remove_private_tree(str(work), str(app))
    assert outside.read_bytes() == b"mine" and not os.access(outside, os.W_OK)
