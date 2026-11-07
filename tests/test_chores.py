"""Daily chores (engine/chores.py) against the game's own menus, as logged: a pretend game serves
each NPC's pages, and every entry the bot chooses does what the game does with it."""

from __future__ import annotations

import re
import threading
from typing import Any, TypeAlias
from pathlib import Path

import pytest

from unclefarmer.engine import chores, crafting, script, town
from collections.abc import Callable, Generator
from conftest import as_bot, stand_in


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float | int:
        return self.now

    def wait(self, event: threading.Event, seconds: float) -> bool:
        self.now += seconds
        return False


Action: TypeAlias = "Callable[[Game], str | None]"     # an entry's action: runs when chosen, names the next page
Entry: TypeAlias = "tuple[str, str | Action | None]"   # (text, next page or what choosing it does)
Page: TypeAlias = "list[Entry] | Callable[[Game], list[Entry]]"
Pages: TypeAlias = "dict[str, Page]"


class SaidAgent:
    """The agent's `said`: the game's spoken lines after a moment."""

    def __init__(self, g: Game) -> None:
        self.g = g

    def json(self, cmd: str) -> dict[str, Any]:
        if cmd.startswith("said"):
            since = int(cmd.split()[1])
            return {"now": self.g.tick, "said": [{"t": t, "text": x} for t, x in self.g.heard if t > since]}
        return {}


class Game:
    """A pretend game: pages of menu entries; choosing an entry runs its action, which returns the
    next page (a name, or None: the menu closes)."""

    def __init__(self, pages: Pages, roles: dict[str, str], arnebia: int=100000, map_id: int=1041) -> None:
        self.pages, self.roles = pages, roles
        self.page: str | None = None
        self.share_tried = -1           # no half hour tried yet (the real Bot sets it on its first try)
        self.p: dict[str, Any] = {"gem_keep_arnebia": 20000}
        self.character = "Hero"
        self.arnebia = arnebia
        self.mid = map_id
        self.clock = Clock()
        self.stop_event = threading.Event()
        self.chosen: list[str] = []
        self.lines: list[str] = []
        self.talked: list[str] = []
        self.left = False
        self.tick = 0                    # the agent's clock for `said`
        self.heard: list[tuple[int, str]] = []
        self.a = SaidAgent(self)

    def say(self, text: str) -> None:
        """The game says a line (a refusal, a thank-you), as IN op=60."""
        self.tick += 1
        self.heard.append((self.tick, text))

    # the bot's surface the chores use
    def entries(self) -> list[Entry]:
        assert self.page is not None, "no menu is open"
        page = self.pages[self.page]
        return page(self) if callable(page) else page

    def dialog(self) -> dict[str, Any]:
        if self.page is None:
            return {"open": 0, "top": None}
        rows = [[{"text": t}] for t, _a in self.entries()]
        return {"open": 1, "top": {"rows": rows, "row": 0, "col": 0}}

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
                self.chosen.append(text)
                nxt = action(self) if callable(action) else action
                self.page = nxt
                return {"text": text}
        self.page = None
        return None

    def wait_dialog(self, seconds: float=3.0, want_open: bool=True) -> dict[str, Any] | None:
        d = self.dialog()
        return d if bool(d["open"]) == want_open else None

    def close_dialogs(self) -> None:
        self.page = None

    def talk(self, name: str, at: tuple[float, float] | None=None, stand: tuple[float, float] | None=None) -> bool:
        self.talked.append(name)
        if name not in self.pages:
            return False
        self.page = name
        return True

    def bag_rows(self) -> list[dict[str, Any]]:
        return [{"name": "Arnebia", "count": self.arnebia}]

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def sleep(self, s: float) -> None:
        self.clock.now += s

    def map_id(self) -> int:
        return self.mid

    def wait_map_change(self, mid: int, seconds: float) -> bool:
        return self.mid != mid

    def check(self) -> None:
        pass

    def clear_popups(self) -> bool:
        return False

    def leave_instance(self) -> bool:
        self.left = True
        self.mid = 1041
        return True

    def refresh_sheet(self, force: bool=False) -> dict[str, Any] | None:
        return {"level": 79}

    def me(self) -> dict[str, Any]:
        return {"x": 0, "y": 0, "sheet": {"level": 79}}

    # what the game shows round us, for the sweeps that look for places to build
    view: list[dict[str, Any]] = []
    walked: list[Any] = []

    def snap(self) -> dict[str, Any]:
        return {"entities": list(self.view)}

    def walk_to(self, cell: tuple[float, float], map_change: bool=False, near: Callable[[dict[str, Any]], bool] | None=None) -> str:
        self.walked = self.walked + [cell]
        return "arrived"

    def find_npc(self, name: str, near: Callable[[dict[str, Any]], bool] | None=None, within: float | None=None) -> dict[str, Any] | None:
        return next((e for e in self.view if e["name"] == name), None)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> type[Game]:
    def visit(b: Game, role: str | None, name: str | None = None, candidates: object = None) -> str | None:
        assert role is not None
        b.talked.append(role)
        b.page = b.roles[role]
        return b.page
    monkeypatch.setattr(town, "visit", visit)
    monkeypatch.setattr(crafting, "in_gang", stand_in(crafting.in_gang, lambda b: True))
    monkeypatch.setattr(crafting, "reachable_cell", stand_in(crafting.reachable_cell, lambda b, cell: True))
    # the map kinds of the pretend world, whatever atlas data other tests have loaded
    names: dict[int | str, str] = {1041: "central plains", 209: "ocean shard secret room"}
    monkeypatch.setattr(chores.atlas, "kind", stand_in(chores.atlas.kind, lambda mid: "secret_room" if mid == 209 else "field"))
    monkeypatch.setattr(chores.atlas, "label", stand_in(chores.atlas.label, lambda mid: names.get(mid, str(mid))))
    return Game


def cfg(**over: Any) -> dict[str, Any]:
    return {**chores.DEFAULTS, "bank": False, "bounty": False, **over}


# ---------------------------------------------------------------------------------------- the done list
def test_the_done_list_is_per_character_and_game_day() -> None:
    assert chores.done_today("Hero", "2026-10-24") == {}
    chores.mark("Hero", "bank", "collected", "2026-10-24")
    assert chores.done_today("Hero", "2026-10-24")["bank"]["note"] == "collected"
    assert chores.done_today("Other", "2026-10-24") == {}
    assert chores.done_today("Hero", "2026-10-25") == {}          # a new game day: nothing done yet
    chores.mark("Hero", "donation", "done", "2026-10-25")
    assert set(chores.done_today("Hero", "2026-10-25")) == {"donation"}


def test_only_what_is_turned_on_is_due() -> None:
    c = cfg(bank=True, donation="20000", pet_rebirth=True, pet="")   # rebirth without a pet: not due
    assert chores.due("Nobody", c) == ["bank", "donation"]


# ---------------------------------------------------------------------------------------- bank
def bank_pages() -> tuple[Pages, dict[str, Any]]:
    collected: dict[str, Any] = {"deposit": False, "cards": 0}

    def main(g: Game) -> list[Entry]:
        rows: list[Entry] = [("Hero, welcome, you have 327 diamonds in the bank", None)]
        if not collected["deposit"]:
            rows.append(("get deposit income", lambda g: collected.__setitem__("deposit", True)))
        rows.append(("Value Product", "value"))
        return rows

    def value(g: Game) -> list[Entry]:
        rows: list[Entry] = [("VIP And Income monthly card | After purchase ...", None)]
        rows.extend(("get income", lambda g: collected.__setitem__("cards", collected["cards"] + 1) or "value")
                    for _i in range(2 - collected["cards"]))
        return rows
    return {"bank": main, "value": value}, collected


def test_the_bank_collects_the_deposit_and_every_value_product(world: type[Game]) -> None:
    pages, got = bank_pages()
    g = world(pages, {"bank": "bank"})
    out = chores.run(as_bot(g), cfg(bank=True))
    assert got == {"deposit": True, "cards": 2}
    assert "collected the deposit income" in out[0][1] and "2 Value Product incomes" in out[0][1]
    again = chores.run(as_bot(g), cfg(bank=True))
    assert again == [("bank", "already done today")]                 # written down: not twice


def test_a_value_product_already_collected_today_is_not_counted(world: type[Game]) -> None:
    pages: Pages = {"bank": [("Hero, welcome, you have no deposit in the bank", None), ("Value Product", "value")],
                    "value": [("VIP And Income monthly card", None),
                              ("get income", lambda g: g.say("You have gotten the income of this value product today"))]}
    g = world(pages, {"bank": "bank"})
    out = chores.run(as_bot(g), cfg(bank=True))
    assert "1 Value Product incomes were already collected today" in out[0][1]
    assert "collected 1 Value Product" not in out[0][1]


def test_check_only_collects_nothing(world: type[Game]) -> None:
    pages, got = bank_pages()
    g = world(pages, {"bank": "bank"})
    out = chores.run(as_bot(g), cfg(bank=True), check_only=True)
    assert got == {"deposit": False, "cards": 0}
    assert "can be collected" in out[0][1]
    assert chores.done_today("Hero") == {}


# ---------------------------------------------------------------------------------------- pet rebirth
def trainer_pages(free: int=2) -> tuple[Pages, dict[str, int]]:
    state = {"free": free, "rebirths": 0, "paid": 0}

    def pet(g: Game) -> list[Entry]:
        rows: list[Entry] = [("Pet name: Pixie>><< mimi", None), ("Current assets %d; mean value" % (200 + state["rebirths"]), None)]
        if state["free"]:
            rows.append(("You still have %d free Pet Rebirth times today; press here to Rebirth" % state["free"],
                         lambda g: state.update(free=state["free"] - 1, rebirths=state["rebirths"] + 1)))  # menu closes
        else:
            rows.append(("Press to  rebirth, fee 50 diamonds", lambda g: state.update(paid=state["paid"] + 1) or "pet"))
        return rows
    pages: Pages = {"trainer": [("Get Rebirth", "pets"), ("Pet Rebirth Instruction", None)],
             "pets": [("Please choose a pet", None), ("Frog>><< tok", None),
                      ("Shorthair Cat>><< puff", None), ("Pixie>><< mimi", "pet")],
             "pet": pet}
    return pages, state


def test_pet_rebirth_uses_the_free_ones_on_the_chosen_pet_and_never_pays(world: type[Game]) -> None:
    pages, state = trainer_pages()
    g = world(pages, {"pet_trainer": "trainer"})
    out = chores.run(as_bot(g), cfg(pet_rebirth=True, pet="Pixie mimi", rebirths=2))
    assert state == {"free": 0, "rebirths": 2, "paid": 0}
    assert "2 free rebirths, assets 200 -> 202" in out[0][1]


def test_pet_rebirth_names_the_pets_when_the_chosen_one_isnt_there(world: type[Game]) -> None:
    pages, state = trainer_pages()
    g = world(pages, {"pet_trainer": "trainer"})
    out = chores.run(as_bot(g), cfg(pet_rebirth=True, pet="Dragon"))
    assert state["rebirths"] == 0 and "Pixie mimi" in out[0][1] and "Frog tok" in out[0][1]
    assert chores.done_today("Hero") == {}                           # tried again next time


def test_a_pet_is_known_by_its_scan_name_its_own_name_or_its_kind() -> None:
    assert chores.same_pet("Wild Eagle>><< max", "Eagle max")         # the scan shortens the kind
    assert chores.same_pet("Wild Eagle>><< max", "wild eagle max")
    assert not chores.same_pet("Wild Eagle>><< max", "Frog max")
    assert not chores.same_pet("Wild Eagle>><< max", "Eagle maxi")
    assert chores.same_pet("Pixie>><< mimi", "Pixie mimi")
    assert chores.same_pet("Pixie>><< mimi", "mimi")
    assert chores.same_pet("Pixie>><< mimi", "PIXIE")
    assert not chores.same_pet("Pixie>><< mimi", "mim")
    assert not chores.same_pet("Frog>><< ", "")


# ---------------------------------------------------------------------------------------- gang
def head_pages(g_state: dict[str, int]) -> Pages:
    def give(amount: int) -> Action:
        def act(g: Game) -> str:
            g.arnebia -= amount
            g_state["donated"] = amount
            return "sure"
        return act
    return {
        "Head of inner hall": [("take Mission", None), ("Donation", "donation")],
        "donation": [("Are you sure you want to donate 10000 Arnebia to the gang? | donate 10000 Arnebia, ...", give(10000)),
                     ("Are you sure you want to donate 20000 Arnebia to the gang? | donate 20000 Arnebia, ...", give(20000)),
                     ("Are you sure you want to donate 20 diamonds to the gang? | donate 20 diamonds, ...", None),
                     ("Note: Each member can only donate once a day", None)],
        "sure": [("Yes", None), ("No", None)],
    }


def test_the_donation_is_the_chosen_one(world: type[Game]) -> None:
    state: dict[str, int] = {}
    g = world(head_pages(state), {"gang_entrance": "gate"}, arnebia=100000)
    out = chores.run(as_bot(g), cfg(donation="20000"))
    assert state == {"donated": 20000} and g.arnebia == 80000
    assert out[0][1].startswith("done: donate 20000 Arnebia")


def test_a_donation_already_made_today_is_said_so_and_written_down(world: type[Game]) -> None:
    pages = head_pages({})
    pages["donation"] = [("Are you sure you want to donate 20000 Arnebia to the gang? | donate 20000 Arnebia, ...",
                          lambda g: g.say("already donated today"))]
    g = world(pages, {"gang_entrance": "gate"}, arnebia=100000)
    out = chores.run(as_bot(g), cfg(donation="20000"))
    assert out[0][1] == "already donated today" and g.arnebia == 100000
    assert "donation" in chores.done_today("Hero")


def test_the_donation_keeps_the_arnebia_reserve(world: type[Game]) -> None:
    state: dict[str, int] = {}
    g = world(head_pages(state), {"gang_entrance": "gate"}, arnebia=30000)    # reserve 20000: 10000 spare
    out = chores.run(as_bot(g), cfg(donation="20000"))
    assert state == {} and "above your reserve" in out[0][1]


def npc(name: str, x: int, y: int) -> dict[str, Any]:
    return {"name": name, "kind": "npc", "x": x, "y": y}


def site_pages(takes: dict[str, int], pays: int = 6000, verb: str = "build") -> tuple[Pages, dict[str, int]]:
    """Each place takes takes[name] builds today, then pays no more. Building costs nothing: as
    the game's menu says, each one pays (a general's 3000 contribution, 3000 points, 6000 arnebia)."""
    builds: dict[str, int] = {}

    def build(name: str) -> Action:
        def act(g: Game) -> None:
            if builds.get(name, 0) < takes[name]:
                builds[name] = builds.get(name, 0) + 1
                g.arnebia += pays
        return act
    pages: Pages = {name: [(name, None),
                           ("construction general, When %s, each time can get 3000gang contributions; "
                            "3000gang points; %d Arnebia; Press to view the description" % (verb, pays), None),
                           (verb, build(name))] for name in takes}
    return pages, builds


WEST, EAST = (n for n, _c in chores.POSTS)


def test_the_two_posts_are_built_and_pay_for_it(world: type[Game]) -> None:
    pages, builds = site_pages({WEST: 1, EAST: 1})
    g = world(pages, {"gang_entrance": "gate"}, arnebia=5000)          # building costs nothing
    out = chores.run(as_bot(g), cfg(construction=2))
    assert builds == {WEST: 1, EAST: 1} and g.arnebia == 5000 + 2 * 6000
    assert out[0][1] == "built 2 times" and g.walked == []            # straight to them: no walk round


def test_the_game_s_two_builds_a_day_end_construction(world: type[Game]) -> None:
    # builds and repairs share one limit: the third answers "you've already built twice today"
    pages: Pages = {WEST: [(WEST, None), ("build", lambda g: g.say("you've already built twice today"))],
                    EAST: [(EAST, None), ("build", None)]}
    g = world(pages, {"gang_entrance": "gate"})
    out = chores.run(as_bot(g), cfg(construction=2))
    assert out[0][1] == "built 0 times; the game's two builds a day are made"
    assert "construction" in chores.done_today("Hero") and EAST not in g.talked


def test_one_build_a_place_and_never_more_than_the_game_s_two(world: type[Game]) -> None:
    # each place takes one a day ("You've repaired the crystal turret today"): on to the next
    pages, builds = site_pages({WEST: 5, EAST: 5})
    g = world(pages, {"gang_entrance": "gate"}, arnebia=0)
    assert chores.run(as_bot(g), cfg(construction=9))[0][1] == "built 2 times"
    assert builds == {WEST: 1, EAST: 1}


def test_a_place_that_pays_no_arnebia_is_built_at_once_a_run(world: type[Game]) -> None:
    # a soldier's rank gets 0 arnebia: nothing tells a second build took, so one per place
    pages, builds = site_pages({WEST: 5, EAST: 5}, pays=0)
    g = world(pages, {"gang_entrance": "gate"})
    assert chores.run(as_bot(g), cfg(construction=2))[0][1] == "built 2 times"
    assert builds == {WEST: 1, EAST: 1}


def test_a_refusal_the_game_says_stops_building_there(world: type[Game]) -> None:
    pages: Pages = {WEST: [(WEST, None), ("build", lambda g: g.say("Not enough gang contributions"))]}
    g = world(pages, {"gang_entrance": "gate"})
    out = chores.run(as_bot(g), cfg(construction=2))
    assert "built 0 times" in out[0][1]
    assert any("Not enough gang contributions" in ln for ln in g.lines)


INNER, HIGHLAND = 4004, 3701


def highland_game(world: type[Game], number_listed: int = 38) -> tuple[Game, dict[str, int]]:
    """The inner hall's Highland Entrance; Occupied Highlands lists the gang's own highland by its
    number (each gang's is different); inside, a Crystal Turret and a Wall barricade take repairs."""
    pages, repairs = site_pages({"Crystal Turret": 1, "Wall barricade": 1}, pays=6000, verb="repair")

    def into(g: Game) -> None:
        g.mid = HIGHLAND
        g.view = [npc("Teleportation circle", 40, 40), npc("Crystal Turret", 30, 20), npc("Wall barricade", 50, 20)]

    def home(g: Game) -> None:
        g.mid, g.view = INNER, []
    pages["Highland Entrance"] = [("Occupied Highlands", "occupied"), ("Gang Highlands", "every"), ("Description", None)]
    pages["occupied"] = [("the own Gang's Highland %d" % number_listed, into)]
    pages["every"] = [("Highland 35Occupied by Another gang", None), ("Highland 38", into)]
    pages["Teleportation circle"] = [("Return to Main mansion", home)]
    return world(pages, {"gang_entrance": "gate"}, arnebia=0, map_id=INNER), repairs


def test_the_highland_the_player_numbered_is_repaired_and_the_bot_comes_back(world: type[Game]) -> None:
    g, repairs = highland_game(world)
    out = chores.run(as_bot(g), cfg(construction=2, build_at="highland", highland=38))
    assert repairs == {"Crystal Turret": 1, "Wall barricade": 1} and g.arnebia == 12000
    assert g.chosen[:2] == ["Occupied Highlands", "the own Gang's Highland 38"]
    assert g.mid == INNER and "Return to Main mansion" in g.chosen
    assert WEST not in g.talked and out[0][1] == "built 2 times"
    assert chores.STANDS["Wall barricade"] in g.walked        # its south side, where it answers


def test_a_highland_number_occupied_highlands_doesn_t_list_says_what_it_lists(world: type[Game]) -> None:
    g, repairs = highland_game(world, number_listed=38)
    out = chores.run(as_bot(g), cfg(construction=2, build_at="highland", highland=3))
    assert repairs == {} and g.mid == INNER
    assert "has no highland 3 (it lists: the own Gang's Highland 38)" in out[0][1]
    assert "construction" not in chores.done_today("Hero")


def test_the_whole_run_rides_the_swift_beast(world: type[Game]) -> None:
    # every walk of a chores run counts as travelling (most stay on one map, where travel() has
    # nothing to plan), so the walks put the Swift fusion beast on; afterwards it is as it was
    seen: list[bool] = []
    pages, _builds = site_pages({WEST: 1, EAST: 1})
    pages[WEST] = [(WEST, None), ("build", lambda g: seen.append(getattr(g, "traveling", False)))]
    g = world(pages, {"gang_entrance": "gate"})
    chores.run(as_bot(g), cfg(construction=2))
    assert seen == [True] and getattr(g, "traveling", None) is False


def test_the_highland_without_its_number_is_not_due() -> None:
    assert chores.due("Nobody", cfg(construction=2, build_at="highland", highland=0)) == []
    assert chores.due("Nobody", cfg(construction=2, build_at="highland", highland=38)) == ["construction"]


def test_the_way_home_is_never_taken_for_a_place_to_build(world: type[Game]) -> None:
    g, _repairs = highland_game(world)
    g.view = [npc("Teleportation circle", 40, 40), npc("Highland Entrance", 41, 2), npc("Crystal Turret", 30, 20)]
    assert [n for n, _x, _y in chores.sites_in_view(as_bot(g))] == ["Crystal Turret"]


FEED_TEXT = ("each feed can get 500 gang contributions, 500 gang points and 2000 arnebia. Are you sure you "
             "want to feed?")


def shed_pages(answer: Callable[[Game], None], text: str = FEED_TEXT) -> Pages:
    return {"Animal Shed": [(text, answer),
                            ("View the existing breed beasts", None), ("Administration", None)]}


def test_the_breed_beast_is_fed_once_a_day(world: type[Game]) -> None:
    def fed(g: Game) -> None:
        g.arnebia += 2000
    pages = shed_pages(fed, "Breeding King beetle; 85.0% complete; press to feed")
    g = world(pages, {"gang_entrance": "gate"}, arnebia=0)
    out = chores.run(as_bot(g), cfg(feed=True))
    assert out == [("feed", "fed Breeding King beetle; 85.0% complete")] and g.arnebia == 2000
    assert chores.run(as_bot(g), cfg(feed=True)) == [("feed", "already done today")]


def test_a_feed_the_game_refuses_says_why(world: type[Game]) -> None:
    g = world(shed_pages(lambda g: g.say("Not enough arnebia")), {"gang_entrance": "gate"})
    out = chores.run(as_bot(g), cfg(feed=True))
    assert out[0][1] == "not fed: Not enough arnebia" and "feed" not in chores.done_today("Hero")


# ---------------------------------------------------------------------------------------- secret room
ROOM_LIST = ["Today's secret room training time is 3 hours, and you can enter 8 more times",
             "Level 70 ocean shard secret room 1ï¿½ï¿½ Number of people 12/12",
             "Level 70 ocean shard secret room 2ï¿½ï¿½ Number of people 7/12",
             "Level 60 tomb shard secret room 1ï¿½ï¿½ Number of people 8/12",
             "Level 80 sky shard secret room 1ï¿½ï¿½ Number of people 0/12"]


def test_the_room_is_the_highest_level_one_with_a_free_place() -> None:
    found = chores.rooms(ROOM_LIST)
    assert [(r[0], r[1], r[2], r[3]) for r in found][:2] == [(70, "ocean shard", 1, 12), (70, "ocean shard", 2, 7)]
    best, tomb = chores.pick_room(found, 79), chores.pick_room(found, 79, "tomb")
    assert best is not None and best[:3] == (70, "ocean shard", 2)      # room 1 is full, 80 too high
    assert tomb is not None and tomb[:3] == (60, "tomb shard", 1)
    assert chores.pick_room(found, 50) is None


def test_practice_time_left_is_read_from_the_manager() -> None:
    assert chores.practice_left("The assistant has killed 3 monsters for you; You still have 2 hours 58 minutes "
                                "52 seconds of practice time.") == 2 * 3600 + 58 * 60 + 52
    assert chores.practice_left("Your remaining practice time for today is 59 seconds; Please select") == 59
    assert chores.practice_left("Share Arnebia") is None


class Room:
    """What the pretend secret room saw: the room entered, the assistant summoned, who got arnebia."""

    def __init__(self) -> None:
        self.entered: str | None = None
        self.assistant: str | None = None
        self.shared: list[str] = []

    def summon(self, name: str) -> None:
        self.assistant = name


def room_game(world: type[Game]) -> tuple[Game, Room]:
    state = Room()

    def enter(text: str) -> Action:
        def act(g: Game) -> None:
            state.entered = text
            g.mid = 209                     # the ocean secret room
            return None
        return act

    def summoner(n: str) -> Action:
        return lambda g: state.summon(n)

    def manager(g: Game) -> list[Entry]:
        if state.assistant is None:
            rows: list[Entry] = [("Your remaining practice time for today is 2 hours 59 minutes 59 seconds; "
                                  "Please select the assistant", None)]
            rows.extend(("Summoned %s" % n, summoner(n)) for n in ("snowflake prince", "snowflake princess", "Snowball prince"))
            return rows
        return [("The assistant has killed 4 monsters for you; You still have 2 hours 10 minutes 0 seconds of practice time.", None),
                ("Share Arnebia", "share")]

    def share(g: Game) -> list[Entry]:
        return [("Share arnebia with other players in the training secret room.", None),
                ("Choose a player; %d/5" % len(state.shared), "players"), ("Rankings", None)]

    def pick(name: str) -> Action:
        return lambda g: state.shared.append(name)

    pages: Pages = {
        "instructor": [("Young one, you must train yourself", None), ("Secret room training", "training")],
        "training": [("Enter the secret room to practice", "rooms"), ("View sharing ranking", None)],
        "rooms": [(t, enter(t)) for t in ROOM_LIST],
        chores.MANAGER: manager, "share": share,
        "players": [("Stranger; Warrior; male ; level 79", pick("Stranger")),
                    ("Ally; Sorceress; female ; level 79", pick("Ally"))],
    }
    return world(pages, {"instructor": "instructor"}), state


HALF = chores.SLOT


def test_a_visit_just_before_the_half_hour_shares_twice_and_leaves(world: type[Game]) -> None:
    g, state = room_game(world)
    g.clock.now = 1000 * HALF - 120                  # 10:28, say
    out = chores.run(as_bot(g), cfg(secret_room=True, assistant=2, share_with="Nobody, Ally"))
    assert state.entered is not None and state.entered.startswith("Level 70 ocean shard secret room 2")
    assert state.assistant == "snowflake princess"
    assert state.shared == ["Ally", "Ally"]       # 10:28, then 10:30
    assert g.left is True and g.clock.now < 1000 * HALF + 60    # out again within a minute or so
    made, used = chores.shares("Hero")
    assert made == 2 and used == {999, 1000}
    assert "shared arnebia 2 of 5 times today" in out[0][1]
    assert "secret_room" not in chores.done_today("Hero")       # three to go: not done yet


def test_a_visit_far_from_the_half_hour_shares_once(world: type[Game]) -> None:
    g, state = room_game(world)
    g.clock.now = 1000 * HALF + 300                  # 10:35: the next half hour is 25 minutes off
    chores.run(as_bot(g), cfg(secret_room=True))
    assert len(state.shared) == 1 and g.left is True


def test_the_fifth_share_finishes_the_day(world: type[Game]) -> None:
    g, state = room_game(world)
    state.shared = ["x"] * 4                       # the game counts 4 made
    chores.put("Hero", "_shares", {"count": 4, "slots": [1, 2, 3, 4]})
    g.clock.now = 1000 * HALF + 300
    chores.run(as_bot(g), cfg(secret_room=True))
    assert len(state.shared) == 5 and "secret_room" in chores.done_today("Hero")


def test_practising_shares_in_every_half_hour(world: type[Game]) -> None:
    g, state = room_game(world)
    g.clock.now = 1000 * HALF - 120
    chores.run(as_bot(g), cfg(secret_room=True, practice_minutes=62))
    assert len(state.shared) == 4                 # :28, :30, :00, :30 within the hour and two minutes


def test_sharing_waits_for_a_named_player_when_anyone_isnt_allowed(world: type[Game]) -> None:
    g, state = room_game(world)
    g.clock.now = 1000 * HALF + 300
    chores.run(as_bot(g), cfg(secret_room=True, share_with="Nobody", share_anyone=False))
    assert state.shared == [] and any("nobody to share" in ln for ln in g.lines)
    assert chores.shares("Hero") == (0, set())       # that half hour wasn't used: a later trip tries again


def trip_bot(world: type[Game], now: float) -> Game:
    g, _state = room_game(world)
    g.p["chores"] = cfg(secret_room=True, trip_lead=10)
    g.clock.now = now
    return g


def test_a_share_trip_is_due_just_before_an_unused_half_hour(world: type[Game]) -> None:
    assert chores.trip_due(as_bot(trip_bot(world, 1000 * HALF - 9 * 60)))            # 10:21
    assert not chores.trip_due(as_bot(trip_bot(world, 1000 * HALF - 15 * 60)))       # 10:15: too early
    g = trip_bot(world, 1000 * HALF - 9 * 60)
    g.share_tried = 999                                                     # tried this half hour
    assert not chores.trip_due(as_bot(g))
    chores.put("Hero", "_shares", {"count": 1, "slots": [999]})
    assert not chores.trip_due(as_bot(trip_bot(world, 1000 * HALF - 9 * 60)))       # this half hour's is made
    chores.put("Hero", "_shares", {"count": 5, "slots": [1, 2, 3, 4, 5]})
    assert not chores.trip_due(as_bot(trip_bot(world, 1001 * HALF - 9 * 60)))       # all five made
    g = trip_bot(world, 1002 * HALF - 9 * 60)
    g.p["chores"]["share_trips"] = False
    assert not chores.trip_due(as_bot(g))


def test_a_farming_run_goes_to_share_and_comes_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager

    from unclefarmer.engine.botparts.farming import FarmingMixin

    class Trip(FarmingMixin):
        def __init__(self) -> None:
            self.p = {}
            self.lines: list[str] = []
            self.trips: list[int | str] = []
            self.stop_event = threading.Event()

        def log(self, line: str, speak: bool=False):
            self.lines.append(line)

        @contextmanager
        def timed(self, phase: str) -> Generator[None, None, None]:
            yield

        def travel(self, dst: int | str, cell: tuple[float, float] | None = None) -> None:
            self.trips.append(dst)

        def set_modes(self) -> None:
            self.trips.append("modes")
    monkeypatch.setattr(chores, "trip_due", stand_in(chores.trip_due, lambda b: True))
    monkeypatch.setattr(chores, "share_visit", stand_in(chores.share_visit, lambda b, cfg: (3, "ocean shard 2")))
    t = Trip()
    assert t.share_trip(5302) is True
    assert t.trips == [5302, "modes"] and any("3 of 5" in ln for ln in t.lines)
    monkeypatch.setattr(chores, "trip_due", stand_in(chores.trip_due, lambda b: False))
    assert t.share_trip(5302) is False


def test_check_only_never_enters_the_room(world: type[Game]) -> None:
    g, state = room_game(world)
    out = chores.run(as_bot(g), cfg(secret_room=True), check_only=True)
    assert state.entered is None and "would enter ocean shard 2" in out[0][1]
    assert "training time is 3 hours" in out[0][1]


# ---------------------------------------------------------------------------------------- scripts
def test_the_chores_command_in_a_script() -> None:
    main, _subs, _stop = script.parse("chores check only=bank,donation\nchores\n")
    assert main[0].opts == {"check": True, "only": ["bank", "donation"]}
    assert main[1].opts == {"check": False, "only": []}
    with pytest.raises(script.ScriptError):
        script.parse("chores only=laundry\n")


# ---------------------------------------------------------------------------------------- settings and done list
def test_label_and_config_fill_every_chore() -> None:
    assert chores.label("bank") == "bank income"
    assert chores.label("secret_room") == "secret room"
    cfg = chores.config({"chores": {"bank": False, "donation": "diamonds"}})
    assert cfg["bank"] is False and cfg["donation"] == "diamonds"
    assert cfg["bounty"] is True and cfg["construction"] == 0


def test_chores_config_does_not_share_mutable_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Today's defaults are all scalars; a nested one added later must still not be shared.
    monkeypatch.setitem(chores.DEFAULTS, "share_list", [])
    a = chores.config({})
    b = chores.config({})
    a["share_list"].append("Ally")
    assert b["share_list"] == [] and chores.DEFAULTS["share_list"] == []


def test_wanted_uses_each_chores_own_switch() -> None:
    cfg = dict(chores.DEFAULTS)
    assert chores.wanted(cfg, "bank")
    assert not chores.wanted(dict(cfg, bank=False), "bank")
    assert chores.wanted(dict(cfg, donation="10000"), "donation")
    assert not chores.wanted(dict(cfg, donation="off"), "donation")
    assert chores.wanted(dict(cfg, construction=2), "construction")
    assert not chores.wanted(dict(cfg, construction=0), "construction")
    assert chores.wanted(dict(cfg, pet_rebirth=True, pet="Sparky"), "pet_rebirth")
    assert not chores.wanted(dict(cfg, pet_rebirth=True, pet=""), "pet_rebirth")
    assert not chores.wanted(dict(cfg, pet_rebirth=False, pet="Sparky"), "pet_rebirth")


def test_the_done_list_is_per_day_and_per_character(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chores.paths, "LOGS", str(tmp_path))
    monkeypatch.setattr(chores.daily, "game_day", lambda: "2026-10-30")
    chores.mark("Hero", "bank", "done")
    assert "bank" in chores.done_today("Hero")
    assert chores.done_today("Other") == {}
    monkeypatch.setattr(chores.daily, "game_day", lambda: "2026-10-31")
    assert chores.done_today("Hero") == {}                     # yesterday's list is not today's
    chores.put("Hero", "_shares", [1])
    assert chores.done_today("Hero")["_shares"] == [1]


def test_due_lists_only_switched_on_chores_not_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chores.paths, "LOGS", str(tmp_path))
    monkeypatch.setattr(chores.daily, "game_day", lambda: "2026-10-30")
    cfg = dict(chores.DEFAULTS, bank=True, bounty=True, donation="off")
    chores.mark("Hero", "bank", "done")
    assert chores.due("Hero", cfg) == ["bounty"]
