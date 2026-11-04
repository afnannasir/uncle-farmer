"""Farming with a pet, simulated end to end: the real bot (Run, the scan, the farm loop, the skill
book, engine/pets.py) against the pretend game with a pet out, monsters that go for the pet, and
each class's own skills (tests/simgame.py CLASS_SKILLS: the game's skill names, so the skill book
takes the class it is).

What is measured is what the game did (simgame truth): heals that landed on the pet, pet potions
drunk, Rice Rolls fed, the pet's deaths, the pet's attackers we killed. A Sorceress (Witcher) and a
Priest have a quick heal and should keep the pet up with it, potions only as a fallback; a
Warrior has none and lives on pet potions as before."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import pytest

import simgame
from unclefarmer import paths, settings

pytestmark = pytest.mark.slow


def errors_logged() -> str:
    path = os.path.join(paths.LOGS, "errors.log")
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


def farm_with_pet(sim_env: Callable[..., simgame.SimGame], cls: str, minutes: int = 10, hunters: int = 2,
                  seed: int = 7, **profile: Any) -> tuple[simgame.Soak, simgame.SimGame]:
    game = sim_env(seed=seed, monsters=8)
    game.version = 44                     # casts name whom they struck: the pet's attackers are known
    w = game.world
    w.use_class(cls)
    w.with_pet(hp=120000, amiability=60.0, hunters=hunters)
    ranged = cls != "Warrior"
    settings.save_profile(simgame.NAME, dict(
        settings.PROFILE_DEFAULTS, pets=[{"name": simgame.PET_NAME, "use": True}], points_key="t",
        pet_vitals_key="shift+r", pet_food="Pet Rice Roll", attack_range=19.5 if ranged else "auto",
        **profile))
    s = simgame.Soak(game, "end_at here\nfarm minutes=%d" % minutes, watchdog=240).go()
    return s, game


def sound(s: simgame.Soak, game: simgame.SimGame) -> None:
    assert s.reason == "finished", list(s.lines)[-15:]
    assert not s.timed_out and not game.errors and not game.unknown, (game.errors, game.unknown)
    assert errors_logged() == ""


@pytest.mark.parametrize("cls", ["Witcher", "Priest"])
def test_a_healer_keeps_its_pet_up_with_its_heal_not_potions(sim_env: Callable[..., simgame.SimGame], cls: str) -> None:
    s, game = farm_with_pet(sim_env, cls)
    sound(s, game)
    t = game.world.truth
    heals, potions = t.get("pet_heals", 0), t.get("pet_potions", 0)
    assert t.get("pet_bitten", 0) > 20, t                      # the pet really was under attack
    assert heals >= 10, (t, [line for line in s.lines if "heal" in line.lower()][:10])
    assert potions <= heals // 4, t                            # potions only now and then
    assert t.get("pet_deaths", 0) == 0, t
    assert any("heals the pet with" in line for line in s.lines)


def test_a_class_with_no_heal_still_drinks_pet_potions(sim_env: Callable[..., simgame.SimGame]) -> None:
    s, game = farm_with_pet(sim_env, "Warrior")
    sound(s, game)
    t = game.world.truth
    assert t.get("pet_heals", 0) == 0 and t.get("pet_potions", 0) > 0, t


@pytest.mark.parametrize("cls", ["Witcher", "Priest", "Warrior"])
def test_monsters_attacking_the_pet_are_fought(sim_env: Callable[..., simgame.SimGame], cls: str) -> None:
    s, game = farm_with_pet(sim_env, cls, hunters=2)
    sound(s, game)
    t = game.world.truth
    assert t.get("pet_hunters_killed", 0) >= 5, t
    assert t["kills"] >= 10, t                                 # normal farming went on as well


@pytest.mark.parametrize("cls", ["Priest", "Witcher", "Warrior"])
def test_rice_rolls_are_fed_when_the_amiability_is_low(sim_env: Callable[..., simgame.SimGame], cls: str) -> None:
    s, game = farm_with_pet(sim_env, cls, hunters=0)
    sound(s, game)
    t = game.world.truth
    assert t.get("rice_rolls", 0) >= 1, (t, [line for line in s.lines if "amiab" in line][:5])
    assert game.world.pet is not None and game.world.pet["amiability"] >= 70.0


def test_heal_skills_fall_back_to_potions_when_they_cannot_reach_the_pet(sim_env: Callable[..., simgame.SimGame]) -> None:
    # the pet can't be selected (the game refuses it): its HP comes from potions, and the run says why
    game_holder: list[simgame.SimGame] = []

    def make(seed: int = 7, map_id: int = 1503, monsters: int = 12) -> simgame.SimGame:
        g = sim_env(seed=seed, map_id=map_id, monsters=monsters)
        real = g.world.select

        def select(mid: int) -> str:
            if mid == simgame.PET:
                return "refused: %d isn't in a clear line" % mid
            return real(mid)
        g.world.select = select           # type: ignore[method-assign]
        game_holder.append(g)
        return g
    s, game = farm_with_pet(make, "Witcher")
    sound(s, game)
    t = game.world.truth
    assert t.get("pet_heals", 0) == 0 and t.get("pet_potions", 0) > 0, t      # potions instead
    # (two biters outdo what pet potions alone heal in this pretend game: the Warrior's pet, with
    # no heal at all, fares the same - what is checked is that the potions take over)
    assert any("can't heal" in line and "wouldn't select" in line for line in s.lines)
