# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The log files: the level, the day's file name, rotation, and the best-effort promises."""

from __future__ import annotations

import os
import sys
import time

import pytest

from unclefarmer import applog
from pathlib import Path
from collections.abc import Iterator


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(applog, "_warned", False)
    monkeypatch.setattr(applog, "_rotate_after", {})
    yield
    applog.configure()


def test_write_appends_to_the_days_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(applog.paths, "LOGS", str(tmp_path))
    applog.write("first")
    applog.write("second")
    path = tmp_path / (time.strftime("%Y-%m-%d") + ".log")
    assert path.read_text(encoding="utf-8") == "first\nsecond\n"


def test_the_level_hides_lines_but_never_a_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(applog.paths, "LOGS", str(tmp_path))
    applog.configure({"log_level": "ERROR"})
    applog.write("quiet")
    assert list(tmp_path.glob("*.log")) == []
    try:
        raise ValueError("boom")
    except ValueError:
        applog.crash("Title", "Hero")
    assert "ValueError: boom" in (tmp_path / "errors.log").read_text(encoding="utf-8")


def test_warnings_pass_a_warning_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(applog.paths, "LOGS", str(tmp_path))
    applog.configure({"log_level": "WARNING"})
    applog.write("info line")
    applog.write("warning line", "WARNING")
    text = next(tmp_path.glob("*.log")).read_text(encoding="utf-8")
    assert "warning line" in text
    assert "info line" not in text


def test_rotation_keeps_the_set_number_of_old_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(applog.paths, "LOGS", str(tmp_path))
    applog.configure({"log_max_mb": 0.00001, "log_backups": 2})     # about ten bytes
    for n in range(5):
        applog.write("line number %d" % n)
    base = (tmp_path / (time.strftime("%Y-%m-%d") + ".log"))
    assert base.is_file()
    assert (tmp_path / (base.name + ".1")).is_file()
    assert (tmp_path / (base.name + ".2")).is_file()
    assert not (tmp_path / (base.name + ".3")).exists()
    assert "line number 4" in base.read_text(encoding="utf-8")
    assert "line number 3" in (tmp_path / (base.name + ".1")).read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="a file held open can't be renamed on Windows only")
def test_a_rotation_that_fails_still_writes_the_line_and_is_tried_again_later(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(applog.paths, "LOGS", str(tmp_path))
    applog.configure({"log_max_mb": 0.00001, "log_backups": 3})     # about ten bytes
    base = tmp_path / (time.strftime("%Y-%m-%d") + ".log")
    applog.write("first line, long enough to be rotated")
    applog.write("second line")                                     # rotates the first away
    old = tmp_path / (base.name + ".1")
    assert "first line" in old.read_text(encoding="utf-8")
    with open(base, encoding="utf-8"):                              # another program holds it: no rename
        applog.write("third line, written while the file is held")
        applog.write("fourth line, before the retry")
    assert "third line" in base.read_text(encoding="utf-8")
    assert "fourth line" in base.read_text(encoding="utf-8")
    assert not (tmp_path / (base.name + ".3")).exists()             # the failed tries pushed nothing out
    assert capsys.readouterr().err == ""                            # a failed rotation isn't a failed write
    applog.write("fifth line, still waiting for the retry")
    assert not (tmp_path / (base.name + ".3")).exists()
    monkeypatch.setattr(applog, "_rotate_after", {})               # the retry pause is over
    applog.write("sixth line, after the file was let go")
    assert "sixth line" in base.read_text(encoding="utf-8")
    assert "fifth line" in old.read_text(encoding="utf-8")
    assert "first line" in (tmp_path / (base.name + ".2")).read_text(encoding="utf-8")
    assert not (tmp_path / (base.name + ".3")).exists()


def test_a_rotation_waits_its_pause_before_it_is_tried_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(applog.paths, "LOGS", str(tmp_path))
    applog.configure({"log_max_mb": 0.00001, "log_backups": 2})
    tried: list[str] = []
    real = os.replace

    def locked(src: str, dst: str) -> None:
        tried.append(src)
        raise PermissionError(13, "in use by another program", src)
    monkeypatch.setattr(applog.os, "replace", locked)
    clock = [100.0]
    monkeypatch.setattr(applog.time, "monotonic", lambda: clock[0])
    base = tmp_path / (time.strftime("%Y-%m-%d") + ".log")
    applog.write("first line, long enough to be rotated")
    applog.write("second line")
    applog.write("third line")
    assert len(tried) == 1                                          # once, then the pause
    assert base.read_text(encoding="utf-8").count("line") == 3
    clock[0] += applog.ROTATE_RETRY
    monkeypatch.setattr(applog.os, "replace", real)
    applog.write("fourth line")
    assert "third line" in (tmp_path / (base.name + ".1")).read_text(encoding="utf-8")
    assert base.read_text(encoding="utf-8") == "fourth line\n"


def test_no_rotation_when_the_size_is_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(applog.paths, "LOGS", str(tmp_path))
    applog.configure({"log_max_mb": 0, "log_backups": 2})
    for n in range(50):
        applog.write("x" * 100 + str(n))
    assert len(list(tmp_path.glob("*.log*"))) == 1


def test_bad_settings_fall_back_to_the_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(applog.paths, "LOGS", str(tmp_path))
    applog.configure({"log_level": "nonsense", "log_max_mb": "big", "log_backups": None})
    assert applog._level == applog.LEVELS.index(applog.DEFAULT_LEVEL)
    assert applog._max_bytes == applog.DEFAULT_MAX_MB * 1024 * 1024
    assert applog._backups == applog.DEFAULT_BACKUPS
    applog.write("still written")


def test_dialog_respects_the_level_and_returns_the_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(applog.paths, "LOGS", str(tmp_path))
    applog.configure({"log_level": "ERROR"})
    assert applog.dialog("ignored\n") is None
    assert not (tmp_path / "dialogs.log").exists()
    applog.configure({"log_level": "INFO"})
    assert applog.dialog("kept\n") is None
    assert (tmp_path / "dialogs.log").read_text(encoding="utf-8") == "kept\n"
    broken = tmp_path / "broken"
    broken.write_text("not a folder", encoding="utf-8")
    monkeypatch.setattr(applog.paths, "LOGS", str(broken))
    problem = applog.dialog("lost\n")
    assert isinstance(problem, OSError)
    assert applog._warned


def test_a_broken_log_never_raises_and_warns_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    broken = tmp_path / "broken"
    broken.write_text("not a folder", encoding="utf-8")
    monkeypatch.setattr(applog.paths, "LOGS", str(broken))
    applog.write("first")
    applog.write("second")
    assert applog._warned
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        applog.crash("Title", "Hero")          # still no raise; the traceback goes to stderr
    assert not os.path.exists(broken / "errors.log")
