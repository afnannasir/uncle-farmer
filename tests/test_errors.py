"""carry_on() is the one place that decides whether an ignored failure is fatal."""

import pytest

from unclefarmer.errors import carry_on


def test_carry_on_raises_the_original_exception_in_tests():
    with pytest.raises(ValueError, match="hidden bug"):
        carry_on(ValueError("hidden bug"), "refreshing the status")


def test_carry_on_stays_quiet_outside_tests(monkeypatch):
    monkeypatch.delenv("UNCLEFARMER_TESTING", raising=False)
    assert carry_on(ValueError("expected"), "reading a missing optional file") is None
