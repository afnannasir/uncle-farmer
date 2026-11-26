"""Tools, Construction scanner (engine/constructionscan.py): the reading, the line sent, and publishing."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from conftest import Clock, as_bot, stand_in

from unclefarmer.engine import chores, constructionscan as hr, script, town

TURRET, WALL = (n for n, _s in chores.HIGHLAND_SITES)
ACTION = [["Action menu; lag 126"], ["Disorder gang", "transfer the gang", "disband", "send system message",
                                    "send broadcast", "send an announcement"]]


class Agent:
    def __init__(self, g: Game) -> None:
        self.g = g

    def json(self, line: str) -> dict[str, Any]:
        if line.startswith("said"):
            return {"now": 1, "said": [{"t": 2, "text": x} for x in self.g.said]}
        if line.startswith("chat"):
            return {"last": 1, "chat": self.g.chat}
        return {}

    def send(self, line: str) -> str:
        self.g.sent.append(line)
        return "ok"


class Game:
    """Menus as rows of texts; a key opens one, a chosen entry may open the next."""

    def __init__(self, action: list[list[str]] | None = None) -> None:
        self.character = "Hero"
        self.p: dict[str, Any] = {}
        self.clock = Clock()
        self.a = Agent(self)
        self.menu: list[list[str]] | None = None
        self.action = ACTION if action is None else action
        self.notices: list[list[str]] = []
        self.said: list[str] = []
        self.chat: list[dict[str, Any]] = []
        self.sent: list[str] = []
        self.chosen: list[str] = []
        self.levels: dict[str, str] = {}
        self.deleted: list[str] = []

    def dialog(self) -> dict[str, Any]:
        if self.menu is None:
            return {"open": 0, "top": None}
        return {"open": 1, "top": {"rows": [[{"text": t} for t in row] for row in self.menu]}}

    @staticmethod
    def entry_texts(e: dict[str, Any]) -> list[str]:
        return [e["text"]]

    def wait_dialog(self, seconds: float = 3.0) -> dict[str, Any] | None:
        return self.dialog() if self.menu is not None else None

    def close_dialogs(self) -> None:
        self.menu = None

    def key(self, k: str) -> None:
        self.menu = [["battle report"], ["gang announcement"]] if k == "backspace" else self.action

    def find_entry(self, top: dict[str, Any] | None, pattern: str) -> tuple[int, int] | None:
        for r, row in enumerate((top or {}).get("rows", [])):
            for c, e in enumerate(row):
                if re.search(pattern, e["text"], re.I):
                    return r, c
        return None

    def choose(self, pattern: str, row: str | None = None) -> dict[str, Any] | None:
        for r, cells in enumerate(self.menu or []):
            if row is not None and not re.search(row, cells[0], re.I):
                continue
            for text in cells:
                if re.search(pattern, text, re.I):
                    self.chosen.append(text)
                    return self._act(text, r)
        self.menu = None
        return None

    def _act(self, text: str, r: int) -> dict[str, Any]:
        assert self.menu is not None
        if text == "gang announcement":
            self.menu = self.notices
        elif text == "delete":
            self.deleted.append(self.menu[r][0])
            del self.notices[r - 1:r + 1]
            self.menu = [["Are you sure you want to delete this gang Announcement?"], ["Confirm"], ["cancel"]]
        else:
            self.menu = None
        return {"text": text}

    def sleep(self, s: float) -> None:
        self.clock.now += s

    def walk_to(self, cell: tuple[float, float]) -> str:
        return "arrived"

    def talk(self, name: str, at: Any = None, stand: Any = None) -> bool:
        if name not in self.levels:
            return False
        self.menu = [[self.levels[name]], ["repair"]]
        return True


@pytest.fixture
def typed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    got: list[str] = []

    def type_text(b: Any, text: str) -> bool:
        got.append(text)
        return True
    monkeypatch.setattr(hr.paths, "PROFILES", str(tmp_path))
    monkeypatch.setattr(town, "type_text", stand_in(town.type_text, type_text))
    monkeypatch.setattr(hr.gamekeys, "notice_key", stand_in(hr.gamekeys.notice_key, lambda: "backspace"))
    return got


def keep(rows: list[Any]) -> None:
    hr.jsonio.save(hr._path(), {"at": "2026-11-18 22:00", "by": "Hero", "rows": rows})  # pyright: ignore[reportPrivateUsage]


def test_the_line_lists_what_is_unfinished_the_least_complete_first() -> None:
    text = hr.message({"rows": [["highland 38 turret", 90.0], ["Level 5 Magic Cannon", 62.0],
                                ["highland 41 turret", 100.0], ["highland 38 wall", None]]})
    assert text == hr.TITLE + ": Level 5 Magic Cannon 62.0%; highland 38 turret 90.0%"


def test_everything_at_100_percent_says_so() -> None:
    assert hr.message({"rows": [["highland 38 turret", 100.0], ["highland 38 wall", 100.0]]}) == hr.ALL_FINE


def test_a_long_report_stays_short_and_says_how_many_are_left_out() -> None:
    text = hr.message({"rows": [["highland %d wall" % n, 50.0 + n / 10] for n in range(100, 140)]})
    assert len(text) <= hr.MOST_TEXT and text.startswith(hr.TITLE + ": highland 100 wall") and re.search(r" and \d+ more$", text)


def test_the_scan_reads_camps_and_highlands_and_repairs_nothing(typed: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game()
    camp = "Level 5 Magic Cannon Construction"
    g.levels = {camp: "Level 5 Magic Cannon Construction; Completed 62.0%",
                TURRET: "the completeness is 97.5%", WALL: "You've repaired the wall today"}
    left = [38]
    home: list[int] = []
    monkeypatch.setattr(chores, "inner_hall", stand_in(chores.inner_hall, lambda b: None))
    monkeypatch.setattr(chores, "gang_camps", stand_in(chores.gang_camps, lambda b, allowed=(): [camp]))
    monkeypatch.setattr(chores, "next_highland", stand_in(chores.next_highland, lambda b, seen, taken=False: left.pop(0) if left else None))
    monkeypatch.setattr(chores, "leave_highland", stand_in(chores.leave_highland, lambda b: home.append(1)))
    g.log = lambda line, speak=False: None  # type: ignore[attr-defined]
    report = hr.scan(as_bot(g))
    assert report["rows"] == [("Level 5 Magic Cannon", 62.0), ("highland 38 turret", 97.5), ("highland 38 wall", None)]
    assert home == [1] and "repair" not in g.chosen and hr.load() is not None
    assert "highland 38 wall: couldn't be read" in hr.details(hr.load() or {})


def test_nothing_to_read_stops_the_scan(typed: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chores, "inner_hall", stand_in(chores.inner_hall, lambda b: None))
    monkeypatch.setattr(chores, "gang_camps", stand_in(chores.gang_camps, lambda b, allowed=(): []))
    monkeypatch.setattr(chores, "next_highland", stand_in(chores.next_highland, lambda b, seen, taken=False: None))
    with pytest.raises(chores.Stopped, match="no construction"):
        hr.scan(as_bot(Game()))


@pytest.mark.parametrize("kind,entry", [("broadcast", "send broadcast"), ("system", "send system message"),
                                        ("announcement", "send an announcement")])
def test_publishing_chooses_only_that_entry_and_types_the_report(typed: list[str], kind: str, entry: str) -> None:
    keep([["highland 38 wall", 80.0]])
    g = Game()
    g.log = lambda line, speak=False: None  # type: ignore[attr-defined]
    text = hr.TITLE + ": highland 38 wall 80.0%"
    g.chat = [{"name": "Hero", "text": text}]
    hr.publish(as_bot(g), kind)
    assert g.chosen[0] == entry and typed == [text]
    assert "disband" not in g.chosen and "transfer the gang" not in g.chosen


def test_a_character_without_the_rights_is_told_and_nothing_is_typed(typed: list[str]) -> None:
    keep([["highland 38 wall", 80.0]])
    g = Game(action=[["Action menu"], ["Disorder gang", "quit"]])
    with pytest.raises(chores.Stopped, match="elder"):
        hr.publish(as_bot(g), "broadcast")
    assert typed == [] and g.chosen == []


def test_nothing_to_publish_before_a_scan(typed: list[str]) -> None:
    with pytest.raises(chores.Stopped, match="scan first"):
        hr.publish(as_bot(Game()), "system")


def test_a_new_announcement_replaces_only_our_own_old_report(typed: list[str]) -> None:
    keep([["highland 38 wall", 80.0]])
    g = Game()
    g.log = lambda line, speak=False: None  # type: ignore[attr-defined]
    g.said = ["sent"]
    g.notices = [["1;New; %s: highland 38.." % hr.TITLE], ["Hero issued at Nov 18, 22:44:03, 2026", "delete"],
                 ["2;%s; highla..; Press to see the full text" % hr.TITLE], ["Boss issued at Nov 17, 23:42:41, 2026", "delete"],
                 ["3;%s; highla..; Press to see the full text" % hr.TITLE], ["Hero issued at Nov 10, 10:00:00, 2026", "delete"],
                 ["4;repair 68"], ["Hero issued at Nov 09, 10:00:00, 2026", "delete"]]
    hr.publish(as_bot(g), "announcement")
    assert g.deleted == ["Hero issued at Nov 10, 10:00:00, 2026"] and "Confirm" in g.chosen
    assert [row[0] for row in g.notices if "issued" in row[0]] == ["Hero issued at Nov 18, 22:44:03, 2026",
                                                                   "Boss issued at Nov 17, 23:42:41, 2026",
                                                                   "Hero issued at Nov 09, 10:00:00, 2026"]


def test_an_unconfirmed_send_removes_nothing(typed: list[str]) -> None:
    keep([["highland 38 wall", 80.0]])
    g = Game()
    logged: list[str] = []
    g.log = lambda line, speak=False: logged.append(line)  # type: ignore[attr-defined]
    hr.publish(as_bot(g), "announcement")
    assert g.deleted == [] and "didn't confirm" in logged[-1]


ELDER = [["Action menu; lag 80"], ["Disorder gang", "send system message", "send broadcast", "send an announcement"]]


def auto_game(monkeypatch: pytest.MonkeyPatch, percent: float, action: list[list[str]] | None = None) -> tuple[Game, list[str]]:
    g = Game(action)
    logged: list[str] = []
    g.log = lambda line, speak=False: logged.append(line)  # type: ignore[attr-defined]
    g.levels = {"Level 5 Supply Camp Construction": "Level 5 supply camp construction; Completed %.1f%%" % percent}
    monkeypatch.setattr(chores, "inner_hall", stand_in(chores.inner_hall, lambda b: None))
    monkeypatch.setattr(chores, "gang_camps", stand_in(chores.gang_camps, lambda b, allowed=(): list(g.levels)))
    monkeypatch.setattr(chores, "next_highland", stand_in(chores.next_highland, lambda b, seen, taken=False: None))
    return g, logged


def test_a_schedule_publishes_once_until_something_changes(typed: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    g, logged = auto_game(monkeypatch, 62.0, ELDER)              # an elder: no disband in the menu
    text = hr.TITLE + ": Level 5 Supply Camp 62.0%"
    g.chat = [{"name": "Hero", "text": text}]
    hr.scan_and_publish(as_bot(g), "system")
    hr.scan_and_publish(as_bot(g), "system")
    assert typed == [text] and "nothing changed" in logged[-1]
    # the same line sent another way is news of its own
    hr.scan_and_publish(as_bot(g), "broadcast")
    assert typed == [text, text]
    g.levels = {name: "Completed 70.0%" for name in g.levels}
    g.chat = [{"name": "Hero", "text": hr.TITLE + ": Level 5 Supply Camp 70.0%"}]
    hr.scan_and_publish(as_bot(g), "system")
    assert typed[-1].endswith("70.0%") and "send broadcast" in g.chosen


def test_a_schedule_stays_quiet_when_everything_is_finished(typed: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    g, logged = auto_game(monkeypatch, 100.0)
    hr.scan_and_publish(as_bot(g), "broadcast")
    assert typed == [] and "nothing published" in logged[-1] and g.chosen == []


def test_a_schedule_without_the_rights_does_not_even_scan(typed: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    g, _logged = auto_game(monkeypatch, 50.0, [["Action menu"], ["Disorder gang", "quit"]])
    monkeypatch.setattr(hr, "scan", stand_in(hr.scan, lambda b: pytest.fail("no scan")))
    with pytest.raises(chores.Stopped, match="elder"):
        hr.scan_and_publish(as_bot(g), "system")


def test_the_script_command_takes_a_publication_kind() -> None:
    assert len(script.parse("construction_scan\nconstruction_scan publish=broadcast\nconstruction_scan auto=system\n")[0]) == 3
    with pytest.raises(script.ScriptError):
        script.parse("construction_scan publish=everywhere\n")


# ---------------------------------------------------------------- a line to each highland
ROWS = [["highland 80 turret", 95.0], ["highland 58 wall", 98.0], ["highland 67 turret", 99.0],
        ["highland 80 wall", 97.0], ["Level 5 Magic Cannon", 62.0], ["highland 67 wall", 100.0]]


def test_each_highland_is_one_line_with_its_turret_and_wall_together() -> None:
    lines = hr.message_lines({"rows": ROWS})
    assert lines == [hr.TITLE + ":", "Level 5 Magic Cannon 62.0%", "highland 80: turret 95.0%, wall 97.0%",
                     "highland 58: wall 98.0%", "highland 67: turret 99.0%"]
    assert not any(line.endswith(" ") for line in lines)


def test_a_finished_report_is_still_the_one_line_that_says_so() -> None:
    assert hr.message_lines({"rows": [["highland 38 turret", 100.0], ["highland 38 wall", None]]}) == [hr.ALL_FINE]


def test_a_long_report_by_lines_stays_short_and_counts_places_left_out() -> None:
    lines = hr.message_lines({"rows": [["highland %d wall" % n, 50.0 + n / 10] for n in range(100, 140)]})
    assert len("\n".join(lines)) <= hr.MOST_TEXT and lines[1].startswith("highland 100: wall")
    assert re.fullmatch(r"and \d+ more", lines[-1])


@pytest.fixture
def pasted(typed: list[str], monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    got: list[list[str]] = []

    def type_lines(b: Any, lines: list[str]) -> bool:
        got.append(lines)
        return True
    monkeypatch.setattr(town, "type_lines", stand_in(town.type_lines, type_lines))
    return got


def elder(version: int = 80) -> tuple[Game, list[str]]:
    g = Game()
    g.agent_version = version  # type: ignore[attr-defined]
    logged: list[str] = []
    g.log = lambda line, speak=False: logged.append(line)  # type: ignore[attr-defined]
    g.said = ["sent"]
    return g, logged


def test_an_announcement_by_lines_is_pasted_a_line_to_each_place(typed: list[str], pasted: list[list[str]]) -> None:
    keep(ROWS)
    g, _logged = elder()
    hr.publish(as_bot(g), "announcement", True)
    assert pasted == [hr.message_lines(hr.load() or {})] and typed == []


def test_the_app_setting_decides_when_the_script_does_not_say(typed: list[str], pasted: list[list[str]]) -> None:
    keep(ROWS)
    hr.settings.update_app({"construction_lines": True})
    g, _logged = elder()
    hr.publish(as_bot(g), "announcement")
    assert len(pasted) == 1 and typed == []
    hr.publish(as_bot(g), "announcement", False)             # the script's word wins over the setting
    assert len(pasted) == 1 and len(typed) == 1


def test_by_default_an_announcement_stays_one_line(typed: list[str], pasted: list[list[str]]) -> None:
    keep(ROWS)
    g, _logged = elder()
    hr.publish(as_bot(g), "announcement")
    assert pasted == [] and typed == [hr.message(hr.load() or {})]


@pytest.mark.parametrize("kind", ["broadcast", "system"])
def test_a_broadcast_and_a_system_message_never_go_by_lines(typed: list[str], pasted: list[list[str]], kind: str) -> None:
    keep(ROWS)
    g, logged = elder()
    hr.publish(as_bot(g), kind, True)
    assert pasted == [] and typed == [hr.message(hr.load() or {})] and "one line" in logged[0]


def test_an_older_agent_sends_one_line_and_says_why(typed: list[str], pasted: list[list[str]]) -> None:
    keep(ROWS)
    g, logged = elder(79)
    hr.publish(as_bot(g), "announcement", True)
    assert pasted == [] and typed == [hr.message(hr.load() or {})]
    assert "too old" in logged[0] and "restart Mist World" in logged[0]


def test_a_schedule_by_lines_tells_the_gang_again_only_when_the_layout_or_news_changes(
        typed: list[str], pasted: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    g, logged = auto_game(monkeypatch, 62.0, ELDER)
    g.agent_version = 80  # type: ignore[attr-defined]
    g.said = ["sent"]
    bot = as_bot(g)
    hr.scan_and_publish(bot, "announcement", True)
    hr.scan_and_publish(bot, "announcement", True)
    assert len(pasted) == 1 and "nothing changed" in logged[-1]
    hr.scan_and_publish(bot, "announcement", False)         # the same news in the other layout is sent again
    assert len(pasted) == 1 and len(typed) == 1


def test_the_script_command_takes_lines_yes_or_no_for_an_announcement() -> None:
    nodes = script.parse("construction_scan publish=announcement lines=yes\nconstruction_scan auto=announcement lines=no\n")[0]
    assert [n.opts.get("lines") for n in nodes] == [True, False]
    assert script.parse("construction_scan publish=broadcast\n")[0][0].opts.get("lines") is None
    for bad in ("construction_scan lines=yes", "construction_scan publish=announcement lines=maybe",
                "construction_scan publish=announcement yes", "construction_scan publish=announcement lines=yes lines=no"):
        with pytest.raises(script.ScriptError):
            script.parse(bad + "\n")


def test_the_lines_reach_the_agent_as_one_request_a_backslash_and_n_standing_for_each_break(
        monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game()
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, lambda b, want_open=True, seconds=3.0: True))
    sent: list[str] = []
    g.a.send = lambda line: (sent.append(line), "pasted 9" if line.startswith("typepaste") else "ok")[1]  # type: ignore[method-assign]
    assert town.type_lines(as_bot(g), ["Title:", "back\\slash\tand\ttab", "last"])
    assert sent == ["typeclear", "typepaste Title:\\nback\\\\slash and tab\\nlast", "typeenter"]


def test_a_refused_paste_is_not_submitted(monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game()
    g.log = lambda line, speak=False: None  # type: ignore[attr-defined]
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, lambda b, want_open=True, seconds=3.0: True))
    sent: list[str] = []
    g.a.send = lambda line: (sent.append(line), "refused: no input box is open")[1]  # type: ignore[method-assign]
    assert not town.type_lines(as_bot(g), ["a", "b"])
    assert "typeenter" not in sent


def test_only_an_agent_with_typepaste_can_paste() -> None:
    g = Game()
    assert not town.can_paste(as_bot(g))
    g.agent_version = town.PASTE_AGENT - 1  # type: ignore[attr-defined]
    assert not town.can_paste(as_bot(g))
    g.agent_version = town.PASTE_AGENT  # type: ignore[attr-defined]
    assert town.can_paste(as_bot(g))


# ---------------------------------------------------------------- highlands taken from other gangs
@pytest.mark.parametrize("setting,asked,want", [(False, None, False), (True, None, True),
                                                (True, False, False), (False, True, True)])
def test_highlands_taken_from_other_gangs_are_scanned_as_set_or_asked(
        typed: list[str], monkeypatch: pytest.MonkeyPatch, setting: bool, asked: bool | None, want: bool) -> None:
    hr.settings.update_app({"construction_taken": setting})
    g, _logged = auto_game(monkeypatch, 62.0)
    asked_for: list[bool] = []

    def tour(b: Any, seen: Any, taken: bool = False) -> None:
        asked_for.append(taken)
    monkeypatch.setattr(chores, "next_highland", stand_in(chores.next_highland, tour))
    hr.scan(as_bot(g), asked)
    assert asked_for == [want]


def test_a_schedule_passes_taken_on_to_the_scan(typed: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    g, _logged = auto_game(monkeypatch, 100.0)
    got: list[bool | None] = []
    real = hr.scan

    def scan(b: Any, taken: bool | None = None) -> dict[str, Any]:
        got.append(taken)
        return real(b, taken)
    monkeypatch.setattr(hr, "scan", stand_in(hr.scan, scan))
    hr.scan_and_publish(as_bot(g), "system", None, True)
    assert got == [True]


def test_the_taken_setting_is_off_by_default() -> None:
    assert hr.settings.APP_DEFAULTS["construction_taken"] is False
    assert hr.settings.APP_DEFAULTS["construction_publish"] is False


def test_the_script_command_takes_taken_yes_or_no_for_a_scan() -> None:
    nodes = script.parse("construction_scan taken=yes\nconstruction_scan auto=system taken=no\n"
                         "construction_scan taken=yes auto=announcement lines=yes\nconstruction_scan\n")[0]
    assert [n.opts.get("taken") for n in nodes] == [True, False, True, None]
    assert nodes[2].opts["auto"] == "announcement" and nodes[2].opts["lines"] is True
    for bad in ("construction_scan taken=maybe", "construction_scan publish=system taken=yes",
                "construction_scan taken=yes taken=no", "construction_scan auto=system publish=system",
                "construction_scan taken", "construction_scan every=yes"):
        with pytest.raises(script.ScriptError):
            script.parse(bad + "\n")


@pytest.mark.parametrize("opts,want", [({}, ("scan", None)), ({"taken": True}, ("scan", True)),
                                       ({"auto": "system", "taken": False}, ("auto", False))])
def test_the_script_step_hands_taken_to_the_scanner(
        monkeypatch: pytest.MonkeyPatch, opts: dict[str, Any], want: tuple[str, bool | None]) -> None:
    from unclefarmer.engine import activities
    got: list[tuple[str, bool | None]] = []
    monkeypatch.setattr(hr, "scan", stand_in(hr.scan, lambda b, taken=None: got.append(("scan", taken))))
    monkeypatch.setattr(hr, "scan_and_publish", stand_in(
        hr.scan_and_publish, lambda b, kind, lines=None, taken=None: got.append(("auto", taken))))
    activities.run(as_bot(Game()), "construction_scan", False, opts)
    assert got == [want]
