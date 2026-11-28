"""The wait away from a boss ends when the game tells the server the boss died: its last words on the
news channel (seen in game: "Whale Monster Bigger: Damn <player> doesn't even let go of rare and
protected animals!", "Fire Lord: Damn... I want to vaporize the person named <player>!"). Anything
else - another boss's words, a player saying the name, a line from before the wait, an agent with no
chat - leaves the wait as long as it was."""

from __future__ import annotations

from typing import Any

from conftest import as_bot
from test_bosses import Walker, mob

from unclefarmer.engine import bosses
from unclefarmer.link import AgentError


class News:
    """The agent's chat: lines given out at the clock time they are said, numbered as the game's."""

    def __init__(self, walker: Walker, lines: list[tuple[float, int, str, str]], fails: bool = False) -> None:
        self.w, self.lines, self.fails = walker, lines, fails     # (when, channel, name, text)
        self.asked: list[str] = []

    def said_by_now(self) -> list[tuple[float, int, str, str]]:
        return [x for x in self.lines if x[0] <= self.w.clock.time()]

    def json(self, line: str) -> dict[str, Any]:
        self.asked.append(line)
        if self.fails:
            raise AgentError("unknown command: chat")
        since = int(line.split()[1])
        now = self.said_by_now()
        out = [{"channel": ch, "name": name, "text": text, "age_ms": 0}
               for i, (_t, ch, name, text) in enumerate(now, 1) if i > since]
        return {"last": len(now), "chat": out}


WHALE = "Whale Monster Bigger"
DEATH = "Whale Monster Bigger: Damn Hero doesn't even let go of rare and protected animals!"


def whale_snap() -> dict[str, Any]:
    return {"self": {"id": 1, "x": 100.0, "y": 100.0}, "entities": [mob(-1, WHALE, 105.0, hp=900000)]}


def leave(lines: list[tuple[float, int, str, str]], fails: bool = False,
          first: int = 300) -> tuple[Walker, News, float]:
    w = Walker(whale_snap(), [])
    w.p.update(boss_walk_offs=0, boss_away_first=first, boss_away_most=first)
    w.random_cell = lambda *a, **k: (40, 100)   # type: ignore[method-assign]
    news = News(w, lines, fails)
    w.a = news  # type: ignore[attr-defined]
    start = w.clock.time()
    assert bosses.keep_away(as_bot(w), whale_snap(), 5121, None)
    return w, news, w.clock.time() - start


def test_the_boss_s_last_words_end_the_wait_and_it_goes_back_at_once() -> None:
    w, _news, took = leave([(1030.0, 34, "", DEATH)])
    assert took < 40                               # not the 300 s
    assert "the game says Whale Monster Bigger is dead: back to the farm without waiting any longer" in w.lines
    assert w.trips == [1041, 5121] and w.lines[-1] == "back to Wolf Prairie (5121)"


def test_other_words_leave_the_wait_as_it_was() -> None:
    lines = [
        (1010.0, 34, "", "Fire Lord: Damn... I want to vaporize the person named Bob!"),      # another boss
        (1012.0, 0, "Bob", DEATH),                                         # a player pasting it in the chat
        (1014.0, 34, "", "Bob got 1 Whale Monster Bigger: Damn card"),     # the name, not at the start
        (1016.0, 33, "", DEATH),                                           # not the news channel
        (1018.0, 34, "", "Whale Monster Biggerish: Damn Bob"),             # another name that starts the same
    ]
    w, _news, took = leave(lines)
    assert took >= 300
    assert not any("is dead" in line for line in w.lines)


def test_a_death_said_before_the_wait_doesn_t_count() -> None:
    w, _news, took = leave([(0.0, 34, "", DEATH)])
    assert took >= 300 and not any("is dead" in line for line in w.lines)


def test_without_the_chat_the_wait_is_as_long_as_it_was() -> None:
    w, news, took = leave([(1030.0, 34, "", DEATH)], fails=True)
    assert took >= 300 and news.asked                # asked, refused, never a crash
    assert w.trips == [1041, 5121]


def test_the_news_is_read_every_few_seconds_not_at_every_step() -> None:
    _w, news, took = leave([], first=60)
    assert took >= 60
    assert len(news.asked) <= 60 / bosses.NEWS_EVERY + 2


def test_case_and_spaces_in_the_line_don_t_matter() -> None:
    w, _news, took = leave([(1020.0, 34, "", "  whale monster bigger :  DAMN Hero, again")])
    assert took < 40 and any("is dead" in line for line in w.lines)


def test_the_farm_ending_still_wins_over_the_news() -> None:
    """The farm over during the wait: its own end takes it from here, news or not."""
    w = Walker(whale_snap(), [])
    w.p.update(boss_walk_offs=0, boss_away_first=300, boss_away_most=300)
    w.random_cell = lambda *a, **k: (40, 100)   # type: ignore[method-assign]
    w.a = News(w, [(1030.0, 34, "", DEATH)])  # type: ignore[attr-defined]
    start = w.clock.time()
    assert bosses.keep_away(as_bot(w), whale_snap(), 5121, None, done=lambda: w.clock.time() - start >= 10)
    assert w.trips == [1041] and not any("is dead" in line for line in w.lines)
