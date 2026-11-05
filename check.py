"""Every check, in one go: what the GitHub workflow runs on each push, now on this computer.

    python check.py            ruff, pyright, check-manifest, pytest with coverage, then the agent's Java tests
    python check.py --quick    the same without the slow real-time tests
    python check.py --report-dir reports   also write pytest.xml (JUnit) and coverage.xml there
    python check.py --skip-agent           leave the Java tests out (CI runs them in their own job)
    python check.py --only ruff,pyright,manifest   just those steps (CI's static job)
    python check.py --only core            the engine's own floor, on the .coverage pytest left
                                           (CI's tests job runs `uv run pytest` itself, then this)

Exit code 0 when everything passed. release.py --local runs it before it builds anything, and
.githooks/pre-commit runs it on every commit.
"""

import os
import shutil
import subprocess
import sys

CORE_FLOOR = 92              # unclefarmer/engine, the bot itself (the whole app's floor is
                             # [tool.coverage.report] fail_under in pyproject.toml)
# The tests with coverage, as CI's tests job runs them (`uv run pytest` + these): the overall floor
# comes from pyproject.toml, so a plain pytest run of a few files never fails on coverage.
PYTEST_COV = ["--cov", "--cov-report=term-missing"]
# Every step runs in the repository, wherever check.py was started from: ruff and pyright check
# "this folder", and release.py run from the folder above once linted every project in it.
ROOT = os.path.dirname(os.path.abspath(__file__))


def have_jdk() -> bool:
    """javac from JAVA_HOME or PATH: the agent's tests need a JDK, the app itself doesn't."""
    home = os.environ.get("JAVA_HOME")
    if home and os.path.exists(os.path.join(home, "bin", "javac.exe" if os.name == "nt" else "javac")):
        return True
    return shutil.which("javac") is not None


def report_dir(argv: list[str]) -> str | None:
    """--report-dir DIR: where CI keeps the test results, made absolute (the steps run in ROOT)."""
    if "--report-dir" not in argv:
        return None
    i = argv.index("--report-dir")
    if i + 1 >= len(argv):
        sys.exit("--report-dir needs a folder")
    folder = os.path.abspath(argv[i + 1])
    os.makedirs(folder, exist_ok=True)
    return folder


STEPS = ("ruff", "pyright", "manifest", "pytest", "core", "agent")


def only(argv: list[str]) -> set[str]:
    """--only a,b: the steps to run (all of STEPS without it). CI runs the static checks and the
    tests in parallel jobs, each one exactly once, with the same commands and floors as here."""
    if "--only" not in argv:
        return set(STEPS)
    i = argv.index("--only")
    names: set[str] = {n.strip() for n in argv[i + 1].split(",")} if i + 1 < len(argv) else set()
    unknown = names - set(STEPS)
    if not names or unknown:
        sys.exit("--only takes a comma list of: " + ", ".join(STEPS))
    return names


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    reports = report_dir(argv)
    wanted = only(argv)
    tests = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"] + PYTEST_COV
    if reports:
        tests += ["--junitxml=" + os.path.join(reports, "pytest.xml"),
                  "--cov-report=xml:" + os.path.join(reports, "coverage.xml")]
    if "--quick" in argv:
        tests += ["-m", "not slow"]
    core = [sys.executable, "-m", "coverage", "report",
            "--include=unclefarmer/*", "--omit=unclefarmer/gui/*",
            "--fail-under=%d" % CORE_FLOOR]
    # check-manifest builds the source distribution (MANIFEST.in) with the locked setuptools, not
    # one downloaded for the build, and compares it with what git tracks.
    steps = [step for step in [("ruff", [sys.executable, "-m", "ruff", "check", "."]),
                               ("pyright", [sys.executable, "-m", "pyright", "--pythonpath", sys.executable]),
                               ("manifest", [sys.executable, "-m", "check_manifest", "--no-build-isolation"]),
                               ("pytest", tests)]
             if step[0] in wanted]
    # The agent runs inside the game: a broken parser takes the game down, so it is checked here
    # too (compiled into a temporary folder, never over the jar a running game has loaded).
    if "agent" in wanted:
        if "--skip-agent" in argv:
            print("== agent java tests: left out (--skip-agent)", flush=True)
        elif have_jdk():
            steps.append(("agent java tests", [sys.executable, os.path.join("agent", "java_tests.py")]))
        else:
            print("== agent java tests: skipped, no JDK (install one, or set JAVA_HOME)", flush=True)
    failed: list[str] = []
    egg = os.path.join(ROOT, "unclefarmer.egg-info")
    for name, cmd in steps:
        print("== %s" % name, flush=True)
        made_egg = name == "manifest" and not os.path.exists(egg)
        if subprocess.call(cmd, cwd=ROOT) != 0:
            failed.append(name)
        if made_egg:
            # setuptools writes the sdist's metadata beside the source; it is the check's leftover
            # (with the README in it), never part of the tree
            shutil.rmtree(egg, ignore_errors=True)
    if "pytest" in wanted or "core" in wanted:
        # The engine on its own: the GUI is untested and must not hide an engine regression.
        print("== core coverage (%d%%)" % CORE_FLOOR, flush=True)
        if not os.path.exists(os.path.join(ROOT, ".coverage")):
            print("no .coverage to report on: run the tests with coverage first", flush=True)
            failed.append("core coverage")
        elif subprocess.call(core, cwd=ROOT) != 0:
            failed.append("core coverage")
    print("\n" + ("failed: " + ", ".join(failed) if failed else "all checks passed"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
