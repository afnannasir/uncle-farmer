"""Publish a new version of Uncle Farmer on GitHub, for every copy's updater to find.

    python release.py 0.2.0 --notes "What's new, in a line or two"
    python release.py 0.2.0 --notes-file notes.md
    python release.py 0.2.0 --draft          (a draft release: no copy sees it until you publish it)
    python release.py 0.2.0 --local --notes-file notes.md   (build and zip only, no GitHub)
    python release.py 0.2.0 --local --full ...              (every core, normal priority: faster)

It sets the version in unclefarmer/__init__.py and pyproject.toml (and refreshes uv.lock), builds
the folder (build_exe.py), zips it as dist/UncleFarmer-<version>.zip and makes the GitHub release
v<version> with that zip, using the GitHub CLI (gh, logged in). The release notes are what the app shows under "What's new". Commit
and push first: the release's tag points at what GitHub has.

--local builds and zips without GitHub: the notes go into the zip as "What's new.txt" and next to
it, and the old builds in dist are removed first, so dist holds just this release.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from unclefarmer import updater  # noqa: E402

INIT = os.path.join(ROOT, "unclefarmer", "__init__.py")
PYPROJECT = os.path.join(ROOT, "pyproject.toml")
OUT = os.path.join(ROOT, "dist", "Uncle Farmer")


def _replace_once(path: str, pattern: str, new: str) -> None:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    text, n = re.subn(pattern, new, text, count=1, flags=re.M)
    if n != 1:
        sys.exit("couldn't find the version in " + path)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def set_version(v: str) -> None:
    """The version in unclefarmer/__init__.py and pyproject.toml, and uv.lock to match: the lock
    records the project's version, and CI's `uv sync --locked` fails when the two disagree."""
    _replace_once(INIT, r'__version__ = "[^"]*"', '__version__ = "%s"' % v)
    _replace_once(PYPROJECT, r'^version = "[^"]*"', 'version = "%s"' % v)
    if subprocess.call(["uv", "lock"], cwd=ROOT) != 0:
        sys.exit("uv lock failed: uv.lock no longer matches pyproject.toml")


def make_zip(v: str) -> str:
    path = os.path.join(ROOT, "dist", "UncleFarmer-%s.zip" % v)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, _dirs, files in os.walk(OUT):
            rel = os.path.relpath(folder, OUT)
            top = rel.split(os.sep)[0].lower()
            if top in updater.NEVER or (rel != "." and top == "agent" and "run" in rel.split(os.sep)):
                sys.exit("the built folder holds %s - a player's own files never go into a release" % rel)
            for f in files:
                if rel == "." and f.lower() in updater.NEVER:
                    sys.exit("the built folder holds %s - a player's own files never go into a release" % f)
                if f == "game_texts.json":
                    sys.exit("the built folder holds game_texts.json, which is made from each player's game")
                full = os.path.join(folder, f)
                z.write(full, os.path.join("Uncle Farmer", os.path.relpath(full, OUT)))
    return path


def main() -> None:
    a = _args()
    v = _checked_version(a)
    notes = a.notes
    if a.notes_file:
        with open(a.notes_file, encoding="utf-8") as f:
            notes = f.read()
    if a.local:
        _clear_dist()
    set_version(v)
    import build_exe
    build_exe.main(full=a.full)
    if a.local and notes:
        with open(os.path.join(OUT, "What's new.txt"), "w", encoding="utf-8") as f:
            f.write("Uncle Farmer %s\n\n%s\n" % (v, notes.strip()))
    zip_path = make_zip(v)
    print("zipped:", zip_path, "(%.1f MB)" % (os.path.getsize(zip_path) / 1e6))
    if a.local:
        if notes:
            with open(os.path.join(ROOT, "dist", "UncleFarmer-%s.txt" % v), "w", encoding="utf-8") as f:
                f.write("Uncle Farmer %s\n\n%s\n" % (v, notes.strip()))
        print("built v%s locally. Commit the version change (unclefarmer/__init__.py, pyproject.toml, uv.lock)." % v)
        return
    _publish(a, v, zip_path)


@dataclass
class Options:
    """The command line, as _args() reads it."""
    version: str
    notes: str | None
    notes_file: str | None
    draft: bool
    local: bool
    no_check: bool
    full: bool


def _args() -> Options:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", help="the new version, e.g. 0.2.0")
    ap.add_argument("--notes", help="release notes (What's new)")
    ap.add_argument("--notes-file", help="release notes from a file")
    ap.add_argument("--draft", action="store_true", help="make a draft release")
    ap.add_argument("--local", action="store_true", help="build and zip only; nothing goes to GitHub")
    ap.add_argument("--no-check", action="store_true",
                    help="skip check.py before building (only for --local builds)")
    ap.add_argument("--full", action="store_true",
                    help="build with every core at normal priority (faster; the PC is busy meanwhile)")
    ns = ap.parse_args()
    return Options(version=ns.version, notes=ns.notes, notes_file=ns.notes_file, draft=ns.draft,
                   local=ns.local, no_check=ns.no_check, full=ns.full)


def _checked_version(a: Options) -> str:
    """The version to build, once everything that must hold first does: a newer version, a clean
    tree for a published release, and the checks passed."""
    if a.no_check and not a.local:
        sys.exit("--no-check is only for --local builds: a published release must pass its checks")
    v = a.version.lstrip("vV")
    if not re.fullmatch(r"\d+(\.\d+){1,3}", v):
        sys.exit("a version looks like 0.2.0")
    from unclefarmer import __version__
    if not updater.is_newer(v, __version__) and v != __version__:
        sys.exit("%s is not newer than %s" % (v, __version__))
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout
    if dirty.strip() and not a.local:
        sys.exit("there are uncommitted changes: commit them first (a --local build may be dirty)")
    if not a.no_check:
        import check
        if check.main([]) != 0:
            sys.exit("the checks failed: nothing was built (fix them, or --no-check)")
    return v


def _clear_dist() -> None:
    """Older releases out of dist: it holds this one only."""
    dist = os.path.join(ROOT, "dist")
    for name in os.listdir(dist) if os.path.isdir(dist) else []:
        if name.startswith("UncleFarmer-") and name.endswith((".zip", ".txt")):
            os.remove(os.path.join(dist, name))


def _publish(a: Options, v: str, zip_path: str) -> None:
    """The GitHub release v<version> with the zip."""
    if not updater.REPO:
        sys.exit("no GitHub repository is set (updater.REPO): use --local")
    cmd = ["gh", "release", "create", "v" + v, zip_path, "--repo", updater.REPO, "--title", "Uncle Farmer " + v]
    if a.notes_file:
        cmd += ["--notes-file", a.notes_file]
    elif a.notes:
        cmd += ["--notes", a.notes]
    else:
        cmd += ["--generate-notes"]
    if a.draft:
        cmd.append("--draft")
    subprocess.check_call(cmd, cwd=ROOT)
    print("released v%s. Commit the version change (unclefarmer/__init__.py, pyproject.toml, uv.lock)." % v)


if __name__ == "__main__":
    main()
