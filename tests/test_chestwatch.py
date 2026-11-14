"""chestwatch.py: what is worth a line, and one game's watcher turning the agent's answers into the
log's lines (maps, things that come and go, the game's chest broadcasts, the bag's gains)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chestwatch as cw  # noqa: E402
from unclefarmer import link, paths  # noqa: E402

CHEST = {"id": -129, "kind": "monster", "side": -2, "name": "Chest", "x": 110.0, "y": 104.0,
         "type": 0, "facing": 7203, "hp": 19998239, "maxhp": 20000000}
WOLF = {"id": -1, "kind": "monster", "side": -2, "name": "Grey wolf", "x": 105.0, "y": 100.0, "hp": 30, "maxhp": 30}
EFFECT = {"id": -2147483647, "kind": "other", "side": 0, "name": "", "x": 101.0, "y": 100.0, "hp": 1, "maxhp": 1}


class Agent:
    """The agent's answers to the watcher's requests, changed by the test between looks."""

    def __init__(self) -> None:
        self.map: dict[str, Any] = {"id": 5281, "name": "Margit Rainforest"}
        self.entities: list[dict[str, Any]] = [WOLF, EFFECT]
        self.said: list[dict[str, Any]] = []
        self.chat: list[dict[str, Any]] = []
        self.bag: list[dict[str, Any]] = [{"name": "Arnebia", "count": 100}]

    def json(self, line: str) -> Any:
        if line == "mem map":
            return self.map
        if line == "mem self":
            return {"id": 5, "kind": "player", "name": "Hero", "x": 100.0, "y": 100.0, "hp": 50, "maxhp": 50}
        if line == "mem entities":
            return [dict(e) for e in self.entities]
        if line == "mem bag":
            return [dict(r) for r in self.bag]
        if line.startswith("said "):
            since = int(line.split()[1])
            return {"now": 0, "said": [r for r in self.said if r["t"] > since]}
        if line.startswith("chat "):
            after = int(line.split()[1])
            rows = [r for r in self.chat if r["n"] > after]
            return {"ok": True, "last": max([after] + [r["n"] for r in self.chat]), "chat": rows}
        return {"ok": False, "error": "unknown"}


@pytest.fixture
def watcher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> cw.Watcher:
    monkeypatch.setattr(paths, "LOGS", str(tmp_path))
    log = cw.Log()
    monkeypatch.setattr(cw.time, "time", lambda: 1000.0)
    return cw.Watcher(1, str(tmp_path), log, 0.5, cw.threading.Event())


def lines(w: cw.Watcher) -> list[str]:
    with open(w.log.path, encoding="utf-8") as f:
        return [line.split(" ", 2)[2] for line in f.read().splitlines()]


def look(w: cw.Watcher, a: Agent) -> None:
    w.look(cast(link.AgentLink, a))


def test_worth_a_look() -> None:
    assert cw.worth_a_look(CHEST)                       # a chest's name, whatever its hp
    assert cw.worth_a_look({"id": -3, "kind": "monster", "name": "Red shard"})    # a drop: no hp
    assert not cw.worth_a_look(WOLF)
    assert not cw.worth_a_look(EFFECT)                  # nameless skill effects come by the hundred
    assert not cw.worth_a_look({"id": 7, "kind": "player", "name": "Bob", "hp": 1, "maxhp": 1})


def test_a_chest_coming_and_going_and_what_came_with_it(watcher: cw.Watcher) -> None:
    a = Agent()
    look(watcher, a)
    a.entities = [WOLF, EFFECT, CHEST]
    look(watcher, a)
    a.entities = [WOLF]
    a.said = [{"t": 5, "text": "Requirements not met"}]
    a.chat = [{"n": 1, "channel": 0, "name": "Bob", "text": "any chest here?"},     # a player: never logged
              {"n": 2, "channel": 37, "name": "",
               "text": "The lucky adventurer has opened the treasure chest in Margit Rainforest."}]
    a.bag = [{"name": "Arnebia", "count": 9290}, {"name": "Level 1 Obsidian Shard", "count": 24}]
    watcher.next_bag = 0.0
    look(watcher, a)
    got = lines(watcher)
    assert got[0].startswith("MAP") and "Margit Rainforest (5281)  (a chest map)" in got[0]
    assert sum(g.startswith("NAME") for g in got) == 3            # the wolf, the effect, the chest: once each
    appear = next(g for g in got if g.startswith("APPEAR"))
    assert "'Chest' at 110.0,104.0, 10.8 steps from us" in appear and "looks like a chest" in appear
    assert any(g.startswith("GONE") and "'Chest'" in g for g in got)
    assert any(g.startswith("SAID") and "Requirements not met" in g for g in got)
    news = [g for g in got if g.startswith("NEWS")]
    assert news == ["NEWS    channel 37: 'The lucky adventurer has opened the treasure chest in Margit Rainforest.'"]
    assert not any("any chest here" in g for g in got)
    assert any(g.startswith("BAG") and "+9190 Arnebia" in g and "+24 Level 1 Obsidian Shard" in g for g in got)


def test_a_new_map_starts_over(watcher: cw.Watcher) -> None:
    a = Agent()
    a.entities = [CHEST]
    look(watcher, a)
    a.map = {"id": 1161, "name": "secluded valley"}
    a.entities = []
    look(watcher, a)
    got = lines(watcher)
    assert [g for g in got if g.startswith("MAP")][-1] == "MAP     secluded valley (1161)"
    assert not any(g.startswith("GONE") for g in got)       # left behind with its map, not gone


def test_a_look_between_maps_writes_nothing(watcher: cw.Watcher) -> None:
    a = Agent()
    a.map = {"ok": False, "error": "noworld"}
    look(watcher, a)
    assert not os.path.exists(watcher.log.path)
