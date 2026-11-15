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
    ("replacement-moved:UncleFarmer.exe", "apply", False),
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
        for name in (updater.BOOTSTRAP, "settings.json", "agent/run/port", "data/game_texts.json"):
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
    monkeypatch.setattr(sys, "argv", ["UncleFarmer.exe", "--recover-update", str(app)])
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
    """The real bootstrap.exe and a stub that stands in for UncleFarmer.exe in all three roles:
    the app (writes payload-started with its arguments), the rescue runtime (--recover-update) and
    the update copier (--apply-update: writes copier-args.txt and copier-pid.txt beside itself)."""
    import build_exe
    from build_native import compiler
    folder = tmp_path_factory.mktemp("native-recovery")
    build_exe.build_bootstrap(str(folder))
    stub = folder / "stub.c"
    stub.write_text(r'''
#include <windows.h>
#include <wchar.h>
#include <stdio.h>
static int note(const wchar_t *name, const wchar_t *text) {
    HANDLE file = CreateFileW(name, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return 0;
    DWORD written;
    WriteFile(file, text, (DWORD)(wcslen(text) * sizeof(wchar_t)), &written, NULL);
    CloseHandle(file);
    return 1;
}
int WINAPI wWinMain(HINSTANCE a, HINSTANCE b, LPWSTR args, int show) {
    (void)a; (void)b; (void)show;
    int recovery = wcsstr(args, L"--recover-update") != NULL;
    wchar_t marker[32768];
    wchar_t value[2];
    if (wcsstr(args, L"--apply-update") != NULL) {
        wchar_t pid[32];
        swprintf(pid, 32, L"%lu", (unsigned long)GetCurrentProcessId());
        if (!note(L"copier-pid.txt", pid) || !note(L"copier-args.txt", args)) return 48;
        if (GetEnvironmentVariableW(L"UF_TEST_COPIER_SLEEP", value, 2)) Sleep(60000);
        if (GetEnvironmentVariableW(L"UF_TEST_COPIER_EXIT", value, 2)) return value[0] - L'0';
        return 0;
    }
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
    if (!note(name, recovery ? L"" : args)) return 43;
    if (recovery && !note(L"rescue-working-directory", L"")) return 47;
    return 0;
}
''', encoding="utf-8")
    command = compiler()
    assert command is not None
    env = dict(os.environ)
    if os.path.isabs(command[0]):
        env["PATH"] = os.path.dirname(command[0]) + os.pathsep + env.get("PATH", "")
    subprocess.run(command + ["-municode", "-mwindows", str(stub), "-o", str(folder / "stub.exe")], check=True, env=env)
    return folder / updater.BOOTSTRAP, folder / "stub.exe"


def installed(native_programs: tuple[Path, Path], app: Path) -> Path:
    """bootstrap.exe and the stub app (UncleFarmer.exe) in app; returns the bootstrap."""
    import shutil
    bootstrap, stub = native_programs
    shutil.copy2(bootstrap, app / updater.BOOTSTRAP)
    shutil.copy2(stub, app / recovery.PAYLOAD)
    return app / updater.BOOTSTRAP


def started(path: Path, seconds: float = 15) -> str | None:
    """The app stub's arguments once it has started (bootstrap.exe does not wait for it), else None."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            return path.read_bytes().decode("utf-16-le")
        except OSError:
            time.sleep(.05)
    return None


def test_the_bootstrap_imports_no_application_runtime(native_programs: tuple[Path, Path]) -> None:
    bootstrap, _ = native_programs
    data = bootstrap.read_bytes()
    assert updater.BOOTSTRAP_MARKER in data and len(data) < updater.BOOTSTRAP_MOST
    for dll in (b"python3", b"wxbase", b"wxmsw"):
        assert dll not in data.lower()


@pytest.mark.parametrize("pending,failed", [(False, False), (True, False), (True, True)])
def test_native_bootstrap_recovers_before_loading_any_application_runtime(
        native_programs: tuple[Path, Path], tmp_path: Path, pending: bool, failed: bool) -> None:
    import shutil
    bootstrap = installed(native_programs, tmp_path)
    # A corrupt application Python DLL cannot stop the independent native bootstrap/helper.
    (tmp_path / "python314.dll").write_bytes(b"corrupt application DLL")
    runtime = tmp_path / recovery.HERE / "runtime"
    if pending:
        runtime.mkdir(parents=True)
        shutil.copy2(native_programs[1], runtime / recovery.PAYLOAD)
        (runtime.parent / recovery.JOURNAL).write_text("pending")
    env = dict(os.environ)
    if failed:
        env["UF_TEST_RECOVERY_FAIL"] = "1"
    if pending:
        env["UF_TEST_REQUIRE_RECOVERY"] = "1"
    result = subprocess.run([str(bootstrap), "--some-option"], cwd=tmp_path, env=env, timeout=15)
    assert result.returncode == (42 if failed else 0)
    assert (tmp_path / "recovery-started").exists() == (pending and not failed)
    args = started(tmp_path / "payload-started", 0 if failed else 15)
    assert (args is not None) == (not failed)
    if args is not None:
        # arguments pass through; --recovered tells the app not to hand the update back again
        assert args == ("--recovered --some-option" if pending else "--some-option")
    if failed:
        (error_log,) = (tmp_path / recovery.HERE).glob("bootstrap-error-*.txt")
        assert "42" in error_log.read_text()
    if pending and not failed:
        assert (runtime / "rescue-working-directory").exists()
        assert not (tmp_path / "rescue-working-directory").exists()


def test_native_bootstrap_checks_armed_marker_when_journals_are_missing(
        native_programs: tuple[Path, Path], tmp_path: Path) -> None:
    import shutil
    bootstrap = installed(native_programs, tmp_path)
    runtime = tmp_path / recovery.HERE / "runtime"
    runtime.mkdir(parents=True)
    shutil.copy2(native_programs[1], runtime / recovery.PAYLOAD)
    (runtime.parent / "armed").write_bytes(b"armed")
    result = subprocess.run([str(bootstrap)], cwd=tmp_path,
                            env=dict(os.environ, UF_TEST_REQUIRE_RECOVERY="1"), timeout=15)
    assert result.returncode == 0
    assert (tmp_path / "recovery-started").exists()
    assert started(tmp_path / "payload-started") is not None


def staged_copier(native_programs: tuple[Path, Path], app: Path) -> Path:
    import shutil
    staged = app / "update" / "new" / "Uncle Farmer"
    staged.mkdir(parents=True)
    shutil.copy2(native_programs[1], staged / recovery.PAYLOAD)
    return staged / recovery.PAYLOAD


@pytest.mark.parametrize("copier_exit", [0, updater.RESTART_WITHHELD])
def test_the_bootstrap_runs_the_update_then_starts_the_updated_app(
        native_programs: tuple[Path, Path], tmp_path: Path, copier_exit: int) -> None:
    bootstrap = installed(native_programs, tmp_path)
    copier = staged_copier(native_programs, tmp_path)
    token = "0123456789abcdef" * 2
    env = dict(os.environ, UF_TEST_COPIER_EXIT=str(copier_exit))
    result = subprocess.run([str(bootstrap), "--update", str(copier), "4242", "0.5.0-dev.7", token],
                            cwd=tmp_path, env=env, timeout=15)
    assert result.returncode == copier_exit
    args = (copier.parent / "copier-args.txt").read_bytes().decode("utf-16-le")
    assert args == '--apply-update "%s" 4242 0.5.0-dev.7 %s --bootstrap' % (tmp_path, token)
    if copier_exit:
        assert started(tmp_path / "payload-started", 1) is None     # the copier withheld the restart
    else:
        assert started(tmp_path / "payload-started") == ""           # the updated app, started from its folder


@pytest.mark.parametrize("change", ["outside update/new", "not the app", "parent folder", "forward slash",
                                    "bad token", "bad pid"])
def test_the_bootstrap_refuses_an_update_request_it_cannot_trust(
        native_programs: tuple[Path, Path], tmp_path: Path, change: str) -> None:
    import shutil
    bootstrap = installed(native_programs, tmp_path)
    copier = staged_copier(native_programs, tmp_path)
    pid, token = "4242", "0123456789abcdef" * 2
    target: Path | str = copier
    if change == "outside update/new":
        other = tmp_path / "elsewhere"
        other.mkdir()
        target = Path(shutil.copy2(copier, other / recovery.PAYLOAD))
    elif change == "not the app":
        target = Path(shutil.copy2(copier, copier.parent / "other.exe"))
    elif change == "parent folder":
        target = "\\".join([str(tmp_path / "update" / "new"), "..", "..", recovery.PAYLOAD])
    elif change == "forward slash":
        shutil.copy2(copier, tmp_path / recovery.PAYLOAD)
        target = str(tmp_path / "update" / "new") + "/../../" + recovery.PAYLOAD
    elif change == "bad token":
        token = "not-a-token"
    else:
        pid = "12x"
    result = subprocess.run([str(bootstrap), "--update", str(target), pid, "0.5.0", token], cwd=tmp_path, timeout=15)
    assert result.returncode != 0
    assert not list(tmp_path.rglob("copier-args.txt"))
    assert started(tmp_path / "payload-started", 0.5) is None
    assert list((tmp_path / recovery.HERE).glob("bootstrap-error-*.txt"))


def test_the_bootstrap_relaunches_only_after_the_process_has_ended(
        native_programs: tuple[Path, Path], tmp_path: Path) -> None:
    bootstrap = installed(native_programs, tmp_path)
    with subprocess.Popen([PYTHON, "-c", "import time; time.sleep(2)"]) as waiting:
        began = time.monotonic()
        result = subprocess.run([str(bootstrap), "--relaunch", str(waiting.pid)], cwd=tmp_path, timeout=30)
        assert waiting.poll() is not None              # the bootstrap returned only after it ended
    assert result.returncode == 0 and time.monotonic() - began >= 1.5
    assert started(tmp_path / "payload-started") == ""


def test_killing_the_bootstrap_takes_the_paused_copier_with_it(
        native_programs: tuple[Path, Path], tmp_path: Path) -> None:
    """The app cancels a paused update by ending the process it started: bootstrap.exe."""
    bootstrap = installed(native_programs, tmp_path)
    copier = staged_copier(native_programs, tmp_path)
    process = subprocess.Popen([str(bootstrap), "--update", str(copier), "4242", "0.5.0", "ab" * 16],
                               cwd=tmp_path, env=dict(os.environ, UF_TEST_COPIER_SLEEP="1"))
    note = copier.parent / "copier-pid.txt"
    deadline = time.monotonic() + 15
    while not note.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    time.sleep(.2)
    copier_pid = int(note.read_bytes().decode("utf-16-le"))
    assert updater._alive(copier_pid)  # pyright: ignore[reportPrivateUsage]
    process.terminate()
    process.wait(timeout=10)
    deadline = time.monotonic() + 10
    while updater._alive(copier_pid) and time.monotonic() < deadline:  # pyright: ignore[reportPrivateUsage]
        time.sleep(.05)
    assert not updater._alive(copier_pid)  # pyright: ignore[reportPrivateUsage]
    assert started(tmp_path / "payload-started", 0.5) is None


def test_the_app_hands_an_unfinished_update_to_the_bootstrap(
        trees: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer import __main__ as entry
    app, _ = trees
    (app / recovery.BOOTSTRAP).write_bytes(b"bootstrap")
    recovery.begin(str(app), "0.4.0", "0.5.0", "DOWNLOADING")        # an interrupted, unfinished update
    launched: list[list[str]] = []
    def popen(args: list[str], **kwargs: object) -> None:
        launched.append(args)
    monkeypatch.setattr(subprocess, "Popen", popen)
    from unclefarmer import paths
    monkeypatch.setattr(paths, "APP", str(app))
    monkeypatch.setattr(paths, "frozen", lambda: True)
    monkeypatch.setattr(sys, "argv", ["UncleFarmer.exe"])
    with pytest.raises(SystemExit) as stopped:
        entry.main()
    assert stopped.value.code == 0
    assert launched == [[str(app / recovery.BOOTSTRAP), "--relaunch", str(os.getpid())]]


def test_the_app_never_hands_the_update_back_right_after_a_recovery(
        trees: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes
    from unclefarmer import __main__ as entry, paths
    app, _ = trees
    (app / recovery.BOOTSTRAP).write_bytes(b"bootstrap")
    recovery.begin(str(app), "0.4.0", "0.5.0", "DOWNLOADING")
    launched: list[list[str]] = []
    shown: list[str] = []
    def popen(args: list[str], **kwargs: object) -> None:
        launched.append(args)
    monkeypatch.setattr(subprocess, "Popen", popen)
    def box(owner: object, text: str, title: str, flags: int) -> None:
        shown.append(text)
    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", box)
    monkeypatch.setattr(paths, "APP", str(app))
    monkeypatch.setattr(paths, "frozen", lambda: True)
    monkeypatch.setattr(sys, "argv", ["UncleFarmer.exe", "--recovered"])
    with pytest.raises(SystemExit) as stopped:
        entry.main()
    assert stopped.value.code != 0 and launched == []
    assert shown and "bootstrap.exe" in shown[0]


def test_a_finished_or_absent_update_needs_no_bootstrap(trees: tuple[Path, Path]) -> None:
    from unclefarmer import __main__ as entry
    app, _ = trees
    assert not entry._unfinished_update(str(app))  # pyright: ignore[reportPrivateUsage]
    record = recovery.begin(str(app), "0.4.0", "0.5.0", "DOWNLOADING")
    assert entry._unfinished_update(str(app))  # pyright: ignore[reportPrivateUsage]
    recovery.save(str(app), record, "ROLLED_BACK", "test")
    assert not entry._unfinished_update(str(app))  # pyright: ignore[reportPrivateUsage]
    (app / recovery.HERE / recovery.JOURNAL).write_text("{torn")
    (app / recovery.HERE / (recovery.JOURNAL + ".prev")).write_text("{torn")
    assert entry._unfinished_update(str(app))  # pyright: ignore[reportPrivateUsage]


def short_name(path: Path) -> str:
    import ctypes
    buffer = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 32768)
    return buffer.value if 0 < n < 32768 else str(path)


@pytest.mark.parametrize("short_side", ["bootstrap", "copier"])
def test_the_bootstrap_accepts_short_and_long_names_of_the_same_folder(
        native_programs: tuple[Path, Path], tmp_path: Path, short_side: str) -> None:
    """Found by the real-binary test: the app named its folder by the 8.3 name, Windows told the
    bootstrap the long one, and a valid update was refused."""
    bootstrap = installed(native_programs, tmp_path)
    copier = staged_copier(native_programs, tmp_path)
    short_bootstrap, short_copier = short_name(bootstrap), short_name(copier)
    if short_bootstrap == str(bootstrap):
        pytest.skip("this volume has no 8.3 names")
    command = [short_bootstrap if short_side == "bootstrap" else str(bootstrap), "--update",
               short_copier if short_side == "copier" else str(copier), "4242", "0.5.0", "ab" * 16]
    result = subprocess.run(command, cwd=tmp_path, timeout=15)
    assert result.returncode == 0
    assert started(tmp_path / "payload-started") == ""


def test_the_rescue_runtime_accepts_its_own_short_name(trees: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Found by the real-binary test, and why an updated copy never opened again: a frozen app's
    sys.executable is the 8.3 short name when a folder has spaces ("Uncle Farmer"), bootstrap.exe
    passes the long one, and the rescue runtime refused to run, so nothing was started."""
    from unclefarmer import __main__ as entry
    app, _ = trees
    runtime = app / recovery.HERE / "runtime"
    runtime.mkdir(parents=True)
    (runtime / recovery.PAYLOAD).write_bytes(b"rescue")
    short = short_name(runtime / recovery.PAYLOAD)
    if short == str(runtime / recovery.PAYLOAD):
        pytest.skip("this volume has no 8.3 names")
    calls: list[str] = []
    monkeypatch.setattr(sys, "argv", ["UncleFarmer.exe", "--recover-update", str(app)])
    monkeypatch.setattr(sys, "executable", short)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    def recovered(path: str) -> str:
        calls.append(path)
        return "COMMITTED"
    monkeypatch.setattr(recovery, "recover", recovered)
    entry.main()
    assert calls == [str(app)]


@pytest.mark.parametrize("written_short", [True, False])
def test_a_journal_reads_under_the_short_and_the_long_name(trees: tuple[Path, Path], written_short: bool) -> None:
    app, _ = trees
    short = short_name(app)
    if short == str(app):
        pytest.skip("this volume has no 8.3 names")
    writer, reader = (short, str(app)) if written_short else (str(app), short)
    record = recovery.begin(writer, "0.4.0", "0.5.0", "DOWNLOADING")
    loaded = recovery.load(reader)
    assert loaded is not None and loaded["id"] == record["id"]
    recovery.save(reader, loaded, "ROLLED_BACK", "test")
    assert recovery.recover(reader) == "ROLLED_BACK"


def test_the_rescue_runtime_accepts_nuitkas_python_exe(trees: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Found by the real-binary test: in a Nuitka build sys.executable is a python.exe beside
    UncleFarmer.exe. The rescue entry compared it with the exe's own name and refused every
    recovery, so the old front door never started an updated copy."""
    from unclefarmer import __main__ as entry
    app, _ = trees
    runtime = app / recovery.HERE / "runtime"
    runtime.mkdir(parents=True)
    calls: list[str] = []
    monkeypatch.setattr(sys, "argv", ["UncleFarmer.exe", "--recover-update", str(app)])
    monkeypatch.setattr(sys, "executable", os.path.join(short_name(runtime), "python.exe"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    def recovered(path: str) -> str:
        calls.append(path)
        return "COMMITTED"
    monkeypatch.setattr(recovery, "recover", recovered)
    entry.main()
    assert calls == [str(app)]


def test_python_exe_elsewhere_is_still_refused(trees: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer import __main__ as entry
    app, _ = trees
    monkeypatch.setattr(sys, "argv", ["UncleFarmer.exe", "--recover-update", str(app)])
    monkeypatch.setattr(sys, "executable", str(app / "python.exe"))       # the installation, not the rescue copy
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    def refused(path: str) -> str:
        pytest.fail("recovery ran outside the rescue runtime")
    monkeypatch.setattr(recovery, "recover", refused)
    with pytest.raises(SystemExit) as stopped:
        entry.main()
    assert stopped.value.code == 1
