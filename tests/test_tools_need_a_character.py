"""The Tools that act in the game wait for a logged-in character: a job started at the login screen
would log in whichever character the Games list last named there, or stop at once. The Construction
scanner still opens for its last report, and a Market window's jobs run on its own game."""

from __future__ import annotations

from typing import Any

import pytest

wx = pytest.importorskip("wx")

from unclefarmer.gui import chores as chores_gui, main  # noqa: E402
from unclefarmer.gui.mainparts import editing, runs as runs_part  # noqa: E402

from conftest import Clock  # noqa: E402

LOGGED_IN = {"self": {"name": "Hero", "id": 7}, "map": {"name": "tulip city"}}
LOGIN_SCREEN = None                       # what a game at the login screen reads as


class FakeRun:
    made: list["FakeRun"] = []

    def __init__(self, text: str, title: str, pid: int, *_args: Any, **kwargs: Any) -> None:
        self.text, self.title, self.pid, self.walk = text, title, pid, kwargs.get("walk", False)
        self.profile: str | None = None
        self.started = False
        FakeRun.made.append(self)

    def start(self) -> None:
        self.started = True


class Frame:
    """Just what run_errand reads: the selected game, each game's reading and listed name."""

    def __init__(self, selected: int | None, reads: dict[int, Any], names: dict[int, str]) -> None:
        self.clock = Clock()
        self.selected, self.reads, self.names = selected, reads, names
        self.runs: dict[int, Any] = {}
        self.last_line: dict[int, str] = {}
        self.said: list[str] = []

    def selected_pid(self) -> int | None:
        return self.selected

    def snapshot(self, pid: int | None = None) -> Any:
        return self.reads.get(pid if pid is not None else self.selected or -1)

    def character(self, pid: int) -> str | None:
        return self.names.get(pid)

    def say(self, text: str) -> None:
        self.said.append(text)

    def on_log(self, *_args: Any) -> None: ...
    def on_done(self, *_args: Any) -> None: ...
    def locate(self, *_args: Any) -> None: ...
    def last_seen(self, *_args: Any) -> None: ...
    def label_games(self) -> None: ...
    def update_buttons(self) -> None: ...


@pytest.fixture(autouse=True)
def fake_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeRun.made = []
    monkeypatch.setattr(runs_part, "Run", FakeRun)


def errand(frame: Frame, text: str = "restock\n", **kw: Any) -> None:
    runs_part.RunsMixin.run_errand(frame, text, "Restocking supplies", **kw)  # type: ignore[arg-type]


def test_a_tool_at_the_login_screen_starts_nothing() -> None:
    # the Games list still names the character that was logged in before Switch account
    f = Frame(1, {1: LOGIN_SCREEN}, {1: "Hero--S1--7"})
    errand(f)
    assert FakeRun.made == [] and f.runs == {}
    assert f.said == ["Log in to the character in the game first."]


def test_a_tool_with_a_logged_in_character_starts_its_job() -> None:
    f = Frame(1, {1: LOGGED_IN}, {1: "Hero--S1--7"})
    errand(f)
    [run] = FakeRun.made
    assert run.started and run.pid == 1 and run.profile == "Hero--S1--7" and f.runs == {1: run}


def test_no_game_and_a_busy_game_still_say_so() -> None:
    f = Frame(None, {}, {})
    errand(f)
    assert f.said == ["No game with the agent is running."]
    busy = Frame(1, {1: LOGGED_IN}, {1: "Hero--S1--7"})
    busy.runs[1] = object()
    errand(busy)
    assert busy.said == ["Stop the bot first."] and FakeRun.made == []


def test_a_market_job_runs_on_its_window_s_game_not_the_selected_one() -> None:
    f = Frame(1, {1: LOGGED_IN, 2: {"self": {"name": "Bob", "id": 9}}}, {1: "Hero--S1--7", 2: "Bob--S1--9"})
    errand(f, "market search\n", pid=2)
    [run] = FakeRun.made
    assert run.pid == 2 and run.profile == "Bob--S1--9"


def test_a_market_job_waits_for_its_own_game_s_character() -> None:
    f = Frame(1, {1: LOGGED_IN, 2: LOGIN_SCREEN}, {1: "Hero--S1--7", 2: "Bob--S1--9"})
    errand(f, "market search\n", pid=2)
    assert FakeRun.made == [] and f.said == ["Log in to the character in the game first."]


REPORT = {"at": "now", "by": "Hero", "rows": [["Magic Cannon", 40.0]]}


@pytest.fixture
def app() -> Any:
    return wx.GetApp() or wx.App(False)


@pytest.mark.parametrize("report,can_act,enabled", [
    (REPORT, False, {"scan": False, "copy": True, "publish": False}),
    (REPORT, True, {"scan": True, "copy": True, "publish": True}),
    (None, False, {"scan": False, "copy": False, "publish": False}),
    (None, True, {"scan": True, "copy": False, "publish": False}),
])
def test_the_construction_scanner_reads_without_a_character_but_scans_only_with_one(
        app: Any, monkeypatch: pytest.MonkeyPatch, report: Any, can_act: bool, enabled: dict[str, bool]) -> None:
    monkeypatch.setattr(chores_gui.constructionscan, "load", lambda: report)
    parent = wx.Frame(None)
    try:
        d = chores_gui.ConstructionScanDialog(parent, can_act)
        assert {key: b.IsEnabled() for key, b in d.buttons.items()} == enabled
        assert d.where.IsEnabled() == can_act
        d.Destroy()
    finally:
        parent.Destroy()


class ScanFrame:
    def __init__(self, selected: int | None, read: Any, runs: dict[int, Any] | None = None) -> None:
        self.selected, self.read, self.runs = selected, read, dict(runs or {})
        self.errands: list[str] = []

    def selected_pid(self) -> int | None:
        return self.selected

    def selected_profile(self) -> str | None:
        return "Hero--S1--7" if self.read else None

    def snapshot(self, pid: int | None = None) -> Any:
        return self.read

    def run_errand(self, text: str, title: str) -> None:
        self.errands.append(text)


@pytest.mark.parametrize("selected,read,runs,can_act", [
    (None, None, {}, False),                      # no game: the last report only
    (1, LOGIN_SCREEN, {}, False),                 # the login screen
    (1, LOGGED_IN, {1: object()}, False),         # its bot is running
    (1, LOGGED_IN, {}, True),
])
def test_the_scanner_window_is_told_whether_it_may_scan(monkeypatch: pytest.MonkeyPatch, selected: int | None,
                                                        read: Any, runs: dict[int, Any], can_act: bool) -> None:
    told: list[bool] = []

    class Dialog:
        kind = "announcement"

        def __init__(self, _parent: Any, may: bool) -> None:
            told.append(may)

        def __enter__(self) -> "Dialog":
            return self

        def __exit__(self, *_args: Any) -> None: ...

        def ShowModal(self) -> int:
            return wx.ID_CANCEL
    monkeypatch.setattr(chores_gui, "ConstructionScanDialog", Dialog)
    f = ScanFrame(selected, read, runs)
    editing.EditingMixin.open_construction_scan(f)  # type: ignore[arg-type]
    assert told == [can_act] and f.errands == []


def test_the_market_window_opens_only_with_a_logged_in_character(monkeypatch: pytest.MonkeyPatch) -> None:
    errors: list[str] = []
    def error(_parent: Any, text: str) -> None:
        errors.append(text)
    monkeypatch.setattr(main, "error", error)
    opened: list[int] = []

    class Market:
        def __init__(self, _host: Any, pid: int, _name: Any) -> None:
            opened.append(pid)

        def Show(self) -> None: ...
        def Raise(self) -> None: ...
    monkeypatch.setattr(main, "MarketFrame", Market)
    f: Any = ScanFrame(1, LOGIN_SCREEN)
    f.market_windows = {}
    f.character = lambda _pid: "Hero--S1--7"  # pyright: ignore[reportUnknownLambdaType]
    main.MainFrame.open_market(f)
    assert opened == [] and errors == ["Log in to the character in the game first."]
    f.read = LOGGED_IN
    main.MainFrame.open_market(f)
    assert opened == [1]
