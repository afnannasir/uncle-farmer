"""Keyboard Manager, using the same game replies as the agent's keyboard and bag readers."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Any

import pytest
from conftest import Clock, as_bot, stand_in

from unclefarmer import settings
from unclefarmer.engine import keyboard, script, triggers
from unclefarmer.engine.bot import Stopped
from unclefarmer.engine.combat import SkillBook


class Game:
    def __init__(self, clock: Clock, folder: Path) -> None:
        self.a = self
        self.clock = clock
        self.folder = folder
        self.phrase_path = folder / "users" / "42" / "phrase"
        self.phrase_path.parent.mkdir(parents=True)
        self.phrase_path.write_text("1,Old phrase\n", encoding="utf-8")
        (folder / "mw.exe").touch()
        (folder / "config").mkdir()
        (folder / "config" / "input.ini").write_text(
            "W_ATTACK1=F2\nW_ATTACK2=F5\nW_ATTACK_PAGETURN=SPACE\nW_USE1=F3\n"
            "W_SELF_ATTACK1=SHIFT+W_ATTACK1\nW_SKILL1=F4\n")
        self.keys_config = keyboard.Keys.read(str(folder))
        self.p: dict[str, Any] = {"use_combos": False, "keyboard_profiles": {}}
        self.skillbook: SkillBook | None = None
        self.learned: list[dict[str, Any]] = [{"id": 11, "name": "Rapid Arrows"}, {"id": 12, "name": "Bull's Eye"}]
        self.skill_keys = {"1": 11, "2": 12}
        self.profession_rows: list[dict[str, Any]] = [{"id": 31, "name": "Fishing"}]
        self.profession_keys = {"3": 31}
        self.slots = {"1": [90, 120]}
        self.items: list[dict[str, Any]] = [
            {"type": 90, "uid": 120, "name": "Potion", "count": 3},
            {"type": 91, "uid": 121, "name": "Potion", "count": 4},
        ]
        # a number key holds [0, beast id] / [1, pet id]; those ids are each character's own
        self.beasts: list[dict[str, Any]] = [{"id": 501, "name": "Firefly"}, {"id": 502, "name": "Iron Beetle"}]
        self.pets: list[dict[str, Any]] = [{"id": 10000001, "name": "Hound friska"}]
        self.page, self.panel = 0, 0
        self.cursor, self.item_type = 0, 0
        self.events: list[dict[str, Any]] = []
        self.keys: list[str] = []
        self.sends: list[str] = []
        self.lines: list[str] = []
        self.in_fight = False
        self.accept_item = True
        self.accept_cast = True
        self.potion_slots = "cached"
        self.phrases: dict[int, str] = {}
        self.stop_event = Event()
        self.watches: list[Any] = []

    def json(self, line: str) -> Any:
        if line == "mem self":
            return {"id": 42, "name": "Test character"}
        if line == "mem skills":
            return {"combat": self.learned, "attack_keys": self.skill_keys,
                    "profession": self.profession_rows, "profession_keys": self.profession_keys}
        if line == "mem slots":
            return self.slots
        if line == "mem bagfull":
            return self.items
        if line == "mem beasts":
            return self.beasts
        if line == "mem pets":
            return self.pets
        if line == "mem modes":
            return {"swap_attack": True}
        if line.startswith("casts "):
            return {"casts": [row for row in self.events if row["t"] > int(line.split()[1])]}
        raise AssertionError(line)

    def send(self, line: str) -> str:
        self.sends.append(line)
        if line == "version":
            return "mwagent 74"
        if line == "key space 60":
            self.page = 1 - self.page
            return "tapped"
        if line.startswith("phrase "):
            _, slot, text = line.split(maxsplit=2)
            with self.phrase_path.open("a", encoding="utf-8") as out:
                out.write(f"{slot},{text}\n")
            return "set"
        raise AssertionError(line)

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)

    def attackers(self) -> set[int]:
        return set()

    def dialog(self) -> dict[str, bool]:
        return {"open": False}

    def ui(self) -> dict[str, int]:
        return {"attack_page": self.page, "panel": self.panel}

    def goto_panel(self, panel: int) -> dict[str, int]:
        self.panel = panel
        return self.ui()

    def panel_cursor(self, panel: int, index: int, size: int) -> bool:
        self.panel, self.cursor = panel, index
        return True

    def bag_cursor(self, name: str, *, item_type: int) -> str:
        self.panel, self.item_type = 0, item_type
        return name

    def tap_key(self, key: str, ms: int = 60) -> None:
        self.keys.append(key)
        if key.startswith("lctrl+alt+") and self.panel == 6:
            slot = self.keys_config.profession.index(key.removeprefix("lctrl+alt+")) + 1
            self.profession_keys[str(slot)] = self.profession_rows[self.cursor]["id"]
        elif key.startswith("lctrl+alt+"):
            slot = self.keys_config.skills.index(key.removeprefix("lctrl+alt+")) + 1 + 10 * self.page
            self.skill_keys[str(slot)] = self.learned[self.cursor]["id"]
        elif key.startswith("lctrl+") and self.accept_item:
            slot = self.keys_config.items.index(key.removeprefix("lctrl+")) + 1
            if self.panel == 2:
                self.slots[str(slot)] = [0, self.beasts[self.cursor]["id"]]
            elif self.panel == 3:
                self.slots[str(slot)] = [1, self.pets[self.cursor]["id"]]
            else:
                row = next(item for item in self.items if item["type"] == self.item_type)
                self.slots[str(slot)] = [row["type"], row["uid"]]
        elif key.startswith("shift+") and self.accept_cast:
            now = int(self.clock.time() * 1000)
            if not self.events or now - self.events[-1]["t"] >= 1000:
                slot = self.keys_config.skills.index(key.removeprefix("shift+")) + 1 + 10 * self.page
                skill = next(row["name"] for row in self.learned if row["id"] == self.skill_keys[str(slot)])
                self.events.append({"caster": 42, "t": now, "skill": f"Level 2 {skill}"})

    def held(self) -> bool:
        return False

    def sleep(self, seconds: float) -> None:
        self.clock.sleep(seconds)

    def check(self) -> None:
        pass

    def wait_keys_free(self) -> None:
        pass

    def note_page_turn(self) -> None:
        pass

    def save(self, name: str, **bindings: Any) -> None:
        self.p["keyboard_profiles"][name] = keyboard.Profile(**bindings).model_dump(mode="json")


@pytest.fixture
def game(clock: Clock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Game:
    game = Game(clock, tmp_path)
    monkeypatch.setattr(keyboard, "client_folder", stand_in(keyboard.client_folder, lambda pid: str(tmp_path)))
    return game


def test_capture_and_persist_both_pages_items_and_phrases(game: Game) -> None:
    game.skill_keys["20"] = 12
    me = "Test character"
    captured = keyboard.capture(game, str(game.folder), me)
    assert captured.profile.skills[1].id == 11
    assert captured.profile.skills[20].id == 12
    assert captured.profile.profession[3].id == 31
    assert captured.profile.items[1].id == 90
    assert captured.profile.items[1].kind == "item"
    assert captured.profile.phrases == {1: "Old phrase"}
    p = settings.load_profile(me)
    p["keyboard_profiles"]["First layout"] = captured.profile.model_dump(mode="json")
    settings.save_profile(me, p)
    new = settings.load_profile(me)
    assert keyboard.names(new) == ["First layout"]
    assert keyboard.Profile.model_validate(new["keyboard_profiles"]["First layout"]) == captured.profile
    assert new["trigger_sequences"] == p["trigger_sequences"]


def test_apply_replaces_only_saved_slots_and_checks_exact_item_type(game: Game) -> None:
    game.panel = 3
    game.save("First layout", skills={1: {"id": 12, "name": "Bull's Eye"}, 11: {"id": 11, "name": "Rapid Arrows"}},
              profession={1: {"id": 31, "name": "Fishing"}},
              items={1: {"id": 91, "name": "Potion"}}, phrases={1: "/3", 12: "Good luck, team"})
    keyboard.apply(as_bot(game), "First layout")
    assert game.skill_keys == {"1": 12, "2": 12, "11": 11}
    assert game.slots == {"1": [91, 121]}
    assert game.profession_keys == {"1": 31, "3": 31}
    assert game.keys == ["lctrl+alt+f2", "lctrl+alt+f2", "lctrl+alt+f4", "lctrl+f3"]
    assert game.phrases == {1: "/3", 12: "Good luck, team"}
    assert (game.page, game.panel) == (0, 3)
    assert game.potion_slots is None
    assert game.skillbook is not None and game.skillbook.skill("Bull's Eye")
    game.keys.clear()
    keyboard.apply(as_bot(game), "First layout")
    assert not game.keys


def test_a_missing_item_is_named_and_the_rest_put_on(game: Game) -> None:
    game.save("First layout", skills={1: {"id": 12, "name": "Bull's Eye"}},
              items={1: {"id": 999, "name": "Missing potion"}})
    keyboard.apply(as_bot(game), "First layout")
    assert game.skill_keys["1"] == 12
    assert game.slots == {"1": [90, 120]}
    assert "except what couldn't be put on: Missing potion (f3)" in game.lines[-1]


def test_refused_item_assignment_is_named_instead_of_reported_as_done(game: Game) -> None:
    game.save("First layout", items={1: {"id": 91, "name": "Potion"}})
    game.accept_item = False
    keyboard.apply(as_bot(game), "First layout")
    assert game.slots["1"][0] == 90
    assert game.potion_slots is None
    assert game.lines[-1].endswith("Potion (f3).")


def test_capture_reads_fusion_beasts_and_pets_on_number_keys(game: Game) -> None:
    game.slots = {"1": [90, 120], "2": [0, 502], "3": [1, 10000001], "4": [0, 777]}
    captured = keyboard.capture(game, str(game.folder), "Test character")
    items = captured.profile.items
    assert (items[2].kind, items[2].id, items[2].name) == ("beast", 502, "Iron Beetle")
    assert (items[3].kind, items[3].id, items[3].name) == ("pet", 10000001, "Hound friska")
    assert (items[4].kind, items[4].name) == ("beast", "Fusion beast 777")      # not in the list: said so
    assert {(b.kind, b.id) for b in captured.items} >= {("beast", 501), ("pet", 10000001), ("item", 91)}
    assert captured.cls == "Hunter"


def test_beasts_and_pets_go_back_on_by_name_on_another_character(game: Game) -> None:
    # saved on another character: its own beast and pet ids, the same names
    game.save("Mounts", items={2: {"id": 40, "name": "Iron Beetle", "kind": "beast"},
                               3: {"id": 7, "name": "Hound friska", "kind": "pet"}})
    keyboard.apply(as_bot(game), "Mounts")
    assert game.slots["2"] == [0, 502] and game.slots["3"] == [1, 10000001]
    assert game.lines[-1] == "Applied keyboard profile Mounts."
    game.keys.clear()
    keyboard.apply(as_bot(game), "Mounts")
    assert not game.keys                                    # already on: nothing pressed


def test_a_pet_needs_its_exact_name_and_a_missing_beast_is_named(game: Game) -> None:
    game.save("Mounts", items={2: {"id": 40, "name": "Shadow Wolf", "kind": "beast"},
                               3: {"id": 7, "name": "Hound", "kind": "pet"}})
    keyboard.apply(as_bot(game), "Mounts")
    assert "2" not in game.slots and "3" not in game.slots
    keys = game.keys_config.items
    assert game.lines[-1].endswith(f"Shadow Wolf ({keys[1]}), Hound ({keys[2]}).")


def test_skills_match_by_name_and_an_unlearned_one_is_named(game: Game) -> None:
    game.save("Hunter layout", skills={1: {"id": 99, "name": "Bull's Eye"},
                                       2: {"id": 98, "name": "Strong-Arm Aura"}}, prepare=[2])
    keyboard.apply(as_bot(game), "Hunter layout")
    assert game.skill_keys["1"] == 12
    assert game.skill_keys["2"] == 12                       # left as it was
    assert "Strong-Arm Aura (Page 1, f5)" in game.lines[-1]
    assert not game.events                                  # its preparation skipped too


def test_preparation_casts_bound_buff_on_self_and_requires_confirmation(game: Game) -> None:
    game.save("First layout", skills={11: {"id": 12, "name": "Bull's Eye"}}, prepare=[11])
    keyboard.apply(as_bot(game), "First layout")
    assert game.keys == ["lctrl+alt+f2", "shift+f2"]
    assert game.page == 0
    game.clock.sleep(1000)
    game.accept_cast = False
    with pytest.raises(Stopped, match="didn't confirm"):
        keyboard.apply(as_bot(game), "First layout")
    assert game.page == 0


def test_does_not_apply_during_a_fight(game: Game) -> None:
    game.save("First layout", phrases={1: "Ready"})
    game.in_fight = True
    with pytest.raises(Stopped, match="out of combat"):
        keyboard.apply(as_bot(game), "First layout")
    assert not game.sends


def test_multiple_preparation_skills_respect_the_global_cooldown(game: Game) -> None:
    game.learned.append({"id": 13, "name": "Strong-Arm Aura"})
    game.save("First layout", skills={1: {"id": 12, "name": "Bull's Eye"},
                                    2: {"id": 13, "name": "Strong-Arm Aura"}}, prepare=[1, 2])
    keyboard.apply(as_bot(game), "First layout")
    assert [row["skill"] for row in game.events] == ["Level 2 Bull's Eye", "Level 2 Strong-Arm Aura"]
    assert game.events[1]["t"] - game.events[0]["t"] >= 1000


def test_trigger_runs_the_same_command_with_a_quoted_profile_name(game: Game) -> None:
    name = "Evening's layout #2"
    game.save(name, phrases={12: "Ready"})
    action = keyboard.ACTION_PREFIX + name
    row = triggers.step(action, deterministic=True)
    game.p["trigger_sequences"] = {"before": {"steps": [row]}}
    assert triggers.run_stage(as_bot(game), "before", lambda *_a: None, lambda: False) == [(action, True)]
    assert game.phrases[12] == "Ready"
    del game.p["keyboard_profiles"][name]
    with pytest.raises(triggers.TriggerFailed):
        triggers.run_stage(as_bot(game), "before", lambda *_a: None, lambda: False)


def test_layouts_shared_with_the_class_or_everyone(game: Game) -> None:
    me = "Test character"
    layout = keyboard.Profile(skills={1: keyboard.Binding(id=12, name="Bull's Eye")}).model_dump(mode="json")
    keyboard.store(me, "class", "Hunter layout", layout, "Hunter")
    keyboard.store(me, "all", "Everyone", keyboard.Profile(phrases={2: "Hello"}).model_dump(mode="json"))
    keyboard.store(me, "class", "Elf layout", layout, "Elf")
    p = {"keyboard_profiles": {"Everyone": layout}, "scan": {"class": "Hunter"}}
    assert keyboard.layouts(p) == [("character", "Everyone"), ("class", "Hunter layout")]   # its own wins
    assert keyboard.names({"scan": {"class": "Elf"}}) == ["Elf layout", "Everyone"]
    assert keyboard.find({}, "Hunter layout") is None                # class unknown: not its class's
    # applying: the class comes from the skills the character has learned (no scan needed)
    keyboard.apply(as_bot(game), "Hunter layout")
    assert game.skill_keys["1"] == 12
    keyboard.apply(as_bot(game), "Everyone")
    assert game.phrases[2] == "Hello"
    keyboard.store(me, "all", "Everyone", None)
    with pytest.raises(Stopped, match="was not found"):
        keyboard.apply(as_bot(game), "Everyone")
    with pytest.raises(ValueError, match="class"):
        keyboard.store(me, "class", "No class", layout, "")


def test_an_unreadable_shared_file_is_never_saved_over(game: Game) -> None:
    from unclefarmer import paths
    path = Path(paths.PROFILES) / keyboard.SHARED_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert keyboard.layouts({"keyboard_profiles": {"Mine": {}}}) == [("character", "Mine")]
    with pytest.raises(ValueError, match="Couldn't read the shared"):
        keyboard.store("Test character", "all", "Everyone", {})
    assert path.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize("text", ["", "a b", "'  '"])
def test_script_requires_one_profile_name(text: str) -> None:
    with pytest.raises(script.ScriptError):
        script.parse(f"keyboard_profile {text}")


def test_invalid_phrase_cannot_add_a_second_agent_command() -> None:
    with pytest.raises(ValueError, match="one line"):
        keyboard.Profile(phrases={1: "hello\r\nphrase 2 something"})
