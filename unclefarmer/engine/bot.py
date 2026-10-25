"""The bot: reads the game, walks, travels, fights and heals.

Everything it does to the game is a key press through the agent (the game's own key handler);
everything it knows comes from the game's own objects (the agent's `mem`). Every loop checks the
stop flag, so Stop takes effect within a fraction of a second, and every held key is released.

The Bot is put together from one mixin per job (engine/botparts): reading the game, keys,
health and potions, walking, the bag, travel, NPC dialogs, fighting and farming. Each part
calls the others through self.
"""

import queue
import threading

from .. import trust
from ..clock import CLOCK, Clock
from . import repair
from .kills import KillTracker
from .botparts.base import (CONFIRM, DIRECTIONS, NPC_HP, Respawned, Stopped, potion_kind, skill_things,
                            snap_map)
from .botparts.reading import ReadingMixin
from .botparts.keys import KeysMixin
from .botparts.health import HealthMixin
from .botparts.walking import WalkingMixin
from .botparts.bag import BagMixin
from .botparts.travel import TravelMixin
from .botparts.dialogs import DialogsMixin
from .botparts.fighting import FightingMixin
from .botparts.farming import FarmingMixin

__all__ = ["Bot", "CONFIRM", "DIRECTIONS", "NPC_HP", "Respawned", "Stopped", "potion_kind", "skill_things",
           "snap_map"]


class Bot(ReadingMixin, KeysMixin, HealthMixin, WalkingMixin, BagMixin, TravelMixin, DialogsMixin,
          FightingMixin, FarmingMixin):
    """One character's bot: everything it does to the game goes through link (the agent)."""

    def __init__(self, link, profile, log, stop_event, clock: Clock | None = None):
        self.clock = clock or CLOCK
        self.a = link
        self.p = profile
        self.log = log
        self.stop_event = stop_event
        self.started = self.clock.time()
        self.kills = 0
        self.kill_tracker = KillTracker(link)   # kills from the game's own reports (agent v30)
        self.mob_names = {}           # monster id -> name, for the log line when it dies
        self.counted = {}             # monster id -> when it was counted as our kill
        self.last_hp_key = 0.0
        self.last_mp_key = 0.0
        self.mp_added = 0.0
        self.mp_share = None      # MP percent: the last sheet reading plus potions drunk since
        self.last_sheet = 0.0
        self.low_since = None
        self.stuck_in_a_row = 0
        self.skip = {}            # monster id -> ignore until
        self.skip_names = {}      # (map id, name) -> ignore until: things that can't be attacked
        self.killed_kinds = set() # (map id, name) we have killed: attackable, whatever one of them does
        self.approaching = False  # walking up to a fight's target
        self.walk_steps = {}      # targets()' walking distances, for the field log
        self.picked = None        # the farm loop's pick, written to the field log after its fight
        self.field_record = None
        self._potions_had = {}    # kind -> the bag held some this run
        self._potions_out = {}    # kind -> when the bag first read none
        self._potions_warned = set()
        self._potions_warned_out = set()
        self._potions_stopped = False
        self.watches = []         # (condition callable, message) checked all the time
        self.last_me = None
        self.in_fight = False     # fighting now: walking inside a fight must not trigger fight-back
        self.last_hp = None       # HP at the previous step of a walk, to notice being hit
        self.modes_done = False   # set_modes() has run at least once this run
        self.save_profile = None  # set by the runner: saves what the bot learns (hearthstone home)
        self.restocked_once = False
        self.beasts_refused = {}      # fusion beast id -> don't try again before (time)
        self.last_combat = 0.0        # end of the last fight (the server keeps us "in combat" a while)
        self.pets_refused = {}        # pet id -> don't try to summon before (time)
        self.deaths = 0
        self.skillbook = None         # combat.SkillBook, read from the game at the first fight
        self.last_vitals_key = 0.0    # the own-HP key (R): HP and MP from the server, no window
        self.last_points_key = 0.0    # the accumulation-points key (T): rage, marks, combos
        self.mp_seen = 0.0            # when the MP reading in use arrived
        self.maxmp = 0                # max MP, from the last character sheet seen
        self.mp_peak = 0              # highest MP read this run: the maximum until a sheet is seen
        self.load_share = None        # bag load percent at the last sheet reading
        self.last_popup_check = 0.0
        self.tally = None             # stats.Tally: what this run earned, when the runner keeps one
        self.paused = threading.Event()   # the Pause button or a trusted player's "pause": hold still until cleared
        self.requests = queue.Queue()     # things asked for by private message, done at a safe moment
        self.held = set()                 # keys held down right now
        self.doing_request = False
        self.trusted = trust.Trusted()    # names or ids whose team invites are accepted
        self.gear = repair.Gear()         # wear warnings and repairs
        self.resting = False              # standing still to get HP back: a heal skill tops up to rest_until
        self.start_place = None           # (map, x, y) where the run started
        self.drop_keep = None             # minutes of drop time this run saves (keep_drop), else the profile's
        self.daily_stop_at = 0.0          # epoch seconds: the run ends then, whatever it is doing
        self.daily_reset = ""             # the game's day reset, "HH:MM" on this computer's clock
        self.daily_done = False
        self.end_place = ("start",)       # where any end of the run goes: script end_at / the plan
        self.walk_only = False            # the walking assistant (go): Swift beast all the way, no fights
        self._dists = None                # (map, cell, model, steps from that cell): walk_dist's search
        self.farm_filter = None           # (area, only, skip, avoid) while farming: what counts as a target
        self.timing = None                # phase -> seconds this hour (farm sets it up; see timed())
        self._phases = []                 # [phase, started, seconds of inner phases] of the phases running
