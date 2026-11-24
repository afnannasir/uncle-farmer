"""Equipment picked up while farming, thrown away by its rarity - never a piece the bag held when
the run began."""
from __future__ import annotations

from typing import Any

from conftest import Clock, as_bot

from unclefarmer.engine import gearthrow


def piece(uid: int, name: str, rarity: str | None, equipped: int = 0) -> dict[str, Any]:
    """A `mem equipment` row as the agent gives it: "o" is the rarity (9: a medal, no rarity)."""
    o = gearthrow.RARITIES.index(rarity) if rarity else 9
    return {"uid": uid, "type": 30000 + uid, "name": name, "equipped": equipped, "n": 0, "o": o, "p": 0, "q": 0}


class Link:
    def __init__(self, bag: list[dict[str, Any]], answer: str = "sent all 1") -> None:
        self.bag = bag
        self.answer = answer
        self.sent: list[str] = []
        self.asked = 0

    def json(self, line: str) -> Any:
        assert line == "mem equipment"
        self.asked += 1
        return list(self.bag)

    def send(self, line: str) -> str:
        self.sent.append(line)
        if line.startswith("discardgear "):
            gone = {int(u) for u in line.split()[1:]}
            self.bag = [r for r in self.bag if r["uid"] not in gone]
        return self.answer


class Bot:
    def __init__(self, bag: list[dict[str, Any]], throw: list[str], answer: str = "sent all 1") -> None:
        self.a = Link(bag, answer)
        self.p: dict[str, Any] = {"gear_throw": throw, "gear_throw_minutes": 30}
        self.clock = Clock()
        self.agent_version = gearthrow.GEAR_DISCARD_AGENT
        self.lines: list[str] = []

    def log(self, text: str, speak: bool = False) -> None:
        self.lines.append(text)


def test_nothing_ticked_never_reads_the_bag() -> None:
    bot = Bot([piece(1, "Iron Sword", "common")], [])
    gearthrow.note_start(as_bot(bot))
    assert gearthrow.sweep(as_bot(bot)) == 0 and bot.a.asked == 0 and bot.a.sent == []


def test_only_pieces_picked_up_during_the_run_go() -> None:
    bot = Bot([piece(1, "Old Sword", "common")], ["common", "crude"])
    gearthrow.note_start(as_bot(bot))
    bot.a.bag += [piece(2, "Iron Sword", "common"), piece(3, "Cloth Hat", "crude"),
                  piece(4, "Dragon Blade", "legendary"), piece(5, "Large Red Potion", None)]
    assert gearthrow.sweep(as_bot(bot)) == 2
    assert bot.a.sent == ["discardgear 2 3"]
    assert [r["uid"] for r in bot.a.bag] == [1, 4, 5]          # the run's start piece and the rest stay
    assert bot.lines == ["threw away Iron Sword (Common), picked up while farming",
                         "threw away Cloth Hat (Crude), picked up while farming"]


def test_a_second_spot_keeps_the_first_start() -> None:
    bot = Bot([piece(1, "Old Sword", "common")], ["common"])
    gearthrow.note_start(as_bot(bot))
    bot.a.bag.append(piece(2, "Iron Sword", "common"))
    gearthrow.note_start(as_bot(bot))                           # the plan's next spot: the run's own start stands
    assert gearthrow.sweep(as_bot(bot)) == 1 and bot.a.sent == ["discardgear 2"]


def test_a_piece_without_rarity_or_worn_is_never_thrown() -> None:
    bot = Bot([], ["common"])
    gearthrow.note_start(as_bot(bot))
    bot.a.bag += [piece(2, "Tutor Medal", None), piece(3, "Iron Sword", "common", equipped=1)]
    assert gearthrow.sweep(as_bot(bot)) == 0 and bot.a.sent == []


def test_an_agent_that_can_t_throw_equipment_sends_nothing() -> None:
    bot = Bot([], ["common"])
    bot.agent_version = gearthrow.GEAR_DISCARD_AGENT - 1
    gearthrow.note_start(as_bot(bot))
    bot.a.bag.append(piece(2, "Iron Sword", "common"))
    assert gearthrow.sweep(as_bot(bot)) == 0 and bot.a.sent == []


def test_the_rarity_is_the_row_s_field_o_as_read_live() -> None:
    seen = {"Firestorm Leggings": 0, "Firestorm Tabard": 3, "Sunfire Robe": 4, "silk hat": 5}
    assert {n: gearthrow.rarity_of({"o": o}) for n, o in seen.items()} == {
        "Firestorm Leggings": "crude", "Firestorm Tabard": "perfect", "Sunfire Robe": "rare", "silk hat": "legendary"}
    assert gearthrow.rarity_of({"o": 9}) is None and gearthrow.rarity_of({}) is None


def test_no_start_noted_throws_nothing() -> None:
    bot = Bot([piece(2, "Iron Sword", "common")], ["common"])
    assert gearthrow.sweep(as_bot(bot)) == 0 and bot.a.sent == []   # farming never began: nothing is the run's


def test_a_refused_discard_says_so_and_counts_nothing() -> None:
    bot = Bot([], ["rare"], answer="refused: busy")
    gearthrow.note_start(as_bot(bot))
    bot.a.bag.append(piece(7, "Moon Ring", "rare"))
    assert gearthrow.sweep(as_bot(bot)) == 0
    assert bot.lines == ["the game didn't take the equipment to throw away: refused: busy"]


def test_a_sweep_is_due_every_so_many_minutes_of_farming() -> None:
    bot = Bot([], ["common"])
    gearthrow.note_start(as_bot(bot))
    assert not gearthrow.due(as_bot(bot))
    bot.clock.advance(30 * 60)
    assert gearthrow.due(as_bot(bot))
    bot.a.bag.append(piece(2, "Iron Sword", "common"))
    assert gearthrow.between_fights(as_bot(bot))
    assert not gearthrow.due(as_bot(bot))                       # the sweep starts the minutes again


def test_one_sweep_takes_thirty_at_most() -> None:
    bot = Bot([], ["crude"])
    gearthrow.note_start(as_bot(bot))
    bot.a.bag += [piece(100 + i, "Rag", "crude") for i in range(40)]
    assert gearthrow.sweep(as_bot(bot)) == gearthrow.MOST


def test_the_rarities_are_the_game_s_own() -> None:
    assert gearthrow.chosen({"gear_throw": ["Common", "superior", "LEGENDARY"]}) == {"common", "legendary"}
    assert gearthrow.RARITIES == ("crude", "common", "sophisticated", "perfect", "rare", "legendary")
