# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Gathering (engine/gathering.py) and selling the collection (town.sell_collection), against a
pretend game shaped like the logged sessions: the bag's Profession skills panel starts the skill,
the game then casts it by itself every two seconds and drops the catch into the bag, says "lack of
vitality" when Vitality runs out, and the buyers list one row per kind ("101 Milkfish; Unit price
670 arnebia")."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import Any, TypeAlias

import pytest

from unclefarmer.engine import chores, gathering, script, town
from unclefarmer.engine.bot import Stopped
from conftest import as_bot, returning, stand_in
from unclefarmer.clock import Waitable

ME = 1234
SKILLS: dict[str, Any] = {"combat": [], "talents": [],
          "profession": [{"id": 21, "name": "Draw Blood"}, {"id": 13, "name": "Fishing"},
                         {"id": 1, "name": "Snuggle"}],
          "profession_ranks": {"21": 5, "13": 3, "1": 0}}


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def time(self) -> float:
        return self.now

    def wait(self, event: Waitable, seconds: float) -> bool:
        self.now += seconds
        return False


class Agent:
    """The agent's answers: skills, our casts (the game gathering) and what the game said."""

    def __init__(self, game: Game) -> None:
        self.g = game

    def json(self, line: str) -> dict[str, Any]:
        g = self.g
        if line == "mem skills":
            return g.skills
        verb, _sp, rest = line.partition(" ")
        since = int(rest or 0)
        if verb == "casts":
            return {"casts": [c for c in g.casts if c["t"] > since]}
        if verb == "said":
            return {"said": [{"t": t, "text": s} for t, s in g.said if t > since]}
        return {"error": "unknown"}

    def send(self, line: str) -> str:
        return "ok"


Action: TypeAlias = "Callable[[Game], str | None]"    # an entry's action: runs when chosen, names the next page
Entry: TypeAlias = "tuple[str, str | Action | None]"
Page: TypeAlias = "list[Entry] | Callable[[Game], list[Entry]]"


class Game:
    """What gathering and selling use of the Bot, with a game that gathers once started."""

    def __init__(self, map_id: int=1561, vitality: int=20, catches_on: bool=True) -> None:
        self.clock = Clock()
        self.p: dict[str, Any] = {"use_key": "rctrl", "bag_full_at": 100, "gem_keep_arnebia": 20000}
        self.a = Agent(self)
        self.skills: dict[str, Any] = SKILLS
        self.character = "Hero"
        self.stop_event = threading.Event()
        self.mid = map_id
        self.x, self.y = 100.0, 100.0
        self.bag: dict[str, int] = {"Arnebia": 50000}
        self.casts: list[dict[str, Any]] = []
        self.said: list[tuple[int, str]] = []
        self.lines: list[str] = []
        self.vitality = vitality
        self.catches_on = catches_on          # False: a place where the game never starts it
        self.gathering = False
        self.next_cast = 0.0
        self.starts = 0
        self.attack_at: float | None = None
        self.fought = 0
        self.travelled: list[Any] = []
        self.page: str | None = None
        self.pages: dict[str, Page] = {}
        self.prices: dict[str, int] = {}       # what the pretend buyer pays (buyer())
        self.load = 100
        self.hp = 100
        self.saved: list[Any] = []
        self.pressed: list[str] = []
        self.stopped = 0
        self.drank = 0
        self.looked = 0
        self.bag_start = None

    # time passing: the game casts while gathering is on
    def _tick(self, seconds: float) -> None:
        end = self.clock.now + seconds
        while self.gathering and self.next_cast <= end:
            self.clock.now = max(self.clock.now, self.next_cast)
            if self.vitality <= 0:
                self.gathering = False
                self.said.append((self.ms(), "lack of vitality"))
                break
            self.vitality -= 1
            self.casts.append({"t": self.ms(), "caster": ME, "seq": len(self.casts), "skill": "Fishing"})
            self.bag["Milkfish"] = self.bag.get("Milkfish", 0) + 2
            self.next_cast += 2.04
        self.clock.now = max(self.clock.now, end)

    def ms(self) -> int:
        return int(self.clock.now * 1000)

    def sleep(self, s: float) -> None:
        self._tick(s)

    def check(self) -> None:
        if self.stop_event.is_set():
            raise Stopped("stopped by you")

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def map_id(self) -> int:
        return self.mid

    def me(self) -> dict[str, Any]:
        return {"id": ME, "x": self.x, "y": self.y, "hp": self.hp, "maxhp": 100, "sheet": {"level": 85}}

    def snap(self) -> dict[str, Any]:
        return {"self": self.me(), "entities": [], "map": {"id": self.mid}}

    def look_after(self, me: dict[str, Any] | None=None) -> dict[str, Any]:
        self.looked += 1
        if self.hp < 100:                 # a potion: it stops gathering in the game
            self.drank += 1
            self.hp = 100
            self.gathering = False
        return me if me is not None else self.me()

    def refresh_sheet(self, force: bool=False) -> dict[str, Any] | None:
        return {"level": 85, "load": self.load, "maxload": 10000, "vitality": float(self.vitality)}

    def attackers(self) -> set[int]:
        if self.attack_at is not None and self.clock.now >= self.attack_at:
            return {-5}
        return set()

    def fight_back(self) -> None:
        self.fought += 1
        self.attack_at = None
        self.gathering = False            # a fight stops gathering
        self.x += 4                       # and moves us off the spot

    def travel(self, mid: int, cell: tuple[float, float] | None=None) -> None:
        self.travelled.append((mid, cell))
        self.mid = mid
        if cell:
            self.x, self.y = float(cell[0]), float(cell[1])

    # the panel start
    def close_dialogs(self) -> None:
        self.page = None

    def close_text_box(self) -> bool:
        return False

    def panel_cursor(self, panel: int, index: int, size: int) -> bool:
        self.cursor = (panel, index)
        return True

    def key(self, k: str, ms: int=60) -> None:
        self.pressed.append(k)
        if k == "rctrl" and getattr(self, "cursor", None) == (gathering.PANEL, 1):
            self.page = "skill"

    def bag_rows(self) -> list[dict[str, Any]]:
        return [{"name": n, "count": c} for n, c in self.bag.items()]

    # menus
    def entries(self) -> list[Entry]:
        menus: dict[str, Page] = {
            "skill": [("Stop Fishing", self._stop)] if self.gathering else [("Start Fishing", "confirm")],
            "confirm": [("Fishing; Use the Fishing Skill to catch fish in the specified Water area.", None),
                        ("Confirm", self._start)]}
        page = menus.get(self.page or "") or self.pages.get(self.page or "")
        return page(self) if callable(page) else (page or [])

    def _stop(self, g: Game) -> None:
        self.gathering = False
        self.stopped += 1

    def _start(self, g: Game) -> None:
        self.starts += 1
        if self.catches_on and self.vitality > 0:
            self.gathering = True
            self.next_cast = self.clock.now + 0.6
        return None

    def dialog(self) -> dict[str, Any]:
        if self.page is None:
            return {"open": 0, "top": None}
        return {"open": 1, "top": {"rows": [[{"text": t}] for t, _a in self.entries()], "row": 0, "col": 0}}

    @staticmethod
    def entry_texts(e: dict[str, Any]) -> list[str]:
        return [e["text"]]

    def find_entry(self, top: dict[str, Any] | None, pattern: str) -> tuple[int, int] | None:
        for r, row in enumerate((top or {}).get("rows", [])):
            if row and re.search(pattern, row[0]["text"], re.I):
                return r, 0
        return None

    def choose(self, pattern: str) -> dict[str, Any] | None:
        for text, action in self.entries():
            if re.search(pattern, text, re.I):
                self.page = action(self) if callable(action) else action
                return {"text": text}
        self.page = None
        return None

    def wait_dialog(self, seconds: float=3.0, want_open: bool=True) -> dict[str, Any] | None:
        d = self.dialog()
        return d if bool(d["open"]) == want_open else None

    def note_dialog(self, d: dict[str, Any], why: str) -> None:
        pass

    def talk(self, name: str, at: tuple[float, float] | None=None) -> bool:
        if name not in self.pages:
            return False
        self.page = name
        return True

    def wait_map_change(self, mid: int, seconds: float) -> bool:
        return self.mid != mid

    def leave_instance(self) -> bool:
        self.mid = 1041
        return True

    def save_profile(self, p: dict[str, Any]) -> None:
        self.saved.append(p)


def own(**over: Any) -> dict[str, Any]:
    return dict({"skill": "Fishing", "map": 1561, "x": 186, "y": 205}, **over)


# ---------------------------------------------------------------------------------------- the skill
def test_the_gathering_skill_and_its_rank_come_from_the_profession_list() -> None:
    assert gathering.skill_of(SKILLS) == ("Fishing", 3)
    assert gathering.rank_name(3) == "Expert" and gathering.rank_name(5) == "Grand Master"
    older = dict(SKILLS, profession_ranks={})            # an agent before 49 sends no ranks
    assert gathering.skill_of(older) == ("Fishing", None)
    assert gathering.skill_of({"profession": [{"id": 21, "name": "Draw Blood"}]}) == (None, None)


# ---------------------------------------------------------------------------------------- places
def test_the_best_place_is_the_highest_area_the_character_can_take() -> None:
    found = [dict(own(), source="mine", level=80, label="north bank"),
             dict(own(x=15, y=397), source="known", level=90, label="south shore"),
             dict(own(map=1501, x=300, y=30), source="known", level=30, label="river")]
    cfg = dict(gathering.DEFAULTS)

    def label(level: int, **over: Any) -> str | None:
        p = gathering.choose(found, level, 5, dict(cfg, **over))
        return p["label"] if p else None
    assert label(85) == "south shore"
    assert label(70) == "river"                                # 80 and 90 are too far above 70
    assert label(70, above=10) == "north bank"
    assert label(85, place="river") == "river"


def test_a_place_that_refused_us_waits_for_a_better_rank() -> None:
    p = dict(own(), source="known", level=80, label="north bank")
    cfg = dict(gathering.DEFAULTS, refused={gathering.place_key(p): 3})
    assert gathering.choose([p], 85, 3, cfg) is None
    assert gathering.choose([p], 85, 4, cfg) is p


def test_places_know_their_area_level_and_never_include_a_gangs_area() -> None:
    cfg = dict(gathering.DEFAULTS, known_places=False,
               places=[own(), own(map=4222, x=3, y=45)])
    found = gathering.places(cfg, "Fishing")
    assert [p["map"] for p in found] == [1561]
    assert found[0]["level"] == gathering.area_level(1561, 186, 205) and found[0]["level"] >= 80
    assert found[0]["label"] == "far off regions (1561) at 186,205"
    assert gathering.places(cfg, "Mining") == []


def test_every_known_place_is_a_walkable_cell_of_an_open_field() -> None:
    found = 0
    for skill in gathering.SKILLS:
        for p in gathering.known_places(skill):
            found += 1
            assert p["skill"] == skill and int(p["map"]) not in gathering.GANG_AREAS
            assert gathering.atlas.kind(int(p["map"])) == "field"
            assert gathering.atlas.model(int(p["map"])).ok(int(p["x"]), int(p["y"]))
    assert found >= 10


# ---------------------------------------------------------------------------------------- gathering
def test_it_gathers_until_vitality_runs_out() -> None:
    g = Game(vitality=10)
    why = gathering.gather_here(as_bot(g), dict(gathering.DEFAULTS), "Fishing", None, None)
    assert why == "vitality"
    assert g.starts == 1 and g.bag["Milkfish"] == 20


def test_a_fight_stops_it_and_it_starts_again_on_its_own_spot() -> None:
    g = Game(vitality=20)
    g.attack_at = g.clock.now + 10
    p = dict(own(), source="mine", label="north bank")
    g.x, g.y = 186.0, 205.0
    why = gathering.gather_here(as_bot(g), dict(gathering.DEFAULTS), "Fishing", p, None)
    assert why == "vitality" and g.fought == 1
    assert g.starts == 2                                   # once, and again after the fight
    assert g.travelled[-1] == (1561, (186, 205))           # walked back onto the spot


def test_a_place_where_the_game_never_starts_it_is_refused() -> None:
    g = Game(catches_on=False)
    why = gathering.gather_here(as_bot(g), dict(gathering.DEFAULTS), "Fishing", None, None)
    assert why == "refused" and g.starts == gathering.STARTS


def test_a_full_bag_and_the_time_limit_end_it() -> None:
    g = Game(vitality=100)
    g.load = 10000
    assert gathering.gather_here(as_bot(g), dict(gathering.DEFAULTS), "Fishing", None, None) == "full"
    g = Game(vitality=100)
    end = g.clock.now + 30
    assert gathering.gather_here(as_bot(g), dict(gathering.DEFAULTS), "Fishing", None, end) == "time"


# ---------------------------------------------------------------------------------------- the gang's area
def gang_pages(g: Game, fee: int=0) -> None:
    def entrance(_g: Game) -> list[Entry]:
        return [("lake of the gang; level 3; saturation 99.9%", None),
                ("Entry costs %d arnebia" % fee, None),
                ("Enter", lambda gg: setattr(gg, "mid", 4222)),
                ("administer", None), ("instruction", None)]
    g.pages["Lake Entrance"] = entrance


@pytest.fixture
def inner_hall(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chores, "inner_hall", stand_in(chores.inner_hall, lambda b: None))
    monkeypatch.setattr(gathering.crafting, "reachable_cell", stand_in(gathering.crafting.reachable_cell, lambda b, cell: True))


def test_the_gangs_lake_is_entered_once_a_day(inner_hall: None) -> None:
    g = Game(map_id=4004)
    gang_pages(g)
    entered, about, over = gathering.gang_area(as_bot(g), dict(gathering.DEFAULTS), "Fishing")
    assert entered and over and g.mid == 4222
    assert about == "level 3, saturation 99.9%, entry 0 arnebia"


def test_check_only_and_a_fee_above_the_limit_never_enter(inner_hall: None) -> None:
    g = Game(map_id=4004)
    gang_pages(g, fee=18000)
    entered, about, _over = gathering.gang_area(as_bot(g), dict(gathering.DEFAULTS), "Fishing", check_only=True)
    assert not entered and g.mid == 4004 and "18000" in about
    entered, about, over = gathering.gang_area(as_bot(g), dict(gathering.DEFAULTS, gang_fee_max=10000), "Fishing")
    assert not entered and not over and g.mid == 4004


def test_a_gang_without_that_area_is_done_for_the_day(inner_hall: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game(map_id=4004)
    monkeypatch.setattr(g, "find_npc", returning(None), raising=False)
    monkeypatch.setattr(g, "walk_to", returning("arrived"), raising=False)
    entered, about, over = gathering.gang_area(as_bot(g), dict(gathering.DEFAULTS), "Mining")
    assert not entered and over and "Mining Area Entrance" in about


# ---------------------------------------------------------------------------------------- selling
def buyer(g: Game, lines: list[tuple[str, int]]) -> None:
    def menu(_g: Game) -> list[Entry]:
        rows: list[Entry] = [("Hello Hero, what would you like to sell?", None)]
        for item, price in lines:
            if g.bag.get(item):
                rows.append(("%d %s; Unit price %d arnebia" % (g.bag[item], item, price), None))
        if len(rows) == 1:
            rows.append(("You have no fish and shrimp to sell", None))
        return rows
    g.pages["Aquaculture Buyer"] = menu
    g.prices = dict(lines)


@pytest.fixture
def market(monkeypatch: pytest.MonkeyPatch) -> None:
    def visit(b: Game, role: str | None, name: str | None = None, candidates: object = None) -> str:
        b.page = "Aquaculture Buyer"
        return "Aquaculture Buyer"

    def pick(b: Game, pattern: str, quantity: int | None = None) -> bool:
        item = next(i for i in b.prices if re.search(pattern, "%d %s; Unit price" % (b.bag.get(i, 0), i)))
        n = min(quantity or 0, b.bag[item])
        b.bag[item] -= n
        b.bag["Arnebia"] += n * b.prices[item]
        b.page = None
        return True
    monkeypatch.setattr(town, "visit", visit)
    monkeypatch.setattr(town, "pick", pick)
    monkeypatch.setattr(town.knowledge, "service_npcs", stand_in(town.knowledge.service_npcs, lambda role: [(1041, "Aquaculture Buyer", 151, 192)]))
    monkeypatch.setattr(town.stats, "moved", stand_in(town.stats.moved, lambda bot, how, item, n: None))


def test_the_collection_is_sold_row_by_row_but_never_what_is_kept(market: None) -> None:
    g = Game()
    g.bag.update({"Milkfish": 101, "Crystal Fish": 96, "Lobster": 47})
    g.p["bag_rules"] = {"crystal fish": {"action": "keep", "keep": 0}, "lobster": {"action": "sell", "keep": 7}}
    buyer(g, [("Milkfish", 670), ("Crystal Fish", 350), ("Lobster", 1150)])
    earned = town.sell_collection(as_bot(g), ("fish",))
    assert g.bag["Milkfish"] == 0 and g.bag["Crystal Fish"] == 96 and g.bag["Lobster"] == 7
    assert earned == 101 * 670 + 40 * 1150


def test_the_buyers_rows_are_read() -> None:
    rows = [(101, "Milkfish", 670), (39, "Flame Starfish", 2020)]
    g = Game()
    page: list[Entry] = [("Hello Hero, what would you like to sell?", None)]
    page.extend(("%d %s; Unit price %d arnebia" % r, None) for r in rows)
    g.pages["b"] = page
    g.page = "b"
    assert town.buyer_rows(as_bot(g)) == rows


# ---------------------------------------------------------------------------------------- the run
def test_a_run_uses_the_gang_area_then_sells(inner_hall: None, market: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game(map_id=4004, vitality=6)
    gang_pages(g)
    buyer(g, [("Milkfish", 670)])
    monkeypatch.setattr(gathering.beasts, "off", stand_in(gathering.beasts.off, lambda b: None))
    monkeypatch.setattr(gathering.town.knowledge, "category", stand_in(gathering.town.knowledge.category, lambda name: "fish" if "fish" in (name or "").lower() else "other"))
    notes = gathering.run(as_bot(g), dict(gathering.DEFAULTS, outside=False))
    assert g.bag.get("Milkfish") == 0 and g.bag["Arnebia"] == 50000 + 12 * 670
    assert gathering.gang_done_today(as_bot(g)) is not None
    assert any("Vitality" in n for n in notes)
    # the same day the gang's area is not tried again
    g2 = Game(map_id=4004, vitality=0)
    gang_pages(g2)
    gathering.run(as_bot(g2), dict(gathering.DEFAULTS, outside=False))
    assert g2.mid == 4004


def test_no_gathering_skill_says_what_to_learn() -> None:
    g = Game()
    g.skills = {"profession": [{"id": 21, "name": "Draw Blood"}]}
    with pytest.raises(Stopped, match="Guru Elder"):
        gathering.run(as_bot(g), dict(gathering.DEFAULTS))


# ---------------------------------------------------------------------------------------- scripts
def test_the_script_commands_parse() -> None:
    main, _subs, _stop = script.parse("gather check\nsell_collection fish\nsell_collection\n")
    assert main[0].opts["check"] is True
    assert main[1].opts["kinds"] == ("fish",)
    assert main[2].opts["kinds"] == ("fish", "ore", "herb")
    with pytest.raises(script.ScriptError):
        script.parse("sell_collection gems\n")


def test_outside_a_refused_place_is_remembered_and_the_next_one_is_used(market: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game(map_id=1041, vitality=4)
    buyer(g, [("Milkfish", 670)])
    monkeypatch.setattr(gathering.beasts, "off", stand_in(gathering.beasts.off, lambda b: None))
    refusing = (1561, (15, 397))
    real_travel = g.travel

    def travel(mid: int, cell: tuple[float, float] | None=None):
        real_travel(mid, cell)
        g.catches_on = (mid, cell) != refusing     # the game never starts it on the first place
    monkeypatch.setattr(g, "travel", travel)
    cfg = dict(gathering.DEFAULTS, gang=False, known_places=False,
               places=[own(x=15, y=397, name="south shore"), own(name="north bank")])
    notes = gathering.run(as_bot(g), cfg)
    assert [t for t in g.travelled if t[0] == 1561] == [refusing, (1561, (186, 205))]
    key = gathering.place_key(own(x=15, y=397))
    assert cfg["refused"][key] == 3 and g.saved            # remembered for rank 3, in the profile
    assert "gathered at north bank" in notes and g.bag["Milkfish"] == 0


def test_outside_a_full_bag_is_sold_and_it_comes_back(market: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game(map_id=1041, vitality=6)
    buyer(g, [("Milkfish", 670)])
    monkeypatch.setattr(gathering.beasts, "off", stand_in(gathering.beasts.off, lambda b: None))
    full = {"n": 0}

    def sheet(force: bool=False):
        full["n"] += 1
        return {"level": 85, "load": 10000 if full["n"] == 8 else 100, "maxload": 10000}
    monkeypatch.setattr(g, "refresh_sheet", sheet)
    cfg = dict(gathering.DEFAULTS, gang=False, known_places=False, places=[own(name="north bank")])
    gathering.run(as_bot(g), cfg)
    assert [t[0] for t in g.travelled].count(1561) == 2          # there, to sell, and back
    assert g.bag["Milkfish"] == 0 and g.bag["Arnebia"] == 50000 + 12 * 670


def test_a_place_the_way_to_fails_is_left_for_the_run(market: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game(map_id=1041, vitality=2)
    buyer(g, [("Milkfish", 670)])
    monkeypatch.setattr(gathering.beasts, "off", stand_in(gathering.beasts.off, lambda b: None))
    real_travel = g.travel

    def travel(mid: int, cell: tuple[float, float] | None=None):
        if cell == (15, 397):
            raise Stopped("no way there")
        real_travel(mid, cell)
    monkeypatch.setattr(g, "travel", travel)
    cfg = dict(gathering.DEFAULTS, gang=False, known_places=False,
               places=[own(x=15, y=397, name="south shore"), own(name="north bank")])
    notes = gathering.run(as_bot(g), cfg)
    assert "gathered at north bank" in notes and not cfg["refused"]


# ---------------------------------------------------------------------------------------- other gangs
def test_the_gang_list_keeps_the_players_order_once_each() -> None:
    assert gathering.gangs({}) == [""]
    assert gathering.gangs({"gangs": ["", "Friends", " friends ", "Allies"]}) == ["", "Friends", "Allies"]
    assert gathering.gang_name("") == "our own gang" and gathering.gang_name("Allies") == "Allies"


def test_another_gangs_base_is_found_by_name_at_the_gang_entrance(monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game(map_id=1041)
    g.pages["Gang Entrance"] = [("Enter the Outer hall of the gang", None), ("all gangs", None)]
    g.pages["Friends"] = [("Enter the Outer hall of the gang", None),
                          ("Enter the inner  hall of the gang", lambda gg: setattr(gg, "mid", 4002))]
    picked: list[str] = []

    def visit(b: Game, role: str | None, name: str | None = None, candidates: object = None) -> None:
        b.page = "Gang Entrance"
    monkeypatch.setattr(gathering.town, "visit", visit)
    monkeypatch.setattr(gathering.crafting, "pick_at_entrance",
                        stand_in(gathering.crafting.pick_at_entrance, lambda b, gang: picked.append(gang) or setattr(b, "page", gang) or True))
    monkeypatch.setattr(gathering.crafting, "in_gang", stand_in(gathering.crafting.in_gang, lambda b: g.mid == 4002))
    gathering._into_gang(as_bot(g), "Friends")
    assert picked == ["Friends"] and g.mid == 4002                  # its inner hall, where the Lake Entrance is
    monkeypatch.setattr(gathering.crafting, "pick_at_entrance", stand_in(gathering.crafting.pick_at_entrance, lambda b, gang: False))
    with pytest.raises(Stopped, match="no gang called Nobody"):
        gathering._into_gang(as_bot(g), "Nobody")


def test_own_gang_then_the_others_each_once_a_day(market: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game(map_id=1041, vitality=100)
    buyer(g, [("Milkfish", 670)])
    monkeypatch.setattr(gathering.beasts, "off", stand_in(gathering.beasts.off, lambda b: None))
    went: list[str] = []

    def into(b: Game, gang: str) -> None:
        went.append(gang)
        b.mid = 4004
        b.page = None
    monkeypatch.setattr(gathering, "_into_gang", into)
    monkeypatch.setattr(gathering.crafting, "reachable_cell", stand_in(gathering.crafting.reachable_cell, lambda b, cell: True))
    gang_pages(g)

    def leave():                          # out of the area: the half hour is cut short
        g.mid = 1041
        return True
    monkeypatch.setattr(g, "leave_instance", leave)
    real_sleep = g.sleep

    def sleep(s: float) -> None:          # each area's half hour ends after a few catches
        real_sleep(s)
        if g.mid == 4222 and len(g.casts) % 5 == 4:
            g.mid = 1041
            g.gathering = False
    monkeypatch.setattr(g, "sleep", sleep)
    cfg = dict(gathering.DEFAULTS, outside=False, gangs=["", "Friends", "Allies"])
    notes = gathering.run(as_bot(g), cfg)
    assert went == ["", "Friends", "Allies"]
    assert all(gathering.gang_done_today(as_bot(g), x) for x in ("", "Friends", "Allies"))
    assert [n.split(":")[0] for n in notes][:3] == ["our own gang", "Friends", "Allies"]
    went.clear()
    gathering.run(as_bot(g), cfg)
    assert went == []                     # each gang's area once a game day


def test_the_load_is_looked_at_before_gathering_not_kept_free_while_it_runs() -> None:
    g = Game()
    cfg = dict(gathering.DEFAULTS)
    g.load = 8900
    assert gathering.no_room(as_bot(g), cfg) is None                # 1100 left
    g.load = 9100
    assert gathering.no_room(as_bot(g), cfg) == (9100, 10000)      # 900 left: full for setting out
    assert gathering.no_room(as_bot(g), dict(cfg, free_load=800)) is None
    assert not gathering.bag_full(as_bot(g))                       # while gathering: only the game's limit
    g.load = 10000
    assert gathering.bag_full(as_bot(g))


def test_a_full_bag_is_said_before_setting_out(monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game(map_id=1041)
    g.load = 9500
    notes: list[str] = []
    assert not gathering._room(as_bot(g), dict(gathering.DEFAULTS, sell=False), "Fishing", notes)
    assert notes == ["the bag is full: load 9500 of 10000"] and notes[0] in g.lines


def test_a_gangs_area_is_left_to_the_game_for_its_half_hour() -> None:
    g = Game(vitality=12)
    g.hp = 50
    g.attack_at = g.clock.now + 5
    gathering.gather_here(as_bot(g), dict(gathering.DEFAULTS), "Fishing", None, None)
    assert g.starts == 1 and g.pressed == ["rctrl"]        # started, then nothing pressed
    assert g.looked == 0 and g.fought == 0


def test_outside_it_looks_after_itself_and_starts_again() -> None:
    g = Game(vitality=12)
    p = dict(own(), source="mine", label="north bank")
    g.x, g.y = 186.0, 205.0
    real_tick = g._tick
    hurt = {"done": False}

    def tick(seconds: float):                    # hurt in the middle: a potion stops it, then a new start
        real_tick(seconds)
        if len(g.casts) >= 3 and not hurt["done"]:
            hurt["done"] = True
            g.hp = 50
    g._tick = tick
    gathering.gather_here(as_bot(g), dict(gathering.DEFAULTS), "Fishing", p, None)
    assert g.looked > 1 and g.drank == 1 and g.starts == 2


def test_a_menu_offering_stop_means_it_runs_already() -> None:
    g = Game(vitality=12)
    g.gathering = True
    assert gathering.start(as_bot(g), "Fishing") is True
    assert g.stopped == 0 and g.page is None             # Stop is never chosen


def test_no_gangs_area_is_entered_without_room_in_the_bag(inner_hall: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game(map_id=4004)
    gang_pages(g)
    g.load = 9500
    monkeypatch.setattr(gathering, "_holding", stand_in(gathering._holding, lambda b, skill: False))
    notes: list[str] = []
    assert gathering._gang_round(as_bot(g), dict(gathering.DEFAULTS, sell=False), "Fishing", None, notes) == "full"
    assert notes == ["the bag is full: load 9500 of 10000"]
    assert g.mid == 4004 and gathering.gang_done_today(as_bot(g)) is None     # the day's entry is kept


# ---------------------------------------------------------------------------------------- Vitality
def test_outside_it_stops_at_the_vitality_the_player_keeps() -> None:
    g = Game(vitality=50)
    p = dict(own(), source="mine", label="north bank")
    g.x, g.y = 186.0, 205.0
    cfg = dict(gathering.DEFAULTS, stop_vitality=40)
    assert gathering.gather_here(as_bot(g), cfg, "Fishing", p, None) == "kept"
    assert 39 <= g.vitality <= 40                           # read from the sheet as it went down
    g = Game(vitality=50)
    assert gathering.gather_here(as_bot(g), cfg, "Fishing", None, None) == "vitality"   # a gang's area: its half hour


def test_it_doesnt_set_out_with_vitality_already_that_low(market: None, monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game(map_id=1041, vitality=30)
    buyer(g, [("Milkfish", 670)])
    cfg = dict(gathering.DEFAULTS, gang=False, known_places=False, places=[own(name="north bank")],
               stop_vitality=40)
    notes = gathering.run(as_bot(g), cfg)
    assert g.travelled == [] and any("30" in n for n in notes)
    assert gathering.vitality_kept(as_bot(g), dict(cfg, stop_vitality=0)) is None     # 0: until it runs out
