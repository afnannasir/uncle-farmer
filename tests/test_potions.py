# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""What a potion heals and when to drink it, from the game's own item text - including the twin
guess (Small Blue stands in for Small Red), the 30/97% clamps and the fallback for a potion the
game hasn't described yet."""

from __future__ import annotations

from unclefarmer.engine import potions
import pytest
from conftest import stand_in


BIG_RED = {"name": "Large Red Potion",
           "description": "Recover 15% Max HP and 1500 points of HP, 11 seconds CD."}


def test_parse_reads_a_full_description() -> None:
    assert potions._parse(BIG_RED) == {"pct": 15.0, "flat": 1500, "cd": 11.0, "from": "Large Red Potion"}


def test_parse_accepts_decimals_and_mp() -> None:
    got = potions._parse({"name": "Big Blue Potion",
                          "description": "Recover 12.5% Max MP and 200 points of MP, 3 seconds CD."})
    assert got == {"pct": 12.5, "flat": 200, "cd": 3.0, "from": "Big Blue Potion"}


def test_parse_rejects_missing_or_unrelated_text() -> None:
    assert potions._parse(None) is None
    assert potions._parse({}) is None
    assert potions._parse({"name": "Rock", "description": "does nothing"}) is None


def test_info_reads_real_knowledge() -> None:
    assert potions.info("Large Red Potion") == {
        "pct": 15.0, "flat": 1500, "cd": 11.0, "from": "Large Red Potion"}


def test_info_none_for_missing_name() -> None:
    assert potions.info(None) is None
    assert potions.info("") is None
    assert potions.info("No Such Potion") is None


def test_info_borrows_the_twin_of_the_same_size(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, bool] = {}

    def item(name: str):
        calls[name.lower()] = True
        if name.lower() == "small red potion":
            return {"name": "Small Red Potion",
                    "description": "Recover 20% Max HP and 100 points of HP, 8 seconds CD."}
        return None

    monkeypatch.setattr(potions.knowledge, "item", item)
    got = potions.info("Small Blue Potion")
    assert got == {"pct": 20.0, "flat": 100, "cd": 8.0, "from": "Small Red Potion", "guessed": True}
    assert "small red potion" in calls and "small blue potion" in calls


def test_info_no_guess_for_a_name_without_a_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(potions.knowledge, "item", stand_in(potions.knowledge.item, lambda name: None))
    assert potions.info("Mystery Potion") is None


def test_recovers_percent_plus_flat() -> None:
    inf = {"pct": 10.0, "flat": 500}
    assert potions.recovers(inf, 10000) == 1500.0


def test_mark_is_when_one_potion_is_missing() -> None:
    inf = {"pct": 15.0, "flat": 1500}
    assert potions.mark(inf, 10000) == 70.0        # 15% + 1500 of 10000 = 30% missing


def test_mark_clamps_both_ends() -> None:
    assert potions.mark({"pct": 90.0, "flat": 0}, 1000) == potions.LOWEST_MARK
    assert potions.mark({"pct": 0.0, "flat": 0}, 1_000_000) == potions.HIGHEST_MARK


def test_mark_none_without_info_or_pool() -> None:
    assert potions.mark(None, 10000) is None
    assert potions.mark({"pct": 10.0, "flat": 0}, 0) is None
    assert potions.mark({"pct": 10.0, "flat": 0}, -5) is None


def test_describe_known_potion() -> None:
    line = potions.describe("Large Red Potion", what="HP")
    assert "Large Red Potion recovers 15% of your max HP plus 1500, cooldown 11 seconds." in line
    assert "as soon as that much HP is missing." in line


def test_describe_guessed_potion_names_the_twin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(potions.knowledge, "item", stand_in(potions.knowledge.item, lambda name: (
        {"name": "Small Red Potion", "description": "Recover 20% Max HP and 100 points of HP, 8 seconds CD."}
        if (name or "").lower() == "small red potion" else None)))
    line = potions.describe("Small Blue Potion")
    assert "(Taken from Small Red Potion, the same size.)" in line


def test_describe_unknown_potion_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(potions.knowledge, "item", stand_in(potions.knowledge.item, lambda name: None))
    line = potions.describe("Mystery Potion")
    assert "the game hasn't shown what it heals yet" in line
    assert "below 80%" in line and "every 3 seconds at most" in line


def test_auto_recognises_the_auto_markers() -> None:
    for value in (None, "", "auto", 0, 0.0):
        assert potions.auto(value) is True
    for value in ("80", 80, 3.0, "large"):
        assert potions.auto(value) is False
