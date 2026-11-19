"""A real update from published releases to a new build, before that build reaches anyone.

    python update_compat.py --new <new build zip> [--from master] [--from dev] [--from v1.2.0] [--work DIR]

For each --from (master = GitHub's latest release, dev = the newest dev pre-release, or a tag):
download that release's zip and unpack it into "<work>/<tag>/Uncle Farmer". Then run THAT
release's own updater code from its tagged source, with its paths pointed at the copy. It takes
the new zip through its normal steps: download (from a file:// link, size and SHA-256 checked),
unpack, start_apply, then the window "closes". The new build's copier (and bootstrap.exe) must
then install the update and start Uncle Farmer again. The check passes when build.json is the
new version, the journal is COMMITTED, and the restarted app logs that the update finished.

This is the check unit tests cannot be: the old code and the old binaries are the real ones that
players run. A build that the last release can't update to never reaches a player. CI runs it
after every dev and master build, before publish-dev. It needs git (the release tags) and gh
(GH_TOKEN) on PATH, and it runs on Windows only.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable

from unclefarmer import updater

REPO = os.environ.get("GITHUB_REPOSITORY") or updater.REPO      # the repository the app updates from
EXE = "UncleFarmer.exe"
BOOTSTRAP = "bootstrap.exe"
WAIT = 300.0        # seconds for each step; a cold runner takes a while to start a new exe

# The old app: runs in its own python with the OLD source first on sys.path, like the old window
# would. Versions before 1.2.0 have no schedule_apply/finish_apply: their start_apply starts the
# copier itself, and the copier restarts the app.
OLD_APP = r'''
import json, os, sys
root, app, zip_path, version, sha, size = sys.argv[1:]
sys.path.insert(0, root)
from unclefarmer import updater, paths
paths.APP = app
paths.LOGS = os.path.join(app, "logs")
updater.UPDATE = os.path.join(app, "update")
updater.NEW = os.path.join(updater.UPDATE, "new")
updater.RESULT = os.path.join(updater.UPDATE, "result.json")
updater.LOG = os.path.join(paths.LOGS, "update.log")
updater.frozen = lambda: True
print("old copy says it is", updater.installed_version(), flush=True)
rel = {"version": version, "tag": "v" + version, "notes": "", "asset": "UncleFarmer-%s.zip" % version,
       "size": int(size), "url": pathlib_uri, "sha256": sha}
path = updater.download(rel)
folder = updater.unpack(path)
handle = updater.start_apply(folder, version)
if hasattr(updater, "schedule_apply"):
    updater.schedule_apply(handle)
    updater.finish_apply()
print("old app hands over to the copier and exits", flush=True)
'''


def run(*args: str, cwd: str | None = None) -> str:
    done = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if done.returncode:
        raise SystemExit("%s failed (%d):\n%s%s" % (" ".join(args), done.returncode, done.stdout, done.stderr))
    return done.stdout


def resolve(which: str) -> str:
    """master / dev / a tag -> the release tag."""
    if which == "master":
        return run("gh", "api", "repos/%s/releases/latest" % REPO, "--jq", ".tag_name").strip()
    if which == "dev":
        out = run("gh", "api", "repos/%s/releases?per_page=30" % REPO, "--jq",
                  '[.[] | select(.prerelease and (.draft | not) and (.tag_name | test("-dev\\\\.")))'
                  ' | .tag_name] | .[]')
        tags = [t for t in out.split() if t]
        if not tags:
            raise SystemExit("no dev pre-release to update from")
        return max(tags, key=lambda t: [int(x) for x in t.lstrip("v").replace("-dev.", ".").split(".")])
    return which


def build_version(zip_path: str) -> str:
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.endswith("/build.json") and n.count("/") == 1)
        return str(json.loads(z.read(name))["version"])


def processes(path: str) -> list[int]:
    """PIDs whose executable is exactly this file."""
    out = run("powershell", "-NoProfile", "-Command",
              "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" }")
    want = os.path.normcase(os.path.realpath(path))
    found: list[int] = []
    for line in out.splitlines():
        pid, _, exe = line.partition("|")
        if exe and os.path.normcase(os.path.realpath(exe)) == want:
            found.append(int(pid))
    return found


def read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def stop(app: str) -> None:
    for pid in processes(os.path.join(app, EXE)) + processes(os.path.join(app, BOOTSTRAP)):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def report(app: str) -> None:
    for name in ("logs/update.log", "logs/bootup.log", ".recovery/transaction.json"):
        print("----- %s -----\n%s" % (name, read(os.path.join(app, name))[-6000:]))
    recovery = os.path.join(app, ".recovery")
    if os.path.isdir(recovery):
        print("----- .recovery: %s" % sorted(os.listdir(recovery)))
        for name in os.listdir(recovery):
            if name.startswith("bootstrap-error") or name.endswith(".log"):
                print("----- .recovery/%s -----\n%s" % (name, read(os.path.join(recovery, name))[-3000:]))


def wait(what: str, check: Callable[[], bool], app: str, version: str) -> None:
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        if check():
            print("  ok:", what, flush=True)
            return
        if "the update to %s didn't finish" % version in read(os.path.join(app, "logs", "update.log")):
            break               # the restarted app has reported the failure: no point waiting on
        time.sleep(1)
    report(app)
    stop(app)
    raise SystemExit("FAILED: %s" % what)


def journal_state(app: str) -> str:
    try:
        return str(json.loads(read(os.path.join(app, ".recovery", "transaction.json")))["payload"]["state"])
    except (ValueError, KeyError, TypeError):
        return ""


def update_from(tag: str, new_zip: str, new_version: str, work: str) -> None:
    print("== %s -> %s" % (tag, new_version), flush=True)
    here = os.path.join(work, tag)
    if os.path.exists(here):
        shutil.rmtree(here)
    os.makedirs(here)
    run("gh", "release", "download", tag, "--repo", REPO, "--pattern", "UncleFarmer-*.zip", "--dir", here)
    old_zip = next(os.path.join(here, n) for n in os.listdir(here) if n.endswith(".zip"))
    source = os.path.join(here, "source")
    run("git", "fetch", "--quiet", "--depth", "1", "origin", "tag", tag)
    run("git", "worktree", "add", "--quiet", "--detach", source, tag)
    try:
        app = os.path.join(here, "Uncle Farmer")         # a space, as in every real install
        with zipfile.ZipFile(old_zip) as z:
            z.extractall(here)
        if not os.path.isfile(os.path.join(app, EXE)):
            raise SystemExit("%s has no Uncle Farmer/%s" % (old_zip, EXE))
        with open(os.path.join(app, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"check_updates": False, "language_prompted": True}, f)
        sha = hashlib.sha256(pathlib.Path(new_zip).read_bytes()).hexdigest()
        began = time.time()
        script = OLD_APP.replace("pathlib_uri", repr(pathlib.Path(new_zip).resolve().as_uri()))
        old = subprocess.run([sys.executable, "-c", script, source, app, new_zip, new_version, sha,
                              str(os.path.getsize(new_zip))], capture_output=True, text=True, timeout=WAIT)
        print(old.stdout.rstrip())
        if old.returncode:
            report(app)
            raise SystemExit("FAILED: the %s updater could not hand over:\n%s" % (tag, old.stderr[-4000:]))
        build = os.path.join(app, "build.json")
        wait("build.json says %s" % new_version,
             lambda: new_version in read(build) and json.loads(read(build) or "{}").get("version") == new_version, app, new_version)
        if os.path.isdir(os.path.join(app, ".recovery")):
            wait("the update journal is COMMITTED", lambda: journal_state(app) == "COMMITTED", app, new_version)
        wait("Uncle Farmer started again", lambda: bool(processes(os.path.join(app, EXE))), app, new_version)
        wait("the restarted app says the update finished",
             lambda: "the update to %s finished" % new_version in read(os.path.join(app, "logs", "update.log")), app, new_version)
        print("  passed in %.0f s" % (time.time() - began), flush=True)
        stop(app)
    finally:
        run("git", "worktree", "remove", "--force", source)


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("update_compat.py runs on Windows only")
    ctypes.windll.kernel32.SetErrorMode(0x8003)      # no crash dialogs on a runner
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--new", required=True, help="the new build's zip")
    parser.add_argument("--from", dest="sources", action="append", default=[],
                        help="master, dev or a release tag (repeatable; default: master and dev)")
    parser.add_argument("--work", default=os.path.join(os.environ.get("RUNNER_TEMP", "dist"), "update-compat"))
    args = parser.parse_args()
    new_zip = os.path.abspath(args.new)
    new_version = build_version(new_zip)
    tags: list[str] = []
    for which in args.sources or ["master", "dev"]:
        tag = resolve(which)
        if tag not in tags and tag.lstrip("v") != new_version:
            tags.append(tag)
    for tag in tags:
        update_from(tag, new_zip, new_version, os.path.abspath(args.work))
    print("PASS: %s updates to %s" % (", ".join(tags) or "nothing", new_version))


if __name__ == "__main__":
    main()
