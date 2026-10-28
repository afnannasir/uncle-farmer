"""Time as a dependency: one clock object for the bot, so tests can move it by hand."""

from __future__ import annotations

import time
from typing import Protocol


class Waitable(Protocol):
    """What a clock waits on: a threading.Event."""

    def is_set(self) -> bool: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class StopFlag(Waitable, Protocol):
    """A run's Stop (threading.Event): the bot reads it and waits on it, the runner sets it."""

    def set(self) -> None: ...


class Flag(Protocol):
    """An on/off flag shared between threads (threading.Event): a run's pause."""

    def is_set(self) -> bool: ...

    def set(self) -> None: ...

    def clear(self) -> None: ...


class Clock(Protocol):
    def time(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...

    def wait(self, event: Waitable, timeout: float | None = None) -> bool: ...


class SystemClock:
    """The real clock: time.time(), time.sleep() and Event.wait()."""

    def time(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def wait(self, event: Waitable, timeout: float | None = None) -> bool:
        return event.wait(timeout)


CLOCK: Clock = SystemClock()


def clock_for(bot: object) -> Clock:
    """The bot's clock, or the system clock for a stand-in that has none."""
    return getattr(bot, "clock", None) or CLOCK
