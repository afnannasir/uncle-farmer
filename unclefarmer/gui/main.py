"""The main window: three tabs - Bot (games, start and stop, live status), Plans (plans and
scripts), Log.

Several game clients can run at once (the agent lets each one start), and each can run its own
plan: self.runs holds one Run per game process. Start, Stop and the status fields are about the
game selected in the Games list; the Pause key is about the game window in front, if it is one of
ours.
"""

import os

import wx

from .. import __version__, messages, paths, settings
from ..clock import CLOCK, Clock
from ..i18n import _
from .common import ask, buttons, group, guide_text, info, labeled, show_text, tabs
from .mainparts.editing import EditingMixin
from .mainparts.games import GamesMixin
from .mainparts.logpane import LogMixin
from .mainparts.results import ResultsMixin
from .mainparts.runs import RunsMixin
from .mainparts.talk import TalkMixin
from .mainparts.updates import UpdatesMixin

HOTKEY_ID = 0xBF01
RESULTS_HOTKEY_ID = 0xBF02
VK_PAUSE = 0x13
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000


class MainFrame(GamesMixin, RunsMixin, LogMixin, TalkMixin, ResultsMixin, EditingMixin,
                UpdatesMixin, wx.Frame):
    """The window, its three tabs and its menus; what each part does is in gui/mainparts."""
    clock: Clock = CLOCK

    def __init__(self, clock: Clock = CLOCK):
        super().__init__(None, title=_("Uncle Farmer"), size=wx.Size(860, 640))
        self.clock = clock
        self.s = settings.load_app()
        self.runs = {}           # pid -> Run, one per game client
        self.farm_starts = {}    # pid -> (map, x, y) where that game's last farming run started (`home`)
        self.messengers = {}     # pid -> messages.Messenger, private messages of that game
        self.chats = {}          # pid -> chat.Feed, that game's chat (memory only, never saved)
        self.chat_windows = {}   # pid -> its open Chat window
        self.games = []          # [(pid, character, map name)]
        self.items = []          # [("plan"|"script", name)]
        self.log_lines = []      # [(text, important)]
        self.last_line = {}      # pid -> the latest log line of that game's run
        self.silenced = set()    # games this window silenced because game_speech is "always"
        self.filling = False     # the Games list is being remade: its selection events mean nothing
        self.destroyed = False
        self.snap = None         # (pid, snapshot, when): the last reading of a game
        self.snap_busy = False
        self.scanning = set()    # characters being scanned on a worker thread
        self.updating = False

        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)
        self.nb = tabs(panel)
        self.bot_page()
        self.plans_page()
        self.log_page()
        root.Add(self.nb, 1, wx.EXPAND | wx.ALL, 8)
        panel.SetSizer(root)
        self.CreateStatusBar()

        self.build_menu()
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self.poll(), self.timer)
        self.timer.Start(3000)
        if self.RegisterHotKey(HOTKEY_ID, MOD_NOREPEAT, VK_PAUSE):
            self.Bind(wx.EVT_HOTKEY, lambda e: self.toggle(), id=HOTKEY_ID)
        else:
            self._append(_("Another program is using the Pause key; the Pause shortcut won't work."), False)
        if self.RegisterHotKey(RESULTS_HOTKEY_ID, MOD_SHIFT | MOD_NOREPEAT, VK_PAUSE):
            self.Bind(wx.EVT_HOTKEY, lambda e: self.speak_results(front=True), id=RESULTS_HOTKEY_ID)

        self.fill_runs()
        self.refresh_games()
        self.update_buttons()
        self.game_list.SetFocus()
        wx.CallAfter(self.first_run)

    # ------------------------------------------------------------ tabs
    def bot_page(self):
        panel = wx.Panel(self.nb)
        box = wx.BoxSizer(wx.VERTICAL)
        self.game_list = labeled(panel, box, _("&Games:"), lambda w: wx.ListCtrl(
            w, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=wx.Size(-1, 110)), 1)
        for i, (title, width) in enumerate(((_("Character"), 200), (_("Map"), 240), (_("Bot"), 300))):
            self.game_list.InsertColumn(i, title, width=width)
        self.game_list.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e: None if self.filling else self.game_changed())

        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(panel, label=_("Pla&n:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.what = wx.Choice(panel, size=wx.Size(320, -1))
        self.what.SetName(_("Plan"))
        row.Add(self.what, 1, wx.RIGHT, 12)
        self.start_btn = wx.Button(panel, label=_("&Start"))
        self.pause_btn = wx.Button(panel, label=_("&Pause"))
        self.stop_btn = wx.Button(panel, label=_("S&top"))
        row.Add(self.start_btn, 0, wx.RIGHT, 8)
        row.Add(self.pause_btn, 0, wx.RIGHT, 8)
        row.Add(self.stop_btn)
        box.Add(row, 0, wx.EXPAND | wx.TOP, 12)
        self.start_btn.Bind(wx.EVT_BUTTON, lambda e: self.start())
        self.pause_btn.Bind(wx.EVT_BUTTON, lambda e: self.toggle_pause())
        self.stop_btn.Bind(wx.EVT_BUTTON, lambda e: self.stop())

        f = group(panel, box, _("Status"))
        self.fields = {}
        for key, label in (("activity", _("Activity:")), ("where", _("Location:")), ("hp", _("HP:")),
                           ("mp", _("MP:")), ("pet", _("Pet:")), ("players", _("Players near:")),
                           ("bag", _("Bag:")), ("drop", _("Drop time:")),
                           ("run", _("This run:"))):
            self.fields[key] = f.readonly(label)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(box, 1, wx.EXPAND | wx.ALL, 12)
        panel.SetSizer(outer)
        self.nb.AddPage(panel, _("Bot"))

    def plans_page(self):
        panel = wx.Panel(self.nb)
        box = wx.BoxSizer(wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        left = wx.BoxSizer(wx.VERTICAL)
        self.plan_list = labeled(panel, left, _("Plans and &scripts:"), lambda w: wx.ListBox(w, size=wx.Size(280, -1)), 1)
        row.Add(left, 2, wx.EXPAND | wx.RIGHT, 12)
        right = wx.BoxSizer(wx.VERTICAL)
        self.summary = labeled(panel, right, _("Summary:"), lambda w: wx.TextCtrl(
            w, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP), 1)
        row.Add(right, 3, wx.EXPAND)
        box.Add(row, 1, wx.EXPAND)
        buttons(panel, box, (_("&New plan..."), lambda e: self.new_plan()),
                (_("New sc&ript..."), lambda e: self.new_script()),
                (_("&Edit..."), lambda e: self.edit_selected()),
                (_("D&uplicate..."), lambda e: self.copy_plan()),
                (_("&Delete"), lambda e: self.delete_selected()))
        self.plan_list.Bind(wx.EVT_LISTBOX, lambda e: self.show_summary())
        self.plan_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.edit_selected())
        self.plan_list.Bind(wx.EVT_KEY_DOWN, self.on_plan_key)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(box, 1, wx.EXPAND | wx.ALL, 12)
        panel.SetSizer(outer)
        self.nb.AddPage(panel, _("Plans"))

    def log_page(self):
        panel = wx.Panel(self.nb)
        box = wx.BoxSizer(wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(panel, label=_("Sho&w:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.log_filter = wx.Choice(panel, choices=[_("Everything"), _("Important events only")])
        self.log_filter.SetName(_("Show"))
        self.log_filter.SetSelection(0)
        self.log_filter.Bind(wx.EVT_CHOICE, lambda e: self.show_log())
        row.Add(self.log_filter)
        box.Add(row)
        self.log_box = labeled(panel, box, _("&Log:"), lambda w: wx.TextCtrl(
            w, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2), 1)
        buttons(panel, box, (_("Cop&y all"), lambda e: self.copy_log()), (_("Cl&ear"), lambda e: self.clear_log()),
                (_("&Open the log folder"), lambda e: os.startfile(_logs())))
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(box, 1, wx.EXPAND | wx.ALL, 12)
        panel.SetSizer(outer)
        self.nb.AddPage(panel, _("Log"))

    # ------------------------------------------------------------ menus
    def build_menu(self):
        bar = wx.MenuBar()

        def menu(title, entries):
            # No separator lines: a screen reader counts each one as an entry, so a menu of four
            # read "1 of 5".
            m = wx.Menu()
            for label, handler in entries:
                item = m.Append(wx.ID_ANY, label)
                self.Bind(wx.EVT_MENU, handler, item)
            bar.Append(m, title)

        menu(_("&File"), [(_("&Settings..."), lambda e: self.open_setup()),
                          (_("Open the &log folder"), lambda e: os.startfile(_logs())),
                          (_("Open the &results folder"), lambda e: self.open_results_folder()),
                          (_("E&xit"), lambda e: self.Close())])
        menu(_("&View"), [(_("&Bot\tCtrl+1"), lambda e: self.go_tab(0)), (_("&Plans\tCtrl+2"), lambda e: self.go_tab(1)),
                          (_("&Log\tCtrl+3"), lambda e: self.go_tab(2))])
        menu(_("&Bot"), [(_("&Start\tF5"), lambda e: self.start()), (_("S&top\tShift+F5"), lambda e: self.stop()),
                         (_("P&ause or go on\tF6"), lambda e: self.toggle_pause()),
                         (_("Stop &every bot\tCtrl+Shift+S"), lambda e: self.stop_all()),
                         (_("Wa&lk to a dungeon or map...\tCtrl+W"), lambda e: self.walk()),
                         (_("&Results...\tCtrl+R"), lambda e: self.show_results()),
                         (_("Spea&k the results\tCtrl+Shift+R"), lambda e: self.speak_results()),
                         (_("&Mute this game\tCtrl+M"), lambda e: self.set_game_speech(False)),
                         (_("&Unmute this game\tCtrl+U"), lambda e: self.set_game_speech(True)),
                         (_("Mute e&very game"), lambda e: self.set_game_speech(False, every=True)),
                         (_("Unmute every &game"), lambda e: self.set_game_speech(True, every=True)),
                         (_("Open another game &window\tCtrl+O"), lambda e: self.open_client()),
                         (_("&Private messages...\tCtrl+Shift+M"), lambda e: self.edit_messages()),
                         (_("C&hat...\tCtrl+T"), lambda e: self.open_chat()),
                         (_("Let everyone get replies &again"), lambda e: self.forget_answered())])
        menu(_("&Character"), [(_("&Settings...\tCtrl+P"), lambda e: self.edit_profile()),
                               (_("&Bag and supplies...\tCtrl+B"), lambda e: self.edit_bag()),
                               (_("S&can now"), lambda e: self.scan_now()),
                               (_("&Another character..."), lambda e: self.edit_other_profile())])
        menu(_("&Tools"), [(_("&Gem Maker...\tCtrl+G"), lambda e: self.open_tool("gem")),
                           (_("&Equipment Enhancer...\tCtrl+Shift+E"), lambda e: self.open_tool("enhance")),
                           (_("&Daily chores...\tCtrl+Shift+D"), lambda e: self.open_tool("chores"))])
        menu(_("&Plans"), [(_("&New plan...\tCtrl+N"), lambda e: self.new_plan()),
                           (_("New &script...\tCtrl+Shift+N"), lambda e: self.new_script()),
                           (_("&Edit...\tCtrl+E"), lambda e: self.edit_selected()),
                           (_("D&uplicate...\tCtrl+D"), lambda e: self.copy_plan()),
                           (_("&Delete"), lambda e: self.delete_selected())])
        menu(_("&Help"), [(_("&Quick guide\tF1"), lambda e: show_text(self, _("Quick guide"), GUIDE)),
                          (_("&User guide (opens in your browser)"), lambda e: self.open_user_guide()),
                          (_("&Keyboard shortcuts"), lambda e: show_text(self, _("Keyboard shortcuts"), SHORTCUTS)),
                          (_("Ho&w the game works"), lambda e: show_text(self, _("How the game works"),
                                                                          guide_text("GAME_GUIDE.md"))),
                          (_("Script &guide"), lambda e: show_text(self, _("Script guide"), guide_text("SCRIPTING.md"))),
                          (_("&Script commands"), lambda e: self.script_help()),
                          (_("&Commands for trusted players"),
                           lambda e: show_text(self, _("Commands for trusted players"), messages.__doc__)),
                          (_("Check for &updates..."), lambda e: self.check_updates()),
                          (_("&About Uncle Farmer"), lambda e: info(self, _("Uncle Farmer {v}\nA farming bot for Mist World.",
                                                                        v=__version__)))])
        self.SetMenuBar(bar)

    def go_tab(self, i):
        self.nb.SetSelection(i)
        (self.game_list, self.plan_list, self.log_box)[i].SetFocus()

    def on_close(self, evt):
        if self.runs:
            if not ask(self, _("A bot is running. Stop it and quit?")):
                evt.Veto()
                return
            for run in list(self.runs.values()):
                run.stop()
            for run in list(self.runs.values()):
                run.join(5)
        for m in self.messengers.values():
            m.stop()
        self.destroyed = True         # pending CallLater/CallAfter work must not touch the window
        self.UnregisterHotKey(HOTKEY_ID)
        self.UnregisterHotKey(RESULTS_HOTKEY_ID)
        self.timer.Stop()
        self.Destroy()


def _logs():
    return paths.LOGS


SHORTCUTS = """KEYBOARD SHORTCUTS

Anywhere, even inside the game
  Pause               Start or stop the bot (in a game window: that game's bot)
  Shift+Pause         Speak the results of the game in front

Main window
  Ctrl+1, 2, 3        Bot, Plans and Log tabs (Ctrl+Tab also moves between tabs)
  F5                  Start the selected game's bot
  Shift+F5            Stop it
  F6                  Pause it where it is, or let a paused bot go on (you can play by hand meanwhile)
  Ctrl+Shift+S       Stop every bot
  Ctrl+W              Walk to a dungeon entrance or a map (Pause stops the walk)
  Ctrl+R              Results
  Ctrl+Shift+R        Speak the results
  Ctrl+M              Mute the selected game (Ctrl+U lets it talk again; Bot menu: every game)
  Ctrl+O              Open another game window
  Ctrl+G              Gem Maker
  Ctrl+Shift+E        Equipment Enhancer
  Ctrl+Shift+D        Daily chores
  Ctrl+P              Character settings
  Ctrl+B              Bag and supplies
  Ctrl+N              New plan
  Ctrl+Shift+N        New script
  Ctrl+E              Edit the selected plan
  Ctrl+D              Duplicate the selected plan
  Ctrl+Shift+M        Private messages
  Ctrl+T              Chat: read and write the game's chat while the bot farms
  F1                  Quick guide

Plans tab
  Enter               Edit the selected plan or script
  Delete              Delete it

Bag and supplies, Shards and Other items
  Applications key    Move the selected item to another list

Script editor
  F1                  Script commands
  F5                  Check the script
  Ctrl+S              Save
"""


GUIDE = """UNCLE FARMER - QUICK GUIDE

1. FIRST TIME
File, Settings, Game tab: choose the Mist World folder (or press "Find the running game" while it
runs), then press Install and restart the game. Do this once per computer.

2. YOUR CHARACTER
Log in to the game. The bot scans the character by itself: class, skills, keys, potions, fusion
beasts and pets. Character, Settings (Ctrl+P) holds the choices that are yours to make: how to
fight, which potion, which beast, which pets. Keys and timings are under Advanced; most players
never need them.

3. THE BAG
Character, Bag and supplies (Ctrl+B). Shards tab: sort every shard into Throw away, Keep or Sell
with the Move buttons (or the Applications key). To make gems, use Tools, Gem Maker. Other items: Keep, Store, Sell or Throw away.
Supplies: tick the potions and food to refill, and from where. Full bag: what happens when the bag
is full, and stopping when the day's drop time runs out.

TOOLS
Tools, Gem Maker (Ctrl+G): make a gem (choose which, its level and how many, or as many as you
can; it gets more Level 1 shards from the storehouse or the Shard Market when you let it), or make
the most of your bag (every shard into gems, then every colour up as far as it goes). Tools, Equipment Enhancer (Ctrl+Shift+E): your
equipment up to +N (+12 at most), with protection runes; it makes the Enhance Crystals from crystal
shards it takes from the storehouse or buys at the Shard Broker, keeps a stock of them in the bag
if you want, and makes more when they run out. Use
your own gang's craftsmen (cheaper), another gang's by its name, or the nearest city's. Press Check only first: the bot walks there,
reads the real prices and chances, says what it would do, and spends nothing.
Tools, Daily chores (Ctrl+Shift+D): once a game day (from 00:00 GMT) the bank's incomes, hunting
bounties, the free rebirths of the pet you choose, the gang donation, and construction builds at
every place the gang has to build (the inner and outer halls, the highland). Sharing arnebia in a
secret room: once in each half hour, 5 a day; the bot goes in just before a half hour, shares, shares
again as it starts, and leaves, and while farming it leaves for that and comes back by itself. Tick
"at the start of the first farming run" and a farming run does the chores first.

UPDATES
At start Uncle Farmer looks on GitHub for a new version; if there is one it says what's new and
asks. Yes: it downloads it, closes, updates and starts again, keeping your characters, plans,
scripts and settings. Close the games and stop the bots first. Help, Check for updates looks any
time; Settings, Speech and language, Updates turns the start-up check off.

WALKING
Bot, Walk to a dungeon or map (Ctrl+W). Dungeon entrance tab: the dungeons whose entrance NPC the
bot knows (a teleport circle or the NPC at the cave mouth); it walks up to the NPC and, if you
like, opens its menu so you choose. Map tab: any map of the open world (the ones you can walk to and
back from; closed maps and the maps inside dungeons aren't listed), or a cell on it. Show the route says the way
before you go. The whole way it rides your fastest Swift fusion beast, in towns and through
monster ground alike, and it doesn't stop to fight; potions still keep your HP up. Pause stops it.

4. A PLAN
Plans tab, New plan (Ctrl+N). Three tabs, three questions. Where to farm: one or more farming spots
(a map, how to move around, which monsters); with several, it goes round them in turn or through
them once. When to stop: a time, a clock time, a kill count or an item running low (the drop time,
a full bag and deaths stop it by themselves). Where to end: back where you started it (the
default), a place you choose, home with the hearthstone, the nearest map without monsters, or
where it is - every end goes there.

5. RUN IT
Bot tab: choose the game in the Games list, choose the plan, press Start (F5). The Pause key
starts and stops the bot from anywhere, even inside the game. The Status fields show what the bot
is doing, HP, MP, the bag and the drop time. Important events are spoken with NVDA, and the Log tab
keeps everything.

6. MORE THAN ONE GAME
Bot, Open another game window (Ctrl+O). Every game shows in the Games list with its character;
each runs its own plan. Inside a game window, Pause starts and stops that game's bot.
Ctrl+Shift+S stops them all. Each game needs about 1 GB of memory.

7. RESULTS
Bot, Results (Ctrl+R): arnebia, shards, contribution, experience, levels and items, for this run,
today, a week, a month or ever. Shift+Pause speaks a short summary from anywhere.

8. PRIVATE MESSAGES
Bot, Private messages (Ctrl+Shift+M). Strangers get a few of your reply lines, then a goodbye and
silence. Trusted players can send commands; send "help" to the character to get the list.

9. SCRIPTS
For anything a plan can't do: Plans tab, New script. Help, Script commands lists every command.

SAFETY
Stop always lets go of every key. The bot stops by itself when it gets stuck again and again, dies
too often, or can't read the game. If the bot says it can't find the game, a tool such as mwMod
may have removed the agent: open File, Settings and press Install again.
"""
