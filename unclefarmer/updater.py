"""Updates from the GitHub releases of Uncle Farmer.

A release is a tag "v<version>" (v0.2.0) with one asset, UncleFarmer-<version>.zip: the built
"Uncle Farmer" folder (release.py makes and publishes it). Updating goes in three steps:

  1. latest() asks GitHub for the newest release; the window says what's new and asks.
  2. download() + unpack() put the new folder in update/new, next to the exe.
  3. start_apply() runs the NEW UncleFarmer.exe from update/new with --apply-update, and the app
     closes. That copy waits for the old one to exit, copies itself over the app folder (nothing
     there is in use any more) and starts the updated app, which reads update/result.json and
     says how it went.

Only what a release ships is replaced: the exe and its _internal folder, agent/, data/, lang/,
docs/ and the files beside the exe. Character profiles, plans, scripts, settings, logs, messages,
results and anything learned (data/game_texts.json, agent/run) are never in a release, so they stay.

The repository is private for now, and GitHub shows a private repository's releases only with a
token: set UNCLEFARMER_GITHUB_TOKEN (a read-only token) to test updates before it goes public.
Without one, a private repository simply has no update to offer.
"""

import ctypes
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from . import __version__, jsonio as json, paths

# Where releases are published ("owner/name" on GitHub). Empty: there is no place to look, so the
# app never asks the network for one. Only ever a repository the project controls: a name nobody
# controls any more could be taken by someone else, whose "release" every copy would then offer
# and install.
REPO = "askunclefarmer/uncle-farmer"
LATEST = "https://api.github.com/repos/%s/releases/latest" % REPO
ASSET = re.compile(r"^UncleFarmer-[\w.\-]+\.zip$", re.I)
EXE = "UncleFarmer.exe"
UPDATE = os.path.join(paths.APP, "update")
NEW = os.path.join(UPDATE, "new")
RESULT = os.path.join(UPDATE, "result.json")
LOG = os.path.join(UPDATE, "update.log")
# folders a release replaces whole (old files in them go); every other shipped folder is merged,
# so files the app made there itself (data/game_texts.json, agent/run) stay
REPLACE = ("_internal",)
KEEP_IN = {"agent": ("run",)}
# never copied, even if a release carried them: the player's own things
NEVER = {"profiles", "plans", "scripts", "logs", "messages", "results", "survey", "update", "settings.json"}


class UpdateError(Exception):
    pass


def frozen():
    """True in the built exe; from source, updating is git's job."""
    return bool(getattr(sys, "frozen", False))


def version_tuple(v):
    return tuple(int(n) for n in re.findall(r"\d+", v or "")[:4]) or (0,)


def is_newer(v, than=__version__):
    return version_tuple(v) > version_tuple(than)


# ---------------------------------------------------------------------------------------- GitHub
def _token():
    return os.environ.get("UNCLEFARMER_GITHUB_TOKEN", "").strip()


class _NoAuthElsewhere(urllib.request.HTTPRedirectHandler):
    """An asset download redirects to GitHub's file storage, which refuses a second login: the
    token goes to api.github.com only."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlparse(newurl).hostname != "api.github.com":
            new.remove_header("Authorization")
        return new


def _open(url, accept, timeout):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "UncleFarmer/" + __version__})
    if _token():
        req.add_header("Authorization", "Bearer " + _token())
    return urllib.request.build_opener(_NoAuthElsewhere).open(req, timeout=timeout)


def latest(timeout=10):
    """The newest release: {"version", "tag", "notes", "asset", "size", "url", "sha256"}, or None
    when there is none to see (no release yet, or a private repository). UpdateError when GitHub
    can't be reached. None, without asking anyone, while no repository is set."""
    if not REPO:
        return None
    try:
        with _open(LATEST, "application/vnd.github+json", timeout) as r:
            rel = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise UpdateError("GitHub answered %s" % e.code)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UpdateError(str(getattr(e, "reason", e)))
    if rel.get("draft") or rel.get("prerelease"):
        return None
    asset = next((a for a in rel.get("assets", []) if ASSET.match(a.get("name", ""))), None)
    if asset is None:
        return None
    digest = asset.get("digest") or ""
    return {"version": (rel.get("tag_name") or "").lstrip("vV"), "tag": rel.get("tag_name"),
            "notes": (rel.get("body") or "").strip(), "asset": asset["name"], "size": asset.get("size", 0),
            # with a token the API's own asset link (a private repository has no public one)
            "url": asset["url"] if _token() else asset["browser_download_url"],
            "sha256": digest[7:].lower() if digest.startswith("sha256:") else ""}


def check(timeout=10):
    """The newest release if it is newer than this copy, else None (errors too: None)."""
    try:
        rel = latest(timeout)
    except UpdateError:
        return None
    return rel if rel and is_newer(rel["version"]) else None


# ---------------------------------------------------------------------------------------- download
def download(rel, progress=None):
    """Download the release's zip into update/; progress(done, total) -> False cancels. Returns
    the zip's path. Checks the size and, when GitHub gives one, the SHA-256."""
    os.makedirs(UPDATE, exist_ok=True)
    path = os.path.join(UPDATE, rel["asset"])
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
    return path


def unpack(zip_path):
    """Unpack the release into update/new and return the folder holding the new exe."""
    shutil.rmtree(NEW, ignore_errors=True)
    os.makedirs(NEW)
    root = os.path.realpath(NEW)
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
            return folder
    raise UpdateError("the update has no %s" % EXE)


def start_apply(new_folder, version):
    """Start the new copy's --apply-update; the caller then closes the app at once."""
    args = [os.path.join(new_folder, EXE), "--apply-update", paths.APP, str(os.getpid()), version]
    subprocess.Popen(args, cwd=new_folder, close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) |
                     getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


# ---------------------------------------------------------------------------------------- applying
def _wait_for_exit(pid, seconds=120):
    kernel = ctypes.windll.kernel32
    h = kernel.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
    if h:
        try:
            kernel.WaitForSingleObject(h, int(seconds * 1000))
        finally:
            kernel.CloseHandle(h)
    time.sleep(1.0)                     # OS wait in the update helper, not the app clock: let Windows let go of the files


def _copy(src, dst, log, tries=30):
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


def apply(app, src, log):
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


def run_apply(argv):
    """`UncleFarmer.exe --apply-update <app folder> <old pid> <version>`, run from update/new."""
    app, pid, version = argv[0], int(argv[1]), argv[2]
    src = os.path.dirname(os.path.abspath(sys.executable))
    update = os.path.join(app, "update")
    os.makedirs(update, exist_ok=True)
    lines = []

    def log(text):
        lines.append(time.strftime("%H:%M:%S ") + text)
        with open(os.path.join(update, "update.log"), "a", encoding="utf-8") as f:
            f.write(lines[-1] + "\n")

    log("updating %s to %s" % (app, version))
    _wait_for_exit(pid)
    result = {"version": version, "ok": True, "error": ""}
    try:
        apply(app, src, log)
    except Exception as e:                    # report it in the app, which starts again either way
        # Not carry_on(): silently continuing would leave a half-applied release. The update
        # worker records the failure for the restarted app instead.
        result.update(ok=False, error="%s: %s" % (type(e).__name__, e))
        log("FAILED " + result["error"])
    with open(os.path.join(update, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f)
    subprocess.Popen([os.path.join(app, EXE)], cwd=app, close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def take_result():
    """After an update: {"version", "ok", "error"}, once (the update folder is cleared when it
    worked). None when there was no update."""
    try:
        with open(RESULT, encoding="utf-8") as f:
            result = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        os.remove(RESULT)
    except OSError:
        pass
    if result.get("ok"):
        clean()
    return result


def clean():
    """Remove the downloaded zip and the unpacked copy (the copier may still be closing: then the
    next start does it)."""
    for name in os.listdir(UPDATE) if os.path.isdir(UPDATE) else ():
        p = os.path.join(UPDATE, name)
        if name in ("update.log", "result.json"):
            continue
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                os.remove(p)
            except OSError:
                pass
