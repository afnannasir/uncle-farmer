"""A chosen confirmation is not proof that the server repaired the gear."""
from __future__ import annotations

from typing import Any

import pytest

from conftest import Clock, as_bot
from test_toolbox_tiers import GameBot, dlg
from unclefarmer.engine import repair


class ConfirmationBot(GameBot):
    def __init__(self, *, consumed: bool, repaired: bool, delay: float = 0) -> None:
        super().__init__(75, {"Large Toolbox": 2})
        self.clock = Clock()
        self.consumed = consumed
        self.repaired = repaired
        self.delay = delay
        self.confirmed_at: float | None = None
        self.gear.warned = {1: "low"}
        self.link.equipment[0]["equipped"] = 1  # worn, not broken: no broken->fixed shortcut

    def choose(self, pat: str) -> dict[str, Any] | None:
        self.chosen.append(pat)
        self.confirmed_at = self.clock.time()
        return {"text": "Confirm"}

    def sleep(self, s: float) -> None:
        self.clock.advance(s)
        if self.confirmed_at is not None and self.clock.time() >= self.confirmed_at + self.delay:
            if self.consumed:
                self.counts["Large Toolbox"] = 1
            if self.repaired:
                self.link.equipment[0]["equipped"] = 1


def test_confirmation_without_server_effect_is_not_a_repair() -> None:
    b = ConfirmationBot(consumed=False, repaired=False)
    assert repair.repair(as_bot(b)) is False
    assert b.gear.warned == {1: "low"}
    assert b.gear.repaired_at == 0 and b.gear.failed_at > 0
    assert not any("all the gear repaired" in line for line in b.lines)


def test_still_broken_does_not_clear_warnings_or_set_repair_time() -> None:
    b = ConfirmationBot(consumed=True, repaired=False)
    b.link.equipment[0]["equipped"] = -1
    b.gear.warned = {1: "broken"}
    assert repair.repair(as_bot(b)) is False
    assert b.gear.warned == {1: "broken"} and b.gear.repaired_at == 0


def test_delayed_consumption_is_verified_before_success() -> None:
    b = ConfirmationBot(consumed=True, repaired=True, delay=1.6)
    assert repair.repair(as_bot(b)) is True
    assert b.counts["Large Toolbox"] == 1
    assert b.gear.repaired_at >= (b.confirmed_at or 0) + 1.6
    assert b.gear.warned == {}


@pytest.mark.parametrize("snapshot", [[], {}, [{"uid": 1, "name": "Helm"}]])
def test_missing_equipment_data_cannot_confirm_repair(snapshot: Any) -> None:
    b = ConfirmationBot(consumed=True, repaired=True)
    original = b.link.json

    def answer(line: str) -> Any:
        return snapshot if line == "mem equipment" else original(line)

    b.link.json = answer
    assert repair.repair(as_bot(b)) is False
    assert b.gear.warned and b.gear.repaired_at == 0


def test_broken_to_repaired_state_is_proof_even_if_count_snapshot_lags() -> None:
    b = ConfirmationBot(consumed=False, repaired=True, delay=0.3)
    b.link.equipment[0]["equipped"] = -1
    assert repair.repair(as_bot(b)) is True
    assert b.link.equipment[0]["equipped"] == 1


def test_unreadable_bag_is_not_treated_as_a_consumed_toolbox() -> None:
    b = ConfirmationBot(consumed=False, repaired=False)
    original = b.link.json

    def answer(line: str) -> Any:
        return {"error": "busy"} if line == "mem bag" else original(line)

    b.link.json = answer
    assert repair.repair(as_bot(b)) is False
    assert b.gear.warned and b.gear.repaired_at == 0


def test_consuming_an_underlevel_toolbox_does_not_prove_worn_gear_repaired() -> None:
    b = ConfirmationBot(consumed=True, repaired=False)
    b.last_me = {"sheet": {"level": 95}}  # Large Toolbox only covers level80
    assert repair.repair(as_bot(b)) is False
    assert b.gear.warned and b.gear.repaired_at == 0


class NoneToDoBot(ConfirmationBot):
    """The game answers "No repairable equipment" to the toolbox."""

    def wait_dialog(self, t: float) -> dict[str, Any] | None:
        return dlg("No repairable equipment", "OK")


def test_nothing_to_repair_with_unreadable_gear_waits_before_asking_again() -> None:
    b = NoneToDoBot(consumed=False, repaired=False)
    original = b.link.json

    def answer(line: str) -> Any:
        return {"error": "busy"} if line == "mem equipment" else original(line)

    b.link.json = answer
    assert repair.repair(as_bot(b)) is False
    assert b.gear.warned and b.gear.failed_at > 0      # check() leaves it for 600 s, not 2 s


def test_nothing_to_repair_with_sound_gear_clears_the_warnings() -> None:
    b = NoneToDoBot(consumed=False, repaired=False)
    assert repair.repair(as_bot(b)) is False
    assert b.gear.warned == {} and b.gear.repaired_at == 0


def test_an_equipped_value_of_zero_counts_as_mended() -> None:
    b = ConfirmationBot(consumed=False, repaired=False)
    b.link.equipment[0]["equipped"] = -1
    b.gear.warned = {1: "broken"}

    def choose(pat: str) -> dict[str, Any] | None:
        b.chosen.append(pat)
        b.link.equipment[0]["equipped"] = 0
        return {"text": "Confirm"}
    b.choose = choose
    assert repair.repair(as_bot(b)) is True
