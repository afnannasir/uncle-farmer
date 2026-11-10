"""The Results window, built for real (skipped where wx can't make a window) but never shown: the
periods Today, Yesterday and a chosen day each add up only that day's runs, the Day list holds
every day with runs, newest first, and the longer periods are still there."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

wx = pytest.importorskip("wx")

from unclefarmer import stats  # noqa: E402
from unclefarmer.gui import results  # noqa: E402


@pytest.fixture(scope="module")
def frame() -> Iterator[Any]:
    app = wx.App.Get() or wx.App(False)
    try:
        top = wx.Frame(None)
    except Exception as e:                      # no desktop to make windows on
        pytest.skip("no windows here: %s" % e)
    yield top
    top.Destroy()
    del app


def noon(days_back: int) -> str:
    """Noon of a day this many days back, as a record's start (noon: a clock change can't move it
    to another day)."""
    t = time.localtime(time.time() - days_back * 86400)
    return time.strftime("%Y-%m-%d 12:00:00", t)


@pytest.fixture
def history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path))
    kills = {0: 5, 1: 7, 2: 11, 40: 13}           # days back -> kills that day
    lines = ['{"kind": "farming", "start": "%s", "minutes": 60, "kills": %d}' % (noon(d), k) for d, k in kills.items()]
    (tmp_path / "Hero.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def dialog(frame: Any) -> Any:
    return results.ResultsDialog(frame, "Hero", lambda name: None)


def pick(dlg: Any, period: str) -> None:
    dlg.when.SetSelection(results.PERIODS.index(period))
    dlg.fill_days()


def kills_line(dlg: Any) -> str:
    text, _spoken = dlg.result()
    assert text is not None
    return next(line for line in text.splitlines() if line.startswith("Time used:"))


@pytest.mark.usefixtures("history")
def test_each_day_adds_up_only_its_own_runs_and_the_long_periods_stay(frame: Any) -> None:
    dlg = dialog(frame)
    try:
        pick(dlg, "today")
        assert "5 kills" in kills_line(dlg)
        pick(dlg, "yesterday")
        assert "7 kills" in kills_line(dlg)
        assert not dlg.day.IsEnabled()                      # the Day list is for "A day of my choice"
        pick(dlg, "day")
        assert dlg.day.IsEnabled()
        assert [d[:10] for d in dlg.days] == [noon(d)[:10] for d in (0, 1, 2, 40)]   # newest first
        assert dlg.day.GetString(2).endswith("(2 days ago)")
        dlg.day.SetSelection(2)
        assert "11 kills" in kills_line(dlg)
        pick(dlg, "week")
        assert "23 kills" in kills_line(dlg)
        pick(dlg, "all")
        assert "36 kills" in kills_line(dlg)
    finally:
        dlg.Destroy()


def test_a_day_reads_as_its_date_weekday_and_how_long_ago() -> None:
    assert results.day_label("2026-11-08", "2026-11-10") == "2026-11-08, Sunday (2 days ago)"
    assert results.day_label("2026-11-09", "2026-11-10") == "2026-11-09, Monday (yesterday)"
    assert results.day_label("2026-11-10", "2026-11-10") == "2026-11-10, Tuesday (today)"
