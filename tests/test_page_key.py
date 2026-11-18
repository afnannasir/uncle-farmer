"""The skill page turns with the game's own key (W_ATTACK_PAGETURN in input.ini), not always Caps
Lock: the game listens only to bound keys, so a player who moved the page to Space had every Caps
Lock thrown away and the skills of the other page pressed on empty keys."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from unclefarmer import gamekeys, settings
from unclefarmer.engine import combat
from conftest import as_parts, stand_in
from test_combat_extra import TB, TLink, pinned, sb


def game_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ini: str | None) -> None:
    if ini is not None:
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "input.ini").write_text(ini, encoding="latin-1")
    monkeypatch.setattr(settings, "load_app", stand_in(settings.load_app, lambda: {"game_folder": str(tmp_path)}))


def test_the_page_key_is_the_one_bound_in_the_game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    game_with(tmp_path, monkeypatch, "W_ATTACK1=Y\nW_ATTACK_PAGETURN=SPACE\nW_DEFAULT_ATTACK=F\n")
    assert gamekeys.page_turn_key() == "space"


@pytest.mark.parametrize("ini", [None, "W_ATTACK1=Y\n", "W_ATTACK_PAGETURN=SOMETHING_ODD\n",
                                 "W_ATTACK_PAGETURN=CAPSLOCK\n"])
def test_caps_lock_where_the_game_binds_no_other_page_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                          ini: str | None) -> None:
    game_with(tmp_path, monkeypatch, ini)
    assert gamekeys.page_turn_key() == "capslock"


class Link(TLink):
    """The page turns only for the page key, as the game does: any other key is thrown away."""

    def __init__(self, key: str) -> None:
        super().__init__()
        self.key, self.page, self.sent = key, 0, list[str]()

    def send(self, line: str) -> str:
        self.sent.append(line)
        if line == "key %s 60" % self.key:
            self.page = 1 - self.page
        return "tapped"


class Game(TB):
    def __init__(self, key: str) -> None:
        self.link = Link(key)
        super().__init__(a=self.link)

    def ui(self) -> dict[str, Any]:
        return {"attack_page": self.link.page}


def test_the_page_turns_with_the_game_key() -> None:
    g = Game("space")
    s = pinned(sb(b=g, page_key="space"), page=0, at=0.0)
    assert s.flip_to(1) is True
    assert g.link.sent == ["key space 60"] and g.link.page == 1
    pinned(s, page=1, at=0.0)
    assert s.flip_to(0) is True and g.link.page == 0


def test_caps_lock_alone_never_turns_a_page_bound_to_space() -> None:
    """What the bot did before: the page stayed where it was."""
    g = Game("space")
    s = pinned(sb(b=g, page_key="capslock"), page=0, at=0.0)
    assert s.flip_to(1) is False
    assert g.link.page == 0


def test_a_moved_page_key_is_said_when_the_other_page_holds_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gamekeys, "page_turn_key", stand_in(gamekeys.page_turn_key, lambda: "space"))
    skills = {"combat": [{"id": 1, "name": "Fireball"}, {"id": 2, "name": "Sear"}],
              "attack_keys": {"1": 1, "11": 2}, "talents": []}
    bot = TB(a=TLink(skills=skills, modes={"swap_attack": True}))
    book = combat.SkillBook(as_parts(bot))
    assert book.page_key == "space"
    assert "the skill page turns with space (the game's key for it)" in bot.lines

    bot = TB(a=TLink(skills=dict(skills, attack_keys={"1": 1}), modes={"swap_attack": True}))
    combat.SkillBook(as_parts(bot))
    assert not any("skill page turns" in line for line in bot.lines)     # one page: nothing to turn
