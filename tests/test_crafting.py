# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The crafting tools: getting to a craftsman, reading and ticking the game's menus, making gems and
crystals, and the enhancer's decisions.

Every test drives the module functions against a stand-in bot whose dialog is a plain menu (rows of
entries) that the bot walks with choose(), exactly as the real DialogsMixin does, and monkeypatches
the town helpers (visit, pick, counts' inputs) so nothing touches a real game or the repo.
"""

from __future__ import annotations

import threading

import pytest

from unclefarmer import atlas, knowledge, settings, travel
from unclefarmer.engine import bagrules, crafting, town
from unclefarmer.engine.botparts.base import Stopped
from typing import Any
from collections.abc import Callable
from conftest import failing, as_bot, as_kind, returning, stand_in
from unclefarmer.atlaslib import MapModel


def entry(text: str, cmd: int=0) -> dict[str, Any]:
    return {"flags": 12, "options": [text], "cmd": [cmd]}


def menu(*rows: list[Any]) -> dict[str, Any]:
    return {"row": 0, "col": 0, "rows": [[entry(t) if isinstance(t, str) else t for t in row] for row in rows]}


def add(b: Bot, name: str, n: int) -> None:
    for r in b.bag:
        if r["name"] == name:
            r["count"] = r.get("count", 0) + n
            return
    b.bag.append({"name": name, "count": n})


class Link:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    def send(self, line: str) -> str:
        self.bot.sent.append(line)
        return "ok"

    def json(self, line: str) -> Any:
        if line == "input":
            return {"ok": True, "open": self.bot.input_open}
        return {"ok": True}


class OneRegion:
    """A map whose region at each cell comes from region_of(x, y)."""

    def __init__(self, region_of: Callable[[int, int], int]) -> None:
        self.region_of = region_of

    def comp_of(self, x: int, y: int) -> int:
        return self.region_of(x, y)


class Bot:
    def __init__(self, bag: list[dict[str, Any]] | None=None, profile: dict[str, Any] | None=None, top: dict[str, Any] | None=None) -> None:
        self.p = dict(settings.PROFILE_DEFAULTS)
        self.p.update(profile or {})
        self.bag = [dict(r) for r in (bag or [])]
        self.top = top
        self.open = top is not None
        self.lines: list[str] = []
        self.keys: list[str] = []
        self.sleeps: list[float] = []
        self.chooses: list[str] = []
        self.closed = 0
        self.map = 1
        self.map_name = ""
        self.pos = (0.0, 0.0)
        self.stop_event = threading.Event()
        self.map_change = True
        self.map_change_args = None
        self.talk_result = True
        self.talked: list[tuple[str, tuple[float, float] | None]] = []
        self.visited: list[object] = []           # a map id, a role, or what visit() was asked
        self.sheet: dict[str, Any] | None = None
        self.refresh_calls = 0
        self.input_open = False
        self.sent: list[str] = []
        self.a = Link(self)
        self.on_choose: Callable[[str], dict[str, Any] | None] | None = None
        self.on_key: Callable[[str], object] | None = None

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def key(self, k: str) -> None:
        self.keys.append(k)
        if self.on_key is not None:
            self.on_key(k)

    @staticmethod
    def entry_texts(e: dict[str, Any]) -> list[str]:
        out = [e.get("text"), e.get("raw")] + [e.get(k) for k in ("f", "g", "h", "i")]
        return [t for t in out + list(e.get("options") or []) if t]

    def find_entry(self, top: dict[str, Any] | None, pattern: str) -> tuple[int, int] | None:
        if not top:
            return None
        for r, row in enumerate(top["rows"]):
            for c, e in enumerate(row):
                if e and any(travel.matches(pattern, t) for t in self.entry_texts(e)):
                    return r, c
        return None

    def dialog(self) -> dict[str, Any]:
        return {"ok": True, "open": 1 if self.open else 0, "top": self.top if self.open else None}

    def choose(self, pattern: str) -> dict[str, Any] | None:
        self.chooses.append(pattern)
        if self.on_choose is not None:
            return self.on_choose(pattern)
        if not self.open or self.top is None:
            return None
        where = self.find_entry(self.top, pattern)
        if where is None:
            self.close_dialogs()
            return None
        r, c = where
        self.top["row"], self.top["col"] = r, c
        return self.top["rows"][r][c]

    def close_dialogs(self) -> None:
        self.closed += 1
        self.open = False
        self.top = None

    def wait_dialog(self, seconds: float=3.0, want_open: bool=True) -> dict[str, Any] | None:
        if (self.open > 0) == want_open:
            return self.dialog()
        return None

    def bag_rows(self) -> list[dict[str, Any]]:
        return list(self.bag)

    def refresh_sheet(self, force: bool=False) -> dict[str, Any] | None:
        self.refresh_calls += 1
        return self.sheet

    def map_id(self) -> int:
        return self.map

    def me(self) -> dict[str, Any]:
        return {"x": self.pos[0], "y": self.pos[1]}

    def snap(self) -> dict[str, Any]:
        return {"self": self.me(), "map": {"id": self.map, "name": self.map_name}, "entities": []}

    def talk(self, name: str, at: tuple[float, float] | None=None, stand: tuple[float, float] | None=None) -> bool:
        self.talked.append((name, at))
        return self.talk_result

    def travel(self, mid: int) -> None:
        self.visited.append(mid)

    def wait_map_change(self, mid: int, seconds: float) -> bool:
        self.map_change_args = (mid, seconds)
        return self.map_change


@pytest.fixture
def gang_atlas(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeys = {1: "town", 4001: "gang", 4002: "gang"}

    def kind(mid: int):
        return monkeys.get(int(mid))

    labels = {1: "Somewhere (1)", 4001: "Our Gang Base (4001)", 4002: "Harvest Base (4002)"}
    monkeypatch.setattr(atlas, "kind", kind)
    monkeypatch.setattr(atlas, "label", stand_in(atlas.label, lambda mid: labels.get(int(mid), "%s (?)" % mid)))
    return monkeypatch


def visit_to(b: Bot, mid: int=4001, texts: tuple[str, ...]=("Enter the Outer hall of the gang",)) -> Callable[..., str]:
    def visit(bot: Bot, role: str | None, name: str | None = None, candidates: object = None) -> str:
        bot.visited.append((role, name, candidates))
        bot.map = mid
        bot.map_name = "Gang Base"
        bot.top = menu(list(texts))
        bot.open = True
        return "NPC"

    return visit


# ---------------------------------------------------------------------------------------- pure bits
def test_place_defaults_to_your_gang() -> None:
    assert crafting.place({}) == "gang"


def test_place_other_gang_by_name() -> None:
    assert crafting.place({"where": "other_gang", "gang_name": "  Harvest  "}) == "gang:Harvest"


def test_place_other_gang_without_a_name_is_your_gang() -> None:
    assert crafting.place({"where": "other_gang", "gang_name": "   "}) == "gang"


def test_place_city_passes_through() -> None:
    assert crafting.place({"where": "city"}) == "city"


def test_num_reads_a_number_defaults_and_casts() -> None:
    assert crafting._num(r"fee\s+(\d+)", "fee 2051 arnebia", 0) == 2051
    assert crafting._num(r"fee\s+(\d+)", "nothing here", 7) == 7
    assert crafting._num(r"([\d.]+)%", "5.0% success rate", None, float) == 5.0


def test_is_ticked_reads_selected_wording() -> None:
    assert crafting.is_ticked({"text": "not selected"}) is False
    assert crafting.is_ticked({"text": "selected"}) is True


def test_is_ticked_reads_the_command_number() -> None:
    assert crafting.is_ticked({"cmd": [1]}) is True
    assert crafting.is_ticked({"cmd": [0]}) is False
    assert crafting.is_ticked({"cmd": []}) is False
    assert crafting.is_ticked({}) is False


def test_is_ticked_not_selected_wins_over_a_set_command() -> None:
    assert crafting.is_ticked({"text": "not selected", "cmd": [1]}) is False


def test_colour_of_gems_and_shards() -> None:
    assert crafting.colour_of("Level 2 Topaz") == "Topaz"
    assert crafting.colour_of("Level 2 Topaz Shard") == "Topaz"
    assert crafting.colour_of("Level 5 Equipment Enhance Crystal") is None
    assert crafting.colour_of("") is None
    assert crafting.colour_of("Junk") is None


def test_shortfall_round_trip_without_protection() -> None:
    out, shards = crafting.shortfall({}, {}, 3, 1, False)
    assert out == {1: 16, 2: 4, 3: 1}
    assert shards == 64


def test_shortfall_round_trip_with_protection() -> None:
    out, shards = crafting.shortfall({}, {}, 3, 1, True)
    assert out == {1: 9, 2: 3, 3: 1}
    assert shards == 36


def test_shortfall_uses_what_is_already_in_the_bag() -> None:
    out, shards = crafting.shortfall({1: 5}, {1: 2}, 2, 1, False)
    assert out == {2: 1, 1: 0}
    assert shards == 0


def test_shortfall_of_nothing() -> None:
    out, shards = crafting.shortfall({}, {}, 2, 0, False)
    assert all(v == 0 for v in out.values())
    assert shards == 0


def test_spare_arnebia_keeps_the_reserve() -> None:
    assert crafting.spare_arnebia(as_bot(Bot(bag=[{"name": "Arnebia", "count": 25000}]))) == 5000


def test_fee_per_shard_is_a_quarter_of_the_gem_fee() -> None:
    b = Bot(profile={"gem_fees": {"level 2 topaz": 2108}})
    assert crafting.fee_per_shard(as_bot(b), "Level 2 Topaz Shard") == 527.0
    assert crafting.fee_per_shard(as_bot(b), "Not A Shard") is None
    assert crafting.fee_per_shard(as_bot(Bot()), "Level 2 Topaz Shard") is None


def test_fees_owed_sums_the_bag() -> None:
    b = Bot(bag=[{"name": "Level 2 Topaz Shard", "count": 4}], profile={"gem_fees": {"level 2 topaz": 400}})
    assert crafting.fees_owed(as_bot(b), ["Level 2 Topaz Shard"]) == 400.0


def test_gem_levels_skips_shards_and_other_colours() -> None:
    b = Bot(bag=[{"name": "Level 2 Topaz", "count": 4}, {"name": "Level 1 Topaz", "count": 3},
                 {"name": "Level 2 Topaz Shard", "count": 9}, {"name": "Level 1 Ruby", "count": 1}])
    assert crafting.gem_levels(as_bot(b), "topaz") == {1: 3, 2: 4}


def test_shard_levels_reads_only_shards() -> None:
    b = Bot(bag=[{"name": "Level 2 Topaz Shard", "count": 10}, {"name": "Level 1 Topaz Shard", "count": 3},
                 {"name": "Level 2 Topaz", "count": 4}])
    assert crafting.shard_levels(as_bot(b), "Topaz") == {1: 3, 2: 10}


def test_in_gang_by_map_kind(gang_atlas: pytest.MonkeyPatch) -> None:
    b = Bot()
    b.map = 4001
    assert crafting.in_gang(as_bot(b)) is True
    b.map = 1
    assert crafting.in_gang(as_bot(b)) is False


def test_in_gang_by_label_when_the_kind_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "kind", stand_in(atlas.kind, lambda mid: None))
    monkeypatch.setattr(atlas, "label", stand_in(atlas.label, lambda mid: "Harvest Gang Base (999)"))
    assert crafting.in_gang(as_bot(Bot())) is True
    monkeypatch.setattr(atlas, "label", stand_in(atlas.label, lambda mid: "A City (1)"))
    assert crafting.in_gang(as_bot(Bot())) is False


def test_entries_lists_rows_and_columns() -> None:
    b = Bot(top=menu(["a", "b"], ["c"]))
    got = [(r, c, e["options"][0]) for r, c, e in crafting.entries(as_bot(b))]
    assert got == [(0, 0, "a"), (0, 1, "b"), (1, 0, "c")]


def test_entry_like_finds_by_regex_case_insensitively() -> None:
    b = Bot(top=menu(["Level 2 Topaz 4"], ["OK to Synthesize"]))
    entry = crafting.entry_like(as_bot(b), r"^ok to synthesize$")
    assert entry is not None and entry["options"] == ["OK to Synthesize"]
    assert crafting.entry_like(as_bot(b), r"nothing") is None


def test_menu_text_joins_the_entry_texts() -> None:
    b = Bot(top=menu(["Level 2 Topaz 4"], ["fee 527 arnebia"]))
    assert crafting.menu_text(as_bot(b)) == "Level 2 Topaz 4 | fee 527 arnebia"


def test_storage_count_reads_the_amount_in_the_row() -> None:
    b = Bot(top=menu(["1; 20 Memory Butterfly"], ["3; 4 Topaz Shard"]))
    assert crafting.storage_count(as_bot(b), "Memory Butterfly") == 20
    assert crafting.storage_count(as_bot(b), "Topaz Shard") == 4
    assert crafting.storage_count(as_bot(b), "Nope") == 0


def test_recipe_and_crystal_counts() -> None:
    b = Bot(bag=[{"name": "Level 5 Equipment Enhance Crystal", "count": 3}],
            profile={"crystal_recipes": {"equipment 5": {"shards": 70, "fee": 2051}}})
    assert crafting._recipe(as_bot(b), "Equipment", 5) == {"shards": 70, "fee": 2051}
    assert crafting._recipe(as_bot(b), "Weapon", 5) is None
    assert crafting._crystals(as_bot(b), "Equipment", 5) == 3
    assert crafting._crystals(as_bot(b), "Weapon", 5) == 0


def test_item_row_reads_the_name_and_enhancement_level() -> None:
    b = Bot(top=menu(["Epic  Sniper Belt;rare | enhancement level: 10"], ["Back"]))
    e, level = crafting._item_row(as_bot(b), "sniper belt")
    assert e is not None and level == 10
    assert crafting._item_row(as_bot(b), "nothing") == (None, None)


# ---------------------------------------------------------------------------------------- reachable
def test_reachable_same_half_of_the_gang(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: as_kind(MapModel, OneRegion(lambda x, y: 0))))
    b = Bot()
    b.pos = (10.0, 10.0)
    assert crafting._reachable(as_bot(b), "forger") is True


def test_reachable_other_half_of_the_gang(monkeypatch: pytest.MonkeyPatch) -> None:
    spot = crafting.GANG_SPOTS["forger"][1]
    monkeypatch.setattr(atlas, "model",
                        stand_in(atlas.model, lambda mid: as_kind(MapModel, OneRegion(lambda x, y: 1 if (x, y) == spot else 0))))
    b = Bot()
    b.pos = (10.0, 10.0)
    assert crafting._reachable(as_bot(b), "forger") is False


def test_reachable_without_map_data_is_optimistic(monkeypatch: pytest.MonkeyPatch, allow_ignored_failures: None) -> None:
    def boom(mid: int):
        raise OSError("no data")

    monkeypatch.setattr(atlas, "model", boom)
    assert crafting._reachable(as_bot(Bot()), "forger") is True


# ---------------------------------------------------------------------------------------- _pick_gang
def test_pick_gang_types_the_name_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    states = iter([True, False])
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, lambda b, want_open, seconds: next(states)))
    typed: list[str] = []
    monkeypatch.setattr(town, "type_text", stand_in(town.type_text, lambda b, text: typed.append(text) or True))

    def choose(pattern: str):
        b.top = menu(["Harvest Team"])
        return entry("Harvest Team")

    b.on_choose = choose
    b.top = menu(["Harvest Team"])
    b.open = True
    assert crafting._pick_gang(as_bot(b), "Harvest") is True
    assert typed == ["Harvest"]


def test_pick_gang_gives_up_when_the_name_will_not_type(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, lambda b, want_open, seconds: True))
    monkeypatch.setattr(town, "type_text", stand_in(town.type_text, lambda b, text: False))
    assert crafting._pick_gang(as_bot(b), "Harvest") is False


def test_pick_gang_chooses_a_search_entry_then_the_gang(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(top=menu(["please enter the gang name"]))
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, lambda b, want_open, seconds: False))
    steps: list[str] = []

    def choose(pattern: str):
        steps.append(pattern)
        if "please enter" in pattern:
            b.top = menu(["Harvest Team"])
        return entry("x")

    b.on_choose = choose
    assert crafting._pick_gang(as_bot(b), "Harvest") is True
    assert len(steps) == 2


def test_pick_gang_turns_the_page_then_finds_it(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(top=menu(["next page"]))
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, lambda b, want_open, seconds: False))
    steps: list[str] = []

    def choose(pattern: str):
        steps.append(pattern)
        if "next" in pattern:
            b.top = menu(["Harvest Team"])
        return entry("x")

    b.on_choose = choose
    assert crafting._pick_gang(as_bot(b), "Harvest") is True
    assert len(steps) == 2


def test_pick_gang_with_no_way_on(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(top=menu(["Some other gang"]))
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, lambda b, want_open, seconds: False))
    assert crafting._pick_gang(as_bot(b), "Harvest") is False


# ---------------------------------------------------------------------------------------- enter_gang
def test_enter_gang_already_there_and_reachable(gang_atlas: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    b.map = 4001
    monkeypatch.setattr(crafting, "_reachable", stand_in(crafting._reachable, lambda b, role: True))
    assert crafting.enter_gang(as_bot(b), None, "forger") is True
    assert b.visited == []


def test_enter_gang_walks_in_from_outside(gang_atlas: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(town, "visit", visit_to(b))
    monkeypatch.setattr(crafting, "_reachable", stand_in(crafting._reachable, lambda b, role: True))
    assert crafting.enter_gang(as_bot(b), None, "forger") is True
    assert b.map == 4001
    assert any("in Gang Base" in line for line in b.lines)


def test_enter_gang_needs_the_map_to_change(gang_atlas: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(town, "visit", visit_to(b))
    monkeypatch.setattr(crafting, "_reachable", stand_in(crafting._reachable, lambda b, role: True))
    b.map_change = False
    assert crafting.enter_gang(as_bot(b), None, "forger") is False


def test_enter_gang_landing_in_the_inner_hall(gang_atlas: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(town, "visit", visit_to(b))
    monkeypatch.setattr(crafting, "_reachable", stand_in(crafting._reachable, lambda b, role: False))
    assert crafting.enter_gang(as_bot(b), None, "forger") is False
    assert any("walled off" in line for line in b.lines)


def test_enter_gang_landing_somewhere_that_is_not_a_gang(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "kind", stand_in(atlas.kind, lambda mid: "town"))
    monkeypatch.setattr(atlas, "label", stand_in(atlas.label, lambda mid: "Somewhere (1)"))
    b = Bot()
    monkeypatch.setattr(town, "visit", visit_to(b, mid=1))
    assert crafting.enter_gang(as_bot(b), None, "forger") is False


def test_enter_gang_gives_up_when_the_list_has_no_gang(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(town, "visit", visit_to(b, texts=("Choose a service",)))
    monkeypatch.setattr(crafting, "in_gang", stand_in(crafting.in_gang, lambda b: False))
    assert crafting.enter_gang(as_bot(b), "Harvest", "forger") is False
    assert any("lists no gang called Harvest" in line for line in b.lines)


def test_enter_gang_named_through_all_gangs(gang_atlas: pytest.MonkeyPatch, monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(town, "visit", visit_to(b, texts=("All gangs",)))
    monkeypatch.setattr(crafting, "in_gang", stand_in(crafting.in_gang, lambda b: True))
    monkeypatch.setattr(crafting, "_reachable", stand_in(crafting._reachable, lambda b, role: True))
    monkeypatch.setattr(crafting, "_pick_gang", stand_in(crafting._pick_gang, lambda b, gang: True))

    def wait_dialog(seconds: float=2.0, want_open: bool=True):
        b.top = menu(["Enter the Outer hall of the gang"])
        return b.dialog()

    b.wait_dialog = wait_dialog
    assert crafting.enter_gang(as_bot(b), "Harvest", "forger") is True


# ---------------------------------------------------------------------------------------- open_craftsman
def test_open_craftsman_your_gang(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(crafting, "enter_gang", stand_in(crafting.enter_gang, lambda b, gang, role: True))
    monkeypatch.setattr(knowledge, "service_npcs", stand_in(knowledge.service_npcs, lambda role: []))
    assert crafting.open_craftsman(as_bot(b), "gem_craftsman", "gang") == "gang"
    assert b.talked == [("Gem Craftsman", (59, 93))]


def test_open_craftsman_falls_back_to_the_city(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(crafting, "enter_gang", stand_in(crafting.enter_gang, lambda b, gang, role: False))
    monkeypatch.setattr(knowledge, "service_npcs", stand_in(knowledge.service_npcs, lambda role: [(1, "Gem Craftsman", 5, 5)]))
    monkeypatch.setattr(atlas, "kind", stand_in(atlas.kind, lambda mid: "town"))
    monkeypatch.setattr(town, "visit", stand_in(town.visit, lambda b, role, name=None, candidates=None: None))
    assert crafting.open_craftsman(as_bot(b), "gem_craftsman", "gang") == "city"
    assert any("couldn't reach your gang's Gem Craftsman" in line for line in b.lines)


def test_open_craftsman_named_gang_talk_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    b.talk_result = False
    monkeypatch.setattr(crafting, "enter_gang", stand_in(crafting.enter_gang, lambda b, gang, role: True))
    monkeypatch.setattr(knowledge, "service_npcs", stand_in(knowledge.service_npcs, lambda role: []))
    monkeypatch.setattr(atlas, "kind", stand_in(atlas.kind, lambda mid: "town"))
    monkeypatch.setattr(town, "visit", stand_in(town.visit, lambda b, role, name=None, candidates=None: None))
    assert crafting.open_craftsman(as_bot(b), "forger", "gang:Harvest") == "city"
    assert any("of the gang Harvest" in line for line in b.lines)


def test_open_craftsman_reraises_a_stop_when_the_run_is_stopping(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    b.stop_event.set()

    def boom(bot: Bot, gang: str | None = None, role: str = "gem_craftsman") -> bool:
        raise Stopped("stopped")

    monkeypatch.setattr(crafting, "enter_gang", boom)
    with pytest.raises(Stopped):
        crafting.open_craftsman(as_bot(b), "forger", "gang")


def test_open_craftsman_swallows_a_stop_that_is_not_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(crafting, "enter_gang", stand_in(crafting.enter_gang, lambda b, gang, role: (_ for _ in ()).throw(Stopped("nope"))))
    monkeypatch.setattr(knowledge, "service_npcs", stand_in(knowledge.service_npcs, lambda role: []))
    monkeypatch.setattr(town, "visit", stand_in(town.visit, lambda b, role, name=None, candidates=None: None))
    assert crafting.open_craftsman(as_bot(b), "forger", "gang") == "city"


def test_open_craftsman_reraises_a_final_stop_even_without_the_stop_event(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(crafting, "enter_gang",
                        stand_in(crafting.enter_gang, lambda b, gang, role: (_ for _ in ()).throw(Stopped("can't read", final=True))))
    with pytest.raises(Stopped):
        crafting.open_craftsman(as_bot(b), "forger", "gang")


# ---------------------------------------------------------------------------------------- tick
def test_tick_already_set_needs_no_choice() -> None:
    e = entry("add 1 Level 2 Topaz Shard", cmd=1)
    b = Bot(top=menu([e]))
    assert crafting.tick(as_bot(b), r"add 1 .*Shard", True) is True
    assert b.chooses == []


def test_tick_toggles_the_entry_with_choose() -> None:
    e = entry("add 1 Level 2 Topaz Shard", cmd=0)
    b = Bot(top=menu([e]))

    def choose(pattern: str):
        e["cmd"][-1] = 1
        return e

    b.on_choose = choose
    assert crafting.tick(as_bot(b), r"add 1 .*Shard", True) is True


def test_tick_falls_back_to_the_space_key() -> None:
    e = entry("add 1 Level 2 Topaz Shard", cmd=0)
    b = Bot(top=menu([e]))

    def press_space(k: str) -> None:
        if k == "space":
            e["cmd"][-1] = 1

    b.on_key = press_space
    assert crafting.tick(as_bot(b), r"add 1 .*Shard", True) is True
    assert b.keys == ["space"]


def test_tick_missing_entry_is_false() -> None:
    b = Bot(top=menu(["something else"]))
    assert crafting.tick(as_bot(b), r"add 1 .*Shard", True) is False


def test_tick_still_not_wanted_is_false() -> None:
    e = {"text": "not selected", "cmd": [0]}
    b = Bot(top=menu([e]))
    assert crafting.tick(as_bot(b), r"rune", True) is False


# ---------------------------------------------------------------------------------------- storage
def test_take_from_storage_with_nothing_to_take() -> None:
    b = Bot()
    assert crafting.take_from_storage(as_bot(b), "Shard", 0) == 0
    assert b.visited == []


def test_take_from_storage_check_only_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(town, "visit", visit_storehouse(b, item_count=20))
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, lambda b, pattern: True))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, failing("check only must not withdraw")))
    assert crafting.take_from_storage(as_bot(b), "Memory Butterfly", 5, check_only=True) == 0
    assert any("the storehouse has 20 Memory Butterfly" in line for line in b.lines)


def test_take_from_storage_takes_and_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(town, "visit", visit_storehouse(b, item_count=20))
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, lambda b, pattern: True))

    def pick(bot: Bot, pattern: str, quantity: int | None = None) -> bool:
        add(bot, "Memory Butterfly", quantity or 0)
        return True

    monkeypatch.setattr(town, "pick", pick)
    assert crafting.take_from_storage(as_bot(b), "Memory Butterfly", 5) == 5
    assert any("took 5 Memory Butterfly out of the storehouse" in line for line in b.lines)


def test_take_from_storage_without_the_section(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(town, "visit", stand_in(town.visit, lambda b, role, name=None, candidates=None: None))
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, lambda b, pattern: False))
    assert crafting.take_from_storage(as_bot(b), "Memory Butterfly", 5) == 0


def test_take_from_storage_when_the_store_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(town, "visit", visit_storehouse(b, item_count=0))
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, lambda b, pattern: True))
    assert crafting.take_from_storage(as_bot(b), "Memory Butterfly", 5) == 0


def visit_storehouse(b: Bot, item_count: int) -> Callable[..., str | None]:
    def visit(bot: Bot, role: str | None, name: str | None = None, candidates: object = None) -> str | None:
        bot.visited.append(role)
        bot.top = menu(["item storage"], ["%d; %d Memory Butterfly" % (1, item_count)])
        bot.open = True

    return visit


# ---------------------------------------------------------------------------------------- buying shards
def shard_market(price: int, shard: str="Level 1 Obsidian Shard") -> dict[str, Any]:
    return menu([entry("%s; Unit price %d arnebia" % (shard, price))])


def market_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "shard_catalog", stand_in(town.shard_catalog, lambda: ({}, {1: [5, 5]})))
    monkeypatch.setattr(town, "market_candidates", stand_in(town.market_candidates, lambda markets: [(1, "Shard Broker", 5, 5)]))
    monkeypatch.setattr(town, "visit", stand_in(town.visit, lambda b, role, name=None, candidates=None: None))


def test_buy_shards_nothing_to_buy() -> None:
    b = Bot()
    assert crafting.buy_shards(as_bot(b), "Level 1 Obsidian Shard", 0) == 0


def test_buy_shards_without_a_market(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(town, "shard_catalog", stand_in(town.shard_catalog, lambda: ({}, {})))
    assert crafting.buy_shards(as_bot(b), "Level 1 Obsidian Shard", 5) == 0
    assert any("don't know where a Shard Market is" in line for line in b.lines)


def test_buy_shards_the_market_does_not_sell_it(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    market_env(monkeypatch)
    b.top = menu(["nothing useful"])
    b.open = True
    assert crafting.buy_shards(as_bot(b), "Level 1 Obsidian Shard", 5) == 0
    assert any("doesn't sell" in line for line in b.lines)


def test_buy_shards_check_only_reports_the_price(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    market_env(monkeypatch)
    b.top = shard_market(100)
    b.open = True
    assert crafting.buy_shards(as_bot(b), "Level 1 Obsidian Shard", 5, check_only=True) == 0
    assert any("sells Level 1 Obsidian Shard at 100 arnebia each" in line for line in b.lines)


def test_buy_shards_respects_the_price_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    market_env(monkeypatch)
    b.top = shard_market(150)
    b.open = True
    assert crafting.buy_shards(as_bot(b), "Level 1 Obsidian Shard", 5, max_price=100) == 0
    assert any("more than your limit of 100" in line for line in b.lines)


def test_buy_shards_the_fee_limits_the_quantity(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 30000}], profile={"gem_fees": {"level 1 obsidian": 400}})
    market_env(monkeypatch)
    b.top = shard_market(100)
    b.open = True
    picked: list[int | None] = []

    def pick(bot: Bot, pattern: str, quantity: int | None = None) -> bool:
        picked.append(quantity)
        add(bot, "Level 1 Obsidian Shard", quantity or 0)
        return True

    monkeypatch.setattr(town, "pick", pick)
    assert crafting.buy_shards(as_bot(b), "Level 1 Obsidian Shard", 100) == 50
    assert picked == [50]


def test_buy_shards_unknown_fee_leaves_a_fifth(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = Bot(bag=[{"name": "Arnebia", "count": 30000}])
    market_env(monkeypatch)
    tb.top = shard_market(100)
    tb.open = True
    picked: list[int | None] = []
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity: picked.append(quantity) or add(tb, "Level 1 Obsidian Shard", quantity or 0) or True))
    assert crafting.buy_shards(as_bot(tb), "Level 1 Obsidian Shard", 100) == 80
    assert picked == [80]


def test_buy_shards_not_enough_spare(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 10000}], profile={"gem_fees": {"level 1 obsidian": 0}})
    market_env(monkeypatch)
    b.top = shard_market(100)
    b.open = True
    assert crafting.buy_shards(as_bot(b), "Level 1 Obsidian Shard", 5) == 0
    assert any("not enough spare arnebia" in line for line in b.lines)


def test_buy_shards_in_batches_of_two_thousand(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = Bot()
    market_env(monkeypatch)
    tb.top = shard_market(0)
    tb.open = True
    picked: list[int | None] = []
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity: picked.append(quantity) or add(tb, "Level 1 Obsidian Shard", quantity or 0) or True))
    assert crafting.buy_shards(as_bot(tb), "Level 1 Obsidian Shard", 2500) == 2500
    assert picked == [2000, 500]


# ---------------------------------------------------------------------------------------- fetch_shards
def test_fetch_shards_storage_then_market(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(crafting, "take_from_storage", stand_in(crafting.take_from_storage, lambda b, item, n, check_only: calls.append(("store", n)) or 2))
    monkeypatch.setattr(crafting, "buy_shards", stand_in(crafting.buy_shards, lambda b, shard, n, max_price, check_only, owed=0: calls.append(("buy", n)) or 3))
    assert crafting.fetch_shards(as_bot(b), "S", 10, "both") == 5
    assert calls == [("store", 10), ("buy", 8)]


def test_fetch_shards_storage_only(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(crafting, "take_from_storage", stand_in(crafting.take_from_storage, lambda b, item, n, check_only: n))
    monkeypatch.setattr(crafting, "buy_shards", stand_in(crafting.buy_shards, failing("should not buy")))
    assert crafting.fetch_shards(as_bot(b), "S", 10, "storage") == 10


def test_fetch_shards_buy_only(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(crafting, "take_from_storage", stand_in(crafting.take_from_storage, failing("should not withdraw")))
    monkeypatch.setattr(crafting, "buy_shards", stand_in(crafting.buy_shards, lambda b, shard, n, max_price, check_only, owed=0: n))
    assert crafting.fetch_shards(as_bot(b), "S", 10, "buy") == 10


def test_fetch_shards_needs_no_market_when_the_store_fills_it(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(crafting, "take_from_storage", stand_in(crafting.take_from_storage, lambda b, item, n, check_only: n))
    monkeypatch.setattr(crafting, "buy_shards", stand_in(crafting.buy_shards, failing("should not buy")))
    assert crafting.fetch_shards(as_bot(b), "S", 10, "both") == 10


def test_fetch_shards_keeps_back_the_fees_of_the_chosen_shards(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(crafting, "take_from_storage", stand_in(crafting.take_from_storage, lambda b, item, n, check_only: 0))
    seen: dict[str, float] = {}
    monkeypatch.setattr(crafting, "fees_owed", stand_in(crafting.fees_owed, lambda b, shards: 77))
    monkeypatch.setattr(crafting, "buy_shards", stand_in(crafting.buy_shards, lambda b, shard, n, max_price, check_only, owed=0: seen.update(owed=owed) or n))
    crafting.fetch_shards(as_bot(b), "S", 10, "buy", chosen=["A", "B"])
    assert seen["owed"] == 77


# ---------------------------------------------------------------------------------------- room / fill
def test_room_for_needs_a_load_limit() -> None:
    b = Bot(profile={"bag_full_at": 100})
    b.sheet = {"load": 5, "maxload": 0}
    assert crafting.room_for(as_bot(b), "S", 1.0) is None


def test_room_for_needs_a_known_weight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bagrules, "weights", stand_in(bagrules.weights, lambda: {}))
    b = Bot(profile={"bag_full_at": 100})
    b.sheet = {"load": 0, "maxload": 1000}
    assert crafting.room_for(as_bot(b), "S", 1.0) is None


def test_room_for_computes_the_share(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bagrules, "weights", stand_in(bagrules.weights, lambda: {"s": 0.5}))
    b = Bot(profile={"bag_full_at": 100})
    b.sheet = {"load": 100, "maxload": 1000}
    assert crafting.room_for(as_bot(b), "S", 1.0) == 1800
    assert crafting.room_for(as_bot(b), "S", 0.5) == 900


def test_room_for_is_capped_at_two_thousand(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bagrules, "weights", stand_in(bagrules.weights, lambda: {"s": 0.1}))
    b = Bot(profile={"bag_full_at": 100})
    b.sheet = {"load": 0, "maxload": 1000}
    assert crafting.room_for(as_bot(b), "S", 1.0) == 2000


def test_fill_with_a_known_weight(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(crafting, "room_for", stand_in(crafting.room_for, lambda b, shard, share: 5))
    monkeypatch.setattr(crafting, "fetch_shards", stand_in(crafting.fetch_shards, lambda b, shard, n, supply, max_price=0, check_only=False, chosen=(): n))
    assert crafting.fill_with(as_bot(b), "S", 1.0, "buy", 0) == 5


def test_fill_with_learns_the_weight_from_the_load(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    rooms = iter([None, 5])
    monkeypatch.setattr(crafting, "room_for", stand_in(crafting.room_for, lambda b, shard, share: next(rooms)))
    loads = iter([100.0, 103.0, 103.0])
    monkeypatch.setattr(town, "load_now", stand_in(town.load_now, lambda b: next(loads)))
    monkeypatch.setattr(crafting, "fetch_shards", stand_in(crafting.fetch_shards, lambda b, shard, n, supply, max_price=0, check_only=False, chosen=(): n))
    learned: list[tuple[str, float | None, float | None, int]] = []
    monkeypatch.setattr(bagrules, "learn_weight", stand_in(bagrules.learn_weight, lambda name, load_before, load_after, n: learned.append((name, load_before, load_after, n))))
    assert crafting.fill_with(as_bot(b), "S", 1.0, "buy", 0) == 25
    assert learned == [("S", 103.0, 100.0, 20)]


# ---------------------------------------------------------------------------------------- make_crystals
def crystal_menus(kind: str="Equipment", level: int=5, need: int=70, fee: int=2051) -> dict[str, Any]:
    return menu(["shard synthesis"], ["%s Enhance Crystal Shard 100" % kind],
                ["synthesize Level %d %s Enhance Crystal" % (level, kind)],
                ["synthesize 1 Level %d %s Enhance Crystal needs %d %s Enhance Crystal Shard; fee %d arnebia"
                 % (level, kind, need, kind, fee)])


def test_make_crystals_records_the_recipe_and_makes(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Equipment Enhance Crystal Shard", "count": 200},
                 {"name": "Arnebia", "count": 100000}])
    b.top = crystal_menus()
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))

    def pick(bot: Bot, pattern: str, quantity: int | None = None) -> bool:
        add(bot, "Level 5 Equipment Enhance Crystal", quantity or 0)
        return True

    monkeypatch.setattr(town, "pick", pick)
    assert crafting.make_crystals(as_bot(b), "Equipment", 5, 2, "gang") == 2
    assert b.p["crystal_recipes"]["equipment 5"] == {"shards": 70, "fee": 2051}
    assert any("made 2 Level 5 Equipment Enhance Crystals" in line for line in b.lines)


def test_make_crystals_not_enough_shards(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Equipment Enhance Crystal Shard", "count": 10},
                 {"name": "Arnebia", "count": 100000}])
    b.top = crystal_menus()
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    assert crafting.make_crystals(as_bot(b), "Equipment", 5, 2, "gang") == 0
    assert any("not enough shards or spare arnebia" in line for line in b.lines)


def test_make_crystals_without_the_shard_synthesis_section(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    b.top = menu(["no such thing"])
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    assert crafting.make_crystals(as_bot(b), "Equipment", 5, 2, "gang") == 0


def test_make_crystals_without_the_shard_row(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    b.top = menu(["shard synthesis"], ["some other shard 3"])
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    assert crafting.make_crystals(as_bot(b), "Equipment", 5, 2, "gang") == 0
    assert any("no Equipment Enhance Crystal Shards" in line for line in b.lines)


def test_stock_crystals_nothing_short() -> None:
    b = Bot(bag=[{"name": "Level 5 Equipment Enhance Crystal", "count": 5}])
    assert crafting.stock_crystals(as_bot(b), {}, "Equipment", 5, 3, "gang") == 0


def test_stock_crystals_with_a_known_recipe(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Equipment Enhance Crystal Shard", "count": 400}],
            profile={"crystal_recipes": {"equipment 5": {"shards": 70, "fee": 100}}})
    monkeypatch.setattr(crafting, "fetch_shards", stand_in(crafting.fetch_shards, failing("should not fetch")))
    monkeypatch.setattr(crafting, "make_crystals", stand_in(crafting.make_crystals, lambda b, kind, level, n, where: 5))
    assert crafting.stock_crystals(as_bot(b), {}, "Equipment", 5, 5, "gang") == 5


def test_stock_crystals_learns_the_recipe_first(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    fetched: list[int] = []
    monkeypatch.setattr(crafting, "fetch_shards", stand_in(crafting.fetch_shards, lambda b, shard, n, supply, max_price=0, check_only=False, chosen=(): fetched.append(n) or 0))
    monkeypatch.setattr(crafting, "room_for", stand_in(crafting.room_for, lambda b, shard, share: None))
    calls = {"n": 0}

    def make_crystals(bot: Bot, kind: str, level: int, n: int, where: str) -> int:
        calls["n"] += 1
        bot.p.setdefault("crystal_recipes", {})["equipment 5"] = {"shards": 70, "fee": 100}
        return 0 if calls["n"] == 1 else 3

    monkeypatch.setattr(crafting, "make_crystals", make_crystals)
    assert crafting.stock_crystals(as_bot(b), {}, "Equipment", 5, 5, "gang") == 3
    assert fetched == [5, 350]


# ---------------------------------------------------------------------------------------- raise_gems
def raise_menu(colour: str="Topaz", level: int=2, fee: int=527) -> dict[str, Any]:
    return menu(["Batch gem synthesis"], ["gem synthesis"],
                ["Level %d %s 15" % (level, colour)],
                ["It takes 3 Level %d %s and cost %d arnebia to synthesize each Level %d %s"
                 % (level, colour, fee, level + 1, colour)],
                ["Automatically use Level %d %s Shard in the backpack to increase the success rate" % (level, colour)],
                ["add 1 Level %d %s Shard" % (level, colour)],
                ["use 1 Gem Protection Rune"], ["OK to Synthesize"])


def test_raise_gems_check_only_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(top=raise_menu())
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 4, True, False, "gang", check_only=True) == (0, 0)
    assert any("would combine 4 x 3 Level 2 Topaz into Level 3 Topaz" in line for line in b.lines)


def test_raise_gems_batch_makes_and_loses(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 100000}])
    b.top = raise_menu()
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))

    def pick(bot: Bot, pattern: str, quantity: int | None = None) -> bool:
        add(bot, "Level 3 Topaz", 1)
        return True

    monkeypatch.setattr(town, "pick", pick)
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 3, False, False, "gang") == (1, 2)
    assert any("3 tries, 1 Level 3 Topaz made, 2 failed" in line for line in b.lines)


def test_raise_gems_fee_caps_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 20527}])
    b.top = raise_menu(fee=527)
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    picked: list[int | None] = []
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity: picked.append(quantity) or True))
    crafting.raise_gems(as_bot(b), "Topaz", 2, 10, False, False, "gang")
    assert picked == [1]


def test_raise_gems_stops_when_no_spare_arnebia(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 0}])
    b.top = raise_menu()
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 3, False, False, "gang") == (0, 0)
    assert any("not enough spare arnebia to raise" in line for line in b.lines)


def test_raise_gems_without_the_section(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(top=menu(["nothing"]))
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 3, False, False, "gang") == (0, 0)


def test_raise_gems_protected_one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 100000}])
    b.top = raise_menu()
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    monkeypatch.setattr(crafting, "tick", stand_in(crafting.tick, lambda b, pattern, want=True: True))
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 2, False, True, "gang") == (0, 2)
    assert any("OK to Synthesize" in c for c in b.chooses)


def test_raise_gems_protected_without_a_rune(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 100000}])
    b.top = raise_menu()
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))

    def tick(bot: Bot, pattern: str, want: bool = True) -> bool:
        return "Protection Rune" not in pattern

    monkeypatch.setattr(crafting, "tick", tick)
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 2, False, True, "gang") == (0, 0)
    assert any("no Gem Protection Rune to use" in line for line in b.lines)


# ---------------------------------------------------------------------------------------- gem_maker
def test_gem_maker_without_a_colour_stops() -> None:
    with pytest.raises(Stopped):
        crafting.gem_maker(as_bot(Bot()), {}, check_only=False)


def test_gem_maker_mode_bag_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crafting, "make_most", stand_in(crafting.make_most, lambda b, cfg, check_only: "made"))
    assert crafting.gem_maker(as_bot(Bot()), {"mode": "bag"}, check_only=False) == "made"


def test_gem_maker_check_only_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Level 1 Topaz Shard", "count": 8}])
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    crafting.gem_maker(as_bot(b), {"colour": "Topaz", "level": 2, "count": 1, "amount": 1, "supply": "none"}, check_only=True)
    assert any("check only" in line for line in b.lines)
    assert any("would take about" in line for line in b.lines)


def test_gem_maker_makes_the_target(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Level 1 Topaz", "count": 3}])
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))

    def raise_gems(bot: Bot, colour: str, level: int, k: int, boost: bool, protect: bool, where: str,
                   check_only: bool = False) -> tuple[int, int]:
        add(bot, "Level 2 Topaz", 1)
        for r in bot.bag:
            if r["name"] == "Level 1 Topaz":
                r["count"] = 0
        return (1, 0)

    monkeypatch.setattr(crafting, "raise_gems", raise_gems)
    crafting.gem_maker(as_bot(b), {"colour": "Topaz", "level": 2, "count": 1, "amount": 1,
                           "supply": "none", "where": "gang"})
    assert any("done: made 1 Level 2 Topaz" in line for line in b.lines)


def test_gem_maker_fetches_shards_when_the_bag_is_short(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    monkeypatch.setattr(crafting, "room_for", stand_in(crafting.room_for, lambda b, shard, share: None))
    fetched: list[tuple[str, int]] = []
    monkeypatch.setattr(crafting, "fetch_shards", stand_in(crafting.fetch_shards, lambda b, shard, n, supply, max_price=0, check_only=False, chosen=(): fetched.append((shard, n)) or 0))
    crafting.gem_maker(as_bot(b), {"colour": "Topaz", "level": 1, "count": 1, "amount": 1,
                           "supply": "buy", "where": "gang"})
    assert fetched and fetched[0][0] == "Level 1 Topaz Shard"
    assert any("made 0 of the 1 Level 1 Topaz" in line for line in b.lines)


def test_gem_maker_most_runs_to_the_end(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    crafting.gem_maker(as_bot(b), {"colour": "Topaz", "level": 1, "amount": "most", "supply": "none", "where": "gang"})
    assert any("made 0 Level 1 Topaz" in line for line in b.lines)


# ---------------------------------------------------------------------------------------- make_most
def test_make_most_check_only_lists_colours(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Level 1 Topaz Shard", "count": 8}, {"name": "Level 2 Topaz", "count": 1}])
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    monkeypatch.setattr(town, "make_gems", stand_in(town.make_gems, failing("check only must not spend")))
    crafting.make_most(as_bot(b), {"where": "gang", "up_to": 5, "keep": 0}, check_only=True)
    assert any(line.startswith("Topaz: shards") for line in b.lines)
    assert any("nothing was made (check only)" in line for line in b.lines)


def test_make_most_with_nothing_to_do() -> None:
    b = Bot()
    crafting.make_most(as_bot(b), {"where": "gang", "up_to": 5, "keep": 0})
    assert any("as high as they go" in line for line in b.lines)


def test_make_most_turns_shards_into_gems(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Level 1 Topaz Shard", "count": 8}])
    made: list[dict[str, int]] = []

    def make_gems(bot: Bot, wanted: dict[str, int], opener: Callable[[], object] | None = None) -> int:
        made.append(dict(wanted))
        for r in bot.bag:
            if r["name"] == "Level 1 Topaz Shard":
                r["count"] = 0
        return 2

    monkeypatch.setattr(town, "make_gems", make_gems)
    crafting.make_most(as_bot(b), {"where": "gang", "up_to": 3, "keep": 0, "colours": []})
    assert made and "level 1 topaz shard" in made[0]


# ---------------------------------------------------------------------------------------- enhance
def test_enhance_no_enhance_equipment_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(top=menu(["forging something else"]))
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    crafting.enhance(as_bot(b), {"items": ["Sniper Belt"], "where": "gang"})
    assert any("has no enhance equipment" in line for line in b.lines)


def test_enhance_item_not_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(top=menu(["enhance equipment"], ["Epic Other Thing; enhancement level: 5"]))
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    crafting.enhance(as_bot(b), {"items": ["Sniper Belt"], "where": "gang"})
    assert any("didn't list Sniper Belt" in line for line in b.lines)


def test_enhance_item_already_at_the_target(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(top=menu(["enhance equipment"], ["Epic Sniper Belt | enhancement level: 12"]))
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    crafting.enhance(as_bot(b), {"items": ["Sniper Belt"], "where": "gang", "up_to": 12})
    assert any("Sniper Belt is +12: done" in line for line in b.lines)


def test_enhance_check_only_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(top=menu(["enhance equipment"],
                     ["Epic Sniper Belt;rare | enhancement level: 5"],
                     ["forging Epic Sniper Belt;rare , Enhancement level increased from 5 to 6, requires 1 Level 5 Equipment Enhance Crystal, costs 9523 arnebia"],
                     ["5.0% success rate"],
                     ["Use 11 Equipment Enhance Protection Rune; not selected"]))
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    fetched: list[bool] = []
    monkeypatch.setattr(crafting, "fetch_shards",
                        stand_in(crafting.fetch_shards, lambda b, shard, n, supply, max_price=0, check_only=False, chosen=():
                                 fetched.append(check_only) or 0))
    crafting.enhance(as_bot(b), {"items": ["Sniper Belt"], "where": "gang", "up_to": 12,
                         "make_crystals": True, "crystal_supply": "both"}, check_only=True)
    assert any("Sniper Belt: +5 to +6 needs 1 Level 5 Equipment Enhance Crystal" in line for line in b.lines)
    assert fetched == [True]
    assert "start forging" not in b.chooses


def test_enhance_stops_without_spare_arnebia(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 1000}])
    b.top = menu(["enhance equipment"], ["Epic Sniper Belt | enhancement level: 5"],
                 ["requires 1 Level 5 Equipment Enhance Crystal, costs 9523 arnebia"])
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    crafting.enhance(as_bot(b), {"items": ["Sniper Belt"], "where": "gang", "up_to": 12})
    assert any("not enough spare arnebia to enhance" in line for line in b.lines)


def test_enhance_success_moves_on(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 100000},
                 {"name": "Level 5 Equipment Enhance Crystal", "count": 10}])
    b.choose = lambda pattern: entry("x")
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    b.dialog = lambda: {"ok": True, "open": 1, "top": None}
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    levels = iter([(entry("x"), 5), (entry("x"), 6), (entry("x"), 6)])
    monkeypatch.setattr(crafting, "_item_row", stand_in(crafting._item_row, lambda b, name: next(levels)))
    monkeypatch.setattr(crafting, "tick", stand_in(crafting.tick, lambda b, pattern, want=True: True))
    monkeypatch.setattr(crafting, "entry_like", stand_in(crafting.entry_like, lambda b, pattern: None))
    text = "forging Sniper Belt, Enhancement level increased from 5 to 6, requires 1 Level 5 Equipment Enhance Crystal, costs 100 arnebia"
    monkeypatch.setattr(crafting, "menu_text", stand_in(crafting.menu_text, lambda b: text))
    crafting.enhance(as_bot(b), {"items": ["Sniper Belt"], "where": "gang", "up_to": 6, "make_crystals": False})
    assert any("Sniper Belt: +6, success" in line for line in b.lines)


def test_enhance_gives_up_after_the_fail_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Level 5 Equipment Enhance Crystal", "count": 10}])
    b.choose = lambda pattern: entry("x")
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    b.dialog = lambda: {"ok": True, "open": 1, "top": None}
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    monkeypatch.setattr(crafting, "_item_row", stand_in(crafting._item_row, lambda b, name: (entry("x"), 4)))
    monkeypatch.setattr(crafting, "entry_like", stand_in(crafting.entry_like, lambda b, pattern: None))
    text = "Enhancement level increased from 10 to 11, requires 1 Level 5 Equipment Enhance Crystal"
    monkeypatch.setattr(crafting, "menu_text", stand_in(crafting.menu_text, lambda b: text))
    crafting.enhance(as_bot(b), {"items": ["Sniper Belt"], "where": "gang", "up_to": 12, "make_crystals": False,
                         "protect": "never", "max_fails": 2})
    assert any("2 failures in a row - stopping Sniper Belt" in line for line in b.lines)


def test_enhance_stops_without_protection_runes(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Level 5 Equipment Enhance Crystal", "count": 10}])
    b.choose = lambda pattern: entry("x")
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    b.dialog = lambda: {"ok": True, "open": 1, "top": None}
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    monkeypatch.setattr(crafting, "_item_row", stand_in(crafting._item_row, lambda b, name: (entry("x"), 5)))
    monkeypatch.setattr(crafting, "entry_like", stand_in(crafting.entry_like, lambda b, pattern: None))
    text = ("Enhancement level increased from 5 to 6, requires 1 Level 5 Equipment Enhance Crystal, "
            "Use 11 Equipment Enhance Protection Rune")
    monkeypatch.setattr(crafting, "menu_text", stand_in(crafting.menu_text, lambda b: text))
    crafting.enhance(as_bot(b), {"items": ["Sniper Belt"], "where": "gang", "up_to": 12, "make_crystals": False,
                         "protect": "always"})
    assert any("not enough protection runes" in line for line in b.lines)


def test_enhance_stops_when_the_rune_will_not_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Level 5 Equipment Enhance Crystal", "count": 10},
                 {"name": "Equipment Enhance Protection Rune", "count": 20}])
    b.choose = lambda pattern: entry("x")
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    b.dialog = lambda: {"ok": True, "open": 1, "top": None}
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    monkeypatch.setattr(crafting, "_item_row", stand_in(crafting._item_row, lambda b, name: (entry("x"), 5)))
    monkeypatch.setattr(crafting, "entry_like", stand_in(crafting.entry_like, lambda b, pattern: None))
    monkeypatch.setattr(crafting, "tick", stand_in(crafting.tick, lambda b, pattern, want=True: False))
    text = ("Enhancement level increased from 5 to 6, requires 1 Level 5 Equipment Enhance Crystal, "
            "Use 11 Equipment Enhance Protection Rune")
    monkeypatch.setattr(crafting, "menu_text", stand_in(crafting.menu_text, lambda b: text))
    crafting.enhance(as_bot(b), {"items": ["Sniper Belt"], "where": "gang", "up_to": 12, "make_crystals": False,
                         "protect": "always"})
    assert any("couldn't tick the protection rune" in line for line in b.lines)


# ---------------------------------------------------------------------------------------- spending safety
def test_raise_gems_check_only_never_picks(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(top=raise_menu())
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, failing("check only must not spend")))
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 3, False, False, "gang", check_only=True) == (0, 0)
    assert "OK to Synthesize" not in b.chooses


def test_raise_gems_batch_pick_failure_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 100000}], top=raise_menu())
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, returning(False)))
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 3, False, False, "gang") == (0, 0)
    assert any("didn't ask how many" in line for line in b.lines)


def test_raise_gems_ticks_the_boost_shard_in_a_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 100000},
                 {"name": "Level 2 Topaz Shard", "count": 1}], top=raise_menu())
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    ticks: list[tuple[str, bool]] = []
    monkeypatch.setattr(crafting, "tick", stand_in(crafting.tick, lambda b, pattern, want=True: ticks.append((pattern, want)) or True))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, returning(True)))
    crafting.raise_gems(as_bot(b), "Topaz", 2, 1, True, False, "gang")
    assert ticks == [(r"Automatically use .*Shard", True)]


def test_buy_shards_check_only_never_picks(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    market_env(monkeypatch)
    b.top = shard_market(100)
    b.open = True
    monkeypatch.setattr(town, "pick", stand_in(town.pick, failing("check only must not buy")))
    assert crafting.buy_shards(as_bot(b), "Level 1 Obsidian Shard", 5, check_only=True) == 0


def test_buy_shards_pick_failure_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 30000}], profile={"gem_fees": {"level 1 obsidian": 100}})
    market_env(monkeypatch)
    b.top = shard_market(100)
    b.open = True
    monkeypatch.setattr(town, "pick", stand_in(town.pick, returning(False)))
    assert crafting.buy_shards(as_bot(b), "Level 1 Obsidian Shard", 5) == 0


def test_make_crystals_fee_and_reserve_limit_the_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Equipment Enhance Crystal Shard", "count": 200},
                 {"name": "Arnebia", "count": 22051}], top=crystal_menus(fee=2051))
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    picked: list[int | None] = []

    def pick(bot: Bot, pattern: str, quantity: int | None = None) -> bool:
        picked.append(quantity)
        add(bot, "Level 5 Equipment Enhance Crystal", quantity or 0)
        return True

    monkeypatch.setattr(town, "pick", pick)
    assert crafting.make_crystals(as_bot(b), "Equipment", 5, 5, "gang") == 1
    assert picked == [1]


def test_make_crystals_with_no_spare_arnebia_does_not_pick(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Equipment Enhance Crystal Shard", "count": 200},
                 {"name": "Arnebia", "count": 20000}], top=crystal_menus(fee=2051))
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, failing("must not spend")))
    assert crafting.make_crystals(as_bot(b), "Equipment", 5, 2, "gang") == 0
    assert any("not enough shards or spare arnebia" in line for line in b.lines)


def test_make_crystals_without_the_synthesize_row(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Equipment Enhance Crystal Shard", "count": 200}],
            top=menu(["shard synthesis"], ["Equipment Enhance Crystal Shard 100"], ["something else"]))
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    assert crafting.make_crystals(as_bot(b), "Equipment", 5, 1, "gang") == 0
    assert b.closed


def test_gem_maker_check_only_still_fetches_with_check_only(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Level 1 Topaz Shard", "count": 8}])
    calls: list[tuple[str, int, str, bool]] = []
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    monkeypatch.setattr(town, "make_gems", stand_in(town.make_gems, failing("check only must not spend")))
    monkeypatch.setattr(crafting, "fetch_shards",
                        stand_in(crafting.fetch_shards, lambda b, shard, n, supply, max_price, check_only=False, chosen=():
                        calls.append((shard, n, supply, check_only)) or 0))
    monkeypatch.setattr(crafting, "raise_gems", stand_in(crafting.raise_gems, failing("check only must not make gems")))
    crafting.gem_maker(as_bot(b), {"colour": "Topaz", "level": 1, "count": 1, "amount": 1,
                           "supply": "buy", "where": "gang"}, check_only=True)
    assert calls == [("Level 1 Topaz Shard", 1, "buy", True)]


def test_raise_gems_missing_the_gem_row_does_not_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(top=menu(["Batch gem synthesis"], ["some other gem 3"]))
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, failing("must not spend")))
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 1, False, False, "gang") == (0, 0)
    assert any("didn't list Level 2 Topaz" in line for line in b.lines)


def test_raise_gems_protected_stops_when_ok_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 100000}], top=raise_menu())
    b.open = True
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    monkeypatch.setattr(crafting, "tick", stand_in(crafting.tick, returning(True)))
    real_choose = b.choose

    def choose(pattern: str):
        return None if pattern == r"OK to Synthesize" else real_choose(pattern)

    b.choose = choose
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 1, False, True, "gang") == (0, 0)


def test_make_most_honours_stop_before_spending(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Level 1 Topaz", "count": 3}])
    b.stop_event.set()
    monkeypatch.setattr(crafting, "raise_gems", stand_in(crafting.raise_gems, failing("stopped run must not combine")))
    monkeypatch.setattr(town, "make_gems", stand_in(town.make_gems, failing("stopped run must not make gems")))
    with pytest.raises(Stopped):
        crafting.make_most(as_bot(b), {"where": "gang", "up_to": 3, "keep": 0})


def test_make_most_stops_after_a_combine_when_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Level 1 Topaz", "count": 3}])
    calls: list[tuple[str, int, int]] = []

    def raise_gems(bot: Bot, colour: str, level: int, k: int, boost: bool, protect: bool, where: str,
                   check_only: bool = False) -> tuple[int, int]:
        calls.append((colour, level, k))
        bot.stop_event.set()
        return 0, 1

    monkeypatch.setattr(crafting, "raise_gems", raise_gems)
    with pytest.raises(Stopped):
        crafting.make_most(as_bot(b), {"where": "gang", "up_to": 3, "keep": 0})
    assert calls == [("Topaz", 1, 1)]


def test_gem_maker_honours_stop_before_spending(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Level 1 Topaz Shard", "count": 8}])
    b.stop_event.set()
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, failing("stopped run must not open a menu")))
    monkeypatch.setattr(town, "make_gems", stand_in(town.make_gems, failing("stopped run must not spend")))
    with pytest.raises(Stopped):
        crafting.gem_maker(as_bot(b), {"colour": "Topaz", "level": 2, "count": 1, "amount": 1,
                               "supply": "none", "where": "gang"})


def test_enhance_stocks_crystals_before_forging(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 100000}],
            top=menu(["enhance equipment"], ["Epic Sniper Belt;rare | enhancement level: 5"]))
    b.open = True

    def choose(pattern: str):
        b.chooses.append(pattern)
        return entry("x")

    b.choose = choose
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    b.dialog = lambda: {"ok": True, "open": 1, "top": None}
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    levels = iter([(entry("x"), 5), (entry("x"), 5), (entry("x"), 6), (entry("x"), 6)])
    monkeypatch.setattr(crafting, "_item_row", stand_in(crafting._item_row, lambda b, name: next(levels)))
    monkeypatch.setattr(crafting, "entry_like", stand_in(crafting.entry_like, lambda b, pattern: None))
    monkeypatch.setattr(crafting, "menu_text",
                        stand_in(crafting.menu_text, lambda b: "Enhancement level increased from 5 to 6, requires 1 Level 5 Equipment Enhance Crystal, costs 100 arnebia"))
    stocked: list[tuple[str, int, int]] = []

    def stock(bot: Bot, cfg: dict[str, Any], kind: str, level: int, want: int, where: str) -> int:
        stocked.append((kind, level, want))
        add(bot, "Level 5 Equipment Enhance Crystal", 2)
        return 2

    monkeypatch.setattr(crafting, "stock_crystals", stock)
    crafting.enhance(as_bot(b), {"items": ["Sniper Belt"], "where": "gang", "up_to": 6,
                         "make_crystals": True, "crystal_stock": 2, "protect": "never"})
    assert stocked == [("Equipment", 5, 2)]
    assert "start forging" in b.chooses
    assert any("Sniper Belt: +6, success" in line for line in b.lines)


def test_enhance_stops_when_stocking_makes_no_crystals(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Arnebia", "count": 100000}],
            top=menu(["enhance equipment"], ["Epic Sniper Belt;rare | enhancement level: 5"]))
    b.open = True
    b.choose = lambda pattern: entry("x")
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    b.dialog = lambda: {"ok": True, "open": 1, "top": None}
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: "gang"))
    monkeypatch.setattr(crafting, "_item_row", stand_in(crafting._item_row, lambda b, name: (entry("x"), 5)))
    monkeypatch.setattr(crafting, "entry_like", stand_in(crafting.entry_like, lambda b, pattern: None))
    monkeypatch.setattr(crafting, "menu_text",
                        stand_in(crafting.menu_text, lambda b: "requires 1 Level 5 Equipment Enhance Crystal, costs 100 arnebia"))
    monkeypatch.setattr(crafting, "stock_crystals", stand_in(crafting.stock_crystals, returning(0)))
    crafting.enhance(as_bot(b), {"items": ["Sniper Belt"], "where": "gang", "up_to": 6,
                         "make_crystals": True, "crystal_stock": 2, "protect": "never"})
    assert any("out of Level 5 Equipment Enhance Crystals - stopping" in line for line in b.lines)
    assert "start forging" not in b.chooses
