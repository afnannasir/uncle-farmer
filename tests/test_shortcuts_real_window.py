"""The real main window with the player's shortcuts: shortcuts.txt read at start (its bad lines in the
log), the menus' labels, the hotkeys held, and a change applied at once - the old hotkeys let go, the
new ones held. Windows' own hotkeys are stood in for (a test must not take keys from the desktop),
and no game runs."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import paths, schedule, shortcuts
from unclefarmer.shortcuts import EVERYWHERE, IN_APP, Shortcut

wx = pytest.importorskip("wx")


@pytest.fixture
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from unclefarmer import i18n
    from unclefarmer.gui import main
    monkeypatch.setattr(paths, "AGENT_RUN", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    monkeypatch.setattr(schedule, "SCHEDULES", str(tmp_path / "schedules.json"))
    held: dict[int, tuple[int, int]] = {}

    def register(self: Any, hid: int, mods: int, vk: int) -> bool:
        if (mods, vk) in held.values():
            return False
        held[hid] = (mods, vk)
        return True

    def unregister(self: Any, hid: int) -> bool:
        return held.pop(hid, None) is not None
    monkeypatch.setattr(main.MainFrame, "RegisterHotKey", register)
    monkeypatch.setattr(main.MainFrame, "UnregisterHotKey", unregister)
    app = wx.GetApp() or wx.App(False)
    i18n.load("en")
    with open(shortcuts.file_path(), "w", encoding="utf-8") as f:
        f.write("game_next = ctrl+shift+f11 = global\nrun_start = F7 = inapp\nnonsense\n")
    frame: Any = main.MainFrame()
    frame.held = held
    yield frame
    frame.destroyed = True
    frame._stop_timers()
    for h in list(frame.hotkeys):
        frame.UnregisterHotKey(h)
    frame.Destroy()
    app.ProcessPendingEvents()


def test_the_window_starts_with_the_player_s_shortcuts(window: Any) -> None:
    assert window.action_items["run_start"].GetItemLabel() == "&Start\tF7"
    assert window.action_items["game_next"].GetItemLabel().endswith("\tCtrl+Shift+F11")
    assert sorted(window.held.values()) == sorted([(0x4000, 0x13), (0x4004, 0x13), (0x4006, 0x7A)])
    log = [line for line, _important in window.log_lines]
    assert any("line 3" in line for line in log)


def test_a_change_is_applied_at_once(window: Any) -> None:
    table = shortcuts.defaults()
    table["run_toggle"] = Shortcut("Ctrl+F9", EVERYWHERE)
    table["run_start"] = Shortcut("F5", IN_APP)
    window.apply_shortcuts(table)
    assert window.action_items["run_start"].GetItemLabel() == "&Start\tF5"
    assert "\t" not in window.action_items["game_next"].GetItemLabel()
    assert sorted(window.held.values()) == sorted([(0x4002, 0x78), (0x4004, 0x13)])    # Pause let go
    assert len(window.hotkeys) == 2 and window.GetAcceleratorTable().IsOk()
