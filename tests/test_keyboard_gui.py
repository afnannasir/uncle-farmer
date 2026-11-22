# pyright: reportPrivateUsage=false
"""Exercise the real Keyboard Manager dialog with the game capture stood in."""

from __future__ import annotations

import pytest
import wx
from conftest import returning

from unclefarmer import settings
from unclefarmer.engine import keyboard, triggers
from unclefarmer.gui.keyboard import KeyboardDialog
from unclefarmer.gui.triggers import StepEditor


def test_keyboard_manager_has_tabs_above_profile_controls_and_one_footer() -> None:
    app = wx.GetApp() or wx.App(False)
    with KeyboardDialog(None, "Test character", 42) as dialog:
        notebook = next(child for child in dialog.GetChildren() if isinstance(child, wx.Notebook))
        assert [notebook.GetPageText(index) for index in range(notebook.GetPageCount())] == [
            "Skills", "Profession skills", "Items", "Quick phrases",
        ]
        note = dialog.FindWindowByName("Keyboard profile information", dialog)
        assert isinstance(note, wx.TextCtrl) and note.IsMultiLine() and not note.IsEditable()
        assert "Slots marked Leave unchanged" in note.GetValue()
        profile = dialog.profiles.GetParent()
        assert profile == note.GetParent() == dialog.profile_name.GetParent()
        assert notebook.GetRect().GetBottom() < profile.GetPosition().y
        footer = [child for child in dialog.GetChildren() if isinstance(child, wx.Button)]
        assert [button.GetLabelText() for button in footer] == ["Save profile", "Apply profile", "Close"]
        assert len({button.GetPosition().y for button in footer}) == 1
        assert profile.GetRect().GetBottom() < footer[0].GetPosition().y
    app.ProcessPendingEvents()


def test_capture_save_edit_and_reload_from_keyboard_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    app = wx.GetApp() or wx.App(False)
    buff = keyboard.Binding(id=12, name="Bull's Eye")
    saved = keyboard.Profile(skills={11: buff}, phrases={5: "Opening line"})
    monkeypatch.setattr(KeyboardDialog, "_game", returning(keyboard.Capture(saved, [buff], [], [])))
    with KeyboardDialog(None, "Test character", 42) as dialog:
        assert set(dialog.controls) == {"skills", "profession", "items", "phrases"}
        dialog._capture(wx.CommandEvent())
        dialog.controls["skills"].SetSelection(10)
        dialog._toggle_prepare(wx.CommandEvent())
        assert "prepare when applied" in dialog.controls["skills"].GetString(10)
        dialog.profile_name.SetValue("Late session")
        dialog._save(wx.CommandEvent())
        dialog._new(wx.CommandEvent())
        assert not dialog.draft.skills
        dialog.profiles.SetSelection(1)
        dialog._choose(wx.CommandEvent())
        assert dialog.draft.prepare == [11]
        assert dialog.draft.phrases == {5: "Opening line"}
    cfg = settings.load_profile("Test character")
    assert keyboard.names(cfg) == ["Late session"]
    with StepEditor(None, keyboard_names=tuple(keyboard.names(cfg))) as editor:
        job = keyboard.ACTION_PREFIX + "Late session"
        editor.action.SetSelection(editor.actions.index(job))
        assert editor.read() == triggers.step(job)
    app.ProcessPendingEvents()
