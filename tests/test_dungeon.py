"""The once-a-day caves (engine/dungeon.py) against a pretend game shaped like the logged hand runs
of 2026-10-27: the entrance NPC's menu offers "Enter the dungeon", choosing it lands on the cave's
map, and after the 15 minutes the game moves the character out by itself."""

import threading
from typing import Any

import pytest

from unclefarmer.engine import chores, dungeon, script, town
from unclefarmer.engine.bot import Stopped


class Clock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def time(self):
        return self.now


class Agent:
    """The agent's pet list: one pet out (usable_at -1), or none."""

    def __init__(self, pet_out):
        self.pet_out = pet_out

    def json(self, line):
        if line == "mem pets":
            return [{"id": 77, "name": "Wild Eagle>><< Sparky", "usable_at": -1}] if self.pet_out else []
        return {"error": "unknown"}


class Game:
    """What a dungeon run uses of the Bot."""

    def __init__(self, level=80, bag=None, pets=True, menu=True, lets_in=True, moves_out=True, pet_out=False):
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

    def log(self, line, speak=False):
        self.lines.append(line)

    def refresh_sheet(self, force=False):
        return {"level": self.level}

    def me(self):
        return {"level": self.level}

    def potion(self, kind):
        return {"mp": ("2", "Large Blue Potion"), "hp": ("1", "Large Red Potion"),
                "pet_mp": ("5", "Pet Large Blue Potion"), "pet_hp": (None, None)}.get(kind, (None, None))

    def map_id(self):
        return self.mid

    def go_dungeon(self, entry, talk=False):
        self.walked.append(entry["name"])
        self.cave = entry["name"]

    def wait_dialog(self, seconds):
        return {"open": 1}

    def choose(self, pattern):
        return 0 if self.menu else None

    def close_dialogs(self):
        pass

    def wait_map_change(self, mid, seconds):
        if self.lets_in:
            self.mid = dungeon.CAVES[self.cave]["map"]
        return self.lets_in

    def farm(self, map_id, until=None, **kw):
        self.farmed.append(dict(kw, map=map_id))
        for _i in range(100):
            self.clock.now += 10
            self.kills += 1
            if self.moves_out and self.clock.now - self.entered_at() >= dungeon.MINUTES * 60:
                self.mid = 1531
            if until and until():
                return

    def entered_at(self):
        return self._entered if hasattr(self, "_entered") else self._enter()

    def _enter(self):
        self._entered = self.clock.now
        return self._entered

    def leave_instance(self):
        self.left += 1
        self.mid = 1531
        return True


@pytest.fixture
def shop(monkeypatch):
    """town.restock with a storehouse that has whatever is asked for."""
    def restock(b, rules, start=False):
        b.restocks.append(rules)
        for r in rules:
            b.bag[r["item"].lower()] = max(b.bag.get(r["item"].lower(), 0), r["stock"])
        return True
    monkeypatch.setattr(town, "counts", lambda b: dict(b.bag))
    monkeypatch.setattr(town, "restock", restock)


def test_settings_fill_in_and_keep_the_players_supplies():
    cfg = dungeon.config({"dungeon": {"caves": ["Spider cave"], "supplies": {"pet_mp": {"min": 20}}}})
    assert cfg["caves"] == ["Spider cave"]
    assert cfg["supplies"]["pet_mp"] == {"item": "", "min": 20}
    assert cfg["supplies"]["pet_food"] == {"item": "", "min": 3}
    assert cfg["patrol"] == "quick" and cfg["pack"] == 2 and cfg["end"] == {"how": "start"}


def test_the_supplies_are_the_potions_the_character_drinks_and_the_pet_food():
    rules, unknown = dungeon.supply_rules(Game(), dungeon.config({}))
    assert [(r["item"], r["min"]) for r in rules] == [
        ("Pet Large Blue Potion", 25), ("Pet Rice Roll", 3), ("Large Blue Potion", 50), ("Large Red Potion", 30)]
    assert unknown == []


def test_no_pet_ticked_no_pet_supplies_and_a_named_item_wins():
    g = Game(pets=False)
    cfg = dungeon.config({"dungeon": {"supplies": {"mp": {"item": "Super Blue Potion", "min": 10}}}})
    rules, _u = dungeon.supply_rules(g, cfg)
    assert [r["item"] for r in rules] == ["Super Blue Potion", "Large Red Potion"]


def test_a_character_without_mp_and_a_potion_it_has_none_of():
    g = Game()
    g.p["mp_use"] = False
    g.potion = lambda kind: (None, None)
    rules, unknown = dungeon.supply_rules(g, dungeon.config({}))
    assert [r["item"] for r in rules] == ["Pet Rice Roll"]
    assert len(unknown) == 2                          # the pet's blues and our reds: nothing to go by


def test_short_supplies_are_topped_up_before_setting_out(shop):
    g = Game(bag={"pet large blue potion": 5, "pet rice roll": 3, "large blue potion": 80, "large red potion": 40})
    assert dungeon.get_supplies(g, dungeon.config({})) == []
    assert len(g.restocks) == 1
    assert g.bag["pet large blue potion"] == 25


def test_still_short_stops_unless_the_player_said_go(monkeypatch):
    monkeypatch.setattr(town, "counts", lambda b: dict(b.bag))
    monkeypatch.setattr(town, "restock", lambda b, rules, start=False: True)    # the shop had none
    g = Game(bag={"pet rice roll": 3, "large blue potion": 80, "large red potion": 40})
    with pytest.raises(Stopped):
        dungeon.get_supplies(g, dungeon.config({}))
    short = dungeon.get_supplies(g, dungeon.config({"dungeon": {"when_short": "go"}}))
    assert short == ["Pet Large Blue Potion 0/25"]


def test_check_only_never_restocks(shop):
    g = Game()
    short = dungeon.get_supplies(g, dungeon.config({}), check_only=True)
    assert g.restocks == [] and len(short) == 4


def test_a_visit_goes_in_fights_till_moved_out_and_is_done_for_the_day(shop):
    g = Game(bag={"pet large blue potion": 30, "pet rice roll": 5, "large blue potion": 80, "large red potion": 40})
    g._enter()
    notes = dungeon.run(g, dungeon.config({"dungeon": {"caves": ["Scorpion Cave"]}}))
    assert g.walked == ["Scorpion Cave"]
    assert g.farmed == [{"map": 1539, "patrol": "quick", "instance": True, "pack": 2, "chase": 90}]
    assert g.mid == 1531 and g.left == 0
    assert notes and "Scorpion Cave" in notes[0] and "kills" in notes[0]
    assert dungeon.done_today("Hero") == ["Scorpion Cave"]
    assert dungeon.run(g, dungeon.config({"dungeon": {"caves": ["Scorpion Cave"]}})) == []
    assert g.walked == ["Scorpion Cave"]                      # not again today


def test_still_inside_after_the_timer_it_leaves_itself(shop):
    g = Game(moves_out=False, bag={"pet large blue potion": 30, "pet rice roll": 5,
                                   "large blue potion": 80, "large red potion": 40})
    g._enter()
    dungeon.run(g, dungeon.config({"dungeon": {"caves": ["Spider cave"]}}))
    assert g.left == 1 and g.mid == 1531


def test_not_let_in_is_written_down_and_the_next_cave_is_tried(shop):
    g = Game(lets_in=False, bag={"pet large blue potion": 30, "pet rice roll": 5,
                                 "large blue potion": 80, "large red potion": 40})
    notes = dungeon.run(g, dungeon.config({}))
    assert g.walked == ["Scorpion Cave", "Spider cave"]
    assert g.farmed == []
    assert all("didn't let us in" in n for n in notes)
    assert chores.done_today("Hero")["_dungeon Scorpion Cave"]["note"] == "not let in"


def test_a_menu_without_enter_is_said(shop):
    g = Game(menu=False)
    assert dungeon.enter(g, "Spider cave") is False
    assert any("Enter the dungeon" in line for line in g.lines)


def test_a_cave_above_the_characters_level_is_left_out(shop):
    g = Game(level=50, bag={"pet large blue potion": 30, "pet rice roll": 5,
                            "large blue potion": 80, "large red potion": 40})
    g._enter()
    dungeon.run(g, dungeon.config({}))
    assert g.walked == ["Spider cave"]
    assert any("needs level 60" in line for line in g.lines)


def test_check_only_says_where_it_would_go_and_goes_nowhere(shop):
    g = Game()
    assert dungeon.run(g, dungeon.config({}), check_only=True) == ["would go to: Scorpion Cave, Spider cave"]
    assert g.walked == [] and g.restocks == []


def test_the_window_runs_its_end_place_then_the_caves():
    assert dungeon.script(dungeon.config({})) == "end_at start\ndungeon\n"
    cfg = dungeon.config({"dungeon": {"end": {"how": "place", "map": 1531, "cell": [160, 180]}}})
    text = dungeon.script(cfg)
    assert text.startswith("end_at 1531 at 160,180\n")
    assert script.check(text) is None


def test_the_script_command_parses():
    for line in ("dungeon", "dungeon check"):
        assert not script.check(line + "\n")


def test_every_cave_has_a_known_entrance_and_a_patrol():
    from unclefarmer import atlas, dungeons
    for cave, v in dungeon.CAVES.items():
        e = dungeons.find(cave)
        assert e is not None and v["map"] in e["dungeon_maps"]
        assert atlas.patrol_loop(v["map"], "quick")
    assert dungeon.cave_of(1503) == "Spider cave" and dungeon.cave_of(1) is None


def test_the_supply_choices_are_the_games_items():
    assert "Pet Large Blue Potion" in dungeon.known_items("pet_mp")
    assert dungeon.known_items("pet_food") == ["Pet Rice Roll"]
    assert "Large Red Potion" in dungeon.known_items("hp")
    assert not [n for n in dungeon.known_items("mp") if "Pet" in n]


def test_with_no_pet_ticked_the_pet_out_is_looked_after(shop):
    g = Game(pets=False, pet_out=True)
    dungeon.run(g, dungeon.config({}), check_only=True)
    assert vars(g)["run_pets"] == ["Wild Eagle>><< Sparky"]
    rules, _u = dungeon.supply_rules(g, dungeon.config({}))
    assert "Pet Large Blue Potion" in [r["item"] for r in rules]
    assert g.p["pets"] == [{"name": "Pixie Sparky", "use": False}]     # nothing saved in the profile


def test_no_pet_at_all_is_said(shop):
    g = Game(pets=False)
    dungeon.run(g, dungeon.config({}), check_only=True)
    assert any("going without one" in line for line in g.lines)
