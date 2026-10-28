# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The once-a-day caves (engine/dungeon.py) against a pretend game shaped like the logged hand runs
of 2026-10-27: the entrance NPC's menu offers "Enter the dungeon", choosing it lands on the cave's
map, and after the 15 minutes the game moves the character out by itself."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from unclefarmer.engine import chores, dungeon, pets, script, town
from unclefarmer.engine.bot import Stopped
from conftest import as_bot, as_kind, stand_in
from collections.abc import Callable


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def time(self) -> float:
        return self.now


class Agent:
    """The agent's pet list: one pet out (usable_at -1), or none."""

    def __init__(self, pet_out: bool) -> None:
        self.pet_out = pet_out

    def json(self, line: str) -> Any:           # an agent answer: any JSON, as the real link
        if line == "mem pets":
            return [{"id": 77, "name": "Wild Eagle>><< Sparky", "usable_at": -1}] if self.pet_out else []
        return {"error": "unknown"}


class Game:
    """What a dungeon run uses of the Bot."""

    def __init__(self, level: int=80, bag: dict[str, int] | None=None, pets: bool=True, menu: bool=True, lets_in: bool=True, moves_out: bool=True, pet_out: bool=False) -> None:
        self.clock = Clock()
        self.a = Agent(pet_out)
        self.p: dict[str, Any] = {"mp_use": True, "pets": [{"name": "Pixie Sparky", "use": pets}]}
        self.character = "Hero"
        self.stop_event = threading.Event()
        self.level = level
        self.bag: dict[str, int] = dict(bag or {})
        self.menu, self.lets_in, self.moves_out = menu, lets_in, moves_out
        self.mid = 1531
        self.kills = 0
        self.lines: list[str] = []
        self.walked: list[str] = []
        self.farmed: list[dict[str, Any]] = []
        self.restocks: list[list[dict[str, Any]]] = []
        self.left = 0
        self.slept: list[float] = []
        self.move_shots = False

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock.now += seconds

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def refresh_sheet(self, force: bool=False) -> dict[str, Any] | None:
        return {"level": self.level}

    def me(self) -> dict[str, Any]:
        return {"level": self.level}

    def potion(self, kind: str) -> tuple[str | None, str | None]:
        return {"mp": ("2", "Large Blue Potion"), "hp": ("1", "Large Red Potion"),
                "pet_mp": ("5", "Pet Large Blue Potion"), "pet_hp": (None, None)}.get(kind, (None, None))

    def map_id(self) -> int:
        return self.mid

    def go_dungeon(self, entry: dict[str, Any], talk: bool=False) -> None:
        self.walked.append(entry["name"])
        self.cave = entry["name"]

    def wait_dialog(self, seconds: float) -> dict[str, Any] | None:
        return {"open": 1}

    def choose(self, pattern: str) -> dict[str, Any] | None:
        return {"text": pattern} if self.menu else None

    def close_dialogs(self) -> None:
        pass

    def wait_map_change(self, mid: int, seconds: float) -> bool:
        if self.lets_in:
            self.mid = dungeon.CAVES[self.cave]["map"]
        return self.lets_in

    def farm(self, map_id: int, until: Callable[[], bool] | None=None, **kw: Any) -> None:
        self.farmed.append(dict(kw, map=map_id))
        self.shot_moving = self.move_shots
        for _i in range(100):
            self.clock.now += 10
            self.kills += 1
            if self.moves_out and self.clock.now - self.entered_at() >= dungeon.MINUTES * 60:
                self.mid = 1531
            if until and until():
                return

    def entered_at(self) -> float:
        return self._entered if hasattr(self, "_entered") else self._enter()

    def _enter(self) -> float:
        self._entered = self.clock.now
        return self._entered

    def leave_instance(self) -> bool:
        self.left += 1
        self.mid = 1531
        return True


@pytest.fixture
def shop(monkeypatch: pytest.MonkeyPatch) -> None:
    """town.restock with a storehouse that has whatever is asked for."""
    def restock(b: Game, rules: list[dict[str, Any]], start: bool = False) -> bool:
        b.restocks.append(rules)
        for r in rules:
            b.bag[r["item"].lower()] = max(b.bag.get(r["item"].lower(), 0), r["stock"])
        return True
    monkeypatch.setattr(town, "counts", stand_in(town.counts, lambda b: dict(as_kind(Game, b).bag)))
    monkeypatch.setattr(town, "restock", restock)


def test_settings_fill_in_and_keep_the_players_supplies() -> None:
    cfg = dungeon.config({"dungeon": {"caves": ["Spider cave"], "supplies": {"pet_mp": {"min": 20}}}})
    assert cfg["caves"] == ["Spider cave"]
    assert cfg["supplies"]["pet_mp"] == {"item": "", "min": 20}
    assert cfg["supplies"]["pet_food"] == {"item": "", "min": 3}
    assert cfg["patrol"] == "quick" and cfg["pack"] == 2 and cfg["end"] == {"how": "start"}


def test_the_supplies_are_the_potions_the_character_drinks_and_the_pet_food() -> None:
    rules, unknown = dungeon.supply_rules(as_bot(Game()), dungeon.config({}))
    assert [(r["item"], r["min"]) for r in rules] == [
        ("Pet Large Blue Potion", 25), ("Pet Rice Roll", 3), ("Large Blue Potion", 50), ("Large Red Potion", 30)]
    assert unknown == []


def test_no_pet_ticked_no_pet_supplies_and_a_named_item_wins() -> None:
    g = Game(pets=False)
    cfg = dungeon.config({"dungeon": {"supplies": {"mp": {"item": "Super Blue Potion", "min": 10}}}})
    rules, _u = dungeon.supply_rules(as_bot(g), cfg)
    assert [r["item"] for r in rules] == ["Super Blue Potion", "Large Red Potion"]


def test_a_character_without_mp_and_a_potion_it_has_none_of() -> None:
    g = Game()
    g.p["mp_use"] = False
    g.potion = lambda kind: (None, None)
    rules, unknown = dungeon.supply_rules(as_bot(g), dungeon.config({}))
    assert [r["item"] for r in rules] == ["Pet Rice Roll"]
    assert len(unknown) == 2                          # the pet's blues and our reds: nothing to go by


def test_short_supplies_are_topped_up_before_setting_out(shop: None) -> None:
    g = Game(bag={"pet large blue potion": 5, "pet rice roll": 3, "large blue potion": 80, "large red potion": 40})
    assert dungeon.get_supplies(as_bot(g), dungeon.config({})) == []
    assert len(g.restocks) == 1
    assert g.bag["pet large blue potion"] == 25


def test_still_short_stops_unless_the_player_said_go(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "counts", stand_in(town.counts, lambda b: dict(as_kind(Game, b).bag)))
    monkeypatch.setattr(town, "restock", stand_in(town.restock, lambda b, rules, start=False: True))    # the shop had none
    g = Game(bag={"pet rice roll": 3, "large blue potion": 80, "large red potion": 40})
    with pytest.raises(Stopped):
        dungeon.get_supplies(as_bot(g), dungeon.config({}))
    short = dungeon.get_supplies(as_bot(g), dungeon.config({"dungeon": {"when_short": "go"}}))
    assert short == ["Pet Large Blue Potion 0/25"]


def test_check_only_never_restocks(shop: None) -> None:
    g = Game()
    short = dungeon.get_supplies(as_bot(g), dungeon.config({}), check_only=True)
    assert g.restocks == [] and len(short) == 4


def test_a_visit_goes_in_fights_till_moved_out_and_is_done_for_the_day(shop: None) -> None:
    g = Game(bag={"pet large blue potion": 30, "pet rice roll": 5, "large blue potion": 80, "large red potion": 40})
    g._enter()
    notes = dungeon.run(as_bot(g), dungeon.config({"dungeon": {"caves": ["Scorpion Cave"]}}))
    assert g.walked == ["Scorpion Cave"]
    assert g.farmed == [{"map": 1539, "patrol": "quick", "instance": True, "pack": 2, "chase": 90}]
    assert g.mid == 1531 and g.left == 0
    assert g.slept == [dungeon.SETTLE]          # the game's move out finishes before the end place
    assert vars(g)["shot_moving"] is True and g.move_shots is False    # inside only
    assert notes and "Scorpion Cave" in notes[0] and "kills" in notes[0]
    assert dungeon.done_today("Hero") == ["Scorpion Cave"]
    assert dungeon.run(as_bot(g), dungeon.config({"dungeon": {"caves": ["Scorpion Cave"]}})) == []
    assert g.walked == ["Scorpion Cave"]                      # not again today


def test_still_inside_after_the_timer_it_leaves_itself(shop: None) -> None:
    g = Game(moves_out=False, bag={"pet large blue potion": 30, "pet rice roll": 5,
                                   "large blue potion": 80, "large red potion": 40})
    g._enter()
    dungeon.run(as_bot(g), dungeon.config({"dungeon": {"caves": ["Spider cave"]}}))
    assert g.left == 1 and g.mid == 1531


def test_not_let_in_is_written_down_and_the_next_cave_is_tried(shop: None) -> None:
    g = Game(lets_in=False, bag={"pet large blue potion": 30, "pet rice roll": 5,
                                 "large blue potion": 80, "large red potion": 40})
    notes = dungeon.run(as_bot(g), dungeon.config({}))
    assert g.walked == ["Scorpion Cave", "Spider cave"]
    assert g.farmed == []
    assert all("didn't let us in" in n for n in notes)
    assert chores.done_today("Hero")["_dungeon Scorpion Cave"]["note"] == "not let in"


def test_a_menu_without_enter_is_said(shop: None) -> None:
    g = Game(menu=False)
    assert dungeon.enter(as_bot(g), "Spider cave") is False
    assert any("Enter the dungeon" in line for line in g.lines)


def test_a_cave_above_the_characters_level_is_left_out(shop: None) -> None:
    g = Game(level=50, bag={"pet large blue potion": 30, "pet rice roll": 5,
                            "large blue potion": 80, "large red potion": 40})
    g._enter()
    dungeon.run(as_bot(g), dungeon.config({}))
    assert g.walked == ["Spider cave"]
    assert any("needs level 60" in line for line in g.lines)


def test_check_only_says_where_it_would_go_and_goes_nowhere(shop: None) -> None:
    g = Game()
    assert dungeon.run(as_bot(g), dungeon.config({}), check_only=True) == ["would go to: Scorpion Cave, Spider cave"]
    assert g.walked == [] and g.restocks == []


def test_the_window_runs_its_end_place_then_the_caves() -> None:
    assert dungeon.script(dungeon.config({})) == "end_at start\ndungeon\n"
    cfg = dungeon.config({"dungeon": {"end": {"how": "place", "map": 1531, "cell": [160, 180]}}})
    text = dungeon.script(cfg)
    assert text.startswith("end_at 1531 at 160,180\n")
    assert script.check(text) is None


def test_the_script_command_parses() -> None:
    for line in ("dungeon", "dungeon check"):
        assert not script.check(line + "\n")


def test_every_cave_has_a_known_entrance_and_a_patrol() -> None:
    from unclefarmer import atlas, dungeons
    for cave, v in dungeon.CAVES.items():
        e = dungeons.find(cave)
        assert e is not None and v["map"] in e["dungeon_maps"]
        assert atlas.patrol_loop(v["map"], "quick")
    assert dungeon.cave_of(1503) == "Spider cave" and dungeon.cave_of(1) is None


def test_the_supply_choices_are_the_games_items() -> None:
    assert "Pet Large Blue Potion" in dungeon.known_items("pet_mp")
    assert dungeon.known_items("pet_food") == ["Pet Rice Roll"]
    assert "Large Red Potion" in dungeon.known_items("hp")
    assert not [n for n in dungeon.known_items("mp") if "Pet" in n]


def test_with_no_pet_ticked_the_pet_out_is_looked_after(shop: None) -> None:
    g = Game(pets=False, pet_out=True)
    dungeon.run(as_bot(g), dungeon.config({}), check_only=True)
    assert vars(g)["run_pets"] == ["Wild Eagle>><< Sparky"]
    rules, _u = dungeon.supply_rules(as_bot(g), dungeon.config({}))
    assert "Pet Large Blue Potion" in [r["item"] for r in rules]
    assert g.p["pets"] == [{"name": "Pixie Sparky", "use": False}]     # nothing saved in the profile


class PetGame(Game):
    """Notes which pets the run looks after while it farms inside."""

    def __init__(self, character: str) -> None:
        super().__init__(pets=True, pet_out=True)
        self.character = character        # its own done list: other tests' visits don't count
        self.inside: list[list[str]] = []
        self._enter()

    def farm(self, map_id: int, until: Callable[[], bool] | None=None, **kw: Any) -> None:
        self.inside.append(pets.chosen(as_bot(self)))
        super().farm(map_id, until, **kw)


def test_the_pet_chosen_for_the_dungeons_goes_over_the_ticked_ones(shop: None) -> None:
    g = PetGame("Chosen pet")
    dungeon.run(as_bot(g), dungeon.config({"dungeon": {"caves": ["Scorpion Cave"], "pet": "Wild Eagle>><< Sparky"}}))
    assert g.inside and g.inside[0] == ["Wild Eagle>><< Sparky"]
    assert any("taking Wild Eagle>><< Sparky into the dungeons" in line for line in g.lines)
    assert pets.chosen(as_bot(g)) == ["Pixie Sparky"]     # afterwards: Character, Settings again
    assert g.p["pets"] == [{"name": "Pixie Sparky", "use": True}]     # nothing saved in the profile


def test_no_pet_chosen_for_the_dungeons_takes_none_and_no_pet_supplies(shop: None) -> None:
    g = PetGame("No pet")
    cfg = dungeon.config({"dungeon": {"caves": ["Scorpion Cave"], "pet": dungeon.NO_PET}})
    dungeon.run(as_bot(g), cfg)
    assert g.inside and g.inside[0] == []
    assert any("without a pet" in line for line in g.lines)
    assert "run_pets" not in vars(g)                      # the pet out isn't adopted either
    shopped = [r["item"] for rules in g.restocks for r in rules]
    assert shopped and not [i for i in shopped if i.startswith("Pet ")]


def test_no_pet_at_all_is_said(shop: None) -> None:
    g = Game(pets=False)
    dungeon.run(as_bot(g), dungeon.config({}), check_only=True)
    assert any("going without one" in line for line in g.lines)
