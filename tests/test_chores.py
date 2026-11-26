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
    hall_view: list[dict[str, Any]] = []     # what a return to the inner hall shows
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
    def visit(b: Game, role: str | None, name: str | None = None, candidates: object = None,
              back_to: object = None) -> str | None:
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
TURRET, WALL = (n for n, _s in chores.HIGHLAND_SITES)
REPAIR_PAY = "Each time of repair can get 3000gang contributions; 3000gang points; 6000 Arnebia"


def highland_game(world: type[Game], highlands: dict[int, dict[str, float]] | None = None,
                  extra: Pages | None = None) -> tuple[Game, list[tuple[int, str]]]:
    """The inner hall's Highland Entrance; Occupied Highlands lists each of the gang's highlands;
    inside (one map, whichever the highland), the turret and the wall say how complete they are and
    take one repair a day each."""
    levels = highlands if highlands is not None else {38: {TURRET: 90.0, WALL: 90.0}}
    repairs: list[tuple[int, str]] = []
    here = [0]

    def site(name: str) -> Page:
        def repair(g: Game) -> None:
            repairs.append((here[0], name))
            g.arnebia += 6000

        def page(g: Game) -> list[Entry]:
            if any(n == name for _h, n in repairs):
                return [(name, None), ("You've repaired the %s today" % name.lower(), None)]
            return [(name, None), ("the completeness is %.1f%%" % levels[here[0]][name], None),
                    (REPAIR_PAY, None), ("repair", repair)]
        return page

    def into(number: int) -> Action:
        def act(g: Game) -> None:
            here[0] = number
            g.mid = HIGHLAND
            g.view = [npc("Teleportation circle", 30, 2), npc(TURRET, 30, 28)]
        return act

    def home(g: Game) -> None:
        g.mid, g.view = INNER, list(g.hall_view)
    pages: Pages = {TURRET: site(TURRET), WALL: site(WALL), **(extra or {})}
    pages["Highland Entrance"] = [("Occupied Highlands", "occupied"), ("Gang Highlands", "every"), ("Description", None)]
    pages["occupied"] = [("the own Gang's Highland %d" % n, into(n)) for n in levels]
    pages["every"] = [("Highland 35Occupied by Another gang", None)]
    pages["Teleportation circle"] = [("Return to Main mansion", home)]
    return world(pages, {"gang_entrance": "gate"}, arnebia=0, map_id=INNER), repairs


def test_both_defences_of_a_highland_are_repaired(world: type[Game]) -> None:
    g, repairs = highland_game(world)
    out = chores.run(as_bot(g), cfg(construction=2))
    assert repairs == [(38, TURRET), (38, WALL)] and g.arnebia == 12000
    assert g.chosen[:2] == ["Occupied Highlands", "the own Gang's Highland 38"]
    assert g.mid == INNER and "Return to Main mansion" in g.chosen
    assert WEST not in g.talked and out[0][1] == "built 2 times"
    assert dict(chores.HIGHLAND_SITES)[WALL] in g.walked      # its south side, where it answers
    assert g.walked[-1] == chores.HIGHLAND_HOME_STAND


CLOSED = ("There are corresponding construction mission, and the construction of the West "
          "encampment reinforced construction is closed")


def test_a_highland_at_100_percent_is_left_for_the_next_one(world: type[Game]) -> None:
    g, repairs = highland_game(world, {38: {TURRET: 100.0, WALL: 100.0}, 41: {TURRET: 50.0, WALL: 75.5}})
    out = chores.run(as_bot(g), cfg(construction=2))
    assert repairs == [(41, TURRET), (41, WALL)] and out[0][1] == "built 2 times"
    assert g.mid == INNER


def test_each_defence_takes_one_repair_a_day_over_all_the_highlands(world: type[Game]) -> None:
    g, repairs = highland_game(world, {38: {TURRET: 90.0, WALL: 100.0}, 41: {TURRET: 90.0, WALL: 90.0}})
    chores.run(as_bot(g), cfg(construction=2))
    assert repairs == [(38, TURRET), (41, WALL)]


def test_a_second_page_of_highlands_is_read(world: type[Game]) -> None:
    g, repairs = highland_game(world, {38: {TURRET: 100.0, WALL: 100.0}, 41: {TURRET: 90.0, WALL: 90.0}})
    pages = g.pages["occupied"]
    assert isinstance(pages, list)
    g.pages["occupied"] = [pages[0], ("next page: 2", "more")]
    g.pages["more"] = [pages[1]]
    chores.run(as_bot(g), cfg(construction=2))
    assert repairs == [(41, TURRET), (41, WALL)]


def test_a_highland_taken_from_another_gang_counts_only_when_asked(world: type[Game]) -> None:
    """Occupied Highlands names a taken highland after the gang it was taken from (read live)."""
    g, _repairs = highland_game(world, {38: {TURRET: 90.0, WALL: 90.0}, 208: {TURRET: 50.0, WALL: 60.0}})
    pages = g.pages["occupied"]
    assert isinstance(pages, list)
    g.pages["occupied"] = [pages[0], ("Simgang Gang's Highland 208", pages[1][1])]
    assert chores.next_highland(as_bot(g), {38}) is None
    assert chores.next_highland(as_bot(g), {38}, taken=True) == 208
    assert g.chosen[-1] == "Simgang Gang's Highland 208"


def test_the_whole_list_is_told_only_when_its_last_page_was_read(world: type[Game]) -> None:
    g, _repairs = highland_game(world, {38: {TURRET: 90.0, WALL: 90.0}, 41: {TURRET: 90.0, WALL: 90.0}})
    whole: set[int] = set()
    assert chores.next_highland(as_bot(g), set(), whole=whole) == 38
    assert whole == {38, 41}                               # one page: every highland is known


def test_a_list_of_two_pages_is_whole_only_from_its_last_page(world: type[Game]) -> None:
    g, _repairs = highland_game(world, {38: {TURRET: 90.0, WALL: 90.0}, 41: {TURRET: 90.0, WALL: 90.0}})
    pages = g.pages["occupied"]
    assert isinstance(pages, list)
    g.pages["occupied"] = [pages[0], ("next page: 2", "more")]
    g.pages["more"] = [pages[1]]
    whole: set[int] = {99}
    assert chores.next_highland(as_bot(g), set(), whole=whole) == 38
    assert whole == set()                                  # page 2 unread: not known yet
    g.mid, g.view = INNER, list(g.hall_view)
    assert chores.next_highland(as_bot(g), {38}, whole=whole) == 41
    assert whole == {38, 41}

def test_repairs_leave_highlands_taken_from_other_gangs_alone(world: type[Game]) -> None:
    g, repairs = highland_game(world, {38: {TURRET: 100.0, WALL: 100.0}, 208: {TURRET: 50.0, WALL: 60.0}})
    pages = g.pages["occupied"]
    assert isinstance(pages, list)
    g.pages["occupied"] = [pages[0], ("Simgang Gang's Highland 208", pages[1][1])]
    chores.run(as_bot(g), cfg(construction=2))
    assert all(n != 208 for n, _name in repairs)

def test_repairs_already_made_today_count_and_end_the_trip(world: type[Game]) -> None:
    extra: Pages = {TURRET: [("You've repaired the crystal turret today", None)],
                    WALL: [("You've repaired the Wall barricade today", None)]}
    g, repairs = highland_game(world, {38: {TURRET: 90.0, WALL: 90.0}, 41: {TURRET: 90.0, WALL: 90.0}})
    g.pages.update(extra)
    out = chores.run(as_bot(g), cfg(construction=2))
    assert repairs == [] and out[0][1] == "built 0 times; 2 already made today"
    assert "the own Gang's Highland 41" not in g.chosen and "construction" in chores.done_today("Hero")


def test_no_highland_left_goes_on_to_the_posts(world: type[Game]) -> None:
    posts, builds = site_pages({WEST: 1, EAST: 1})
    g, repairs = highland_game(world, {38: {TURRET: 100.0, WALL: 100.0}}, posts)
    out = chores.run(as_bot(g), cfg(construction=2))
    assert repairs == [] and builds == {WEST: 1, EAST: 1} and out[0][1] == "built 2 times"


def test_closed_posts_go_on_to_the_highlands(world: type[Game]) -> None:
    posts: Pages = {WEST: [(CLOSED, None)], EAST: [(CLOSED.replace("West", "East"), None)]}
    g, repairs = highland_game(world, {38: {TURRET: 100.0, WALL: 100.0}}, posts)
    out = chores.run(as_bot(g), cfg(construction=2))
    assert repairs == [(38, TURRET), (38, WALL)] and out[0][1] == "built 2 times"   # at 100%, last of all
    assert EAST in g.talked


def test_one_closed_post_leaves_the_other_to_build(world: type[Game]) -> None:
    pages, builds = site_pages({EAST: 1})
    pages[WEST] = [(CLOSED, None)]
    g = world(pages, {"gang_entrance": "gate"})
    chores.run(as_bot(g), cfg(construction=1, build_highlands=False))
    assert builds == {EAST: 1}


def test_damaged_highlands_come_before_what_the_gang_is_building(world: type[Game]) -> None:
    camp = "Level 5 Magic Cannon Construction"
    extra, builds = site_pages({camp: 1, WEST: 1, EAST: 1})
    g, repairs = highland_game(world, {38: {TURRET: 90.0, WALL: 100.0}}, extra)
    g.view = hall = [npc(camp, 43, 15)]
    g.hall_view = hall
    chores.run(as_bot(g), cfg(construction=2))
    assert repairs == [(38, TURRET)] and builds == {camp: 1}


def test_only_the_ticked_places_are_used(world: type[Game]) -> None:
    camp, other = "Level 5 Magic Cannon Construction", "Level 5 Supply Camp Construction"
    pages, builds = site_pages({camp: 1, other: 1, WEST: 1, EAST: 1})
    g = world(pages, {"gang_entrance": "gate"}, map_id=INNER)
    g.view = [npc(camp, 43, 15), npc(other, 43, 15)]
    chores.run(as_bot(g), cfg(construction=2, build_highlands=False, build_cannon=False, build_posts=False))
    assert builds == {other: 1} and "Highland Entrance" not in g.talked and WEST not in g.talked


def test_what_the_gang_is_building_now_comes_first(world: type[Game]) -> None:
    camp = "Level 5 Magic Cannon Construction"
    pages, builds = site_pages({camp: 1, WEST: 1, EAST: 1})
    pages[camp] = [("Completed 40.0%", None), *pages[camp]]            # type: ignore[misc]
    g = world(pages, {"gang_entrance": "gate"}, map_id=INNER)
    g.view = [npc(camp, 43, 15), npc("Sentry Tower", 38, 15)]
    out = chores.run(as_bot(g), cfg(construction=2))
    assert builds == {camp: 1, WEST: 1} and out[0][1] == "built 2 times"


def test_a_finished_camp_is_passed_over(world: type[Game]) -> None:
    camp = "Level 5 Supply Camp Construction"
    pages, builds = site_pages({camp: 1, WEST: 1, EAST: 1})
    pages[camp] = [("the completeness is 100%", None), *pages[camp]]   # type: ignore[misc]
    g = world(pages, {"gang_entrance": "gate"}, map_id=INNER)
    g.view = [npc(camp, 43, 15)]
    chores.run(as_bot(g), cfg(construction=2))
    assert builds == {WEST: 1, EAST: 1}


def test_check_only_builds_nothing_and_comes_back_from_the_highland(world: type[Game]) -> None:
    g, repairs = highland_game(world)
    out = chores.run(as_bot(g), cfg(construction=2), check_only=True)
    assert repairs == [] and out[0][1] == "can build at the Crystal Turret" and g.mid == INNER
    assert "construction" not in chores.done_today("Hero")


def test_a_highland_that_cant_be_left_is_reported(world: type[Game]) -> None:
    g, _repairs = highland_game(world, {38: {TURRET: 100.0, WALL: 100.0}})
    g.pages["Teleportation circle"] = [("Description", None)]
    g.pages[WEST] = [(WEST, None)]
    g.pages[EAST] = [(EAST, None)]
    out = chores.run(as_bot(g), cfg(construction=1))
    assert "couldn't get back from the highland" in out[0][1]
    assert "construction" not in chores.done_today("Hero")


def test_highlands_need_no_number_any_more() -> None:
    assert chores.due("Nobody", cfg(construction=2)) == ["construction"]


def test_no_place_ticked_is_no_construction() -> None:
    off = {key: False for key in chores.BUILD_PLACES}
    assert chores.due("Nobody", cfg(construction=2, **off)) == []


def test_a_new_gang_member_builds_nothing_today_and_it_is_no_failure(world: type[Game]) -> None:
    pages: Pages = {WEST: [(WEST, None), ("build", lambda g: g.say("cannot build on the day you join a gang"))],
                    EAST: [(EAST, None), ("build", None)]}
    g = world(pages, {"gang_entrance": "gate"})
    out = chores.run(as_bot(g), cfg(construction=2))
    assert out[0][1] == "you joined the gang today: the game allows no building until tomorrow"
    assert EAST not in g.talked and "construction" in chores.done_today("Hero")


def test_a_new_gang_member_can_t_donate_and_it_is_no_failure(world: type[Game]) -> None:
    def refuse(g: Game) -> None:
        g.say("cannot donate on the first day of joining a gang")
    pages: Pages = {"Head of inner hall": [("Donation", refuse)]}
    g = world(pages, {"gang_entrance": "gate"})
    out = chores.run(as_bot(g), cfg(donation="10000"))
    assert out[0][1] == "you joined the gang today: the game allows no donation until tomorrow"
    assert "donation" in chores.done_today("Hero")


def test_a_beast_already_fed_today_is_done(world: type[Game]) -> None:
    g = world({"Animal Shed": [("You have fed today", None)]}, {"gang_entrance": "gate"})
    out = chores.run(as_bot(g), cfg(feed=True))
    assert out == [("feed", "already fed today")] and "feed" in chores.done_today("Hero")


def bounty_game(world: type[Game], monkeypatch: pytest.MonkeyPatch, pages: Pages) -> Game:
    """Officers reached by name; one with no page can't be reached."""
    spots: dict[str, dict[str, Any]] = {n: {"map": 1081, "name": n, "x": 10 * i, "y": 10}
                                        for i, n in enumerate(chores.BOUNTY_OFFICERS)}

    def nearest(b: Any, role: str | None, candidates: Any = None, back_to: Any = None) -> Any:
        return candidates[0]

    def visit(b: Any, role: str | None, name: str | None = None, candidates: Any = None, back_to: Any = None) -> str:
        npc = candidates[0][1]
        b.talked.append(npc)
        if npc not in b.pages:
            raise chores.Stopped("couldn't talk to %s" % npc)
        b.page = npc
        return npc
    monkeypatch.setattr(chores.knowledge, "describe_npc", stand_in(chores.knowledge.describe_npc, lambda name: spots.get(name or "")))
    monkeypatch.setattr(town, "nearest", stand_in(town.nearest, nearest))
    monkeypatch.setattr(town, "visit", stand_in(town.visit, visit))
    return world(pages, {})


SUKE, BREN = chores.BOUNTY_OFFICERS[:2]


def paying(g: Game) -> None:
    g.arnebia += 500
    g.say("Bounty received")


def test_bounties_go_only_to_the_ticked_officers_and_are_no_daily_chore(world: type[Game], monkeypatch: pytest.MonkeyPatch) -> None:
    g = bounty_game(world, monkeypatch, {SUKE: [("Reset Talents", None), ("Collect hunting bountie", paying)],
                                         BREN: [("Collect Hunting Bounty", paying)]})
    notes = chores.collect_bounties(as_bot(g), cfg(bounty_officers=[SUKE, BREN]))
    assert g.talked == [SUKE, BREN] and g.arnebia == 101000
    assert notes[0] == "Guard Captain Suke: +500 arnebia; Bounty received"
    assert "bounty" not in chores.CHORES and chores.done_today("Hero") == {}
    assert len(chores.collect_bounties(as_bot(g), cfg(bounty_officers=[SUKE]))) == 1     # again whenever asked


def test_an_officer_out_of_reach_does_not_end_the_round(world: type[Game], monkeypatch: pytest.MonkeyPatch) -> None:
    g = bounty_game(world, monkeypatch, {BREN: [("Collect Hunting Bounty", paying)]})
    notes = chores.collect_bounties(as_bot(g), cfg(bounty_officers=[SUKE, BREN]))
    assert notes[0] == "couldn't talk to Guard Captain Suke" and notes[1].startswith("Chancellor Bren: +500")


def test_check_only_collects_no_bounty(world: type[Game], monkeypatch: pytest.MonkeyPatch) -> None:
    g = bounty_game(world, monkeypatch, {SUKE: [("Collect hunting bountie", paying)]})
    assert chores.collect_bounties(as_bot(g), cfg(bounty_officers=[SUKE]), True) == ["Guard Captain Suke can pay hunting bounties"]
    assert g.arnebia == 100000 and g.chosen == []


def test_no_officer_ticked_says_where_to_tick_them(world: type[Game], monkeypatch: pytest.MonkeyPatch) -> None:
    g = bounty_game(world, monkeypatch, {})
    with pytest.raises(chores.Stopped, match="Tools, Hunting bounties"):
        chores.collect_bounties(as_bot(g), cfg())


class ChestGame(Game):
    """Guard Captain Suke's exchange: each confirmed one puts a chest in the bag."""
    chests = 0
    contribution: int | None = None
    went: tuple[int, Any] | None = None

    def refresh_sheet(self, force: bool = False) -> dict[str, Any] | None:
        return {"level": 100, "contribution": self.contribution}

    def bag_rows(self) -> list[dict[str, Any]]:
        return [*super().bag_rows(), {"name": "Contribution Chest", "count": self.chests}]

    def travel(self, dst: int, cell: Any = None) -> None:
        self.went = (dst, cell)
        self.mid = dst


def chest_game(left: int = 2, price: str = "This exchange requires 5000 contributions, continue?") -> ChestGame:
    def give(g: Game) -> None:
        assert isinstance(g, ChestGame)
        g.chests += 1

    def officer(g: Game) -> list[Entry]:
        assert isinstance(g, ChestGame)
        if g.chests >= left:
            g.say("Exceeded times")
            return [("Guard Captain Suke", None)]
        return [("Exchange Contribution Chest", "question"), ("Goodbye", None)]
    pages: Pages = {chores.CHEST_OFFICER: officer, "question": [(price, None), ("Confirm", give), ("Cancel", None)]}
    return ChestGame(pages, {})


def test_two_contribution_chests_are_exchanged_and_counted_from_the_bag(world: type[Game]) -> None:
    g = chest_game()
    out = chores.run(as_bot(g), cfg(contribution_chests=True))
    assert g.chests == 2 and g.went == (chores.CHEST_MAP, chores.CHEST_SPOT)
    assert out == [("contribution_chests", "got 2 Contribution Chests")]
    assert "contribution_chests" in chores.done_today("Hero")


def test_the_chest_count_is_kept_to_what_the_player_set(world: type[Game]) -> None:
    g = chest_game()
    chores.run(as_bot(g), cfg(contribution_chests=True, chest_count=1))
    assert g.chests == 1


def test_the_game_s_daily_limit_ends_the_chests_for_today(world: type[Game]) -> None:
    g = chest_game(left=1)
    out = chores.run(as_bot(g), cfg(contribution_chests=True))
    assert g.chests == 1 and out[0][1] == "got 1 Contribution Chests; the game allows no more today"
    assert "contribution_chests" in chores.done_today("Hero")


def test_nothing_is_confirmed_without_the_price(world: type[Game]) -> None:
    g = chest_game(price="Something else entirely")
    out = chores.run(as_bot(g), cfg(contribution_chests=True))
    assert g.chests == 0 and "Confirm" not in g.chosen and "nothing was confirmed" in out[0][1]
    assert "contribution_chests" not in chores.done_today("Hero")


def test_too_little_contribution_is_said_and_tried_another_day(world: type[Game]) -> None:
    g = chest_game()
    g.pages["question"] = [("Not enough gang contributions", None)]
    out = chores.run(as_bot(g), cfg(contribution_chests=True))
    assert g.chests == 0 and "not enough contribution" in out[0][1]
    assert "contribution_chests" not in chores.done_today("Hero")


def test_too_little_contribution_on_the_sheet_saves_the_trip(world: type[Game]) -> None:
    g = chest_game()
    g.contribution = 4999
    out = chores.run(as_bot(g), cfg(contribution_chests=True))
    assert g.went is None and g.chests == 0
    assert out[0][1] == "not enough contribution for a Contribution Chest: 4999 of 5000"
    assert "contribution_chests" not in chores.done_today("Hero")


def test_the_sheet_limits_the_chests_to_what_it_can_pay_for(world: type[Game]) -> None:
    g = chest_game()
    g.contribution = 9999
    out = chores.run(as_bot(g), cfg(contribution_chests=True))
    assert g.chests == 1 and out[0][1] == "got 1 Contribution Chests; not enough contribution for another"


def test_check_only_exchanges_nothing(world: type[Game]) -> None:
    g = chest_game()
    out = chores.run(as_bot(g), cfg(contribution_chests=True), check_only=True)
    assert g.chests == 0 and "Exchange Contribution Chest" not in g.chosen
    assert out[0][1] == "can exchange contribution for a Contribution Chest"


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

        def me(self) -> dict[str, Any]:
            return {"x": 40.0, "y": 50.0}

        @contextmanager
        def timed(self, phase: str) -> Generator[None, None, None]:
            yield

        def travel(self, dst: int | str, cell: tuple[float, float] | None = None) -> None:
            self.trips.append(dst)

        def set_modes(self) -> None:
            self.trips.append("modes")
    monkeypatch.setattr(chores, "trip_due", stand_in(chores.trip_due, lambda b: True))
    went: list[object] = []

    def visit(b: object, cfg: dict[str, Any], practise: int = 0, back_to: object = None) -> tuple[int, str]:
        went.append(back_to)
        return 3, "ocean shard 2"
    monkeypatch.setattr(chores, "share_visit", stand_in(chores.share_visit, visit))
    t = Trip()
    assert t.share_trip(5302) is True
    assert t.trips == [5302, "modes"] and any("3 of 5" in ln for ln in t.lines)
    assert went == [(5302, (40.0, 50.0))]          # the instructor is chosen for the way back to the farm too
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
    assert cfg["bounty_officers"] == [] and cfg["construction"] == 0


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


def test_same_name_characters_keep_their_own_done_lists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chores.paths, "LOGS", str(tmp_path))
    monkeypatch.setattr(chores.daily, "game_day", lambda: "2026-10-30")
    chores.mark("Hero", "bank", "done")                    # written before characters had keys
    first = chores.settings.character_profile("Hero", 17, "S1")
    second = chores.settings.character_profile("Hero", 42, "S2")
    assert "bank" in chores.done_today(first)              # carried on: no bank visit twice today
    chores.mark(first, "bounty", "done")
    chores.mark(second, "donation", "done")
    assert set(chores.done_today(first)) == {"bank", "bounty"}
    assert set(chores.done_today(second)) == {"bank", "donation"}   # both started from the shared old list
    chores.mark(second, "bounty", "done")
    assert set(chores.done_today(first)) == {"bank", "bounty"}       # but each writes only its own
    assert set(chores.done_today(chores.settings.character_profile("Hero", 17))) == {"bank"}   # old fallback stays ambiguous
    assert set(chores.done_today("Hero")) == {"bank"}


def test_same_id_on_two_servers_never_shares_a_done_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chores.paths, "LOGS", str(tmp_path))
    monkeypatch.setattr(chores.daily, "game_day", lambda: "2026-10-30")
    old = chores.settings.character_profile("Hero", 17)
    first = chores.settings.character_profile("Hero", 17, "S1")
    second = chores.settings.character_profile("Hero", 17, "S2")
    chores.mark(old, "bank", "old")                         # written before server identities were known
    assert "bank" in chores.done_today(first)
    chores.mark(first, "bounty", "done")
    assert chores.done_today(second) == {}                    # the old ID-only list may belong to S1
    chores.mark(second, "donation", "done")
    assert set(chores.done_today(first)) == {"bank", "bounty"}
    assert set(chores.done_today(second)) == {"donation"}


def test_due_lists_only_switched_on_chores_not_done(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chores.paths, "LOGS", str(tmp_path))
    monkeypatch.setattr(chores.daily, "game_day", lambda: "2026-10-30")
    cfg = dict(chores.DEFAULTS, bank=True, feed=True, donation="off")
    chores.mark("Hero", "bank", "done")
    assert chores.due("Hero", cfg) == ["feed"]
