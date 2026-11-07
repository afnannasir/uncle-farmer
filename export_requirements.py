"""Write requirements.txt and requirements-dev.txt from uv.lock, for running from source with pip.

    python export_requirements.py

uv.lock is the source of truth (uv sync); these two files are the same pins for people who use pip
instead: requirements.txt holds what the app needs to run, requirements-dev.txt adds the checking
tools and Nuitka. Every pin carries its hashes, so pip checks each download. Run this after every
`uv lock` (release.py does it when it sets a version); tests/test_requirements.py fails while
either file differs from the lock.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
# file -> (what it is for, the uv export options that choose its groups)
FILES = {
    "requirements.txt": ("to run from source", ["--no-default-groups"]),
    "requirements-dev.txt": ("to run, check (check.py) and build (build_exe.py)", ["--all-groups"]),
}
WINDOWS_MARKER = " ; sys_platform == 'win32'"


def normalize_windows_export(text: str) -> str:
    """A Windows-only requirements file does not need uv's redundant Windows marker.

    uv 0.8 wrote it while newer uv versions omit it, although both exports describe the same
    Windows pins. Keeping the file normalized makes the checked-in pip files independent of that
    formatting change.
    """
    return text.replace(WINDOWS_MARKER, "")


def exported(name: str) -> str:
    """The file's text as uv.lock says it should be. --locked: a lock that no longer matches
    pyproject.toml is an error, never quietly refreshed."""
    purpose, groups = FILES[name]
    out = subprocess.run(["uv", "export", "--locked", "--quiet", "--no-emit-project", "--no-header",
                          "--format", "requirements-txt", *groups],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        sys.exit("uv export failed:\n" + out.stderr)
    header = ("# Uncle Farmer's packages %s, pinned, for Python 3.14 on Windows:\n"
              "#     py -3.14 -m venv .venv\n"
              "#     .venv\\Scripts\\pip install -r %s\n"
              "# Made from uv.lock by export_requirements.py: don't edit it, run that after `uv lock`.\n"
              % (purpose, name))
    return header + normalize_windows_export(out.stdout.replace("\r\n", "\n"))


def main() -> int:
    for name in FILES:
        with open(os.path.join(ROOT, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(exported(name))
        print("wrote " + name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
