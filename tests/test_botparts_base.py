"""The shared pieces every bot part uses (engine/botparts/base.py) and the key press/hold helpers
(engine/botparts/keys.py): the evenly-spaced _steps helper, potion_kind's branches, Stopped's
`final` flag, and how key()/hold()/let_go()/move_key() talk to the agent."""

import re
from typing import cast

import pytest

from unclefarmer import settings
from unclefarmer.engine.botparts.base import (CONFIRM, DIRECTIONS, NPC_HP, Respawned, Stopped,
                                              _steps, potion_kind, skill_things, snap_map)
from unclefarmer.engine.botparts.keys import KeysMixin


# ------------------------------------------------------------------ base: _steps
def test_steps_a_span_between_two_points():
    assert _steps(0, 10, 5) == [0, 5, 10]
    assert _steps(0, 10, 4) == [0, 3, 7, 10]
    assert _steps(0, 10, 3) == [0, 2, 5, 8, 10]


def test_steps_both_ends_are_kept():
    got = _steps(3, 17, 6)
    assert got[0] == 3 and got[-1] == 17


def test_steps_at_most_most_apart():
    got = _steps(0, 100, 7)
    assert max(b - a for a, b in zip(got, got[1:])) <= 7


def test_steps_of_a_single_point_is_that_point():
    assert _steps(5, 5, 10) == [5]
    assert _steps(1, 2, 10) == [1, 2]


def test_steps_when_hi_is_below_lo_is_the_midpoint():
    assert _steps(9, 3, 4) == [6]
    assert _steps(-1, -9, 2) == [-5]


# ------------------------------------------------------------------ base: potion_kind
@pytest.mark.parametrize("name,kind", [
    ("Large Red Potion", "hp"),
    ("Small Red Potion", "hp"),
    ("Large Blue Potion", "mp"),
    ("Large Magic Potion", "mp"),
    ("Pet Large Red Potion", "pet_hp"),
    ("Pet Large Blue Potion", "pet_mp"),
    ("PET SMALL MAGIC POTION", "pet_mp"),
])
def test_potion_kind_reads_the_kind(name, kind):
    assert potion_kind(name) == kind


@pytest.mark.parametrize("name", [None, "", "Sword", "Red Herb", "Mystery Potion", "Red Wine"])
def test_potion_kind_none_when_it_is_not_a_potion_of_ours(name):
    assert potion_kind(name) is None


def test_potion_kind_is_case_insensitive():
    assert potion_kind("large red potion") == "hp"
    assert potion_kind("Large Magic Potion") == "mp"


# ------------------------------------------------------------------ base: Stopped / Respawned
def test_stopped_is_an_exception_by_default_not_final():
    e = Stopped("stopped by you")
    assert isinstance(e, Exception)
    assert str(e) == "stopped by you"
    assert e.final is False


def test_stopped_can_be_final():
    e = Stopped("keys refused", final=True)
    assert e.final is True


def test_respawned_is_an_exception():
    assert issubclass(Respawned, Exception)


# ------------------------------------------------------------------ base: snap_map / constants
def test_snap_map_reads_the_map_dict_id():
    assert snap_map({"map": {"id": 5121}}) == 5121


def test_snap_map_accepts_a_bare_number():
    assert snap_map({"map": 5302}) == 5302
    assert snap_map({}) is None


def test_the_game_text_constants():
    assert NPC_HP == 20000000
    assert DIRECTIONS == {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}


@pytest.mark.parametrize("text", ["ok", "ok then", "yes", "yes please", "  confirm", "sure!", "确定"])
def test_confirm_accepts_an_agreement(text):
    assert re.match(CONFIRM, text)


@pytest.mark.parametrize("text", ["Yes", "OK", "Confirm", "  Sure"])
def test_confirm_takes_a_capitalised_agreement(text):
    # The game capitalises its buttons ("Yes", "OK"): case must not decide whether we answer.
    assert re.match(CONFIRM, text)


@pytest.mark.parametrize("text", ["no", "nope", "", "okay no"])
def test_confirm_refuses_anything_else(text):
    assert not re.match(CONFIRM, text)


# ------------------------------------------------------------------ base: skill_things
def test_skill_things_are_lower_case_names_and_cached():
    things = skill_things()
    assert things
    assert all(name and name == name.lower() for name in things)
    assert skill_things() is things


# ------------------------------------------------------------------ keys
class Link:
    def __init__(self, replies=None):
        self.sent = []
        self.replies = replies or {}

    def send(self, line):
        self.sent.append(line)
        if line in self.replies:
            return self.replies[line]
        if line.startswith("keydown "):
            return "down " + line.split()[1]
        return "tapped"


class Keys(KeysMixin):
    """Only the key helpers, with check()/wait_keys_free() recorded instead of talking to a game."""

    @property
    def link(self) -> Link:
        return cast(Link, self.a)

    def __init__(self, replies=None, profile=None):
        self.a = Link(replies)
        self.p = dict(settings.PROFILE_DEFAULTS)
        self.p.update(profile or {})
        self.held = set()
        self.checked = 0
        self.waited = 0

    def check(self):
        self.checked += 1

    def wait_keys_free(self):
        self.waited += 1


def test_key_presses_through_the_agent():
    b = Keys()
    b.key("w")
    assert b.link.sent == ["key w 60"]
    assert (b.checked, b.waited) == (1, 1)


def test_key_takes_a_hold_time():
    b = Keys()
    b.key("5", 120)
    assert b.link.sent == ["key 5 120"]


def test_key_refused_stops_the_run_for_good():
    b = Keys({"key w 60": "busy: noworld"})
    with pytest.raises(Stopped) as caught:
        b.key("w")
    assert caught.value.final is True
    assert "key w didn't work: busy: noworld" in str(caught.value)


def test_hold_presses_down_and_remembers_the_key():
    b = Keys()
    b.hold("d")
    assert b.link.sent == ["keydown d"]
    assert b.held == {"d"}
    assert b.waited == 1


def test_hold_refused_stops_the_run_and_holds_nothing():
    b = Keys({"keydown d": "no"})
    with pytest.raises(Stopped) as caught:
        b.hold("d")
    assert caught.value.final is True
    assert "key d didn't work: no" in str(caught.value)
    assert b.held == set()


def test_let_go_one_key():
    b = Keys()
    b.held = {"d", "s"}
    b.let_go("d")
    assert b.link.sent == ["keyup d"]
    assert b.held == {"s"}


def test_let_go_everything():
    b = Keys()
    b.held = {"d", "s"}
    b.let_go()
    assert b.link.sent == ["keyup d", "keyup s"]
    assert b.held == set()


def test_let_go_everything_on_a_new_agent_uses_the_connection_keysup():
    b = Keys()
    b.agent_keys = True
    b.held = {"d", "s"}
    b.let_go()
    assert b.link.sent == ["keysup"]
    assert b.held == set()


def test_move_key_reads_the_profile():
    b = Keys()
    assert [b.move_key(d) for d in ("up", "down", "left", "right")] == ["w", "s", "a", "d"]
    b2 = Keys(profile={"move_keys": {"up": "i", "down": "k", "left": "j", "right": "l"}})
    assert b2.move_key("up") == "i"
