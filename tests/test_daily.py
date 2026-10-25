"""The farming day: the next drop-time reset, the stop time in force, and when a run may start -
all pure clock math, so the tests pin the timezone and the reset themselves."""

import datetime as dt

import pytest

from unclefarmer import daily

TZ5 = dt.timezone(dt.timedelta(hours=5))


# ---------------------------------------------------------------- reset_local
def test_reset_local_is_the_next_utc_midnight_in_local_time():
    now = dt.datetime(2026, 1, 1, 12, 0, tzinfo=TZ5)
    assert daily.reset_local(now) == dt.datetime(2026, 1, 2, 5, 0)


def test_reset_local_before_the_local_reset_hour():
    now = dt.datetime(2026, 1, 2, 2, 0, tzinfo=TZ5)
    assert daily.reset_local(now) == dt.datetime(2026, 1, 2, 5, 0)


def test_reset_local_after_the_local_reset_hour_rolls_to_tomorrow():
    now = dt.datetime(2026, 1, 2, 6, 0, tzinfo=TZ5)
    assert daily.reset_local(now) == dt.datetime(2026, 1, 3, 5, 0)


def test_reset_local_returns_a_naive_local_datetime():
    now = dt.datetime(2026, 1, 1, 12, 0, tzinfo=TZ5)
    assert daily.reset_local(now).tzinfo is None


def test_reset_clock_formats_as_hh_mm(monkeypatch):
    monkeypatch.setattr(daily, "reset_local", lambda *a, **k: dt.datetime(2026, 1, 2, 5, 7))
    assert daily.reset_clock() == "05:07"


def test_default_clock_is_stop_before_minutes_early(monkeypatch):
    monkeypatch.setattr(daily, "reset_local", lambda *a, **k: dt.datetime(2026, 1, 2, 5, 0))
    assert daily.default_clock() == "04:50"
    assert daily.STOP_BEFORE == 10


def test_default_clock_wraps_over_midnight(monkeypatch):
    monkeypatch.setattr(daily, "reset_local", lambda *a, **k: dt.datetime(2026, 1, 2, 0, 5))
    assert daily.default_clock() == "23:55"


# ---------------------------------------------------------------- valid
@pytest.mark.parametrize("text", ["00:00", "23:59", "05:00", "09:05", " 12:00 "])
def test_valid_accepts(text):
    assert daily.valid(text)


@pytest.mark.parametrize("text", ["", "24:00", "9:05", "12:60", "12:5", "ab:cd", "12-00", None, "1:2", "12:00:00"])
def test_valid_refuses(text):
    assert not daily.valid(text)


# ---------------------------------------------------------------- stop_clock
def test_stop_clock_off():
    assert daily.stop_clock({"daily_stop": False}) is None


def test_stop_clock_uses_a_valid_custom_time():
    assert daily.stop_clock({"daily_stop": True, "daily_stop_at": "12:34"}) == "12:34"


@pytest.mark.parametrize("app", [{}, {"daily_stop": True}, {"daily_stop": True, "daily_stop_at": ""},
                                 {"daily_stop": True, "daily_stop_at": "not a time"}])
def test_stop_clock_falls_back_to_default(monkeypatch, app):
    monkeypatch.setattr(daily, "default_clock", lambda: "04:50")
    assert daily.stop_clock(app) == "04:50"


# ---------------------------------------------------------------- plan
def test_plan_turned_off():
    assert daily.plan({"daily_stop": False}) == (None, None)


def test_plan_before_the_stop_time_is_not_blocked(monkeypatch):
    monkeypatch.setattr(daily, "reset_local", lambda *a, **k: dt.datetime(2026, 1, 1, 5, 0))
    nxt, blocked = daily.plan({"daily_stop_at": "04:50"}, dt.datetime(2026, 1, 1, 3, 0))
    assert nxt == dt.datetime(2026, 1, 1, 4, 50)
    assert blocked is None


def test_plan_just_after_the_stop_time_blocks_until_the_reset(monkeypatch):
    monkeypatch.setattr(daily, "reset_local", lambda *a, **k: dt.datetime(2026, 1, 1, 5, 0))
    nxt, blocked = daily.plan({"daily_stop_at": "04:50"}, dt.datetime(2026, 1, 1, 4, 55))
    assert nxt == dt.datetime(2026, 1, 2, 4, 50)
    assert blocked == "05:00"


def test_plan_after_the_reset_is_allowed_again(monkeypatch):
    monkeypatch.setattr(daily, "reset_local", lambda *a, **k: dt.datetime(2026, 1, 1, 5, 0))
    nxt, blocked = daily.plan({"daily_stop_at": "04:50"}, dt.datetime(2026, 1, 1, 6, 0))
    assert nxt == dt.datetime(2026, 1, 2, 4, 50)
    assert blocked is None


def test_plan_a_stop_time_far_from_the_reset_never_blocks(monkeypatch):
    monkeypatch.setattr(daily, "reset_local", lambda *a, **k: dt.datetime(2026, 1, 2, 5, 0))
    nxt, blocked = daily.plan({"daily_stop_at": "12:00"}, dt.datetime(2026, 1, 1, 12, 5))
    assert nxt == dt.datetime(2026, 1, 2, 12, 0)
    assert blocked is None


def test_plan_the_default_stop_time_blocks_just_before_the_reset(monkeypatch):
    monkeypatch.setattr(daily, "reset_local", lambda *a, **k: dt.datetime(2026, 1, 1, 5, 0))
    nxt, blocked = daily.plan({}, dt.datetime(2026, 1, 1, 4, 55))
    assert nxt == dt.datetime(2026, 1, 2, 4, 50)
    assert blocked == "05:00"
