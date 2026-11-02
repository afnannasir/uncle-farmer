"""More game clients, editing plans, scripts and settings, the tools and the guide."""

from __future__ import annotations

import os
import subprocess

import wx

from ... import filelock, gameinstall, i18n, paths, settings
from ...engine import dungeon, plan as plans, script
from ...i18n import _
from .. import agentfix
from ..common import ask, ask_ok, error, info, save_file, show_text, modal
from ..plan import PlanDialog
from ..profile import ProfileDialog
from ..scripts import ScriptDialog
from ..setup import SetupDialog
from .base import FrameParts, character_of


class EditingMixin(FrameParts):
    # ------------------------------------------------------------ more game clients
    def open_client(self) -> None:
        """Start one more Mist World. The agent in the game folder is what lets it run beside the
        others, so it has to be installed; and only clients started since then have it."""
        folder = self.remember_game_folder()
        if not gameinstall.is_game_folder(folder) or gameinstall.status(folder) != "installed":
            if ask(self, _("The agent isn't installed in the game yet, and without it a second game closes "
                           "right after it starts. Open Settings now?")):
                self.open_setup()
            return
        try:
            subprocess.Popen([os.path.join(folder, "mw.exe")], cwd=folder, close_fds=True,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0x8))
        except OSError as e:
            error(self, _("Couldn't start the game: {err}", err=e))
            return
        self.say(_("Opening another game window. Log in, then choose it in the Games list."))
        wx.CallLater(8000, self.refresh_games)

    # ------------------------------------------------------------ editing
    def new_plan(self) -> None:
        with PlanDialog(self, dict(plans.PLAN_DEFAULTS, spots=[], end={"how": "start"}), self.snapshot) as d:
            if d.ShowModal() == wx.ID_OK:
                self.fill_runs(("plan", d.plan["name"]))

    def copy_plan(self) -> None:
        item = self.listed_item() if self.nb.GetSelection() == 1 else self.selected_item()
        if not item or item[0] != "plan":
            self.say(_("Select a plan first."))
            return
        p = plans.load(item[1])
        p["name"] = _("{name} copy", name=p["name"])
        with PlanDialog(self, p, self.snapshot) as d:
            d.original_name = ""
            if d.ShowModal() == wx.ID_OK:
                self.fill_runs(("plan", d.plan["name"]))

    def new_script(self) -> None:
        with ScriptDialog(self) as d:
            d.ShowModal()
            name = d.original
        self.fill_runs(("script", name) if name else None)

    def edit_selected(self) -> None:
        item = self.listed_item() if self.nb.GetSelection() == 1 else self.selected_item()
        if not item:
            self.new_plan()
            return
        if item[0] == "plan":
            with PlanDialog(self, plans.load(item[1]), self.snapshot) as d:
                if d.ShowModal() == wx.ID_OK:
                    self.fill_runs(("plan", d.plan["name"]))
        else:
            with ScriptDialog(self, item[1]) as d:
                d.ShowModal()
                name = d.original
            self.fill_runs(("script", name))

    def delete_selected(self) -> None:
        item = self.listed_item() if self.nb.GetSelection() == 1 else self.selected_item()
        if not item:
            return
        if not ask(self, _("Delete {name}? This can't be undone.", name=item[1])):
            return
        try:
            if item[0] == "plan":
                plans.delete(item[1])
            else:
                filelock.delete(plans.script_path(item[1]))
        except OSError as e:
            error(self, _("Couldn't delete it: {err}", err=e))
            return
        self.fill_runs()

    def script_help(self) -> None:
        show_text(self, _("Script commands"), script.__doc__ or "")

    def edit_profile(self) -> None:
        name = character_of(self.snapshot())
        if not name:
            error(self, _("Log in to the character in the game first, or use Character, Another character."))
            return
        with ProfileDialog(self, name, self.s.get("game_folder", "")) as d:
            d.ShowModal()

    def walk(self) -> None:
        """Bot, Walk to: the walking assistant takes the selected game's character to a dungeon
        entrance or a map on the fastest Swift fusion beast, never stopping to fight."""
        from ..walk import WalkDialog
        if self.selected_pid() is None:
            self.refresh_games()
        if self.selected_pid() is None:
            self.say(_("No game with the agent is running."))
            return
        if self.selected_pid() in self.runs:
            self.say(_("Stop the bot first."))
            return
        with WalkDialog(self, self.snapshot) as d:
            if d.ShowModal() != wx.ID_OK:
                return
            text, title = d.script, d.title
        self.run_errand(text, title, walk=True)

    def open_tool(self, which: str) -> None:
        """Tools, Gem Maker / Equipment Enhancer / Daily chores / Gathering / Dungeons: set up for the
        logged-in character, then check or start it as a job on that game."""
        from ..crafting import CHECK, START, EnhancerDialog, GemMakerDialog
        name = character_of(self.snapshot())
        if not name:
            error(self, _("Log in to the character in the game first."))
            return
        if self.selected_pid() not in self.runs:
            self.scanned = getattr(self, "scanned", {})
            self.scanned.pop(name, None)
            with wx.BusyCursor():
                self.auto_scan(name)          # a fresh look at the bag and gear first
        from ..chores import ChoresDialog
        from ..dungeon import DungeonDialog
        from ..gathering import GatheringDialog
        cls, cmd, title = {"gem": (GemMakerDialog, "gem_maker", _("Gem Maker")),
                           "enhance": (EnhancerDialog, "enhance", _("Equipment Enhancer")),
                           "chores": (ChoresDialog, "chores", _("Daily chores")),
                           "gather": (GatheringDialog, "gather", _("Gathering")),
                           "dungeon": (DungeonDialog, "dungeon", _("Dungeons"))}[which]
        # "Add where I stand" and "Use where I stand now" read the game
        extra = (self.snapshot,) if which in ("gather", "dungeon") else ()
        with cls(self, name, *extra) as d:
            code = d.ShowModal()
            cfg = getattr(d, "cfg", None)
        if code == CHECK:
            self.run_errand(cmd + " check\n", _("{tool} check", tool=title))
        elif code == START and which == "dungeon":
            self.run_errand(dungeon.script(cfg or {}), title)     # its end place, then the caves
        elif code == START:
            self.run_errand(cmd + "\n", title)

    def edit_bag(self) -> None:
        name = character_of(self.snapshot())
        if not name:
            error(self, _("Log in to the character in the game first."))
            return
        from ..bagmanager import BagManagerDialog
        if self.selected_pid() not in self.runs:
            self.scanned = getattr(self, "scanned", {})
            self.scanned.pop(name, None)
            with wx.BusyCursor():
                self.auto_scan(name)          # a fresh look at the bag before choosing
        with BagManagerDialog(self, name) as d:
            d.ShowModal()

    def edit_other_profile(self) -> None:
        names = settings.profiles()
        if not names:
            info(self, _("No character has settings yet. Log in to one in the game and the bot scans it."))
            return
        with modal(wx.SingleChoiceDialog(self, _("Character:"), _("Another character"), names)) as d:
            if d.ShowModal() != wx.ID_OK:
                return
            name = names[d.GetSelection()]
        with ProfileDialog(self, name, self.s.get("game_folder", "")) as d:
            d.ShowModal()

    # ------------------------------------------------------------ settings
    def first_run(self) -> None:
        self.after_update()
        folder = self.remember_game_folder()
        if not gameinstall.is_game_folder(folder):
            if ask(self, _("Welcome to Uncle Farmer. The agent needs to be installed in Mist World first. "
                           "Open Settings now?")):
                self.open_setup()
            return
        self.check_agent(folder)

    def check_agent(self, folder: str) -> None:
        """The agent must be in the game's settings, pointing at this copy, and every open game
        must have loaded this version of it. Otherwise it is reinstalled (reinstall_agent)."""
        st = gameinstall.status(folder)
        if st == "missing":
            why = _("Mist World's settings don't load it (it was never installed there, or another tool "
                    "removed it)")
        elif st == "other":
            why = _("Mist World loads another Uncle Farmer's agent (an older copy, or this one before it "
                    "moved)")
        else:
            stale = gameinstall.stale_games()
            if not stale:
                return
            why = _("open games started without this version's agent: {n}", n=len(stale))
        self.reinstall_agent(folder, why)

    def reinstall_agent(self, folder: str, why: str) -> None:
        """Ask; on OK close every open game (a game loads the agent only as it starts), then
        install the agent again. Nothing happens while a bot runs: its game would close under it."""
        if self.runs:
            error(self, _("Uncle Farmer's agent needs to be reinstalled: {why}. Stop every running bot, then "
                          "use File, Settings, Install.", why=why))
            return
        games = agentfix.running_pids()
        if games:
            text = _("Uncle Farmer's agent needs to be reinstalled: {why}.\n\nPress OK to close every Mist "
                     "World window ({n} open) and reinstall it. A game loads the agent only as it starts, so "
                     "start Mist World again afterwards.", why=why, n=len(games))
        else:
            text = _("Uncle Farmer's agent needs to be reinstalled: {why}.\n\nPress OK to reinstall it.", why=why)
        if not ask_ok(self, text, _("Reinstall the agent")):
            return
        if not games:
            self._install_agent(folder, [])
            return
        self.say(_("Closing every Mist World window to reinstall the agent..."))
        agentfix.close_then(games, lambda left: self._install_agent(folder, left))

    def _install_agent(self, folder: str, left: list[int]) -> None:
        if self.destroyed:
            return
        if left:
            error(self, _("Mist World windows that didn't close: {n}. The agent wasn't reinstalled: close them, "
                          "then use File, Settings, Install.", n=len(left)))
            return
        try:
            gameinstall.install(folder)
        except OSError as e:
            error(self, _("Couldn't change the game's settings: {err}", err=e))
            return
        if self.s.get("game_folder") != folder:
            self.s["game_folder"] = folder
            save_file(self, settings.save_app, self.s)
        info(self, _("The agent is reinstalled. Start Mist World again: it loads the agent as it starts."))

    def remember_game_folder(self) -> str:
        """The game folder in the settings, or - when that is empty or holds no agent - the one
        the agent is installed in, found and saved (gameinstall.find_installed)."""
        folder = self.s.get("game_folder", "")
        if gameinstall.status(folder) == "installed":
            return folder
        found = gameinstall.find_installed(folder)
        if found:
            self.s["game_folder"] = found
            save_file(self, settings.save_app, self.s)
            return found
        return folder

    def open_setup(self) -> None:
        with SetupDialog(self, self.s) as d:
            if d.ShowModal() != wx.ID_OK:
                return
            old_lang = self.s.get("language", "en")
            try:
                d.apply()
            except (ValueError, IndexError) as e:
                error(self, _("Couldn't apply the settings: {err}", err=e))
                return
            if save_file(self, settings.save_app, self.s) and self.s["language"] != old_lang:
                i18n.load(self.s["language"])
                info(self, _("Restart Uncle Farmer to switch the language."))

    def open_user_guide(self) -> None:
        """README.html beside the exe (build_exe.py makes it from docs/USER_GUIDE.md)."""
        page = os.path.join(paths.APP, "README.html")
        if not os.path.exists(page):
            page = os.path.join(paths.DOCS, "USER_GUIDE.md")       # running from source
        os.startfile(page)
