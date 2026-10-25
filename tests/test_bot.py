"""The bot itself (engine/bot.py): it is put together from every part, its __init__ wires the link,
profile and stop flag, and it starts every collaborator a run needs."""

import queue
import threading

from conftest import Clock

from unclefarmer.clock import CLOCK
from unclefarmer.engine import bot as botmod
from unclefarmer.engine import repair
from unclefarmer.engine.bot import Bot
from unclefarmer.engine.botparts.bag import BagMixin
from unclefarmer.engine.botparts.dialogs import DialogsMixin
from unclefarmer.engine.botparts.farming import FarmingMixin
from unclefarmer.engine.botparts.fighting import FightingMixin
from unclefarmer.engine.botparts.health import HealthMixin
from unclefarmer.engine.botparts.keys import KeysMixin
from unclefarmer.engine.botparts.reading import ReadingMixin
from unclefarmer.engine.botparts.travel import TravelMixin
from unclefarmer.engine.botparts.walking import WalkingMixin
from unclefarmer.engine.kills import KillTracker

PARTS = (ReadingMixin, KeysMixin, HealthMixin, WalkingMixin, BagMixin, TravelMixin, DialogsMixin,
         FightingMixin, FarmingMixin)


class Link:
    """A link that answers nothing in particular; __init__ only stores it."""

    def __init__(self):
        self.sent = []

    def send(self, line):
        self.sent.append(line)
        return "tapped"

    def json(self, line):
        return {}


def make(link=None, profile=None, log=None, stop_event=None):
    link = link or Link()
    profile = {"name": "Hero"} if profile is None else profile
    lines: list[str] = []
    log = log or (lambda line, speak=False: lines.append(line))
    stop_event = stop_event or threading.Event()
    b = Bot(link, profile, log, stop_event)
    return b, link, profile, log, stop_event


# ------------------------------------------------------------------ assembly
def test_the_bot_is_put_together_from_every_part():
    for part in PARTS:
        assert issubclass(Bot, part)


def test_the_bot_has_the_methods_of_every_part():
    for name in ("check", "sleep", "snap", "me", "map_id", "run_requests", "timing_report", "ui",
                 "key", "hold", "let_go", "move_key", "look_after", "drink", "walk_to", "get_unstuck",
                 "bag_check", "travel", "talk", "fight", "farm"):
        assert callable(getattr(Bot, name))


def test_the_bot_module_exports_what_other_modules_import():
    assert set(botmod.__all__) == {"Bot", "CONFIRM", "DIRECTIONS", "NPC_HP", "Respawned", "Stopped",
                                   "potion_kind", "skill_things", "snap_map"}
    for name in botmod.__all__:
        assert getattr(botmod, name) is not None


# ------------------------------------------------------------------ __init__ wiring
def test_init_keeps_the_link_profile_log_and_stop_flag():
    b, link, profile, log, stop_event = make()
    assert b.a is link
    assert b.p is profile
    assert b.log is log
    assert b.stop_event is stop_event


def test_init_records_the_start_time(monkeypatch):
    clock = Clock(5000.0)
    monkeypatch.setattr(CLOCK, "time", clock.time)
    b, *_ = make()
    assert b.started == 5000.0


def test_init_starts_a_kill_tracker_on_the_link():
    b, link, *_ = make()
    assert isinstance(b.kill_tracker, KillTracker)
    assert b.kill_tracker.a is link
    assert b.kill_tracker.live is None


def test_init_starts_the_counters_at_zero():
    b, *_ = make()
    assert b.kills == 0
    assert b.deaths == 0
    assert b.stuck_in_a_row == 0
    assert (b.last_hp_key, b.last_mp_key, b.last_sheet) == (0.0, 0.0, 0.0)
    assert (b.last_vitals_key, b.last_points_key, b.last_combat) == (0.0, 0.0, 0.0)
    assert b.mp_added == 0.0 and b.mp_seen == 0.0
    assert b.maxmp == 0 and b.mp_peak == 0
    assert b.last_popup_check == 0.0
    assert b.daily_stop_at == 0.0
    assert b.daily_reset == "" and b.daily_done is False


def test_init_starts_the_maps_empty():
    b, *_ = make()
    assert b.mob_names == {}
    assert b.counted == {}
    assert b.skip == {}
    assert b.skip_names == {}
    assert b.killed_kinds == set()
    assert b.beasts_refused == {}
    assert b.pets_refused == {}


def test_init_starts_the_potion_state_clean():
    b, *_ = make()
    assert b._potions_had == {}
    assert b._potions_out == {}
    assert b._potions_warned == set()
    assert b._potions_warned_out == set()
    assert b._potions_stopped is False
    assert b.watches == []


def test_init_starts_the_run_flags():
    b, *_ = make()
    assert b.approaching is False
    assert b.in_fight is False
    assert b.modes_done is False
    assert b.restocked_once is False
    assert b.resting is False
    assert b.walk_only is False
    assert b.doing_request is False
    assert b.daily_done is False
    assert b.last_me is None and b.last_hp is None
    assert b.save_profile is None and b.skillbook is None and b.tally is None
    assert b.start_place is None
    assert b.load_share is None and b.mp_share is None
    assert b.farm_filter is None and b._dists is None
    assert b.timing is None and b._phases == []


def test_init_starts_the_end_and_trust_state():
    b, *_ = make()
    assert b.end_place == ("start",)
    assert not b.trusted


def test_init_starts_a_gear_reader():
    b, *_ = make()
    assert isinstance(b.gear, repair.Gear)


def test_init_starts_the_pause_and_request_machines():
    b, *_ = make()
    assert isinstance(b.paused, threading.Event)
    assert not b.paused.is_set()
    assert isinstance(b.requests, queue.Queue)
    assert b.requests.empty()
    assert b.held == set()


def test_each_bot_gets_its_own_mutable_state():
    b1, *_ = make()
    b2, *_ = make()
    b1.held.add("d")
    b1.mob_names[1] = "Wolf"
    b1.skip[2] = 5
    assert b2.held == set()
    assert b2.mob_names == {}
    assert b2.skip == {}
    assert b1.held is not b2.held
    assert b1.requests is not b2.requests
    assert b1.paused is not b2.paused
