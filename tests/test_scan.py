"""Reading a character's setup into a profile: the all-in-one scan, folding it into a profile,
saving it, and the screen-reader summary.
"""

import json

from unclefarmer import paths, scan

HUNTER_SKILLS = ["Arcane Shot", "Aimed Shot", "Rapid Arrows"]

INPUT_INI = """\
W_MOVE_NORTH=W
W_MOVE_SOUTH=S
W_MOVE_WEST=A
W_MOVE_EAST=D
W_WEST_TURN=K
W_NORTH_TURN=O
W_SOUTH_TURN=L
W_EAST_TURN=SEMICOLON
W_ATTACK1=J
W_ATTACK2=Y
W_SET_ATTACKMODE=Z
W_SELF_INFO=O
W_USE1=1
W_USE2=2
"""


class FakeLink:
    def __init__(self, answers):
        self.answers = answers

    def json(self, line):
        return self.answers.get(line)


def game_folder(tmp_path, phrase_lines=()):
    """A pretend game install with the player's keymap (and quick phrases)."""
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True)
    (cfg / "input.ini").write_text(INPUT_INI, encoding="latin-1")
    if phrase_lines:
        user = tmp_path / "users" / "1001"
        user.mkdir(parents=True)
        (user / "phrase").write_text("\n".join(phrase_lines), encoding="utf-8")
    return str(tmp_path)


def hunter_answers():
    me = {"id": 1001, "name": "Hero", "maxhp": 100000, "sheet": {"level": 80}}
    bag = [{"name": "Large Red Potion", "count": 200, "type": 23387, "uid": 5001},
           {"name": "Large Blue Potion", "count": 200, "type": 23397, "uid": 5002},
           {"name": "Hearthstone", "count": 1, "type": 500, "uid": 77},
           {"name": "Green Shard", "count": 5, "type": 900, "uid": 99}]
    return {
        "mem": {"ok": True, "map": {"id": 1041, "name": "central plains"}, "self": me},
        "mem skills": {"combat": [{"id": 1 + i, "name": n} for i, n in enumerate(HUNTER_SKILLS)],
                       "attack_keys": {"1": 1, "2": 2},
                       "talents": [{"name": "Call of the Wilderness", "b": 1},
                                   {"name": "Steady Hands", "b": 0}]},
        "mem bagfull": bag,
        "mem slots": {"1": [23387, 5001], "2": [23397, 5002]},
        "mem beasts": [{"name": "Behemoth Beast"}],
        "mem pets": [{"name": "Little Cat"}],
        "mem equipment": [{"name": "Sword", "equipped": 1}, {"name": "Shield", "equipped": 0}],
        "mem modes": {"options": 1025917, "swap_attack": True, "auto_lock_target": True,
                      "skip_enemy_corpses": True},
    }


# ---------------------------------------------------------------- the scan
def test_scan_reads_the_character_and_its_setup(tmp_path):
    link = FakeLink(hunter_answers())
    folder = game_folder(tmp_path, phrase_lines=["1,/6", "2,/0 hello", "3,some text"])
    out = scan.scan(link, folder)
    assert out["name"] == "Hero" and out["maxhp"] == 100000 and out["level"] == 80
    assert out["class"] == "Hunter" and out["skills"] == sorted(HUNTER_SKILLS)
    assert out["keys"]["move_keys"] == {"up": "w", "down": "s", "left": "a", "right": "d"}
    assert out["keys"]["target_keys"] == {"west": "k", "north": "o", "south": "l", "east": ";"}
    assert out["keys"]["pk_key"] == "z" and out["keys"]["sheet_key"] == "o"
    assert out["skill_keys"] == [
        {"slot": 1, "page": 1, "key": "j", "skill": "Arcane Shot"},
        {"slot": 2, "page": 1, "key": "y", "skill": "Aimed Shot"}]
    assert out["talents"] == ["Call of the Wilderness", "Steady Hands"]
    assert out["active_talents"] == ["Call of the Wilderness"]
    assert isinstance(out["combos"], list)
    assert out["hotkeys"][0]["item"] == "Large Red Potion" and out["hotkeys"][0]["count"] == 200
    assert out["hotkeys"][0]["key"] == "1" and out["hotkeys"][0]["type"] == 23387
    assert out["phrases"] == {"1": "/6", "2": "/0+text", "3": "text"}
    assert out["potions_in_bag"] == ["Large Blue Potion", "Large Red Potion"]
    assert out["bag"] == [{"name": "Green Shard", "count": 5}, {"name": "Hearthstone", "count": 1},
                          {"name": "Large Blue Potion", "count": 200},
                          {"name": "Large Red Potion", "count": 200}]
    assert out["beasts"] == [{"name": "Behemoth Beast", "kind": "swift", "speed": 2.6, "level": 40}]
    assert out["pets"] == ["Little Cat"]
    assert out["gear"] == ["Sword"]                     # only the equipped one
    assert out["game_options"] == {"swap_attack": True, "auto_lock_target": True,
                                   "skip_enemy_corpses": True}


def test_scan_without_a_game_folder_reports_what_it_can(tmp_path):
    link = FakeLink(hunter_answers())
    out = scan.scan(link, "")
    assert out["name"] == "Hero" and out["keys"] == {}
    # the attack keys fall back to the game's own defaults
    assert out["skill_keys"][0]["key"] == "y"


def test_scan_of_a_game_that_will_not_answer():
    link = FakeLink({"mem": {"error": "not running"}})
    out = scan.scan(link, "")
    assert out["name"] is None and out["class"] is None and out["skills"] == []
    assert out["hotkeys"] == [] and out["bag"] == [] and out["pets"] == []


def test_json_swallows_errors_and_error_answers():
    assert scan._json(FakeLink({"x": {"error": "no"}}), "x") is None
    assert scan._json(FakeLink({"x": {"a": 1}}), "x") == {"a": 1}
    assert scan._json(FakeLink({}), "missing") is None

    class Boom:
        def json(self, line):
            raise OSError("closed")

    assert scan._json(Boom(), "x") is None


# ---------------------------------------------------------------- applying
def test_apply_learns_keys_beasts_and_pets():
    p = {}
    s = {"keys": {"move_keys": {"up": "w"}, "pk_key": "z"},
         "skill_keys": [{"slot": 1, "page": 1, "key": "j", "skill": "A"},
                        {"slot": 11, "page": 2, "key": "y", "skill": "B"}],
         "beasts": [{"name": "Behemoth Beast"}], "pets": ["Little Cat", "Puppy"]}
    out = scan.apply(p, s)
    assert out is p and p["scan"] is s
    assert p["move_keys"] == {"up": "w"} and p["pk_key"] == "z"
    assert p["attack_keys"] == ["j"]                    # the first page only
    assert p["speed_beast"] == "auto" and p["fight_beast"] == "none"
    assert p["pets"] == [{"name": "Little Cat", "use": False}, {"name": "Puppy", "use": False}]


def test_apply_keeps_the_choices_still_owned_and_drops_the_rest():
    p = {"speed_beast": "Behemoth Beast", "fight_beast": "none",
         "pets": [{"name": "Little Cat", "use": True}, {"name": "Goldy", "use": True}]}
    scan.apply(p, {"beasts": [{"name": "Behemoth Beast"}], "pets": ["Little Cat", "Puppy"]})
    assert p["speed_beast"] == "Behemoth Beast"          # still owned
    assert p["fight_beast"] == "none"                    # "none" is never overwritten
    assert p["pets"] == [{"name": "Little Cat", "use": True}, {"name": "Puppy", "use": False}]


def test_apply_clears_a_beast_that_is_gone():
    p = {"speed_beast": "Goldy", "fight_beast": "Behemoth Beast"}
    scan.apply(p, {"beasts": [{"name": "Behemoth Beast"}]})
    assert p["speed_beast"] == "auto"                    # speed defaults back to auto
    assert p["fight_beast"] == "Behemoth Beast"          # still owned


def test_apply_of_an_empty_scan_only_sets_the_defaults():
    p = {"attack_keys": ["a"]}
    scan.apply(p, {})
    assert p["scan"] == {} and p["attack_keys"] == ["a"]
    assert p["speed_beast"] == "auto" and p["fight_beast"] == "none" and p["pets"] == []


# ---------------------------------------------------------------- saving and summary
def test_scan_and_save_folds_it_into_the_file(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    p, s = scan.scan_and_save(FakeLink(hunter_answers()), "Hero", "")
    assert s["name"] == "Hero" and p["scan"] is s and s["class"] == "Hunter"
    with open(tmp_path / "Hero.json", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["scan"]["name"] == "Hero"


def test_summary_reads_the_scan_back(monkeypatch):
    monkeypatch.setattr(scan.gamekeys, "_game", lambda: {})     # the default phrase keys
    assert scan.summary(None) == ""
    assert scan.summary({}) == ""
    s = {"name": "Hero", "class": "Hunter", "level": 80, "when": "2026-01-01 10:00",
         "skill_keys": [{"slot": 1, "page": 1, "key": "j", "skill": "Arcane Shot"},
                        {"slot": 11, "page": 2, "key": "o", "skill": "Aimed Shot"}],
         "active_talents": ["Call of the Wilderness"], "combos": ["A then B"],
         "hotkeys": [{"slot": 1, "key": "1", "item": "Large Red Potion", "count": 200},
                     {"slot": 2, "key": "2", "item": None, "count": 0}],
         "phrases": {"1": "/6"},
         "beasts": [{"name": "Behemoth Beast", "kind": "swift", "speed": 2.6, "level": 40}],
         "pets": ["Little Cat"]}
    text = scan.summary(s)
    assert "Hero, Hunter, level 80 - scanned 2026-01-01 10:00" in text
    assert "Skills, page 1: j Arcane Shot" in text
    assert "Skills, page 2: o Aimed Shot" in text
    assert "Activated talents: Call of the Wilderness" in text
    assert "Combos used in fights: A then B" in text
    assert "Hotkeys: 1 Large Red Potion x200, 2 (empty)" in text
    assert "Quick phrases: shift+1 /6" in text and "empty:" in text
    assert "Fusion beasts: Behemoth Beast (swift, speed +2.6)" in text
    assert "Pets: Little Cat" in text
