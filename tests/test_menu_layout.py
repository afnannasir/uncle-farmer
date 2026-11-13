"""Native context menus use the original commands and keep their keyboard shortcuts."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any, cast

import pytest

pytest.importorskip("wx")
import wx

from unclefarmer import i18n
from unclefarmer.gui import main


class MenuFrame(wx.Frame):
    """Real menus with harmless command spies, without scanning games or registering hotkeys."""
    sync_action_menus = main.MainFrame.sync_action_menus
    context_handler = main.MainFrame.context_handler
    destroy_context_menu = main.MainFrame.destroy_context_menu
    game_context_menu = main.MainFrame.game_context_menu

    def __init__(self) -> None:
        super().__init__(None, title="Uncle Farmer menu preview", size=(860, 640))
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.runs: dict[int, object] = {}
        self.items = [("plan", "Diagnostic plan")]
        self.messengers: dict[int, object] = {}
        panel = wx.Panel(self)
        self.game_list = wx.ListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.game_list.SetName("Games")
        self.game_list.InsertColumn(0, "Character", width=240)
        self.game_list.InsertColumn(1, "Bot", width=240)
        self.game_list.InsertItem(0, "Preview Hero")
        self.game_list.SetItem(0, 1, "Stopped")
        self.game_list.InsertItem(1, "Preview Mage")
        self.game_list.SetItem(1, 1, "Stopped")
        self.game_list.SetItemState(0, wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED,
                                    wx.LIST_STATE_SELECTED | wx.LIST_STATE_FOCUSED)
        self.game_list.Bind(wx.EVT_CONTEXT_MENU, self.game_context_menu)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(panel, label="Menu preview: commands record their names without contacting a game."),
                  0, wx.ALL, 8)
        sizer.Add(self.game_list, 1, wx.EXPAND | wx.ALL, 8)
        panel.SetSizer(sizer)
        self.CreateStatusBar()
        self.SetStatusText("Ready for menu verification")
        main.MainFrame.build_menu(cast(Any, self))
        self.game_list.SetFocus()

    def selected_pid(self) -> int | None:
        index = self.game_list.GetNextItem(-1, state=wx.LIST_STATE_SELECTED)
        return index + 1 if index != wx.NOT_FOUND else None

    def active_item(self) -> tuple[str, str] | None:
        return self.items[0] if self.items else None


def command_spy(name: str) -> Callable[..., None]:
    def action(frame: MenuFrame, *args: Any, **kwargs: Any) -> None:
        frame.calls.append((name, args, kwargs))
        frame.SetStatusText(name + (" " + str(args) if args else ""))
    return action


for _name in ("open_tool", "sell_collection_now", "sell_shards_now", "clean_bag_now", "store_items_now",
              "restock_now", "update_shard_prices_now", "edit_profile", "edit_bag", "scan_now",
              "edit_other_profile", "export_character_settings", "import_character_settings", "start", "stop",
              "toggle_pause", "stop_all", "walk", "open_schedules", "show_results", "speak_results",
              "go_tab", "open_chat", "open_silencer"):
    setattr(MenuFrame, _name, command_spy(_name))


@pytest.fixture
def frame() -> Iterator[Any]:
    app = wx.GetApp() or wx.App(False)
    i18n.load("en")
    window = MenuFrame()
    yield window
    window.Destroy()
    app.ProcessPendingEvents()


def test_only_three_context_groups_and_no_standalone_duplicates(frame: Any) -> None:
    assert [item.GetItemLabelText() for item in frame.context_menu.GetMenuItems()] == ["Tools", "Character", "Run"]
    bar = frame.GetMenuBar()
    assert [bar.GetMenuLabelText(index) for index in range(bar.GetMenuCount())] == [
        "File", "View", "Game", "Messages", "Plans", "Help"]
    assert frame.action_menus["tools"].GetMenuItemCount() == 13
    tool_labels = [item.GetItemLabelText() for item in frame.action_menus["tools"].GetMenuItems()]
    assert tool_labels.count("Sound Silencer") == 1
    assert "Sound controls" not in tool_labels
    assert frame.action_menus["character"].GetMenuItemCount() == 6
    assert frame.action_menus["run"].GetMenuItemCount() == 8
    for group in ("tools", "character", "run"):
        labels = [item.GetItemLabelText() for item in frame.action_menus[group].GetMenuItems()]
        assert len(labels) == len(set(labels))
    ids = [item.GetId() for item in frame.action_items.values()]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("key,handler,args", [
    ("tools_gem", "open_tool", ("gem",)), ("tools_enhance", "open_tool", ("enhance",)),
    ("tools_chores", "open_tool", ("chores",)), ("tools_gather", "open_tool", ("gather",)),
    ("tools_dungeon", "open_tool", ("dungeon",)), ("tools_xploss", "open_tool", ("xploss",)),
    ("tools_collection", "sell_collection_now", ()),
    ("tools_shards", "sell_shards_now", ()), ("tools_clean", "clean_bag_now", ()),
    ("tools_store", "store_items_now", ()), ("tools_restock", "restock_now", ()),
    ("tools_prices", "update_shard_prices_now", ()), ("tools_silencer", "open_silencer", ()),
    ("character_settings", "edit_profile", ()),
    ("character_bag", "edit_bag", ()), ("character_scan", "scan_now", ()),
    ("character_other", "edit_other_profile", ()), ("character_export", "export_character_settings", ()),
    ("character_import", "import_character_settings", ()), ("run_start", "start", ()),
    ("run_stop", "stop", ()), ("run_pause", "toggle_pause", ()), ("run_stop_all", "stop_all", ()),
    ("run_walk", "walk", ()), ("run_schedules", "open_schedules", ()),
    ("run_results", "show_results", ()), ("run_speak_results", "speak_results", ()),
])
def test_each_moved_command_calls_its_original_handler(frame: Any, key: str, handler: str, args: tuple[str, ...]) -> None:
    if key in ("run_stop", "run_pause", "run_stop_all"):
        frame.runs[1] = object()
    item = frame.action_items[key]
    event = wx.CommandEvent(wx.EVT_MENU.typeId, item.GetId())
    frame.GetEventHandler().ProcessEvent(event)
    assert frame.calls == [(handler, args, {})]


@pytest.mark.parametrize("key", ["tools_restock", "tools_gem", "tools_clean", "character_scan", "run_start", "run_walk"])
def test_context_or_shortcut_command_cannot_bypass_running_guard(frame: Any, key: str) -> None:
    frame.runs[1] = object()
    frame.GetEventHandler().ProcessEvent(wx.CommandEvent(wx.EVT_MENU.typeId, frame.action_items[key].GetId()))
    assert frame.calls == []
    assert not frame.action_items[key].IsEnabled()
    frame.runs.clear()
    frame.GetEventHandler().ProcessEvent(wx.CommandEvent(wx.EVT_MENU.typeId, frame.action_items[key].GetId()))
    assert len(frame.calls) == 1


def test_missing_selection_and_empty_plan_list_keep_existing_availability(frame: Any) -> None:
    frame.game_list.SetItemState(0, 0, wx.LIST_STATE_SELECTED)
    frame.sync_action_menus()
    assert not frame.action_items["tools_restock"].IsEnabled()
    assert not frame.action_items["character_settings"].IsEnabled()
    assert frame.action_items["character_other"].IsEnabled()
    assert frame.action_items["run_schedules"].IsEnabled()
    frame.game_list.SetItemState(0, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
    frame.items.clear()
    frame.sync_action_menus()
    assert not frame.action_items["run_start"].IsEnabled()
    assert frame.action_items["tools_restock"].IsEnabled()


def test_shortcuts_remain_registered_without_opening_popup(frame: Any) -> None:
    assert frame.GetAcceleratorTable().IsOk()
    expected = {"run_start": "F5", "run_stop": "Shift+F5", "run_pause": "F6",
                "character_settings": "Ctrl+P", "character_bag": "Ctrl+B", "tools_gem": "Ctrl+G",
                "tools_enhance": "Ctrl+Shift+E", "tools_chores": "Ctrl+Shift+D",
                "tools_gather": "Ctrl+Shift+F", "tools_dungeon": "Ctrl+Shift+G"}
    for key, shortcut in expected.items():
        assert frame.action_items[key].GetItemLabel().endswith("\t" + shortcut)


@pytest.mark.parametrize("size", [(360, 240), (860, 640), (1280, 720)])
def test_popup_reopens_without_duplicating_or_destroying_its_submenus(
        frame: Any, size: tuple[int, int], monkeypatch: pytest.MonkeyPatch) -> None:
    frame.SetSize(size)
    shown: list[Any] = []
    def capture(menu: wx.Menu) -> bool:
        shown.append(menu)
        return True
    monkeypatch.setattr(frame.game_list, "PopupMenu", capture)
    for _ in range(3):
        event = wx.ContextMenuEvent(wx.EVT_CONTEXT_MENU.typeId)
        event.SetPosition(wx.DefaultPosition)
        frame.game_context_menu(event)
    assert shown == [frame.context_menu] * 3
    assert frame.context_menu.GetMenuItemCount() == 3
    assert frame.action_menus["tools"].GetMenuItemCount() == 13
    tool_labels = [item.GetItemLabelText() for item in frame.action_menus["tools"].GetMenuItems()]
    assert tool_labels.count("Sound Silencer") == 1
    assert "Sound controls" not in tool_labels


def test_sound_controls_available_during_farming_but_require_selected_game(frame: Any) -> None:
    frame.runs[1] = object()
    frame.sync_action_menus()
    assert frame.action_items["tools_silencer"].IsEnabled()
    frame.GetEventHandler().ProcessEvent(wx.CommandEvent(wx.EVT_MENU.typeId, frame.action_items["tools_silencer"].GetId()))
    assert frame.calls == [("open_silencer", (), {})]
    frame.game_list.SetItemState(0, 0, wx.LIST_STATE_SELECTED)
    frame.sync_action_menus()
    assert not frame.action_items["tools_silencer"].IsEnabled()
