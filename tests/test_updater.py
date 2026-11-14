# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Self-updating against GitHub, with no network at all: _open is replaced by a fake response.

Covers reading the newest release, the version comparison, downloading with size/checksum checks,
unpacking a fake zip, applying it over a folder, and the result/log bookkeeping.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from typing import Any, NoReturn, Self, cast

import pytest

from unclefarmer import updater
from pathlib import Path
from conftest import as_kind, failing, stand_in


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The app folder, its logs and its staging folder (update/), all in the test's own temp
    folder: no test writes busy.json, a download or a log into the checkout."""
    app = tmp_path / "home"
    (app / "logs").mkdir(parents=True)
    monkeypatch.setattr(updater.paths, "APP", str(app))
    monkeypatch.setattr(updater.paths, "LOGS", str(app / "logs"))
    monkeypatch.setattr(updater, "UPDATE", str(app / "update"))
    monkeypatch.setattr(updater, "NEW", str(app / "update" / "new"))
    monkeypatch.setattr(updater, "RESULT", str(app / "update" / "result.json"))
    monkeypatch.setattr(updater, "LOG", str(app / "logs" / "update.log"))
    return app


def update_log(home: Path) -> str:
    path = home / "logs" / "update.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


class Resp:
    """A fake open response: a body readable in chunks, plus headers."""

    def __init__(self, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.body = body
        self.headers = headers or {}
        self._i = 0

    def read(self, n: int | None = -1) -> bytes:
        if n is None or n < 0:
            chunk, self._i = self.body[self._i:], len(self.body)
        else:
            chunk = self.body[self._i:self._i + n]
            self._i += len(chunk)
        return chunk

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def release(**over: Any) -> dict[str, Any]:
    rel: dict[str, Any] = {"tag_name": "v0.3.0", "body": "  notes here \n", "draft": False, "prerelease": False,
           "assets": [{"name": "UncleFarmer-0.3.0.zip", "size": 123, "digest": "sha256:ABCdef",
                       "state": "uploaded", "browser_download_url": "https://example/dl.zip", "url": "https://api/asset"}]}
    rel.update(over)
    return rel


def for_download(asset: str = "a.zip", size: int = 0, sha256: str = "", version: str = "0.3.0") -> updater.Release:
    """A release as download() is handed one; an empty sha256 means GitHub gave no digest."""
    return {"version": version, "tag": "v" + version, "notes": "", "asset": asset, "size": size, "url": "u",
            "sha256": sha256}


def popen_with(record: Callable[[list[str], dict[str, object]], object]) -> Callable[..., Any]:
    """subprocess.Popen for the updater: hands each launch to record instead of starting it
    (Popen is a class with overloaded constructors, so stand_in can't take its signature)."""
    def popen(args: list[str], **k: object) -> Any:
        record(args, k)
        if "--apply-update" in args:
            updater.json.save(os.path.join(args[2], "update", updater.BUSY),
                              {"pid": 4242, "what": "update copier", "since": time.time(), "token": args[-1]})
        from types import SimpleNamespace
        return SimpleNamespace(pid=4242, poll=lambda: None, terminate=lambda: None)
    return popen


def fake_open(monkeypatch: pytest.MonkeyPatch,
              result: urllib.error.HTTPError | urllib.error.URLError | Resp | Callable[[], Resp]) -> list[tuple[str, str, float]]:
    calls: list[tuple[str, str, float]] = []

    def _open(url: str, accept: str, timeout: float) -> Resp:
        calls.append((url, accept, timeout))
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result()
        return result

    monkeypatch.setattr(updater, "_open", _open)
    return calls


# ---------------------------------------------------------------- version helper
@pytest.mark.parametrize("value, want", [
    ("1.2.3", (1, 2, 3)), ("v0.2.1", (0, 2, 1)), ("1.2.3.4.5", (1, 2, 3, 4)), ("", (0,)), ("junk", (0,)),
    ("1.0.1-dev.129", (1, 0, 1, 129)),                     # a dev build: its release's numbers, then the build
])
def test_version_tuple(value: str, want: tuple[int] | tuple[int, int, int] | tuple[int, int, int, int]) -> None:
    assert updater.version_tuple(value) == want


@pytest.mark.parametrize("value, want", [
    ("0.3.0", True), ("0.2.2", True), ("0.2.1", False), ("0.2.0", False), ("1.0", True),
])
def test_is_newer(value: str, want: bool) -> None:
    assert updater.is_newer(value, than="0.2.1") is want


# ---------------------------------------------------------------- latest
def test_latest_without_a_repository_never_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "REPO", "")
    monkeypatch.setattr(updater, "_open", stand_in(updater._open, failing("asked the network")))
    assert updater.latest() is None


def test_latest_none_when_github_has_no_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "REPO", "owner/repo")
    fake_open(monkeypatch, urllib.error.HTTPError("u", 404, "nf", cast(Any, None), None))
    assert updater.latest() is None


def test_latest_raises_on_a_server_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "REPO", "owner/repo")
    fake_open(monkeypatch, urllib.error.HTTPError("u", 500, "boom", cast(Any, None), None))
    with pytest.raises(updater.UpdateError, match="500"):
        updater.latest()


def test_latest_raises_on_a_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "REPO", "owner/repo")
    fake_open(monkeypatch, urllib.error.URLError("no dns"))
    with pytest.raises(updater.UpdateError, match="no dns"):
        updater.latest()


def test_latest_wraps_a_bad_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "REPO", "owner/repo")
    fake_open(monkeypatch, Resp(b"not json"))
    with pytest.raises(updater.UpdateError):
        updater.latest()


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
def test_latest_skips_drafts_and_prereleases(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    monkeypatch.setattr(updater, "REPO", "owner/repo")
    fake_open(monkeypatch, Resp(updater.json.dumps(release(**{flag: True})).encode()))
    assert updater.latest() is None


def test_latest_skips_a_release_without_our_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "REPO", "owner/repo")
    fake_open(monkeypatch, Resp(updater.json.dumps(release(assets=[])).encode()))
    assert updater.latest() is None


def test_latest_reads_a_public_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "REPO", "owner/repo")
    monkeypatch.delenv("UNCLEFARMER_GITHUB_TOKEN", raising=False)
    fake_open(monkeypatch, Resp(updater.json.dumps(release()).encode()))
    got = updater.latest()
    assert got == {"version": "0.3.0", "tag": "v0.3.0", "notes": "notes here",
                   "asset": "UncleFarmer-0.3.0.zip", "size": 123,
                   "url": "https://example/dl.zip", "sha256": "abcdef"}


def test_latest_with_a_token_uses_the_api_asset_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "REPO", "owner/repo")
    monkeypatch.setenv("UNCLEFARMER_GITHUB_TOKEN", "secret")
    fake_open(monkeypatch, Resp(updater.json.dumps(release()).encode()))
    got = updater.latest()
    assert got is not None
    assert got["url"] == "https://api/asset"
    assert got["sha256"] == "abcdef"


def test_latest_without_a_digest_has_no_checksum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "REPO", "owner/repo")
    monkeypatch.delenv("UNCLEFARMER_GITHUB_TOKEN", raising=False)
    rel = release(assets=[{"name": "UncleFarmer-0.3.0.zip", "size": 1,
                           "state": "uploaded", "browser_download_url": "u"}])
    fake_open(monkeypatch, Resp(updater.json.dumps(rel).encode()))
    latest = updater.latest()
    assert latest is not None and latest["sha256"] == ""


# ---------------------------------------------------------------- check
def test_check_offers_a_newer_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "latest", stand_in(updater.latest, lambda timeout=10, update_channel="master": {"version": "99.0"}))
    newer = updater.check()
    assert newer is not None and newer["version"] == "99.0"


def test_check_ignores_an_older_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "latest", stand_in(updater.latest, lambda timeout=10, update_channel="master": {"version": "0.0.1"}))
    assert updater.check() is None


def test_check_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(timeout: float=10, update_channel: str = "master") -> NoReturn:
        raise updater.UpdateError("no net")

    monkeypatch.setattr(updater, "latest", boom)
    assert updater.check() is None


def test_check_without_a_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "latest", stand_in(updater.latest, lambda timeout=10, update_channel="master": None))
    assert updater.check() is None


# ---------------------------------------------------------------- download
def test_download_writes_the_zip_and_checks_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    body = b"zip-bytes" * 100
    digest = hashlib.sha256(body).hexdigest()
    fake_open(monkeypatch, Resp(body, {"Content-Length": str(len(body))}))
    rel = for_download(asset="UncleFarmer-0.3.0.zip", size=len(body), sha256=digest)
    path = updater.download(rel)
    assert path == os.path.join(updater.UPDATE, rel["asset"])
    with open(path, "rb") as f:
        assert f.read() == body
    assert not os.path.exists(path + ".part")


def test_download_rejects_a_short_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    body = b"abc"
    fake_open(monkeypatch, Resp(body))                    # no Content-Length; the declared size is used
    with pytest.raises(updater.UpdateError, match="stopped at"):
        updater.download(for_download(size=10))


def test_download_rejects_a_wrong_checksum(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    body = b"abc"
    fake_open(monkeypatch, Resp(body, {"Content-Length": str(len(body))}))
    rel = for_download(size=len(body), sha256="0" * 64)
    with pytest.raises(updater.UpdateError, match="damaged"):
        updater.download(rel)


def test_download_can_be_cancelled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    body = b"abc"
    fake_open(monkeypatch, Resp(body, {"Content-Length": str(len(body))}))
    with pytest.raises(updater.UpdateError, match="cancelled"):
        updater.download(for_download(size=len(body)), progress=lambda done, total: False)


def test_download_wraps_a_connection_drop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Bad(Resp):
        def read(self, n: int | None = -1) -> bytes:
            raise urllib.error.URLError("reset")

    fake_open(monkeypatch, Bad())
    with pytest.raises(updater.UpdateError, match="download failed: reset"):
        updater.download(for_download())


# ---------------------------------------------------------------- unpack
def test_unpack_returns_the_folder_with_the_exe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    z = tmp_path / "r.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Uncle Farmer/UncleFarmer.exe", "MZ")
        zf.writestr("Uncle Farmer/data/x.json", "{}")
    folder = updater.unpack(str(z))
    assert os.path.isfile(os.path.join(folder, updater.EXE))


def test_unpack_rejects_a_non_zip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"definitely not a zip")
    with pytest.raises(updater.UpdateError, match="not a zip"):
        updater.unpack(str(bad))


def test_unpack_needs_the_exe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    z = tmp_path / "r.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(updater.UpdateError, match="no UncleFarmer.exe"):
        updater.unpack(str(z))


def test_unpack_refuses_a_file_outside_the_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../evil.txt", "boom")
    with pytest.raises(updater.UpdateError, match="unsafe update archive entry"):
        updater.unpack(str(z))


# ---------------------------------------------------------------- apply
def make_app(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    (app / "_internal").mkdir(parents=True)
    (app / "_internal" / "old.dll").write_text("old", encoding="utf-8")
    (app / "agent" / "run").mkdir(parents=True)
    (app / "agent" / "run" / "port.txt").write_text("live", encoding="utf-8")
    (app / "settings.json").write_text("mine", encoding="utf-8")
    (app / "profile.txt").write_text("keep me", encoding="utf-8")
    (app / updater.EXE).write_text("old exe", encoding="utf-8")
    return app


def make_release(tmp_path: Path) -> Path:
    src = tmp_path / "new"
    (src / "_internal").mkdir(parents=True)
    (src / "_internal" / "new.dll").write_text("new", encoding="utf-8")
    (src / "agent" / "run").mkdir(parents=True)
    (src / "agent" / "run" / "port.txt").write_text("from-release", encoding="utf-8")
    (src / "agent" / "other.txt").write_text("n", encoding="utf-8")
    (src / "data").mkdir()
    (src / "data" / "d.json").write_text("{}", encoding="utf-8")
    (src / "settings.json").write_text("release", encoding="utf-8")
    (src / "main.exe").write_text("bin", encoding="utf-8")
    for pattern in updater.REQUIRED:
        path = src / pattern.replace("*", "314")
        if pattern in updater.RUNTIME_DIRS:
            path = path / "__init__.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"new")
    updater.write_build(str(src), "0.5.0", "master")
    return src


def copier_environment(monkeypatch: pytest.MonkeyPatch, app: Path, version: str = "0.5.0") -> Path:
    (app / updater.EXE).write_bytes(b"old exe")
    for pattern in updater.REQUIRED:
        path = app / pattern.replace("*", "314")
        if pattern in updater.RUNTIME_DIRS:
            path /= "__init__.py"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old runtime")
    updater.write_build(str(app), "0.4.0", "master")
    src = make_release(app / "update")
    updater.write_build(str(src), version, "master")
    monkeypatch.setattr(updater, "frozen", lambda: True)
    monkeypatch.setattr(updater.sys, "executable", str(src / updater.EXE))
    return src


def test_apply_replaces_merges_and_keeps_the_players_things(tmp_path: Path) -> None:
    app, src = make_app(tmp_path), make_release(tmp_path)
    logs: list[str] = []
    updater.apply(str(app), str(src), logs.append)
    assert (app / "_internal" / "new.dll").read_text(encoding="utf-8") == "new"
    assert not (app / "_internal" / "old.dll").exists()
    assert not (app / "_internal.old").exists()
    assert (app / "agent" / "other.txt").read_text(encoding="utf-8") == "n"
    assert (app / "agent" / "run" / "port.txt").read_text(encoding="utf-8") == "live"  # never copied
    assert (app / "data" / "d.json").read_text(encoding="utf-8") == "{}"
    assert (app / "settings.json").read_text(encoding="utf-8") == "mine"               # NEVER
    assert (app / "main.exe").read_text(encoding="utf-8") == "bin"
    assert "replaced _internal" in logs and "updated agent" in logs


def test_apply_puts_the_old_folder_back_when_the_copy_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app, src = make_app(tmp_path), make_release(tmp_path)

    def boom(*a: object, **k: object) -> NoReturn:
        raise OSError("disk full")

    monkeypatch.setattr(updater.shutil, "copy2", boom)
    with pytest.raises(OSError, match="disk full"):
        updater.apply(str(app), str(src), lambda text: None)
    assert (app / "_internal" / "old.dll").read_text(encoding="utf-8") == "old"
    assert not (app / "_internal.old").exists()


# ---------------------------------------------------------------- start/apply/result
def test_start_apply_launches_the_new_exe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(updater.subprocess, "Popen", popen_with(lambda args, k: calls.append((args, k))))
    monkeypatch.setattr(updater.paths, "APP", str(tmp_path))
    monkeypatch.setattr(updater, "UPDATE", str(tmp_path / "update"))
    monkeypatch.setattr(updater, "NEW", str(tmp_path / "update/new"))
    src = copier_environment(monkeypatch, tmp_path, "0.3.0")
    updater.start_apply(str(src), "0.3.0")
    args, kw = calls[0]
    assert args[:5] == [str(src / updater.recovery.PAYLOAD), "--apply-update", str(tmp_path), str(os.getpid()), "0.3.0"]
    assert re.fullmatch(r"[0-9a-f]{32}", args[5])
    assert kw["cwd"] == str(src)


def test_run_apply_writes_the_result_and_restarts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    copier_environment(monkeypatch, app)
    monkeypatch.setattr(updater, "_wait_for_exit", stand_in(updater._wait_for_exit, lambda pid: None))
    monkeypatch.setattr(updater, "apply", stand_in(updater.apply, lambda app, src, log: log("did it")))
    spawned: list[list[str]] = []
    monkeypatch.setattr(updater.subprocess, "Popen", popen_with(lambda args, k: spawned.append(args)))
    updater.run_apply([str(app), "4242", "0.5.0"])
    with open(app / "update" / "result.json", encoding="utf-8") as f:
        result = updater.json.load(f)
    assert result == {"version": "0.5.0", "ok": True, "error": ""}
    assert spawned == [[os.path.join(str(app), updater.EXE)]]
    assert "copier: did it" in (app / "logs" / "update.log").read_text(encoding="utf-8")


def test_run_apply_reports_a_failure_and_still_restarts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    copier_environment(monkeypatch, app)

    def boom(a: str, s: str, log: Callable[[str], None]) -> NoReturn:
        raise RuntimeError("nope")

    monkeypatch.setattr(updater, "_wait_for_exit", stand_in(updater._wait_for_exit, lambda pid: None))
    monkeypatch.setattr(updater, "apply", boom)
    monkeypatch.setattr(updater.subprocess, "Popen", popen_with(lambda args, k: None))
    updater.run_apply([str(app), "1", "0.5.0"])
    with open(app / "update" / "result.json", encoding="utf-8") as f:
        result = updater.json.load(f)
    assert result["ok"] is False and result["error"] == "RuntimeError: nope"
    assert "FAILED RuntimeError: nope" in (app / "logs" / "update.log").read_text(encoding="utf-8")
    assert not (app / "update" / "update.log").exists()


def test_take_result_reads_once_and_logs_it(home: Path) -> None:
    (home / "update").mkdir()
    (home / "update" / "result.json").write_text(updater.json.dumps({"version": "0.3", "ok": True, "error": ""}),
                                                 encoding="utf-8")
    result = updater.take_result()
    assert result is not None and result["ok"] is True and result["version"] == "0.3"
    assert not (home / "update" / "result.json").exists()
    assert "the update to 0.3 finished" in update_log(home)
    assert updater.take_result() is None


def test_take_result_none_when_there_was_no_update() -> None:
    assert updater.take_result() is None


def test_an_unreadable_result_is_a_failed_update_said_once(home: Path) -> None:
    (home / "update").mkdir()
    (home / "update" / "result.json").write_text("{bad json", encoding="utf-8")
    failed = updater.take_result()
    assert failed is not None and failed["ok"] is False and "unreadable" in failed["error"]
    assert updater.take_result() is None


def test_take_result_leaves_the_staging_folder_to_clean(home: Path) -> None:
    up = staged(home)
    (up / "result.json").write_text(updater.json.dumps({"version": "0.3", "ok": False, "error": "x"}),
                                    encoding="utf-8")
    failed = updater.take_result()
    assert failed is not None and failed["ok"] is False
    assert (up / "UncleFarmer-0.3.zip").exists()       # the window cleans it afterwards (clean(wait=...))
    assert updater.clean() is True and not up.exists()  # a failed update's copy is of no further use


# ---------------------------------------------------------------- the staging folder
def staged(home: Path) -> Path:
    """update/ as an update leaves it: an unpacked copy, a zip and a half download."""
    up = home / "update"
    (up / "new" / "_internal").mkdir(parents=True)
    (up / "new" / "UncleFarmer.exe").write_text("MZ", encoding="utf-8")
    (up / "new" / "_internal" / "x.dll").write_text("dll", encoding="utf-8")
    (up / "UncleFarmer-0.3.zip").write_text("zip", encoding="utf-8")
    (up / "UncleFarmer-0.2.zip.part").write_text("half", encoding="utf-8")
    return up


def keepsakes(home: Path) -> dict[Path, str]:
    """The app, the player's things and the logs, which a cleanup must never touch."""
    files = {home / "UncleFarmer.exe": "app", home / "_internal" / "core.dll": "core",
             home / "settings.json": "settings", home / "profiles" / "Hero.json": "profile",
             home / "logs" / "bootup.log": "boot", home / "plans" / "p.json": "plan"}
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return files


def untouched(files: dict[Path, str]) -> bool:
    return all(path.read_text(encoding="utf-8") == text for path, text in files.items())


def busy_file(home: Path, pid: int, since: float | None = None, what: str = "download") -> None:
    (home / "update").mkdir(exist_ok=True)
    (home / "update" / updater.BUSY).write_text(updater.json.dumps(
        {"pid": pid, "what": what, "since": time.time() if since is None else since}), encoding="utf-8")


def alive_only(monkeypatch: pytest.MonkeyPatch, *pids: int) -> None:
    monkeypatch.setattr(updater, "_alive", stand_in(updater._alive, lambda pid: pid in pids or pid == os.getpid()))


def junction(link: Path, target: Path) -> None:
    import _winapi
    _winapi.CreateJunction(str(target), str(link))


def test_clean_empties_and_removes_the_staging_folder_and_nothing_else(home: Path) -> None:
    keep = keepsakes(home)
    up = staged(home)
    assert updater.clean() is True
    assert not up.exists()
    assert untouched(keep)
    assert "staging folder cleaned" in update_log(home)


def test_clean_keeps_a_result_still_to_be_read(home: Path) -> None:
    up = staged(home)
    (up / "result.json").write_text("{}", encoding="utf-8")
    assert updater.clean() is True
    assert sorted(os.listdir(up)) == ["result.json"]


def test_clean_with_no_staging_folder_is_fine(home: Path) -> None:
    assert updater.clean() is True
    assert not (home / "update").exists()


def test_the_old_update_log_moves_into_logs_before_the_folder_goes(home: Path) -> None:
    up = staged(home)
    (up / "update.log").write_text("12:00:00 updating to 0.2\n", encoding="utf-8")
    assert updater.clean() is True
    assert not up.exists()
    text = update_log(home)
    assert "12:00:00 updating to 0.2" in text and "an older version" in text


@pytest.mark.parametrize("where", ["the app", "logs", "elsewhere", "another update"])
def test_clean_refuses_any_folder_but_the_apps_own_update_folder(home: Path, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    keep = keepsakes(home)
    folder = {"the app": home, "logs": home / "logs", "elsewhere": tmp_path / "elsewhere",
              "another update": tmp_path / "other" / "update"}[where]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "precious.txt").write_text("mine", encoding="utf-8")
    monkeypatch.setattr(updater, "UPDATE", str(folder))
    assert updater.clean() is False
    assert (folder / "precious.txt").read_text(encoding="utf-8") == "mine"
    assert untouched(keep)
    assert "staging cleanup refused" in update_log(home)


def test_a_path_that_climbs_out_is_not_the_staging_folder(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keep = keepsakes(home)
    monkeypatch.setattr(updater, "UPDATE", str(home / "update" / ".." / "logs" / ".." / "update" / ".."))
    assert updater.clean() is False
    assert untouched(keep)


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows'")
def test_clean_removes_a_junction_but_never_what_it_points_at(home: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("mine", encoding="utf-8")
    up = staged(home)
    junction(up / "new" / "link", outside)
    assert updater.clean() is True
    assert not up.exists()
    assert (outside / "precious.txt").read_text(encoding="utf-8") == "mine"


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows'")
def test_a_staging_folder_that_is_a_junction_is_left_alone(home: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / "precious.txt").write_text("mine", encoding="utf-8")
    junction(home / "update", other)
    assert updater.clean() is False
    assert (other / "precious.txt").read_text(encoding="utf-8") == "mine"
    os.rmdir(home / "update")


def test_clean_is_put_off_while_another_process_uses_the_folder(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    up = staged(home)
    busy_file(home, 999999, what="update copier")
    alive_only(monkeypatch, 999999)
    assert updater.clean() is False
    assert (up / "new" / "UncleFarmer.exe").exists() and (up / "UncleFarmer-0.3.zip").exists()
    assert "put off: the update copier of process 999999" in update_log(home)
    alive_only(monkeypatch)                          # it has exited: the next start cleans
    assert updater.clean() is True and not up.exists()


def test_clean_waits_for_the_closing_update_copier(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    up = staged(home)
    busy_file(home, 4242, what="update copier")
    gone: list[int] = []
    monkeypatch.setattr(updater, "_alive", stand_in(updater._alive, lambda pid: pid == os.getpid() or
                                                    (pid == 4242 and not gone)))
    waited: list[tuple[int, float]] = []

    def wait(pid: int, seconds: float = 120) -> None:
        waited.append((pid, seconds))
        gone.append(pid)
    monkeypatch.setattr(updater, "_wait_for_exit", wait)
    assert updater.clean(wait=60) is True
    assert waited == [(4242, 60)] and not up.exists()


def test_nothing_is_cleaned_during_this_processs_own_update(home: Path) -> None:
    up = staged(home)
    with updater.claimed("download"):
        assert updater.clean() is False
        assert (up / "UncleFarmer-0.3.zip").exists()
    assert not (up / updater.BUSY).exists()
    assert updater.clean() is True


@pytest.mark.parametrize("content", ["{bad", '{"pid": "x", "since": 1}', '{"what": "download"}', "[]"])
def test_a_broken_busy_file_counts_for_nothing(home: Path, content: str) -> None:
    (home / "update").mkdir()
    (home / "update" / updater.BUSY).write_text(content, encoding="utf-8")
    assert updater.busy() is None
    assert updater.clean() is True


def test_a_stale_busy_file_counts_for_nothing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    busy_file(home, 999999, since=time.time() - updater.STALE_SECONDS - 1)
    alive_only(monkeypatch, 999999)                  # even if that id runs again (a reused id)
    assert updater.busy() is None


def test_a_busy_file_of_a_process_that_is_gone_counts_for_nothing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    busy_file(home, 999999)
    alive_only(monkeypatch)
    assert updater.busy() is None


def test_claims_nest_and_the_last_one_lets_go(home: Path) -> None:
    with updater.claimed("update"):
        with updater.claimed("download"):
            holder = updater.busy()
            assert holder is not None and holder["pid"] == os.getpid() and holder["what"] == "update"
        assert (home / "update" / updater.BUSY).exists()
    assert not (home / "update" / updater.BUSY).exists()


def test_another_processs_update_blocks_a_new_one(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    busy_file(home, 999999)
    alive_only(monkeypatch, 999999)
    with pytest.raises(updater.UpdateError, match="another update is under way"):
        with updater.claimed("download"):
            pass
    fake_open(monkeypatch, failing("no download while another update runs"))
    with pytest.raises(updater.UpdateError, match="another update is under way"):
        updater.download(for_download())
    assert (home / "update" / updater.BUSY).exists()       # still the other process's


@pytest.mark.skipif(os.name != "nt", reason="an open file can't be deleted on Windows only")
def test_a_file_in_use_is_left_and_said_and_goes_next_time(home: Path) -> None:
    up = staged(home)
    held = open(up / "new" / "UncleFarmer.exe", "rb")
    try:
        assert updater.clean() is False
        assert (up / "new" / "UncleFarmer.exe").exists()
        assert not (up / "UncleFarmer-0.3.zip").exists()      # the rest went
        assert "staging cleanup left 2 item(s)" in update_log(home)     # the file and its folder
    finally:
        held.close()
    assert updater.clean() is True and not up.exists()


def test_read_only_files_are_cleaned_too(home: Path) -> None:
    up = staged(home)
    os.chmod(up / "new" / "_internal" / "x.dll", stat.S_IREAD)
    os.chmod(up / "UncleFarmer-0.3.zip", stat.S_IREAD)
    assert updater.clean() is True and not up.exists()


def test_a_download_clears_the_older_ones_first(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    up = staged(home)
    body = b"new zip"
    fake_open(monkeypatch, Resp(body, {"Content-Length": str(len(body))}))
    path = updater.download(for_download(asset="UncleFarmer-0.4.zip", size=len(body)))
    assert sorted(os.listdir(up)) == ["UncleFarmer-0.4.zip"]       # no old zip, half download, copy or busy.json
    assert path == str(up / "UncleFarmer-0.4.zip")
    text = update_log(home)
    assert "download of UncleFarmer-0.4.zip (0.3.0) started" in text and "done: 7 bytes" in text


def test_a_failed_or_cancelled_download_leaves_no_half_file(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_open(monkeypatch, Resp(b"abc"))
    with pytest.raises(updater.UpdateError, match="stopped at"):
        updater.download(for_download(asset="a.zip", size=10))
    fake_open(monkeypatch, Resp(b"abc", {"Content-Length": "3"}))
    with pytest.raises(updater.UpdateError, match="cancelled"):
        updater.download(for_download(asset="b.zip", size=3), progress=lambda done, total: False)
    assert os.listdir(home / "update") == []
    text = update_log(home)
    assert "download of a.zip FAILED: the download stopped at 3 of 10 bytes" in text
    assert "download of b.zip cancelled" in text


def test_unpack_replaces_an_older_unpacked_copy(home: Path, tmp_path: Path) -> None:
    up = staged(home)
    z = tmp_path / "r.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Uncle Farmer/UncleFarmer.exe", "MZ2")
    folder = updater.unpack(str(z))
    assert (Path(folder) / updater.EXE).read_text(encoding="utf-8") == "MZ2"
    assert not (up / "new" / "_internal").exists()
    assert "unpacked r.zip" in update_log(home)


@pytest.mark.skipif(os.name != "nt", reason="an open file can't be deleted on Windows only")
def test_unpack_stops_when_the_old_copy_is_still_in_use(home: Path, tmp_path: Path) -> None:
    up = staged(home)
    z = tmp_path / "r.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Uncle Farmer/UncleFarmer.exe", "MZ2")
    with open(up / "new" / "UncleFarmer.exe", "rb"):
        with pytest.raises(updater.UpdateError, match="still in use"):
            updater.unpack(str(z))


def test_the_update_copier_holds_the_staging_folder_until_it_exits(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    copier_environment(monkeypatch, home)
    monkeypatch.setattr(updater, "_wait_for_exit", stand_in(updater._wait_for_exit, lambda pid, seconds=120: None))
    monkeypatch.setattr(updater, "apply", stand_in(updater.apply, lambda app, src, log: None))
    monkeypatch.setattr(updater.subprocess, "Popen", popen_with(lambda args, k: None))
    updater.run_apply([str(home), "4242", "0.5.0"])
    holder = updater.busy()
    assert holder is not None and holder["pid"] == os.getpid() and holder["what"] == "update copier"


def test_a_whole_update_leaves_only_the_app_and_its_logs(home: Path, tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Download, unpack, the copier, the restarted app's result and cleanup: what is left at the end."""
    keep = keepsakes(home)
    release_folder = make_release(tmp_path / "fixture")
    for pattern in updater.REQUIRED:
        path = home / pattern.replace("*", "314")
        if pattern in updater.RUNTIME_DIRS:
            path /= "__init__.py"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old runtime")
    updater.write_build(str(home), "0.4.0", "master")
    z = tmp_path / "UncleFarmer-0.5.0.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for path in release_folder.rglob("*"):
            if path.is_file():
                zf.write(path, "Uncle Farmer/" + path.relative_to(release_folder).as_posix())
    body = z.read_bytes()
    fake_open(monkeypatch, Resp(body, {"Content-Length": str(len(body))}))
    launched: list[list[str]] = []
    monkeypatch.setattr(updater.subprocess, "Popen", popen_with(lambda args, k: launched.append(args)))
    monkeypatch.setattr(updater, "_wait_for_exit", stand_in(updater._wait_for_exit, lambda pid, seconds=120: None))
    monkeypatch.setattr(updater, "frozen", lambda: True)
    with updater.claimed("update"):
        folder = updater.unpack(updater.download(for_download(asset=z.name, version="0.5.0", size=len(body),
                                                              sha256=hashlib.sha256(body).hexdigest())))
    handle = updater.start_apply(folder, "0.5.0")
    handle.activate()
    monkeypatch.setattr(updater.sys, "executable", os.path.join(folder, updater.EXE))   # the copier's own exe
    arguments = launched[0][2:]
    arguments[1] = "4242"  # emulate an exited parent; this test runs both roles in one process
    updater.run_apply(arguments)
    # here the copier ran in this process; in a real update it is another one, which has exited
    held = updater.json.loads((home / "update" / updater.BUSY).read_text(encoding="utf-8"))
    assert held["what"] == "update copier"
    busy_file(home, 4242, since=held["since"], what=held["what"])
    alive_only(monkeypatch)
    result = updater.take_result()
    assert result == {"version": "0.5.0", "ok": True, "error": ""}
    assert updater.clean(wait=1) is True
    assert not (home / "update").exists()
    assert (home / updater.EXE).read_text(encoding="utf-8") == keep[home / updater.EXE]
    assert (home / updater.recovery.PAYLOAD).read_text(encoding="utf-8") == "new"
    assert (home / "data" / "d.json").exists()
    del keep[home / "_internal" / "core.dll"]  # obsolete runtime is intentionally replaced
    assert untouched(keep)                           # settings, profiles, plans and logs as they were
    text = update_log(home)
    for stage in ("download of UncleFarmer-0.5.0.zip", "unpacked", "copier for 0.5.0 started", "copier: updating",
                  "copier: updated UncleFarmerApp.exe", "the update to 0.5.0 finished", "staging folder cleaned"):
        assert stage in text, stage


# ---------------------------------------------------------------- logs/update.log
def test_update_lines_go_to_the_logs_folder_with_time_level_and_pid(home: Path) -> None:
    updater.log("hello", "WARNING")
    line = update_log(home).splitlines()[-1]
    assert line.endswith("WARNING [%d] hello" % os.getpid())
    assert re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}  ", line)
    assert not (home / "update").exists()            # nothing written into the staging folder


def test_the_logs_folder_is_made_when_missing(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gone = tmp_path / "fresh" / "logs"
    monkeypatch.setattr(updater.paths, "LOGS", str(gone))
    updater.log("first line")
    assert "first line" in (gone / "update.log").read_text(encoding="utf-8")


def test_the_copier_makes_the_apps_logs_folder_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = tmp_path / "bare"
    app.mkdir()
    copier_environment(monkeypatch, app)
    monkeypatch.setattr(updater, "_wait_for_exit", stand_in(updater._wait_for_exit, lambda pid, seconds=120: None))
    monkeypatch.setattr(updater, "apply", stand_in(updater.apply, lambda app, src, log: log("copied")))
    monkeypatch.setattr(updater.subprocess, "Popen", popen_with(lambda args, k: None))
    updater.run_apply([str(app), "1", "0.5.0"])
    assert "copier: copied" in (app / "logs" / "update.log").read_text(encoding="utf-8")


# ---------------------------------------------------------------- the opener and token
def test_open_sends_the_token_only_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class Opener:
        def open(self, req: urllib.request.Request, timeout: float | None = None) -> str:
            captured["req"], captured["timeout"] = req, timeout
            return "resp"

    monkeypatch.setattr(updater.urllib.request, "build_opener", stand_in(updater.urllib.request.build_opener, lambda *handlers: as_kind(urllib.request.OpenerDirector, Opener())))
    monkeypatch.delenv("UNCLEFARMER_GITHUB_TOKEN", raising=False)
    assert updater._open("https://x", "application/json", 5) == "resp"
    headers = {k.lower(): v for k, v in captured["req"].headers.items()}
    assert "authorization" not in headers
    assert headers["user-agent"].startswith("UncleFarmer/")
    assert captured["timeout"] == 5
    monkeypatch.setenv("UNCLEFARMER_GITHUB_TOKEN", "secret")
    updater._open("https://x", "application/json", 5)
    assert captured["req"].get_header("Authorization") is None
    updater._open("https://api.github.com/repos/owner/repo", "application/json", 5)
    assert captured["req"].get_header("Authorization") == "Bearer secret"


@pytest.mark.parametrize("update_channel, body, url", [
    ("master", b"{}", "https://api.github.com/repos/afnannasir/uncle-farmer/releases/latest"),
    ("dev", b"[]", "https://api.github.com/repos/afnannasir/uncle-farmer/releases?per_page=30"),
])
def test_the_update_check_asks_only_our_own_repository(monkeypatch: pytest.MonkeyPatch, update_channel: str,
                                                       body: bytes, url: str) -> None:
    """Every copy installs what this address offers, so it must stay the project's own repository
    on GitHub's API. The addresses are written out, not built from REPO, LATEST or RELEASES: a
    changed owner, host or path fails here instead of being followed."""
    assert updater.REPO == "afnannasir/uncle-farmer"
    asked: list[str] = []

    class Opener:
        def open(self, req: urllib.request.Request, timeout: float | None = None) -> io.BytesIO:
            asked.append(req.full_url)
            return io.BytesIO(body)

    monkeypatch.setattr(updater.urllib.request, "build_opener", stand_in(updater.urllib.request.build_opener, lambda *handlers: as_kind(urllib.request.OpenerDirector, Opener())))
    monkeypatch.delenv("UNCLEFARMER_GITHUB_TOKEN", raising=False)
    assert updater.latest(update_channel=update_channel) is None
    assert asked == [url]


def test_redirect_drops_the_token_off_github() -> None:
    h = updater._NoAuthElsewhere()
    req = urllib.request.Request("https://api.github.com/x", headers={"Authorization": "Bearer t"})
    other = h.redirect_request(req, cast(Any, None), 302, "Found", cast(Any, {}), "https://objects.example/y")
    assert other is not None and other.get_header("Authorization") is None
    same = h.redirect_request(req, cast(Any, None), 302, "Found", cast(Any, {}), "https://api.github.com/z")
    assert same is not None and same.get_header("Authorization") == "Bearer t"


def test_frozen_is_the_shared_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    # tests/test_built_copy.py covers paths.frozen itself (Nuitka's marker, sys.frozen)
    monkeypatch.setattr(updater.paths, "frozen", lambda: True)
    assert updater.frozen() is True
    monkeypatch.setattr(updater.paths, "frozen", lambda: False)
    assert updater.frozen() is False


# ---------------------------------------------------------------- who holds the staging folder
def test_alive_knows_this_process_a_running_one_and_ids_that_are_no_process() -> None:
    import subprocess
    import sys
    assert updater._alive(os.getpid()) is True
    assert updater._alive(0) is False and updater._alive(-5) is False
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert updater._alive(child.pid) is True
    finally:
        child.kill()
        child.wait()
    assert updater._alive(child.pid) is False          # exited: not counted as busy any more


def test_a_busy_file_of_a_process_that_exited_lets_the_cleanup_go_ahead(home: Path) -> None:
    import subprocess
    import sys
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    up = staged(home)
    busy_file(home, child.pid, what="update copier")
    assert updater.busy() is None
    assert updater.clean() is True and not up.exists()
