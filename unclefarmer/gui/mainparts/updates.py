"""Updates (updater.py): after one, checking, offering and installing."""

from __future__ import annotations

import threading

import wx

from ... import __version__, gameinstall, settings, updater
from ...i18n import _
from ..common import ask, error, info, save_file
from .base import FrameParts


class UpdatesMixin(FrameParts):
    # ------------------------------------------------------------ updates (updater.py)
    def after_update(self) -> None:
        """Say how an update went, and look for a newer one in the background."""
        result = updater.take_result()
        if result is None:
            updater.clean()
        elif result.get("ok"):
            info(self, _("Uncle Farmer is updated to {v}.", v=__version__))
        else:
            error(self, _("The update to {v} didn't finish: {why}\nUncle Farmer {cur} still works. The details are "
                          "in {log}.", v=result.get("version"), why=result.get("error"), cur=__version__,
                          log=updater.LOG))
        if updater.frozen() and self.s.get("check_updates", True):
            def look() -> None:
                rel = updater.check()
                if rel and rel["version"] != self.s.get("skip_version"):
                    wx.CallAfter(self.offer_update, rel, True)
            threading.Thread(target=look, daemon=True).start()

    def check_updates(self) -> None:
        """Help, Check for updates. The request goes to a worker: GitHub can take its time and the
        window must not freeze (a BusyCursor only hid that)."""
        if self.destroyed:
            return
        self._append(_("Looking for a new version..."), False)

        def work() -> None:
            rel: updater.Release | None = None
            err: updater.UpdateError | None = None
            try:
                rel = updater.latest()
            except updater.UpdateError as e:
                err = e
            try:
                wx.CallAfter(self._checked_updates, rel, err)
            except RuntimeError:          # the window went away meanwhile
                pass
        threading.Thread(target=work, daemon=True).start()

    def _checked_updates(self, rel: updater.Release | None, err: updater.UpdateError | None) -> None:
        if self.destroyed:
            return
        self.SetStatusText("")
        if err is not None:
            error(self, _("Couldn't reach GitHub to look for updates: {why}", why=err))
        elif rel is None and not updater.REPO:
            info(self, _("Uncle Farmer {v} doesn't look for updates online. New versions come as a zip: "
                         "unpack it over this folder.", v=__version__))
        elif rel is None:
            info(self, _("No release of Uncle Farmer can be seen on GitHub yet."))
        elif not updater.is_newer(rel["version"]):
            info(self, _("Uncle Farmer {v} is the newest version.", v=__version__))
        else:
            self.offer_update(rel, False)

    def offer_update(self, rel: updater.Release, at_start: bool) -> None:
        if self.destroyed:
            return
        notes = rel.get("notes") or _("(no notes)")
        if len(notes) > 1500:
            notes = notes[:1500].rstrip() + " ..."
        if not ask(self, _("Uncle Farmer {v} is out (you have {cur}).\n\nWhat's new:\n{notes}\n\n"
                           "Update now? Uncle Farmer closes, updates and starts again; your characters, plans, "
                           "scripts and settings stay as they are.", v=rel["version"], cur=__version__, notes=notes),
                   _("Update")):
            if at_start:                      # not asked again at start for this one; the Help menu still offers it
                self.s["skip_version"] = rel["version"]
                save_file(self, settings.save_app, self.s)
            return
        self.install_update(rel)

    def install_update(self, rel: updater.Release) -> None:
        if not updater.frozen():
            info(self, _("This copy runs from its source code: update it with git pull."))
            return
        if self.updating:
            return
        if self.runs:
            error(self, _("Stop every running bot first, then update."))
            return
        if gameinstall.running_game_folders():
            error(self, _("Close every Mist World window first: an open game keeps the bot's agent file in use, "
                          "so it can't be updated."))
            return
        self.updating = True
        dlg = wx.ProgressDialog(_("Update"), _("Downloading Uncle Farmer {v}...", v=rel["version"]), 100, self,
                                wx.PD_APP_MODAL | wx.PD_CAN_ABORT | wx.PD_AUTO_HIDE | wx.PD_ELAPSED_TIME)
        state = {"cancel": False}       # the worker asks this; the window's thread sets it

        def progress(done: int, total: int) -> bool:
            if self.destroyed:
                return False
            try:
                wx.CallAfter(self._update_progress, dlg, state, done, total)
            except RuntimeError:
                return False
            return not state["cancel"]

        def work() -> None:
            folder: str | None = None
            err: Exception | None = None
            try:
                zip_path = updater.download(rel, progress)
                try:
                    wx.CallAfter(self._update_progress, dlg, state, 99, 99, _("Unpacking..."))
                except RuntimeError:
                    pass
                folder = updater.unpack(zip_path)
            except (updater.UpdateError, OSError) as e:
                err = e
            try:
                wx.CallAfter(self._update_done, dlg, rel, folder, err)
            except RuntimeError:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _update_progress(self, dlg: wx.ProgressDialog, state: dict[str, bool], done: int, total: int,
                         message: str | None = None) -> None:
        if self.destroyed:
            state["cancel"] = True
            return
        value = min(99, done * 100 // max(1, total))
        keep = dlg.Update(value, message)[0] if message else dlg.Update(value)[0]
        if not keep:
            state["cancel"] = True

    def _update_done(self, dlg: wx.ProgressDialog, rel: updater.Release, folder: str | None,
                     err: Exception | None) -> None:
        self.updating = False
        if self.destroyed:
            return
        dlg.Destroy()
        if err is not None or folder is None:        # (no folder comes only with an error)
            if str(err) != "cancelled":
                error(self, _("The update didn't download: {why}", why=err))
            updater.clean()
            return
        try:
            updater.start_apply(folder, rel["version"])
        except OSError as e:
            error(self, _("The update couldn't start: {why}", why=e))
            return
        self.Close()
