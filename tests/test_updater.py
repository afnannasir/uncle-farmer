# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Self-updating against GitHub, with no network at all: _open is replaced by a fake response.

Covers reading the newest release, the version comparison, downloading with size/checksum checks,
unpacking a fake zip, applying it over a folder, and the result/log bookkeeping.
"""

from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from typing import Any, NoReturn, Self, cast

import pytest

from unclefarmer import updater
from pathlib import Path
from conftest import as_kind, failing, stand_in


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
                       "browser_download_url": "https://example/dl.zip", "url": "https://api/asset"}]}
    rel.update(over)
    return rel


def for_download(asset: str = "a.zip", size: int = 0, sha256: str = "") -> updater.Release:
    """A release as download() is handed one; an empty sha256 means GitHub gave no digest."""
    return {"version": "0.3.0", "tag": "v0.3.0", "notes": "", "asset": asset, "size": size, "url": "u",
            "sha256": sha256}


def popen_with(record: Callable[[list[str], dict[str, object]], object]) -> Callable[..., None]:
    """subprocess.Popen for the updater: hands each launch to record instead of starting it
    (Popen is a class with overloaded constructors, so stand_in can't take its signature)."""
    def popen(args: list[str], **k: object) -> None:
        record(args, k)
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
                           "browser_download_url": "u"}])
    fake_open(monkeypatch, Resp(updater.json.dumps(rel).encode()))
    latest = updater.latest()
    assert latest is not None and latest["sha256"] == ""


# ---------------------------------------------------------------- check
def test_check_offers_a_newer_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "latest", stand_in(updater.latest, lambda timeout=10: {"version": "99.0"}))
    newer = updater.check()
    assert newer is not None and newer["version"] == "99.0"


def test_check_ignores_an_older_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "latest", stand_in(updater.latest, lambda timeout=10: {"version": "0.0.1"}))
    assert updater.check() is None


def test_check_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(timeout: float=10):
        raise updater.UpdateError("no net")

    monkeypatch.setattr(updater, "latest", boom)
    assert updater.check() is None


def test_check_without_a_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "latest", stand_in(updater.latest, lambda timeout=10: None))
    assert updater.check() is None


# ---------------------------------------------------------------- download
def test_download_writes_the_zip_and_checks_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater, "UPDATE", str(tmp_path))
    body = b"zip-bytes" * 100
    digest = hashlib.sha256(body).hexdigest()
    fake_open(monkeypatch, Resp(body, {"Content-Length": str(len(body))}))
    rel = for_download(asset="UncleFarmer-0.3.0.zip", size=len(body), sha256=digest)
    path = updater.download(rel)
    assert path == os.path.join(str(tmp_path), rel["asset"])
    with open(path, "rb") as f:
        assert f.read() == body
    assert not os.path.exists(path + ".part")


def test_download_rejects_a_short_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater, "UPDATE", str(tmp_path))
    body = b"abc"
    fake_open(monkeypatch, Resp(body))                    # no Content-Length; the declared size is used
    with pytest.raises(updater.UpdateError, match="stopped at"):
        updater.download(for_download(size=10))


def test_download_rejects_a_wrong_checksum(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater, "UPDATE", str(tmp_path))
    body = b"abc"
    fake_open(monkeypatch, Resp(body, {"Content-Length": str(len(body))}))
    rel = for_download(size=len(body), sha256="0" * 64)
    with pytest.raises(updater.UpdateError, match="damaged"):
        updater.download(rel)


def test_download_can_be_cancelled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater, "UPDATE", str(tmp_path))
    body = b"abc"
    fake_open(monkeypatch, Resp(body, {"Content-Length": str(len(body))}))
    with pytest.raises(updater.UpdateError, match="cancelled"):
        updater.download(for_download(size=len(body)), progress=lambda done, total: False)


def test_download_wraps_a_connection_drop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Bad(Resp):
        def read(self, n: int | None = -1) -> bytes:
            raise urllib.error.URLError("reset")

    monkeypatch.setattr(updater, "UPDATE", str(tmp_path))
    fake_open(monkeypatch, Bad())
    with pytest.raises(updater.UpdateError, match="download failed: reset"):
        updater.download(for_download())


# ---------------------------------------------------------------- unpack
def test_unpack_returns_the_folder_with_the_exe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater, "NEW", str(tmp_path / "new"))
    z = tmp_path / "r.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Uncle Farmer/UncleFarmer.exe", "MZ")
        zf.writestr("Uncle Farmer/data/x.json", "{}")
    folder = updater.unpack(str(z))
    assert os.path.isfile(os.path.join(folder, updater.EXE))


def test_unpack_rejects_a_non_zip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater, "NEW", str(tmp_path / "new"))
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"definitely not a zip")
    with pytest.raises(updater.UpdateError, match="not a zip"):
        updater.unpack(str(bad))


def test_unpack_needs_the_exe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater, "NEW", str(tmp_path / "new"))
    z = tmp_path / "r.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("readme.txt", "hi")
    with pytest.raises(updater.UpdateError, match="no UncleFarmer.exe"):
        updater.unpack(str(z))


def test_unpack_refuses_a_file_outside_the_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater, "NEW", str(tmp_path / "new"))
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../evil.txt", "boom")
    with pytest.raises(updater.UpdateError, match="outside its folder"):
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

    monkeypatch.setattr(updater.shutil, "copytree", boom)
    with pytest.raises(OSError, match="disk full"):
        updater.apply(str(app), str(src), lambda text: None)
    assert (app / "_internal" / "old.dll").read_text(encoding="utf-8") == "old"
    assert not (app / "_internal.old").exists()


# ---------------------------------------------------------------- start/apply/result
def test_start_apply_launches_the_new_exe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(updater.subprocess, "Popen", popen_with(lambda args, k: calls.append((args, k))))
    monkeypatch.setattr(updater.paths, "APP", str(tmp_path))
    updater.start_apply(str(tmp_path / "new"), "0.3.0")
    args, kw = calls[0]
    assert args == [os.path.join(str(tmp_path / "new"), updater.EXE), "--apply-update", str(tmp_path),
                    str(os.getpid()), "0.3.0"]
    assert kw["cwd"] == str(tmp_path / "new")


def test_run_apply_writes_the_result_and_restarts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(updater, "_wait_for_exit", stand_in(updater._wait_for_exit, lambda pid: None))
    monkeypatch.setattr(updater, "apply", stand_in(updater.apply, lambda app, src, log: log("did it")))
    spawned: list[list[str]] = []
    monkeypatch.setattr(updater.subprocess, "Popen", popen_with(lambda args, k: spawned.append(args)))
    updater.run_apply([str(app), "4242", "0.5.0"])
    with open(app / "update" / "result.json", encoding="utf-8") as f:
        result = updater.json.load(f)
    assert result == {"version": "0.5.0", "ok": True, "error": ""}
    assert spawned == [[os.path.join(str(app), updater.EXE)]]
    assert (app / "update" / "update.log").exists()


def test_run_apply_reports_a_failure_and_still_restarts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()

    def boom(a: str, s: str, log: Callable[[str], None]) -> NoReturn:
        raise RuntimeError("nope")

    monkeypatch.setattr(updater, "_wait_for_exit", stand_in(updater._wait_for_exit, lambda pid: None))
    monkeypatch.setattr(updater, "apply", boom)
    monkeypatch.setattr(updater.subprocess, "Popen", popen_with(lambda args, k: None))
    updater.run_apply([str(app), "1", "0.5.0"])
    with open(app / "update" / "result.json", encoding="utf-8") as f:
        result = updater.json.load(f)
    assert result["ok"] is False and result["error"] == "RuntimeError: nope"
    assert "FAILED RuntimeError: nope" in (app / "update" / "update.log").read_text(encoding="utf-8")


def test_take_result_reads_once_and_cleans_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater, "UPDATE", str(tmp_path))
    monkeypatch.setattr(updater, "RESULT", str(tmp_path / "result.json"))
    (tmp_path / "result.json").write_text(updater.json.dumps({"version": "0.3", "ok": True, "error": ""}),
                                          encoding="utf-8")
    (tmp_path / "UncleFarmer-0.3.zip").write_text("zip", encoding="utf-8")
    (tmp_path / "update.log").write_text("log", encoding="utf-8")
    result = updater.take_result()
    assert result is not None and result["ok"] is True and result["version"] == "0.3"
    assert not (tmp_path / "result.json").exists()
    assert not (tmp_path / "UncleFarmer-0.3.zip").exists()
    assert (tmp_path / "update.log").exists()
    assert updater.take_result() is None


def test_take_result_none_when_there_was_no_update(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater, "RESULT", str(tmp_path / "result.json"))
    assert updater.take_result() is None
    (tmp_path / "result.json").write_text("{bad json", encoding="utf-8")
    assert updater.take_result() is None


def test_take_result_keeps_the_download_when_it_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater, "UPDATE", str(tmp_path))
    monkeypatch.setattr(updater, "RESULT", str(tmp_path / "result.json"))
    (tmp_path / "result.json").write_text(updater.json.dumps({"version": "0.3", "ok": False, "error": "x"}),
                                          encoding="utf-8")
    (tmp_path / "a.zip").write_text("zip", encoding="utf-8")
    failed = updater.take_result()
    assert failed is not None and failed["ok"] is False
    assert (tmp_path / "a.zip").exists()          # not cleaned: the player may retry


def test_clean_removes_downloads_but_keeps_the_logs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(updater, "UPDATE", str(tmp_path))
    (tmp_path / "new").mkdir()
    (tmp_path / "new" / "x").write_text("x", encoding="utf-8")
    (tmp_path / "a.zip").write_text("z", encoding="utf-8")
    (tmp_path / "update.log").write_text("l", encoding="utf-8")
    (tmp_path / "result.json").write_text("{}", encoding="utf-8")
    updater.clean()
    assert not (tmp_path / "new").exists() and not (tmp_path / "a.zip").exists()
    assert (tmp_path / "update.log").exists() and (tmp_path / "result.json").exists()
    monkeypatch.setattr(updater, "UPDATE", str(tmp_path / "missing"))
    updater.clean()                                # no folder: nothing to do, no error


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
    assert captured["req"].get_header("Authorization") == "Bearer secret"


def test_redirect_drops_the_token_off_github() -> None:
    h = updater._NoAuthElsewhere()
    req = urllib.request.Request("https://api.github.com/x", headers={"Authorization": "Bearer t"})
    other = h.redirect_request(req, cast(Any, None), 302, "Found", cast(Any, {}), "https://objects.example/y")
    assert other is not None and other.get_header("Authorization") is None
    same = h.redirect_request(req, cast(Any, None), 302, "Found", cast(Any, {}), "https://api.github.com/z")
    assert same is not None and same.get_header("Authorization") == "Bearer t"


def test_frozen_follows_sys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater.sys, "frozen", True, raising=False)
    assert updater.frozen() is True
    monkeypatch.delattr(updater.sys, "frozen", raising=False)
    assert updater.frozen() is False
