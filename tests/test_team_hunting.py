"""Team hunting across independent bot connections to the same test team."""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from conftest import as_bot
from test_team_offline import TeamBot, TeamGame
from unclefarmer.engine import teamhunt


class Game:
    def __init__(self) -> None:
        self.now = 0.0
        self.leader = "Hero"
        self.team = "Harvest team"
        self.players = {"Hero": 10, "Bob": 20, "Carol": 30}
        self.chat: list[dict[str, Any]] = []
        self.sent: list[tuple[str, str]] = []
        self.transfers: list[tuple[str, str]] = []
        self.direct_transfers: list[tuple[str, str]] = []
        self.direct_ready = True
        self.teleports: list[tuple[str, str]] = []
        self.transfer_ok = True
        self.defer_transfer = False

    def time(self) -> float:
        return self.now

    def line(self, text: str, name: str = "", channel: int = 12) -> None:
        self.chat.append({"n": len(self.chat) + 1, "text": text, "name": name, "channel": channel,
                          "at": self.now})

    def changed(self, name: str) -> None:
        self.leader = name
        self.line(f"{name} has become the team leader", channel=46)


class Agent:
    def __init__(self, game: Game, who: str) -> None:
        self.game = game
        self.who = who
        self.reads = 0
        self.reply: dict[str, Any] | None = None

    def json(self, command: str) -> dict[str, Any]:
        verb, _, cursor = command.partition(" ")
        if verb == "team":
            now = int(self.game.now * 1000)
            return {"now": now, "members": [dict(self.reply, age_ms=now - self.reply["at"])] if self.reply else []}
        assert verb == "chat"
        self.reads += 1
        game = self.game
        return {"last": len(game.chat), "chat": [dict(line, age_ms=int((game.now - line["at"]) * 1000))
                                                for line in game.chat if line["n"] > int(cursor)]}

    def send(self, command: str) -> str:
        if command.startswith("teamtransfer "):
            game = self.game
            if not game.direct_ready:
                return "refused: no verified roster"
            candidate = int(command.split()[1])
            target = next(name for name, pid in game.players.items() if pid == candidate)
            assert game.leader == self.who
            game.direct_transfers.append((self.who, target))
            if not game.defer_transfer:
                game.changed(target)
            return "sent"
        prefix = "say 4 "
        assert command.startswith(prefix)
        text = command[len(prefix):]
        self.game.sent.append((self.who, text))
        self.game.line(text, self.who)
        return "sent"


class Farmer:
    def __init__(self, game: Game, name: str, **options: Any) -> None:
        self.game = game
        self.clock = game
        self.a = Agent(game, name)
        self.name = name
        self.pid = game.players[name]
        self.agent_version = 78
        self.in_fight = False
        self.in_instance = False
        self.active_attackers: set[int] = set()
        self.p = {"team_hunt": False, "team_hunt_subscribe": False, **options}
        self.kill_tracker = SimpleNamespace(died=[], hit={}, pet_struck={})
        self.lines: list[str] = []
        self.mobs: list[dict[str, Any]] = []

    def me(self) -> dict[str, Any]:
        return {"name": self.name, "id": self.pid, "x": 0, "y": 0}

    def map_id(self) -> int:
        return 5322

    def snap(self) -> dict[str, Any]:
        return {"self": self.me(), "entities": self.mobs}

    def key(self, key: str) -> None:
        assert key == "shift+'"
        leader = self.game.leader
        self.game.now += 0.05
        if leader != self.name:
            self.a.reply = {"name": leader, "id": self.game.players[leader], "at": int(self.game.now * 1000)}

    def sleep(self, seconds: float) -> None:
        self.game.now += seconds

    def attackers(self) -> set[int]:
        return {99} if self.in_fight else self.active_attackers

    def fresh_attackers(self) -> set[int]:
        return self.attackers()

    def log(self, message: str, **_kwargs: Any) -> None:
        self.lines.append(message)

    def tick(self, ids: Sequence[int] = ()) -> bool:
        self.game.now += 1.1
        self.mobs = [{"id": mid, "kind": "monster", "hp": 10} for mid in ids]
        return teamhunt.tick(as_bot(self), self.snap(), self.mobs)


PACK = (101, 102, 103)
MESSAGE = "Sweep the cave here"


@pytest.fixture
def crew(monkeypatch: pytest.MonkeyPatch) -> tuple[Game, Farmer, Farmer, Farmer]:
    game = Game()
    leader = Farmer(game, "Hero", team_hunt=True, team_hunt_threshold=2, team_hunt_message=MESSAGE,
                    team_hunt_return=False)
    bob = Farmer(game, "Bob", team_hunt_subscribe=True)
    carol = Farmer(game, "Carol", team_hunt_subscribe=True)

    def roster(bot: Farmer) -> teamhunt.Roster | None:
        if bot.name not in game.players:
            return None
        return teamhunt.Roster(game.team, game.leader, dict(game.players))

    def transfer(bot: Farmer, name: str, *, member_id: int) -> tuple[bool, str]:
        assert game.leader == bot.name
        assert game.players[name] == member_id
        game.transfers.append((bot.name, name))
        if game.transfer_ok and not game.defer_transfer:
            game.changed(name)
        return game.transfer_ok, "transfer result"

    def teleport(bot: Farmer, name: str, *_args: Any, **_kwargs: Any) -> bool:
        assert game.leader == name and bot.name != name
        game.teleports.append((bot.name, name))
        return True

    monkeypatch.setattr(teamhunt, "read_roster", roster)
    monkeypatch.setattr(teamhunt.team, "make_leader", transfer)
    monkeypatch.setattr(teamhunt.follow, "teleport", teleport)
    return game, leader, bob, carol


def start(*farmers: Farmer) -> None:
    for farmer in farmers:
        farmer.tick()


def encounter(leader: Farmer, finder: Farmer, follower: Farmer) -> None:
    finder.tick(PACK)
    leader.tick()
    finder.tick(PACK)
    follower.tick()


def test_patrol_can_queue_and_wake_for_a_leaders_report(
        crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, _carol = crew
    start(leader, bob)
    game.line(MESSAGE, "Hero")
    game.now += 1.1
    teamhunt.observe(as_bot(bob), bob.me())
    assert teamhunt.interrupt_patrol(as_bot(bob), bob.snap())
    assert not game.teleports                  # the patrol returns before the action runs
    assert teamhunt.tick(as_bot(bob), bob.snap(), [])
    assert game.teleports == [("Bob", "Hero")]


def test_direct_handoff_during_the_leaders_fight_does_not_move_that_leader(
        crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, carol = crew
    start(leader, bob, carol)
    leader.agent_version = 79
    leader.tick(PACK)
    leader.in_fight = True
    game.line(MESSAGE, "Bob")
    game.now += 1.1
    teamhunt.observe(as_bot(leader), leader.me())
    assert game.direct_transfers == [("Hero", "Bob")]
    assert not game.transfers and leader.in_fight
    assert carol.tick() and game.teleports == [("Carol", "Bob")]
    teamhunt.observe(as_bot(leader), leader.me())
    assert game.teleports == [("Carol", "Bob")]


def test_a_busy_leader_does_not_open_the_menu_when_direct_transfer_is_unavailable(
        crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, _carol = crew
    start(leader, bob)
    leader.agent_version = 79
    game.direct_ready = False
    leader.tick(PACK)
    leader.in_fight = True
    game.line(MESSAGE, "Bob")
    game.now += 1.1
    teamhunt.observe(as_bot(leader), leader.me())
    assert not game.transfers and not game.direct_transfers
    leader.in_fight = False
    assert leader.tick(PACK) and game.transfers == [("Hero", "Bob")]


def test_unconfirmed_direct_handoff_falls_back_after_verifying_authority(
        crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, _carol = crew
    start(leader, bob)
    leader.agent_version = 79
    leader.in_fight = True
    game.defer_transfer = True
    game.line(MESSAGE, "Bob")
    game.now += 1.1
    teamhunt.observe(as_bot(leader), leader.me())
    leader.in_fight = False
    leader.tick()
    assert game.direct_transfers == [("Hero", "Bob")] and not game.transfers
    game.now += teamhunt.HANDOFF_FOR
    game.defer_transfer = False
    assert leader.tick()
    assert game.transfers == [("Hero", "Bob")] and game.leader == "Bob"
    assert game.direct_transfers == [("Hero", "Bob")]


def test_disabled_and_unsubscribed_make_no_actions(crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, _carol = crew
    leader.p["team_hunt"] = False
    bob.p["team_hunt_subscribe"] = False
    game.changed("Carol")
    assert not leader.tick(PACK) and not bob.tick(PACK)
    assert not game.sent and not game.transfers and not game.teleports
    assert leader.a.reads == bob.a.reads == 0


def test_first_encounter_and_strict_configured_threshold(crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, carol = crew
    start(leader, bob, carol)
    assert any("Subscribe to Team Hunting" in text for who, text in game.sent if who == leader.name)
    bob.tick(PACK[:2])
    leader.tick()
    assert not game.transfers and not game.teleports
    encounter(leader, bob, carol)
    assert ("Bob", MESSAGE) in game.sent
    assert game.transfers == [("Hero", "Bob")]
    assert game.teleports == [("Carol", "Bob")]
    leader.tick()
    assert game.teleports == [("Carol", "Bob"), ("Hero", "Bob")]


@pytest.mark.parametrize("return_leader", [False, True])
def test_both_rotation_modes(crew: tuple[Game, Farmer, Farmer, Farmer], return_leader: bool) -> None:
    game, original, bob, carol = crew
    original.p["team_hunt_return"] = return_leader
    start(original, bob, carol)
    encounter(original, bob, carol)
    bob.tick()
    bob.tick()
    original.tick()
    carol.tick()
    carol.tick()
    assert ("Carol", "Hero") not in game.teleports
    next_leader = original if return_leader else bob
    carol.tick((201, 202, 203))
    next_leader.tick()
    assert game.transfers == ([("Hero", "Bob"), ("Bob", "Hero"), ("Hero", "Carol")]
                              if return_leader else [("Hero", "Bob"), ("Bob", "Carol")])
    assert game.leader == "Carol"


def test_late_and_duplicate_game_messages_require_verified_leader(crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, carol = crew
    start(leader, bob, carol)
    game.defer_transfer = True
    bob.tick(PACK)
    leader.tick()
    assert game.transfers == [("Hero", "Bob")]
    game.line("Bob has become the team leader", name="Bob")
    carol.tick()
    assert not game.teleports
    game.line("Bob has become the team leader", channel=46)
    carol.tick()
    assert not game.teleports
    game.leader = "Bob"  # the game updates the local menu after the genuine message
    carol.tick()
    assert game.teleports == [("Carol", "Bob")]
    game.line("Bob has become the team leader", channel=46)
    game.line("Hero has become the team leader", channel=46)
    carol.tick()
    assert game.teleports == [("Carol", "Bob")]


def test_rotation_without_a_game_message_does_not_teleport(crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, carol = crew
    start(leader, bob, carol)
    game.defer_transfer = True
    bob.tick(PACK)
    leader.tick()
    game.leader = "Bob"
    bob.tick(PACK)
    carol.tick()
    assert not game.teleports


def test_rally_requires_a_fresh_shortcut_response(crew: tuple[Game, Farmer, Farmer, Farmer],
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    game, leader, bob, carol = crew
    start(leader, bob, carol)
    carol.a.reply = {"name": "Bob", "id": game.players["Bob"], "at": int(game.time() * 1000)}

    def no_reply(_key: str) -> None:
        pass

    monkeypatch.setattr(carol, "key", no_reply)
    encounter(leader, bob, carol)
    assert not game.teleports


def test_other_game_channels_cannot_supply_the_leadership_signal(crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, carol = crew
    start(leader, bob, carol)
    game.defer_transfer = True
    bob.tick(PACK)
    leader.tick()
    game.leader = "Bob"
    game.line("Bob has become the team leader", channel=24)
    carol.tick()
    assert not game.teleports


def test_failed_transfer_and_competing_reports(crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, carol = crew
    start(leader, bob, carol)
    game.transfer_ok = False
    bob.tick(PACK)
    carol.tick((201, 202, 203))
    leader.tick()
    leader.tick()
    assert game.transfers.count(("Hero", "Bob")) == 1
    assert not game.teleports
    assert game.leader == "Hero"


def test_already_leading_requires_a_real_encounter_report(crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, carol = crew
    start(leader, bob, carol)
    game.line(MESSAGE, name="Hero", channel=9)  # gang chat
    carol.tick()
    assert not game.teleports
    leader.tick(PACK)
    bob.tick()
    carol.tick()
    assert not game.transfers
    assert game.teleports == [("Bob", "Hero"), ("Carol", "Hero")]


def test_cooldown_defers_without_a_second_announcement(crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, carol = crew
    start(leader, bob, carol)
    carol.__dict__["_teleport"] = {"off": None, "next": game.time() + 18, "not_leader": set()}
    encounter(leader, bob, carol)
    assert not game.teleports
    game.now += 20
    carol.tick()
    carol.tick()
    assert game.teleports == [("Carol", "Bob")]


def test_disband_cancels_an_unfinished_rally(crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, carol = crew
    start(leader, bob, carol)
    carol.in_fight = True
    encounter(leader, bob, carol)
    game.line("Team disbanded", channel=24)
    game.players.clear()
    carol.in_fight = False
    carol.tick()
    assert not game.teleports


def test_join_welcome_comes_from_the_current_leader(crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, original, bob, carol = crew
    start(original, bob, carol)
    encounter(original, bob, carol)
    game.players["Dave"] = 40
    game.line("Dave joined the team", channel=24)
    bob.tick(PACK)
    assert any(who == "Bob" and "Subscribe to Team Hunting" in text for who, text in game.sent)
    dave = Farmer(game, "Dave", team_hunt_subscribe=True)
    dave.tick()
    assert ("Dave", "Bob") not in game.teleports


def test_missing_confirmation_recovers_for_new_reports_without_rallying(crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, original, bob, carol = crew
    start(original, bob, carol)
    game.defer_transfer = True
    bob.tick(PACK)
    original.tick()
    game.leader = "Bob"
    game.now += 15
    bob.tick()
    bob.tick()
    carol.tick()
    assert not game.teleports
    game.defer_transfer = False
    carol.tick((201, 202, 203))
    bob.tick()
    assert game.transfers == [("Hero", "Bob"), ("Bob", "Carol")]


def test_a_new_encounter_can_be_reported_while_monsters_are_attacking(
    crew: tuple[Game, Farmer, Farmer, Farmer], monkeypatch: pytest.MonkeyPatch,
) -> None:
    game, leader, bob, carol = crew
    start(leader, bob, carol)

    def attacking() -> set[int]:
        return set(PACK)

    monkeypatch.setattr(bob, "attackers", attacking)
    bob.tick(PACK)
    leader.tick()
    assert ("Bob", MESSAGE) in game.sent and game.leader == "Bob"


def test_roster_reads_member_ids_and_recognizes_our_own_leader_action() -> None:
    game = TeamGame({"Bob": (20, True), "Carol": (30, True)})
    b = TeamBot(game)
    roster = teamhunt.read_roster(as_bot(b))
    assert roster is not None
    assert roster.leader == "Hero" and roster.players["Bob"] == 20
    assert game.menu is None


def test_team_management_resumes_after_an_intended_hunting_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    lead = teamhunt.teamlead
    game = TeamGame({"Bob": (20, True)})
    b = TeamBot(game, team_accept=True)
    pid = b.me()["id"]
    monkeypatch.setattr(b, "me", lambda: {"name": "Hero", "id": pid})
    bot = as_bot(b)
    lead.watch(bot)
    lead.note_hunt_handoff(bot, "Bob", b.clock.time() + 12)
    game.chat.append("Bob has become the team leader")
    lead.watch(bot)
    assert not lead.on(bot) and not lead.state(bot).off
    game.chat.append("Hero has become the team leader")
    lead.watch(bot)
    assert lead.on(bot)


@pytest.mark.parametrize("event", ["Team disbanded", "Bob joined the team"])
def test_old_welcome_is_ignored_after_the_team_changed(crew: tuple[Game, Farmer, Farmer, Farmer], event: str) -> None:
    game, leader, bob, _carol = crew
    leader.tick()
    game.line(event, channel=24)
    # The same players are in the new team, which has no hunting notice of its own.
    game.team = "New team"
    bob.tick(PACK)
    assert ("Bob", MESSAGE) not in game.sent


def test_welcome_and_first_report_wait_for_combat_to_clear(crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, _carol = crew
    start(bob, leader)
    bob.active_attackers = {99}
    bob.tick()
    leader.tick(PACK)
    bob.tick()
    assert not game.teleports and bob.a.reply is None

    bob.active_attackers.clear()
    bob.tick()
    bob.tick()
    assert game.teleports == [("Bob", "Hero")]
    assert sum(text.startswith(teamhunt.NOTICE) for _, text in game.sent) == 1


def test_one_welcome_survives_a_temporary_roster_failure(crew: tuple[Game, Farmer, Farmer, Farmer],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    game, leader, bob, _carol = crew
    start(bob, leader)
    read = teamhunt.read_roster
    checks: list[str] = []

    def delayed_roster(bot: Farmer) -> teamhunt.Roster | None:
        checks.append(bot.name)
        return None if len(checks) == 1 else read(as_bot(bot))

    monkeypatch.setattr(teamhunt, "read_roster", delayed_roster)
    bob.tick()
    welcome = game.sent[0][1]
    game.line(welcome, leader.name)  # a duplicate must not trigger another menu immediately
    bob.tick()
    assert checks == ["Bob"]
    game.now += teamhunt.ROSTER_RETRY
    bob.tick(PACK)
    assert checks == ["Bob", "Bob"]
    assert ("Bob", MESSAGE) in game.sent


@pytest.mark.parametrize("reason", ["disbanded", "left", "rejoined", "disabled", "expired", "new leader"])
def test_pending_welcome_cannot_activate_for_a_stale_team(crew: tuple[Game, Farmer, Farmer, Farmer],
                                                        reason: str) -> None:
    game, leader, bob, _carol = crew
    start(bob, leader)
    bob.active_attackers = {99}
    bob.tick()
    if reason == "disbanded":
        game.line("Team disbanded", channel=24)
    elif reason == "left":
        game.line("Bob has quit the team", channel=24)
    elif reason == "rejoined":
        game.line("Bob joined the team", channel=24)
    elif reason == "disabled":
        game.line(teamhunt.STOPPED, leader.name)
    elif reason == "expired":
        game.now += teamhunt.WELCOME_FOR + 1
    else:
        game.changed("Carol")
    bob.active_attackers.clear()
    bob.tick(PACK)
    assert ("Bob", MESSAGE) not in game.sent
    assert not game.teleports


def test_accepting_old_welcome_does_not_replay_a_pre_start_report(crew: tuple[Game, Farmer, Farmer, Farmer]) -> None:
    game, leader, bob, _carol = crew
    leader.tick()
    leader.tick(PACK)
    bob.active_attackers = {99}
    bob.tick()
    bob.active_attackers.clear()
    bob.tick()
    assert not game.teleports
    game.now += teamhunt.HANDOFF_FOR + 1
    game.line(MESSAGE, leader.name)
    bob.tick()
    assert game.teleports == [("Bob", "Hero")]


def test_another_members_welcome_cannot_replace_the_leaders_pending_welcome(
    crew: tuple[Game, Farmer, Farmer, Farmer],
) -> None:
    game, leader, bob, carol = crew
    start(bob, leader)
    bob.active_attackers = {99}
    bob.tick()
    game.line(teamhunt.Rules(leader.pid, 3, False, "Different report").welcome(), carol.name)
    leader.tick(PACK)
    bob.active_attackers.clear()
    bob.tick()
    assert game.teleports == [("Bob", "Hero")]
