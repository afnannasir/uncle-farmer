"""How the bot follows a player, the player's choices (engine/followopts.py): heal only (a healer that
attacked healed its tank too late in a dungeon), attack only, attacking before anyone is hit, helping
the team's fights or not, the distance kept, monsters never attacked - on the character's Team page,
and for one follow on the script's `follow` line."""

from __future__ import annotations

import re
from typing import Any

import pytest
from conftest import Clock, as_bot, stand_in

from test_follow_extra import HelpBot, mob, other, world
from unclefarmer import settings
from unclefarmer.engine import follow, followopts, script, teamheal


def following(p: dict[str, Any] | None = None, opts: dict[str, Any] | None = None, **kw: Any) -> HelpBot:
    b = HelpBot(**kw)
    b.following = "Friend"
    b.p = dict(p or {})
    b.follow_opts = dict(opts or {})          # type: ignore[attr-defined]
    return b


# ------------------------------------------------------------------ the choices
def test_new_profiles_follow_as_before() -> None:
    d = settings.PROFILE_DEFAULTS
    assert (d["follow_role"], d["follow_pull"], d["follow_help_team"], d["follow_distance"], d["follow_skip"]) == \
        ("both", False, True, 3, "")


def test_the_role_comes_from_the_script_line_then_the_character() -> None:
    assert followopts.role(as_bot(HelpBot())) == "both"                       # not following
    assert followopts.role(as_bot(following({"follow_role": "heal"}))) == "heal"
    assert followopts.role(as_bot(following({"follow_role": "heal"}, {"role": "attack"}))) == "attack"
    assert followopts.role(as_bot(following({"follow_role": "sing"}))) == "both"
    assert followopts.heal_only(as_bot(following({"follow_role": "heal"})))
    assert followopts.attack_only(as_bot(following(opts={"role": "attack"})))


def test_pull_is_off_unless_ticked_and_a_script_may_turn_it_off() -> None:
    assert followopts.pull(as_bot(following())) is None
    assert followopts.pull(as_bot(following({"follow_pull": True, "follow_pull_range": 8}))) == 8
    assert followopts.pull(as_bot(following({"follow_pull": True}, {"pull": 0}))) is None
    assert followopts.pull(as_bot(following(opts={"pull": 99}))) == followopts.PULL_MOST


def test_the_distance_stays_inside_the_heals_reach() -> None:
    assert followopts.distance(as_bot(following()), None) == 3
    assert followopts.distance(as_bot(following({"follow_distance": 10})), None) == 10
    assert followopts.distance(as_bot(following({"follow_distance": 25})), 15.0) == 14       # the heal reaches 15
    assert followopts.distance(as_bot(following({"follow_distance": 25}, {"role": "attack"})), 15.0) == 25
    assert followopts.distance(as_bot(following(opts={"distance": 0})), None) == 1


def test_skip_is_whole_names_whatever_the_case_and_spaces() -> None:
    pattern = followopts.skip(as_bot(following({"follow_skip": "Bone Dragon ,  Lich"})))
    assert pattern is not None
    assert re.search(pattern, "bone   dragon", re.I) and re.search(pattern, "LICH", re.I)
    assert not re.search(pattern, "Lich King", re.I) and not re.search(pattern, "Dragon", re.I)
    assert followopts.skip(as_bot(following())) is None
    assert followopts.names(" a, ,b  c ") == ["a", "b c"]


# ------------------------------------------------------------------ what it fights
def test_a_healer_only_never_picks_a_fight() -> None:
    p = other(x=0.0, y=0.0)
    b = following({"follow_role": "heal"}, targets=[mob(x=1.0, id=-7)], attackers=[-7])
    assert follow.help_target(as_bot(b), world(), p) is None


def test_without_helping_the_team_only_what_attacks_us() -> None:
    p = other(x=0.0, y=0.0)
    wounded = mob(x=1.0, id=-5, hp=5, maxhp=10)
    b = following({"follow_help_team": False}, targets=[wounded])
    assert follow.help_target(as_bot(b), world(), p) is None
    b = following({"follow_help_team": False}, targets=[wounded], attackers=[-5])
    assert follow.help_target(as_bot(b), world(), p) is wounded


def test_pull_attacks_a_fresh_monster_near_the_player() -> None:
    p = other(x=0.0, y=0.0)
    fresh = mob(x=6.0, id=-8, hp=10, maxhp=10)               # untouched: before, it was left alone
    assert follow.help_target(as_bot(following(targets=[fresh])), world(), p) is None
    b = following({"follow_pull": True, "follow_pull_range": 8}, targets=[fresh])
    assert follow.help_target(as_bot(b), world(), p) is fresh
    far = mob(x=12.0, id=-9, hp=10, maxhp=10)
    assert follow.help_target(as_bot(following({"follow_pull": True, "follow_pull_range": 8}, targets=[far])),
                              world(), p) is None


def test_the_never_attack_names_reach_the_target_choice() -> None:
    seen: list[str | None] = []

    class Picky(HelpBot):
        def targets(self, snap: dict[str, Any], skip: str | None = None) -> list[dict[str, Any]]:
            seen.append(skip)
            return []
    b = Picky()
    b.following, b.p = "Friend", {"follow_skip": "Bone Dragon"}
    follow.help_target(as_bot(b), world(), other())
    assert seen and seen[0] is not None and re.search(seen[0], "Bone Dragon", re.I)


def test_an_attacker_only_never_heals() -> None:
    b = following({"follow_role": "attack", "team_heal": True})
    assert teamheal.look_after(as_bot(b), world()) is False


def test_keep_follows_the_heal_reach(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(teamheal, "heal_reach", stand_in(teamheal.heal_reach, lambda b: 12.0))
    assert follow.keep(as_bot(following({"follow_distance": 20}))) == 11
    monkeypatch.setattr(teamheal, "heal_reach", stand_in(teamheal.heal_reach, lambda b: None))
    assert follow.keep(as_bot(following({"follow_distance": 20}))) == 20


def test_follow_holds_the_script_options_for_its_run_only(monkeypatch: pytest.MonkeyPatch) -> None:
    b = following()
    b.lines = []                                              # type: ignore[attr-defined]
    b.log = lambda line, speak=False: b.lines.append(line)    # type: ignore[attr-defined]
    held: list[dict[str, Any]] = []

    def run(bot: Any, name: str) -> None:
        held.append(dict(bot.follow_opts))
    monkeypatch.setattr(follow, "_follow", run)
    follow.follow(as_bot(b), "Friend", {"role": "heal", "distance": 10})
    assert held == [{"role": "heal", "distance": 10}]
    assert b.follow_opts == {} and b.following is None        # type: ignore[attr-defined]
    assert "healing only while following" in b.lines          # type: ignore[attr-defined]


class Walker:
    """What fight_back and the walk's break-off look at, and no more."""

    def __init__(self, role: str, skip: str = "") -> None:
        self.walk_only = False
        self.following = "Friend"
        self.follow_opts: dict[str, Any] = {}
        self.p: dict[str, Any] = {"follow_role": role, "follow_skip": skip}
        self.skips: list[str | None] = []
        self.fought: list[dict[str, Any]] = []
        self.casts_seen = True
        self.modes_done = True
        self.lines: list[str] = []
        self.clock = Clock()

    def fresh_attackers(self) -> set[int]:
        return set()

    def hit(self, me: dict[str, Any]) -> bool:
        return True                       # our HP just dropped

    def attacked_on_the_way(self) -> bool:
        return True

    def snap(self) -> dict[str, Any]:
        return {"self": {"x": 0.0, "y": 0.0}, "entities": []}

    def attackers(self) -> set[int]:
        return {-4} if not self.fought else set()

    def targets(self, snap: dict[str, Any], skip: str | None = None) -> list[dict[str, Any]]:
        self.skips.append(skip)
        return [dict(mob(x=1.0, id=-4), name="Wolf")]

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)

    def fight(self, m: dict[str, Any]) -> None:
        self.fought.append(m)


def test_a_healer_only_neither_fights_back_nor_stops_walking_for_a_hit() -> None:
    from unclefarmer.engine.botparts.fighting import FightingMixin
    from unclefarmer.engine.botparts.walking import WalkingMixin
    healer = Walker("heal")
    FightingMixin.fight_back(as_bot(healer))
    assert healer.fought == [] and healer.skips == []
    assert WalkingMixin._broken_off(as_bot(healer), {"hp": 50}) is False      # pyright: ignore[reportPrivateUsage]
    helper = Walker("both")
    assert WalkingMixin._broken_off(as_bot(helper), {"hp": 50}) is True       # pyright: ignore[reportPrivateUsage]


def test_fighting_back_while_following_leaves_the_named_monsters_alone() -> None:
    from unclefarmer.engine.botparts.fighting import FightingMixin
    b = Walker("both", skip="Bone Dragon")
    FightingMixin.fight_back(as_bot(b))
    assert b.skips and b.skips[0] is not None and re.search(b.skips[0], "Bone Dragon", re.I)
    assert b.fought                                                           # what isn't named: fought


def test_pull_still_works_with_the_teams_fights_left_alone() -> None:
    p = other(x=0.0, y=0.0)
    fresh = mob(x=4.0, id=-8, hp=10, maxhp=10)
    b = following({"follow_pull": True, "follow_pull_range": 8, "follow_help_team": False}, targets=[fresh])
    assert follow.help_target(as_bot(b), world(), p) is fresh


def test_a_long_distance_is_never_undone_by_a_teleport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shift+P lands right beside the player: at a chosen distance of 28, a player 30 tiles off is
    walked up to (to 28), not teleported to."""
    b = following({"follow_distance": 28, "follow_role": "attack"})
    calls: list[str] = []
    monkeypatch.setattr(follow, "help_target", stand_in(follow.help_target, lambda bot, snap, p: None))
    monkeypatch.setattr(teamheal, "heal_reach", stand_in(teamheal.heal_reach, lambda b: None))
    def teleport(bot: Any, name: str, them: dict[str, Any] | None = None, *, verified_leader: bool = False) -> bool:
        calls.append("teleport")
        return True

    def walk_up(bot: Any, name: str, cell: tuple[int, int], near: int = 3) -> str:
        calls.append("walk %d" % near)
        return "near"

    def beside(bot: Any, p: dict[str, Any]) -> tuple[int, int]:
        return (30, 0)
    monkeypatch.setattr(follow, "teleport", teleport)
    monkeypatch.setattr(follow, "_walk_up", walk_up)
    monkeypatch.setattr(follow, "_beside", beside)
    follow._keep_up(as_bot(b), "Friend", world(), {"x": 0.0, "y": 0.0}, other(x=30.0))   # pyright: ignore[reportPrivateUsage]
    assert calls == ["walk 28"]
    calls.clear()
    follow._keep_up(as_bot(b), "Friend", world(), {"x": 0.0, "y": 0.0}, other(x=40.0))   # pyright: ignore[reportPrivateUsage]
    assert calls == ["teleport"]


def test_waiting_for_the_leader_rests_when_an_attacker_cant_be_fought(monkeypatch: pytest.MonkeyPatch) -> None:
    """A monster on the never-attack list (or a boss) hitting us: fight_back leaves it alone, and the
    wait goes on to its teleport and its sleep - it used to read the game again and again at once."""
    class Waiter:
        def __init__(self) -> None:
            self.following, self.p, self.follow_opts = "Friend", {}, {}
            self.fights = 0
            self.lines: list[str] = []
            self.__dict__["_teleport"] = {"off": None, "next": 0.0, "not_leader": set()}

        def check(self) -> None:
            if self.fights > 5:
                raise RuntimeError("the wait never rests")

        def attackers(self) -> set[int]:
            return {-4}

        def fight_back(self) -> None:
            self.fights += 1

        def map_id(self) -> int:
            return 1041

        def snap(self) -> dict[str, Any]:
            return world()

        def log(self, line: str, speak: bool = False) -> None:
            self.lines.append(line)

    w = Waiter()

    def teleport(bot: Any, name: str, them: dict[str, Any] | None = None, *, verified_leader: bool = False) -> bool:
        return True
    monkeypatch.setattr(follow, "teleport", teleport)
    follow._wait_for_leader(as_bot(w), "Friend", "lost sight of Friend")       # pyright: ignore[reportPrivateUsage]
    assert w.fights == 1


def test_healing_only_without_a_team_heal_is_said(monkeypatch: pytest.MonkeyPatch) -> None:
    b = following()
    b.lines = []                                              # type: ignore[attr-defined]
    b.log = lambda line, speak=False: b.lines.append(line)    # type: ignore[attr-defined]
    monkeypatch.setattr(follow, "_follow", stand_in(follow._follow, lambda bot, name: None))   # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(teamheal, "heal_reach", stand_in(teamheal.heal_reach, lambda b: None))
    follow.follow(as_bot(b), "Friend", {"role": "heal"})
    assert any("no team heal on a key" in line for line in b.lines)   # type: ignore[attr-defined]


# ------------------------------------------------------------------ the script line
def parsed(text: str) -> dict[str, Any]:
    nodes = script.parse(text)[0]
    return nodes[0].opts


def test_the_follow_line_takes_its_options() -> None:
    o = parsed('follow Bob role=heal distance=10 pull=8 help=off skip="Bone Dragon, Lich"')
    assert o["player"] == "Bob"
    assert o["follow"] == {"role": "heal", "distance": 10, "pull": 8, "help": False, "skip": "Bone Dragon, Lich"}
    assert parsed("follow Bob pull=off")["follow"] == {"pull": 0}
    assert parsed("follow Bob")["follow"] == {}


@pytest.mark.parametrize("line", ["follow Bob role=dance", "follow Bob distance=0", "follow Bob distance=99",
                                  "follow Bob pull=x", "follow Bob help=maybe", "follow Bob speed=3",
                                  "follow Bob heal"])
def test_a_wrong_follow_option_is_said_when_the_script_is_read(line: str) -> None:
    with pytest.raises(script.ScriptError):
        script.parse(line)
