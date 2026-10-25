"""More game clients, editing plans, scripts and settings, the tools and the guide."""

import os
import subprocess

import wx

from ... import filelock, gameinstall, i18n, paths, settings
from ...engine import plan as plans, script
from ...i18n import _
from ..common import ask, error, info, save_file, show_text
from ..plan import PlanDialog
from ..profile import ProfileDialog
from ..scripts import ScriptDialog
from ..setup import SetupDialog
from .base import FrameParts


class EditingMixin(FrameParts):
    # ------------------------------------------------------------ more game clients
    def open_client(self):
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
    def new_plan(self):
        with PlanDialog(self, dict(plans.PLAN_DEFAULTS, spots=[], end={"how": "start"}), self.snapshot) as d:
            if d.ShowModal() == wx.ID_OK:
                self.fill_runs(("plan", d.plan["name"]))

    def copy_plan(self):
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

    def new_script(self):
        with ScriptDialog(self) as d:
            d.ShowModal()
            name = d.original
        self.fill_runs(("script", name) if name else None)

    def edit_selected(self):
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

    def delete_selected(self):
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

    def script_help(self):
        show_text(self, _("Script commands"), script.__doc__)

    def edit_profile(self):
        s = self.snapshot()
        name = ((s or {}).get("self") or {}).get("name")
        if not name:
            error(self, _("Log in to the character in the game first, or use Character, Another character."))
            return
        with ProfileDialog(self, name, self.s.get("game_folder", "")) as d:
            d.ShowModal()

    def walk(self):
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

    def open_tool(self, which):
        """Tools, Gem Maker / Equipment Enhancer / Daily chores: set up for the logged-in character,
        then check or start it as a job on that game."""
        from ..crafting import CHECK, START, EnhancerDialog, GemMakerDialog
        s = self.snapshot()
        name = ((s or {}).get("self") or {}).get("name")
        if not name:
            error(self, _("Log in to the character in the game first."))
            return
        if self.selected_pid() not in self.runs:
            self.scanned = getattr(self, "scanned", {})
            self.scanned.pop(name, None)
            with wx.BusyCursor():
                self.auto_scan(name)          # a fresh look at the bag and gear first
        from ..chores import ChoresDialog
        cls, cmd, title = {"gem": (GemMakerDialog, "gem_maker", _("Gem Maker")),
                           "enhance": (EnhancerDialog, "enhance", _("Equipment Enhancer")),
                           "chores": (ChoresDialog, "chores", _("Daily chores"))}[which]
        with cls(self, name) as d:
            code = d.ShowModal()
        if code == CHECK:
            self.run_errand(cmd + " check\n", _("{tool} check", tool=title))
        elif code == START:
            self.run_errand(cmd + "\n", title)

    def edit_bag(self):
        s = self.snapshot()
        name = ((s or {}).get("self") or {}).get("name")
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

    def edit_other_profile(self):
        names = settings.profiles()
        if not names:
            info(self, _("No character has settings yet. Log in to one in the game and the bot scans it."))
            return
        with wx.SingleChoiceDialog(self, _("Character:"), _("Another character"), names) as d:
            if d.ShowModal() != wx.ID_OK:
                return
            name = names[d.GetSelection()]
        with ProfileDialog(self, name, self.s.get("game_folder", "")) as d:
            d.ShowModal()

    # ------------------------------------------------------------ settings
    def first_run(self):
        self.after_update()
        folder = self.remember_game_folder()
        if not folder or gameinstall.status(folder) != "installed":
            if ask(self, _("Welcome to Uncle Farmer. The agent needs to be installed in Mist World first. "
                           "Open Settings now?")):
                self.open_setup()

    def remember_game_folder(self):
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

    def open_setup(self):
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

    def open_user_guide(self):
        """README.html beside the exe (build_exe.py makes it from docs/USER_GUIDE.md)."""
        page = os.path.join(paths.APP, "README.html")
        if not os.path.exists(page):
            page = os.path.join(paths.DOCS, "USER_GUIDE.md")       # running from source
        os.startfile(page)
