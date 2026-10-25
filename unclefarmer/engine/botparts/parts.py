"""The shared surface of the Bot, declared once for the type checker.

The Bot is put together from one mixin per job (engine/botparts) and every part calls the others
through self (see engine/bot.py). Pyright reads one file at a time, so without a declaration here
each mixin looks like a class that calls methods and reads attributes it has never seen.

This class is type-only: it has no __init__, holds no state and changes nothing at run time. The
members below are declared with their real kinds where that is useful (the profile is a dict, the
held keys are strings) and as Any where one name has several shapes across the runs (a fake link in
the tests, the game's own objects in flight)."""

import queue
from typing import Any, Callable

from ...clock import CLOCK, Clock
from ...trust import Trusted


class Parts:
    clock: Clock = CLOCK
    # ---------------------------------------------------------------- connection and settings
    a: Any                      # link.AgentLink, or a stand-in in the tests
    p: dict[str, Any]           # the character's profile
    log: Callable[..., Any]     # the runner's log(line, speak=False, run=None)
    stop_event: Any             # threading.Event, or an Event-like fake
    save_profile: Any
    trusted: Trusted
    requests: queue.Queue[Any]
    timing: dict[str, float] | None
    _phases: list[list[Any]]
    watches: list[tuple[Callable[[], Any], str]]
    character: str | None
    agent_version: Any
    guarding: bool
    locate: Any
    last_seen: Any
    tell: Any
    kind: str
    follows: Any

    # ---------------------------------------------------------------- run state
    started: float
    resumed_at: float
    daily_stop_at: float
    daily_reset: str
    daily_done: bool
    end_place: tuple[Any, ...]
    start_place: tuple[Any, ...] | None
    drop_keep: int | None
    agent_state: Any
    agent_keys: bool
    agent_ask: bool
    agent_sheet: bool
    agent_slots: bool
    agent_act: bool
    paused: Any                 # threading.Event
    held: set[str]
    kills: int
    tally: Any
    kill_tracker: Any
    in_fight: bool
    skip: dict[Any, float]      # monster id -> ignore until
    skip_names: dict[tuple[Any, str], float]
    killed_kinds: set[tuple[Any, str]]
    last_sheet: float
    load_share: float | None
    mp_share: float | None
    _potions_had: dict[str, bool]
    _potions_out: dict[str, float]
    _potions_warned: set[str]
    _potions_warned_out: set[str]
    farm_filter: Any
    walk_only: bool
    skillbook: Any
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
    check: Any
    sleep: Any
    timed: Any
    timing_report: Any
    contribution_now: Any
    ui: Any
    key: Any
    slot_command: Any
    press_slot: Any
    hold: Any
    let_go: Any
    move_key: Any
    hotkey_of: Any
    wait_keys_free: Any
    check_game: Any
    ask_vitals: Any
    snap: Any
    me: Any
    map_id: Any
    settled_map: Any
    walk_to: Any
    travel: Any
    approach: Any
    fight: Any
    fight_back: Any
    hit: Any
    look_after: Any
    select_target: Any
    set_target_mode: Any
    targets: Any
    walk_dist: Any
    found: Any
    def choose(self, pattern: str) -> dict[str, Any] | None: ...
    def dialog(self) -> dict[str, Any]: ...
    def find_entry(self, top: dict[str, Any] | None, pattern: str) -> tuple[int, int] | None: ...
    def note_dialog(self, d: dict[str, Any], why: str) -> None: ...
    close_dialogs: Any
    clear_popups: Any
    close_text_box: Any
    wait_dialog: Any
    talk: Any
    talk_problem: Any
    entry_texts: Any
    use_item: Any
    def refresh_sheet(self, force: bool = False) -> dict[str, Any] | None: ...
    bag_check: Any
    def bag_rows(self) -> list[dict[str, Any]]: ...
    count_kills: Any
    drop_time_check: Any
    def finish(self) -> None: ...
    go_home: Any
    has_hearthstone: Any
    next_target: Any
    rest_up: Any
    run_requests: Any
    spawns_at: Any
    cross_exit: Any
    set_modes: Any
    attackers: Any
    note_pick: Any
    note_search: Any
