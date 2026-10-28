# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The GUI's small pure pieces: choice lookup, save reporting, key capture and status text.

The windows themselves need a display and are not tested; these are the parts a wrong value from a
hand-edited profile, a locked file or a Ctrl+letter press goes through."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from wx import KeyEvent as WxKeyEvent

wx = pytest.importorskip("wx")

from unclefarmer.gui import common, walk  # noqa: E402
from unclefarmer.gui.mainparts import base as main  # noqa: E402  (the helpers moved here from gui/main.py)
from conftest import as_kind, stand_in


def test_pick_uses_the_index_of_the_value() -> None:
    assert common.pick(["a", "b", "c"], "b") == 1
    assert common.pick(["auto"], "auto") == 0


def test_pick_falls_back_for_a_value_this_version_does_not_know() -> None:
    assert common.pick(["round", "once"], "spiral") == 0
    assert common.pick(["round", "once"], None, default=1) == 1


def test_save_file_reports_a_failed_write_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    said: list[str] = []
    monkeypatch.setattr(common, "error", stand_in(common.error, lambda parent, message, title=None: said.append(message)))

    def locked():
        raise OSError("the file is in use")

    assert common.save_file(None, locked) is False
    assert len(said) == 1 and "the file is in use" in said[0]


def test_save_file_returns_true_and_passes_arguments() -> None:
    written: list[str] = []
    assert common.save_file(None, written.append, "plan") is True
    assert written == ["plan"]


def test_save_file_reports_a_value_error_from_the_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    said: list[str] = []
    monkeypatch.setattr(common, "error", stand_in(common.error, lambda parent, message, title=None: said.append(message)))
    assert common.save_file(None, lambda: (_ for _ in ()).throw(ValueError("bad plan"))) is False
    assert said and "bad plan" in said[0]


class Key:
    """The part of a wx.KeyEvent key_from_event reads."""

    def __init__(self, code: int, uni: int=0, ctrl: bool=False, alt: bool=False, shift: bool=False) -> None:
        self.code, self.uni = code, uni
        self.ctrl, self.alt, self.shift = ctrl, alt, shift

    def GetKeyCode(self) -> int:
        return self.code

    def GetUnicodeKey(self) -> int:
        return self.uni

    def ControlDown(self) -> bool:
        return self.ctrl

    def AltDown(self) -> bool:
        return self.alt

    def ShiftDown(self) -> bool:
        return self.shift

    def HasAnyModifiers(self) -> bool:
        return self.ctrl or self.alt or self.shift


def key_event(code: int, uni: int = 0, ctrl: bool = False, alt: bool = False,
              shift: bool = False) -> WxKeyEvent:
    return as_kind(wx.KeyEvent, Key(code, uni, ctrl, alt, shift))


@pytest.fixture
def no_held(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "_held", stand_in(common._held, lambda vk: False))


def test_a_plain_letter_is_its_own_name(no_held: None) -> None:
    assert common.key_from_event(key_event(ord("J"), ord("j"))) == "j"


def test_ctrl_letter_works_even_when_wx_reports_a_control_character(no_held: None) -> None:
    assert common.key_from_event(key_event(ord("J"), 10, ctrl=True)) == "lctrl+j"


def test_function_and_numpad_keys(no_held: None) -> None:
    assert common.key_from_event(key_event(wx.WXK_F5, 0)) == "f5"
    assert common.key_from_event(key_event(wx.WXK_NUMPAD3, 0)) == "num3"


def test_an_unknown_key_is_not_captured(no_held: None) -> None:
    assert common.key_from_event(key_event(wx.WXK_PAGEUP, 0)) is None


def test_key_ok_accepts_names_and_combinations() -> None:
    assert common.key_ok("j")
    assert common.key_ok("lctrl+alt+up")
    assert not common.key_ok("")
    assert not common.key_ok("not a key")


def test_status_text_for_hp_and_mp() -> None:
    assert main.out_of(None, 10) == "Not read yet"
    assert main.out_of(-1, 10) == "Not read yet"
    assert main.out_of(60, 80) == "60 of 80 (75%)"
    assert main.out_of(7, 0) == "7"


def test_durations() -> None:
    assert main.duration_text(90) == "1 min"
    assert main.duration_text(2 * 3600) == "2 h 0 min"


def test_a_quoted_script_word_cannot_break_the_line() -> None:
    assert walk._quote('Wolf "Cave"\nnext') == '"Wolf Cave next"'


def test_the_early_fire_setting_says_what_range_it_adds_to() -> None:
    from unclefarmer.gui import profile
    hunter = profile.pull_label({"attack_range": "auto", "scan": {"class": "Hunter"}})
    assert "Arcane Shot reaches 20" in hunter and "beyond the 19.5 tiles" in hunter
    warrior = profile.pull_label({"attack_range": "auto", "scan": {"class": "Warrior"}})
    assert "beyond the 4.5 tiles" in warrior        # melee: just inside its 5
    by_hand = profile.pull_label({"attack_range": 12.5})
    assert "beyond the 12.5 tiles set above" in by_hand
    unknown = profile.pull_label({"attack_range": "auto"})
    assert "beyond your class's attack range" in unknown
    assert all("walk all the way" not in t for t in (hunter, warrior, by_hand, unknown))
