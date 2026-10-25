"""Failures the app deliberately carries on from, and a way for tests to make them fatal.

A best-effort action (a status refresh, a failed scan, an unreadable map) must not stop the app.
Those handlers call carry_on() so the decision is visible in one place. Tests set
UNCLEFARMER_TESTING and the same exception is re-raised instead of disappearing.
"""

import os

TESTING_ENV = "UNCLEFARMER_TESTING"


def carry_on(exc: BaseException, where: str = "") -> None:
    """Return to let a best-effort action continue; in tests, re-raise the failure."""
    if os.environ.get(TESTING_ENV):
        raise exc
