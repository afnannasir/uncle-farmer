"""Real Windows/NTFS process-kill tests. No physical power-loss claims."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from unclefarmer import updater, update_recovery as recovery

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows/NTFS recovery integration")
ROOT = Path(__file__).resolve().parents[1]
# Kill the real interpreter, not Windows' venv redirector (which can leave its child alive).
PYTHON = getattr(sys, "_base_executable", sys.executable)
ENV = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))


def release(folder: Path, version: str, marker: bytes) -> Path:
    for pattern in updater.REQUIRED:
        path = folder / pattern.replace("*", "314")
        if pattern in updater.RUNTIME_DIRS:
            path /= "__init__.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(marker)
    updater.write_build(str(folder), version, "master")
    return folder


def contents(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
            and p.relative_to(folder).parts[0] not in (recovery.HERE, "update", "logs")}


@pytest.fixture
def trees(tmp_path: Path) -> tuple[Path, Path]:
    recovery.ntfs(str(tmp_path))  # Assert the real integration volume, not a mocked filesystem.
    app = release(tmp_path / "app", "0.4.0", b"old")
    source = release(tmp_path / "release", "0.5.0", b"new")
    (app / "settings.json").write_bytes(b"private settings")
    (app / "agent/run").mkdir()
    (app / "agent/run/port").write_bytes(b"private agent state")
    (app / "data/game_texts.json").write_bytes(b"private learned data")
    return app, source


WORKER = r'''
import io, os, sys, time, zipfile
from pathlib import Path
from unclefarmer import updater as u, update_recovery as r
app, source, boundary, ready, action = sys.argv[1:]
u.paths.APP = app
u.UPDATE = os.path.join(app, "update")
u.NEW = os.path.join(u.UPDATE, "new")
u.RESULT = os.path.join(u.UPDATE, "result.json")
def checkpoint(event):
    if event == boundary or boundary.endswith(":*") and event.startswith(boundary[:-1]):
        Path(ready).write_text(event)
        while True: time.sleep(.01)
r.checkpoint = checkpoint
if action == "recover":
    r.recover(app)
elif action in ("download", "extract"):
    u.frozen = lambda: True
    if action == "download":
        class Response(io.BytesIO):
            headers = {"Content-Length": "6"}
        u._open = lambda *args: Response(b"abcdef")
        u.download(dict(asset="test.zip", version="0.5.0", size=6, sha256="", url="https://unused", tag=None, notes=""))
    else:
        archive = os.path.join(app, "archive.zip")
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("Uncle Farmer/UncleFarmer.exe", b"new")
        u.unpack(archive)
else:
    if action == "rollback":
        original = u._rename_entry
        def fail(source, target, *args, **kwargs):
            if target == os.path.join(app, "build.json") and not kwargs.get("rollback"):
                raise OSError("induced failure before metadata publication")
            return original(source, target, *args, **kwargs)
        u._rename_entry = fail
    u.apply(app, source, print)
'''


def interrupt(app: Path, source: Path, boundary: str, action: str = "apply") -> None:
    ready = app.parent / ("ready-" + str(time.time_ns()))
    with subprocess.Popen([PYTHON, "-c", WORKER, str(app), str(source), boundary, str(ready), action],
                          cwd=ROOT, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        deadline = time.monotonic() + 90
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        if not ready.exists():
            process.kill()
            output, error = process.communicate()
            pytest.fail("operation not reached: " + boundary + "\n" + (output + error).decode(errors="replace"))
        # Real TerminateProcess on Windows: no Python exception/finally executes.
        process.terminate()
        process.wait(timeout=10)
        assert process.returncode != 0


def fresh_recovery(app: Path, success: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([PYTHON, "-c",
                             "import sys; from unclefarmer import update_recovery as r; "
                             "assert 'wx' not in sys.modules and 'orjson' not in sys.modules; "
                             "print(r.recover(sys.argv[1]))", str(app)], cwd=ROOT, env=ENV,
                            capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result


@pytest.mark.parametrize("boundary,action,new", [
    ("download-chunk", "download", False),
    ("extraction-file", "extract", False),
    ("staging-file", "apply", False),
    ("journal-created", "apply", False),
    ("backup-copied:*", "apply", False),
    ("original-retired:*", "apply", False),
    ("replace-intent:*", "apply", False),
    ("replacement-moved:*", "apply", False),
    ("replacement-moved:numpy", "apply", False),
    ("replacement-moved:UncleFarmerApp.exe", "apply", False),
    ("replace-intent:build.json", "apply", False),
    ("cleanup-started", "apply", True),
    ("cleanup-removed:*", "apply", True),
    ("rollback-prepared:build.json", "rollback", False),
    ("installation-verified", "apply", True),
    ("committed", "apply", True),
])
def test_termination_then_fresh_recovery(trees: tuple[Path, Path], boundary: str, action: str, new: bool) -> None:
    app, source = trees
    old = contents(app)
    expected = contents(source) if new else old.copy()
    if new:
        for name in (updater.EXE, "settings.json", "agent/run/port", "data/game_texts.json"):
            expected[name] = old[name]
    interrupt(app, source, boundary, action)
    # The extraction test archive is input, not an installed application entry.
    (app / "archive.zip").unlink(missing_ok=True)
    fresh_recovery(app)
    assert contents(app) == expected
    assert not list((app / "update").glob("*.part"))
    fresh_recovery(app)  # Idempotent after successful recovery/commit and cleanup.
    assert contents(app) == expected


def test_recovery_itself_can_be_terminated_and_restarted(trees: tuple[Path, Path]) -> None:
    app, source = trees
    old = contents(app)
    interrupt(app, source, "replacement-moved:numpy")
    interrupt(app, source, "rollback-restored:*", "recover")
    fresh_recovery(app)
    assert contents(app) == old


@pytest.mark.parametrize("copies", [1, 2, 3])
def test_corrupted_journal_falls_back_or_stops_without_writes(trees: tuple[Path, Path], copies: int) -> None:
    app, source = trees
    old = contents(app)
    interrupt(app, source, "replacement-moved:numpy")
    (app / recovery.HERE / recovery.JOURNAL).write_text("{torn")
    if copies >= 2:
        (app / recovery.HERE / (recovery.JOURNAL + ".prev")).write_text("{also torn")
    if copies == 3:
        (plan,) = (app / recovery.HERE).glob("*/plan.json")
        plan.write_text("{torn plan")
        before = contents(app)
        fresh_recovery(app, False)
        assert contents(app) == before
    else:
        fresh_recovery(app)
        assert contents(app) == old


@pytest.mark.parametrize("original_missing", [False, True])
def test_missing_backup_uses_retired_original_or_stops_safely(
        trees: tuple[Path, Path], original_missing: bool) -> None:
    app, source = trees
    old = contents(app)
    interrupt(app, source, "original-retired:numpy")
    record = recovery.load(str(app))
    assert record is not None
    (Path(record["backup"]) / "numpy/__init__.py").unlink()
    if original_missing:
        (Path(record["backup"]).parent / "retired/numpy/__init__.py").unlink()
        before = contents(app)
        fresh_recovery(app, False)
        assert contents(app) == before
    else:
        fresh_recovery(app)
        assert contents(app) == old


def test_corrupted_replacement_is_retained_and_verified_old_version_restored(trees: tuple[Path, Path]) -> None:
    app, source = trees
    old = contents(app)
    interrupt(app, source, "replacement-moved:numpy")
    (app / "numpy/__init__.py").write_bytes(b"torn replacement or external edit")
    fresh_recovery(app)
    assert contents(app) == old
    (retained,) = (app / recovery.HERE).glob("retained-*/numpy/__init__.py")
    assert retained.read_bytes() == b"torn replacement or external edit"


def test_journal_cannot_redirect_recovery_into_user_files(trees: tuple[Path, Path]) -> None:
    app, source = trees
    interrupt(app, source, "replacement-moved:numpy")
    record = recovery.load(str(app))
    assert record is not None
    record["entries"][0]["name"] = "settings.json"
    for suffix in ("", ".prev"):
        recovery.atomic(str(app / recovery.HERE / (recovery.JOURNAL + suffix)), record)
    (plan,) = (app / recovery.HERE).glob("*/plan.json")
    recovery.atomic(str(plan), record)
    before = contents(app)
    fresh_recovery(app, False)
    assert contents(app) == before


def test_journal_cannot_change_nested_keep_in_data(trees: tuple[Path, Path]) -> None:
    app, source = trees
    interrupt(app, source, "replacement-moved:numpy")
    record = recovery.load(str(app))
    assert record is not None
    agent = next(e for e in record["entries"] if e["name"] == "agent")
    agent["new"]["run/port"] = "0" * 64
    for path in [app / recovery.HERE / recovery.JOURNAL,
                 app / recovery.HERE / (recovery.JOURNAL + ".prev"),
                 Path(record["backup"]).parent / "plan.json"]:
        recovery.atomic(str(path), record)
    before = contents(app)
    fresh_recovery(app, False)
    assert contents(app) == before


def test_live_owner_prevents_early_artifact_cleanup(trees: tuple[Path, Path]) -> None:
    app, _ = trees
    with recovery.locked(str(app)):
        recovery.begin(str(app), "0.4.0", "0.5.0", "DOWNLOADING")
    (app / "update").mkdir()
    part = app / "update/download.part"
    part.write_bytes(b"working")
    fresh_recovery(app, False)
    assert part.read_bytes() == b"working"


def test_ntfs_same_volume_type_change_and_locked_handle(trees: tuple[Path, Path]) -> None:
    app, _ = trees
    source, target = app / "held", app / "moved"
    source.write_bytes(b"old")
    with open(source, "rb"):
        with pytest.raises(PermissionError):
            recovery.move(str(source), str(target))
    recovery.rename(str(source), str(target))
    assert target.read_bytes() == b"old"


def test_recovery_directory_junction_is_rejected(trees: tuple[Path, Path], tmp_path: Path) -> None:
    from test_updater import junction
    app, _ = trees
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_bytes(b"private")
    junction(app / recovery.HERE, outside)
    fresh_recovery(app, False)
    assert (outside / "keep").read_bytes() == b"private"


@pytest.mark.parametrize("directory_first", [True, False])
def test_interrupted_type_change_is_recovered(trees: tuple[Path, Path], directory_first: bool) -> None:
    app, source = trees
    old, new = app / "changed", source / "changed"
    directory, file = (old, new) if directory_first else (new, old)
    directory.mkdir()
    (directory / "item").write_bytes(b"nested")
    file.write_bytes(b"plain")
    expected = contents(app)
    interrupt(app, source, "replacement-moved:changed")
    fresh_recovery(app)
    assert contents(app) == expected


def test_missing_journals_recover_from_immutable_backup_plan(trees: tuple[Path, Path]) -> None:
    app, source = trees
    old = contents(app)
    interrupt(app, source, "replacement-moved:numpy")
    for suffix in ("", ".prev"):
        (app / recovery.HERE / (recovery.JOURNAL + suffix)).unlink()
    fresh_recovery(app)
    assert contents(app) == old


def test_verified_installation_starts_even_if_backup_cleanup_is_locked(trees: tuple[Path, Path]) -> None:
    app, source = trees
    interrupt(app, source, "cleanup-started")
    record = recovery.load(str(app))
    assert record is not None
    held = Path(record["backup"]) / recovery.PAYLOAD
    with open(held, "rb"):
        fresh_recovery(app)
        assert held.exists()
        assert "cleanup deferred" in (app / recovery.HERE / "recovery.log").read_text().lower()
    fresh_recovery(app)
    assert not Path(record["backup"]).exists()


def test_reused_pid_does_not_look_like_a_live_transaction_owner(trees: tuple[Path, Path]) -> None:
    app, _ = trees
    with recovery.locked(str(app)):
        record = recovery.begin(str(app), "0.4.0", "0.5.0", "DOWNLOADING")
        record["owner_stamp"] -= 1
        recovery.save(str(app), record, "DOWNLOADING", "simulate-reused-pid")
    fresh_recovery(app)


def test_recovery_accepts_windows_path_case_variation(trees: tuple[Path, Path]) -> None:
    app, source = trees
    old = contents(app)
    interrupt(app, source, "replacement-moved:numpy")
    fresh_recovery(Path(str(app).swapcase()))
    assert contents(app) == old


def test_readonly_original_aborts_without_changing_the_installed_tree(trees: tuple[Path, Path]) -> None:
    app, source = trees
    old = contents(app)
    protected = app / "python3314.dll"
    os.chmod(protected, stat.S_IREAD)
    try:
        with pytest.raises((OSError, updater.UpdateError)):
            updater.apply(str(app), str(source), lambda message: None)
        assert contents(app) == old
    finally:
        os.chmod(protected, stat.S_IREAD | stat.S_IWRITE)


def test_preexisting_temp_hardlink_cannot_redirect_a_journal_write(trees: tuple[Path, Path], tmp_path: Path) -> None:
    app, _ = trees
    outside = tmp_path / "private"
    outside.write_bytes(b"do not truncate")
    root = app / recovery.HERE
    root.mkdir()
    os.link(outside, root / (recovery.JOURNAL + ".tmp"))
    with recovery.locked(str(app)):
        recovery.begin(str(app), "0.4.0", "0.5.0")
    assert outside.read_bytes() == b"do not truncate"


def test_uninspectable_journal_parent_is_never_written(
        trees: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = trees
    parent = app / recovery.HERE
    parent.mkdir()
    original = os.lstat
    def denied(path: str | os.PathLike[str], *args: object, **kwargs: object) -> os.stat_result:
        if Path(path) == parent:
            raise PermissionError("journal directory cannot be inspected")
        return original(path)
    monkeypatch.setattr(recovery.os, "lstat", denied)
    with pytest.raises(PermissionError, match="cannot be inspected"):
        recovery.atomic(str(parent / recovery.JOURNAL), {"unused": True})
    assert list(parent.iterdir()) == []


def test_failed_staged_copier_keeps_a_known_working_old_rescue_runtime(
        trees: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    app, source = trees
    stage = app / "update/new/release"
    shutil.copytree(source, stage)
    monkeypatch.setattr(updater, "frozen", lambda: True)
    monkeypatch.setattr(updater.paths, "APP", str(app))
    monkeypatch.setattr(updater, "UPDATE", str(app / "update"))
    monkeypatch.setattr(updater, "NEW", str(app / "update/new"))
    def failed(*args: object, **kwargs: object) -> None:
        raise OSError("staged loader failed")
    monkeypatch.setattr(updater.subprocess, "Popen", failed)
    with pytest.raises(OSError, match="staged loader failed"):
        updater.start_apply(str(stage), "0.5.0")
    assert (app / recovery.HERE / "runtime" / recovery.PAYLOAD).read_bytes() == b"old"
    assert (app / recovery.PAYLOAD).read_bytes() == b"old"


def test_recovery_dispatch_precedes_application_initialization(
        trees: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer import __main__ as entry
    app, _ = trees
    calls: list[str] = []
    monkeypatch.setattr(sys, "argv", ["UncleFarmerApp.exe", "--recover-update", str(app)])
    monkeypatch.setattr(sys, "executable", str(app / recovery.HERE / "runtime" / recovery.PAYLOAD))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    def recovered(path: str) -> str:
        calls.append(path)
        return "COMMITTED"
    monkeypatch.setattr(recovery, "recover", recovered)
    entry.main()
    assert calls == [str(app)]


def test_source_recovery_entry_cannot_select_an_arbitrary_destination(
        trees: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer import __main__ as entry
    app, _ = trees
    before = contents(app)
    monkeypatch.setattr(sys, "argv", ["python", "--recover-update", str(app)])
    with pytest.raises(SystemExit) as error:
        entry.main()
    assert error.value.code == 1
    assert contents(app) == before
    assert not (app / recovery.HERE).exists()


@pytest.fixture(scope="module")
def native_programs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    import build_exe
    from build_native import compiler
    folder = tmp_path_factory.mktemp("native-recovery")
    build_exe.build_bootstrap(str(folder))
    stub = folder / "stub.c"
    stub.write_text(r'''
#include <windows.h>
#include <wchar.h>
int WINAPI wWinMain(HINSTANCE a, HINSTANCE b, LPWSTR args, int show) {
    (void)a; (void)b; (void)show;
    int recovery = wcsstr(args, L"--recover-update") != NULL;
    wchar_t marker[32768];
    wchar_t value[2];
    if (recovery && GetEnvironmentVariableW(L"UF_TEST_RECOVERY_FAIL", value, 2)) return 42;
    if (!recovery && GetEnvironmentVariableW(L"UF_TEST_REQUIRE_RECOVERY", value, 2) &&
        GetFileAttributesW(L"recovery-started") == INVALID_FILE_ATTRIBUTES) return 44;
    const wchar_t *name = L"payload-started";
    if (recovery) {
        wchar_t *begin = wcschr(args, L'"');
        if (!begin) return 45;
        wcscpy(marker, begin + 1);
        wchar_t *end = wcschr(marker, L'"');
        if (!end) return 46;
        *end = 0;
        wcscat(marker, L"\\recovery-started");
        name = marker;
    }
    HANDLE file = CreateFileW(name, GENERIC_WRITE,
                              0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return 43;
    CloseHandle(file);
    if (recovery) {
        file = CreateFileW(L"rescue-working-directory", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
        if (file == INVALID_HANDLE_VALUE) return 47;
        CloseHandle(file);
    }
    return 0;
}
''', encoding="utf-8")
    command = compiler()
    assert command is not None
    env = dict(os.environ)
    if os.path.isabs(command[0]):
        env["PATH"] = os.path.dirname(command[0]) + os.pathsep + env.get("PATH", "")
    subprocess.run(command + ["-municode", "-mwindows", str(stub), "-o", str(folder / "stub.exe")], check=True, env=env)
    return folder / updater.EXE, folder / "stub.exe"


@pytest.mark.parametrize("pending,failed", [(False, False), (True, False), (True, True)])
def test_native_bootstrap_recovers_before_loading_any_application_runtime(
        native_programs: tuple[Path, Path], tmp_path: Path, pending: bool, failed: bool) -> None:
    import shutil
    launcher, stub = native_programs
    shutil.copy2(launcher, tmp_path / updater.EXE)
    shutil.copy2(stub, tmp_path / recovery.PAYLOAD)
    # A corrupt application Python DLL cannot stop the independent native launcher/helper.
    (tmp_path / "python314.dll").write_bytes(b"corrupt application DLL")
    runtime = tmp_path / recovery.HERE / "runtime"
    if pending:
        runtime.mkdir(parents=True)
        shutil.copy2(stub, runtime / recovery.PAYLOAD)
        (runtime.parent / recovery.JOURNAL).write_text("pending")
    env = dict(os.environ)
    if failed:
        env["UF_TEST_RECOVERY_FAIL"] = "1"
    if pending:
        env["UF_TEST_REQUIRE_RECOVERY"] = "1"
    result = subprocess.run([str(tmp_path / updater.EXE)], cwd=tmp_path, env=env, timeout=15)
    assert result.returncode == (42 if failed else 0)
    assert (tmp_path / "recovery-started").exists() == (pending and not failed)
    assert (tmp_path / "payload-started").exists() == (not failed)
    if failed:
        (error_log,) = (tmp_path / recovery.HERE).glob("bootstrap-error-*.txt")
        assert "42" in error_log.read_text()
    if pending and not failed:
        assert (runtime / "rescue-working-directory").exists()
        assert not (tmp_path / "rescue-working-directory").exists()


def test_native_bootstrap_checks_armed_marker_when_journals_are_missing(
        native_programs: tuple[Path, Path], tmp_path: Path) -> None:
    import shutil
    launcher, stub = native_programs
    runtime = tmp_path / recovery.HERE / "runtime"
    runtime.mkdir(parents=True)
    shutil.copy2(launcher, tmp_path / updater.EXE)
    shutil.copy2(stub, tmp_path / recovery.PAYLOAD)
    shutil.copy2(stub, runtime / recovery.PAYLOAD)
    (runtime.parent / "armed").write_bytes(b"armed")
    result = subprocess.run([str(tmp_path / updater.EXE)], cwd=tmp_path,
                            env=dict(os.environ, UF_TEST_REQUIRE_RECOVERY="1"), timeout=15)
    assert result.returncode == 0
    assert (tmp_path / "recovery-started").exists()
    assert (tmp_path / "payload-started").exists()
