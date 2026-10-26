# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""The app as a whole: every module imports, the translation template is up to date, script loops
are paced, and the bot is light on the CPU."""

import importlib
import json
import os
import pkgutil
import threading
import time
import tomllib

import pytest
from conftest import ROOT, Clock, data

import unclefarmer
from unclefarmer import catalog, i18n, paths, settings
from unclefarmer.engine import combat
from unclefarmer.engine.bot import Bot
from unclefarmer.engine.script import Runner

try:
    import wx  # noqa: F401
    HAVE_WX = True
except ImportError:
    HAVE_WX = False


def modules():
    return [m.name for m in pkgutil.walk_packages(unclefarmer.__path__, "unclefarmer.")]


@pytest.mark.parametrize("name", modules())
def test_module_imports(name):
    if (".gui" in name or name == "unclefarmer.__main__") and not HAVE_WX:
        pytest.skip("wxPython not installed")
    importlib.import_module(name)


def test_pyproject_has_the_apps_version():
    # release.py sets both; uv.lock records the pyproject one, so a mismatch also means a stale lock.
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        assert tomllib.load(f)["project"]["version"] == unclefarmer.__version__


def test_the_bot_keeps_its_old_names():
    from unclefarmer.engine import bot
    for name in bot.__all__:
        assert hasattr(bot, name)


def test_translation_template_is_up_to_date(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "LANG", str(tmp_path))
    pot, _count = i18n.write_template()
    _header, fresh = catalog.parse_po(open(pot, encoding="utf-8").read())
    _shipped_header, shipped = catalog.parse_po(open(f"{ROOT}/lang/unclefarmer.pot", encoding="utf-8").read())
    assert {msgid for msgid, _msgstr in fresh} == {msgid for msgid, _msgstr in shipped}, \
        "run: python -m unclefarmer --strings"


def test_a_quick_loop_turn_is_paced():
    # the test's own clock: a busy machine pausing between two real readings made this flaky
    c = Clock()
    assert Runner.loop_pause(c.now, c) == pytest.approx(1.0 / Runner.LOOP_TURNS)
    assert Runner.loop_pause(c.now - 2.0, c) == pytest.approx(0.01)


# ---------------------------------------------------------------- CPU
class World:
    """A pretend game behind the agent link: a busy town snapshot of real size, one monster,
    a 1.2 s global cooldown, walking at 4.1 tiles a second. Requests cost what the live agent
    measured (a snapshot 3.4 ms, other reads 0.4 ms) as waiting, not as our CPU."""

    GCD = 1.2

    def __init__(self):
        self.snapshot = json.dumps(data("town_snapshot.json")["snapshot"])
        self.casts, self.last_cast, self.mob_hp = [], 0.0, 10 ** 9
        self.held, self.pos, self.moved = set(), [60.0, 60.0], time.time()
        self.lock = threading.Lock()

    def me(self):
        now = time.time()
        if "d" in self.held:
            self.pos[0] += 4.1 * (now - self.moved)
        self.moved = now
        return {"id": 1, "name": "Hero", "hp": 95000, "maxhp": 100000, "x": self.pos[0], "y": self.pos[1],
                "sheet": {"level": 80}}

    def cast(self):
        with self.lock:
            t = time.time()
            if t - self.last_cast >= self.GCD:
                self.last_cast = t
                self.casts.append(t)
                self.mob_hp -= 1000

    def send(self, line):
        time.sleep(0.0003)
        w = line.split()
        if w[0] == "key":
            threading.Timer(0.1, self.cast).start()
            return "tapped"
        if w[0] == "keydown":
            self.held.add(w[1])
            return "down"
        if w[0] == "keyup":
            self.held.discard(w[1])
            return "up"
        return "mwagent 20" if w[0] == "version" else "ok"

    def json(self, line):
        if line == "mem":
            time.sleep(0.0034)
            s = json.loads(self.snapshot)          # the parse the real link does
            s["self"] = self.me()
            s["entities"].insert(0, {"id": -9, "kind": "monster", "name": "Orc", "hp": self.mob_hp,
                                     "maxhp": 10 ** 9, "x": 62.0, "y": 60.0})
            return s
        time.sleep(0.0004)
        if line.startswith("casts"):
            since = int(line.split()[1])
            with self.lock:
                return {"casts": [{"caster": 1, "t": int(t * 1000) + 100, "seq": 0, "skill": "Level 7 Arcane Shot"}
                                  for t in self.casts if int(t * 1000) + 100 > since and t + 0.1 <= time.time()]}
        return {"mem self": self.me(), "mem map": {"id": 5302},
                "mem ui": {"target": -9, "panel": 0, "cursor": 0, "attack_page": 0},
                "input": {"ok": True, "open": False}, "dialog": {"ok": True, "open": 0}}.get(
            line, [] if line in ("mem equipment", "mem bag", "mem bagfull") else {})


@pytest.fixture
def hunter():
    world = World()
    p = settings.load_profile("_test_")
    p.update(vitals_key="", mp_use=False, fight_timeout=8.0, points_key="")
    b = Bot(world, p, lambda *a, **k: None, threading.Event())
    b.potion = lambda kind: (None, None)
    b.select_target = lambda m: True
    b.reach = lambda: 18.0
    b.clear_shot = lambda *a: True
    sb = combat.SkillBook.__new__(combat.SkillBook)
    sb.__dict__.update(b=b, ok=True, used={}, pending={}, gaps=[], marked_at=0.0, last_cast=0.0, last_press=0.0,
                       can_flip=True, confirm=True, press_confirmed=True, cast_seen=0, cls="Hunter",
                       buff_tried={}, dispel_try=None, no_dispel=None, pending_target={}, landed={},
                       slots={0: [dict({s["name"]: s for s in combat.knowledge()["Hunter"]}["Arcane Shot"],
                                       key="j", still="j")]})
    b.skillbook = sb
    return world, b


def cpu_share(job):
    """Our process's CPU over the job, as a share of one core."""
    c, t = time.process_time(), time.time()
    job()
    return (time.process_time() - c) / (time.time() - t)


@pytest.mark.slow
def test_fighting_is_light_on_the_cpu(hunter):
    world, b = hunter
    share = cpu_share(lambda: b._fight({"id": -9, "kind": "monster", "name": "Orc", "hp": world.mob_hp,
                                        "maxhp": 10 ** 9, "x": 62.0, "y": 60.0}))
    assert len(world.casts) >= 4, "the fight didn't run"
    assert share < 0.05, "%.1f%% of a core" % (100 * share)      # measured about 1%


@pytest.mark.slow
def test_walking_is_light_on_the_cpu(hunter):
    world, b = hunter
    share = cpu_share(lambda: b.walk_axis(0, 90.0, timeout=8.0))
    assert world.pos[0] > 80, "the walk didn't run"
    assert share < 0.05, "%.1f%% of a core" % (100 * share)      # measured under 1%
