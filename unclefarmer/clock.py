"""Time as a dependency: one clock object for the bot, so tests can move it by hand."""

import time
from typing import Any, Protocol


class Clock(Protocol):
    def time(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...

    def wait(self, event: Any, timeout: float | None = None) -> bool: ...


class SystemClock:
    """The real clock: time.time(), time.sleep() and Event.wait()."""

    def time(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def wait(self, event: Any, timeout: float | None = None) -> bool:
        return bool(event.wait(timeout))


CLOCK: Clock = SystemClock()


def clock_for(bot: Any) -> Clock:
    """The bot's clock, or the system clock for a stand-in that has none."""
    return getattr(bot, "clock", None) or CLOCK
