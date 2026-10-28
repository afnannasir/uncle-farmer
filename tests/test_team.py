# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The team chores from the Action menu: invite parsing, applications, leadership and leaving.

The menu fake here is the same shape tests/test_gang.py drives: entries carry their texts in
`options`/`f`, and the menu advances one screen per choice.
"""

from __future__ import annotations

from unclefarmer import travel  # noqa: F401  (find_entry uses travel.matches)
from unclefarmer.engine import team
from unclefarmer.engine.bot import Bot
from conftest import as_bot
from typing import Any


def entry(text: str, cmd: int=0) -> dict[str, Any]:
    return {"flags": 12, "options": [text], "cmd": [cmd]}


class FakeMenu:
    """An Action menu that opens on the menu key and shows one screen per chosen step.

    screens[i] is the rows shown for step i; a None screen means the dialog is gone by then.
    close_after: after choosing at that index the dialog closes, so the next wait_dialog sees none.
    """

    find_entry = Bot.find_entry
    find_in_row = Bot.find_in_row
    entry_texts = staticmethod(Bot.entry_texts)

    def __init__(self, screens: list[Any], close_after: int | None=None) -> None:
        self.screens = screens
        self.close_after = close_after
        self.i = -1
        self.open = False
        self.closed = 0
        self.keys: list[str] = []
        self.chosen: list[tuple[str, int, int, dict[str, Any]]] = []
        self.p = {"action_menu_key": "lctrl+enter"}

    def key(self, k: str) -> None:
        self.keys.append(k)
        if k == self.p["action_menu_key"]:
            self.open = True
            self.i = 0

    def sleep(self, seconds: float) -> None:
        pass

    def close_dialogs(self) -> None:
        self.closed += 1
        self.open = False

    def _top(self) -> Any:
        if not self.open or self.i < 0 or self.i >= len(self.screens):
            return None
        rows = self.screens[self.i]
        if rows is None:
            return None
        return {"row": 0, "col": 0, "rows": rows}

    def wait_dialog(self, seconds: float=3.0) -> dict[str, Any] | None:
        top = self._top()
        return {"open": 1, "top": top} if top else None

    def choose(self, pattern: str, row: str | None=None) -> dict[str, Any] | None:
        top = self._top()
        if top is None:
            return None
        where = self.find_entry(top, pattern) if row is None else self.find_in_row(top, row, pattern)
        if where is None:
            return None
        r, c = where
        e = top["rows"][r][c]
        self.chosen.append((pattern, r, c, e))
        self.i += 1
        if self.close_after is not None and self.i > self.close_after:
            self.open = False
        return e


class YesBot:
    def __init__(self, ok: bool=True) -> None:
        self.ok = ok
        self.patterns: list[str] = []

    def choose(self, pattern: str) -> dict[str, Any] | None:
        self.patterns.append(pattern)
        return {"options": ["yes"]} if self.ok else None


# ---------------------------------------------------------------- reading the invite
def test_invite_in_reads_the_inviter_and_team() -> None:
    texts = ["<system> Something happened", 'Ranger invites you to join the "Harvest" team. '
             "Do you want to accept it?"]
    assert team.invite_in(texts) == ("Ranger", "Harvest")


def test_invite_in_without_quotes_and_case() -> None:
    assert team.invite_in(["  friend invites you to join the Pals team"]) == ("friend", "Pals")


def test_invite_in_nothing() -> None:
    assert team.invite_in(["nothing to see", "", None]) is None


def test_accept_invite_uses_the_yes_entry() -> None:
    yes = YesBot(True)
    assert team.accept_invite(as_bot(yes)) is True
    assert yes.patterns[0] == r"^\s*yes\s*$"
    assert team.accept_invite(as_bot(YesBot(False))) is False


# ---------------------------------------------------------------- the menu walk
def test_menu_runs_each_step_and_closes() -> None:
    b = FakeMenu([[[entry("apply to join: 1")]], [[entry("Ranger")]], [[entry("agree")]]])
    assert team._menu(as_bot(b), [r"apply to join", r"^Ranger", r"agree"]) is None
    assert b.closed == 2          # once before opening, once at the end
    assert b.keys[0] == "lctrl+enter"


def test_menu_reports_the_step_that_was_missing() -> None:
    b = FakeMenu([[[entry("apply to join: 1")]], [[entry("Someone else")]], [[entry("agree")]]])
    assert team._menu(as_bot(b), [r"apply to join", r"^Ranger", r"agree"]) == 1


def test_menu_stops_when_no_dialog_appears() -> None:
    b = FakeMenu([None, [[entry("agree")]]])
    assert team._menu(as_bot(b), [r"anything", r"agree"]) == 0


def test_menu_leaves_a_question_open() -> None:
    b = FakeMenu([[[entry("quit the Harvest team")]], [[entry("yes")]]])
    assert team._menu(as_bot(b), [r"quit the .*team"], close=False) is None
    assert b.closed == 1          # only the initial close_dialogs
    d = b.wait_dialog()
    assert d is not None and d["open"] == 1


def test_menu_dialog_closing_mid_steps() -> None:
    b = FakeMenu([[[entry("one")]], [[entry("two")]], [[entry("three")]]], close_after=0)
    assert team._menu(as_bot(b), [r"one", r"two", r"three"]) == 1


def test_name_pattern_matches_a_word_not_a_prefix() -> None:
    pat = team._name("Al")
    assert travel.matches(pat, "Al")
    assert travel.matches(pat, "Al's")
    assert travel.matches(pat, "Al Ranger")
    assert not travel.matches(pat, "Alvin")


# ---------------------------------------------------------------- applications and leadership
def test_accept_application_success() -> None:
    b = FakeMenu([[[entry("apply to join: 1")]], [[entry("Ranger")]], [[entry("agree")]]])
    ok, msg = team.accept_application(as_bot(b), "Ranger")
    assert ok and "accepted Ranger" in msg


def test_accept_application_failures_say_which_step() -> None:
    assert team.accept_application(as_bot(FakeMenu([[[entry("my mission")]]])), "Ranger") == (
        False, "there are no team applications waiting")
    assert team.accept_application(
        as_bot(FakeMenu([[[entry("apply to join: 1")]], [[entry("Someone")]], [[entry("agree")]]])), "Ranger") == (
        False, "I don't see an application from Ranger")
    assert team.accept_application(
        as_bot(FakeMenu([[[entry("apply to join: 1")]], [[entry("Ranger")]], [[entry("decline")]]])), "Ranger") == (
        False, "the game didn't offer to agree")


def member(name: str, pid: int, *more: str) -> list[dict[str, Any]]:
    """A row of the team's member list, as the game shows it (dialogs.log 2026-10-22)."""
    row = [{"flags": 8192, "options": ["%s;%d" % (name, pid)] + list(more), "cmd": [pid]}]
    if "leader" not in more:
        row += [{"flags": 14, "f": "Are you sure you want to transfer the team leader to this member?",
                 "options": ["Transfer team leader"], "cmd": [1, 9, pid]},
                {"flags": 14, "f": "Are you sure you want to remove this team member?", "options": ["remove"],
                 "cmd": [1, 8, pid]}]
    return row


MEMBERS = [member("Lord", 1, "leader", "Hunter"), member("Cotton", 2, "Mage"), member("Ranger", 3, "Sorceress")]
QUESTION = [[entry("Are you sure you want to transfer the team leader to this member?")], [entry("yes")]]


def test_make_leader_presses_transfer_in_that_member_s_own_row() -> None:
    b = FakeMenu([[[entry("Harvest team")]], MEMBERS, QUESTION])
    ok, msg = team.make_leader(as_bot(b), "Ranger")
    assert ok and "Ranger leads the team now" in msg
    _pattern, r, c, e = b.chosen[1]
    assert (r, c) == (2, 1) and e["cmd"] == [1, 9, 3]        # Ranger's, not Cotton's or our "leader"
    assert b.chosen[2][3]["options"] == ["yes"]


def test_make_leader_failures_say_which_step() -> None:
    led = [member("Lord", 1, "leader"), [{"options": ["Ranger;3", "Sorceress"]}]]     # we don't lead it
    cases = [
        ([[[entry("my mission")]]], "I'm not in a team"),
        ([[[entry("Harvest team")]], MEMBERS[:2], QUESTION], "Ranger isn't in my team"),
        ([[[entry("Harvest team")]], led, QUESTION], "I'm not the team leader"),
        ([[[entry("Harvest team")]], MEMBERS, None], "the game didn't ask to confirm"),
    ]
    for screens, want in cases:
        ok, msg = team.make_leader(as_bot(FakeMenu(screens)), "Ranger")
        assert ok is False and msg == want


# ---------------------------------------------------------------- leaving (the quit row)
def test_leave_without_a_team_row() -> None:
    ok, msg = team.leave(as_bot(FakeMenu([[[entry("my mission")]]])))
    assert ok is False and msg == "I'm not in a team"


def test_leave_without_a_confirm_entry_still_leaves() -> None:
    b = FakeMenu([[[{"options": ["Harvest team"]},
                    {"f": "Do you want to quit the Harvest team?", "options": ["quit"]}]]])
    ok, msg = team.leave(as_bot(b))
    assert ok is True and msg == "left the team"
