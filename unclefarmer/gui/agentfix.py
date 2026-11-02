"""Closing the open games for an agent reinstall or an update: a game loads the agent only as it
starts, and holds the agent file open while it runs."""

from __future__ import annotations

import threading
from collections.abc import Callable

import wx

from .. import gameinstall


def running_pids() -> list[int]:
    return [pid for pid, _folder in gameinstall.running_games()]


def close_then(pids: list[int], then: Callable[[list[int]], None]) -> None:
    """Close the games in pids on a worker (gameinstall.close_games: up to half a minute, and the
    window must not freeze meanwhile), then then(left) on the window's thread, left being the
    games that wouldn't close."""
    def work() -> None:
        left = gameinstall.close_games(pids)
        try:
            wx.CallAfter(then, left)
        except RuntimeError:            # the app is closing
            pass
    threading.Thread(target=work, daemon=True, name="close games").start()
