"""Sound Silencer edits a draft, saving only on the explicit Save action."""
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("wx")
import wx

from unclefarmer.gui import silencer as gui
from unclefarmer.silencer import save as actual_save, load as actual_load
from unclefarmer.link import AgentError


@pytest.fixture
def editor(monkeypatch: pytest.MonkeyPatch):
    app = wx.GetApp() or wx.App(False)
    initial = [dict(id=5, name="Bell", muted=False), dict(id=7, name="Rain", muted=True)]
    monkeypatch.setattr(gui.silencer, "load", lambda: [dict(row) for row in initial])
    def forbid_save(*args: Any) -> None:
        pytest.fail("Draft editing must not save")
    monkeypatch.setattr(gui.silencer, "save", forbid_save)
    parent = wx.Frame(None)
    parent.s = {"sound_silencer": []}  # type: ignore[attr-defined]
    closed: list[bool] = []
    frame = gui.SilencerDialog(parent, 42, "Hero", lambda: closed.append(True), start_discovery=False)
    frame.discovered({5, 7, 10})
    yield frame, parent, closed
    if not frame.closed:
        frame.Destroy()
    parent.Destroy()
    app.ProcessPendingEvents()


def test_add_move_rename_remove_update_draft_only(editor: Any) -> None:
    frame, _parent, _closed = editor
    frame.entry.SetValue("10")
    frame.add()
    assert frame.ids[False] == [5, 10]
    frame.move(10)
    assert frame.ids[True] == [7, 10]
    assert frame.rename(10, "Monster warning")
    assert "Monster warning" in frame.lists[True].GetString(1)
    frame.move(7)
    assert frame.ids[False] == [5, 7]
    frame.remove(5)
    assert frame.ids[False] == [7]
    assert frame.ids[True] == [10]


@pytest.mark.parametrize("text", ["", " ", "0", "100000", "-1", "unknown", "1.5", "9", "5", "7", "١٠", "9" * 5000])
def test_add_rejects_invalid_unknown_and_duplicate_ids(editor: Any, text: str) -> None:
    frame, _parent, _closed = editor
    before = [dict(row) for row in frame.rows]
    frame.entry.SetValue(text)
    frame.add()
    assert frame.rows == before


def test_invalid_rename_preserves_name(editor: Any) -> None:
    frame, _parent, _closed = editor
    assert not frame.rename(5, "line\nbreak")
    assert not frame.rename(5, "x" * 101)
    assert frame.rows[0]["name"] == "Bell"


def test_context_menu_offers_correct_opposite_list(editor: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    frame, _parent, _closed = editor
    for muted, destination in ((False, "Move to Mute List"), (True, "Move to Sounds List")):
        control = frame.lists[muted]
        control.SetSelection(0)
        labels: list[str] = []
        def popup(menu: wx.Menu) -> bool:
            labels.extend(item.GetItemLabelText() for item in menu.GetMenuItems())
            return True
        monkeypatch.setattr(control, "PopupMenu", popup)
        event = wx.ContextMenuEvent(wx.EVT_CONTEXT_MENU.typeId)
        event.SetPosition(wx.DefaultPosition)
        frame.context_menu(event, muted)
        assert labels == [destination, "Rename", "Remove"]


def test_close_discards_draft_without_applying(editor: Any) -> None:
    frame, parent, closed = editor
    frame.move(5)
    frame.Close()
    assert closed == [True]
    assert parent.s["sound_silencer"] == []


def test_save_callback_failure_keeps_editable_draft_and_syncs_persisted_settings(editor: Any) -> None:
    frame, parent, closed = editor
    frame.move(5)
    frame.saving = True
    persisted = [dict(row) for row in frame.rows]
    frame.saved("Saved, but the game disconnected", persisted)
    assert not frame.saving and not frame.closed and closed == []
    assert frame.save_button.IsEnabled()
    assert frame.rows == persisted
    assert parent.s["sound_silencer"] == persisted
    frame.rows[0]["name"] = "Draft again"
    assert parent.s["sound_silencer"][0]["name"] == "Bell"


def test_save_uses_explicit_draft_and_background_worker(editor: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    frame, _parent, closed = editor
    captured: list[Any] = []
    class Thread:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)
        def start(self) -> None:
            pass
    monkeypatch.setattr(gui.threading, "Thread", Thread)
    frame.move(5)
    frame.save()
    assert frame.saving
    assert not frame.entry.IsEnabled() and not frame.save_button.IsEnabled()
    assert captured[0]["target"] == frame.save_worker
    assert all(row["muted"] for row in captured[0]["args"][0])
    frame.saved("", captured[0]["args"][0])
    assert frame.closed and closed == [True]


def test_save_rejects_unknown_id_already_in_saved_rows(editor: Any) -> None:
    frame, _parent, closed = editor
    frame.rows.append(dict(id=999, name="Old sound", muted=True))
    frame.save()
    assert not frame.saving
    assert "999" in frame.status.GetValue()
    assert closed == []


def test_save_persists_even_when_connection_cannot_open(editor: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    frame, parent, _closed = editor
    monkeypatch.setattr(gui.silencer, "save", actual_save)
    monkeypatch.setattr(gui.silencer, "load", actual_load)
    def disconnected(**kwargs: Any) -> Any:
        assert actual_load() == frame.rows  # disk commit precedes connection attempt
        raise AgentError("no_game")
    monkeypatch.setattr(gui, "AgentLink", disconnected)
    def immediate(callback: Any, *args: Any) -> None:
        callback(*args)
    monkeypatch.setattr(gui.wx, "CallAfter", immediate)
    frame.move(5)
    frame.save_worker([dict(row) for row in frame.rows])
    assert actual_load() == frame.rows
    assert parent.s["sound_silencer"] == frame.rows
    assert "saved" in frame.status.GetValue()
    assert "could not be confirmed" in frame.status.GetValue()


def test_edit_and_clear_names_in_both_lists_without_changing_ids_or_mutes(editor: Any) -> None:
    frame, _parent, _closed = editor
    for sound_id, muted in ((5, False), (7, True)):
        assert frame.rename(sound_id, "Custom name")
        assert frame.rename(sound_id, "")
        row = next(row for row in frame.rows if row["id"] == sound_id)
        assert row == {"id": sound_id, "name": "", "muted": muted}
        assert str(sound_id) in frame.lists[muted].GetString(0)


def test_editor_directly_contains_required_controls_without_tabs(editor: Any) -> None:
    frame, _parent, _closed = editor
    assert isinstance(frame, gui.SilencerDialog)
    assert issubclass(gui.SilencerDialog, wx.Dialog)
    assert not hasattr(frame, "tabs") and not hasattr(frame, "live")
    assert frame.entry.GetParent() is frame.add_button.GetParent()
    assert all(control.GetParent() is frame.entry.GetParent() for control in frame.lists.values())
    assert frame.save_button.GetParent() is frame.entry.GetParent()


def test_add_clears_input_and_displays_id_without_an_invented_name(editor: Any) -> None:
    frame, _parent, _closed = editor
    frame.entry.SetValue("10")
    frame.add()
    assert frame.entry.GetValue() == ""
    assert frame.lists[False].GetString(1) == "10"
    assert frame.rows[-1] == dict(id=10, name="", muted=False)
    assert frame.rename(10, "Background Music")
    assert frame.lists[False].GetString(1) == "Background Music (10)"
    frame.move(10)
    assert frame.lists[True].GetString(1) == "Background Music (10)"
    assert frame.rename(10, "")
    assert frame.lists[True].GetString(1) == "10"


def test_successful_save_ends_modal_dialog_with_ok(editor: Any) -> None:
    frame, parent, closed = editor
    rows = [dict(row) for row in frame.rows]
    wx.CallAfter(frame.saved, "", rows)
    result = frame.ShowModal()
    assert result == wx.ID_OK
    assert frame.closed and closed == [True]
    assert parent.s["sound_silencer"] == rows
    frame.Destroy()


def test_draft_edits_discard_on_reopen_then_save_restores_lists_and_names(editor: Any,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    frame, parent, _closed = editor
    saved = [dict(row) for row in frame.rows]
    monkeypatch.setattr(gui.silencer, "load", lambda: [dict(row) for row in saved])
    frame.rename(5, "Unsaved name")
    frame.remove(7)
    frame.move(5)
    frame.entry.SetValue("10")
    frame.add()
    frame.Close()

    reopened = gui.SilencerDialog(parent, 42, "Hero", lambda: None, start_discovery=False)
    reopened.discovered({5, 7, 10})
    assert reopened.rows == saved
    commands: list[str] = []

    class Link:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> Link:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def json(self, command: str) -> Any:
            commands.append(command)
            return dict(hooked=True, reason="test", watch="off", mute="5", observed=0,
                        dropped=0, live_muted=0, unsupported=0)

    monkeypatch.setattr(gui, "AgentLink", Link)
    monkeypatch.setattr(gui.silencer, "save", actual_save)
    monkeypatch.setattr(gui.silencer, "load", actual_load)
    def immediate(callback: Any, *args: Any) -> None:
        callback(*args)
    monkeypatch.setattr(gui.wx, "CallAfter", immediate)
    reopened.rename(5, "Background Music")
    reopened.move(5)
    reopened.remove(7)
    reopened.entry.SetValue("10")
    reopened.add()
    assert commands == []
    assert actual_load() == []
    reopened.save_worker([dict(row) for row in reopened.rows])
    assert commands == ["soundmute 5"]
    assert reopened.closed
    restored = gui.SilencerDialog(parent, 42, "Hero", lambda: None, start_discovery=False)
    try:
        assert restored.ids == {False: [10], True: [5]}
        assert restored.lists[True].GetString(0) == "Background Music (5)"
        assert restored.lists[False].GetString(0) == "10"
    finally:
        restored.Destroy()
