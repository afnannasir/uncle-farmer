"""Runs a script on its own thread, with its own connection to the game.

The agent releases every key a connection held when that connection closes, so even a crash here
can't leave the character walking.

Several runs can go at once, one per game client (each has its own pid, connection and Bot). They
share the day's log file; applog serialises the writes.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Callable
from typing import Any

from .. import applog, atlas, daily, gameinstall, phrases, scan, settings, stats, trust
from ..clock import CLOCK, Clock, clock_for
from ..errors import carry_on
from ..i18n import _
from ..link import AgentError, AgentLink, server_name
from . import chores, keyboard, keyslots, medals, resume, talents, teamlead, triggers, xpcard
from .bot import Bot, Respawned, Stopped
from .botparts.base import GameBroken, GameLost, WrongCharacter
from .botparts.parts import Sighting, Where
from .script import Runner, ScriptError

AGENT_VERSION = 5   # the agent's StateServer.VERSION this app needs (4: dialog, target; 5: typing quantities)
AGENT_SPEECH = 14   # from here the agent can silence the game and reads the own-HP key's answer
AGENT_POINTS = 24   # from here the agent reads the accumulation-points key's answer (rage, marks, combos; 22-23 dropped its request)
AGENT_TEAM = phrases.SET_AGENT  # also reads teammates' positions ("/6")
AGENT_GUARD = 26    # from here the agent can ignore the player's own Shift/Ctrl/Alt while a run drives the game
AGENT_STATE = 37    # from here `mem state` gives the lean map+self+entities snapshot, without bag/slots/modes/ui
AGENT_KEYS = 38     # from here `keysup` releases only the keys this connection holds, not the whole game's
AGENT_ASK = 39      # from here `ask vitals` / `ask petvitals` get HP and MP from the server without the R key
AGENT_ACT = 40      # from here `use <uid>` uses a bag item and `select <id>` picks the target, no arrow or target keys
AGENT_SHEET = 43    # from here `ask sheet` reads the character sheet without the key or its window
AGENT_SLOTS = 46    # from here `slot <n>` presses an attack key through the game's own key method, no keyboard
                    # (45 had it too, but never let the key go: the game then refused every walk)
AGENT_SERVER = 58   # from here `server` identifies the live S1/S2 route without a game packet
AGENT_LOGIN = 69    # from here automatic login also selects the profile's saved server
AGENT_RECONNECT = 70 # from here `reconnect <id>` logs a dropped game back in with its saved account

def _write_log(line: str) -> None:
    """Every run line to today's log; a broken log must never stop a run (applog says so once)."""
    applog.write(line)


def _write_crash(title: str, character: str | None) -> None:
    """The whole traceback of an unexpected error to logs/errors.log: one line in the window says
    what happened, this says where, so a crash on someone else's PC can be fixed from their logs."""
    applog.crash(title, character or "")


class Run(threading.Thread):
    # set by the window once the run starts (main.py): where it is, what it is, who it follows
    locate: Callable[[str], Where | None] | None
    last_seen: Callable[[str], Sighting | None] | None
    tell: Callable[[str], None] | None
    kind: str
    follows: str | None

    def __init__(self, text: str, title: str, pid: int, on_log: Callable[[str, bool, "Run"], None],
                 on_done: Callable[[str, "Run"], None], walk: bool = False, clock: Clock = CLOCK) -> None:
        """on_log(line, speak, run) and on_done(reason, run) are called from this thread.
        walk: the walking assistant - it isn't farming, so the farming day's end doesn't block it,
        it earns no results, and it ends where it arrived, still riding (no end-of-run trip)."""
        super().__init__(daemon=True, name="unclefarmer-run-%d" % pid)
        self.text, self.title, self.pid = text, title, pid
        self.on_log, self.on_done = on_log, on_done
        self.stop_event = threading.Event()
        self.character: str | None = None   # known once connected; names the run in the log
        self.profile: str | None = None     # the character's own settings key, not merely its display name
        self.tally: stats.Tally | None = None   # what this run earns, readable while it runs
        self.bot: Bot | None = None         # the Bot once running (private message commands reach it here)
        self.link: AgentLink | None = None  # this run's connection to the game, while it runs
        self.muted = False        # this run turned the game's own speech off (the end turns it on again)
        self.speech_choice: str | None = None  # "on"/"off" when the player muted or unmuted this game by hand
        self.ended_by_itself = False  # the run ended on its own (plan done, a stop rule, the daily stop...),
                                      # not by the player's Stop or a lost game: the after-stop action applies
        self.failed = False          # scheduled automation records a failure, not a successful stop
        self.after_stop = "nothing"   # what the app setting said to do then: nothing | close_game | shutdown
        self.before_farming_done = False  # the Character settings' before-farming triggers ran (once a run)
        self.after_farming_done = False   # the Character settings' after-farming triggers ran (once a run)
        self.schedule: tuple[str, str] | None = None   # (schedule id, occurrence) when a schedule started it
        self.clock = clock
        self.started = self.clock.time()
        self.walk = walk
        self.done_called = False      # on_done() has run: it runs once, however the end went
        self.end_failures: list[tuple[str, Exception]] = []   # what failed on the way out (the end goes on regardless)
        # Where this plan or script is (engine/resume.py): kept when the run doesn't end - the game
        # lost or Uncle Farmer closed under it - so the next start can carry on from there.
        self.resume_row: dict[str, Any] | None = None   # set by the window: carry on from this place
        self.game_lost = False       # the game closed, froze or lost our character for good
        self.lost_connection = False  # ... through its connection to the server (GameLost connection)
        self.other_character = False  # another character came into the game (WrongCharacter): nothing more there
        self.app_closing = False     # Uncle Farmer is closing under the run: its place is kept
        self.script: Runner | None = None    # the plan or script whose place is kept
        self.place_lock = threading.Lock()
        self.place_done = False      # the run's end has decided about its place: no more saves
        self.talents_back_done = False   # the usual talents were put back (or tried): once a run
        self.medals_back_done = False    # ... and the usual medals

    def farmed(self) -> bool:
        """The after-stop action (close the game, shut the PC down) is for the end of farming: a
        plan or script that farmed. A tool (Gem Maker, Enhancer), the walking assistant, `come` or
        `follow`, or a script that never farmed, ends and leaves the game and the PC alone."""
        return getattr(self, "kind", "") not in ("errand", "tool", "trigger") and bool(getattr(self.bot, "farmed", False))

    def stop(self) -> None:
        self.stop_event.set()

    def guard(self, bot: Bot, finish: bool = True) -> None:
        """The run stopped on its own (stuck, an error, a stop rule): never leave the character
        standing there. Fight back and drink until nothing attacks, then end somewhere safe as a
        finished run does (finish=False: stay where we are, e.g. beside the player who called us).
        Pressing Stop (or losing the game) ends this at once."""
        for _round in range(2):
            try:
                bot.defend()
                if finish:
                    bot.finish()
                return
            except Respawned:
                continue            # died while guarding and came back: guard again from there
            except (GameLost, WrongCharacter) as e:
                self._lost_while_guarding(e)
                return
            except Exception as e:    # stuck again, or the same bug once more
                carry_on(e, "finishing safely")
                if self.stop_event.is_set():
                    return
                self.log(_("couldn't finish safely: {err}", err=e))
                try:
                    bot.defend()    # finishing failed (stuck again?): at least stay on guard
                except (GameLost, WrongCharacter) as e2:
                    self._lost_while_guarding(e2)
                except Exception as e2:
                    carry_on(e2, "staying on guard")
                    self.log(_("couldn't stay on guard either: {err}", err=e2))
                return

    def _lost_while_guarding(self, e: Stopped) -> None:
        """The game went, or another character came in, while the run ended: noted as if the run
        had stopped on it. Swallowed as any other error, it made the run one that ended by itself:
        the After farming list ran on a game that was gone, the after-stop action closed the game
        (perhaps another player's character now) or shut the PC down, and the run's place, which a
        lost game keeps to carry on from, was cleared."""
        self.failed = True
        if isinstance(e, GameLost):
            self.game_lost = True
            self.lost_connection = e.connection
        else:
            self.other_character = True
        self.log(str(e), speak=True)

    def log(self, message: str, speak: bool=False) -> None:
        line = "%s  %s" % (time.strftime("%H:%M:%S"), message)
        _write_log("%s  %s: %s" % (line[:8], self.character, message) if self.character else line)
        self.on_log(line, speak, self)

    def run(self) -> None:
        """Connect, set up, run the plan or script, and whatever ends it, end it well: one step per
        method below. self.link and self.muted hold what the end has to undo even when the start
        failed half way (self.bot is None until the Bot exists)."""
        reason = _("finished")
        app = settings.load_app()
        try:
            bot = self._start(app)
            reason = self._play(bot) or reason
        except GameBroken as e:
            # The game is frozen: guarding or walking on would only look stuck, and closing the game
            # or the PC as if farming had ended hides what happened. The player restarts the game.
            reason = str(e)
            self.failed = True
            self.game_lost = True
        except triggers.TriggerFailed as e:
            reason = str(e)
            self.failed = True
        except WrongCharacter as e:
            # nothing more in this game: its character isn't ours (no guarding, no end place, no
            # carrying on later, no opening the game again)
            reason = str(e)
            self.failed = True
            self.log(reason, speak=True)
        except Stopped as e:
            if getattr(self, "kind", "") in ("tool", "trigger") and not self.stop_event.is_set():
                self.failed = True
            reason = self._stopped(e)
        except ScriptError as e:
            reason = str(e)
            self.failed = True
        except AgentError as e:
            self.failed = True
            self.game_lost = True
            reason = _("lost the connection to the game") if str(e) != "no_game" else _("the game isn't running with the agent")
        except OSError as e:
            self.failed = True
            self.game_lost = True
            reason = _("lost the connection to the game: {err}", err=e)
        except Exception as e:   # never let the thread die silently
            self.failed = True
            reason = self._crashed(e)
        finally:
            self._paused_game_lost()
            try:
                self._after_farming()
                self._talents_back()         # (not after a failed After step: that one is what counts)
                self._medals_back()
            except triggers.TriggerFailed as e:
                # an After step that had to work didn't: said and recorded, but farming did end by
                # itself, so the after-stop action (close the game, shut the PC down) still runs
                reason = str(e)
                self.failed = True
                self.log(reason, speak=True)
            except (Stopped, AgentError, OSError, ValueError) as e:
                reason = str(e)
                self.failed = not self.stop_event.is_set()
                self.ended_by_itself = False
            except Exception as e:
                reason = self._crashed(e)
                self.failed = True
                self.ended_by_itself = False
            self._end(app, reason)

    def _paused_game_lost(self) -> None:
        """A run paused when its game went (closed, logged out): the player had the game, so it is
        neither opened again nor carried on later. Live 2026-11-24: a paused run's game closed by
        the player came back logged in as that run's character, over the one the player wanted."""
        paused = getattr(self.bot, "paused", None)
        if self.game_lost and paused is not None and paused.is_set():
            self.game_lost = False
            self.failed = True
            self.log(_("the game went while the run was paused: it isn't opened again or carried on"))

    # ---------------------------------------------------------------- starting
    def _start(self, app: dict[str, Any]) -> Bot:
        """The game connected and checked, the character scanned, the keys armed: the Bot, set up."""
        # a tool or a trigger list farms nothing by itself (a step that farms is held to the window
        # by farming_window); Dungeons is farming, so it keeps the daily stop as a plan does
        kind = getattr(self, "kind", "")
        independent = kind == "trigger" or (kind == "tool" and self.text.strip() != "dungeon")
        day_end, blocked = daily.plan(app) if not self.walk and not independent else (None, None)
        character: str
        if blocked:
            raise Stopped(_("the day's farming is over - the drop time resets at {reset}; start again "
                            "after that", reset=blocked))
        link = self.link = AgentLink(pid=self.pid)
        version = link.send("version")
        agent = int(version.split()[1]) if version.startswith("mwagent ") else 0
        if agent < AGENT_VERSION:
            raise Stopped(_("the game is running an older Uncle Farmer agent - restart Mist World so it loads "
                            "the new one"))
        snap = link.json("mem state" if agent >= AGENT_STATE else "mem")
        if not snap.get("ok") and self.profile:
            try:
                self._login_saved_character(link, agent)
            except Stopped:
                # carrying on in a game opened for it: a login that fails is the game lost again
                # (the window opens it once more, up to resume.RESTARTS_MOST times a day)
                self.game_lost = self.resume_row is not None and not self.stop_event.is_set()
                raise
            snap = link.json("mem state" if agent >= AGENT_STATE else "mem")
        if not snap.get("ok"):
            raise Stopped(snap.get("error") or _("log in to a character first"))
        character = self.character = snap["self"]["name"]
        server = server_name(link) if agent >= AGENT_SERVER else ""
        observed = settings.character_profile(character, snap["self"].get("id"), server)
        if self._keyboard_errand() and self.profile and not settings.is_profile_of(observed, self.profile):
            raise Stopped(_("Select the same character before applying the keyboard profile."))
        # The Games list may already know this client's server.  Keep that confirmed identity if
        # one server lookup briefly fails while starting the run.
        if not (self.profile and settings.same_character(self.profile, observed)
                and settings.profile_server(self.profile)):
            self.profile = observed
        applog.thread("run %d %s" % (self.pid, character))
        profile, found = self._profile(link, self.profile)
        guard = self._arm(link, agent, profile, app)
        self.log(_("starting {what} as {name}", what=self.title, name=character), speak=True)
        bot = self._make_bot(link, profile, agent, guard, day_end, app)
        self._set_up(bot, snap, profile, found, agent, app)
        return bot

    def _login_saved_character(self, link: AgentLink, agent: int) -> None:
        """Use this plan's saved account, then wait for the requested character to be selected."""
        if agent < AGENT_LOGIN:
            raise Stopped(_("the game is running an older Uncle Farmer agent - restart Mist World so it loads "
                            "the new one"))
        character = self.profile or ""
        email = settings.load_profile(character).get("login_email")
        entity_id, server = settings.profile_id(character), settings.profile_server(character)
        if not isinstance(email, str) or not email or not entity_id or not server:
            raise Stopped(_("the plan's character has no saved account or server. Log in to it once first."))
        if link.send("arm") != "armed":
            raise Stopped(_("the game refused the login request"))
        reply = link.json("autologin %s %s %s" % (email, entity_id, server))
        if not reply.get("ok"):
            raise Stopped(str(reply.get("error") or _("automatic login could not start")))
        self.log(_("starting automatic login"), speak=True)
        while not self.stop_event.wait(0.25):
            state = link.json("autologinstatus")
            if state.get("running"):
                continue
            if state.get("status") == "ready":
                self.log(_("automatic login finished"))
                return
            raise Stopped(str(state.get("status") or _("automatic login stopped")))
        raise Stopped(_("stopped by you"))

    def _profile(self, link: AgentLink, character: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Every run starts with a fresh scan: skills, keys, potions, beasts, pets -> the profile (a
        character without a profile gets one here). (profile, what the scan found)."""
        try:
            return scan.scan_and_save(link, character, self.game_folder())
        except Exception as e:      # a failed scan must never stop a run; the saved profile still works
            carry_on(e, "scanning the character")
            self.log(_("couldn't scan {name}: {err}", name=settings.profile_display(character), err=e))
            if not settings.has_profile(character):
                self.log(_("{name} has no profile yet - using the default keys. Open Character, Settings to check them.",
                           name=settings.profile_display(character)), speak=True)
            return settings.load_profile(character), {}

    def _arm(self, link: AgentLink, agent: int, profile: dict[str, Any], app: dict[str, Any]) -> str | None:
        """Keys armed, the player's modifiers kept out, the game's own speech off as set. Returns
        the answer to `guardmods on` (None for an agent that can't)."""
        link.send("arm")
        keys = link.send("keys")
        if not keys.startswith("ready"):
            raise Stopped(_("the game's keys can't be used: {why}", why=keys))
        guard: str | None = None
        if agent >= AGENT_GUARD:
            # The player holding Ctrl in another window (NVDA) made the bot's potion key a
            # Ctrl+key hotkey binding: the beast went onto the blue potions' key. The agent
            # now ignores the player's Shift/Ctrl/Alt in this game until this run's link closes.
            guard = link.send("guardmods on")
            if guard != "on":
                self.log(_("couldn't stop your Ctrl/Shift/Alt reaching this game: {why}", why=guard))
        if agent < AGENT_POINTS:
            settings.set_not_saved(profile, "points_key", "")     # an older agent can't read the T key's answer
        if agent < AGENT_SPEECH:
            settings.set_not_saved(profile, "vitals_key", "")     # an older agent can't read the own-HP key's answer
        elif app.get("game_speech", "running") in ("running", "always") and link.send("speech") == "on":
            self.muted = link.send("speech off") == "off"
            if self.muted:
                self.log(_("the game's own speech is off while the bot runs"))
        return guard

    def _make_bot(self, link: AgentLink, profile: dict[str, Any], agent: int, guard: str | None,
                  day_end: dt.datetime | None, app: dict[str, Any]) -> Bot:
        bot = Bot(link, profile, self.log, self.stop_event, clock=self.clock)
        bot.agent_version = agent
        bot.agent_state = agent >= AGENT_STATE
        bot.agent_keys = agent >= AGENT_KEYS
        bot.agent_ask = agent >= AGENT_ASK
        bot.agent_act = agent >= AGENT_ACT
        bot.agent_sheet = agent >= AGENT_SHEET
        bot.agent_slots = agent >= AGENT_SLOTS and bot.p.get("skills_by_slot", True)
        bot.guarding = guard == "on"     # let go while paused: the player may play by hand then
        if day_end:
            bot.daily_stop_at = day_end.timestamp()
            bot.daily_reset = daily.reset_clock()
            self.log(_("farming stops at {time} at the latest (the drop time resets at {reset})",
                       time=day_end.strftime("%H:%M"), reset=bot.daily_reset))
        bot.character = self.character   # names the game in logs/dialogs.log when several run
        me_id = settings.profile_id(self.profile or "")
        bot.char_id = int(me_id) if me_id else None      # another character in this game ends the run
        bot.profile_key = self.profile   # the daily chores are kept per character, not per name
        name = self.profile or self.character or ""

        def save_profile(data: dict[str, Any]) -> None:
            settings.save_profile(name, data)

        bot.save_profile = save_profile
        bot.trusted = trust.from_settings(app, settings.profile_server(self.profile or ""))   # entries naming another server don't count
        bot.locate = getattr(self, "locate", None)   # where another of our characters is (follow, come)
        bot.last_seen = getattr(self, "last_seen", None)   # where our games last saw a trusted player
        bot.tell = getattr(self, "tell", None)       # answers the player who asked for this run
        bot.reconnect = lambda: self._reconnect_saved_character(bot, agent)
        bot.after_login = lambda: self._rearm(bot, agent)
        self.bot = bot
        return bot

    def _reconnect_saved_character(self, bot: Bot, agent: int) -> bool:
        link = self.link
        entity_id = settings.profile_id(self.profile or "")
        if link is None or not entity_id or agent < AGENT_RECONNECT:
            return False
        try:
            if link.send("arm") != "armed":
                return False
            started = link.json("reconnect %s" % entity_id)
            if not started.get("ok"):
                why = str(started.get("error") or "?")
                if "still connected" not in why:     # the server only took the character away a while
                    self.log(_("logging back in didn't start: {why}", why=why))
                return False
            while not self.stop_event.wait(0.25):
                state = link.json("autologinstatus")
                if state.get("running"):
                    continue
                if state.get("status") != "ready":
                    # why this try failed (the connection refused again, the character never
                    # loaded...): what the next lost connection is understood from
                    self.log(_("logging back in didn't work: {why}", why=state.get("status") or "?"))
                    return False
                self._rearm(bot, agent)
                self.log(_("automatic reconnection finished"), speak=True)
                return True
        except (AgentError, OSError, Stopped):
            return False
        return False

    def _rearm(self, bot: Bot, agent: int) -> None:
        """Back in the game after a reconnect or a wait: nothing held, the keys armed, the player's
        modifiers kept out and the game's speech off again, as at the start."""
        link = self.link
        if link is None:
            return
        link.send("keysup")
        bot.held.clear()
        bot.guarding = self._arm(link, agent, bot.p, settings.load_app()) == "on"

    def _set_up(self, bot: Bot, snap: dict[str, Any], profile: dict[str, Any], found: dict[str, Any], agent: int,
                app: dict[str, Any]) -> None:
        """The results, where the run started, max MP, the quick phrases and the keys it needs."""
        if not self.walk:
            if getattr(bot, "agent_sheet", False):
                # the results count contribution and experience from the first sheet they see: a
                # fresh one now, or the first reading comes after the first kills and leaves them out
                bot.ask_sheet(force=True)
            if self.profile:
                stats.adopt_old_file(self.profile)      # its name-only results become its own, once
            bot.tally = self.tally = stats.Tally(self.character or "", self.title, clock=clock_for(bot),
                                                 tool=self._tool(), profile=self.profile)
            if getattr(bot, "daily_stop_at", 0.0):
                bot.tally.deadline(bot.daily_stop_at)     # Time remaining counts down to it at the latest
            bot.tally.update(bot, force=True)
        here: dict[str, Any] = snap.get("self") or {}
        where: dict[str, Any] = snap.get("map", {})
        if where.get("id"):
            bot.start_place = (snap["map"]["id"], int(here.get("x", 0)), int(here.get("y", 0)))
            bot.start_name = snap["map"].get("name")
        sheet: dict[str, Any] = here.get("sheet") or {}
        if profile.get("mp_use", True) and not sheet.get("maxmp"):
            # Max MP is only on the character sheet, and only farming reads it on its own:
            # without it a walk, `come` or `follow` never drank a blue potion.
            bot.refresh_sheet(force=True)
        if self._keyboard_errand():
            bot.phrases = phrases.read(self.game_folder(), here.get("id"))
        else:
            # Useful defaults are filled only on empty slots during normal runs.
            phrases.arrange(bot, self.game_folder(), here.get("id"), found.get("class"), agent >= AGENT_TEAM)
            if not self.walk:
                keyslots.arrange(bot)

    def _keyboard_errand(self) -> bool:
        return (getattr(self, "kind", "") == "errand"
                and self.text.split(maxsplit=1)[:1] == [keyboard.COMMAND])

    def game_folder(self) -> str:
        """This game's own folder (the copy it runs from), else the settings' one."""
        return gameinstall.folder_of(self.pid, settings.load_app().get("game_folder", ""))

    def _tool(self) -> str | None:
        """The job (stats.TOOLS) this run is when it is one from the Tools menu, else None: its
        results are kept apart from farming."""
        if getattr(self, "kind", "") not in ("errand", "tool"):
            return None
        words = self.text.split()
        return words[0] if words and words[0] in stats.TOOLS else None

    # ---------------------------------------------------------------- running and stopping
    def _play(self, bot: Bot) -> str | None:
        """The plan or script itself, then the end of a finished run. A reason when not "finished"."""
        kind = getattr(self, "kind", "")
        if kind == "trigger":
            if not triggers.ticked(bot.p, self.text):
                raise triggers.TriggerFailed(_("No enabled triggers are configured for this sequence."))
            results = triggers.run_stage(bot, self.text, self.log, self.stop_event.is_set, self.farming_window)
            self.failed = any(not success for _job, success in results)
            self.ended_by_itself = not self.stop_event.is_set()
            return _("Trigger sequence completed with failures.") if self.failed else None
        text = self.text
        if kind == "tool" and text.strip() == "dungeon":
            from . import dungeon
            text = dungeon.script(dungeon.config(bot.p))  # preserve the tool's saved ending place too
        script = Runner(bot, text)          # read first: a script that doesn't parse does nothing
        self._take_place(script)
        if getattr(script, "own_lists", False):     # (a test's stand-in runner has none)
            # `triggers off` (Run, Autopilot's script of the day): its steps are written into it
            self.before_farming_done = self.after_farming_done = True
        if kind not in ("errand", "tool") and not self.walk:
            if triggers.farms(script.main, script.subs):
                self._level_first(bot, script.main)
                teamlead.at_start(bot)         # out of another's team, into ours (the Team tab)
                if not self._on_the_farm(bot, script) and not self._carried_on_past_before():
                    self._before_farming()
                self._farming_talents(bot, script)
                self._farming_medals(bot)
            cfg = chores.config(bot.p)
            if cfg.get("before_farming") and chores.due(self.profile or self.character, cfg):
                bot.log(_("the day's chores first"))
                with stats.doing(bot, "chores"):
                    chores.run(bot, cfg)
        self._keep_place()
        script.run()
        if self.walk:
            return _("arrived")          # stay there, on the beast
        for _try in range(3):
            try:
                bot.finish()      # a finished run ends off the beast, on a map where no monsters spawn
                break
            except Respawned:     # died or pulled back on the way: go on from there
                continue
        self.ended_by_itself = True
        return None

    def _stopped(self, e: Stopped) -> str:
        """The run stopped itself (stuck, a stop rule, the drop time...): a frozen game says so;
        otherwise guard, and end somewhere safe unless asked to stay."""
        reason = str(e)
        if isinstance(e, GameLost):
            self.failed = self.game_lost = True     # nothing to guard: our character isn't in the game
            self.lost_connection = e.connection
            return reason
        bot = self.bot
        if bot is not None and not self.stop_event.is_set() and not self.walk:
            try:
                bot.check_game(force=True)   # a frozen game looks like a stuck walk, or any other failure
                broken = False
            except GameBroken as why:
                reason, broken = str(why), True
                self.failed = self.game_lost = True      # opened again (the window's revive)
            except Exception:      # the game gone: guard finds that out itself
                broken = False
            if not broken:
                self.guard(bot, finish=not getattr(e, "stay", False))
                # called away by a player (come, follow, home): not the end of farming, so no
                # closing the game or the PC; nor a game lost (or another character) meanwhile
                self.ended_by_itself = (not self.stop_event.is_set() and not getattr(e, "stay", False)
                                        and not getattr(e, "asked", False)
                                        and not self.game_lost and not self.other_character)
        return reason

    @staticmethod
    def _level_first(bot: Bot, main: list[Any]) -> None:
        """Before anything else in a run that farms: the plan's own keep_level (its script's
        top-level line, which only runs after the Before farming list), then "Stop before the
        next level" checked from where we stand - a run that would stop on its first sheet at
        the farm doesn't set out."""
        for node in main:
            if getattr(node, "kind", "") == "keep_level":
                bot.level_keep = node.opts["xp"]
            if getattr(node, "kind", "") == "keep_drop":
                bot.drop_keep = node.opts["minutes"]
        bot.level_before_start()
        bot.drop_before_start()          # nor with the day's drop time already used up

    @staticmethod
    def _on_the_farm(bot: Bot, script: Runner) -> bool:
        """With "Skip this list when the run starts on its first farming map" ticked: the run
        starts on the map it farms first (a Stop and a start again where it was farming), so the
        Before farming list - often a trip to town and back - is left out. True (and said) then."""
        if not bot.p.get("skip_before_on_farm", True):
            return False
        def done(node: Any) -> bool:
            # a spot whose share of the drop time is used up is skipped by the run without a trip:
            # it isn't the map the run farms first (the Before list ran, or was skipped, by it)
            until = (getattr(node, "opts", {}) or {}).get("until")
            try:
                return until is not None and bool(script.value(until, node.line))
            except Exception:           # not known yet: the line counts, as before
                return False
        first = triggers.first_farm_map(script.main, script.subs, done)
        here = bot.map_id()
        if first is None or first not in (triggers.HERE, here):
            return False
        bot.log(_("already on {map}, where this run farms first - the Before farming list is skipped",
                  map=atlas.label(here)))
        return True

    @staticmethod
    def _farming_talents(bot: Bot, script: Runner) -> None:
        """Character settings, Talents: the talents for farming on before the run sets out - a trip to
        the nearest talent master, there and back to the first farming map, even from that map (a run
        that farmed with the wrong ones until its next town trip had them wrong all night)."""
        p: dict[str, Any] = getattr(bot, "p", None) or {}
        if not p.get("farm_talents", False):
            return
        first = triggers.first_farm_map(script.main, script.subs)
        here = bot.map_id()
        farm = here if first in (None, triggers.HERE) else first
        try:
            talents.for_farming(bot, back_to=(farm, None) if farm != here else None)
        except Respawned:
            # died on the way to the talent master: the run goes on from where it came back (its
            # trip to the farm starts there), with the talents as they are
            bot.log(_("died on the way to the talent master - farming with the talents as they are"), speak=True)

    @staticmethod
    def _farming_medals(bot: Bot) -> None:
        """Character settings, Medals: the medals for farming worn before the run sets out, wherever
        it starts (no NPC, no cost). First the bonus of medals that share a name is read, once each,
        for the settings window. Best effort: medals as they were are no reason not to farm."""
        try:
            medals.learn_bonuses(bot)
            p: dict[str, Any] = getattr(bot, "p", None) or {}
            if p.get("farm_medals", False):
                medals.for_farming(bot)
        except Stopped as e:
            if getattr(e, "final", False):
                raise
            bot.log(_("the medals for farming weren't put on: {err}", err=e))

    def _medals_back(self) -> None:
        """Character settings, Medals: the usual medals worn again when farming ended by itself,
        wherever it ended, after the After farming list. Once a run, best effort as _talents_back."""
        bot = self.bot
        p: dict[str, Any] = getattr(bot, "p", None) or {}
        if (getattr(self, "medals_back_done", False) or bot is None or self.walk or not self.ended_by_itself
                or not self.farmed() or self.stop_event.is_set() or not p.get("farm_medals", False)
                or not p.get("medals_back", False) or self._dead(bot)):
            return
        self.medals_back_done = True
        try:
            medals.put_back(bot)
        except Stopped as e:
            if getattr(e, "final", False):
                raise
            self.log(_("the usual medals weren't put back: {err}", err=e))
        except Exception as e:
            carry_on(e, "putting the usual medals back")
            self.log(_("the usual medals weren't put back: {err}", err=e))

    def _talents_back(self) -> None:
        """Character settings, Talents: the usual talents back on when farming ended by itself in a
        town with a talent master (talents.put_back), after the After farming list. Once a run."""
        bot = self.bot
        p: dict[str, Any] = getattr(bot, "p", None) or {}
        if (getattr(self, "talents_back_done", False) or bot is None or self.walk or not self.ended_by_itself
                or not self.farmed() or self.stop_event.is_set() or not p.get("farm_talents", False)
                or not p.get("talents_back", False) or self._dead(bot)):
            return
        self.talents_back_done = True
        # Best effort: talents left as they were are no reason to lose the end of farming (the
        # after-stop action, the run counted as ended by itself). Only a game gone, or another
        # character in it, is passed on: the end handles those as it always does.
        try:
            talents.put_back(bot)
        except Stopped as e:
            if getattr(e, "final", False):
                raise
            self.log(_("the usual talents weren't put back: {err}", err=e))
        except Respawned:
            self.log(_("the usual talents weren't put back: died on the way"))
        except Exception as e:
            carry_on(e, "putting the usual talents back")
            self.log(_("the usual talents weren't put back: {err}", err=e))
        finally:
            self._off_again(bot)

    def _before_farming(self) -> None:
        """Run the saved preparation sequence once. An ordinary unchecked failure is logged;
        a failed deterministic action, Stop or a lost/frozen game prevents farming."""
        bot = self.bot
        if self.before_farming_done or bot is None:
            return
        self.before_farming_done = True
        triggers.run_stage(bot, "before", self.log, self.stop_event.is_set, self.farming_window)

    def farming_window(self) -> None:
        """Before a trigger step that farms: Stopped when the day's farming is over (the daily
        stop has passed and the drop time hasn't reset yet), else the daily stop armed for the
        step - again after a stop that already fired, since a new game day may have begun."""
        bot = self.bot
        if bot is None:
            return
        day_end, blocked = daily.plan(settings.load_app())
        if blocked:
            raise Stopped(_("the day's farming is over - the drop time resets at {reset}; start again "
                            "after that", reset=blocked))
        if day_end:
            bot.daily_stop_at = day_end.timestamp()
            bot.daily_reset = daily.reset_clock()
            bot.daily_done = False

    def _after_farming(self) -> None:
        """The Character settings' after-farming triggers (engine/triggers.py: Sell shards now,
        Clean the bag now, Update shard prices, Gathering, Store items now), each one ticked, one
        after the other, the same as from the Tools menu. Only after a farming run that ended by
        itself - the plan done, a stop rule, the daily stop - as the after-stop action is: never
        after Stop, a lost or frozen game, `come`/`follow`, a tool or the walking assistant. Before
        the results close (each job's earnings are its own, apart from farming) and before the game
        is closed or the PC shut down. Once a run. Failed deterministic actions stop the remaining
        sequence; ordinary unchecked failures are logged and continue. Stop ends them all."""
        bot = self.bot
        if (self.after_farming_done or bot is None or self.walk or not self.ended_by_itself or not self.farmed()
                or getattr(self, "kind", "") in ("tool", "trigger")):
            return
        self.after_farming_done = True
        if self._dead(bot):
            # a respawn menu that never opened ended the run: no errand walks off with a dead
            # character (seen: the shard sale set off while the player respawned by hand)
            self.log(_("still dead - the after-farming list is left out"), speak=True)
            return
        # the daily stop stays in force: only the waits between steps run past it (run_stage), and
        # a step that farms is held to the day's window (farming_window)
        try:
            triggers.run_stage(bot, "after", self.log, self.stop_event.is_set, self.farming_window)
        finally:
            self._off_again(bot)

    def _off_again(self, bot: Bot) -> None:
        """The run's end got off the beast (and put the pet away when asked) before the list, and
        the list's trips get back on to travel: off again once it is over, so the run still ends
        on foot (seen: both characters left riding in town after a shard sale). Not after Stop."""
        if self.stop_event.is_set():
            return
        from . import beasts, pets
        try:
            beasts.off(bot)
            if bot.p.get("pet_recall_at_end", False):
                pets.recall(bot)
        except Stopped:
            pass                       # the game went away meanwhile: nothing to get off
        except Exception as e:         # a beast left on is no reason to lose the run's end
            carry_on(e, "getting off the beast after the after-farming list")

    @staticmethod
    def _dead(bot: Bot) -> bool:
        try:
            return bot.me().get("hp", 1) <= 0
        except Exception:             # the game can't be read: the errands find that out themselves
            return False

    def _crashed(self, e: Exception) -> str:
        carry_on(e, "the run")
        reason = _("unexpected error: {err}", err=repr(e))
        _write_crash(self.title, self.character)
        if self.bot is not None and not self.stop_event.is_set() and not self.walk:
            self.log(reason, speak=True)
            self.guard(self.bot)      # a bug is no reason to stand still while monsters hit us
        return reason

    # ---------------------------------------------------------------- the run's place
    def keeps_place(self) -> bool:
        """Only a plan or a script is carried on: a tool, a trigger list, an errand or the walking
        assistant is quick to start again by hand."""
        return getattr(self, "kind", "") in ("plan", "script") and not self.walk

    def _take_place(self, script: Runner) -> None:
        """Carrying on an interrupted run (the window set resume_row): its place, when it is the
        same plan or script as then; an edited one starts from its beginning."""
        if not self.keeps_place():
            return
        self.script = script
        row = self.resume_row
        if row is None:
            return
        if row.get("text") != resume.fingerprint(self.text):
            self.resume_row = None
            self.log(_("{what} changed since it was interrupted, so it starts from the beginning", what=self.title))
            return
        script.resume_from(row)
        if self.tally is not None and isinstance(row.get("chain"), str):
            self.tally.chain = row["chain"]      # the results add the parts up at the end
        self.log(_("carrying on the interrupted run of {what}", what=self.title), speak=True)

    def _carried_on_past_before(self) -> bool:
        """The interrupted run had done its Before farming list: not again."""
        row = self.resume_row
        if row is not None and row.get("before_done"):
            self.before_farming_done = True
            return True
        return False

    def _keep_place(self) -> None:
        """Save the run's place now and then, on a thread of its own, until the run ends."""
        if self.script is None:
            return

        def work() -> None:
            while not self.stop_event.wait(resume.SAVE_EVERY) and not self.place_done:
                self.save_place()
        threading.Thread(target=work, daemon=True, name="unclefarmer-place-%d" % self.pid).start()

    def save_place(self) -> None:
        script, key = self.script, self.profile
        if script is None or not key:
            return
        try:
            where = script.position()
            if where is None:
                return
            row = dict(where, item="%s:%s" % (getattr(self, "kind", ""), self.title), title=self.title,
                       text=resume.fingerprint(self.text), before_done=self.before_farming_done,
                       saved=time.time())
            tally = self.tally
            if tally is not None:      # the first part's start names the whole run's parts
                row["chain"] = tally.chain or stats.start_stamp(tally.started)
            with self.place_lock:
                if not self.place_done:
                    resume.save(key, row)
        except Exception as e:      # a place that can't be kept must never stop the run
            carry_on(e, "keeping the run's place")

    def _settle_place(self) -> None:
        """The run is over: its place kept when it didn't end (the game lost, Uncle Farmer closing),
        else taken away - a finished run, Stop, or a failure that carrying on would only repeat."""
        with self.place_lock:
            if self.place_done:
                return
            self.place_done = True
        if self.script is None:
            return
        keep = (self.game_lost or self.app_closing) and not self.ended_by_itself
        try:
            if keep:
                self.log(_("the run's place is kept: it can carry on from there"))
            else:
                resume.clear(self.profile)
        except Exception as e:
            carry_on(e, "settling the run's place")

    # ---------------------------------------------------------------- the end
    def _end(self, app: dict[str, Any], reason: str) -> None:
        """However it ended: the results finished, the game let go, said, saved and reported - and
        then, whatever of that failed, on_done() once, which takes the run off the window's list.
        Results that couldn't be saved (a full disk) once left the window saying "Already running"
        with Start greyed out until the app was restarted. What failed is logged after on_done and
        passed to carry_on (in tests: raised), the first failure first, never one hiding another."""
        failures: list[tuple[str, Exception]] = []
        self._settle_place()
        try:
            self._wrap_up(app, reason, failures)
        except Exception as e:      # nothing on the way out may keep the run on the list
            failures.append(("ending the run", e))
        finally:
            self._done(reason, failures)

    def _done(self, reason: str, failures: list[tuple[str, Exception]]) -> None:
        if self.done_called:
            return
        self.done_called = True
        try:
            self.on_done(reason, self)
        except Exception as e:
            failures.append(("telling the window the run ended", e))
        self.end_failures = list(failures)
        for where, e in failures:
            try:
                self.log(_("couldn't finish the run ({where}): {err}", where=where, err=e))
            except Exception:
                pass                # the log is the window too; the failure still goes to carry_on
        if len(failures) == 1:
            carry_on(failures[0][1], failures[0][0])
        elif failures:
            carry_on(ExceptionGroup("the run's end", [e for _w, e in failures]), "the run's end")

    def _wrap_up(self, app: dict[str, Any], reason: str, failures: list[tuple[str, Exception]]) -> None:
        bot = self.bot
        if self.tally:
            self._last_readings(bot)
            try:
                self.tally.finish(bot)
            except Exception as e:
                carry_on(e, "finishing the results on the way out")
                try:
                    self.tally.finish()
                except Exception as e:
                    carry_on(e, "finishing the results without the bot")
                    self.log(_("couldn't finish the results: {err}", err=e))
        if self.link:
            self._pause_card(bot)
            self._let_go(app, bot, self.link)
        self.log(_("stopped: {why}", why=reason), speak=True)
        tell = getattr(self, "tell", None)
        if tell and not self.stop_event.is_set() and reason != _("arrived"):
            try:
                tell(_("Stopped: {why}", why=reason))      # e.g. lost sight of them
            except Exception as e:
                carry_on(e, "telling the player the run stopped")
        if self.tally:
            try:
                self.tally.save()
            except Exception as e:  # the results are still reported below, from memory
                failures.append(("saving the results", e))
                self.log(_("couldn't save the results: {err}", err=e), speak=True)
            r, *jobs = self.tally.results()
            whole, parts = stats.whole_run(r)       # a run carried on: with its parts before
            title = (_("Results of {what}, its {n} parts added up (it was interrupted and carried on)",
                       what=self.title, n=parts) if parts > 1 else _("Results of {what}", what=self.title))
            for line in stats.report(whole, title).splitlines():
                self.log(line)
            for job in jobs:              # chores or the Gem Maker on the way: apart from farming
                for line in stats.report(job, _("Results of {what}", what=job["run"])).splitlines():
                    self.log(line)
            if app.get("results_speak", "end") == "end":
                self.log(stats.short(whole, self.tally.drop_left), speak=True)

    def _pause_card(self, bot: Bot | None) -> None:
        """A farming run over: its running experience card paused (xp_card_pause), so the minutes
        aren't spent standing in town. Not after Stop: the player is there, and every wait would
        end at once. Best effort - the game may already be gone."""
        if bot is None or self.walk or self.stop_event.is_set() or not getattr(bot, "farmed", False):
            return
        try:
            xpcard.away(bot, _("farming is over"))
        except Exception as e:
            carry_on(e, "pausing the experience card at the end")

    def _last_readings(self, bot: Bot | None) -> None:
        """Before the results close: the kills the game reported since the bot last looked, and a
        fresh character sheet. The sheet is read without waiting while farming (agent 43), so the
        last reading can be a few seconds old; the long-run simulator caught each run's results
        coming out a kill and its contribution short. Only the keyless sheet: the key would open
        a window at the end. Best effort - the game may already be gone."""
        if bot is None or not self.link:
            return
        try:
            if hasattr(bot, "count_kills"):
                bot.count_kills()
            if getattr(bot, "agent_sheet", False) and hasattr(bot, "ask_sheet"):
                bot.ask_sheet(force=True)
        except Exception as e:      # a lost game, a stop: the results keep what they had
            carry_on(e, "the last readings for the results")

    def _let_go(self, app: dict[str, Any], bot: Bot | None, link: AgentLink) -> None:
        """The keys let go, the game's speech back, and the after-stop action, then the link closed."""
        try:
            if bot is not None and hasattr(bot, "let_go"):
                bot.let_go()      # only this run's keys; a `keysup` could drop another controller's
            else:
                link.send("keysup")
            if self.muted and app.get("game_speech") != "always" and self.speech_choice != "off":
                link.send("speech on")
            self.after_stop = app.get("after_stop", "nothing")
            if self.ended_by_itself and self.farmed() and self.after_stop in ("close_game", "shutdown"):
                # the game closes as its own close button does; a shutdown waits for every bot
                self.log(_("farming is over - closing the game"), speak=True)
                link.send("arm")
                link.send("closegame")
        except (AgentError, OSError) as e:
            self.log(_("couldn't finish at the game's end: {err}", err=e))
        link.close()
