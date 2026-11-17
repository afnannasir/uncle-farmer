"""Edit a sound-silencer draft; only Save changes the game or saved file."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import wx

from .. import silencer
from ..i18n import _
from ..link import AgentLink
from .common import Dialog, labeled, modal


class SilencerDialog(Dialog):
    def __init__(self, parent: wx.Window, pid: int, character: str | None,
                 on_closed: Callable[[], None], *, start_discovery: bool = True) -> None:
        super().__init__(parent, title=_("Sound Silencer - {name}", name=character or _("game")), size=wx.Size(680, 530))
        self.pid = pid
        self.host = parent
        self.on_closed = on_closed
        self.closed = False
        self.saving = False
        self.known: set[int] = set()
        self.rows: list[dict[str, Any]] = []
        self.ids: dict[bool, list[int]] = {False: [], True: []}
        load_error = ""
        try:
            self.rows = [dict(row) for row in silencer.load()]
        except (OSError, ValueError, RuntimeError) as e:
            load_error = str(e)
        panel = wx.Panel(self)
        box = wx.BoxSizer(wx.VERTICAL)
        self.entry = labeled(panel, box, _("Sound &ID:"), lambda w: wx.TextCtrl(w, style=wx.TE_PROCESS_ENTER))
        self.add_button = wx.Button(panel, label=_("&Add"))
        box.Add(self.add_button, 0, wx.TOP | wx.BOTTOM, 8)
        self.lists: dict[bool, wx.ListBox] = {}
        for muted, label in ((False, _("&Sounds:")), (True, _("&Muted Sounds:"))):
            control = labeled(panel, box, label, lambda w: wx.ListBox(w, style=wx.LB_SINGLE), 1)
            control.Bind(wx.EVT_CONTEXT_MENU, lambda e, muted=muted: self.context_menu(e, muted))
            self.lists[muted] = control
        self.status = labeled(panel, box, _("Status:"), lambda w: wx.TextCtrl(
            w, value=load_error or _("Reading available sound IDs..."), style=wx.TE_READONLY | wx.TE_MULTILINE))
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.save_button = wx.Button(panel, label=_("&Save"))
        self.close_button = wx.Button(panel, wx.ID_CLOSE, _("Close without saving"))
        row.Add(self.save_button, 0, wx.RIGHT, 8)
        row.Add(self.close_button)
        box.Add(row, 0, wx.TOP, 8)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(box, 1, wx.EXPAND | wx.ALL, 12)
        panel.SetSizer(outer)
        layout = wx.BoxSizer(wx.VERTICAL)
        layout.Add(panel, 1, wx.EXPAND)
        self.SetSizer(layout)
        self.add_button.Bind(wx.EVT_BUTTON, lambda e: self.add())
        self.entry.Bind(wx.EVT_TEXT_ENTER, lambda e: self.add())
        self.save_button.Bind(wx.EVT_BUTTON, lambda e: self.save())
        self.close_button.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_WINDOW_DESTROY, self.on_destroy)
        self.SetAcceleratorTable(wx.AcceleratorTable([(wx.ACCEL_NORMAL, wx.WXK_ESCAPE, wx.ID_CLOSE)]))
        self.Bind(wx.EVT_MENU, lambda e: self.Close(), id=wx.ID_CLOSE)
        self.add_button.Disable()
        self.load_failed = bool(load_error)
        self.save_button.Enable(not self.load_failed)
        self.refresh_lists()
        self.entry.SetFocus()
        if start_discovery:
            threading.Thread(target=self.discover, name="sound-ids", daemon=True).start()

    def discover(self) -> None:
        known: set[int]
        try:
            known, error = silencer.known_ids(self.pid), ""
        except (OSError, ValueError, RuntimeError) as e:
            known = set[int]()
            error = str(e)
        if not self.closed:
            wx.CallAfter(self.discovered, known, error)

    def discovered(self, known: set[int], error: str = "") -> None:
        if self.closed:
            return
        self.known = known
        self.add_button.Enable(bool(known) and not self.saving and not self.load_failed)
        if not self.load_failed:
            self.status.SetValue(error or (_("Changes are a draft until Save.") if known else
                                          _("Available sound IDs could not be read. Adding sounds is unavailable.")))

    def refresh_lists(self, select: int | None = None) -> None:
        for muted, control in self.lists.items():
            rows = [row for row in self.rows if row["muted"] == muted]
            self.ids[muted] = [row["id"] for row in rows]
            control.Set([row_label(row) for row in rows])
            if select in self.ids[muted]:
                control.SetSelection(self.ids[muted].index(select))

    def add(self) -> None:
        if self.saving or self.load_failed:
            return
        text = self.entry.GetValue().strip()
        if not 1 <= len(text) <= 5 or not text.isascii() or not text.isdecimal() or not 1 <= int(text) <= 99999:
            self.status.SetValue(_("Enter a sound ID between 1 and 99999."))
            return
        sound_id = int(text)
        if sound_id not in self.known:
            self.status.SetValue(_("That sound ID is not in this game's available sound files."))
            return
        if any(row["id"] == sound_id for row in self.rows):
            self.status.SetValue(_("That sound ID is already in the lists."))
            return
        self.rows.append({"id": sound_id, "name": "", "muted": False})
        self.entry.Clear()
        self.refresh_lists(sound_id)
        self.status.SetValue(_("Added to the draft. Choose Save to apply."))

    def move(self, sound_id: int) -> None:
        if self.saving:
            return
        for row in self.rows:
            if row["id"] == sound_id:
                row["muted"] = not row["muted"]
                self.refresh_lists(sound_id)
                return

    def remove(self, sound_id: int) -> None:
        if not self.saving:
            self.rows = [row for row in self.rows if row["id"] != sound_id]
            self.refresh_lists()

    def rename(self, sound_id: int, name: str) -> bool:
        if self.saving:
            return False
        proposed = [dict(row, name=name) if row["id"] == sound_id else dict(row) for row in self.rows]
        try:
            checked = silencer.validate(proposed)
        except ValueError as e:
            self.status.SetValue(str(e))
            return False
        self.rows = [dict(row) for row in checked]
        self.refresh_lists(sound_id)
        return True

    def ask_rename(self, sound_id: int) -> None:
        row = next((row for row in self.rows if row["id"] == sound_id), None)
        if row is None:
            return
        with modal(wx.TextEntryDialog(self, _("Sound name:"), _("Rename sound"), value=row["name"])) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.rename(sound_id, dialog.GetValue())

    def context_menu(self, event: wx.ContextMenuEvent, muted: bool) -> None:
        if self.saving:
            return
        control = self.lists[muted]
        if event.GetPosition() != wx.DefaultPosition:
            index = control.HitTest(control.ScreenToClient(event.GetPosition()))
            if index == wx.NOT_FOUND:
                return
            control.SetSelection(index)
        selected = control.GetSelection()
        if selected == wx.NOT_FOUND:
            return
        sound_id = self.ids[muted][selected]
        menu = wx.Menu()
        try:
            actions: list[tuple[str, Callable[[], Any]]] = [
                (_("Move to Sounds List") if muted else _("Move to Mute List"), lambda: self.move(sound_id)),
                (_("Rename"), lambda: self.ask_rename(sound_id)), (_("Remove"), lambda: self.remove(sound_id))]
            for label, action in actions:
                item = menu.Append(wx.ID_ANY, label)
                menu.Bind(wx.EVT_MENU, lambda e, action=action: action(), item)
            control.PopupMenu(menu)
        finally:
            menu.Destroy()
            control.SetFocus()

    def save(self) -> None:
        if self.saving or self.load_failed:
            return
        try:
            rows = silencer.validate(self.rows)
        except ValueError as e:
            self.status.SetValue(str(e))
            return
        unknown = [row["id"] for row in rows if row["id"] not in self.known]
        if unknown:
            self.status.SetValue(_("These sound IDs are not available in this game: {ids}", ids=", ".join(map(str, unknown))))
            return
        self.saving = True
        for control in (self.entry, self.add_button, self.save_button, self.close_button, *self.lists.values()):
            control.Disable()
        self.status.SetValue(_("Saving and applying sound settings..."))
        threading.Thread(target=self.save_worker, args=(rows,), name="sound-silencer-save", daemon=True).start()

    def save_worker(self, rows: list[dict[str, Any]]) -> None:
        error = ""
        pid = self.pid
        class DeferredLink:
            def json(self, line: str) -> Any:
                with AgentLink(pid=pid, timeout=3) as link:
                    return link.json(line)
        try:
            silencer.save(rows, DeferredLink())
        except (OSError, ValueError, RuntimeError) as e:
            error = str(e)
        persisted: list[dict[str, Any]] | None = None
        try:
            persisted = [dict(row) for row in silencer.load()]
        except (OSError, ValueError, RuntimeError):
            pass
        if not self.closed:
            wx.CallAfter(self.saved, error, persisted)

    def saved(self, error: str, persisted: list[dict[str, Any]] | None = None) -> None:
        if self.closed:
            return
        self.saving = False
        app_settings = getattr(self.host, "s", None)
        if isinstance(app_settings, dict) and persisted is not None:
            app_settings["sound_silencer"] = [dict(row) for row in persisted]
        if not error:
            self.finish()
            if self.IsModal():
                self.EndModal(wx.ID_OK)
            else:
                self.Destroy()
            return
        for control in (self.entry, self.save_button, self.close_button, *self.lists.values()):
            control.Enable()
        self.add_button.Enable(bool(self.known))
        self.status.SetValue(error or _("Sound settings saved and mute rules applied."))

    def finish(self) -> None:
        if not self.closed:
            self.closed = True
            self.on_closed()

    def on_close(self, event: wx.CloseEvent) -> None:
        if self.saving and event.CanVeto():
            event.Veto()
            return
        self.finish()
        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            self.Destroy()

    def on_destroy(self, event: wx.WindowDestroyEvent) -> None:
        if event.GetEventObject() is self:
            self.finish()
        event.Skip()


def row_label(row: dict[str, Any]) -> str:
    return _("{name} ({id})", name=row["name"], id=row["id"]) if row["name"] else str(row["id"])
