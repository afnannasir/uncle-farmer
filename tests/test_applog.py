# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The log files: the level, the day's file name, rotation, and the best-effort promises."""

from __future__ import annotations

import os
import time

import pytest

from unclefarmer import applog
from pathlib import Path
from collections.abc import Iterator


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(applog, "_warned", False)
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
