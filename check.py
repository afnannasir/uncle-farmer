"""Every check, in one go: what the GitHub workflow runs on each push, now on this computer.

    python check.py            ruff, pyright, pytest with coverage, then the agent's Java tests
    python check.py --quick    the same without the slow real-time tests

Exit code 0 when everything passed. release.py --local runs it before it builds anything, and
.githooks/pre-commit runs it on every commit.
"""

import os
import shutil
import subprocess
import sys

CORE_FLOOR = 90              # unclefarmer/engine, the bot itself


def have_jdk():
    """javac from JAVA_HOME or PATH: the agent's tests need a JDK, the app itself doesn't."""
    home = os.environ.get("JAVA_HOME")
    if home and os.path.exists(os.path.join(home, "bin", "javac.exe" if os.name == "nt" else "javac")):
        return True
    return shutil.which("javac") is not None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    tests = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "--cov=unclefarmer", "--cov-report=term-missing"]
    if "--quick" in argv:
        tests += ["-m", "not slow"]
    core = [sys.executable, "-m", "coverage", "report",
            "--include=unclefarmer/*", "--omit=unclefarmer/gui/*",
            "--fail-under=%d" % CORE_FLOOR]
    steps = [("ruff", [sys.executable, "-m", "ruff", "check", "."]),
             ("pyright", [sys.executable, "-m", "pyright", "--pythonpath", sys.executable]),
             ("pytest", tests)]
    # The agent runs inside the game: a broken parser takes the game down, so it is checked here
    # too (compiled into a temporary folder, never over the jar a running game has loaded).
    if have_jdk():
        steps.append(("agent java tests", [sys.executable, os.path.join("agent", "java_tests.py")]))
    else:
        print("== agent java tests: skipped, no JDK (install one, or set JAVA_HOME)", flush=True)
    failed = []
    for name, cmd in steps:
        print("== %s" % name, flush=True)
        if subprocess.call(cmd) != 0:
            failed.append(name)
    if os.path.exists(".coverage"):
        # The engine on its own: the GUI is untested and must not hide an engine regression.
        print("== core coverage (%d%%)" % CORE_FLOOR, flush=True)
        if subprocess.call(core) != 0:
            failed.append("core coverage")
    print("\n" + ("failed: " + ", ".join(failed) if failed else "all checks passed"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
