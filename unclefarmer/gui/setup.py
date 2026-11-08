"""Settings for the whole app: the game and its agent, speech, language, the farming day, logging
and the updater."""

from __future__ import annotations

import os
from typing import Any

import wx

from .. import __version__, applog, daily, gameinstall, i18n, paths, updater
from ..i18n import _
from . import agentfix
from .common import Dialog, ask, ask_ok, buttons, enable_label, error, group, info, modal, ok_cancel, opens, page, tabs

SPEECH_MODES = ("running", "always", "never")
AFTER = ("nothing", "close_game", "shutdown")
CHECK_UPDATES = wx.ID_HIGHEST + 41      # Updater tab, "Save and check for updates now": the window checks


class SetupDialog(Dialog):
    def __init__(self, parent: wx.Window, app_settings: dict[str, Any]) -> None:
        super().__init__(parent, title=_("Settings"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.s = app_settings
        root = wx.BoxSizer(wx.VERTICAL)
        nb = tabs(self)
        self.game_page(nb)
        self.speech_page(nb)
        self.day_page(nb)
        self.log_page(nb)
        self.updater_page(nb)
        root.Add(nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        ok_cancel(self, root, _("Save"))
        self.SetSizerAndFit(root)
        self.SetMinSize(wx.Size(600, 420))
        self.folder.Bind(wx.EVT_TEXT, lambda e: self.refresh())
        self.refresh()
        # Bound on the dialog, not the button: Enter in a text box sends OK to the dialog directly.
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        if not self.folder.GetValue():
            self.on_find(None, quiet=True)

    def game_page(self, nb: wx.Notebook) -> None:
        panel, box = page(nb, _("Game"))
        f = group(panel, box, _("Mist World"))
        self.folder = f.text(_("&Game folder:"), self.s.get("game_folder", ""))
        parent = f.parent
        buttons(parent, f.box, (opens(_("&Browse")), self.on_browse), (_("&Find the running game"), self.on_find))
        f = group(panel, box, _("Agent"))
        self.status = f.readonly(_("Status:"), lines=2)
        self.install_btn, self.remove_btn = buttons(f.parent, f.box, (_("&Install"), self.on_install),
                                                    (_("&Remove"), self.on_remove))

    def speech_page(self, nb: wx.Notebook) -> None:
        panel, box = page(nb, _("Speech and language"))
        f = group(panel, box, _("Speech"))
        self.speak = f.check(_("&Speak important events with NVDA"), self.s.get("speak", True))
        mode = self.s.get("game_speech", "running")
        self.game_speech = f.choice(_("&Mute the game's own speech:"),
                                    [_("While a bot runs in it"), _("Always"), _("Never")],
                                    SPEECH_MODES.index(mode) if mode in SPEECH_MODES else 0)
        self.speak_end = f.check(_("Speak the &results when a run ends"), self.s.get("results_speak", "end") == "end")
        self.fix_voice = f.check(_("&Fix the speed of voice messages the game plays by itself"),
                                 self.s.get("fix_voice", True))
        every = self.s.get("results_every", 0) or 0
        self.progress, self.every = f.optional(_("Speak &progress while running"), every > 0,
                                               _("Every (minutes):"), every or 15, 1, 600)
        f = group(panel, box, _("Language"))
        self.langs = i18n.languages()
        codes = [c for c, _n in self.langs]
        lang = self.s.get("language", "en")
        self.lang = f.choice(_("&Language:"), [n for _c, n in self.langs], codes.index(lang) if lang in codes else 0)

    def day_page(self, nb: wx.Notebook) -> None:
        panel, box = page(nb, _("Farming day"))
        f = group(panel, box, _("Stop every day"))
        self.day_on = f.check(_("&Stop farming every day at a set time, whatever the bot is doing"),
                              self.s.get("daily_stop", True))
        self.day_at = f.text(_("Stop &at (HH:MM, this computer's clock):"),
                             self.s.get("daily_stop_at") or daily.default_clock())
        f.readonly(_("About:"), _(
            "The game's drop time resets at {reset} on this computer's clock (00:00 GMT). Farming past "
            "it spends the next day's drop time, so every run stops at this time and ends where its plan "
            "says, and no run starts again until {reset}. {default} leaves time to get away from the "
            "monsters before the reset.", reset=daily.reset_clock(), default=daily.default_clock()), lines=4)
        buttons(f.parent, f.box, (_("Use the &default ({time})", time=daily.default_clock()),
                                  lambda e: self.day_at.SetValue(daily.default_clock())))
        f = group(panel, box, _("When farming stops by itself (never after you press Stop)"))
        after = self.s.get("after_stop", "nothing")
        self.after = f.choice(_("&Then:"), [_("Do nothing"), _("Close the game"),
                                            _("Shut down the computer (after every bot has finished)")],
                              AFTER.index(after) if after in AFTER else 0)
        self.delay = f.number(_("Shutdown warning (seconds, you can cancel it):"),
                              self.s.get("shutdown_delay", 60), 10, 600)
        self.after.Bind(wx.EVT_CHOICE, lambda e: self.sync_after())
        self.sync_after()

        def sync(_evt: wx.Event | None = None) -> None:
            self.day_at.Enable(self.day_on.GetValue())
            enable_label(self.day_at, self.day_on.GetValue())
        self.day_on.Bind(wx.EVT_CHECKBOX, sync)
        sync()

    def sync_after(self) -> None:
        on = AFTER[max(0, self.after.GetSelection())] == "shutdown"
        self.delay.Enable(on)
        enable_label(self.delay, on)

    def log_page(self, nb: wx.Notebook) -> None:
        panel, box = page(nb, _("Logging"))
        f = group(panel, box, _("Log files"))
        level = str(self.s.get("log_level", applog.DEFAULT_LEVEL)).upper()
        self.level = f.choice(
            _("&Write to the files in the logs folder:"),
            [_("Everything (debug)"), _("Normal"), _("Warnings and errors"), _("Errors only")],
            applog.LEVELS.index(level) if level in applog.LEVELS else applog.LEVELS.index("INFO"))
        f.readonly(_("About:"), _(
            "The Log tab in the main window always shows a run's lines. This level is for the log "
            "files, which also take the app's own warnings and the crashes with their tracebacks."),
            lines=3)
        f = group(panel, box, _("Rotation"))
        self.log_mb = f.number(_("Start a new file after (MB, 0 = never):"),
                               int(self.s.get("log_max_mb", applog.DEFAULT_MAX_MB) or 0), 0, 1000)
        self.log_kept = f.number(_("Keep &old files:"),
                                 int(self.s.get("log_backups", applog.DEFAULT_BACKUPS) or 1), 1, 50)
        f.readonly(_("About:"), _(
            "A file that reaches the size above is kept as name.1, the older ones move to name.2 and "
            "so on, and the oldest is dropped. The day's run log is rotated the same way inside its day."),
            lines=3)

    def updater_page(self, nb: wx.Notebook) -> None:
        """Everything about updates in one place: this copy's version, where updates come from, the
        check at start, the channel, a skipped release, and a check now (Help, Check for updates)."""
        panel, box = page(nb, _("Updater"))
        f = group(panel, box, _("This copy"))
        f.readonly(_("Status:"), self.updater_status(), lines=4)
        self.updates: wx.CheckBox | None = None
        self.channel: wx.Choice | None = None
        self.unskip: wx.CheckBox | None = None
        if not updater.REPO:          # nowhere to look for updates: no switches for it
            return
        f = group(panel, box, _("Updates"))
        self.updates = f.check(_("Look for a new &version when Uncle Farmer starts"),
                               self.s.get("check_updates", True))
        self.channel = f.choice(_("Release &channel:"),
                                [_("Master: stable releases"),
                                 _("Dev: development builds (newest changes, can break)")],
                                updater.CHANNELS.index(updater.channel(self.s.get("update_channel"))))
        skipped = self.s.get("skip_version") or ""
        if skipped:
            self.unskip = f.check(_("&Offer version {v} again (you chose to skip it)", v=skipped), False)
        buttons(f.parent, f.box, (_("Save and check for updates &now"), self.on_check_now))

    def updater_status(self) -> str:
        """What the Updater tab says about this copy."""
        mine = updater.installed_version()
        lines = [_("Uncle Farmer {v}", v=mine) + (" " + _("(a development build)") if updater.is_dev(mine) else "")]
        if not updater.REPO:
            lines.append(_("This copy doesn't look for updates online. New versions come as a zip: unpack it over "
                           "this folder."))
        elif not updater.frozen():
            lines.append(_("Runs from source: it never updates itself (use git pull)."))
        else:
            lines.append(_("Updates come from GitHub ({repo}) and install themselves after you say yes.",
                           repo=updater.REPO))
        lines.append(_("Update log: {log}", log=updater.LOG))
        if __version__ != mine.split("-dev.")[0]:
            lines.append(_("Program version: {v}", v=__version__))
        return "\n".join(lines)

    def on_check_now(self, _evt: wx.Event) -> None:
        if self.valid():
            self.EndModal(CHECK_UPDATES)

    def valid(self) -> bool:
        if self.day_on.GetValue() and not daily.valid(self.day_at.GetValue()):
            error(self, _("Enter the daily stop time as HH:MM, for example 04:50."))
            self.day_at.SetFocus()
            return False
        return True

    def on_ok(self, _evt: wx.Event) -> None:
        if self.valid():
            self.EndModal(wx.ID_OK)

    def refresh(self) -> None:
        folder = self.folder.GetValue().strip()
        st = gameinstall.status(folder)
        text = {
            "nogame": _("Mist World isn't in this folder. Choose the folder that has mw.exe."),
            "missing": _("Not installed. Press Install, then restart the game."),
            "installed": _("Installed. If the game was running when you installed it, restart the game."),
            "other": _("An older Uncle Farmer agent is installed. Press Install to update it."),
        }.get(st, _("Mist World isn't in this folder. Choose the folder that has mw.exe."))
        if not os.path.exists(paths.AGENT_JAR):
            text += " " + _("The agent file is missing from Uncle Farmer's agent folder.")
        self.status.ChangeValue(text)
        self.install_btn.Enable(st in ("missing", "other"))
        self.remove_btn.Enable(st in ("installed", "other"))

    def on_browse(self, _evt: wx.Event) -> None:
        with modal(wx.DirDialog(self, _("Choose the Mist World folder (the one with mw.exe)"),
                          self.folder.GetValue())) as d:
            if d.ShowModal() == wx.ID_OK:
                self.folder.SetValue(d.GetPath())

    def on_find(self, _evt: wx.Event | None, quiet: bool = False) -> None:
        found = gameinstall.running_game_folders()
        if found:
            self.folder.SetValue(found[0])
        elif not quiet:
            info(self, _("Mist World isn't running. Start it, or use Browse."))

    def on_install(self, _evt: wx.Event) -> None:
        try:
            gameinstall.install(self.folder.GetValue().strip())
        except OSError as e:
            error(self, _("Couldn't change the game's settings: {err}", err=e))
            return
        self.refresh()
        games = agentfix.running_pids()
        if not games:
            info(self, _("Installed. Start Mist World: it loads the agent as it starts."))
            return
        if ask_ok(self, _("Installed. The open games load the agent only when they start again.\n\nPress OK to "
                          "close every Mist World window ({n} open) now, then start Mist World again.", n=len(games)),
                  _("Reinstall the agent")):
            agentfix.close_then(games, self._games_closed)

    def _games_closed(self, left: list[int]) -> None:
        if not self:                     # the dialog closed meanwhile
            return
        if left:
            error(self, _("Mist World windows that didn't close: {n}. Close them yourself.", n=len(left)))
        else:
            info(self, _("The games are closed. Start Mist World again: it loads the agent as it starts."))

    def on_remove(self, _evt: wx.Event) -> None:
        if not ask(self, _("Remove the agent from the game? The bot won't work until you install it again.")):
            return
        try:
            gameinstall.uninstall(self.folder.GetValue().strip())
        except OSError as e:
            error(self, _("Couldn't change the game's settings: {err}", err=e))
            return
        self.refresh()

    def apply(self) -> None:
        self.s["game_folder"] = self.folder.GetValue().strip()
        self.s["language"] = self.langs[max(0, self.lang.GetSelection())][0]
        self.s["speak"] = self.speak.GetValue()
        if self.updates is not None:
            self.s["check_updates"] = self.updates.GetValue()
        if self.channel is not None:
            self.s["update_channel"] = updater.CHANNELS[max(0, self.channel.GetSelection())]
        if self.unskip is not None and self.unskip.GetValue():
            self.s["skip_version"] = ""
        self.s["game_speech"] = SPEECH_MODES[max(0, self.game_speech.GetSelection())]
        self.s["fix_voice"] = self.fix_voice.GetValue()
        self.s["results_speak"] = "end" if self.speak_end.GetValue() else "never"
        self.s["results_every"] = self.every.GetValue() if self.progress.GetValue() else 0
        self.s["daily_stop"] = self.day_on.GetValue()
        self.s["after_stop"] = AFTER[max(0, self.after.GetSelection())]
        self.s["shutdown_delay"] = self.delay.GetValue()
        self.s["log_level"] = applog.LEVELS[max(0, self.level.GetSelection())]
        self.s["log_max_mb"] = self.log_mb.GetValue()
        self.s["log_backups"] = self.log_kept.GetValue()
        applog.configure(self.s)        # the new level and rotation apply to the next line
        t = self.day_at.GetValue().strip()
        # the default is kept as "follow the reset", so it moves if the computer's time zone does
        self.s["daily_stop_at"] = "" if t == daily.default_clock() else t
