# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""NVDA speech through ctypes: loading the controller DLL, speaking, interrupting, and the
'NVDA is not running' fallback. The DLL layer is faked; nothing here needs NVDA."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from unclefarmer import paths, speech
from pathlib import Path
from typing import NoReturn, Protocol, TypeVar

from conftest import returning, stand_in

D = TypeVar("D")


class FakeDll:
    """The few nvdaControllerClient64.dll entry points speech.py calls."""

    def __init__(self, running: int=0, speak_rc: int=0, raise_on: str | None=None) -> None:
        self.running, self.speak_rc, self.raise_on = running, speak_rc, raise_on
        self.cancels = 0
        self.spoken: list[str] = []

    def nvdaController_testIfRunning(self) -> int:
        if self.raise_on == "test":
            raise OSError("gone")
        return self.running

    def nvdaController_cancelSpeech(self) -> None:
        self.cancels += 1

    def nvdaController_speakText(self, text: str) -> int:
        if self.raise_on == "speak":
            raise OSError("gone")
        self.spoken.append(text)
        return self.speak_rc


class Install(Protocol):
    """use_dll's answer: installs a fake (or None: no DLL) and hands it back."""

    def __call__(self, fake: D) -> D: ...


@pytest.fixture
def use_dll(monkeypatch: pytest.MonkeyPatch) -> Install:
    """Put a chosen fake DLL behind speech._load and reset the module's cached globals."""
    def install(fake: D) -> D:
        monkeypatch.setattr(speech, "_tried", True)
        monkeypatch.setattr(speech, "_dll", fake)
        return fake
    return install


# ---------------------------------------------------------------- _load
def test_load_without_the_dll_file_returns_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(speech, "_tried", False)
    monkeypatch.setattr(speech, "_dll", None)
    monkeypatch.setattr(paths, "APP", str(tmp_path))
    assert speech._load() is None


def test_load_with_the_dll_file_loads_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeDll()
    monkeypatch.setattr(speech, "_tried", False)
    monkeypatch.setattr(speech, "_dll", None)
    monkeypatch.setattr(paths, "APP", str(tmp_path))
    monkeypatch.setattr(speech.os.path, "exists", stand_in(speech.os.path.exists, returning(True)))
    monkeypatch.setattr(speech.ctypes, "windll", SimpleNamespace(LoadLibrary=returning(fake)))
    assert speech._load() is fake


def test_load_swallows_a_broken_dll(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(speech, "_tried", False)
    monkeypatch.setattr(speech, "_dll", None)
    monkeypatch.setattr(paths, "APP", str(tmp_path))
    monkeypatch.setattr(speech.os.path, "exists", stand_in(speech.os.path.exists, returning(True)))

    def boom(path: str) -> NoReturn:
        raise OSError("not a dll")

    monkeypatch.setattr(speech.ctypes, "windll", SimpleNamespace(LoadLibrary=boom))
    assert speech._load() is None


def test_load_is_only_tried_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(speech, "_tried", False)
    monkeypatch.setattr(speech, "_dll", None)
    monkeypatch.setattr(paths, "APP", str(tmp_path))
    assert speech._load() is None
    assert speech._tried is True
    # A second call does not look at the filesystem again; it just reuses the (None) cache.
    monkeypatch.setattr(speech.os.path, "exists", stand_in(speech.os.path.exists, returning(True)))
    assert speech._load() is None


# ---------------------------------------------------------------- speak
def test_speak_without_a_dll_is_false(use_dll: Install) -> None:
    use_dll(None)
    assert speech.speak("hello") is False


def test_speak_when_nvda_is_not_running_is_false(use_dll: Install) -> None:
    fake = use_dll(FakeDll(running=1))
    assert speech.speak("hello") is False
    assert fake.spoken == []


def test_speak_returns_true_on_success(use_dll: Install) -> None:
    fake = use_dll(FakeDll(running=0, speak_rc=0))
    assert speech.speak("hello") is True
    assert len(fake.spoken) == 1
    assert fake.cancels == 0


def test_speak_interrupt_cancels_first(use_dll: Install) -> None:
    fake = use_dll(FakeDll(running=0, speak_rc=0))
    assert speech.speak("now", interrupt=True) is True
    assert fake.cancels == 1
    assert len(fake.spoken) == 1


def test_speak_that_the_dll_refuses_is_false(use_dll: Install) -> None:
    fake = use_dll(FakeDll(running=0, speak_rc=1))
    assert speech.speak("hello") is False
    assert len(fake.spoken) == 1


def test_speak_swallows_an_oserror(use_dll: Install) -> None:
    use_dll(FakeDll(running=0, raise_on="speak"))
    assert speech.speak("hello") is False


def test_speak_swallows_an_oserror_from_testifrunning(use_dll: Install) -> None:
    use_dll(FakeDll(raise_on="test"))
    assert speech.speak("hello") is False
