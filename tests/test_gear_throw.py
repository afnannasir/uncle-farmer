"""Equipment picked up while farming, thrown away by its rarity - never a piece the bag held when
the run began."""
from __future__ import annotations

from typing import Any

from conftest import Clock, as_bot

from unclefarmer.engine import gearthrow


def piece(uid: int, name: str, rarity: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {"uid": uid, "type": 30000 + uid, "name": name, "count": 1}
    if rarity:
        row["rarity"] = rarity
    return row


class Link:
    def __init__(self, bag: list[dict[str, Any]], answer: str = "sent all 1") -> None:
        self.bag = bag
        self.answer = answer
        self.sent: list[str] = []
        self.asked = 0

    def json(self, line: str) -> Any:
        assert line == "mem bagfull"
        self.asked += 1
        return list(self.bag)

    def send(self, line: str) -> str:
        self.sent.append(line)
        if line.startswith("discarddirectall "):
            gone = {int(u) for u in line.split()[1:]}
            self.bag = [r for r in self.bag if r["uid"] not in gone]
        return self.answer


class Bot:
    def __init__(self, bag: list[dict[str, Any]], throw: list[str], answer: str = "sent all 1") -> None:
        self.a = Link(bag, answer)
        self.p: dict[str, Any] = {"gear_throw": throw, "gear_throw_minutes": 30}
        self.clock = Clock()
        self.agent_version = 79
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
    assert bot.a.sent == ["discarddirectall 2 3"]
    assert [r["uid"] for r in bot.a.bag] == [1, 4, 5]          # the run's start piece and the rest stay
    assert bot.lines == ["threw away Iron Sword (Common), picked up while farming",
                         "threw away Cloth Hat (Crude), picked up while farming"]


def test_a_second_spot_keeps_the_first_start() -> None:
    bot = Bot([piece(1, "Old Sword", "common")], ["common"])
    gearthrow.note_start(as_bot(bot))
    bot.a.bag.append(piece(2, "Iron Sword", "common"))
    gearthrow.note_start(as_bot(bot))                           # the plan's next spot: the run's own start stands
    assert gearthrow.sweep(as_bot(bot)) == 1 and bot.a.sent == ["discarddirectall 2"]


def test_an_agent_that_doesn_t_say_the_rarity_throws_nothing() -> None:
    bot = Bot([], ["common"])
    gearthrow.note_start(as_bot(bot))
    bot.a.bag.append(piece(2, "Iron Sword", None))
    assert gearthrow.sweep(as_bot(bot)) == 0 and bot.a.sent == []


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
