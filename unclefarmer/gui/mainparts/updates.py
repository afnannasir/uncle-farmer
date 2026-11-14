"""Updates (updater.py): after one, checking, offering and installing."""

from __future__ import annotations

import threading
import traceback
from contextlib import AbstractContextManager

import wx

from ... import __version__, applog, settings, updater
from ...i18n import _
from .. import agentfix
from ..common import ask, ask_ok, error, info, save_file
from .base import FrameParts


class UpdatesMixin(FrameParts):
    # ------------------------------------------------------------ updates (updater.py)
    def after_update(self) -> None:
        """Say how an update went, empty the staging folder, and look for a newer one in the background."""
        result = updater.take_result()
        mine = updater.installed_version()
        # In the background: the update copier may still be closing, and the cleanup waits for it.
        threading.Thread(target=updater.clean, kwargs={"wait": 60}, daemon=True).start()
        if result is not None and result.get("ok"):
            applog.bootup.note("the update to %s finished" % mine)
            info(self, _("Uncle Farmer is updated to {v}.", v=mine))
        elif result is not None:
            applog.bootup.note("the update to %s didn't finish: %s" % (result.get("version"), result.get("error")),
                               "WARNING")
            error(self, _("The update to {v} didn't finish: {why}\nUncle Farmer {cur} still works. The details are "
                          "in {log}.", v=result.get("version"), why=result.get("error"), cur=mine,
                          log=updater.LOG))
        ch = updater.channel(self.s.get("update_channel"))
        if not updater.frozen():
            applog.bootup.note("no update check: this copy runs from source")
        elif not self.s.get("check_updates", True):
            applog.bootup.note("no update check: it is off in Settings")
        else:
            def look() -> None:
                rel = updater.check(update_channel=ch)
                if rel and rel["version"] != self.s.get("skip_version"):
                    wx.CallAfter(self.offer_update, rel, True)
            threading.Thread(target=look, daemon=True).start()
            applog.bootup.note("update check (%s channel) running in the background" % ch)

    def check_updates(self) -> None:
        """Help, Check for updates. The request goes to a worker: GitHub can take its time and the
        window must not freeze (a BusyCursor only hid that)."""
        if self.destroyed:
            return
        self._append(_("Looking for a new version"), False)
        ch = updater.channel(self.s.get("update_channel"))

        def work() -> None:
            rel: updater.Release | None = None
            err: updater.UpdateError | None = None
            try:
                rel = updater.latest(update_channel=ch)
            except updater.UpdateError as e:
                err = e
                updater.log("update check (%s) failed: %s" % (ch, e), "WARNING")
            else:
                updater.log("update check (%s), asked from the Help menu: newest %s" % (
                    ch, rel["version"] if rel else "none to see"))
            try:
                wx.CallAfter(self._checked_updates, rel, err)
            except RuntimeError:          # the window went away meanwhile
                pass
        threading.Thread(target=work, daemon=True).start()

    def _checked_updates(self, rel: updater.Release | None, err: updater.UpdateError | None) -> None:
        if self.destroyed:
            return
        self.SetStatusText("")
        dev = updater.channel(self.s.get("update_channel")) == "dev"
        mine = updater.installed_version()
        if err is not None:
            error(self, _("Couldn't reach GitHub to look for updates: {why}", why=err))
        elif rel is None and not updater.REPO:
            info(self, _("Uncle Farmer {v} doesn't look for updates online. New versions come as a zip: "
                         "unpack it over this folder.", v=__version__))
        elif rel is None and dev:
            info(self, _("No development build of Uncle Farmer can be seen on GitHub yet."))
        elif rel is None:
            info(self, _("No release of Uncle Farmer can be seen on GitHub yet."))
        elif not updater.is_newer(rel["version"], mine) and updater.is_dev(mine) and not dev:
            # switched back to master: a stable release is offered once one is newer than this build
            info(self, _("You have the development build {v}. The newest stable release, {stable}, is older; "
                         "the next stable release newer than your build will be offered.", v=mine,
                         stable=rel["version"]))
        elif not updater.is_newer(rel["version"], mine):
            info(self, _("Uncle Farmer {v} is the newest version.", v=mine))
        else:
            self.offer_update(rel, False)

    def offer_update(self, rel: updater.Release, at_start: bool) -> None:
        if self.destroyed:
            return
        notes = rel.get("notes") or _("(no notes)")
        if len(notes) > 1500:
            notes = notes[:1500].rstrip() + " ..."
        if updater.is_dev(rel["version"]):
            notes = _("This is a development build from the dev branch: the newest changes, not yet a stable "
                      "release, and it can break.") + "\n" + notes
        if not ask(self, _("Uncle Farmer {v} is out (you have {cur}).\n\nWhat's new:\n{notes}\n\n"
                           "Update now? Uncle Farmer closes, updates and starts again; your characters, plans, "
                           "scripts and settings stay as they are.", v=rel["version"],
                           cur=updater.installed_version(), notes=notes),
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
        games = agentfix.running_pids()
        if games:
            # An open game holds the agent file, which the update replaces: the games close first.
            if not ask_ok(self, _("The update reinstalls Uncle Farmer's agent, which the open games have loaded, "
                                  "so every Mist World window must close first.\n\nPress OK to close them ({n} "
                                  "open) and update. Start Mist World again after the update.", n=len(games)),
                          _("Update")):
                return
            self.updating = True
            self.say(_("Closing every Mist World window for the update"))
            agentfix.close_then(games, lambda left: self._games_closed_for_update(rel, left))
            return
        self._download_update(rel)

    def _games_closed_for_update(self, rel: updater.Release, left: list[int]) -> None:
        self.updating = False
        if self.destroyed:
            return
        if left:
            error(self, _("Mist World windows that didn't close: {n}. Close them, then update.", n=len(left)))
            return
        self._download_update(rel)

    def _download_update(self, rel: updater.Release) -> None:
        self.updating = True
        dlg = wx.ProgressDialog(_("Update"), _("Downloading Uncle Farmer {v}", v=rel["version"]), 100, self,
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
            claim = updater.claimed("update")
            held = False
            try:
                # The completion callback owns the claim until the copier has taken it over.
                claim.__enter__()
                held = True
                zip_path = updater.download(rel, progress)
                wx.CallAfter(self._update_progress, dlg, state, 99, 99, _("Unpacking"))
                folder = updater.unpack(zip_path)
            except Exception as e:
                err = e
                updater.log("the update to %s stopped: %s\n%s" % (rel["version"], e, traceback.format_exc()),
                            "INFO" if str(e) == "cancelled" else "WARNING")
            try:
                wx.CallAfter(self._update_done, dlg, rel, folder, err, claim if held else None)
            except RuntimeError:
                if held:
                    claim.__exit__(None, None, None)
                updater.log("the update window closed before completion", "WARNING")
        try:
            threading.Thread(target=work, daemon=True).start()
        except Exception as err:
            updater.log("could not start the download worker\n" + traceback.format_exc(), "ERROR")
            self._update_done(dlg, rel, None, err)

    def _update_progress(self, dlg: wx.ProgressDialog, state: dict[str, bool], done: int, total: int,
                         message: str | None = None) -> None:
        if self.destroyed or not self.updating:
            state["cancel"] = True
            return
        value = min(99, done * 100 // max(1, total))
        keep = dlg.Update(value, message)[0] if message else dlg.Update(value)[0]
        if not keep:
            state["cancel"] = True

    def _update_done(self, dlg: wx.ProgressDialog, rel: updater.Release, folder: str | None,
                     err: Exception | None, claim: AbstractContextManager[None] | None = None) -> None:
        self.updating = False
        if self.destroyed:
            if claim is not None:
                claim.__exit__(None, None, None)
            return
        dlg.Destroy()
        if err is not None or folder is None:        # (no folder comes only with an error)
            if claim is not None:
                claim.__exit__(None, None, None)
            if str(err) != "cancelled":
                error(self, _("The update didn't download: {why}", why=err))
            updater.clean()
            return
        self._apply_downloaded_update(rel, folder, claim)

    def _apply_downloaded_update(self, rel: updater.Release, folder: str,
                                 claim: AbstractContextManager[None] | None) -> None:
        handed_on = False
        handle: updater.ApplyProcess | None = None
        try:
            if self.destroyed:
                return
            games = agentfix.running_pids()
            if games:
                if not ask_ok(self, _("The update reinstalls Uncle Farmer's agent, which the open games have loaded, "
                                      "so every Mist World window must close first.\n\nPress OK to close them ({n} "
                                      "open) and update. Start Mist World again after the update.", n=len(games)), _("Update")):
                    return
                self.updating = True
                agentfix.close_then(games, lambda left: self._games_closed_for_apply(rel, folder, claim, left))
                handed_on = True
                return
            handle = updater.start_apply(folder, rel["version"])
            self.pending_update = handle
            self.close_reason = "the update to %s is ready: the paused copier awaits shutdown" % rel["version"]
            self.Close()
            if self.destroyed:
                updater.schedule_apply(handle)
            else:
                handle.cancel()
                updater.log("the update is ready but not applied: closing was cancelled")
        except Exception as e:
            updater.log("the update couldn't start: %s\n%s" % (e, traceback.format_exc()), "ERROR")
            if handle is not None:
                handle.cancel()
                if self.destroyed:
                    updater.schedule_apply(handle)  # recover after the instance lock is released
            if not self.destroyed:
                error(self, _("The update couldn't start: {why}", why=e))
        finally:
            self.pending_update = None
            self.updating = False
            if claim is not None and not handed_on:
                claim.__exit__(None, None, None)

    def _games_closed_for_apply(self, rel: updater.Release, folder: str,
                                claim: AbstractContextManager[None] | None, left: list[int]) -> None:
        if left:
            self.updating = False
            if claim is not None:
                claim.__exit__(None, None, None)
            if not self.destroyed:
                error(self, _("Mist World windows that didn't close: {n}. Close them, then update.", n=len(left)))
        else:
            self._apply_downloaded_update(rel, folder, claim)
