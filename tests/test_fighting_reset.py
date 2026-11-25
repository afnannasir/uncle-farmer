"""A profile keeps every value it was saved with: an old default stayed in it for good, and a player
whose fights had gone slow deleted the whole profile to get the defaults back. The low HP defaults
are changed once, and Advanced settings can put the fighting settings back to their defaults."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from conftest import returning

from unclefarmer import settings


@pytest.mark.parametrize("below, after, now", [
    (25, 10.0, (30, 3.0)),          # the old defaults: the new ones
    (None, None, (30, 3.0)),        # never saved
    (20, 10.0, (20, 10.0)),         # the player's own number: both kept
    (25, 5.0, (25, 5.0)),
])
def test_the_old_low_hp_defaults_change_once(below: int | None, after: float | None, now: tuple[int, float]) -> None:
    p: dict[str, Any] = {k: v for k, v in (("panic_below", below), ("panic_after", after)) if v is not None}
    settings._upgrade(p)                                              # pyright: ignore[reportPrivateUsage]
    assert (p.get("panic_below"), p.get("panic_after")) == now and p["low_hp_defaults"] is True


def test_low_hp_set_back_to_25_10_after_the_upgrade_stays() -> None:
    p: dict[str, Any] = {"panic_below": 25, "panic_after": 10.0, "low_hp_defaults": True}
    settings._upgrade(p)                                              # pyright: ignore[reportPrivateUsage]
    assert (p["panic_below"], p["panic_after"]) == (25, 10.0)


wx = pytest.importorskip("wx")


@pytest.fixture
def frame() -> Iterator[Any]:
    app = wx.GetApp() or wx.App(False)
    window = wx.Frame(None)
    yield window
    window.Destroy()
    app.ProcessPendingEvents()


def test_advanced_settings_put_the_fighting_settings_back_and_nothing_else(frame: Any,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer.gui import profile as gui
    monkeypatch.setattr(gui, "ask", returning(True))
    monkeypatch.setattr(gui, "info", returning(None))
    p = settings.load_profile("Hero")
    p.update(attack_gap=1.2, press_burst=False, smart_skills=False, fight_timeout=200.0, no_damage_timeout=30,
             attack_range=12.0, chase_range=100, target_mode=0, points_every=8.0, fight_locked=True,
             skills_by_slot=False, skill_use={"Fireball": "never"}, skill_use_others="never",
             boss_distance=25, attack_keys=["1", "2"])
    d = gui.AdvancedDialog(frame, "Hero", p)
    try:
        d.EndModal = returning(None)                                  # type: ignore[method-assign]
        d.on_reset_fighting()
        d.on_ok(wx.CommandEvent())
    finally:
        d.Destroy()
    defaults = settings.PROFILE_DEFAULTS
    for key in ("attack_gap", "press_burst", "smart_skills", "fight_timeout", "no_damage_timeout", "attack_range",
                "chase_range", "target_mode", "points_every", "fight_locked", "skills_by_slot", "skill_use_others"):
        assert p[key] == defaults[key], key
    assert p["skill_use"] == {}
    assert p["boss_distance"] == 25 and p["attack_keys"] == ["1", "2"]            # not fighting tuning: kept
