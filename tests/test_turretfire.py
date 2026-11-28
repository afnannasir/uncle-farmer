"""Turret firing (engine/turretfire.py) against the menus the game showed a turret keeper on 2026-11-28:
a pretend game serves the turret's Fire menus, and each shot tells of itself in chat as the game does."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import as_bot
from test_chores import INNER, HIGHLAND, TURRET, Entry, Game, Page, Pages, cfg, npc
from test_chores import world as world     # the pretend world's fixture

from unclefarmer.engine import chores, turretfire as tf

OWN_GANG = "Sunfield"
ROW = "highland %d, the completeness of crystal turret is %.1f%%; the completeness of Wall barricade is %.1f%%"


class Agent:
    """The agent's `said` and `chat`, as the game fills them."""

    def __init__(self, g: Game) -> None:
        self.g = g

    def json(self, cmd: str) -> dict[str, Any]:
        word, _sp, rest = cmd.partition(" ")
        since = int(rest.split()[0] or 0) if rest else 0
        if word == "said":
            return {"now": self.g.tick, "said": [{"t": t, "text": x} for t, x in self.g.heard if t > since]}
        if word == "chat":
            lines: list[dict[str, Any]] = getattr(self.g, "chat", [])
            return {"last": len(lines), "chat": lines[since:]}
        return {}


class Turrets:
    """Our gang's highlands, the turrets this character keeps, the other gangs' highlands and who is
    in our gang's area; records each shot."""

    def __init__(self, world: type[Game], ours: list[int], keeps: list[int],
                 gangs: dict[str, dict[int, tuple[float, float]]] | None = None,
                 area: list[str] | None = None, per_page: int = 20, occupied: dict[int, str] | None = None,
                 held: list[int] | None = None) -> None:
        taken = occupied or {}                    # highland -> the gang occupying it
        self.keeps, self.gangs = keeps, gangs if gangs is not None else {"Northwind": {7: (99.0, 100.0)}}
        self.area = area if area is not None else []
        self.per_page = per_page
        self.here = 0
        self.fired: dict[int, str] = {}           # turret -> what it hit
        pages: Pages = {TURRET: self.turret, "aim": [("Aim at %s gang area" % OWN_GANG, "own"),
                                                    ("Aim at other highlands", "gangs1")],
                        "own": [("Aim at the main mansion", "area"), ("Aim at the mining area", "area"),
                                ("Aim at the lake", "area")],
                        "area": self.area_page,
                        "Highland Entrance": [("Occupied Highlands", "occupied"), ("Gang Highlands", None)],
                        "occupied": [("the own Gang's Highland %d" % n, self.into(n)) for n in ours]
                        + [("Sunfield Gang's Highland %d" % n, self.into(n)) for n in held or []],
                        "Teleportation circle": [("Return to Main mansion", self.home)]}
        names = list(self.gangs)
        pages_of = [names[i:i + per_page] for i in range(0, len(names), per_page)] or [[]]
        for i, chunk in enumerate(pages_of, 1):
            rows: list[Entry] = [("%d; %s; leader: Someone; has 4 Gang Highlands and 3 occupied Highlands"
                                  % ((i - 1) * per_page + j + 1, gang), "gang:" + gang) for j, gang in enumerate(chunk)]
            if i < len(pages_of):
                rows.append(("Next page: %d" % (i + 1), "gangs%d" % (i + 1)))
            pages["gangs%d" % i] = rows
        for gang, highlands in self.gangs.items():
            listed: list[Entry] = [("Gang Highlands", None)]
            listed += [(ROW % (n, t, w) + ("; Occupied by the %s gang" % taken[n] if n in taken else ""),
                        "pick:%d" % n) for n, (t, w) in highlands.items()]
            pages["gang:" + gang] = listed
            for n in highlands:
                pages["pick:%d" % n] = [("Please select", None), ("Hit Crystal Turret", self.hit(n, "crystal turret")),
                                        ("Hit Wall barricade", self.hit(n, "Wall barricade"))]
        self.g = world(pages, {"gang_entrance": "gate"}, arnebia=0, map_id=INNER)
        self.g.a = Agent(self.g)       # type: ignore[assignment]
        self.g.chat = []               # type: ignore[attr-defined]

    def into(self, number: int) -> Any:
        def act(g: Game) -> None:
            self.here = number
            g.mid = HIGHLAND
            g.view = [npc("Teleportation circle", 30, 2), npc(TURRET, 30, 28)]
        return act

    def home(self, g: Game) -> None:
        g.mid = INNER

    def turret(self, g: Game) -> list[Entry]:
        return [("the completeness is 95.0%", None), ("repair", None), ("Fire", self.fire)]

    def fire(self, g: Game) -> str | None:
        if self.here not in self.keeps:
            g.say("You are not the turret administer")
            return None
        return "aim"

    def tell(self, line: str) -> None:
        self.g.chat.append({"channel": 10, "name": "", "text": line})    # type: ignore[attr-defined]

    def hit(self, n: int, part: str) -> Any:
        def act(g: Game) -> None:
            if self.here in self.fired:
                g.say("already fired today")
                return None
            self.fired[self.here] = "%d %s" % (n, part)
            self.tell("Hero shelled the %s of Highland %d" % (part, n))
        return act

    def area_page(self, g: Game) -> list[Entry]:
        rows: list[Entry] = [("%s lake; currently %d people online" % (OWN_GANG, len(self.area)), None)]
        for i, text in enumerate(self.area):
            rows.append((text, self.confirm(text.split(";")[0])) if ";" in text else (text, None))
            del i
        return rows

    def confirm(self, player: str) -> Any:
        def ask(g: Game) -> str:
            def shoot(g: Game) -> None:
                if self.here in self.fired:
                    g.say("already fired today")
                    return None
                self.fired[self.here] = player
                self.tell("%s was killed by a shell from highland %d" % (player, self.here))
            g.pages["confirm"] = [("Are you sure you want to shoot?", None), ("Confirm", shoot), ("cancel", None)]
            return "confirm"
        return ask


def fire_cfg(**over: Any) -> dict[str, Any]:
    return cfg(**{"fire": True, "fire_turrets": [80], "fire_targets": [{"gang": "Northwind", "highland": 7}], **over})


FOREIGN = "Visitor;22; can be bombarded; Priest;male ; level 16; Foreign player"
SHELTERED = "Stranger;33; in non-shelled areas; Mage;female ; level 40; Foreign player"
OUR_OWN = "Friend;11; in non-shelled areas; Mage;female ; level 89; own gang player"


# ---------------------------------------------------------------------------------------- settings
def test_bad_settings_fall_back_to_safe_values() -> None:
    s = tf.settings_of({"fire_turrets": [80, "67", 80, -1, "x"], "fire_aim": "everyone", "fire_part": 3,
                        "fire_limit": 500, "fire_targets": [{"gang": " North  wind ", "highland": "7"},
                                                            {"gang": "", "highland": 8}, "junk"],
                        "fire_spare": ["Ally", "ally", ""]})
    assert s.turrets == [80, 67] and s.aim == "other" and s.part == "turret" and s.limit == 100
    assert s.targets == [tf.Target("North wind", 7)] and s.spare == ["Ally"] and s.area == "lake"
    assert tf.settings_of({}).limit == tf.DEFAULT_LIMIT


def test_firing_is_due_only_with_a_turret_and_something_to_aim_at() -> None:
    assert not chores.wanted(cfg(fire=True, fire_turrets=[80]), "firing")            # no target listed
    assert chores.wanted(cfg(fire=True, fire_turrets=[80], fire_aim="own"), "firing")
    assert chores.wanted(fire_cfg(), "firing")
    assert not chores.wanted(fire_cfg(fire=False), "firing")
    assert not chores.wanted(cfg(fire=True, fire_aim="own"), "firing")               # no turret listed
    assert "firing" not in chores.due("Nobody", cfg())


# ---------------------------------------------------------------------------------------- deciding
def test_a_gangs_highlands_are_read_from_its_list() -> None:
    text = " | ".join(["Gang Highlands", ROW % (7, 99.0, 100.0), "Hit Crystal Turret", ROW % (171, 95.0, 92.5)])
    assert tf.parse_highlands(text) == {7: (99.0, 100.0, None), 171: (95.0, 92.5, None)}


def test_a_highland_another_gang_occupies_is_read_with_its_occupier() -> None:
    text = " | ".join([ROW % (11, 95.0, 85.0) + "; Occupied by the Sunfield gang", "Hit Crystal Turret",
                       ROW % (109, 96.0, 100.0)])
    assert tf.parse_highlands(text) == {11: (95.0, 85.0, "Sunfield"), 109: (96.0, 100.0, None)}


def test_an_occupied_highland_is_skipped_unless_its_entry_says_fire(world: type[Game]) -> None:
    gangs = {"Northwind": {11: (95.0, 85.0), 109: (96.0, 100.0)}}
    t = Turrets(world, ours=[80], keeps=[80], gangs=gangs, occupied={11: "Sunfield"})
    two = [{"gang": "Northwind", "highland": 11}, {"gang": "Northwind", "highland": 109}]
    out = chores.run(as_bot(t.g), fire_cfg(fire_targets=two))
    assert t.fired == {80: "109 crystal turret"}, out
    assert any("occupied by the Sunfield gang: its owner has changed" in ln for ln in t.g.lines)


def test_an_occupied_highland_is_fired_at_when_its_entry_says_so(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80], gangs={"Northwind": {11: (95.0, 85.0)}}, occupied={11: "Sunfield"})
    out = chores.run(as_bot(t.g), fire_cfg(fire_fallback=[{"gang": "Northwind", "highland": 11, "occupied": True}],
                                           fire_targets=[]))
    assert t.fired == {80: "11 crystal turret"}, out


def test_fire_when_occupied_is_saved_and_shown() -> None:
    got = tf.targets([{"gang": "A", "highland": 1, "occupied": True}, {"gang": "A", "highland": 2, "occupied": "yes"}])
    assert [t.occupied for t in got] == [True, False]
    assert tf.saved_targets(got) == [{"gang": "A", "highland": 1, "occupied": True}, {"gang": "A", "highland": 2}]
    assert tf.target_line(tf.Target("A", 1, 80, True)) == "A, highland 1 - paired with our turret in highland 80 - fires even if occupied"


@pytest.mark.parametrize("part, turret, wall, limit, want", [
    ("turret", 60.0, 100.0, 50.0, "turret"),
    ("turret", 50.0, 100.0, 50.0, None),          # at the limit: left alone
    ("wall", 100.0, 40.0, 50.0, None),
    ("wall", 100.0, 40.0, None, "wall"),          # the random gangs: no limit
    ("highest", 60.0, 80.0, 50.0, "wall"),
    ("highest", 80.0, 80.0, 50.0, "wall"),        # the same: the wall
    ("highest", 30.0, 45.0, 50.0, None),          # the higher one is at the limit too
    ("highest", 0.0, 0.0, None, "wall"),          # both at 0 (a random gang): the wall
    ("highest", 0.0, 0.0, 50.0, None),            # both at 0 (a target): below the limit
])
def test_the_part_to_hit(part: str, turret: float, wall: float, limit: float | None, want: str | None) -> None:
    assert tf.choose_part(part, turret, wall, limit) == want


def test_only_a_foreign_player_who_can_be_shot_is_picked() -> None:
    players = tf.parse_players(["%s lake; currently 3 people online" % OWN_GANG, "Are you sure you want to shoot?",
                                OUR_OWN, SHELTERED, FOREIGN])
    assert [p.name for p in players] == ["Friend", "Stranger", "Visitor"]
    assert tf.pick_player(players, []) == players[2]
    assert tf.pick_player(players, ["visitor"]) is None           # spared, whatever the case
    assert tf.pick_player(players[:2], []) is None


def test_the_games_words() -> None:
    assert tf.answer(["You are not the turret administer"]) == tf.NOT_KEEPER_
    assert tf.answer(["already fired today"]) == tf.ALREADY
    assert tf.answer(["sent"]) is None
    assert tf.shelled("Hero", "turret", 7).search("Hero shelled the crystal turret of Highland 7")
    assert not tf.shelled("Hero", "turret", 7).search("Other shelled the crystal turret of Highland 7")
    assert not tf.shelled("Hero", "turret", 7).search("Hero shelled the crystal turret of Highland 71")
    assert tf.shelled("Hero", "wall", 7).search("Hero shelled the Wall barricade of Highland 7")
    assert tf.killed("Visitor", 67).search("Visitor was killed by a shell from highland 67")


# ---------------------------------------------------------------------------------------- in the game
def test_a_kept_turret_fires_at_the_target_and_is_written_down(world: type[Game]) -> None:
    t = Turrets(world, ours=[80, 67], keeps=[80])
    out = chores.run(as_bot(t.g), fire_cfg())
    assert t.fired == {80: "7 crystal turret"}
    assert out == [("firing", "turret 80: fired at Northwind, highland 7 (Crystal Turret)")]
    assert tf.fired_today("Hero") == {"80": out[0][1]}
    assert t.g.mid == INNER                                       # back through the Teleportation circle
    assert chores.run(as_bot(t.g), fire_cfg())[0][1] == "every turret has fired today"


def test_a_turret_this_character_doesnt_keep_is_said_and_not_tried_again(world: type[Game]) -> None:
    t = Turrets(world, ours=[80, 67], keeps=[80])
    out = chores.run(as_bot(t.g), fire_cfg(fire_turrets=[67, 80]))
    assert "turret 67: this character isn't its keeper" in out[0][1] and t.fired == {80: "7 crystal turret"}
    assert set(tf.fired_today("Hero")) == {"67", "80"}


def test_a_turret_that_already_fired_is_written_down(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])
    t.fired[80] = "by hand"
    out = chores.run(as_bot(t.g), fire_cfg())
    assert out[0][1] == "turret 80: already fired today" and "80" in tf.fired_today("Hero")


def test_a_target_at_the_limit_goes_to_the_next_target(world: type[Game]) -> None:
    gangs = {"Northwind": {7: (40.0, 100.0), 57: (90.0, 90.0)}}
    t = Turrets(world, ours=[80], keeps=[80], gangs=gangs)
    c = fire_cfg(fire_targets=[{"gang": "Northwind", "highland": 7}, {"gang": "Northwind", "highland": 57}])
    chores.run(as_bot(t.g), c)
    assert t.fired == {80: "57 crystal turret"}


def test_every_target_at_the_limit_goes_to_the_random_gangs(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80], gangs={"Northwind": {7: (40.0, 100.0)}, "Riverfolk": {12: (10.0, 10.0)}})
    c = fire_cfg(fire_fallback=[{"gang": "Riverfolk", "highland": 12}])
    out = chores.run(as_bot(t.g), c)
    assert t.fired == {80: "12 crystal turret"}, out                 # the random gang: no limit
    assert any("at or below 50%" in ln for ln in t.g.lines)


def test_a_highland_with_another_owner_is_skipped_and_said(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80], gangs={"Northwind": {57: (90.0, 90.0)}})
    out = chores.run(as_bot(t.g), fire_cfg())
    assert t.fired == {} and out[0][1] == "turret 80: no highland in the lists can be hit today"
    assert any("its owner has changed" in ln for ln in t.g.lines)
    assert tf.fired_today("Hero") == {}                           # tried again on the next run


def test_the_gang_name_must_match_its_capitals(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])
    out = chores.run(as_bot(t.g), fire_cfg(fire_targets=[{"gang": "northwind", "highland": 7}]))
    assert t.fired == {} and out[0][1] == "turret 80: no highland in the lists can be hit today"
    assert any("no gang named northwind" in ln for ln in t.g.lines)


def test_a_gang_on_a_later_page_is_found(world: type[Game]) -> None:
    gangs: dict[str, dict[int, tuple[float, float]]] = {"Gang%d" % i: {} for i in range(25)}
    gangs["Riverfolk"] = {12: (80.0, 70.0)}
    t = Turrets(world, ours=[80], keeps=[80], gangs=gangs)
    chores.run(as_bot(t.g), fire_cfg(fire_targets=[{"gang": "Riverfolk", "highland": 12}], fire_part="highest"))
    assert t.fired == {80: "12 crystal turret"} and "Next page: 2" in t.g.chosen


def test_the_wall_is_hit_when_chosen(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])
    chores.run(as_bot(t.g), fire_cfg(fire_part="wall", fire_limit=99))
    assert t.fired == {80: "7 Wall barricade"}


def test_our_area_shoots_the_foreign_player_who_can_be_shot(world: type[Game]) -> None:
    t = Turrets(world, ours=[67], keeps=[67], area=[OUR_OWN, SHELTERED, FOREIGN])
    out = chores.run(as_bot(t.g), cfg(fire=True, fire_turrets=[67], fire_aim="own", fire_area="lake"))
    assert t.fired == {67: "Visitor"} and "Aim at the lake" in t.g.chosen
    assert out[0][1] == "turret 67: fired at Visitor at the lake"


def test_nobody_to_shoot_fires_nothing_and_tries_again_later(world: type[Game]) -> None:
    t = Turrets(world, ours=[67], keeps=[67], area=[OUR_OWN, FOREIGN])
    out = chores.run(as_bot(t.g), cfg(fire=True, fire_turrets=[67], fire_aim="own", fire_spare=["Visitor"]))
    assert t.fired == {} and out[0][1] == "turret 67: nobody at the lake can be shot now"
    assert tf.fired_today("Hero") == {} and chores.due("Hero", cfg(fire=True, fire_turrets=[67], fire_aim="own"))


def test_check_only_fires_nothing(world: type[Game]) -> None:
    t = Turrets(world, ours=[80, 67], keeps=[80, 67], area=[FOREIGN])
    out = chores.run(as_bot(t.g), fire_cfg(fire_turrets=[80]), check_only=True)
    assert t.fired == {} and out[0][1] == "turret 80: would hit Northwind, highland 7 (Crystal Turret)"
    out = chores.run(as_bot(t.g), cfg(fire=True, fire_turrets=[67], fire_aim="own"), check_only=True)
    assert t.fired == {} and out[0][1] == "turret 67: would shoot Visitor at the lake"
    assert tf.fired_today("Hero") == {}


def test_a_shot_the_game_doesnt_confirm_is_tried_again(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])
    t.tell = lambda line: None                                    # no chat line comes
    out = chores.run(as_bot(t.g), fire_cfg())
    assert "didn't confirm it" in out[0][1] and tf.fired_today("Hero") == {}
    out = chores.run(as_bot(t.g), fire_cfg())                     # the next run: the game refuses
    assert out[0][1] == "turret 80: already fired today" and "80" in tf.fired_today("Hero")


def test_construction_fires_in_the_highlands_it_visits(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])
    t.g.pages[TURRET] = repairable(t)
    out = chores.run(as_bot(t.g), fire_cfg(construction=1))
    assert t.fired == {80: "7 crystal turret"}
    assert [c for c, _n in out] == ["construction", "firing"]
    assert out[1][1] == "turret 80: fired at Northwind, highland 7 (Crystal Turret)"
    assert t.g.chosen.count("the own Gang's Highland 80") == 1      # one trip for both


def test_construction_doesnt_fire_when_only_construction_is_run(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])
    t.g.pages[TURRET] = repairable(t)
    chores.run(as_bot(t.g), fire_cfg(construction=1), only=["construction"])
    assert t.fired == {}


def test_a_typed_highland_number() -> None:
    assert tf.highland_number(" 80 ") == 80
    assert tf.highland_number("0") is None and tf.highland_number("eighty") is None


def test_a_turret_not_in_our_gangs_list_is_said(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])
    out = chores.run(as_bot(t.g), fire_cfg(fire_turrets=[55]))
    assert out[0][1] == "turret 55: highland 55 isn't in our gang's list" and tf.fired_today("Hero") == {}


def test_no_way_into_the_highlands_is_said_and_tried_again(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])
    del t.g.pages["Highland Entrance"]
    out = chores.run(as_bot(t.g), fire_cfg())
    assert out[0][1].startswith("turret 80: couldn't talk to") and tf.fired_today("Hero") == {}


def test_a_gang_not_in_the_list_is_skipped_and_said(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])
    out = chores.run(as_bot(t.g), fire_cfg(fire_targets=[{"gang": "Nobody", "highland": 7}]))
    assert t.fired == {} and "no highland in the lists" in out[0][1]
    assert any("no gang named Nobody" in ln for ln in t.g.lines)


def test_a_gang_already_read_is_not_opened_again_to_skip(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80], gangs={"Northwind": {7: (40.0, 100.0)}})
    c = fire_cfg(fire_targets=[{"gang": "Northwind", "highland": 7}, {"gang": "Northwind", "highland": 8}])
    chores.run(as_bot(t.g), c)
    assert t.g.chosen.count("Aim at other highlands") == 1 and t.fired == {}
    assert any("highland 8 isn't Northwind's" in ln for ln in t.g.lines)


def test_fire_that_opens_nothing_and_says_nothing_is_trouble(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])
    t.g.pages["aim"] = []
    out = chores.run(as_bot(t.g), fire_cfg())
    assert out[0][1] == "turret 80: the turret's menu didn't show what was expected and the game said nothing"
    assert tf.fired_today("Hero") == {} and "firing" not in chores.done_today("Hero")


def test_a_shot_without_the_agents_chat_is_unknown(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])

    class NoChat(Agent):
        def json(self, cmd: str) -> dict[str, Any]:
            if cmd.startswith("chat"):
                raise RuntimeError("unknown command")
            return super().json(cmd)
    t.g.a = NoChat(t.g)       # type: ignore[assignment]
    out = chores.run(as_bot(t.g), fire_cfg())
    assert t.fired == {80: "7 crystal turret"} and "didn't confirm it" in out[0][1]


def test_the_chore_with_every_turret_fired_today_is_finished(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])
    chores.put("Hero", "_fired", {"80": "turret 80: fired"})
    assert tf.firing(as_bot(t.g), fire_cfg(), False) == ("every turret has fired today", True)
    assert t.g.talked == []


def test_each_turret_tries_its_pair_then_the_unpaired_targets_then_the_random_gangs() -> None:
    s = tf.settings_of(fire_cfg(fire_turrets=[80, 67], fire_targets=[
        {"gang": "Northwind", "highland": 7, "turret": 67}, {"gang": "Northwind", "highland": 57},
        {"gang": "Riverfolk", "highland": 12, "turret": 80}], fire_fallback=[{"gang": "Riverfolk", "highland": 3}]))
    assert [(t.highland, limited) for t, limited in tf.order_for(s, 80)] == [(12, True), (57, True), (3, False)]
    assert [(t.highland, limited) for t, limited in tf.order_for(s, 67)] == [(7, True), (57, True), (3, False)]
    assert [t.highland for t, _l in tf.order_for(s, 99)] == [57, 3]       # no pair: never another's


def test_a_turret_can_have_several_pairs_but_only_a_listed_turret() -> None:
    got = tf.targets([{"gang": "A", "highland": 1, "turret": 80}, {"gang": "A", "highland": 2, "turret": 80},
                      {"gang": "A", "highland": 3, "turret": 55}], turrets=[80])
    assert [t.turret for t in got] == [80, 80, None]
    entries = [tf.Target("A", 1, 80), tf.Target("A", 2)]
    assert [t.turret for t in tf.pair(entries, 1, 80)] == [80, 80]
    assert [t.turret for t in tf.pair(entries, 0, None)] == [None, None]
    assert tf.saved_targets([tf.Target("A", 1, 80), tf.Target("A", 2)]) == [
        {"gang": "A", "highland": 1, "turret": 80}, {"gang": "A", "highland": 2}]
    assert tf.target_line(tf.Target("A", 1, 80)) == "A, highland 1 - paired with our turret in highland 80"
    pairs = [tf.Target("A", 1, 80), tf.Target("B", 2), tf.Target("C", 3, 80)]
    assert tf.turret_line(80, pairs) == "Highland 80 - paired with: A highland 1, C highland 3"
    assert tf.turret_line(67, pairs) == "Highland 67"


def test_two_turrets_fire_at_their_own_pairs(world: type[Game]) -> None:
    t = Turrets(world, ours=[80, 67], keeps=[80, 67], gangs={"Northwind": {7: (90.0, 90.0), 57: (90.0, 90.0)}})
    out = chores.run(as_bot(t.g), fire_cfg(fire_turrets=[80, 67], fire_targets=[
        {"gang": "Northwind", "highland": 7, "turret": 67}, {"gang": "Northwind", "highland": 57, "turret": 80}]))
    assert t.fired == {80: "57 crystal turret", 67: "7 crystal turret"}, out


def test_a_turret_tries_its_pairs_in_list_order(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80], gangs={"Northwind": {7: (20.0, 90.0), 55: (90.0, 90.0), 8: (90.0, 90.0)}})
    chores.run(as_bot(t.g), fire_cfg(fire_targets=[
        {"gang": "Northwind", "highland": 8}, {"gang": "Northwind", "highland": 7, "turret": 80},
        {"gang": "Northwind", "highland": 55, "turret": 80}]))
    assert t.fired[80] == "55 crystal turret"                               # 7 at the limit; its pairs before 8


def test_a_pair_at_the_limit_goes_to_the_unpaired_target(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80], gangs={"Northwind": {7: (90.0, 90.0), 57: (20.0, 90.0), 8: (90.0, 90.0)}})
    chores.run(as_bot(t.g), fire_cfg(fire_turrets=[80, 67], fire_targets=[
        {"gang": "Northwind", "highland": 7, "turret": 67}, {"gang": "Northwind", "highland": 57, "turret": 80},
        {"gang": "Northwind", "highland": 8}]))
    assert t.fired[80] == "8 crystal turret"                                # never 67's pair, highland 7


def test_a_turret_added_later_the_same_day_still_fires(world: type[Game]) -> None:
    t = Turrets(world, ours=[80, 67], keeps=[80, 67])
    chores.run(as_bot(t.g), fire_cfg())
    out = chores.run(as_bot(t.g), fire_cfg(fire_turrets=[80, 67]))
    assert t.fired == {80: "7 crystal turret", 67: "7 crystal turret"}, out


def test_an_old_refusal_isnt_taken_for_a_new_one(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])
    t.g.say("already fired today")                                 # said before this run
    chores.run(as_bot(t.g), fire_cfg())
    assert t.fired == {80: "7 crystal turret"}


def test_a_refusal_when_the_player_is_chosen_is_written_down(world: type[Game]) -> None:
    t = Turrets(world, ours=[67], keeps=[67], area=[FOREIGN])

    def refuse(g: Game) -> None:
        g.say("already fired today")
    t.confirm = lambda player: refuse                             # type: ignore[method-assign]
    t.g.pages["area"] = t.area_page
    out = chores.run(as_bot(t.g), cfg(fire=True, fire_turrets=[67], fire_aim="own"))
    assert out[0][1] == "turret 67: already fired today" and "67" in tf.fired_today("Hero")


def test_a_failed_try_on_constructions_visit_is_tried_again_by_the_chore(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[80])
    t.g.pages[TURRET] = repairable(t)
    tries: list[int] = []
    real = t.fire

    def flaky(g: Game) -> str | None:
        tries.append(1)
        return None if len(tries) == 1 else real(g)                # the first Fire opens nothing
    t.fire = flaky                                                  # type: ignore[method-assign]
    t.g.pages[TURRET] = repairable(t)
    out = chores.run(as_bot(t.g), fire_cfg(construction=1))
    assert t.fired == {80: "7 crystal turret"} and len(tries) == 2, out


def repairable(t: Turrets) -> Page:
    repaired: list[int] = []

    def repair(g: Game) -> None:
        repaired.append(t.here)

    def page(g: Game) -> list[Entry]:
        if repaired:
            return [("You've repaired the crystal turret today", None), ("Fire", t.fire)]
        return [("the completeness is 95.0%", None), ("repair", repair), ("Fire", t.fire)]
    return page


# ---------------------------------------------------------------------------------------- the window
def quiet(*_args: object, **_kw: object) -> None:
    """A message box nobody needs to see in a test."""


def test_the_gang_turrets_page_saves_what_was_set(monkeypatch: pytest.MonkeyPatch) -> None:
    wx = pytest.importorskip("wx")
    from unclefarmer import settings
    from unclefarmer.gui.chores import ChoresDialog

    app = wx.GetApp() or wx.App(False)
    frame = wx.Frame(None)
    dlg = ChoresDialog(frame, "Hero")
    try:
        assert not dlg.fire.GetValue() and dlg.aim.GetSelection() == 1    # other gangs' highlands
        assert not dlg.area.IsEnabled() and dlg.limit.IsEnabled()
        answers = iter([80, tf.Target("Northwind", 7), tf.Target("Northwind", 7)])
        monkeypatch.setattr(dlg, "ask_turret", lambda: next(answers))
        monkeypatch.setattr(dlg, "ask_target", lambda: next(answers))
        dlg.turrets.ask, dlg.targets.ask = dlg.ask_turret, dlg.ask_target
        monkeypatch.setattr("unclefarmer.gui.chores.info", quiet)
        dlg.fire.SetValue(True)
        dlg.turrets.on_add(wx.CommandEvent())
        dlg.targets.on_add(wx.CommandEvent())
        dlg.targets.on_add(wx.CommandEvent())                       # the same one again: not added twice
        assert dlg.targets.list.GetStrings() == ["Northwind, highland 7"]
        dlg.part.SetSelection(2)
        dlg.limit.SetValue(30)
        done: list[int] = []
        monkeypatch.setattr(dlg, "EndModal", done.append)
        dlg.done(wx.ID_OK)
        assert done == [wx.ID_OK]
        saved = chores.config(settings.load_profile("Hero"))
        assert saved["fire"] and saved["fire_turrets"] == [80] and saved["fire_part"] == "highest"
        assert saved["fire_limit"] == 30 and saved["fire_targets"] == [{"gang": "Northwind", "highland": 7}]
        assert chores.wanted(saved, "firing")
        more = iter([tf.Target("Riverfolk", 12)])
        dlg.targets.ask = lambda: next(more)
        dlg.targets.on_add(wx.CommandEvent())
        dlg.targets.move(-1)                                         # Riverfolk first now
        assert [x.gang for x in dlg.targets.items] == ["Riverfolk", "Northwind"]

        class Choice:
            def __init__(self, *_a: object) -> None:
                self.chosen = 1

            def SetSelection(self, i: int) -> None:  # noqa: N802 (wx's name)
                pass

            def ShowModal(self) -> int:  # noqa: N802 (wx's name)
                return wx.ID_OK

            def GetSelection(self) -> int:  # noqa: N802 (wx's name)
                return self.chosen

            def Destroy(self) -> None:  # noqa: N802 (wx's name)
                pass
        monkeypatch.setattr("unclefarmer.gui.chores.wx.SingleChoiceDialog", Choice)
        dlg.on_pair(wx.CommandEvent())                               # Riverfolk 12 with turret 80
        assert dlg.targets.list.GetStrings()[0] == "Riverfolk, highland 12 - paired with our turret in highland 80"
        assert dlg.turrets.list.GetStrings() == ["Highland 80 - paired with: Riverfolk highland 12"]
        dlg.done(wx.ID_OK)
        saved = chores.config(settings.load_profile("Hero"))
        assert saved["fire_targets"][0] == {"gang": "Riverfolk", "highland": 12, "turret": 80}
        dlg.aim.SetSelection(0)
        dlg.sync_aim()
        assert dlg.area.IsEnabled() and not dlg.limit.IsEnabled() and not dlg.targets.list.IsEnabled()
    finally:
        dlg.Destroy()
        frame.Destroy()
        app.Yield()


def test_firing_ticked_without_a_turret_isnt_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    wx = pytest.importorskip("wx")
    from unclefarmer.gui.chores import ChoresDialog

    app = wx.GetApp() or wx.App(False)
    frame = wx.Frame(None)
    dlg = ChoresDialog(frame, "Hero")
    try:
        said: list[str] = []
        def error(parent: object, message: str, title: str | None = None) -> None:
            said.append(message)

        def end(code: int) -> None:
            said.append("closed")
        monkeypatch.setattr("unclefarmer.gui.chores.error", error)
        monkeypatch.setattr(dlg, "EndModal", end)
        dlg.fire.SetValue(True)
        dlg.done(wx.ID_OK)
        assert said == ["Add a turret this character keeps, or untick firing."]
    finally:
        dlg.Destroy()
        frame.Destroy()
        app.Yield()


def test_the_add_window_asks_whether_to_fire_when_occupied(monkeypatch: pytest.MonkeyPatch) -> None:
    wx = pytest.importorskip("wx")
    from unclefarmer.gui.chores import ChoresDialog, TargetDialog

    app = wx.GetApp() or wx.App(False)
    frame = wx.Frame(None)
    errors: list[object] = []
    def said(*a: object, **_k: object) -> None:
        errors.append(a)
    monkeypatch.setattr("unclefarmer.gui.chores.error", said)
    d = TargetDialog(frame)
    try:
        monkeypatch.setattr(d, "EndModal", quiet)
        d.gang.SetValue("  Northwind ")
        d.number.SetValue("eleven")
        d.on_ok(wx.CommandEvent())
        assert d.target is None and len(errors) == 1                  # not a number: said, kept open
        d.number.SetValue("11")
        d.occupied.SetValue(True)
        d.on_ok(wx.CommandEvent())
        assert d.target is not None and d.target == tf.Target("Northwind", 11) and d.target.occupied
    finally:
        d.Destroy()
    dlg = ChoresDialog(frame, "Hero")
    try:
        dlg.fallback.items = [tf.Target("Northwind", 11)]
        dlg.fallback.fill(0)
        dlg.toggle(dlg.fallback)
        assert dlg.fallback.items[0].occupied
        assert dlg.fallback.list.GetStrings() == ["Northwind, highland 11 - fires even if occupied"]
        dlg.toggle(dlg.fallback)
        assert not dlg.fallback.items[0].occupied
    finally:
        dlg.Destroy()
        frame.Destroy()
        del app


def test_a_turret_in_a_highland_our_gang_occupies_fires_too(world: type[Game]) -> None:
    t = Turrets(world, ours=[80], keeps=[95], held=[95])          # listed as "Sunfield Gang's Highland 95"
    out = chores.run(as_bot(t.g), fire_cfg(fire_turrets=[95]))
    assert t.fired == {95: "7 crystal turret"}, out
