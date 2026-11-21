"""The read-only changelog shared by Help and the update confirmation."""

from __future__ import annotations

import threading

import wx

from .. import updater
from ..i18n import _
from .common import Dialog, markdown_text, ok_cancel, screen_area


class ChangelogDialog(Dialog):
    def __init__(self, parent: wx.Window, release: updater.Release | None = None) -> None:
        title = _("Update") if release is not None else _("Changelog")
        super().__init__(parent, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.release = release
        layout = wx.BoxSizer(wx.VERTICAL)
        if release is not None:
            message = _("Uncle Farmer {v} is out (you have {cur}).\n\n"
                        "Uncle Farmer closes, updates and starts again; your characters, plans, scripts and settings "
                        "stay as they are.", v=release["version"], cur=updater.installed_version())
            if updater.is_dev(release["version"]):
                message += "\n\n" + _("This is a development build from the dev branch: the newest changes, not yet a "
                                      "stable release, and it can break.")
            summary = wx.StaticText(self, label=message)
            summary.Wrap(min(self.FromDIP(640), screen_area(self).width - self.FromDIP(56)))
            layout.Add(summary, 0, wx.EXPAND | wx.ALL, 8)
        self.text = wx.TextCtrl(self, value=_("Loading the changelog…"), size=self.FromDIP(wx.Size(680, 350)),
                                style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP)
        self.text.SetName(_("Changelog"))
        layout.Add(self.text, 1, wx.EXPAND | wx.ALL, 8)
        if release is not None:
            ok_cancel(self, layout, ok_label=_("&Update now"))
            self.FindWindow(wx.ID_OK).Disable()
        else:
            row = self.CreateStdDialogButtonSizer(wx.CLOSE)
            self.FindWindow(wx.ID_CLOSE).Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
            self.SetEscapeId(wx.ID_CLOSE)
            layout.Add(row, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizerAndFit(layout)
        self.text.SetFocus()
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            text = updater.read_changelog()
        except updater.UpdateError as e:
            updater.log("couldn't load the changelog: %s" % e, "WARNING")
            text = None
        try:
            wx.CallAfter(self._loaded, text)
        except (RuntimeError, AssertionError):
            pass    # the application closed while the request was in flight

    def _loaded(self, text: str | None) -> None:
        if not self or self.IsBeingDeleted():
            return
        notes = ((self.release.get("notes") or "").strip() if self.release is not None else "")
        if text is None:
            content = _("Couldn't load the changelog. Check your internet connection and try again.")
            if notes:
                content += "\n\n" + _("Release notes") + "\n\n" + notes
        elif self.release is None:
            content = text
        else:
            version = self.release["version"]
            section = updater.changelog_section(text, version)
            if section is not None:
                content = section
            elif notes:
                content = _("No changelog entry for {v}. Showing the release notes.", v=version) + "\n\n" + notes
            else:
                content = _("No changelog entry for {v}. Showing the full changelog.", v=version) + "\n\n" + text
        self.text.ChangeValue(markdown_text(content))
        self.text.SetInsertionPoint(0)
        if self.release is not None:
            self.FindWindow(wx.ID_OK).Enable()
