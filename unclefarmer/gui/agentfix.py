"""Closing the open games for an agent reinstall or an update: a game loads the agent only as it
starts, and holds the agent file open while it runs."""

from __future__ import annotations

import threading
from collections.abc import Callable

import wx

from .. import gameinstall, updater
from ..errors import carry_on


def running_pids() -> list[int]:
    """Every running mw.exe, and every other process that has this copy's agent loaded (a game
    started from a renamed exe still writes its agent.<pid>.port, holds the agent jar open and
    logs into agent/run all the time: neither an update nor an agent reinstall may run beside it)."""
    pids = [pid for pid, _folder in gameinstall.running_games()]
    return pids + [pid for pid in updater.agent_games() if pid not in pids]


def close_then(pids: list[int], then: Callable[[list[int]], None], *, force: bool = False) -> None:
    """Close the games in pids on a worker (gameinstall.close_games: up to half a minute, and the
    window must not freeze meanwhile), then then(left) on the window's thread, left being the
    games that wouldn't close. Force only ends requested PIDs still identified as games."""
    def work() -> None:
        if force:
            try:
                live_games = set(running_pids())
                targets = [pid for pid in pids if pid in live_games]
                left = [pid for pid in pids if pid not in live_games] + gameinstall.close_games(targets, force=True)
            except OSError as e:
                carry_on(e, "ending a game process")
                left = pids
        else:
            left = gameinstall.close_games(pids)
        try:
            wx.CallAfter(then, left)
        except RuntimeError:            # the app is closing
            pass
    threading.Thread(target=work, daemon=True, name="close games").start()
