"""Each character's results in its own file (results/Name--S1--41.jsonl): the name alone put two
characters of one name, on two servers, in one file. The name-only file a character had becomes its
own the first time it is logged in or farms, all of its history with it."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from conftest import Clock

from unclefarmer import settings, stats


@pytest.fixture(autouse=True)
def results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path / "results"))     # never the real results


HERO = settings.character_profile("Hero", 7, "S1")
OTHER_HERO = settings.character_profile("Hero", 9, "S2")       # the same name on another server
RUN = {"kind": "farming", "character": "Hero", "run": "Night", "start": "2026-11-20 01:00:00",
       "minutes": 30.0, "kills": 100}


def old_file(*records: Mapping[str, object]) -> str:
    os.makedirs(stats.RESULTS, exist_ok=True)
    path = os.path.join(stats.RESULTS, "Hero.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in records)
    return path


def test_a_character_s_results_have_a_file_of_their_own() -> None:
    assert os.path.basename(stats.file_of(HERO)) == "Hero--S1--7.jsonl"
    assert os.path.basename(stats.file_of(OTHER_HERO)) == "Hero--S2--9.jsonl"
    assert os.path.basename(stats.file_of("Hero")) == "Hero.jsonl"          # an older, name-only key


def test_the_old_file_is_read_until_it_is_taken_then_moved_with_its_history() -> None:
    path = old_file(RUN)
    assert [r["kills"] for r in stats.runs(HERO)] == [100]                 # nothing hidden meanwhile
    assert stats.adopt_old_file(HERO) is True
    assert not os.path.exists(path) and os.path.exists(stats.file_of(HERO))
    assert [r["kills"] for r in stats.runs(HERO)] == [100]
    assert stats.adopt_old_file(HERO) is False                             # once
    assert stats.runs(OTHER_HERO) == []                                     # the other Hero: its own
    assert stats.adopt_old_file(OTHER_HERO) is False


def test_a_run_saves_to_the_character_s_own_file_and_names_it() -> None:
    old_file(RUN)
    stats.adopt_old_file(HERO)
    t = stats.Tally("Hero", "Day", clock=Clock(), profile=HERO)
    t.parts[t.main].kills = 5
    t.parts[t.main].seconds = 600.0
    t.save()
    rows = stats.runs(HERO)
    assert [r["kills"] for r in rows] == [100, 5] and rows[-1]["profile"] == HERO
    other = stats.Tally("Hero", "Day", clock=Clock(), profile=OTHER_HERO)
    other.parts[other.main].kills = 3
    other.parts[other.main].seconds = 600.0
    other.save()
    assert [r["kills"] for r in stats.runs(OTHER_HERO)] == [3]              # never mixed again


def test_the_results_window_lists_the_characters_by_their_keys() -> None:
    settings.save_profile(HERO, {"tank": False})
    old_file(RUN)
    assert stats.characters() == ["Hero"]                                   # not taken yet: the old name
    stats.adopt_old_file(HERO)
    assert stats.characters() == [HERO]
