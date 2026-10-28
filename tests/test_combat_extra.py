# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The branches of the skill picker tests/test_skill_picking.py, test_skill_choices.py and the
fight tests in test_fighting.py / test_app.py / test_approach.py / test_wrong_target.py don't reach.

Most are exercised here directly on a SkillBook built with __new__ (the same stand-in those tests
use), so no real game clock or fight is needed.
"""

from __future__ import annotations


import pytest

from unclefarmer.clock import CLOCK
from unclefarmer import settings
from unclefarmer.engine import combat
from conftest import as_parts, stand_in, Clock
from collections.abc import Callable, Sequence
from typing import Any
from unclefarmer.engine.combat import SkillBook


DEFAULTS = settings.PROFILE_DEFAULTS


class TLink:
    """A pretend agent link: the version string and the two memory reads load() makes."""

    def __init__(self, version: str="mwagent 28", skills: dict[str, Any] | None=None, modes: dict[str, bool] | None=None, casts: object = None) -> None:
        self.version = version
        self.skills = skills
        self.modes = modes
        self.casts = casts

    def send(self, line: str) -> str:
        return self.version if line == "version" else "tapped"

    def json(self, line: str) -> Any:              # an agent answer: any JSON, as the real link
        if line == "mem skills":
            return self.skills if self.skills is not None else {}
        if line == "mem modes":
            return self.modes if self.modes is not None else {}
        if line.startswith("casts"):
            return self.casts if self.casts is not None else {}
        return {}


class TB:
    """The few Bot methods the SkillBook uses."""

    def __init__(self, p: dict[str, bool] | None=None, a: TLink | None=None) -> None:
        self.p = dict(DEFAULTS, **(p or {}))
        self.a = a
        self.lines: list[str] = []
        self.resting = False

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def attackers(self) -> set[int]:
        return set()

    def ui(self) -> dict[str, Any]:
        return {"attack_page": 0}

    page_turns = 0

    def note_page_turn(self) -> None:
        self.page_turns += 1

    def wait_keys_free(self) -> None:
        pass

    def sleep(self, seconds: float) -> None:
        pass


def sb(**attrs: Any) -> SkillBook:
    s = combat.SkillBook.__new__(combat.SkillBook)
    s.__dict__.update(
        b=TB(),
        ok=True, used={}, pending={}, pending_target={}, refused={}, gaps=[],
        marked_at=0.0, last_cast=0.0, last_press=0.0, press_confirmed=True, confirm=False,
        can_flip=True, cast_seen=0, cls="Warrior", combos=None, buff_tried={},
        dispel_try=None, no_dispel=None, landed={}, slots={},
        pts=None, pts_at=0.0, pts_delta=0, target_id=None, last_gcd_cast=None,
    )
    s.__dict__.update(attrs)
    return s


def pinned(s: SkillBook, page: int=0, at: float=1000.0) -> SkillBook:
    """Make page() answer without touching self.b.ui()."""
    s._page, s._page_at = page, at
    return s


def skill(name: str, **kw: Any) -> dict[str, Any]:
    base = {"name": name, "key": "j", "still": "j", "page": 0, "use": "auto", "range": None,
            "cd": 0, "attack": 10}
    base.update(kw)
    return base


class Combos:
    def __init__(self, releases: Sequence[dict[str, tuple[str, list[str]]]]=(),
                 cd_less: Callable[[str], float] | None=None) -> None:
        self.releases = list(releases)
        self._cd_less: Callable[[str], float] = cd_less or (lambda name: 0.0)

    def of(self, kind: str) -> list[dict[str, tuple[str, list[str]]]]:
        return self.releases if kind == "release" else []

    def cd_less(self, name: str) -> float:
        return self._cd_less(name)

    def odd_gap(self, prev_name: str, prev_t: float) -> bool:
        return False

    def cast(self, *a: object, **k: object) -> None:
        pass


# ---------------------------------------------------------------- knowledge / talent_texts
def test_knowledge_survives_an_unreadable_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(combat, "_know", None)
    monkeypatch.setattr(combat.paths, "DATA", "no_such_dir_hopefully")
    assert combat.knowledge() == {}


def test_talent_texts_survive_an_unreadable_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(combat, "_talents", None)
    monkeypatch.setattr(combat.paths, "DATA", "no_such_dir_hopefully")
    assert combat.talent_texts() == {}


# ---------------------------------------------------------------- load() branches
def test_load_with_an_unparsable_version() -> None:
    bot = TB(a=TLink(version="nonsense"))
    book = combat.SkillBook(as_parts(bot))
    assert book.agent_version == 0
    assert book.confirm is False
    assert book.ok is False


def test_load_without_the_combat_memory() -> None:
    bot = TB(a=TLink(skills={"talents": []}))
    book = combat.SkillBook(as_parts(bot))
    assert book.ok is False


def test_load_skips_out_of_range_slots() -> None:
    skills = {"combat": [{"id": 1, "name": "Bash"}],
              "attack_keys": {"0": 1, "21": 1, "1": 1},
              "talents": []}
    bot = TB(a=TLink(skills=skills, modes={"swap_attack": False}))
    book = combat.SkillBook(as_parts(bot))
    assert book.ok is True
    assert [s["name"] for s in book.slots[0]] == ["Bash"]


# ---------------------------------------------------------------- follow_key()
def test_follow_key_needs_the_base_attack_on_the_page_up() -> None:
    base = skill("Attack", role="base_attack")
    s = pinned(sb(slots={0: [base]}), page=0)
    assert s.follow_key() == "j"


def test_follow_key_none_when_the_base_attack_is_on_the_other_page() -> None:
    s = pinned(sb(slots={1: [skill("Attack", role="base_attack")]}), page=0)
    assert s.follow_key() is None


# ---------------------------------------------------------------- flip_to()
def test_flip_to_without_caps_lock() -> None:
    s = pinned(sb(can_flip=False))
    assert s.flip_to(1) is False


def test_flip_to_already_on_the_wanted_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(CLOCK, "time", stand_in(CLOCK.time, lambda: 1000.0))
    s = pinned(sb(), page=1)
    assert s.flip_to(1) is True


def test_flip_to_disables_caps_when_the_key_is_refused() -> None:
    link = TLink()
    link.send = lambda line: "refused"
    tb = TB(a=link)
    s = pinned(sb(b=tb), page=0)
    assert s.flip_to(1) is False
    assert s.can_flip is False
    assert any("switch the skill page" in line for line in tb.lines)


def test_flip_to_gives_up_when_the_page_will_not_change() -> None:
    s = pinned(sb(), page=0)
    s.b.a = TLink()          # send -> "tapped", but ui stays page 0
    assert s.flip_to(1) is False
    assert s.can_flip is True


# ---------------------------------------------------------------- ready() / ready_by()
def test_ready_false_while_refused() -> None:
    s = sb(refused={"Bash": 5000.0})
    assert s.ready({"name": "Bash", "cd": 0}, 1000.0) is False


def test_ready_false_after_a_recent_press() -> None:
    s = sb(pending={"Bash": 1000.0})
    assert s.ready({"name": "Bash", "cd": 0}, 1000.0) is False


def test_ready_by_none_for_a_skill_not_on_a_key() -> None:
    s = sb(slots={})
    assert s.ready_by("Bash", 1000.0) is False


def test_ready_by_false_for_a_skill_set_to_never() -> None:
    s = sb(slots={0: [skill("Bash", use="never")]})
    assert s.ready_by("Bash", 1000.0) is False


# ---------------------------------------------------------------- cut_cooldown()
def test_cut_cooldown_shortens_the_group() -> None:
    s = sb(used={"Bash": 100.0, "Cleave": 50.0},
           slots={0: [skill("Bash", text="Share CD with Cleave")]})
    s.cut_cooldown("Bash", 5.0)
    assert s.used["Bash"] == 95.0
    assert s.used["Cleave"] == 45.0


def test_cut_cooldown_ignores_unknown_skills() -> None:
    s = sb(used={})
    s.cut_cooldown("Nope", 5.0)          # no cooldown recorded: nothing to cut
    assert s.used == {}


# ---------------------------------------------------------------- uses_points()
def test_uses_points_for_a_rage_spender() -> None:
    s = sb(slots={0: [skill("Execute", rage_cost=10)]})
    assert s.uses_points() is True


def test_uses_points_for_a_marked_shot() -> None:
    s = sb(slots={0: [skill("Marked Shot")]})
    assert s.uses_points() is True


def test_uses_points_for_fire_elements() -> None:
    s = sb(slots={0: [skill("Fireball")]},
           combos=Combos(releases=[{"release": ("Ignition", ["Fireball"])}]))
    assert s.uses_points() is True


def test_uses_points_false_for_a_plain_attack() -> None:
    s = sb(slots={0: [skill("Bash")]})
    assert s.uses_points() is False


# ---------------------------------------------------------------- take_points() / points()
def test_take_points_ignores_an_answer_without_a_count() -> None:
    s = sb(pts=5, pts_at=900.0, pts_delta=2)
    s.take_points({"points": {"age_ms": 100}})
    assert (s.pts, s.pts_delta) == (5, 2)


def test_take_points_folds_in_a_new_answer(clock: Clock) -> None:
    s = sb(pts=None)
    s.take_points({"points": {"n": 7, "age_ms": 500}})
    assert s.pts == 7 and s.pts_delta == 0
    assert s.pts_at == pytest.approx(clock.now - 0.5)


def test_take_points_keeps_the_delta_for_a_repeated_answer(clock: Clock) -> None:
    s = sb(pts=7, pts_at=clock.now, pts_delta=3)
    s.take_points({"points": {"n": 7, "age_ms": 0}})
    assert s.pts_delta == 3


def test_points_counts_our_casts_since_the_reading(clock: Clock) -> None:
    s = sb(pts=5, pts_at=clock.now, pts_delta=3)
    assert s.points(clock.now) == 8
    s.pts_delta = -10
    assert s.points(clock.now) == 0


# ---------------------------------------------------------------- affordable()
def test_affordable_true_for_a_free_skill() -> None:
    s = sb()
    assert s.affordable({"name": "Bash"}, 1000.0) is True


def test_affordable_compares_against_a_fresh_reading(clock: Clock) -> None:
    s = sb(pts=10, pts_at=clock.now, pts_delta=0)
    assert s.affordable(skill("Execute", rage_cost=5), clock.now) is True
    assert s.affordable(skill("Execute", rage_cost=20), clock.now) is False


# ---------------------------------------------------------------- refuse()
def test_refuse_short_wait_for_a_free_skill(clock: Clock) -> None:
    s = sb(slots={0: [skill("Bash")]})
    s.refuse("Bash", clock.now)
    assert s.refused["Bash"] == pytest.approx(clock.now + combat.REFUSED_WAIT)


def test_refuse_long_wait_for_a_rage_spender(clock: Clock) -> None:
    s = sb(slots={0: [skill("Execute", rage_cost=10)]})
    s.refuse("Execute", clock.now)
    assert s.refused["Execute"] == pytest.approx(clock.now + combat.REFUSED_WAIT_RAGE)


# ---------------------------------------------------------------- read_casts()
def test_read_casts_does_nothing_without_confirmation() -> None:
    s = sb(confirm=False)
    s.read_casts(1)
    assert s.used == {}


def test_read_casts_ignores_a_non_dict_answer() -> None:
    s = sb(confirm=True, b=TB(a=TLink(casts=[])))
    s.read_casts(1)
    assert s.used == {}


def test_read_casts_trims_the_landed_map() -> None:
    landed = {("Bash", -i): float(i) for i in range(200)}
    cast = {"caster": 1, "t": 500000, "skill": "Level 1 Bash", "hits": [[-999, 3, 100]]}
    s = sb(confirm=True, landed=landed, b=TB(a=TLink(casts={"casts": [cast]})))
    s.read_casts(1)
    assert len(s.landed) == 101          # 201 - the 100 oldest dropped
    assert ("Bash", -999) in s.landed


def test_read_casts_marks_clear_the_marks(clock: Clock) -> None:
    cast = {"caster": 1, "t": 500000, "skill": "Level 1 Marked Shot", "hits": [[-5, 3, 100]]}
    s = sb(confirm=True, pts=5, pts_at=0.0, pts_delta=0, b=TB(a=TLink(casts={"casts": [cast]})))
    s.read_casts(1)
    assert s.pts_delta == -5


def test_read_casts_releases_fire_elements(clock: Clock) -> None:
    cast = {"caster": 1, "t": 500000, "skill": "Level 1 Fireball", "hits": [[-5, 3, 100]]}
    combos = Combos(releases=[{"release": ("Ignition", ["Fireball"])}])
    s = sb(confirm=True, pts=2, pts_at=0.0, pts_delta=0, combos=combos,
           b=TB(a=TLink(casts={"casts": [cast]})))
    s.read_casts(1)
    assert s.pts_delta == 1              # one hit touched the target


def test_read_casts_adds_rage_gained() -> None:
    cast = {"caster": 1, "t": 500000, "skill": "Level 1 Bash", "hits": [[-5, 3, 100]]}
    s = sb(confirm=True, pts=2, pts_at=0.0, pts_delta=0,
           slots={0: [skill("Bash", rage_gain=3)]}, b=TB(a=TLink(casts={"casts": [cast]})))
    s.read_casts(1)
    assert s.pts_delta == 3


# ---------------------------------------------------------------- debuff_worth()
def test_debuff_worth_for_more_damage_always() -> None:
    assert combat.SkillBook.debuff_worth({"text": "makes the target take more damage"}, 90) == 2


def test_debuff_worth_for_accuracy_only_when_hurting() -> None:
    assert combat.SkillBook.debuff_worth({"text": "lowers the target's accuracy"}, 60) == 1
    assert combat.SkillBook.debuff_worth({"text": "lowers the target's accuracy"}, 90) == 0


def test_debuff_worth_nothing_otherwise() -> None:
    assert combat.SkillBook.debuff_worth({"text": "a grand buff"}, 10) == 0


# ---------------------------------------------------------------- shield()
def test_shield_that_stops_attacks() -> None:
    assert combat.SkillBook.shield({"role": "defensive",
                                    "text": "reducing 50% of all damage taken, cannot attack"}) == "attack"


# ---------------------------------------------------------------- emergency() big heal
def test_emergency_uses_a_big_heal_when_no_shield(clock: Clock) -> None:
    heal = skill("Light of Miracles", role="heal", targets=0, text="heals a lot")
    s = pinned(sb(slots={0: [heal]}), page=0)
    s.b.p["emergency_below"] = 50
    r = s.emergency({"hp": 30000, "maxhp": 100000}, clock.now)
    assert r == ("j", "Light of Miracles")


# ---------------------------------------------------------------- press_support()
def test_press_support_none_when_it_cannot_flip() -> None:
    s = pinned(sb(can_flip=False), page=0)
    assert s.press_support(skill("Heal", page=1), 1000.0) is None


# ---------------------------------------------------------------- dispel_now()
def test_dispel_now_off(clock: Clock) -> None:
    s = sb(b=TB(p={"use_dispel": False}))
    assert s.dispel_now({"vitals": {"debuffs": 2, "age_ms": 0}}, clock.now) is None


def test_dispel_now_with_no_debuffs(clock: Clock) -> None:
    s = sb()
    assert s.dispel_now({"vitals": {"debuffs": 0, "age_ms": 0}}, clock.now) is None


def test_dispel_now_presses_a_self_dispel(clock: Clock) -> None:
    d = skill("Purification", role="dispel", text="removes a curse from 1 ally")
    s = pinned(sb(slots={0: [d]}), page=0)
    me = {"vitals": {"debuffs": 1, "age_ms": 0}}
    assert s.dispel_now(me, clock.now) == ("j", "Purification")
    assert s.dispel_try == (clock.now, 1)


def test_dispel_now_gives_up_on_a_debuff_it_cannot_remove(clock: Clock) -> None:
    s = sb(no_dispel=1)
    me = {"vitals": {"debuffs": 1, "age_ms": 0}}
    assert s.dispel_now(me, clock.now) is None


def test_dispel_now_records_a_failed_dispel(clock: Clock) -> None:
    s = sb(dispel_try=(clock.now - 5, 2), slots={})
    me = {"vitals": {"debuffs": 3, "age_ms": 0}}
    assert s.dispel_now(me, clock.now) is None
    assert s.no_dispel == 3
    assert s.dispel_try is None


def test_dispel_now_without_a_dispel_skill(clock: Clock) -> None:
    s = sb(slots={0: [skill("Bash")]})
    me = {"vitals": {"debuffs": 1, "age_ms": 0}}
    assert s.dispel_now(me, clock.now) is None


# ---------------------------------------------------------------- buff_now()
def test_buff_now_off(clock: Clock) -> None:
    s = sb(b=TB(p={"use_buffs": False}))
    assert s.buff_now({}, clock.now) is None


def test_buff_now_skips_a_press_that_was_never_cast(clock: Clock) -> None:
    b = skill("Battle Cry", role="self_buff", text="for 2 minutes")
    s = sb(slots={0: [b]}, buff_tried={"Battle Cry": clock.now - 25}, used={})
    assert s.buff_now({}, clock.now) is None


def test_buff_now_refreshes_a_long_buff_the_game_says_is_gone(clock: Clock) -> None:
    b = skill("Battle Cry", role="self_buff", text="for 2 minutes")
    s = pinned(sb(slots={0: [b]}, used={"Battle Cry": clock.now - 5}), page=0)
    me = {"vitals": {"buffs": 0, "age_ms": 0}}
    assert s.buff_now(me, clock.now) == ("j", "Battle Cry")


def test_buff_now_keeps_a_long_buff_still_up(clock: Clock) -> None:
    b = skill("Battle Cry", role="self_buff", text="for 2 minutes")
    s = pinned(sb(slots={0: [b]}, used={"Battle Cry": clock.now - 5}), page=0)
    me = {"vitals": {"buffs": 1, "age_ms": 0}}
    assert s.buff_now(me, clock.now) is None


def test_buff_now_skips_a_short_buff_outside_a_fight(clock: Clock) -> None:
    b = skill("Bull's Eye", role="self_buff", text="for 10 seconds")
    s = sb(slots={0: [b]}, used={"Bull's Eye": clock.now - 5})
    assert s.buff_now({}, clock.now) is None


def test_buff_now_uses_a_short_buff_at_the_start_of_a_hard_fight(clock: Clock) -> None:
    b = skill("Bull's Eye", role="self_buff", text="for 10 seconds")
    s = pinned(sb(slots={0: [b]}, used={"Bull's Eye": clock.now - 15}), page=0)
    assert s.buff_now({}, clock.now, fight=(True, 1)) == ("j", "Bull's Eye")


# ---------------------------------------------------------------- support()
def test_support_none_when_not_ok() -> None:
    s = sb(ok=False)
    assert s.support({}, 1000.0) is None


def test_support_returns_an_emergency(clock: Clock) -> None:
    s = sb()
    s.emergency = lambda me, now: ("k", "Formless")
    assert s.support({}, clock.now) == ("k", "Formless")


def test_support_returns_a_heal(clock: Clock) -> None:
    s = sb()
    s.emergency = lambda me, now: None
    s.heal_now = lambda me, now: ("k", "Heal")
    assert s.support({}, clock.now) == ("k", "Heal")


def test_support_waits_out_the_global_cooldown(clock: Clock) -> None:
    s = pinned(sb(slots={0: [skill("Battle Cry", role="self_buff", text="for 2 minutes")]}),
               page=0)
    s.emergency = lambda me, now: None
    s.heal_now = lambda me, now: None
    s.confirm = True
    s.press_confirmed = True
    s.last_cast = clock.now
    assert s.support({}, clock.now) == "wait"


# ---------------------------------------------------------------- ready_in()
def test_ready_in_zero_without_confirmation() -> None:
    s = sb(confirm=False)
    assert s.ready_in(1000.0) == 0.0


def test_ready_in_uses_retry_for_an_unconfirmed_press() -> None:
    s = sb(confirm=True, press_confirmed=False, last_press=1000.0)
    assert s.ready_in(1000.0) == pytest.approx(combat.RETRY)


# ---------------------------------------------------------------- pressed()
def test_pressed_without_confirmation_records_the_cooldowns() -> None:
    s = sb(confirm=False)
    s.pressed({"name": "Bash"}, 1234.0)
    assert s.used == {"Bash": 1234.0}


# ---------------------------------------------------------------- heal_now()
def _healer() -> dict[str, Any]:
    return skill("Heal", role="heal", targets=1, cast=0, cd=0)


def test_heal_now_without_a_healer() -> None:
    s = sb(slots={})
    assert s.heal_now({"hp": 10, "maxhp": 100}, 1000.0) is None


def test_heal_now_presses_a_quick_heal(clock: Clock) -> None:
    s = pinned(sb(slots={0: [_healer()]}), page=0)
    assert s.heal_now({"hp": 40, "maxhp": 100}, clock.now) == ("j", "Heal")


def test_heal_now_not_when_hp_is_high(clock: Clock) -> None:
    s = sb(slots={0: [_healer()]})
    assert s.heal_now({"hp": 90, "maxhp": 100}, clock.now) is None


def test_heal_now_waits_while_resting(clock: Clock) -> None:
    s = pinned(sb(slots={0: [_healer()]}), page=0)
    s.b.resting = True
    s.b.p["rest_until"] = 90
    assert s.heal_now({"hp": 10, "maxhp": 100}, clock.now) == ("j", "Heal")


def test_heal_now_emergency_only_skill_needs_low_hp(clock: Clock) -> None:
    em = skill("Heal", role="heal", targets=1, cast=0, cd=0, use="emergency")
    s = sb(slots={0: [em]})
    assert s.heal_now({"hp": 70, "maxhp": 100}, clock.now) is None


def test_heal_now_waits_out_the_global_cooldown(clock: Clock) -> None:
    s = sb(slots={0: [_healer()]}, confirm=True, press_confirmed=True, last_cast=clock.now)
    assert s.heal_now({"hp": 40, "maxhp": 100}, clock.now) == "wait"


def test_heal_now_none_while_the_heal_is_on_cooldown(clock: Clock) -> None:
    s = sb(slots={0: [_healer()]}, pending={"Heal": clock.now})
    assert s.heal_now({"hp": 40, "maxhp": 100}, clock.now) is None


# ---------------------------------------------------------------- pick()
def _snap(me_hp: int=90000, skills: None=None, tgt_max: int=4000) -> tuple[dict[str, Any], dict[str, Any]]:
    me = {"id": 1, "x": 0, "y": 0, "hp": me_hp, "maxhp": 100000}
    tgt = {"id": -9, "x": 2, "y": 0, "hp": tgt_max, "maxhp": tgt_max, "name": "Orc",
           "kind": "monster", "side": 1}
    entities = [me, tgt]
    snap = {"self": me, "entities": entities}
    return snap, tgt


def test_pick_none_when_not_ok() -> None:
    s = sb(ok=False)
    snap, tgt = _snap()
    assert s.pick(snap, tgt) is None


def test_pick_returns_support_first() -> None:
    s = pinned(sb(slots={0: [skill("Bash")]}), page=0)
    s.support = lambda me, now, fight=None: ("k", "Heal")
    snap, tgt = _snap()
    assert s.pick(snap, tgt) == ("k", "Heal")


def test_pick_none_without_any_ready_skill() -> None:
    s = pinned(sb(slots={}), page=0)
    s.support = lambda me, now, fight=None: None
    snap, tgt = _snap()
    assert s.pick(snap, tgt) is None


def test_pick_uses_a_shield_when_hp_is_low() -> None:
    formless = skill("Formless", role="defensive", text="reducing 50% of all damage taken, cannot attack", page=0)
    s = pinned(sb(slots={0: [formless]}), page=0)
    s.support = lambda me, now, fight=None: None
    snap, tgt = _snap(me_hp=29000)
    assert s.pick(snap, tgt) == ("j", "Formless")


def test_pick_jumps_back_when_a_monster_is_on_top() -> None:
    dis = skill("Disengage", role="mobility", text="jump backward out of trouble", page=0)
    s = pinned(sb(slots={0: [dis]}, cls="Hunter"), page=0)
    s.support = lambda me, now, fight=None: None
    snap, tgt = _snap(me_hp=50000)
    assert s.pick(snap, tgt) == ("j", "Disengage")


def test_pick_presses_a_player_ready_self_buff() -> None:
    buff = skill("Battle Cry", role="self_buff", use="ready", text="for 2 minutes", page=0)
    s = pinned(sb(slots={0: [buff]}), page=0)
    s.support = lambda me, now, fight=None: None
    snap, tgt = _snap()
    assert s.pick(snap, tgt) == ("j", "Battle Cry")


def test_pick_shoots_marked_targets_with_marked_shot(clock: Clock) -> None:
    marked = skill("Marked Shot", role="aoe", attack=50, page=0)
    s = pinned(sb(slots={0: [marked]}, cls="Hunter", pts=3, pts_at=clock.now, pts_delta=0),
               page=0)
    s.support = lambda me, now, fight=None: None
    snap, tgt = _snap()
    assert s.pick(snap, tgt) == ("j", "Marked Shot")


def test_pick_none_when_nothing_fits() -> None:
    dot = skill("Sear", role="dot", page=0, text="burns for 5 seconds")
    s = pinned(sb(slots={0: [dot]}), page=0)
    s.support = lambda me, now, fight=None: None
    snap, tgt = _snap()
    assert s.pick(snap, tgt) is None


# ---------------------------------------------------------------- _press_pick() fallback
def test_press_pick_falls_back_to_the_same_kind_on_this_page() -> None:
    s = pinned(sb(can_flip=False), page=0)
    choice = skill("Bash", role="single_target", page=1)
    same = skill("Cleave", role="single_target", page=0)
    r = s._press_pick(choice, [same], 0, 1000.0, lambda x: True)
    assert r == ("j", "Cleave")


def test_press_pick_none_when_no_fallback_exists() -> None:
    s = pinned(sb(can_flip=False), page=0)
    choice = skill("Bash", role="single_target", page=1)
    assert s._press_pick(choice, [], 0, 1000.0, lambda x: True) is None
