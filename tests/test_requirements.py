"""requirements.txt and requirements-dev.txt (for pip users) must hold exactly uv.lock's pins: a
dependency changed with `uv lock` but not exported would install other versions than the ones
tested."""

from __future__ import annotations

import os
import shutil

import pytest

import export_requirements

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv to export the lock")


def test_windows_export_normalizes_the_redundant_platform_marker():
    assert export_requirements.normalize_windows_export("one ; sys_platform == 'win32'\ntwo\n") == "one\ntwo\n"


@pytest.mark.parametrize("name", sorted(export_requirements.FILES))
def test_the_file_matches_the_lock(name: str):
    with open(os.path.join(export_requirements.ROOT, name), encoding="utf-8") as f:
        on_disk = f.read()
    assert on_disk == export_requirements.exported(name), (
        "%s is out of date: run `uv run python export_requirements.py`" % name)


def test_the_app_file_holds_only_what_the_app_runs_on():
    text = export_requirements.exported("requirements.txt")
    for runtime in ("wxpython==", "numpy==", "orjson==", "pydantic=="):
        assert runtime in text.lower()
    for tool in ("pytest==", "nuitka==", "pyright=="):
        assert tool not in text.lower()
    assert "--hash=sha256:" in text
