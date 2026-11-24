"""A run never goes on as another character: one account holds several characters, and the player
can switch to another in the same game (live 2026-11-24: a paused run went on, with its own
restock, on the character the player had switched to). And a paused run whose game goes is not
opened again nor carried on: the player had the game."""

from __future__ import annotations

import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest

import simgame
from unclefarmer.engine.botparts.base import WrongCharacter
from unclefarmer.engine.runner import Run


def switch_while_paused(game: simgame.SimGame, s: simgame.Soak, monkeypatch: pytest.MonkeyPatch) -> None:
    def pause() -> None:
        bot = s.run.bot
        assert bot is not None
        bot.paused.set()

    def other_character_then_go_on() -> None:
        bot = s.run.bot
        assert bot is not None
        monkeypatch.setattr(simgame, "ME", simgame.ME + 1)       # Switch account, another character
        monkeypatch.setattr(simgame, "NAME", "Othername")
        bot.paused.clear()
    s.mark(1.0, pause)
    s.mark(2.0, other_character_then_go_on)


def test_a_paused_run_never_goes_on_as_the_character_switched_to(
        sim_env: Callable[..., simgame.SimGame], monkeypatch: pytest.MonkeyPatch) -> None:
    game = sim_env(seed=7)
    s = simgame.Soak(game, "end_at here\nfarm minutes=30", watchdog=120)
    switch_while_paused(game, s, monkeypatch)
    s.go()
    assert "Othername is logged in now, not Simchar" in s.reason, list(s.lines)[-15:]
    assert "rather than farming as the wrong character" in s.reason
    assert not s.run.game_lost                   # not opened again, not carried on later
    assert s.said("going somewhere safe") == [] and not s.timed_out


def test_the_check_is_by_the_game_s_id() -> None:
    from unclefarmer.engine.botparts.reading import ReadingMixin
    bot: Any = SimpleNamespace(char_id=4242, character="Hero")
    check = cast(Any, ReadingMixin.same_character)
    check(bot, {"id": 4242, "name": "Hero"})
    check(bot, {"id": 0})                        # mid-login: nobody yet
    check(bot, {})                               # a reading without the character
    with pytest.raises(WrongCharacter, match="Bob is logged in now, not Hero"):
        check(bot, {"id": 5151, "name": "Bob"})
    check(SimpleNamespace(char_id=None, character="Hero"), {"id": 5151})   # a run that doesn't know its id


def test_a_paused_run_s_lost_game_is_not_opened_again() -> None:
    lines: list[str] = []
    run = Run("farm minutes=5", "test", 1, on_log=lambda line, speak, r: lines.append(line),
              on_done=lambda why, r: None)
    paused = threading.Event()
    paused.set()
    run.bot = cast(Any, SimpleNamespace(paused=paused))
    run.game_lost = True
    run._paused_game_lost()                     # pyright: ignore[reportPrivateUsage]
    assert not run.game_lost and run.failed
    assert any(line.endswith("the game went while the run was paused: it isn't opened again or carried on")
               for line in lines), lines
    run.game_lost, paused = True, threading.Event()
    run.bot = cast(Any, SimpleNamespace(paused=paused))
    run._paused_game_lost()                     # pyright: ignore[reportPrivateUsage]
    assert run.game_lost                         # running when the game went: opened again as before
