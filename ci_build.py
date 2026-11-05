"""The CI build: the Windows folder, zipped, checked, described and checksummed, ready to upload.

    python ci_build.py build --type development|production [--out DIR]
    python ci_build.py verify-agent <unclefarmer-agent.jar>
    python ci_build.py publish-dev --zip <built zip> --version <x.y.z-dev.N> --sha <commit> [--keep N]

build is what the workflow's build job runs once every test job has passed. It changes nothing in
how a release is made: build_exe.py builds the folder (with its own folder check) and release.py's
make_zip zips it, as for a release. Then:

  1. the zip is opened and read back, and must hold everything build_exe.REQUIRED names;
  2. the built exe checks itself without a window (UncleFarmer.exe --self-test, selftest.py) within
     SMOKE_SECONDS, so a build that can't start or can't find its data never gets uploaded;
  3. build-info.json says what the build is (version, release version, channel, type, branch,
     commit, time, run, tools);
  4. <zip>.sha256 holds the zip's SHA-256 ("<hash> *<name>", as sha256sum writes it), computed on
     the finished zip and checked again by reading the file back.

Names: UncleFarmer-dev-<short sha>.zip for a development build, UncleFarmer-master-<version>-<short
sha>.zip for a production one. The version is the project's own (unclefarmer/__init__.py): this
script never changes it. GITHUB_* variables say the branch, commit and run; outside GitHub git does.

The release version is what the updater compares: the version itself for a production build,
<version>-dev.<run number> for a development one (0.9.0-dev.57: the 57th CI run, made after
0.9.0). Before zipping, build.json beside the exe says it and the channel (updater.write_build),
so the installed copy knows which build it is.

publish-dev is the dev channel's publishing (ci.yml's publish-dev job, after a dev build passed):
the zip goes up as a GitHub PRE-release tagged v<release version> with the asset
UncleFarmer-<release version>.zip and its .sha256, never marked latest, so the master channel
(GitHub's latest release) never sees it. It is read back and checked, and only the newest --keep
dev pre-releases are kept: older ones are deleted with their tags, and nothing else is ever
touched (a release must be a pre-release AND have a dev tag to be deleted). It uses the gh CLI
and the standard library only; GH_TOKEN and GITHUB_REPOSITORY come from the workflow.

verify-agent checks a built jar: the manifest's Premain-Class, and that every class targets the
game's Java 8 (class file major version 52 or lower).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from collections.abc import Callable
from typing import Any

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

TYPES = {"development": "dev", "production": "master"}
# the dev channel's version (updater.DEV_VERSION: a test keeps the two the same) and its tag
DEV_VERSION = re.compile(r"^\d+\.\d+\.\d+-dev\.\d+$")
DEV_TAG = re.compile(r"^v\d+\.\d+\.\d+-dev\.\d+$")
DEV_KEEP = 10               # dev pre-releases kept on GitHub (the updater looks through 30)
SMOKE_SECONDS = 120
JAVA8 = 52                  # class file major version of Java 8, the game's own Java
PREMAIN = "unclefarmer.agent.BotAgent"


def fail(why: str) -> None:
    """Stop with the reason as a GitHub Actions error line (shown on the run's summary page)."""
    print("::error::%s" % why if os.environ.get("GITHUB_ACTIONS") else "error: %s" % why, flush=True)
    sys.exit(1)


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True, check=True).stdout.strip()


def commit() -> tuple[str, str]:
    """(full sha, branch): GitHub's own variables in a workflow, else the checkout's HEAD."""
    sha = os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD")
    branch = os.environ.get("GITHUB_REF_NAME") or git("rev-parse", "--abbrev-ref", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        fail("not a commit sha: %r" % sha)
    return sha, branch


def zip_name(build_type: str, version: str, sha: str) -> str:
    """UncleFarmer-dev-<short>.zip or UncleFarmer-master-<version>-<short>.zip."""
    short = sha[:7]
    if build_type == "development":
        return "UncleFarmer-dev-%s.zip" % short
    if build_type == "production":
        return "UncleFarmer-master-%s-%s.zip" % (version, short)
    fail("unknown build type %r (development or production)" % build_type)
    return ""


def run_number() -> int:
    """The workflow's run number (it only grows, on every branch); 0 outside GitHub."""
    try:
        return max(0, int(os.environ.get("GITHUB_RUN_NUMBER") or 0))
    except ValueError:
        return 0


def release_version(build_type: str, version: str, run: int) -> str:
    """What the updater compares: 0.9.0 for a production build, 0.9.0-dev.57 for a development one."""
    if build_type == "production":
        return version
    if build_type == "development":
        return "%s-dev.%d" % (version, run)
    fail("unknown build type %r (development or production)" % build_type)
    return ""


def tool_version(cmd: list[str]) -> str:
    """The first line a tool prints about its version, or "unknown"."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    lines = (r.stdout + r.stderr).strip().splitlines()
    return lines[0].strip() if lines else "unknown"


def java_version() -> str:
    home = os.environ.get("JAVA_HOME")
    javac = os.path.join(home, "bin", "javac") if home else "javac"
    return tool_version([javac, "-version"])


def build_info(build_type: str, version: str, sha: str, branch: str, zip_path: str, digest: str,
               release: str | None = None) -> dict[str, Any]:
    env = os.environ
    return {
        "application": "Uncle Farmer",
        "version": version,
        "release_version": release or version,
        "channel": TYPES.get(build_type, ""),
        "build_type": build_type,
        "branch": branch,
        "commit": sha,
        "commit_short": sha[:7],
        "built_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "workflow_run_id": env.get("GITHUB_RUN_ID"),
        "workflow_run_number": env.get("GITHUB_RUN_NUMBER"),
        "workflow_run_attempt": env.get("GITHUB_RUN_ATTEMPT"),
        "repository": env.get("GITHUB_REPOSITORY"),
        "python": platform.python_version(),
        "java": java_version(),
        "upx": tool_version([os.path.join(env["UPX_DIR"], "upx"), "--version"]) if env.get("UPX_DIR")
        else tool_version(["upx", "--version"]),
        "os": "%s %s (%s)" % (platform.system(), platform.version(), platform.machine()),
        "runner": {"os": env.get("RUNNER_OS"), "arch": env.get("RUNNER_ARCH"), "name": env.get("RUNNER_NAME"),
                   "image": env.get("ImageOS"), "image_version": env.get("ImageVersion")},
        "package": os.path.basename(zip_path),
        "package_bytes": os.path.getsize(zip_path),
        "sha256": digest,
    }


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_checksum(zip_path: str) -> str:
    """<zip>.sha256 beside the zip, from the finished file, checked by reading it back."""
    digest = sha256_of(zip_path)
    sum_path = zip_path + ".sha256"
    with open(sum_path, "w", encoding="ascii", newline="\n") as f:
        f.write("%s *%s\n" % (digest, os.path.basename(zip_path)))
    with open(sum_path, encoding="ascii") as f:
        written = f.read().split()[0]
    if written != digest or sha256_of(zip_path) != digest:
        fail("the checksum file doesn't match %s" % zip_path)
    return digest


def verify_zip(zip_path: str, required: tuple[str, ...]) -> list[str]:
    """Open the zip, read every member back (CRC check), and find each required path under
    "Uncle Farmer/". Returns what is missing; a broken zip stops here."""
    import fnmatch
    try:
        with zipfile.ZipFile(zip_path) as z:
            bad = z.testzip()
            if bad:
                fail("%s: %s is damaged" % (zip_path, bad))
            names = z.namelist()
    except zipfile.BadZipFile as e:
        fail("%s is not a readable zip: %s" % (zip_path, e))
        return []
    if not names or any(not n.startswith("Uncle Farmer/") for n in names):
        fail("%s must hold one 'Uncle Farmer' folder" % zip_path)
    inside = [n[len("Uncle Farmer/"):] for n in names]
    missing: list[str] = []
    for pattern in required:
        if not any(fnmatch.fnmatch(n, pattern) or n.startswith(pattern.rstrip("/") + "/") for n in inside):
            missing.append(pattern)
    return missing


def smoke_test(zip_path: str, work: str) -> dict[str, Any]:
    """Unpack the zip into work and run its exe's --self-test, with a time limit."""
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(work)
    exe = os.path.join(work, "Uncle Farmer", "UncleFarmer.exe")
    report = os.path.join(work, "self-test.json")
    try:
        code = subprocess.run([exe, "--self-test", report], timeout=SMOKE_SECONDS).returncode
    except subprocess.TimeoutExpired:
        fail("the built exe's self-test didn't finish in %d s" % SMOKE_SECONDS)
        return {}
    except OSError as e:
        fail("the built exe didn't start: %s" % e)
        return {}
    if not os.path.isfile(report):
        fail("the built exe wrote no self-test report (exit code %d)" % code)
    with open(report, encoding="utf-8") as f:
        result: dict[str, Any] = json.load(f)
    for row in result.get("checks", []):
        print("   self-test %-16s %s  %s" % (row["check"], "ok" if row["ok"] else "FAILED", row["detail"]), flush=True)
    if code != 0 or not result.get("ok") or not result.get("frozen"):
        fail("the built exe's self-test failed (exit code %d): see the lines above" % code)
    return result


def build(build_type: str, out: str) -> None:
    import build_exe
    import release
    from unclefarmer import __version__
    sha, branch = commit()
    head = git("rev-parse", "HEAD")
    if head != sha:
        fail("the checkout is at %s, not the commit that was tested (%s)" % (head, sha))
    from unclefarmer import updater
    name = zip_name(build_type, __version__, sha)
    rv = release_version(build_type, __version__, run_number())
    print("== building %s (%s %s, %s at %s)" % (name, build_type, rv, branch, sha), flush=True)
    build_exe.main(full=True)
    updater.write_build(build_exe.OUT, rv, TYPES[build_type], sha)
    os.makedirs(out, exist_ok=True)
    zip_path = os.path.join(out, name)
    if os.path.exists(zip_path):
        os.remove(zip_path)
    release.make_zip(__version__, zip_path)
    print("== checking the zip", flush=True)
    missing = verify_zip(zip_path, build_exe.REQUIRED)
    if missing:
        fail("%s lacks: %s" % (name, ", ".join(missing)))
    print("== the built exe's self-test", flush=True)
    smoke_test(zip_path, os.path.join(out, "smoke"))
    shutil.rmtree(os.path.join(out, "smoke"), ignore_errors=True)
    print("== checksum and build info", flush=True)
    digest = write_checksum(zip_path)
    info = build_info(build_type, __version__, sha, branch, zip_path, digest, rv)
    with open(os.path.join(out, "build-info.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(info, f, indent=2)
    print("built %s (%.1f MB), sha256 %s" % (zip_path, info["package_bytes"] / 1e6, digest), flush=True)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write("artifact=%s\nzip=%s\nrelease_version=%s\n" % (name[:-len(".zip")], zip_path, rv))


def class_major(data: bytes) -> int:
    """A class file's major version (CAFEBABE, minor, major)."""
    if len(data) < 8 or data[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("not a class file")
    return struct.unpack(">H", data[6:8])[0]


def verify_agent(jar: str) -> None:
    """The jar the game loads: a manifest naming the agent's entry class, and Java 8 class files."""
    if not os.path.isfile(jar):
        fail("no agent jar at %s" % jar)
    try:
        with zipfile.ZipFile(jar) as z:
            manifest = z.read("META-INF/MANIFEST.MF").decode("utf-8")
            classes = [n for n in z.namelist() if n.endswith(".class")]
            too_new = sorted({n for n in classes if class_major(z.read(n)) > JAVA8})
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        fail("%s is not a usable agent jar: %s" % (jar, e))
        return
    if "Premain-Class: %s" % PREMAIN not in manifest:
        fail("%s: the manifest doesn't name Premain-Class %s" % (jar, PREMAIN))
    if not any(n == PREMAIN.replace(".", "/") + ".class" for n in classes):
        fail("%s doesn't hold %s" % (jar, PREMAIN))
    if too_new:
        fail("%s: classes newer than Java 8 (the game's Java can't load them): %s" % (jar, ", ".join(too_new[:5])))
    print("agent jar ok: %d classes, all Java 8 (major <= %d), Premain-Class %s" % (len(classes), JAVA8, PREMAIN))


# ------------------------------------------------------------------------------- publish-dev
Runner = Callable[[list[str]], str]


def gh(args: list[str]) -> str:
    """Run the gh CLI and return what it printed; a failure stops the job with gh's own words."""
    r = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        fail("gh %s failed: %s" % (" ".join(args[:3]), (r.stderr or r.stdout).strip()))
    return r.stdout


def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", v)[:4])


def is_dev_release(rel: dict[str, Any]) -> bool:
    """A release publish-dev made, and the only kind it may replace or delete: a pre-release with
    a dev tag (v0.9.0-dev.57). A stable release is never one, whatever else it says."""
    return bool(rel.get("prerelease")) and bool(DEV_TAG.match(str(rel.get("tag_name") or "")))


def to_prune(releases: list[dict[str, Any]], keep: int) -> list[str]:
    """The tags of the dev pre-releases past the newest keep (newest by version, not by date)."""
    dev = sorted((r for r in releases if is_dev_release(r)), key=lambda r: _version_key(r["tag_name"]), reverse=True)
    return [r["tag_name"] for r in dev[max(1, keep):]]


def dev_notes(version: str, sha: str, branch: str = "dev") -> str:
    return ("Development build %s of Uncle Farmer, built by CI from the %s branch at %s.\n\n"
            "Not a stable release: it has the newest changes and can break. Only copies set to the Dev "
            "release channel (Settings, Updates) are offered it." % (version, branch, sha[:7]))


def publish_dev(zip_path: str, version: str, sha: str, keep: int = DEV_KEEP, repo: str | None = None,
                run: Runner = gh) -> str:
    """Publish one dev build as a pre-release (see the module notes). Returns the tag."""
    repo = repo or os.environ.get("GITHUB_REPOSITORY") or ""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        fail("no repository to publish to (GITHUB_REPOSITORY): %r" % repo)
    if not DEV_VERSION.match(version):
        fail("not a dev build's version: %r (x.y.z-dev.N)" % version)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        fail("not a commit sha: %r" % sha)
    if not os.path.isfile(zip_path):
        fail("no built zip at %s" % zip_path)
    tag, asset = "v" + version, "UncleFarmer-%s.zip" % version
    folder = os.path.join(os.path.dirname(os.path.abspath(zip_path)), "publish")
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    upload = os.path.join(folder, asset)
    shutil.copyfile(zip_path, upload)
    digest = write_checksum(upload)
    if digest != sha256_of(zip_path):
        fail("the copy to publish isn't the built zip")
    # A re-run of the same workflow run has the same run number: its own pre-release is replaced.
    existing = json.loads(run(["api", "repos/%s/releases?per_page=100" % repo]) or "[]")
    for rel in existing:
        if rel.get("tag_name") == tag:
            if not is_dev_release(rel):
                fail("%s exists and is not a dev pre-release: left alone" % tag)
            print("== replacing the earlier %s" % tag, flush=True)
            run(["release", "delete", tag, "--repo", repo, "--cleanup-tag", "--yes"])
    print("== publishing %s (%s)" % (tag, asset), flush=True)
    run(["release", "create", tag, upload, upload + ".sha256", "--repo", repo, "--target", sha, "--prerelease",
         "--latest=false", "--title", "Uncle Farmer %s (development build)" % version,
         "--notes", dev_notes(version, sha)])
    got = json.loads(run(["api", "repos/%s/releases/tags/%s" % (repo, tag)]))
    names = {a.get("name"): a for a in got.get("assets", [])}
    if not is_dev_release(got) or got.get("draft") or asset not in names:
        fail("%s didn't come out as a dev pre-release with %s" % (tag, asset))
    published = str(names[asset].get("digest") or "")
    if published and published.lower() != "sha256:" + digest:
        fail("GitHub's checksum of %s (%s) isn't the built zip's (%s)" % (asset, published, digest))
    print("published %s: %s, sha256 %s" % (tag, asset, digest), flush=True)
    for old in to_prune(existing + [got], keep):
        if old == tag:
            continue
        print("== removing the old dev build %s" % old, flush=True)
        run(["release", "delete", old, "--repo", repo, "--cleanup-tag", "--yes"])
    return tag


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build, zip, check, describe and checksum")
    b.add_argument("--type", required=True, choices=sorted(TYPES))
    b.add_argument("--out", default=os.path.join(ROOT, "dist", "ci"))
    v = sub.add_parser("verify-agent", help="check a built agent jar")
    v.add_argument("jar")
    p = sub.add_parser("publish-dev", help="publish a dev build as a GitHub pre-release (CI)")
    p.add_argument("--zip", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--sha", required=True)
    p.add_argument("--keep", type=int, default=DEV_KEEP)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(a.type, os.path.abspath(a.out))
    elif a.cmd == "publish-dev":
        publish_dev(a.zip, a.version, a.sha, a.keep)
    else:
        verify_agent(a.jar)


if __name__ == "__main__":
    main()
