"""Shared pieces for the tests: the app on the import path, a clock the tests move by hand, and a
pretend game that casts what the bot presses the way the real one does (cooldowns, a global
cooldown, channels reported when they end, hit results with critical hits as agent v28 sends them).

Nothing here talks to a real game; the tests run anywhere with Python and numpy.
"""

from __future__ import annotations

import faulthandler
import itertools
import json
import os
import shutil
import sys

import pytest

os.environ["UNCLEFARMER_TESTING"] = "1"
faulthandler.enable()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "tests", "data")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from unclefarmer import paths, settings  # noqa: E402
from unclefarmer.clock import CLOCK  # noqa: E402
from unclefarmer.engine import combat  # noqa: E402
from unclefarmer.link import GameLink  # noqa: E402
from collections.abc import Callable, Generator, Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, ParamSpec, TypeVar, TypeVarTuple, cast

if TYPE_CHECKING:
    import simgame
    from unclefarmer.clock import Waitable
    from unclefarmer.engine.bot import Bot as RealBot
    from unclefarmer.engine.botparts.parts import Parts
    from unclefarmer.engine.runner import Run

ME = 1001


def as_bot(stand_in: object) -> RealBot:
    """A test's stand-in bot handed to engine code that takes the Bot. A stand-in has the part of the
    Bot's surface the code under test uses (anything else fails the test loudly, as an
    AttributeError), so it can't be a Bot to the type checker: this one cast says so, for every test."""
    return cast("RealBot", stand_in)


T = TypeVar("T")


def as_kind(kind: type[T], stand_in: object) -> T:
    """as_bot for any other stand-in: a test's own object in place of one of kind (a tally, a skill
    book, a link) with the part of kind's surface the code under test uses."""
    return cast(T, stand_in)


def as_parts(stand_in: object) -> Parts:
    """as_bot for code that takes the Bot's declared surface (botparts.parts.Parts), as SkillBook does."""
    return cast("Parts", stand_in)


P = ParamSpec("P")
R = TypeVar("R")


def stand_in(real: Callable[P, R], fake: Callable[P, R]) -> Callable[P, R]:
    """A test's replacement for a function or method, shaped like the real one: the fake takes its
    parameter types from it, and the type checker says when the two no longer fit."""
    return fake


def returning(value: R) -> Callable[..., R]:
    """A stand-in that takes anything and gives back value."""
    def stand_in(*_args: object, **_kw: object) -> R:
        return value
    return stand_in


def failing(message: str) -> Callable[..., NoReturn]:
    """A stand-in that must never be called: calling it fails the test with message."""
    def stand_in(*_args: object, **_kw: object) -> NoReturn:
        pytest.fail(message)
    return stand_in


def raising_with(exc: BaseException) -> Callable[..., NoReturn]:
    """A stand-in that raises exc whatever it is called with."""
    def stand_in(*_args: object, **_kw: object) -> NoReturn:
        raise exc
    return stand_in


class JsonOnly:
    """An agent link whose JSON answers come from answer(line); every plain request says "ok"."""

    def __init__(self, answer: Callable[[str], Any]) -> None:
        self.answer = answer

    def send(self, line: str) -> str:
        return "ok"

    def json(self, line: str) -> Any:
        return self.answer(line)


Ts = TypeVarTuple("Ts")


class HostStub:
    """messages.Host with every part doing nothing: a test's host overrides the parts it uses."""

    def run_for(self, pid: int) -> Run | None:
        return None

    def call(self, fn: Callable[[*Ts], T], *args: *Ts) -> T | str | None:
        return None

    def start_bot(self, pid: int, plan: str | None) -> str | None:
        return None

    def stop_bot(self, pid: int) -> None:
        pass

    def log(self, line: str, speak: bool = False, who: str | None = None, private: bool = False) -> None:
        pass

    def character(self, pid: int) -> str | None:
        return None

    def errand_for(self, pid: int, text: str, title: str, tell: Callable[[str], None] | None = None,
                   follows: str | None = None) -> str | None:
        return None

    def follow_for(self, pid: int, leader: str, tell: Callable[[str], None] | None = None) -> str | None:
        return None

    def unfollow(self, pid: int) -> str | None:
        return None


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--soak", action="store_true", default=False,
                     help="run the long soak tests (tests/test_soak.py): a pretend game for hours of game time")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Soak tests run only with --soak: minutes of real time each, too long for every commit.
    (A -m "not soak" in addopts would be replaced by check.py --quick's own -m.)"""
    if config.getoption("--soak"):
        return
    skip = pytest.mark.skip(reason="a soak test: run with --soak")
    for item in items:
        if "soak" in item.keywords:
            item.add_marker(skip)


_app_dirs = itertools.count()


@pytest.fixture(scope="session")
def _user_base(tmp_path_factory: pytest.TempPathFactory) -> Generator[str, None, None]:
    """One folder for the session's per-test app folders; it all goes at the end."""
    base = tmp_path_factory.mktemp("app")
    yield str(base)
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def allow_ignored_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that deliberately exercises a best-effort fallback rather than hiding a bug."""
    monkeypatch.delenv("UNCLEFARMER_TESTING", raising=False)


@pytest.fixture(autouse=True)
def user_dirs(_user_base: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every folder the app writes to, inside this test's own temp folder.

    The app is portable and writes next to itself (plans, profiles, logs, settings, message
    files). A test that forgets to point one of those at a temp folder used to write into the
    checkout: harmless for git (they are ignored) but it pollutes the developer's own data.
    Individual test files can still redirect a folder further (a test's monkeypatch runs after
    this one)."""
    app = os.path.join(_user_base, str(next(_app_dirs)))
    for name, sub in (("LOGS", "logs"), ("PLANS", "plans"), ("SCRIPTS", "scripts"), ("PROFILES", "profiles")):
        monkeypatch.setattr(paths, name, os.path.join(app, sub))
        os.makedirs(os.path.join(app, sub))
    monkeypatch.setattr(paths, "SETTINGS", os.path.join(app, "settings.json"))
    from unclefarmer import messages          # imported here: conftest must stay cheap to import
    monkeypatch.setattr(messages, "MESSAGES_DIR", os.path.join(app, "messages"))
    monkeypatch.setattr(messages, "REPLIES_FILE", os.path.join(app, "messages", "replies.txt"))
    monkeypatch.setattr(messages, "GOODBYES_FILE", os.path.join(app, "messages", "goodbyes.txt"))


@pytest.fixture
def sim_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Callable[..., simgame.SimGame], None, None]:
    """A pretend game (tests/simgame.py) maker, and the app as the player runs it: best-effort
    failures carried on from (not raised), its agent folder and results inside the test's own
    folder, the farming day's stop off (a game night here crosses it in seconds)."""
    import simgame                   # imported here: conftest must stay cheap to import
    from unclefarmer import jsonio, stats
    monkeypatch.delenv("UNCLEFARMER_TESTING", raising=False)
    monkeypatch.setattr(paths, "AGENT_RUN", str(tmp_path / "run"))
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path / "results"))
    jsonio.save(paths.SETTINGS, {"daily_stop": False})
    games: list[simgame.SimGame] = []

    def new_game(seed: int = 7, map_id: int = 1503, monsters: int = 12) -> simgame.SimGame:
        g = simgame.SimGame(paths.AGENT_RUN, map_id=map_id, seed=seed, monsters=monsters)
        games.append(g)
        return g
    yield new_game
    for g in games:
        g.close()


def data(name: str) -> Any:
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def skill_info() -> dict[str, dict[str, Any]]:
    """Every class skill from the game's own texts (data/skills.json), by name."""
    return {s["name"]: s for rows in combat.knowledge().values() for s in rows}


class Clock:
    """The bot's clock for code under test: moves only when a test moves it."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def wait(self, event: Waitable, timeout: float | None = None) -> bool:
        if event.is_set():
            return True
        if hasattr(event, "clock"):
            return event.wait(timeout)
        if timeout:
            self.now += timeout
        return False


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    monkeypatch.setattr(CLOCK, "sleep", c.sleep)
    monkeypatch.setattr(CLOCK, "wait", c.wait)
    return c


class Game:
    """The game's side of a fight: the agent link the SkillBook talks to.

    skills  the skill names on the attack keys, in key order
    talents activated talent names (one more, on the other path, is listed but not activated)
    crit    crit(name, n) -> True when the n-th cast (of any skill) is a critical hit
    resets  {trigger skill or "*": (talent, skill)}: a crit of trigger ends that skill's cooldown,
            as the talent's text says the game does
    """

    def __init__(self, clock: Clock, skills: Sequence[str], talents: Sequence[str] = (), version: int = 28, gcd: float = 1.2,
                 crit: Callable[[str, int], bool] | None = None, resets: dict[str, tuple[str, str]] | None = None,
                 cd_less: dict[str, float] | None = None) -> None:
        self.clock, self.skills, self.talents, self.version, self.gcd = clock, list(skills), list(talents), version, gcd
        self.crit: Callable[[str, int], bool] = crit or (lambda name, n: False)
        self.resets = resets or {}
        self.cd_less = cd_less or {}
        self.info = skill_info()
        self.last: dict[str, float] = {}
        self.gcd_end = 0.0
        self.casts: list[dict[str, Any]] = []
        self.log: list[str] = []          # names cast, "*" marks a critical hit
        self.n = 0

    # the agent link
    def send(self, line: str) -> str:
        return "mwagent %d" % self.version if line == "version" else "tapped"

    def json(self, line: str) -> Any:
        if line == "mem skills":
            return {"combat": [{"id": i + 1, "name": n} for i, n in enumerate(self.skills)],
                    "attack_keys": {str(i + 1): i + 1 for i in range(len(self.skills))},
                    "talents": [{"id": 900 + i, "b": 1, "name": t} for i, t in enumerate(self.talents)]
                    + [{"id": 999, "b": 0, "name": "Aspect of the Monkey"}]}
        if line == "mem modes":
            return {"swap_attack": True}
        if line.startswith("casts"):
            since = int(line.split()[1])
            return {"casts": [c for c in self.casts if since < c["t"] <= self.clock.now * 1000]}
        return {}

    # what a key press does in the game
    def press(self, name: str) -> bool:
        now = self.clock.now
        s = self.info[name]
        cd = max(0.0, (s.get("cd") or 0) - self.cd_less.get(name, 0))
        if (now < self.gcd_end and not s.get("no_gcd")) or now - self.last.get(name, -1e9) < cd:
            return False
        self.n += 1
        t = now + (s.get("cast") or 0) + 0.2        # reported a round trip later, after any channel
        self.last[name] = t
        if not s.get("no_gcd"):
            half = name == "Leap Slash" and "Powerful Leap Attack" in self.talents
            self.gcd_end = t + self.gcd * (0.5 if half else 1.0)
        crit = self.crit(name, self.n)
        row: dict[str, Any] = {"t": int(t * 1000), "caster": ME, "seq": self.n, "skill": "Level 5 " + name}
        if self.version >= 28:
            row["hits"] = [[-5, 5 if crit else 3, 20000]]
        self.casts.append(row)
        self.log.append(name + ("*" if crit else ""))
        for trigger, (talent, skill) in self.resets.items():
            if crit and trigger in (name, "*") and talent in self.talents:
                self.last.pop(skill, None)
        return True


class Bot:
    """The few Bot methods the SkillBook uses."""

    def __init__(self, game: GameLink, profile: dict[str, Any] | None = None) -> None:
        self.follow_buffs = False
        self.following: str | None = None        # the follow run's player (combat holds buffs for it)
        self.a = game
        self.p = dict(settings.PROFILE_DEFAULTS, points_key="")
        self.p.update(profile or {})
        self.retreating = self.resting = False
        self.lines: list[str] = []

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)

    def attackers(self) -> set[int]:
        return set()

    def ui(self) -> dict[str, Any]:
        return {"attack_page": 0}

    def wait_keys_free(self) -> None:
        pass

    def sleep(self, seconds: float) -> None:
        pass


def fight(clock: Clock, skills: Sequence[str], talents: Sequence[str] = (), seconds: float = 30, pack: int = 1,
          dist: float = 4.0, mob_hp: int = 50000, profile: dict[str, Any] | None = None,
          crit: Callable[[str, int], bool] | None = None, version: int = 28, gcd: float = 1.2,
          resets: dict[str, tuple[str, str]] | None = None,
          cd_less: dict[str, float] | None = None) -> tuple[Game, Bot, combat.SkillBook]:
    """Run a fight against the pretend game; returns (game, bot, skillbook)."""
    g = Game(clock, skills, talents, version=version, gcd=gcd, crit=crit, resets=resets, cd_less=cd_less)
    b = Bot(g, profile)
    sb = combat.SkillBook(as_parts(b))
    me = {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}
    mobs = [{"id": -5 - i, "x": 50 + dist, "y": 50 + i * 0.5, "hp": mob_hp, "maxhp": mob_hp, "name": "Wolf",
             "kind": "monster", "side": 1} for i in range(pack)]
    snap: dict[str, Any] = {"self": me, "entities": [me] + mobs}
    end = clock.now + seconds
    while clock.now < end:
        r = sb.pick(snap, mobs[0])
        if isinstance(r, tuple):
            # the bot's press burst: press until the game casts it, for at most burst_time
            began = clock.now
            while clock.now - began < sb.burst_time(r[1]):
                if g.press(r[1]):
                    break
                clock.advance(0.05)
            else:
                sb.refuse(r[1], clock.now)
            while g.casts and clock.now * 1000 < g.casts[-1]["t"]:
                clock.advance(0.05)
            sb.read_casts(ME)
        clock.advance(0.05)
    return g, b, sb


def followed_by(log: Iterable[str], a: str, b: str) -> tuple[int, int]:
    """(how often a was cast and b straight after, how often a was cast with something after)."""
    names = [x.rstrip("*") for x in log]
    total = sum(1 for i in range(len(names) - 1) if names[i] == a)
    hits = sum(1 for i in range(len(names) - 1) if names[i] == a and names[i + 1] == b)
    return hits, total
