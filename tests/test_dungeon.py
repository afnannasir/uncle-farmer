# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The once-a-day caves (engine/dungeon.py) against a pretend game shaped like the logged hand runs
of 2026-10-27: the entrance NPC's menu offers "Enter the dungeon", choosing it lands on the cave's
map, and after the 15 minutes the game moves the character out by itself."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from unclefarmer.engine import beasts, chores, dungeon, pets, script, town
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
        self.pets: list[dict[str, Any]] | None = None      # a test's own pet list
        self.reports: list[dict[str, Any]] = []             # `report` answers, the last one kept

    def json(self, line: str) -> Any:           # an agent answer: any JSON, as the real link
        if line == "mem pets":
            if self.pets is not None:
                return [dict(r) for r in self.pets]
            return [{"id": 77, "name": "Wild Eagle>><< Sparky", "usable_at": -1}] if self.pet_out else []
        if line == "report" and self.reports:
            return self.reports.pop(0) if len(self.reports) > 1 else self.reports[0]
        return {"error": "unknown"}


class Game:
    """What a dungeon run uses of the Bot."""

    def __init__(self, level: int=80, bag: dict[str, int] | None=None, pets: bool=True, menu: bool=True, lets_in: bool=True, moves_out: bool=True, pet_out: bool=False) -> None:
        self.clock = Clock()
        self.a = Agent(pet_out)
        self.p: dict[str, Any] = {"mp_use": True, "pets": [{"name": "Pixie Sparky", "use": pets}],
                                  "smart_skills": False, "use_key": "enter"}
        self.skillbook = None
        self.in_fight = False
        self.pets_refused: dict[int, float] = {}
        self.pet_checked = 0.0
        self.did: list[str] = []          # what was done, in order: at the door, then inside
        self.keys: list[str] = []
        self.cursor = 0
        self.takes_summons = True
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
        self.goal: int | None = None          # what level_goal answers ("Stop before the next level")
        self.xp_needed = -1                   # the sheet's "upgrading requires N experience" (-1: none)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock.now += seconds

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def level_goal(self) -> int | None:
        return self.goal

    def refresh_sheet(self, force: bool=False) -> dict[str, Any] | None:
        return {"level": self.level, "xp_needed": self.xp_needed}

    def me(self) -> dict[str, Any]:
        return {"level": self.level}

    def potion(self, kind: str) -> tuple[str | None, str | None]:
        return {"mp": ("2", "Large Blue Potion"), "hp": ("1", "Large Red Potion"),
                "pet_mp": ("5", "Pet Large Blue Potion"), "pet_hp": (None, None)}.get(kind, (None, None))

    def map_id(self) -> int:
        return self.mid

    def go_dungeon(self, entry: dict[str, Any], talk: bool=False) -> None:
        assert not talk                   # the door's menu is opened only once we are ready
        self.walked.append(entry["name"])
        self.cave = entry["name"]
        self.did.append("arrived")

    def snap(self) -> dict[str, Any]:
        return {"self": {}, "entities": [{"id": -3, "name": self.cave, "x": 10.0, "y": 10.0}]}

    def talk(self, name: str, at: tuple[float, float] | None=None) -> bool:
        self.did.append("talk")
        self.talked: tuple[str, tuple[float, float] | None] = (name, at)
        return True

    def look_after(self, me: dict[str, Any] | None=None) -> dict[str, Any]:
        self.did.append("look")
        return {}

    def attackers(self) -> set[int]:
        return set()

    def goto_panel(self, panel: int) -> dict[str, Any] | None:
        return None

    def panel_cursor(self, panel: int, index: int, size: int) -> bool:
        self.cursor = index
        return True

    def key(self, k: str, ms: int | None=None) -> None:
        """The use key on the pet list: the pet under the cursor comes out, if the game takes it."""
        self.keys.append(k)
        rows = self.a.pets
        if rows is not None and self.takes_summons:
            rows[self.cursor]["usable_at"] = -1
            self.did.append("pet out")

    def wait_dialog(self, seconds: float) -> dict[str, Any] | None:
        return {"open": 1}

    def choose(self, pattern: str) -> dict[str, Any] | None:
        self.did.append("choose")
        if self.menu and self.lets_in:
            self.mid = dungeon.CAVES[self.cave]["map"]      # the game moves us in
        return {"text": pattern} if self.menu else None

    def settled_map(self, seconds: float=15) -> int:
        self.did.append("landed")
        return self.mid

    def close_dialogs(self) -> None:
        pass

    def farm(self, map_id: int, until: Callable[[], bool] | None=None, **kw: Any) -> None:
        self.did.append("farm")
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
    cfg = dungeon.config({"dungeon": {"cave": "Spider cave", "supplies": {"pet_mp": {"min": 20}}}})
    assert cfg["cave"] == "Spider cave"
    assert cfg["supplies"]["pet_mp"] == {"item": "", "min": 20}
    assert cfg["supplies"]["pet_food"] == {"item": "", "min": 3}
    assert cfg["patrol"] == "quick" and cfg["pack"] == 2 and cfg["end"] == {"how": "start"}


def test_the_old_ticked_list_becomes_its_first_cave_and_nonsense_the_best_one() -> None:
    assert dungeon.config({"dungeon": {"caves": ["Spider cave", "Scorpion Cave"]}})["cave"] == "Spider cave"
    assert "caves" not in dungeon.config({"dungeon": {"cave": "Spider cave"}})
    assert dungeon.config({"dungeon": {"caves": []}})["cave"] == dungeon.BEST
    assert dungeon.config({"dungeon": {"cave": "Atlantis"}})["cave"] == dungeon.BEST
    assert dungeon.config({})["cave"] == dungeon.BEST


def test_best_is_the_highest_cave_the_level_lets_in() -> None:
    best = dungeon.config({})
    assert dungeon.pick(best, 79) == "Scorpion Cave"
    assert dungeon.pick(best, 80) == "Bottomless pit"
    assert dungeon.pick(best, 95) == "White Bone Cave"
    assert dungeon.pick(best, 59) == "Spider cave"
    assert dungeon.pick(best, 30) is None
    assert dungeon.pick(best, 0) == "White Bone Cave"         # level unknown: the highest
    assert dungeon.pick(dungeon.config({"dungeon": {"cave": "Spider cave"}}), 79) == "Spider cave"


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
    notes = dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Scorpion Cave"}}))
    assert g.walked == ["Scorpion Cave"]
    assert g.farmed == [{"map": 1539, "patrol": "quick", "instance": True, "pack": 2, "chase": 90}]
    assert g.mid == 1531 and g.left == 0
    assert g.slept[-1] == dungeon.SETTLE        # the game's move out finishes before the end place
    assert g.slept[:-1] == [dungeon.DOOR_LOOK] * dungeon.DOOR_LOOKS     # all else: the looks at the door
    assert g.did[-3:] == ["choose", "landed", "farm"]                   # no settling second inside
    assert vars(g)["shot_moving"] is True and g.move_shots is False    # inside only
    assert notes and "Scorpion Cave" in notes[0] and "kills" in notes[0]
    assert dungeon.done_today("Hero") == ["Scorpion Cave"]
    assert dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Scorpion Cave"}})) == []
    assert g.walked == ["Scorpion Cave"]                      # not again today


def test_the_visit_line_carries_the_games_own_report(shop: None) -> None:
    g = Game(bag={"pet large blue potion": 30, "pet rice roll": 5, "large blue potion": 80, "large red potion": 40})
    g._enter()
    old = {"at": 5, "total_kills": 9, "players": [{"id": 1, "kills": 9, "damage": 10}]}
    new = {"at": 9, "total_kills": 58, "players": [{"id": 2, "kills": 24, "damage": 7965187},
                                                   {"id": 1, "kills": 34, "damage": 12286925, "healing": 0}]}
    g.me = lambda: {"level": 80, "id": 1}  # type: ignore[method-assign]
    g.a.reports = [old, new]
    notes = dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Scorpion Cave"}}))
    assert notes and notes[0].endswith("the game's report: 34 of 58 kills, 12,286,925 damage"), notes


def test_an_old_report_or_none_leaves_the_line_alone(shop: None) -> None:
    g = Game(bag={"pet large blue potion": 30, "pet rice roll": 5, "large blue potion": 80, "large red potion": 40})
    g._enter()
    g.a.reports = [{"at": 5, "total_kills": 9, "players": [{"id": 1, "kills": 9}]}]    # the same one before and after
    notes = dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Scorpion Cave"}}))
    assert notes and "report" not in notes[0]


def test_still_inside_after_the_timer_it_leaves_itself(shop: None) -> None:
    g = Game(moves_out=False, bag={"pet large blue potion": 30, "pet rice roll": 5,
                                   "large blue potion": 80, "large red potion": 40})
    g._enter()
    dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Spider cave"}}))
    assert g.left == 1 and g.mid == 1531


def test_turned_away_is_written_down_and_no_other_cave_is_tried(shop: None) -> None:
    # the game lets a character into one cave a day: turned away from one, it has had its cave
    g = Game(lets_in=False, bag={"pet large blue potion": 30, "pet rice roll": 5,
                                 "large blue potion": 80, "large red potion": 40})
    g.a = SaidAgent(["You have already entered an experience dungeon today"])
    notes = dungeon.run(as_bot(g), dungeon.config({}))
    assert g.walked == ["Bottomless pit"]                     # the best a level 80 has, once
    assert g.farmed == []
    assert len(notes) == 1 and "didn't let us in" in notes[0]
    assert chores.done_today("Hero")["_dungeon Bottomless pit"]["note"] == "not let in"


def test_an_entry_that_just_didnt_work_is_tried_three_times_and_not_written_down(shop: None) -> None:
    g = Game(lets_in=False, bag={"pet large blue potion": 30, "pet rice roll": 5,
                                 "large blue potion": 80, "large red potion": 40})
    g.character = "Unlucky"
    notes = dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Spider cave"}}))
    assert g.walked == ["Spider cave"] and g.did.count("talk") == dungeon.TRIES   # one walk, a knock a try
    assert g.farmed == []
    assert len(notes) == 1 and "couldn't get in after 3 tries" in notes[0]
    assert dungeon.done_today("Unlucky") == []                # the next run goes again
    g.lets_in = True
    dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Spider cave"}}))
    assert g.farmed and dungeon.done_today("Unlucky") == ["Spider cave"]


class LateMenuGame(Game):
    """The entrance's menu isn't there in time at the first knock."""

    def choose(self, pattern: str) -> dict[str, Any] | None:
        return super().choose(pattern) if self.did.count("talk") > 1 else None


def test_a_menu_that_comes_late_is_tried_again(shop: None) -> None:
    g = LateMenuGame()
    assert dungeon.enter(as_bot(g), "Spider cave") == dungeon.IN
    assert g.walked == ["Spider cave"] and g.did.count("talk") == 2 and g.mid == 1503
    assert any("trying again (2 of 3)" in line for line in g.lines)


class SlowGame(Game):
    """The game moves us in only after the LANDING wait is over."""

    def wait_map_change(self, mid: int, seconds: float) -> bool:
        return False

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        if self.walked:
            self.mid = dungeon.CAVES[self.cave]["map"]


def test_a_move_in_that_comes_late_is_seen_and_not_tried_again(shop: None) -> None:
    g = SlowGame()
    assert dungeon.enter(as_bot(g), "Scorpion Cave") == dungeon.IN
    assert g.walked == ["Scorpion Cave"] and g.mid == 1539


def test_one_cave_a_day_whichever_it_was(shop: None) -> None:
    g = Game(bag={"pet large blue potion": 30, "pet rice roll": 5, "large blue potion": 80, "large red potion": 40})
    g.character = "One a day"
    g._enter()
    dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Spider cave"}}))
    assert dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Scorpion Cave"}})) == []
    assert g.walked == ["Spider cave"]
    assert any("Spider cave is done for today - a character goes into one cave a day" in line for line in g.lines)


def test_a_menu_without_enter_is_said(shop: None) -> None:
    g = Game(menu=False)
    assert dungeon.enter(as_bot(g), "Spider cave") == dungeon.MISSED
    assert any("Enter the dungeon" in line for line in g.lines)
    assert g.walked == ["Spider cave"] and g.did.count("talk") == dungeon.TRIES


def test_a_cave_above_the_characters_level_is_said_and_not_tried(shop: None) -> None:
    bag = {"pet large blue potion": 30, "pet rice roll": 5, "large blue potion": 80, "large red potion": 40}
    g = Game(level=50, bag=bag)
    g.character = "Level fifty"
    g._enter()
    assert dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Scorpion Cave"}})) == []
    assert g.walked == [] and any("needs level 60" in line for line in g.lines)
    dungeon.run(as_bot(g), dungeon.config({}))              # the best it can: Spider cave
    assert g.walked == ["Spider cave"]


def test_a_cave_is_not_gone_into_when_the_next_level_is_close(shop: None) -> None:
    # "Stop before the next level": a visit gives a lot of experience and can't be stopped inside
    bag = {"pet large blue potion": 30, "pet rice roll": 5, "large blue potion": 80, "large red potion": 40}
    g = Game(bag=bag)
    g.character = "Close to a level"
    g.goal, g.xp_needed = 100000, 40000
    assert dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Spider cave"}})) == []
    assert g.walked == [] and g.restocks == []
    assert any("the next level needs only 40000 experience - not going into Spider cave" in line
               for line in g.lines)
    assert dungeon.done_today("Close to a level") == []       # the day's cave is still to use


def test_a_cave_is_gone_into_when_the_next_level_is_far_or_there_is_no_such_stop(shop: None) -> None:
    bag = {"pet large blue potion": 30, "pet rice roll": 5, "large blue potion": 80, "large red potion": 40}
    for name, goal, need in (("Far from a level", 100000, 500000), ("No level stop", None, 10),
                             ("Top level", 100000, -1)):
        g = Game(bag=dict(bag))
        g.character = name
        g.goal, g.xp_needed = goal, need
        g._enter()
        dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Spider cave"}}))
        assert g.walked == ["Spider cave"], name


def test_check_only_says_where_it_would_go_and_goes_nowhere(shop: None) -> None:
    g = Game()
    assert dungeon.run(as_bot(g), dungeon.config({}), check_only=True) == ["would go to: Bottomless pit"]
    assert g.walked == [] and g.restocks == []


def test_the_window_runs_its_end_place_then_the_caves() -> None:
    assert dungeon.script(dungeon.config({})) == "end_at start\ndungeon\n"
    cfg = dungeon.config({"dungeon": {"end": {"how": "place", "map": 1531, "cell": [160, 180]}}})
    text = dungeon.script(cfg)
    assert text.startswith("end_at 1531 at 160,180\n")
    assert script.check(text) is None


def test_the_script_command_parses() -> None:
    for line in ("dungeon", "dungeon check", "dungeon Scorpion Cave", "dungeon check spider cave"):
        assert not script.check(line + "\n")
    assert script.check("dungeon Atlantis\n")                           # no such dungeon
    assert script.check("dungeon Scorpion Cave, Spider cave\n")         # one cave a day


def test_the_window_names_the_chosen_cave_in_its_script() -> None:
    # a cave chosen in the window goes into the script, so a stale profile can't send it elsewhere
    text = dungeon.script(dungeon.config({"dungeon": {"cave": "Spider cave"}}))
    assert text == "end_at start\ndungeon Spider cave\n" and script.check(text) is None


def test_a_cave_named_in_the_script_goes_over_the_window_s(shop: None) -> None:
    g = Game(bag={"pet large blue potion": 30, "pet rice roll": 5, "large blue potion": 80, "large red potion": 40})
    g.character = "Named cave"
    g._enter()
    dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Scorpion Cave"}}), cave="Spider cave")
    assert g.walked == ["Spider cave"]


def test_settings_never_share_the_defaults() -> None:
    a = dungeon.config({})
    a["end"]["how"] = "home"
    assert dungeon.config({})["end"] == {"how": "start"} and dungeon.DEFAULTS["end"] == {"how": "start"}


def test_every_cave_has_a_known_entrance_and_a_patrol() -> None:
    from unclefarmer import atlas, dungeons
    for cave, v in dungeon.CAVES.items():
        e = dungeon.entrance(cave)
        assert e is not None and v["map"] in e["dungeon_maps"]
        assert atlas.patrol_loop(v["map"], "quick")
    assert dungeon.cave_of(1503) == "Spider cave" and dungeon.cave_of(1) is None
    assert dungeons.find("Spider cave") == dungeon.entrance("Spider cave")     # the game's files say


def test_the_two_caves_the_games_files_dont_place_carry_their_entrance() -> None:
    from unclefarmer import atlas, travel
    for cave, mid, at in (("Bottomless pit", 1631, (150, 19)), ("White Bone Cave", 1761, (61, 110))):
        e = dungeon.entrance(cave)
        assert e is not None and e["map"] == mid and tuple(e["at"]) == at and e["npc"]
        assert atlas.model(mid).ok(*at)
        assert travel.plan(1041, (200, 205), mid, at) is not None      # walked or by cable car
        assert "(%d)" % mid in dungeon.entrance_text(cave) and "%d,%d" % at in dungeon.entrance_text(cave)
    assert dungeon.entrance("Atlantis") is None and dungeon.entrance_text("Atlantis") == "entrance unknown"


def test_the_new_caves_parse_in_a_script() -> None:
    for line in ("dungeon Bottomless pit", "dungeon white bone cave", "dungeon check Bottomless pit"):
        assert not script.check(line + "\n")


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
    dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Scorpion Cave", "pet": "Wild Eagle>><< Sparky"}}))
    assert g.inside and g.inside[0] == ["Wild Eagle>><< Sparky"]
    assert any("taking Wild Eagle>><< Sparky into the dungeons" in line for line in g.lines)
    assert pets.chosen(as_bot(g)) == ["Pixie Sparky"]     # afterwards: Character, Settings again
    assert g.p["pets"] == [{"name": "Pixie Sparky", "use": True}]     # nothing saved in the profile


def test_no_pet_chosen_for_the_dungeons_takes_none_and_no_pet_supplies(shop: None) -> None:
    g = PetGame("No pet")
    cfg = dungeon.config({"dungeon": {"cave": "Scorpion Cave", "pet": dungeon.NO_PET}})
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


class SaidAgent(Agent):
    """Also what the game said after an action (the agent's `said`)."""

    def __init__(self, said: list[str]) -> None:
        super().__init__(False)
        self.said = said

    def json(self, line: str) -> Any:
        if line.startswith("said"):
            return {"now": 5, "said": [{"text": t} for t in self.said]}
        return super().json(line)


def test_turned_away_for_the_day_says_why(shop: None) -> None:
    g = Game(lets_in=False)
    g.a = SaidAgent(["You have already entered an experience dungeon today"])
    assert dungeon.enter(as_bot(g), "Spider cave") == dungeon.USED
    assert any("already been in an experience dungeon today" in line for line in g.lines)
    assert g.walked == ["Spider cave"]                        # no second go: the day is used


def test_not_let_in_for_another_reason_says_nothing_of_the_day(shop: None) -> None:
    g = Game(lets_in=False)
    g.a = SaidAgent(["Your level is too low"])
    assert dungeon.enter(as_bot(g), "Spider cave") == dungeon.MISSED
    assert not [line for line in g.lines if "already been" in line]
    assert "the game said: Your level is too low" in g.lines  # what it said, for the log


def test_a_run_started_inside_a_cave_hunts_on_without_leaving_or_shopping(shop: None) -> None:
    # the bot (or the game) restarted mid-visit: the day's cave is this one, and going out to
    # check the day or top up would lose the rest of it
    g = Game(bag={})
    g.character = "Restarted inside"
    g.mid = 1792
    g.cave = "White Bone Cave"
    g._enter()
    notes = dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Spider cave"}}))
    assert g.walked == [] and g.restocks == []
    assert g.farmed == [{"map": 1792, "patrol": "quick", "instance": True, "pack": 2, "chase": 90}]
    assert notes and "White Bone Cave" in notes[0] and g.mid == 1531
    assert any("already inside White Bone Cave" in line for line in g.lines)
    assert dungeon.done_today("Restarted inside") == ["White Bone Cave"]


def test_check_only_inside_a_cave_says_so_and_does_nothing(shop: None) -> None:
    g = Game()
    g.character = "Checking inside"
    g.mid = 1636
    assert dungeon.run(as_bot(g), dungeon.config({}), check_only=True) == ["already inside Bottomless pit"]
    assert g.farmed == [] and dungeon.done_today("Checking inside") == []


# ------------------------------------------------------------------------- ready at the door
FULL_BAG = {"pet large blue potion": 30, "pet rice roll": 5, "large blue potion": 80, "large red potion": 40}


def door_game(character: str, pet_rows: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> Game:
    """A game at a cave's door with its own pet list; the fusion beast change is noted."""
    g = Game(bag=dict(FULL_BAG))
    g.character = character               # its own done list: other tests' visits don't count
    g.a.pets = pet_rows
    g.p["pets"] = [{"name": r["name"], "use": True} for r in pet_rows]
    g._enter()

    def beast(b: Any, purpose: Any = None) -> None:
        g.did.append("beast " + str(purpose))
    monkeypatch.setattr(beasts, "ensure", stand_in(beasts.ensure, beast))
    return g


def test_everything_is_made_ready_at_the_door_and_nothing_but_the_fight_is_left_inside(
        shop: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # Live, every visit with a pet stood 11 to 13 s on the spawn point: the pet was called inside,
    # and the server ignores the "get off the beast" pressed right after a pet came out.
    g = door_game("Door", [{"id": 5, "name": "Pixie Sparky", "usable_at": 0}], monkeypatch)
    dungeon.run(as_bot(g), dungeon.config({"dungeon": {"cave": "Scorpion Cave"}}))
    did = [d for d in g.did if d != "look"]
    # the beast before the pet (twice: keep_out sees to it too), both before the door is knocked on
    assert did == ["arrived", "beast fight", "beast fight", "pet out", "talk", "choose", "landed", "farm"]
    assert g.did.count("look") == dungeon.DOOR_LOOKS
    assert g.did.index("talk") > max(i for i, d in enumerate(g.did) if d == "look")
    assert any("getting ready at the door of Scorpion Cave" in line for line in g.lines)
    assert any("summoned Pixie Sparky" in line for line in g.lines)


def test_the_landing_is_taken_the_moment_the_game_has_moved_us(shop: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = door_game("Landing", [], monkeypatch)
    g.cave = "Scorpion Cave"
    g.mid = 1539                                  # moved in already
    assert dungeon.landed(as_bot(g), 1531) and g.slept == []      # no settling second on the timer
    g.mid = 1531                                  # not let in: looked at till LANDING is over
    began = g.clock.now
    assert not dungeon.landed(as_bot(g), 1531)
    assert g.clock.now - began == pytest.approx(dungeon.LANDING, abs=dungeon.LANDING_LOOK)
    assert set(g.slept) == {dungeon.LANDING_LOOK}


def test_a_pet_ready_soon_is_waited_for_at_the_door(shop: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = door_game("Soon", [], monkeypatch)
    g.a.pets = [{"id": 5, "name": "Frog Hop", "usable_at": g.clock.now + 90}]
    g.p["pets"] = [{"name": "Frog Hop", "use": True}]
    began = g.clock.now
    dungeon.pet_ready(as_bot(g))
    assert 90 <= g.clock.now - began <= 95
    assert any("Frog Hop can come out in" in line and "waiting for it at the door" in line for line in g.lines)
    assert g.a.pets[0]["usable_at"] == -1 and any("summoned Frog Hop" in line for line in g.lines)


def test_a_pet_that_takes_long_is_not_waited_for(shop: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = door_game("Late", [], monkeypatch)
    g.a.pets = [{"id": 5, "name": "Frog Hop", "usable_at": g.clock.now + 20 * 60}]
    g.p["pets"] = [{"name": "Frog Hop", "use": True}]
    began = g.clock.now
    dungeon.pet_ready(as_bot(g))
    assert g.clock.now == began and g.keys == []
    assert any("going in without it" in line for line in g.lines)


def test_a_pet_already_out_is_left_as_it_is(shop: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = door_game("Out", [{"id": 5, "name": "Pixie Sparky", "usable_at": -1}], monkeypatch)
    began = g.clock.now
    dungeon.pet_ready(as_bot(g))
    assert g.keys == []                           # nothing called, nothing put away
    assert g.clock.now - began == 0.5             # only its description read (pets.describe), here
    assert 5 in vars(g)["pet_described"]          # ... so not between two fights inside


def test_a_summon_the_game_did_not_take_is_tried_once_more_after_the_gap(
        shop: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = door_game("Refused", [{"id": 5, "name": "Pixie Sparky", "usable_at": 0}], monkeypatch)
    g.takes_summons = False
    dungeon.pet_ready(as_bot(g))
    assert len(g.keys) == 2                       # the summon, and once more
    assert beasts.GAP in g.slept
    assert 5 in g.pets_refused                    # then it is the farm loop's, later


def test_no_pet_for_the_run_nothing_is_called(shop: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = door_game("None", [{"id": 5, "name": "Pixie Sparky", "usable_at": 0}], monkeypatch)
    vars(g)["pets_for_run"] = []
    dungeon.pet_ready(as_bot(g))
    assert g.keys == [] and g.a.pets is not None and g.a.pets[0]["usable_at"] == 0


def test_an_entrance_without_a_known_place_is_knocked_on_where_it_is_seen() -> None:
    g = Game()
    g.cave = "Scorpion Cave"
    assert dungeon.knock(as_bot(g), {"name": "Scorpion Cave", "map": 1531, "npc": None, "at": None})
    assert g.talked == ("Scorpion Cave", (10, 10))
    assert dungeon.knock(as_bot(g), {"name": "Spider cave", "map": 1502, "npc": "Spider cave", "at": [54, 4]})
    assert g.talked == ("Spider cave", (54, 4))
    assert not dungeon.knock(as_bot(g), {"name": "bottomless pit", "map": 1631, "npc": None, "at": None})
