"""The CI build: the Windows folder, zipped, checked, described and checksummed, ready to upload.

    python ci_build.py build --type development|production [--out DIR]
    python ci_build.py verify-agent <unclefarmer-agent.jar>

build is what the workflow's build job runs once every test job has passed. It changes nothing in
how a release is made: build_exe.py builds the folder (with its own folder check) and release.py's
make_zip zips it, as for a release. Then:

  1. the zip is opened and read back, and must hold everything build_exe.REQUIRED names;
  2. the built exe checks itself without a window (UncleFarmer.exe --self-test, selftest.py) within
     SMOKE_SECONDS, so a build that can't start or can't find its data never gets uploaded;
  3. build-info.json says what the build is (version, type, branch, commit, time, run, tools);
  4. <zip>.sha256 holds the zip's SHA-256 ("<hash> *<name>", as sha256sum writes it), computed on
     the finished zip and checked again by reading the file back.

Names: UncleFarmer-dev-<short sha>.zip for a development build, UncleFarmer-master-<version>-<short
sha>.zip for a production one. The version is the project's own (unclefarmer/__init__.py): this
script never changes it. GITHUB_* variables say the branch, commit and run; outside GitHub git does.

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
from typing import Any

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

TYPES = {"development": "dev", "production": "master"}
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


def build_info(build_type: str, version: str, sha: str, branch: str, zip_path: str, digest: str) -> dict[str, Any]:
    env = os.environ
    return {
        "application": "Uncle Farmer",
        "version": version,
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
    name = zip_name(build_type, __version__, sha)
    print("== building %s (%s, %s at %s)" % (name, build_type, branch, sha), flush=True)
    build_exe.main(full=True)
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
    info = build_info(build_type, __version__, sha, branch, zip_path, digest)
    with open(os.path.join(out, "build-info.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(info, f, indent=2)
    print("built %s (%.1f MB), sha256 %s" % (zip_path, info["package_bytes"] / 1e6, digest), flush=True)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write("artifact=%s\nzip=%s\n" % (name[:-len(".zip")], zip_path))


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


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build, zip, check, describe and checksum")
    b.add_argument("--type", required=True, choices=sorted(TYPES))
    b.add_argument("--out", default=os.path.join(ROOT, "dist", "ci"))
    v = sub.add_parser("verify-agent", help="check a built agent jar")
    v.add_argument("jar")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(a.type, os.path.abspath(a.out))
    else:
        verify_agent(a.jar)


if __name__ == "__main__":
    main()
