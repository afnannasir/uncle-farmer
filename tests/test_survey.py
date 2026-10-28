# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""survey.py: learning every NPC and menu from the game itself.

Covers the pure helpers (DENY, plain, words, label, is_thing, safe, is_npc, critter, worth_noting),
the sweep points, the saved Record (marking spots, folding walkers, monsters, save/load), the menu
reader (open_npc, heard, acted, explore), the per-map and whole-run survey loops, and the map plan
(city_rect, hints, hint_rect, plan_targets, tour, done).

Nothing here starts a bot or talks to a game: a stand-in bot drives the parts that need one, the
survey output directory is a tmp_path (survey.OUT is patched), and the game/atlas data is faked, so
nothing is written into the repo.
"""

from __future__ import annotations

import json
import os
import re

import pytest

from unclefarmer import atlas, knowledge, paths, settings, travel
from unclefarmer.engine import beasts, survey
from unclefarmer.engine.botparts.base import Respawned, Stopped
from pathlib import Path
from collections.abc import Iterable
from typing import Any
from conftest import returning, JsonOnly, as_bot, stand_in
from unclefarmer.link import GameLink
from unclefarmer.atlaslib import Cell
from unclefarmer.engine.survey import Record


@pytest.fixture(autouse=True)
def survey_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep the survey files out of the repo and the global text/item caches fresh."""
    monkeypatch.setattr(survey, "OUT", str(tmp_path))
    monkeypatch.setattr(survey, "_TEXTS", survey.TextCache())
    monkeypatch.setattr(settings, "load_app", stand_in(settings.load_app, lambda: {"game_folder": ""}))
    monkeypatch.setattr(atlas, "name", stand_in(atlas.name, lambda mid: "Map %s" % mid))
    monkeypatch.setattr(atlas, "label", stand_in(atlas.label, lambda mid: "Map %s (%s)" % (mid, mid)))
    return tmp_path


@pytest.fixture
def game_texts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A game folder with a language/english/more folder, for the language-file readers."""
    more = tmp_path / "game" / "language" / "english" / "more"
    more.mkdir(parents=True)
    monkeypatch.setattr(settings, "load_app", stand_in(settings.load_app, lambda: {"game_folder": str(tmp_path / "game")}))
    return more


# --------------------------------------------------------------------------------- helpers
def info(text: str, flags: int=12, **extra: Any) -> dict[str, Any]:
    e: dict[str, Any] = {"flags": flags, "options": [text], "cmd": [0]}
    e.update(extra)
    return e


def top(rows: list[list[dict[str, int | list[str] | list[int]] | None]], kind: str="npc") -> dict[str, str | list[list[dict[str, int | list[str] | list[int]]]] | list[list[dict[str, int | list[str] | list[int]] | None]]]:
    return {"kind": kind, "rows": [list(r) for r in rows]}


class Link:
    def __init__(self, said: list[str] | None = None, fail: bool = False) -> None:
        self.said, self.fail = said, fail

    def send(self, line: str) -> str:
        return "ok"

    def json(self, line: str) -> Any:
        if self.fail:
            raise RuntimeError("old agent")
        if line.startswith("said"):
            return {"said": [{"text": t} for t in (self.said or [])]}
        return {}


class MenuBot:
    """A bot whose dialogs are a fixed tree: open_npc()/explore() drive it like the real game."""

    def __init__(self, tree: Any, said: list[str] | None=None) -> None:
        self.tree = tree
        self.a: GameLink = Link(said)
        self.logs: list[str] = []
        self.slept: list[float] = []
        self.closed = 0
        self.path: list[Any] = []
        self.current = None
        self.talk_ok = True
        self.talk_problem: str | None = None

    def log(self, line: str, speak: bool=False) -> None:
        self.logs.append(line)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def close_dialogs(self) -> None:
        self.closed += 1

    def talk(self, name: str, at: tuple[float, float] | None=None, stand: tuple[float, float] | None=None) -> bool:
        self.path: list[Any] = []
        self.current = self.tree.get(())
        return self.talk_ok

    def wait_dialog(self, seconds: float=3.0) -> dict[str, Any] | None:
        return {"open": 1, "top": self.current} if self.current else None

    def dialog(self) -> dict[str, Any]:
        if self.current:
            return {"ok": True, "open": 1, "top": self.current}
        return {"ok": True, "open": 0, "top": None}

    def choose(self, pattern: str) -> dict[str, Any] | None:
        import re
        if not self.current:
            return None
        for row in self.current["rows"]:
            for e in row:
                if not e:
                    continue
                text = (e.get("options") or [None])[0] or e.get("text") or e.get("raw")
                if text and re.match(pattern, text):
                    self.path.append(text)
                    self.current = self.tree.get(tuple(self.path))
                    return e
        return None


class Bot:
    """A stand-in bot for survey_map()/read_menus()/_survey(): records the calls made into it."""

    def __init__(self, mid: int=5, me: tuple[float, float]=(0.0, 0.0), walk_only: bool=False) -> None:
        import threading
        self.p = {"target_mode": 2, "speed_beast": "auto"}
        self.lines: list[str] = []
        self.walked: list[tuple[float, ...]] = []
        self.traveled: list[int] = []
        self.target_modes: list[int] = []
        self.walk_only = walk_only
        self.traveling = False
        self._mid = mid
        self._me = {"x": me[0], "y": me[1]}
        self.entities: list[dict[str, Any]] = []
        self.daily_stop_at = 0.0
        self.stop_event = threading.Event()

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def me(self) -> dict[str, Any]:
        return self._me

    def map_id(self) -> int:
        return self._mid

    def travel(self, mid: int) -> None:
        self.traveled.append(mid)

    def walk_to(self, cell: tuple[float, float], **kw: object) -> None:
        self.walked.append(tuple(cell))

    def snap(self) -> dict[str, Any]:
        return {"entities": list(self.entities)}

    def set_target_mode(self, mode: int) -> bool:
        self.target_modes.append(mode)
        return True

    def sleep(self, seconds: float) -> None:
        pass


def write_record(mid: int, data: dict[str, Any]) -> None:
    os.makedirs(survey.out_dir(), exist_ok=True)
    with open(os.path.join(survey.out_dir(), "%d.json" % mid), "w", encoding="utf-8") as f:
        json.dump(data, f)


def rec_with(mid: int, npcs: dict[str, dict[str, str | int | None] | dict[str, str | int | list[dict[str, str]]] | dict[str, str | int | list[dict[str, list[Any]]]]]) -> Record:
    """A saved record on disk with these NPCs, swept."""
    write_record(mid, {"map": mid, "name": "Map %s" % mid, "npcs": npcs, "swept": True})
    return survey.Record(mid)


class Grid:
    """A 2-D lookup m.directional[y, x] / m.portal[y, x] that is one value with exceptions."""

    def __init__(self, value: int, true: Iterable[tuple[int, int]] = ()) -> None:
        self.value, self.true = value, set(true)

    def __getitem__(self, key: tuple[int, int]) -> int:
        y, x = key
        return 1 if (x, y) in self.true else self.value


class FakeModel:
    def __init__(self, w: int=200, h: int=200, walkable: bool=True, floor: tuple[int, int] | None=None,
                 portal: list[tuple[int, int]] | tuple[()]=(), directional: list[tuple[int, int]] | tuple[()]=()) -> None:
        self.w, self.h = w, h
        self.directional = Grid(0, directional)
        self.portal = Grid(0, portal)
        self._walkable, self._floor = walkable, floor
        self.markers = []
        self.comp_calls: list[tuple[int, int]] = []
        self.walk_calls: list[tuple[int, int]] = []

    def ok(self, x: int, y: int) -> bool:
        return self._walkable

    def comp_of(self, x: int, y: int) -> int:
        self.comp_calls.append((x, y))
        return 0

    def nearest_walkable(self, x: int, y: int, r: int, comp: int | None=None) -> Cell | None:
        self.walk_calls.append((int(x), int(y)))
        return (int(x), int(y)) if self._walkable else None

    def nearest_floor(self, x: int, y: int, r: int, comp: int | None=None) -> Cell | None:
        return self._floor


# ================================================================== module constants / pure text
def test_deny_matches_forbidden_words() -> None:
    assert survey.DENY.search("Buy")
    assert survey.DENY.search("Synthesize 30")
    assert survey.DENY.search("Teleport")
    assert survey.DENY.search("Claim rewards")


def test_deny_uses_whole_words_not_substrings() -> None:
    assert survey.DENY.search("normal") is None
    assert survey.DENY.search("nothing else") is None


def test_plain_lowercases_and_drops_numbers() -> None:
    assert survey.plain("Level 1 Opal Shard") == "level opal shard"
    assert survey.plain("  A   B 12 ") == "a b"
    assert survey.plain(None) == ""


def test_words_collects_every_field_and_drops_empty() -> None:
    e = {"text": "t", "raw": "r", "f": "f", "g": "g", "h": "h", "i": "i", "options": ["o", ""]}
    assert survey.words(e) == ["t", "r", "f", "g", "h", "i", "o"]
    assert survey.words({}) == []


def test_label_prefers_text_then_raw_then_first_option() -> None:
    assert survey.label({"text": "t", "raw": "r", "options": ["o"]}) == "t"
    assert survey.label({"raw": "r", "options": ["o"]}) == "r"
    assert survey.label({"options": ["o"]}) == "o"
    assert survey.label({}) == ""


def test_exact_wraps_the_whole_line() -> None:
    assert re.match(survey.exact("Buy now"), "  Buy now ") is not None
    assert re.match(survey.exact("Buy now"), "Buy now please") is None


def test_is_thing_for_list_rows_and_known_items(game_texts: Path) -> None:
    (game_texts / "item.txt").write_text("Level $1 Opal Shard\nLevel 1 Opal Shard\n", encoding="utf-8")
    assert survey.is_thing("Heavenly Inferno Belt;rare") is True
    assert survey.is_thing("Level 1 Opal Shard") is True
    assert survey.is_thing("About this place") is False
    assert survey.is_thing(None) is False


def test_safe_accepts_plain_information() -> None:
    assert survey.safe(info("About this place")) is True
    assert survey.safe(info("More information", flags=8)) is True


def test_safe_rejects_the_wrong_flags() -> None:
    assert survey.safe(info("About this place", flags=0)) is False
    assert survey.safe(info("About this place", flags=16)) is False


def test_safe_rejects_denied_words() -> None:
    assert survey.safe(info("Confirm")) is False
    assert survey.safe({"flags": 12, "options": ["More"], "f": "Buy now"}) is False


def test_safe_rejects_rows_of_things() -> None:
    assert survey.safe(info("Heavenly Inferno Belt;rare")) is False


def test_is_npc_by_the_games_kind_when_side_is_reported() -> None:
    assert survey.is_npc({"kind": "npc", "side": 1}) is True
    assert survey.is_npc({"kind": "monster", "side": 1}) is False


def test_is_npc_by_negative_id_and_hp_without_side() -> None:
    assert survey.is_npc({"id": -1, "hp": None}) is True
    assert survey.is_npc({"id": -1, "hp": survey.NPC_HP}) is True
    assert survey.is_npc({"id": -1, "maxhp": survey.NPC_HP}) is True
    assert survey.is_npc({"id": -1, "hp": 1000}) is False
    assert survey.is_npc({"id": 5, "hp": survey.NPC_HP}) is False


def test_critter_is_a_small_hp_wanderer() -> None:
    assert survey.critter({"hp": 1000}) is True
    assert survey.critter({"hp": survey.NPC_HP}) is False
    assert survey.critter({"hp": None}) is False


def test_page_records_every_entry() -> None:
    d = {"top": top([[info("Alpha"), None], [info("Beta", flags=8, cmd=[2])]], kind="shop")}
    p = survey.page(d)
    assert p["kind"] == "shop"
    assert [(e["row"], e["col"], e["text"], e["flags"]) for e in p["entries"]] == [
        (0, 0, "Alpha", 12), (1, 0, "Beta", 8)]
    assert p["entries"][1]["cmd"] == [2]


# ================================================================== language files
def test_pairs_reads_the_last_line_of_each_block_and_strips_placeholders(game_texts: Path) -> None:
    (game_texts / "item.txt").write_text(
        "original\nTranslated $5 out\n\nsecond original\nsecond translated\n", encoding="utf-8")
    assert survey._pairs("item.txt") == ["Translated  out", "second translated"]


def test_pairs_missing_file_is_empty(game_texts: Path) -> None:
    assert survey._pairs("nope.txt") == []


def test_described_parses_level_kind_and_text(game_texts: Path) -> None:
    (game_texts / "role_description_npc.txt").write_text(
        "Guard; level 20 guard; A sturdy guard\n\nSignpost; A wooden sign\n", encoding="utf-8")
    about = survey.described()
    assert about["guard"] == [(20, "guard", "A sturdy guard")]
    assert about["signpost"] == [(None, "npc", "A wooden sign")]


def test_described_caches_the_result(game_texts: Path) -> None:
    f = game_texts / "role_description_npc.txt"
    f.write_text("Guard; level 20 guard; A sturdy guard\n", encoding="utf-8")
    first = survey.described()
    f.write_text("Something else; level 1 npc; changed\n", encoding="utf-8")
    assert survey.described() is first


def test_item_names_collects_plain_names(game_texts: Path) -> None:
    (game_texts / "item.txt").write_text("Level $1 Opal Shard\nLevel 1 Opal Shard\n", encoding="utf-8")
    assert survey.item_names() == {"level opal shard"}


def test_item_names_empty_without_a_file() -> None:
    assert survey.item_names() == set()


def test_out_dir_creates_it(survey_env: Path) -> None:
    d = survey.out_dir()
    assert os.path.isdir(d)
    assert os.path.abspath(str(survey_env)) == d


# ================================================================== sweep points
def test_sweep_points_none_when_nothing_is_walkable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: FakeModel(walkable=False)))
    assert survey.sweep_points(5) == []


def test_sweep_points_covers_the_map_and_starts_near_us(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: FakeModel(w=200, h=200)))
    pts = survey.sweep_points(5, (199, 199))
    assert len(pts) == 16
    assert pts[0] == (200, 200)          # the nearest of the grid stops to where we stand


def test_sweep_points_uses_the_rect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: FakeModel(w=200, h=200)))
    pts = survey.sweep_points(5, None, (1, 1, 100, 100))
    assert pts == [(34, 34), (100, 34), (100, 100), (34, 100)]


def test_sweep_points_skips_stops_on_an_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: FakeModel(w=200, h=200, portal=[(34, 34)])))
    pts = survey.sweep_points(5)
    assert (34, 34) not in pts
    assert len(pts) == 15


def test_sweep_points_never_stops_on_a_ledge(monkeypatch: pytest.MonkeyPatch) -> None:
    def model(mid: int):
        m = FakeModel(w=200, h=200, directional=[(34, 34)], floor=(999, 999))
        return m
    monkeypatch.setattr(atlas, "model", model)
    pts = survey.sweep_points(5)
    assert (999, 999) in pts


def test_sweep_points_comp_of_and_walkable_use_our_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    m = FakeModel(w=200, h=200)
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: m))
    survey.sweep_points(5, (10, 10))
    assert m.comp_calls == [(10, 10)]
    assert (34, 34) in m.walk_calls


# ================================================================== Record
def test_record_new_has_the_defaults() -> None:
    r = survey.Record(5)
    assert r.data["map"] == 5 and r.data["name"] == "Map 5"
    assert r.data["npcs"] == {} and r.data["swept"] is False
    assert "started" in r.data


def test_record_key_rounds_the_position() -> None:
    r = survey.Record(5)
    assert r.key({"name": "Duck", "x": 1.6, "y": 2.4}) == "Duck@2,2"


def test_record_saw_creates_an_entry() -> None:
    r = survey.Record(5)
    n = r.saw({"name": "Guard", "x": 1.0, "y": 2.0, "id": -1, "kind": "npc", "side": 1, "hp": 5})
    assert n["name"] == "Guard" and n["x"] == 1.0 and n["menus"] is None
    assert r.data["npcs"]["Guard@1,2"] is n


def test_record_saw_a_walking_npc_moves_its_one_entry() -> None:
    r = survey.Record(5)
    r.saw({"name": "Duck", "x": 1.0, "y": 1.0, "id": -9, "hp": 1000})
    r.saw({"name": "Duck", "x": 5.0, "y": 5.0, "id": -9, "hp": 1000})
    assert len(r.data["npcs"]) == 1
    n = r.data["npcs"]["Duck@1,1"]
    assert (n["x"], n["y"]) == (5.0, 5.0)
    assert n["walks"] == [1.0, 1.0, 5.0, 5.0]


def test_record_saw_monster_limits_cells_and_keeps_maxhp() -> None:
    r = survey.Record(5)
    for i in range(20):
        r.saw_monster({"name": "Wolf", "id": 5, "x": i, "y": 0, "hp": 100, "maxhp": 200 + i})
    m = r.data["monsters"]["Wolf"]
    assert m["maxhp"] == 219
    assert len(m["cells"]) == 12
    assert m["ids"] == [5]


def test_record_save_then_load_roundtrip() -> None:
    r = survey.Record(5)
    r.saw({"name": "Guard", "x": 1.0, "y": 2.0, "id": -1})
    r.save()
    assert "updated" in r.data
    again = survey.Record(5)
    assert "Guard@1,2" in again.data["npcs"]


def test_record_merge_walkers_folds_old_records() -> None:
    write_record(5, {"map": 5, "npcs": {
        "Duck@1,1": {"name": "Duck", "x": 1, "y": 1, "id": -9, "menus": None},
        "Duck@2,2": {"name": "Duck", "x": 2, "y": 2, "id": -9, "menus": [{"path": []}]}}, "swept": True})
    r = survey.Record(5)
    assert len(r.data["npcs"]) == 1
    n = r.data["npcs"]["Duck@1,1"]
    assert (n["x"], n["y"]) == (2, 2)
    assert n["walks"] == [1, 1, 2, 2]
    assert n["menus"] == [{"path": []}]


def test_record_moved_expands_the_walking_box() -> None:
    n = {"x": 5, "y": 5}
    survey.Record.moved(n, 2, 9)
    assert n["walks"] == [2, 5, 5, 9]


# ================================================================== look
def test_look_notes_npcs_and_monsters() -> None:
    r = survey.Record(5)
    b = Bot()
    b.entities = [
        {"name": "Guard", "x": 1, "y": 1, "id": -1, "kind": "npc", "side": 1},
        {"name": "Wolf", "x": 2, "y": 2, "id": 5, "kind": "monster", "side": 1, "hp": 100},
        {"name": "Rock", "x": 3, "y": 3, "id": 6, "kind": "object", "side": 1},
    ]
    assert survey.look(as_bot(b), r) == 1
    assert "Guard@1,1" in r.data["npcs"]
    assert "Wolf" in r.data["monsters"]


# ================================================================== worth_noting
def test_worth_noting_an_npc_with_side() -> None:
    assert survey.worth_noting({"id": -1, "name": "Guard", "kind": "npc", "side": 1}) is True


def test_worth_noting_rejects_positive_ids_and_empty_names() -> None:
    assert survey.worth_noting({"id": 5, "name": "Wolf", "kind": "monster", "side": 1}) is False
    assert survey.worth_noting({"id": -1, "name": ""}) is False


def test_worth_noting_rejects_a_dropped_item(game_texts: Path) -> None:
    (game_texts / "item.txt").write_text("Strange Box\n", encoding="utf-8")
    assert survey.worth_noting({"id": -1, "name": "Strange Box", "hp": None}) is False


def test_worth_noting_an_object_the_texts_call_an_npc(game_texts: Path) -> None:
    (game_texts / "role_description_npc.txt").write_text(
        "Signpost; A wooden sign\n", encoding="utf-8")
    assert survey.worth_noting({"id": -1, "name": "Signpost", "kind": "other", "side": 5}) is True


def test_worth_noting_ignores_other_players_pets() -> None:
    assert survey.worth_noting({"id": -1, "name": "Pet", "kind": "other", "side": 5000}) is False


# ================================================================== open_npc / heard / acted
def test_open_npc_walks_the_path_and_returns_the_dialog() -> None:
    tree = {(): top([[info("About")]]), ("About",): top([[info("History")]])}
    b = MenuBot(tree)
    d = survey.open_npc(as_bot(b), {"name": "Guard", "x": 1, "y": 1}, ["About"])
    assert d is not None and d["open"] == 1 and d["top"] is tree[("About",)]
    assert b.closed == 1


def test_open_npc_none_when_talk_fails() -> None:
    b = MenuBot({(): top([[info("About")]])})
    b.talk_ok = False
    assert survey.open_npc(as_bot(b), {"name": "Guard", "x": 1, "y": 1}, []) is None


def test_open_npc_none_when_a_choice_is_missing() -> None:
    b = MenuBot({(): top([[info("About")]])})
    assert survey.open_npc(as_bot(b), {"name": "Guard", "x": 1, "y": 1}, ["Nope"]) is None


def test_open_npc_none_when_no_dialog_follows() -> None:
    tree = {(): top([[info("About")]])}          # choosing About leads nowhere
    b = MenuBot(tree)
    assert survey.open_npc(as_bot(b), {"name": "Guard", "x": 1, "y": 1}, ["About"]) is None


def test_heard_reads_what_the_game_said() -> None:
    b = MenuBot({}, said=["Requirements not met"])
    assert survey.heard(as_bot(b), 1.0) == ["Requirements not met"]


def test_heard_an_old_agent_returns_empty(allow_ignored_failures: None) -> None:
    b = MenuBot({})
    b.a = Link(fail=True)
    assert survey.heard(as_bot(b), 1.0) == []


def test_heard_a_non_dict_answer_returns_empty() -> None:
    b = MenuBot({})
    b.a = JsonOnly(lambda line: ["nope"])
    assert survey.heard(as_bot(b), 1.0) == []


def test_acted_logs_the_spoken_answer_and_returns_true() -> None:
    b = MenuBot({})
    assert survey.acted(as_bot(b), {"name": "Guard"}, ["Buy", "Confirm"], ["taken out"]) is True
    assert any("taken out" in line for line in b.logs)


# ================================================================== explore
def child_tree() -> dict[tuple[str, ...], dict[str, Any]]:
    return {(): top([[info("About")], [info("Rules", flags=0)]],
                     kind="npc"),
            ("About",): top([[info("History")]]),
            ("About", "History"): top([[info("Done")]])}


def test_explore_walks_safe_children_only() -> None:
    b = MenuBot(child_tree())
    pages = survey.explore(as_bot(b), survey.Record(5), {"name": "Guard", "x": 1, "y": 1})
    paths = [tuple(p["path"]) for p in pages]
    assert () in paths
    assert ("About",) in paths
    assert ("Rules",) not in paths          # flags 0 is not safe
    assert b.closed >= 1


def test_explore_records_a_page_that_did_not_open() -> None:
    b = MenuBot({(): top([[info("About")]])})
    b.talk_ok = False
    pages = survey.explore(as_bot(b), survey.Record(5), {"name": "Guard", "x": 1, "y": 1})
    assert pages == [{"path": [], "error": "didn't open"}]


def test_explore_uses_the_talk_problem_at_the_root() -> None:
    b = MenuBot({(): top([[info("About")]])})
    b.talk_ok = False
    b.talk_problem = "not found"
    pages = survey.explore(as_bot(b), survey.Record(5), {"name": "Guard", "x": 1, "y": 1})
    assert pages[0]["error"] == "not found"


def test_explore_stops_after_a_spoken_answer_on_a_submenu() -> None:
    tree = {(): top([[info("About")]]), ("About",): top([[info("History")]])}
    b = MenuBot(tree, said=["taken out"])
    pages = survey.explore(as_bot(b), survey.Record(5), {"name": "Guard", "x": 1, "y": 1})
    assert pages[-1]["path"] == ["About"]
    assert pages[-1]["said"] == ["taken out"]
    assert any("taken out" in line for line in b.logs)


def test_explore_folds_a_duplicate_page_into_the_one_seen() -> None:
    same = top([[info("About")]])
    b = MenuBot({(): same, ("About",): same})
    pages = survey.explore(as_bot(b), survey.Record(5), {"name": "Guard", "x": 1, "y": 1})
    assert [p["path"] for p in pages] == [[]]      # the identical child is not recorded again


def test_explore_respects_max_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(survey, "MAX_DEPTH", 1)
    b = MenuBot(child_tree())
    pages = survey.explore(as_bot(b), survey.Record(5), {"name": "Guard", "x": 1, "y": 1})
    paths = [tuple(p["path"]) for p in pages]
    assert ("About",) in paths
    assert ("About", "History") not in paths


def test_explore_respects_max_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(survey, "MAX_PAGES", 1)
    b = MenuBot(child_tree())
    pages = survey.explore(as_bot(b), survey.Record(5), {"name": "Guard", "x": 1, "y": 1})
    assert len(pages) == 1


# ================================================================== survey_map
def test_survey_map_sweeps_and_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot(mid=5)
    monkeypatch.setattr(survey, "sweep_points", stand_in(survey.sweep_points, returning([(1, 1), (2, 2)])))
    data = survey.survey_map(as_bot(bot), 5, talk=False, go=False)
    assert bot.walked == [(1, 1), (2, 2)]
    assert data["swept"] is True and data["sweep_rect"] is None
    assert os.path.exists(survey.Record(5).path)
    assert not bot.traveled


def test_survey_map_go_travels_there(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot(mid=5)
    monkeypatch.setattr(survey, "sweep_points", stand_in(survey.sweep_points, returning([])))
    survey.survey_map(as_bot(bot), 5, talk=False, go=True)
    assert bot.traveled == [5]


def test_survey_map_already_swept_does_not_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    write_record(5, {"map": 5, "name": "Map 5", "npcs": {}, "swept": True})
    bot = Bot(mid=5)
    called: list[int] = []
    monkeypatch.setattr(survey, "sweep_points", stand_in(survey.sweep_points, lambda mid, start=None, rect=None: called.append(1) or []))
    data = survey.survey_map(as_bot(bot), 5, talk=False, go=False)
    assert called == [] and bot.walked == []
    assert data["swept"] is True


def test_survey_map_reads_menus_off_the_beast(monkeypatch: pytest.MonkeyPatch) -> None:
    write_record(5, {"map": 5, "name": "Map 5", "swept": True,
                     "npcs": {"Guard@1,1": {"name": "Guard", "x": 1, "y": 1, "id": -1, "menus": None}}})
    bot = Bot(mid=5, walk_only=True)
    seen: list[object] = []            # the real Bot handed to beasts.off, then a flag
    monkeypatch.setattr(beasts, "off", stand_in(beasts.off, lambda b: seen.append(b)))
    monkeypatch.setattr(survey, "read_menus", stand_in(survey.read_menus, lambda b, rec, redo=False: seen.append(b.walk_only)))
    survey.survey_map(as_bot(bot), 5, talk=True, go=False)
    assert seen[0] is bot                 # beasts.off(b) called
    assert seen[1] is False               # read_menus ran with walk_only off
    assert bot.walk_only is True          # and it was put back


# ================================================================== silent / retry
def test_silent_is_talked_to_and_never_answers() -> None:
    assert survey.silent({"menus": [{"error": "no answer"}]}) is True
    assert survey.silent({"menus": [{"error": "didn't open"}]}) is False
    assert survey.silent({"menus": None}) is False
    assert survey.silent({"menus": []}) is False


def test_retry_gives_a_stuck_npc_its_second_try() -> None:
    stuck = {"menus": [{"error": "out of reach"}], "tries": 1}
    assert survey.retry(stuck) is True
    assert survey.retry({"menus": [{"error": "out of reach"}], "tries": 2}) is False
    assert survey.retry({"menus": [{"error": "out of reach"}, {"said": ["hi"]}], "tries": 1}) is False
    assert survey.retry({"menus": [{"error": "no answer"}], "tries": 1}) is False


# ================================================================== read_menus
def test_read_menus_skips_those_already_read(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = rec_with(5, {"Guard@1,1": {"name": "Guard", "x": 1, "y": 1, "id": -1,
                                     "menus": [{"path": []}]}})
    bot = Bot()
    called: list[dict[str, Any]] = []
    monkeypatch.setattr(survey, "explore", stand_in(survey.explore, lambda b, rec, n: called.append(n) or []))
    survey.read_menus(as_bot(bot), rec)
    assert called == []
    assert bot.target_modes == [3, 2]     # set, then put back to the profile's mode


def test_read_menus_marks_a_critter_without_exploring(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = rec_with(5, {"Duck@1,1": {"name": "Duck", "x": 1, "y": 1, "id": -9, "hp": 1000, "menus": None}})
    bot = Bot()
    monkeypatch.setattr(survey, "explore", stand_in(survey.explore, lambda b, rec, n: (_ for _ in ()).throw(AssertionError)))
    survey.read_menus(as_bot(bot), rec)
    assert rec.data["npcs"]["Duck@1,1"]["menus"] == []
    assert rec.data["npcs"]["Duck@1,1"]["critter"] is True


def test_read_menus_makes_a_twin_of_a_silent_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = rec_with(5, {
        "Guard@1,1": {"name": "Guard", "x": 1, "y": 1, "id": -1, "menus": None},
        "Guard@2,2": {"name": "Guard", "x": 2, "y": 2, "id": -2, "menus": [{"error": "no answer"}]}})
    bot = Bot()
    monkeypatch.setattr(survey, "explore", stand_in(survey.explore, lambda b, rec, n: (_ for _ in ()).throw(AssertionError)))
    survey.read_menus(as_bot(bot), rec)
    n = rec.data["npcs"]["Guard@1,1"]
    assert n["menus"] == [] and n["same_as"] == "Guard@2,2"


def test_read_menus_catches_one_odd_npc(monkeypatch: pytest.MonkeyPatch, allow_ignored_failures: None) -> None:
    rec = rec_with(5, {"Guard@1,1": {"name": "Guard", "x": 1, "y": 1, "id": -1, "menus": None}})
    bot = Bot()
    monkeypatch.setattr(survey, "explore", stand_in(survey.explore, lambda b, rec, n: (_ for _ in ()).throw(ValueError("odd"))))
    survey.read_menus(as_bot(bot), rec)
    assert rec.data["npcs"]["Guard@1,1"]["menus"] == [{"error": "odd"}]


def test_read_menus_lets_stopped_through(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = rec_with(5, {"Guard@1,1": {"name": "Guard", "x": 1, "y": 1, "id": -1, "menus": None}})
    bot = Bot()
    monkeypatch.setattr(survey, "explore", stand_in(survey.explore, lambda b, rec, n: (_ for _ in ()).throw(Stopped("stop"))))
    with pytest.raises(Stopped):
        survey.read_menus(as_bot(bot), rec)


def test_read_menus_redo_reads_again(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = rec_with(5, {"Guard@1,1": {"name": "Guard", "x": 1, "y": 1, "id": -1,
                                     "menus": [{"path": []}]}})
    bot = Bot()
    called: list[dict[str, Any]] = []
    monkeypatch.setattr(survey, "explore", stand_in(survey.explore, lambda b, rec, n: called.append(n) or [{"path": []}]))
    survey.read_menus(as_bot(bot), rec, redo=True)
    assert len(called) == 1


# ================================================================== the map plan
def test_city_rect_none_without_towns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "maps", stand_in(atlas.maps, lambda: {5: {"towns": [], "w": 100, "h": 100}}))
    assert survey.city_rect(5) is None


def test_city_rect_merges_and_pads_the_towns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "maps", stand_in(atlas.maps, lambda: {5: {
        "towns": [["", 10, 20, 30, 40], ["", 50, 5, 60, 25]], "w": 200, "h": 200}}))
    assert survey.city_rect(5, pad=12) == (1, 1, 72, 52)


def test_hints_empty_for_a_plain_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allow_ignored_failures: None) -> None:
    monkeypatch.setattr(atlas, "maps", stand_in(atlas.maps, lambda: {5: {"towns": [], "w": 100, "h": 100}}))
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: (_ for _ in ()).throw(ValueError)))
    monkeypatch.setattr(knowledge, "data", stand_in(knowledge.data, lambda: {"npcs": []}))
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    assert survey.hints(5) == []


def test_hints_collects_towns_dungeons_transports_and_known_npcs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allow_ignored_failures: None) -> None:
    monkeypatch.setattr(atlas, "maps", stand_in(atlas.maps, lambda: {5: {"towns": [["", 1, 2, 3, 4]], "w": 100, "h": 100}}))
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: (_ for _ in ()).throw(ValueError)))
    monkeypatch.setattr(knowledge, "data", stand_in(knowledge.data, lambda: {"npcs": [
        {"map": 5, "x": 30, "y": 31}, {"map": 6, "x": 1, "y": 1}]}))
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    (tmp_path / "dungeons.json").write_text(json.dumps({"entrances": [
        {"map": 5, "spots": [[7, 8, "entrance"]], "at": [9, 10], "stand": [11, 12]}]}), encoding="utf-8")
    (tmp_path / "transports.json").write_text(json.dumps({"transports": [
        {"map": 5, "at": [20, 21]}]}), encoding="utf-8")
    assert survey.hints(5) == [(1, 2, 3, 4), (7, 8, 7, 8), (9, 10, 9, 10), (11, 12, 11, 12),
                               (20, 21, 20, 21), (30, 31, 30, 31)]


def test_hint_rect_none_without_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(survey, "hints", stand_in(survey.hints, lambda mid: []))
    assert survey.hint_rect(5) is None


def test_hint_rect_pads_around_the_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(survey, "hints", stand_in(survey.hints, lambda mid: [(1, 2, 3, 4), (10, 20, 10, 20)]))
    monkeypatch.setattr(atlas, "maps", stand_in(atlas.maps, lambda: {5: {"w": 100, "h": 100}}))
    assert survey.hint_rect(5, pad=55) == (1, 1, 65, 75)


def _plan_maps() -> dict[int, Any]:
    return {
        1: {"towns": [["", 1, 1, 2, 2]], "exits": [{"dest": 2}]},
        2: {"exits": [{"dest": 3}]},
        3: {"towns": []},
        1001: {},
        1042: {},
    }


def test_plan_targets_a_list_is_taken_as_is() -> None:
    assert survey.plan_targets(["5", 6, "7"]) == [5, 6, 7]


def test_plan_targets_everything_is_every_open_world_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "maps", _plan_maps)
    monkeypatch.setattr(travel, "is_instance", stand_in(travel.is_instance, lambda mid: mid == 3))
    assert survey.plan_targets("everything") == [1, 2, 1001, 1042]


def test_plan_targets_all_keeps_maps_with_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "maps", _plan_maps)
    monkeypatch.setattr(travel, "is_instance", stand_in(travel.is_instance, lambda mid: False))
    monkeypatch.setattr(survey, "hints", stand_in(survey.hints, lambda mid: [1] if mid in (1, 3) else []))
    assert survey.plan_targets("all") == [1, 3]


def test_plan_targets_cities_keeps_towns_and_the_extra_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "maps", _plan_maps)
    monkeypatch.setattr(travel, "is_instance", stand_in(travel.is_instance, lambda mid: False))
    assert survey.plan_targets("cities") == [1, 1001, 1042]


def test_tour_walks_the_exit_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "maps", stand_in(atlas.maps, lambda: {
        1: {"exits": [{"dest": 2}]}, 2: {"exits": [{"dest": 3}]}, 3: {"exits": []}}))
    assert survey.tour(1, [1, 2, 3]) == [1, 2, 3]


def test_tour_puts_unreachable_targets_last(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "maps", stand_in(atlas.maps, lambda: {
        1: {"exits": [{"dest": 2}]}, 2: {"exits": []}, 5: {"exits": []}}))
    assert survey.tour(1, [1, 2, 5]) == [1, 2, 5]


def test_done_needs_a_sweep_and_read_menus() -> None:
    write_record(5, {"map": 5, "npcs": {}, "swept": True})
    assert survey.done(5) is True
    assert survey.done(5, talk=False) is True
    write_record(6, {"map": 6, "npcs": {"Guard@1,1": {"name": "Guard", "x": 1, "y": 1,
                                                      "id": -1, "menus": None}}, "swept": True})
    assert survey.done(6) is False
    write_record(7, {"map": 7, "npcs": {}, "swept": False})
    assert survey.done(7) is False


# ================================================================== survey / _survey
def test_survey_sets_walk_only_and_restores_it(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot(walk_only=False)
    seen: list[bool] = []
    monkeypatch.setattr(beasts, "owned", stand_in(beasts.owned, lambda b: [{"kind": "swift"}]))
    monkeypatch.setattr(survey, "_survey", stand_in(survey._survey, lambda b, scope, talk, redo: seen.append(b.walk_only)))
    survey.survey(as_bot(bot), "here")
    assert seen == [True]
    assert bot.walk_only is False


def test_survey_warns_when_there_is_no_swift_beast(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot()
    monkeypatch.setattr(beasts, "owned", stand_in(beasts.owned, lambda b: [{"kind": "fierce"}]))
    monkeypatch.setattr(survey, "_survey", stand_in(survey._survey, lambda b, scope, talk, redo: None))
    survey.survey(as_bot(bot), "here")
    assert any("no Swift" in line for line in bot.lines)


def test_survey_here_surveys_the_map_we_stand_on(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot(mid=9)
    calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(survey, "survey_map",
                        stand_in(survey.survey_map, lambda b, mid, talk=True, rect=None, redo=False, go=True: calls.append((mid, go))))
    survey._survey(as_bot(bot), "here", talk=True, redo=False)
    assert calls == [(9, False)]
    assert any("survey finished" in line for line in bot.lines)


def test_survey_skips_a_map_already_done(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot(mid=1)
    monkeypatch.setattr(survey, "plan_targets", stand_in(survey.plan_targets, lambda scope: [1, 2]))
    monkeypatch.setattr(survey, "tour", stand_in(survey.tour, lambda start, targets: targets))
    monkeypatch.setattr(survey, "done", stand_in(survey.done, lambda mid, talk: mid == 1))
    seen: list[int] = []
    monkeypatch.setattr(survey, "survey_map", stand_in(survey.survey_map, lambda b, mid, talk=True, rect=None, redo=False, go=True: seen.append(mid)))
    survey._survey(as_bot(bot), "cities", talk=True, redo=False)
    assert seen == [2]


def test_survey_retries_a_map_we_respawned_on(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot(mid=1)
    monkeypatch.setattr(survey, "plan_targets", stand_in(survey.plan_targets, lambda scope: [1]))
    monkeypatch.setattr(survey, "tour", stand_in(survey.tour, lambda start, targets: targets))
    monkeypatch.setattr(survey, "done", stand_in(survey.done, lambda mid, talk: False))
    monkeypatch.setattr(survey, "city_rect", stand_in(survey.city_rect, lambda mid: None))
    calls: list[int] = []

    def sm(b: Bot, mid: int, **k: object) -> dict[str, Any] | None:
        calls.append(mid)
        if len(calls) < 3:
            raise Respawned()
    monkeypatch.setattr(survey, "survey_map", sm)
    survey._survey(as_bot(bot), "cities", talk=True, redo=False)
    assert len(calls) == 3
    assert not any("skipping" in line for line in bot.lines)


def test_survey_gives_up_after_three_deaths(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot(mid=1)
    monkeypatch.setattr(survey, "plan_targets", stand_in(survey.plan_targets, lambda scope: [1]))
    monkeypatch.setattr(survey, "tour", stand_in(survey.tour, lambda start, targets: targets))
    monkeypatch.setattr(survey, "done", stand_in(survey.done, lambda mid, talk: False))
    monkeypatch.setattr(survey, "city_rect", stand_in(survey.city_rect, lambda mid: None))
    monkeypatch.setattr(survey, "survey_map", stand_in(survey.survey_map, lambda b, mid, talk=True, rect=None, redo=False, go=True: (_ for _ in ()).throw(Respawned())))
    survey._survey(as_bot(bot), "cities", talk=True, redo=False)
    assert any("died there every time" in line for line in bot.lines)


def test_survey_raises_on_a_final_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot(mid=1)
    monkeypatch.setattr(survey, "plan_targets", stand_in(survey.plan_targets, lambda scope: [1]))
    monkeypatch.setattr(survey, "tour", stand_in(survey.tour, lambda start, targets: targets))
    monkeypatch.setattr(survey, "done", stand_in(survey.done, lambda mid, talk: False))
    monkeypatch.setattr(survey, "city_rect", stand_in(survey.city_rect, lambda mid: None))
    monkeypatch.setattr(survey, "survey_map",
                        stand_in(survey.survey_map, lambda b, mid, talk=True, rect=None, redo=False, go=True: (_ for _ in ()).throw(Stopped("stop", final=True))))
    with pytest.raises(Stopped):
        survey._survey(as_bot(bot), "cities", talk=True, redo=False)


def test_survey_skips_a_map_on_a_non_final_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot(mid=1)
    monkeypatch.setattr(survey, "plan_targets", stand_in(survey.plan_targets, lambda scope: [1, 2]))
    monkeypatch.setattr(survey, "tour", stand_in(survey.tour, lambda start, targets: targets))
    monkeypatch.setattr(survey, "done", stand_in(survey.done, lambda mid, talk: False))
    monkeypatch.setattr(survey, "city_rect", stand_in(survey.city_rect, lambda mid: None))
    seen: list[int] = []

    def sm(b: Bot, mid: int, **k: object) -> dict[str, Any] | None:
        seen.append(mid)
        if mid == 1:
            raise Stopped("stuck", final=False)
    monkeypatch.setattr(survey, "survey_map", sm)
    survey._survey(as_bot(bot), "cities", talk=True, redo=False)
    assert seen == [1, 2]
    assert any("skipping" in line for line in bot.lines)


def test_survey_restores_the_daily_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = Bot(mid=1)
    bot.daily_stop_at = 5 * 3600.0
    monkeypatch.setattr(survey, "plan_targets", stand_in(survey.plan_targets, lambda scope: [1]))
    monkeypatch.setattr(survey, "tour", stand_in(survey.tour, lambda start, targets: targets))
    monkeypatch.setattr(survey, "done", stand_in(survey.done, lambda mid, talk: False))
    monkeypatch.setattr(survey, "city_rect", stand_in(survey.city_rect, lambda mid: None))
    during: list[float] = []
    monkeypatch.setattr(survey, "survey_map", stand_in(survey.survey_map, lambda b, mid, talk=True, rect=None, redo=False, go=True: during.append(b.daily_stop_at)))
    survey._survey(as_bot(bot), "cities", talk=True, redo=False)
    assert during == [0.0]          # 0: no daily stop
    assert bot.daily_stop_at == 5 * 3600.0


def test_survey_without_a_readable_map_id_does_not_tour(monkeypatch: pytest.MonkeyPatch, allow_ignored_failures: None) -> None:
    bot = Bot(mid=1)
    monkeypatch.setattr(bot, "map_id", stand_in(bot.map_id, lambda: (_ for _ in ()).throw(RuntimeError("no map"))))
    monkeypatch.setattr(survey, "plan_targets", stand_in(survey.plan_targets, lambda scope: [1]))
    toured: list[int] = []
    monkeypatch.setattr(survey, "tour", stand_in(survey.tour, lambda start, targets: toured.append(start) or targets))
    monkeypatch.setattr(survey, "done", stand_in(survey.done, lambda mid, talk: False))
    monkeypatch.setattr(survey, "city_rect", stand_in(survey.city_rect, lambda mid: None))
    monkeypatch.setattr(survey, "survey_map", stand_in(survey.survey_map, lambda b, mid, talk=True, rect=None, redo=False, go=True: None))
    survey._survey(as_bot(bot), "cities", talk=True, redo=False)
    assert toured == []
