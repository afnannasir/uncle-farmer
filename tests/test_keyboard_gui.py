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
            "Skills", "Profession skills", "Number keys", "Quick phrases",
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


def test_a_profile_shared_with_the_class_moves_out_of_the_character_and_back(monkeypatch: pytest.MonkeyPatch) -> None:
    app = wx.GetApp() or wx.App(False)
    beetle = keyboard.Binding(id=502, name="Iron Beetle", kind="beast")
    saved = keyboard.Profile(items={2: beetle})
    monkeypatch.setattr(KeyboardDialog, "_game", returning(keyboard.Capture(saved, [], [beetle], [], "Hunter")))
    with KeyboardDialog(None, "Test character", 42) as dialog:
        assert dialog.scope.GetString(1) == "Every character of its class"       # class not known yet
        dialog._capture(wx.CommandEvent())
        assert dialog.scope.GetString(1) == "Every Hunter character"
        assert "Iron Beetle (fusion beast)" in dialog.controls["items"].GetString(1)
        dialog.profile_name.SetValue("Mounts")
        dialog.scope.SetSelection(1)
        dialog._save(wx.CommandEvent())
        assert dialog.profiles.GetString(1) == "Mounts (shared with every Hunter)"
        assert not settings.load_profile("Test character")["keyboard_profiles"]
        assert keyboard.find({"scan": {"class": "Hunter"}}, "Mounts") == ("class", saved.model_dump(mode="json"))
        dialog.scope.SetSelection(0)
        dialog._save(wx.CommandEvent())                  # the same name back to this character: moved
        assert keyboard.find({"scan": {"class": "Hunter"}}, "Mounts") is None
        assert keyboard.names(settings.load_profile("Test character")) == ["Mounts"]
        dialog._apply(wx.CommandEvent())
        assert dialog.apply_name == "Mounts"
    app.ProcessPendingEvents()


def test_saving_over_a_hidden_shared_profile_asks_and_names_who_has_it(monkeypatch: pytest.MonkeyPatch) -> None:
    app = wx.GetApp() or wx.App(False)
    mine = keyboard.Profile(phrases={1: "Mine"})
    theirs = keyboard.Profile(phrases={1: "Theirs"}).model_dump(mode="json")
    keyboard.store("Test character", "class", "Farm", theirs, "Hunter")
    keyboard.store("Test character", "character", "Farm", mine.model_dump(mode="json"))
    questions: list[str] = []
    answer = [False]

    def asked(_parent: object, text: str, *_a: object) -> bool:
        questions.append(text)
        return answer[0]
    monkeypatch.setattr("unclefarmer.gui.keyboard.ask", asked)
    monkeypatch.setattr(KeyboardDialog, "_game", returning(keyboard.Capture(mine, [], [], [], "Hunter")))
    with KeyboardDialog(None, "Test character", 42) as dialog:
        dialog._capture(wx.CommandEvent())
        assert dialog.entries == [("character", "Farm")]       # the class's Farm is hidden by its own
        dialog.scope.SetSelection(1)
        dialog._save(wx.CommandEvent())                       # moving it onto the class's own Farm
        assert questions == ["Replace keyboard profile Farm (shared with every Hunter)?"]
        assert keyboard.find({"scan": {"class": "Hunter"}}, "Farm") == ("class", theirs)    # declined: kept
        answer[0] = True
        dialog._save(wx.CommandEvent())
        assert keyboard.find({"scan": {"class": "Hunter"}}, "Farm") == ("class", mine.model_dump(mode="json"))
        assert not settings.load_profile("Test character")["keyboard_profiles"]            # moved, not copied
        dialog.scope.SetSelection(0)
        dialog.profile_name.SetValue("Farm")
        questions.clear()
        dialog._delete(wx.CommandEvent())
        assert questions == ["Delete keyboard profile Farm (shared with every Hunter)?"]
        assert keyboard.find({"scan": {"class": "Hunter"}}, "Farm") is None
    app.ProcessPendingEvents()


def test_the_class_scope_needs_the_class(monkeypatch: pytest.MonkeyPatch) -> None:
    app = wx.GetApp() or wx.App(False)
    errors: list[str] = []
    def shown(_parent: object, text: str) -> None:
        errors.append(text)
    monkeypatch.setattr("unclefarmer.gui.keyboard.error", shown)
    with KeyboardDialog(None, "Test character", 42) as dialog:
        dialog.profile_name.SetValue("Farm")
        dialog.scope.SetSelection(1)
        dialog._save(wx.CommandEvent())
        assert errors and "class" in errors[0]
        assert keyboard.names(settings.load_profile("Test character")) == []
    app.ProcessPendingEvents()
