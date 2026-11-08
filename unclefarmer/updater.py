"""Updates from the GitHub releases of Uncle Farmer, on one of two channels.

  master  stable releases: a tag "v<version>" (v0.2.0) with one asset, UncleFarmer-<version>.zip,
          published by hand (release.py). GitHub's "latest release", which never is a pre-release.
  dev     development builds of the dev branch, published by CI (ci.yml, ci_build.py publish-dev):
          a PRE-release tagged "v<version>-dev.<build>" with UncleFarmer-<version>-dev.<build>.zip.
          <version> is the last stable release's, <build> the CI run number, so 0.9.0-dev.57 is a
          build made after 0.9.0, newer than 0.9.0 and older than 0.10.0 (version_tuple orders them).

The player picks the channel in Settings (update_channel, master when never set). Each channel
reads only its own releases: master never takes a pre-release or a "-dev." version, dev only
pre-releases whose tag, version and asset name all have the dev shape. A built copy says which
build it is in build.json beside the exe (write_build); a copy without one is __version__.

Updating goes in three steps:

  1. latest() asks GitHub for the newest release of the channel; the window says what's new and asks.
  2. download() + unpack() put the new folder in update/new, next to the exe.
  3. start_apply() runs the NEW UncleFarmer.exe from update/new with --apply-update, and the app
     closes. That copy waits for the old one to exit, copies itself over the app folder (nothing
     there is in use any more) and starts the updated app, which reads update/result.json and
     says how it went.

update/ is the staging folder: only the download, the unpacked copy, the result and busy.json
(who is using it) live there, and clean() empties and removes it once nothing uses it. Every step
is written to logs/update.log with the app's other logs, so a cleanup never takes the log away.

Only what a release ships is replaced: the exe and its _internal folder, agent/, data/, lang/,
docs/ and the files beside the exe. Character profiles, plans, scripts, settings, logs, messages,
results and anything learned (data/game_texts.json, agent/run) are never in a release, so they stay.

The releases are public, so no login is needed. UNCLEFARMER_GITHUB_TOKEN (a read-only token)
still works for a private fork's releases; without one, a private repository has no update to offer.
"""

from __future__ import annotations

import ctypes
import hashlib
import http.client
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import IO, Any, TypedDict, cast

from . import __version__, applog, jsonio as json, paths

# Where releases are published ("owner/name" on GitHub). Empty: there is no place to look, so the
# app never asks the network for one. Only ever a repository the project controls: a name nobody
# controls any more could be taken by someone else, whose "release" every copy would then offer
# and install.
REPO = "afnannasir/uncle-farmer"
LATEST = "https://api.github.com/repos/%s/releases/latest"      # % REPO, when asked
RELEASES = "https://api.github.com/repos/%s/releases?per_page=%d"   # % (REPO, DEV_PAGE)
DEV_PAGE = 30               # the newest releases looked through for dev builds (CI keeps 10)
ASSET = re.compile(r"^UncleFarmer-[\w.\-]+\.zip$", re.I)
# the dev channel's shapes; ci_build.py checks its own copy of DEV_VERSION against this one
DEV_VERSION = re.compile(r"^\d+\.\d+\.\d+-dev\.\d+$")
CHANNELS = ("master", "dev")
DEFAULT_CHANNEL = "master"
EXE = "UncleFarmer.exe"
BUILD_FILE = "build.json"   # beside the exe in a CI build: which build this copy is
UPDATE = os.path.join(paths.APP, "update")
NEW = os.path.join(UPDATE, "new")
RESULT = os.path.join(UPDATE, "result.json")
BUSY = "busy.json"          # in UPDATE while a download, an unpack or the copier uses it
STALE_SECONDS = 6 * 3600    # a busy.json older than this is a leftover, whoever wrote it
LOG = os.path.join(paths.LOGS, "update.log")
# folders a release replaces whole (old files in them go); every other shipped folder is merged,
# so files the app made there itself (data/game_texts.json, agent/run) stay
REPLACE = ("_internal",)
KEEP_IN = {"agent": ("run",)}
# never copied, even if a release carried them: the player's own things
NEVER = {"profiles", "plans", "scripts", "logs", "messages", "results", "survey", "update", "settings.json",
         "schedules.json"}

_lock = threading.Lock()    # the staging folder, inside this process: a claim or a cleanup at a time
_claims = 0                 # nested claims of this process (busy.json is written for the first)


class UpdateError(Exception):
    pass


class Release(TypedDict):
    """The newest release, as latest() reads it from GitHub."""
    version: str
    tag: str | None
    notes: str
    asset: str          # the zip's file name
    size: int
    url: str
    sha256: str         # "" when GitHub gives no digest


class UpdateResult(TypedDict):
    """What the update copier reports to the restarted app (update/result.json)."""
    version: str
    ok: bool
    error: str


def frozen() -> bool:
    """True in the built exe (paths.frozen); from source, updating is git's job."""
    return paths.frozen()


def version_tuple(v: str | None) -> tuple[int, ...]:
    """The numbers to compare: 1.0.1 is (1, 0, 1), a dev build 1.0.1-dev.129 is (1, 0, 1, 129).
    A dev build is made from dev AFTER its release (dev keeps the last release's version), so it
    sorts after 1.0.1 and before 1.0.2, not before 1.0.1 as a semver pre-release would."""
    return tuple(int(n) for n in re.findall(r"\d+", v or "")[:4]) or (0,)


def is_newer(v: str, than: str = __version__) -> bool:
    return version_tuple(v) > version_tuple(than)


def channel(value: object) -> str:
    """The channel a setting names; anything else (never set, a typo) is the default, master."""
    return value if isinstance(value, str) and value in CHANNELS else DEFAULT_CHANNEL


def is_dev(version: str) -> bool:
    """A development build's version (0.9.0-dev.57), as no stable release ever has."""
    return bool(DEV_VERSION.match(version or ""))


def log(text: str, level: str = "INFO") -> None:
    """One line to logs/update.log (applog.update), never an error of its own."""
    applog.update(text, level)


# ---------------------------------------------------------------------------------- this copy
def write_build(folder: str, version: str, build_channel: str, commit: str = "") -> str:
    """build.json in a built folder (ci_build.py, release.py): this copy's version and channel.
    Every build writes one, so an update always replaces the one before."""
    path = os.path.join(folder, BUILD_FILE)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"version": version, "channel": channel(build_channel), "commit": commit}, f)
    return path


def installed_version() -> str:
    """This copy's version: a dev build's from build.json (0.9.0-dev.57), else __version__.
    A build.json that doesn't belong to this __version__ is a leftover (a copy unpacked over
    another by hand) and counts for nothing, so it can't hide a stable release."""
    try:
        with open(os.path.join(paths.APP, BUILD_FILE), encoding="utf-8") as f:
            data = json.load(f)
        found = _obj(data)
        v = str(found.get("version") or "") if found is not None else ""
    except (OSError, ValueError):
        return __version__
    if v != __version__ and not (is_dev(v) and version_tuple(v)[:3] == version_tuple(__version__)[:3]):
        return __version__
    return v


# ---------------------------------------------------------------------------------------- GitHub
def _token() -> str:
    return os.environ.get("UNCLEFARMER_GITHUB_TOKEN", "").strip()


class _NoAuthElsewhere(urllib.request.HTTPRedirectHandler):
    """An asset download redirects to GitHub's file storage, which refuses a second login: the
    token goes to api.github.com only."""

    def redirect_request(self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str,
                         headers: http.client.HTTPMessage, newurl: str) -> urllib.request.Request | None:
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlparse(newurl).hostname != "api.github.com":
            new.remove_header("Authorization")
        return new


def _open(url: str, accept: str, timeout: float) -> http.client.HTTPResponse:
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "UncleFarmer/" + __version__})
    if _token():
        req.add_header("Authorization", "Bearer " + _token())
    return urllib.request.build_opener(_NoAuthElsewhere).open(req, timeout=timeout)


def _ask(url: str, timeout: float) -> Any:
    """GitHub's JSON answer; None for a 404 (nothing to see), UpdateError when it can't be had."""
    try:
        with _open(url, "application/vnd.github+json", timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise UpdateError("GitHub answered %s" % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UpdateError(str(getattr(e, "reason", e)))


def _release(rel: dict[str, Any], version: str, asset: dict[str, Any]) -> Release:
    digest = asset.get("digest") or ""
    return {"version": version, "tag": rel.get("tag_name"),
            "notes": (rel.get("body") or "").strip(), "asset": asset["name"], "size": asset.get("size", 0),
            # with a token the API's own asset link (a private repository has no public one)
            "url": asset["url"] if _token() else asset["browser_download_url"],
            "sha256": digest[7:].lower() if digest.startswith("sha256:") else ""}


def _obj(value: object) -> dict[str, Any] | None:
    """A JSON object from GitHub (or a file) as a dict, or None when it is something else."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


def _assets(rel: dict[str, Any]) -> list[dict[str, Any]]:
    found = rel.get("assets")
    items = cast(list[object], found) if isinstance(found, list) else []
    return [a for a in (_obj(x) for x in items) if a is not None]


def _stable(answer: object) -> Release | None:
    """GitHub's latest release as a master release, or None when it isn't one."""
    rel = _obj(answer)
    if rel is None or rel.get("draft") or rel.get("prerelease"):
        return None
    version = str(rel.get("tag_name") or "").lstrip("vV")
    if is_dev(version):                  # never a dev build on master, however it was published
        return None
    asset = next((a for a in _assets(rel) if ASSET.match(str(a.get("name", "")))
                  and "-dev." not in str(a.get("name", ""))), None)
    return None if asset is None else _release(rel, version, asset)


def _dev(answer: object) -> Release | None:
    """One release from the list as a dev build, or None: a pre-release, not a draft, tagged
    v<dev version>, carrying exactly UncleFarmer-<dev version>.zip."""
    rel = _obj(answer)
    if rel is None or rel.get("draft") or not rel.get("prerelease"):
        return None
    tag = str(rel.get("tag_name") or "")
    version = tag[1:] if tag[:1] in ("v", "V") else ""
    if not is_dev(version):
        return None
    want = "UncleFarmer-%s.zip" % version
    # an asset still being uploaded (CI publishing right now) is not one yet
    asset = next((a for a in _assets(rel) if a.get("name") == want
                  and a.get("state", "uploaded") == "uploaded"), None)
    return None if asset is None else _release(rel, version, asset)


def latest(timeout: float=10, update_channel: str = DEFAULT_CHANNEL) -> Release | None:
    """The newest release of the channel: {"version", "tag", "notes", "asset", "size", "url",
    "sha256"}, or None when there is none to see (no release yet, or a private repository).
    UpdateError when GitHub can't be reached. None, without asking anyone, while no repository is set."""
    if not REPO:
        return None
    if channel(update_channel) == "dev":
        answer = _ask(RELEASES % (REPO, DEV_PAGE), timeout)
        if answer is None:
            return None
        if not isinstance(answer, list):
            raise UpdateError("GitHub's list of releases isn't a list")
        builds = [b for b in (_dev(r) for r in cast(list[object], answer)) if b is not None]
        return max(builds, key=lambda b: version_tuple(b["version"]), default=None)
    return _stable(_ask(LATEST % REPO, timeout))


def check(timeout: float=10, update_channel: str = DEFAULT_CHANNEL) -> Release | None:
    """The channel's newest release if it is newer than this copy, else None (errors too: None)."""
    ch = channel(update_channel)
    try:
        rel = latest(timeout, ch)
    except UpdateError as e:
        log("update check (%s) failed: %s" % (ch, e), "WARNING")
        return None
    mine = installed_version()
    if rel is None:
        log("update check (%s): no release to see; this copy is %s" % (ch, mine))
        return None
    newer = is_newer(rel["version"], mine)
    log("update check (%s): newest %s, this copy %s: %s" % (ch, rel["version"], mine,
                                                             "newer, offered" if newer else "nothing newer"))
    return rel if newer else None


# ---------------------------------------------------------------------------------- staging
def _junction(path: str) -> bool:
    try:
        return os.path.isjunction(path)
    except (AttributeError, OSError):
        return False


def _link(path: str) -> bool:
    return os.path.islink(path) or _junction(path)


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _inside(path: str, folder: str) -> bool:
    """path is folder itself or below it, after every link is resolved."""
    p, f = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(folder))
    try:
        return os.path.commonpath([p, f]) == f
    except ValueError:                   # another drive
        return False


def _checked_staging(staging: str, app: str) -> str | None:
    """staging when it is exactly <app>/update, a real folder (no link or junction that could lead
    elsewhere); else None, and nothing in it may be deleted. Nothing but that one folder ever
    qualifies, so the app folder, settings, profiles and logs can never be taken for it."""
    if os.path.normcase(os.path.basename(os.path.normpath(staging))) != "update":
        return None
    if _link(staging) or not _same(os.path.dirname(os.path.abspath(staging)), app):
        return None
    if not _inside(staging, app) or _same(staging, app):
        return None
    return staging


def _alive(pid: int) -> bool:
    """A process with this id is running (this one always is)."""
    if pid == os.getpid():
        return True
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    kernel = ctypes.windll.kernel32
    h = kernel.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
    finally:
        kernel.CloseHandle(h)


def busy(staging: str | None = None) -> dict[str, Any] | None:
    """Who uses the staging folder: busy.json's {"pid", "what", "since"} while that process still
    runs; None when nobody does (no file, a broken one, a process that is gone, or older than
    STALE_SECONDS)."""
    path = os.path.join(staging or UPDATE, BUSY)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        pid, since = int(data["pid"]), float(data["since"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if time.time() - since > STALE_SECONDS or not _alive(pid):
        return None
    return {"pid": pid, "what": str(data.get("what") or "update"), "since": since}


def _mark_busy(staging: str, what: str) -> None:
    os.makedirs(staging, exist_ok=True)
    with open(os.path.join(staging, BUSY), "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "what": what, "since": time.time()}, f)


@contextmanager
def claimed(what: str) -> Generator[None]:
    """Hold the staging folder for a download or an unpack: busy.json says so, cleanup leaves it
    alone, and another copy of the app can't start its own update into it. Nested claims of this
    process are one claim. UpdateError when another running process holds it."""
    global _claims
    with _lock:
        if _claims == 0:
            holder = busy(UPDATE)
            if holder and holder["pid"] != os.getpid():
                raise UpdateError("another update is under way (%s, process %d)" % (holder["what"], holder["pid"]))
            _mark_busy(UPDATE, what)
        _claims += 1
    try:
        yield
    finally:
        with _lock:
            _claims -= 1
            if _claims == 0:
                try:
                    os.remove(os.path.join(UPDATE, BUSY))
                except OSError:
                    pass


def _remove(path: str, staging: str, left: list[str]) -> None:
    """Delete one entry of the staging folder. A link or junction goes itself, never what it points
    at; anything that resolves outside the folder is left alone; read-only files are made writable
    once; what can't go (in use, no permission) is added to left."""
    if _link(path):
        try:
            if os.path.isdir(path) and _junction(path):
                os.rmdir(path)
            else:
                os.unlink(path)
        except OSError as e:
            left.append("%s (%s)" % (path, e))
        return
    if not _inside(path, staging) or _same(path, staging):
        left.append("%s (outside the staging folder: left alone)" % path)
        return

    def retry(func: Callable[..., object], p: str, exc: BaseException) -> None:
        if isinstance(exc, PermissionError):
            try:
                os.chmod(p, stat.S_IWRITE)
                func(p)
                return
            except OSError as again:
                exc = again
        if not isinstance(exc, FileNotFoundError):
            left.append("%s (%s)" % (p, exc))

    if os.path.isdir(path):
        shutil.rmtree(path, onexc=retry)
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            retry(os.remove, path, e)


def _move_old_log(staging: str, app: str) -> None:
    """Older versions wrote update/update.log: it joins logs/update.log before the folder goes."""
    old = os.path.join(staging, "update.log")
    if not os.path.isfile(old) or _link(old):
        return
    try:
        with open(old, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return
    if applog.update("---- the update log of an older version, from the staging folder ----\n" + text.rstrip("\n"),
                     logs=os.path.join(app, "logs")) is None:
        try:
            os.remove(old)
        except OSError:
            pass


def _purge(staging: str, app: str, keep: tuple[str, ...]) -> list[str]:
    """Empty the (checked) staging folder but for keep, and remove it when nothing is left.
    Returns what couldn't go."""
    left: list[str] = []
    if not os.path.isdir(staging):
        return left
    _move_old_log(staging, app)
    for name in os.listdir(staging):
        if name in keep:
            continue
        _remove(os.path.join(staging, name), staging, left)
    try:
        if not os.listdir(staging):
            os.rmdir(staging)
    except OSError:
        pass
    return left


def clean(wait: float = 0.0) -> bool:
    """Empty the staging folder (update/) and remove it: old downloads, a half download, the
    unpacked copy, the old update.log (moved to logs/ first). Never while a running process holds
    it (busy): wait up to that many seconds for it to finish (the update copier is closing just
    as the updated app starts), else leave it for the next start. Only <app>/update itself is
    ever emptied. True when it is empty (but for a result still to be read); what couldn't go is
    in logs/update.log."""
    staging, app = UPDATE, paths.APP
    if not os.path.exists(staging):
        return True
    if _checked_staging(staging, app) is None:
        log("staging cleanup refused: %s is not the app's own update folder" % staging, "WARNING")
        return False
    holder = busy(staging)
    if holder and wait > 0 and holder["pid"] != os.getpid():
        _wait_for_exit(holder["pid"], wait)
    with _lock:
        holder = busy(staging)
        if holder or _claims:
            who = holder or {"what": "update", "pid": os.getpid()}
            log("staging cleanup put off: the %s of process %d still uses it" % (who["what"], who["pid"]))
            return False
        left = _purge(staging, app, (os.path.basename(RESULT),))
    if left:
        log("staging cleanup left %d item(s), tried again at the next start: %s" % (len(left), "; ".join(left[:5])),
            "WARNING")
        return False
    log("staging folder cleaned")
    return True


# ---------------------------------------------------------------------------------------- download
def download(rel: Release, progress: Callable[[int, int], object] | None = None) -> str:
    """Download the release's zip into update/; progress(done, total) -> False cancels. Returns
    the zip's path. Checks the size and, when GitHub gives one, the SHA-256. Older downloads in
    the staging folder go first; a failed or cancelled one leaves no partial file behind."""
    with claimed("download"):
        if _checked_staging(UPDATE, paths.APP) is not None:
            with _lock:
                left = _purge(UPDATE, paths.APP, (BUSY, os.path.basename(RESULT)))
            if left:
                log("older staging files still in use, left: %s" % "; ".join(left[:3]), "WARNING")
        os.makedirs(UPDATE, exist_ok=True)
        path = os.path.join(UPDATE, rel["asset"])
        log("download of %s (%s) started" % (rel["asset"], rel["version"]))
        try:
            got = _fetch(rel, path, progress)
        except UpdateError as e:
            try:
                os.remove(path + ".part")
            except OSError:
                pass
            log("download of %s %s" % (rel["asset"], "cancelled" if str(e) == "cancelled" else "FAILED: %s" % e),
                "INFO" if str(e) == "cancelled" else "WARNING")
            raise
        log("download of %s done: %d bytes, %s" % (rel["asset"], got,
                                                   "checksum ok" if rel.get("sha256") else "no checksum given"))
        return path


def _fetch(rel: Release, path: str, progress: Callable[[int, int], object] | None) -> int:
    sha, done, total = hashlib.sha256(), 0, int(rel.get("size") or 0)
    try:
        with _open(rel["url"], "application/octet-stream", 60) as r, open(path + ".part", "wb") as f:
            total = int(r.headers.get("Content-Length") or total)
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if progress and progress(done, total) is False:
                    raise UpdateError("cancelled")
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError("the download failed: %s" % getattr(e, "reason", e))
    if total and done != total:
        raise UpdateError("the download stopped at %d of %d bytes" % (done, total))
    if rel.get("sha256") and sha.hexdigest() != rel["sha256"]:
        raise UpdateError("the downloaded file is damaged (its checksum is wrong)")
    os.replace(path + ".part", path)
    return done


def unpack(zip_path: str) -> str:
    """Unpack the release into update/new and return the folder holding the new exe."""
    with claimed("unpack"):
        if os.path.exists(NEW):
            left: list[str] = []
            if _checked_staging(UPDATE, paths.APP) is None:
                raise UpdateError("%s is not the app's own update folder" % UPDATE)
            _remove(NEW, UPDATE, left)
            if left or os.path.exists(NEW):
                raise UpdateError("the last unpacked update is still in use: %s" % "; ".join(left[:2]))
        os.makedirs(NEW)
        root = os.path.realpath(NEW)
        try:
            try:
                with zipfile.ZipFile(zip_path) as z:
                    for name in z.namelist():
                        target = os.path.realpath(os.path.join(NEW, name))
                        if target != root and not target.startswith(root + os.sep):
                            raise UpdateError("the update holds a file outside its folder: %s" % name)
                    z.extractall(NEW)
            except zipfile.BadZipFile:
                raise UpdateError("the downloaded file is not a zip")
            for folder, _dirs, files in os.walk(NEW):
                if EXE in files:
                    log("unpacked %s" % os.path.basename(zip_path))
                    return folder
            raise UpdateError("the update has no %s" % EXE)
        except UpdateError as e:
            log("unpacking %s FAILED: %s" % (os.path.basename(zip_path), e), "WARNING")
            raise


def start_apply(new_folder: str, version: str) -> None:
    """Start the new copy's --apply-update; the caller then closes the app at once."""
    args = [os.path.join(new_folder, EXE), "--apply-update", paths.APP, str(os.getpid()), version]
    subprocess.Popen(args, cwd=new_folder, close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) |
                     getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    log("the update copier for %s started; the app closes for it" % version)


# ---------------------------------------------------------------------------------------- applying
def _wait_for_exit(pid: int, seconds: float=120) -> None:
    kernel = ctypes.windll.kernel32
    h = kernel.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
    if h:
        try:
            kernel.WaitForSingleObject(h, int(seconds * 1000))
        finally:
            kernel.CloseHandle(h)
    time.sleep(1.0)                     # OS wait in the update helper, not the app clock: let Windows let go of the files


def _copy(src: str, dst: str, log: Callable[[str], None], tries: int = 30) -> None:
    """Copy one file, waiting a while for one still in use (an antivirus scan, a closing game)."""
    for i in range(tries):
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            log("in use, waiting: %s" % dst)
            time.sleep(2)                 # OS wait in the update helper, not the app clock


def apply(app: str, src: str, log: Callable[[str], None]) -> None:
    """Copy the release in src over the app folder (see the module notes)."""
    for name in sorted(os.listdir(src)):
        if name.lower() in NEVER:
            continue
        s, d = os.path.join(src, name), os.path.join(app, name)
        if os.path.isdir(s) and name in REPLACE:
            old = d + ".old"
            shutil.rmtree(old, ignore_errors=True)
            if os.path.exists(d):
                os.rename(d, old)
            try:
                shutil.copytree(s, d)
            except Exception:
                # The update cannot carry on with a half-copied folder: put the old one back and
                # let run_apply report the failure.
                shutil.rmtree(d, ignore_errors=True)
                if os.path.exists(old):
                    os.rename(old, d)
                raise
            shutil.rmtree(old, ignore_errors=True)
            log("replaced %s" % name)
        elif os.path.isdir(s):
            keep = KEEP_IN.get(name, ())
            for folder, dirs, files in os.walk(s):
                rel = os.path.relpath(folder, s)
                if rel == ".":
                    dirs[:] = [x for x in dirs if x not in keep]
                for f in files:
                    _copy(os.path.join(folder, f), os.path.normpath(os.path.join(d, rel, f)), log)
            log("updated %s" % name)
        else:
            _copy(s, d, log)
            log("updated %s" % name)


def run_apply(argv: list[str]) -> None:
    """`UncleFarmer.exe --apply-update <app folder> <old pid> <version>`, run from update/new.

    This process runs from the staging folder, so its own paths.APP is update/new: the app folder
    comes from argv, and the log goes to <app>/logs/update.log. busy.json names this process until
    it exits, so the restarted app waits for it before it empties the staging folder."""
    app, pid, version = argv[0], int(argv[1]), argv[2]
    src = os.path.dirname(os.path.abspath(sys.executable))
    update = os.path.join(app, "update")
    os.makedirs(update, exist_ok=True)
    logs = os.path.join(app, "logs")

    def log(text: str) -> None:
        applog.update("copier: " + text, "ERROR" if text.startswith("FAILED") else "INFO", logs=logs)

    _mark_busy(update, "update copier")
    log("updating %s to %s" % (app, version))
    _wait_for_exit(pid)
    result: UpdateResult = {"version": version, "ok": True, "error": ""}
    try:
        apply(app, src, log)
    except Exception as e:                    # report it in the app, which starts again either way
        # Not carry_on(): silently continuing would leave a half-applied release. The update
        # worker records the failure for the restarted app instead.
        result["ok"], result["error"] = False, "%s: %s" % (type(e).__name__, e)
        log("FAILED " + result["error"])
    with open(os.path.join(update, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f)
    log("done; starting the app again")
    subprocess.Popen([os.path.join(app, EXE)], cwd=app, close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def take_result() -> UpdateResult | None:
    """After an update: {"version", "ok", "error"}, once. None when there was no update. The
    caller cleans the staging folder afterwards (clean(wait=...)), which it does either way: a
    failed update's copy is of no further use, and the log in logs/ says what happened."""
    try:
        with open(RESULT, encoding="utf-8") as f:
            result: UpdateResult = json.load(f)
    except OSError:
        return None
    except ValueError:
        result = {"version": "?", "ok": False, "error": "the update's result file is unreadable"}
    try:
        os.remove(RESULT)
    except OSError:
        pass
    log("the update to %s %s" % (result.get("version"), "finished" if result.get("ok") else
                                 "didn't finish: %s" % result.get("error")), "INFO" if result.get("ok") else "WARNING")
    return result
