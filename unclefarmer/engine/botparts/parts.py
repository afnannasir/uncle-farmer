"""The shared surface of the Bot, declared once for the type checker.

The Bot is put together from one mixin per job (engine/botparts) and every part calls the others
through self (see engine/bot.py). Pyright reads one file at a time, so without a declaration here
each mixin looks like a class that calls methods and reads attributes it has never seen.

This class is type-only: it has no __init__, holds no state and changes nothing at run time (the
peer mixins' methods are declared under TYPE_CHECKING). The game's own readings are JSON and stay
dict[str, Any]: their shape is the agent's, one per command."""

from __future__ import annotations

import queue
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Protocol, cast

from ...clock import CLOCK, Clock, Flag, StopFlag
from ...link import GameLink
from ...trust import Trusted

if TYPE_CHECKING:
    from ...stats import Tally
    from ..bot import Bot
    from ..combat import SkillBook
    from ..kills import KillTracker


Where = tuple[int, float, float]                 # (map, x, y)
Sighting = tuple[int, float, float, float]       # (map, x, y, seconds ago)
Rect = tuple[float, float, float, float]         # x1, y1, x2, y2
# where the run ends (script end_at, plan): (how,) or ("place", map, [x, y] or None)
EndPlace = tuple[str] | tuple[str, int, list[float] | None]
# farming's target filter: (area, only, skip, avoid rects)
FarmFilter = tuple[Sequence[float] | None, str, str, list[Rect]]


class Log(Protocol):
    """The runner's log: a line for the window and the day's log, spoken when speak."""

    def __call__(self, line: str, /, speak: bool = False) -> None: ...


def as_bot(part: "Parts") -> "Bot":
    """A part handed to the engine (which takes the whole Bot): a part is only ever part of the Bot
    (engine/bot.py puts them all together), so this is the Bot itself, seen whole. The one cast
    the mixin design needs, in one place."""
    return cast("Bot", part)


class Parts:
    clock: Clock = CLOCK
    # ---------------------------------------------------------------- connection and settings
    a: GameLink                 # link.AgentLink, or a stand-in in the tests
    p: dict[str, Any]           # the character's profile
    stop_event: StopFlag
    save_profile: Callable[[dict[str, Any]], None] | None   # set by the runner: keeps what the bot learns
    trusted: Trusted
    requests: "queue.Queue[Callable[[Bot], object]]"   # chores asked for by private message
    timing: dict[str, float] | None
    _phases: list[list[float | str]]
    watches: list[tuple[Callable[[], object], str]]   # (condition, message) checked all the time
    character: str | None
    agent_version: int
    guarding: bool
    locate: Callable[[str], Where | None] | None       # where one of our games plays a player
    last_seen: Callable[[str], Sighting | None] | None  # where a player was last seen, and how long ago
    tell: Callable[[str], None] | None             # answers the player who asked for this run
    kind: str

    # ---------------------------------------------------------------- state the engine modules keep
    # on the bot for a run (pets, team heals, repairs, follow, gathering...): set where they are used,
    # usually read with a getattr default, so a run that never got there has none of them
    keys_to_fill: bool          # a hotkey went empty: keyslots.refill puts the bag's potion on it
    repairing: bool             # a repair is under way (engine/repair.py)
    agent_misses: dict[str, int]          # agent command -> wrong answers in a row (agent_strike)
    failure_notes: set[tuple[str, str]]   # (where, error kind) of ignored failures already logged
    follow_buffs: bool          # following, and the leader's `buff` message turned buffs on
    gather_casts: int           # gathering casts seen this run
    last_beast_change: float    # when the bot last changed or got off its beast
    last_ride_check: float
    phrases: dict[int, str]     # the game's quick phrases by slot (engine/runner.py)
    said_low_power: bool
    said_no_vitality: bool
    share_tried: int            # the half hour of the last secret room share try (engine/chores.py)
    trip_checked: float
    traveling: bool
    # the pet (engine/pets.py) and the fusion beasts (engine/beasts.py)
    pets_refused: dict[int, float]        # pet id -> don't try to summon it before then
    beasts_refused: dict[int, float]      # beast id -> don't try it again before then
    run_pets: list[str]         # with no pet ticked, the one a dungeon run looks after
    pets_for_run: list[str] | None   # Tools, Dungeons' pet for its run ([]: none), over the ticked ones
    pet_out: dict[str, Any] | None
    pet_checked: float
    pet_described: dict[int, float]
    pet_said_waiting: bool
    pet_fed_at: float
    pet_potion_at: dict[str, float]
    pet_vitals_at: float
    pet_points_at: float
    pet_mp_peak: dict[int | None, int]
    pet_healed_at: float
    pet_heal_try: tuple[float, int, bool] | None
    pet_heal_misses: int
    pet_heal_rest_until: float
    told_pet_heal_rest: bool
    told_no_pet_food: bool
    told_pet_heal: bool
    told_pet_seen: int | None
    told_pet_unseen: bool
    # teammates (engine/teamheal.py) and the skill keys (engine/keyslots.py)
    team_names: set[str]
    team_learn_at: float
    team_healed_at: float
    team_heal_try: tuple[float, int, int, bool] | None
    team_heal_misses: int
    team_heal_rest_until: float
    told_team_heal_rest: bool
    told_no_skill_room: bool

    # ---------------------------------------------------------------- run state
    following: str | None       # the player a follow run stays with (engine/follow.py)
    team_ask_at: float          # when follow next asks the team phrase where its player is
    supply_told: set[str]       # supplies already said to be low (town.restock): said once a run
    started: float
    resumed_at: float
    daily_stop_at: float
    daily_reset: str
    daily_done: bool
    end_place: EndPlace
    in_instance: bool
    pack_min: int
    start_place: Where | None
    drop_keep: int | None
    agent_state: bool
    agent_keys: bool
    agent_ask: bool
    agent_sheet: bool
    agent_slots: bool
    agent_act: bool
    paused: Flag
    resting: bool               # standing still to get HP back (health.py); skills hold their heals
    held: set[str]
    kills: int
    tally: "Tally | None"
    kill_tracker: "KillTracker"
    in_fight: bool
    skip: dict[int, float]      # monster id -> ignore until
    skip_names: dict[tuple[int, str], float]
    killed_kinds: set[tuple[int, str]]
    last_sheet: float
    load_share: float | None
    mp_share: float | None
    _potions_had: dict[str, bool]
    _potions_out: dict[str, float]
    _potions_warned: set[str]
    _potions_warned_out: set[str]
    farm_filter: FarmFilter | None
    walk_only: bool
    skillbook: "SkillBook | None"
    mob_names: dict[int, str]
    counted: dict[int, float]
    last_hp: float | None
    last_combat: float
    _speech: str
    _speech_at: float
    last_points_key: float
    casts_seen: bool
    _attackers: set[int]
    _attackers_at: float
    hp_log: dict[int, list[tuple[float, float]]]
    follow_stalls: int
    follow_ok: bool
    approaching: bool
    modes_done: bool
    walk_steps: dict[int, float]            # targets()' walking distances, for the field log
    picked: dict[str, Any] | None           # the farm loop's pick, waiting for its fight
    field_record: dict[str, Any] | None     # the field-log record of the fight under way

    # ---------------------------------------------------------------- methods that live in a peer mixin
    # Declared for the checker only: at run time each is the mixin's own (see the module notes).
    if TYPE_CHECKING:
        # bot.py: the runner's log, set per Bot
        def log(self, line: str, /, speak: bool = False) -> None: ...
        # reading.py
        def check(self) -> None: ...
        def sleep(self, seconds: float) -> None: ...
        def timed(self, phase: str) -> AbstractContextManager[None]: ...
        def timing_report(self, final: bool = False) -> None: ...
        def contribution_now(self) -> int: ...
        def ui(self) -> dict[str, Any]: ...
        def wait_keys_free(self) -> None: ...
        def check_game(self, force: bool = False) -> None: ...
        def snap(self) -> dict[str, Any]: ...
        def me(self) -> dict[str, Any]: ...
        def map_id(self) -> int: ...
        def settled_map(self, seconds: float = 15) -> int: ...
        def close_text_box(self) -> bool: ...
        def run_requests(self) -> None: ...
        # keys.py
        def key(self, k: str, ms: int = 60) -> None: ...
        def slot_command(self, k: str) -> str | None: ...
        def press_slot(self, k: str) -> bool: ...
        def note_page_turn(self) -> None: ...
        def hold(self, k: str) -> None: ...
        def let_go(self, k: str | None = None) -> None: ...
        def move_key(self, direction: str) -> str: ...
        # health.py
        def hotkey_of(self, name: str) -> str | None: ...
        def ask_vitals(self, what: str) -> bool | None: ...
        def look_after(self, me: dict[str, Any] | None = None) -> dict[str, Any]: ...
        def rest_up(self, me: dict[str, Any]) -> bool: ...
        # walking.py
        def walk_to(self, cell: tuple[float, float], map_change: bool = False,
                    near: Callable[[dict[str, Any]], bool] | None = None) -> str: ...
        def walk_dist(self, snap: dict[str, Any], e: dict[str, Any], limit: float | None = None) -> float: ...
        # travel.py
        def travel(self, dst: int | str, cell: tuple[float, float] | None = None) -> None: ...
        def go_home(self, tries: int = 3) -> bool: ...
        def has_hearthstone(self) -> bool: ...
        @staticmethod
        def spawns_at(mid: int, cell: tuple[float, float]) -> bool: ...
        def cross_exit(self, leg: dict[str, Any]) -> None: ...
        # fighting.py
        def fight(self, mob: dict[str, Any]) -> None: ...
        def fight_back(self) -> None: ...
        def hit(self, me: dict[str, Any]) -> bool: ...
        def select_target(self, mob: dict[str, Any], by_keys: bool = False) -> bool: ...
        def set_target_mode(self, want: int) -> bool: ...
        def targets(self, snap: dict[str, Any], area: Sequence[float] | None = None, only: str | None = None,
                    skip: str | None = None, avoid_rects: Sequence[Rect] = (),
                    limit: float | None = None) -> list[dict[str, Any]]: ...
        def count_kills(self, snap: dict[str, Any] | None = None) -> list[int]: ...
        def next_target(self, snap: dict[str, Any], mobs: list[dict[str, Any]]) -> dict[str, Any]: ...
        def attackers(self) -> set[int]: ...
        def note_pick(self, snap: dict[str, Any], mob: dict[str, Any], mobs: list[dict[str, Any]]) -> None: ...
        def note_search(self, map_id: int, me: dict[str, Any], cell: tuple[float, float], start: float,
                        result: str) -> None: ...
        def follows(self) -> bool: ...
        # dialogs.py
        def close_dialogs(self) -> None: ...
        def clear_popups(self, force: bool = False) -> bool: ...
        def wait_dialog(self, seconds: float = 3.0, want_open: bool = True) -> dict[str, Any] | None: ...
        def approach(self, name: str, at: tuple[float, float] | None = None,
                     stand: tuple[float, float] | None = None) -> dict[str, Any] | None: ...
        def talk(self, name: str, at: tuple[float, float] | None = None,
                 stand: tuple[float, float] | None = None) -> bool: ...
        @staticmethod
        def entry_texts(e: dict[str, Any]) -> list[str]: ...
        # bag.py
        def use_item(self, name: str) -> bool: ...
        def bag_check(self) -> bool: ...
        # farming.py
        def drop_time_check(self, sheet: dict[str, Any]) -> None: ...
        def set_modes(self) -> None: ...

    def choose(self, pattern: str) -> dict[str, Any] | None: ...
    def dialog(self) -> dict[str, Any]: ...
    def find_entry(self, top: dict[str, Any] | None, pattern: str) -> tuple[int, int] | None: ...
    def note_dialog(self, d: dict[str, Any], why: str) -> None: ...
    def refresh_sheet(self, force: bool = False) -> dict[str, Any] | None: ...
    def bag_rows(self) -> list[dict[str, Any]]: ...
    def finish(self) -> None: ...
