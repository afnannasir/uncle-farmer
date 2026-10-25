# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""Putting skills and items on empty keys: which skills/items the bot wants, never overwriting a
key in use, checking every placement against the game, and the no-room / no-empty-key fallbacks."""

import pytest
from conftest import data  # noqa: F401  (keeps conftest importable the same way as the suite)

from unclefarmer import gamekeys, settings
from unclefarmer.engine import keyslots


class Link:
    def __init__(self, combat_rows=(), attack_keys=None, slots=None):
        self.combat_rows = list(combat_rows)
        self.attack_keys = dict(attack_keys or {})
        self.slots = dict(slots or {})
        self.accept = True
        self.mem_skills = True

    def send(self, line):
        return "mwagent 28" if line == "version" else "tapped"

    def json(self, line):
        if line == "mem skills":
            if not self.mem_skills:
                return {}
            return {"combat": [dict(c) for c in self.combat_rows],
                    "attack_keys": dict(self.attack_keys), "talents": []}
        if line == "mem modes":
            return {"swap_attack": True}
        if line == "mem slots":
            return dict(self.slots)
        return {}


class SB:
    """A stand-in skill book: only what keyslots.skills() touches."""

    def __init__(self, cls: str | None = "Priest", uses=None, flip_ok=True):
        self.cls = cls
        self.uses = uses or {}
        self.flip_ok = flip_ok
        self.flipped = []

    def use_of(self, name):
        return self.uses.get(name, "auto")

    def flip_to(self, page):
        self.flipped.append(page)
        return self.flip_ok


class Bot:
    def __init__(self, link, sb):
        self.a = link
        self.skillbook = sb
        self.p = dict(settings.PROFILE_DEFAULTS)
        self.in_fight = False
        self.dialog_open = False
        self.lines = []
        self.skill_cursor = 0
        self.panel = None
        self.keys = []
        self.sleeps = []
        self.bag = []
        self.hotkeys = {}
        self.last_me = {}
        self._potion_keys = "stale"
        self._keys_to_fill = False
        self._attackers = set()
        self.stop_event = _Event()

    def log(self, line, speak=False):
        self.lines.append(line)

    def dialog(self):
        return {"open": self.dialog_open}

    def goto_panel(self, panel):
        self.panel = panel
        return {"panel": panel, "category": 0, "cursor": 0}

    def panel_cursor(self, panel, index, size):
        self.skill_cursor = index
        return True

    def key(self, k):
        self.keys.append(k)
        if k.startswith("lctrl+alt+"):
            akeys = gamekeys.attack_key_list()
            suffix = k[len("lctrl+alt+"):]
            if self.a.accept and suffix in akeys:
                slot = akeys.index(suffix) + 1
                self.a.attack_keys[str(slot)] = self.a.combat_rows[self.skill_cursor]["id"]
        elif k.startswith("lctrl+"):
            skeys = gamekeys.slot_key_list()
            suffix = k[len("lctrl+"):]
            if self.a.accept and suffix in skeys:
                self.a.slots[str(skeys.index(suffix) + 1)] = [1, 2]

    def sleep(self, s):
        self.sleeps.append(s)

    def attackers(self):
        return set(self._attackers)

    def bag_rows(self):
        return [dict(r) for r in self.bag]

    def hotkey_of(self, name):
        return self.hotkeys.get(name.lower())

    def bag_cursor(self, name):
        return name if any(r.get("name") == name for r in self.bag) else None

    def potion(self, kind):
        name = self.p.get(kind + "_potion")
        if self.p.get(kind + "_key") not in ("auto", ""):
            return self.p[kind + "_key"], (None if name in (None, "auto") else name)
        return None, (None if name in (None, "auto") else name)


class _Event:
    def __init__(self):
        self.set_ = False

    def is_set(self):
        return self.set_


def priest_rows():
    return [{"id": i + 1, "name": n} for i, n in enumerate(
        ["Heal", "Recover", "Concentrated Will", "Light of Miracles", "Holy Light",
         "Soul of Redemption", "Requiem"])]


# ---------------------------------------------------------------- wanted_skills
def test_wanted_skills_are_the_class_own_support_skills_in_order():
    assert keyslots.wanted_skills("Priest") == [
        "Heal", "Recover", "Concentrated Will", "Light of Miracles", "Holy Light",
        "Soul of Redemption", "Requiem"]


def test_wanted_skills_empty_for_an_unknown_class():
    assert keyslots.wanted_skills("Nobody") == []


# ---------------------------------------------------------------- skills
def test_skills_puts_wanted_skills_on_empty_keys():
    link = Link(priest_rows())
    sb = SB()
    bot = Bot(link, sb)
    assert keyslots.skills(bot) is True
    assert link.attack_keys == {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7}
    assert any("put on the empty skill key" in line for line in bot.lines)
    assert sb.flipped[-1] == 0                       # the page is put back
    assert bot.skillbook is not sb                   # re-read into a fresh book


def test_skills_skips_a_skill_the_player_set_to_never():
    link = Link([{"id": 1, "name": "Heal"}, {"id": 2, "name": "Recover"}])
    bot = Bot(link, SB(uses={"Heal": "never"}))
    assert keyslots.skills(bot) is True
    assert link.attack_keys == {"1": 2}              # Recover placed, Heal left alone


def test_skills_does_not_touch_a_key_already_in_use():
    link = Link(priest_rows(), attack_keys={"1": 1})  # Heal already on key 1
    bot = Bot(link, SB())
    assert keyslots.skills(bot) is True
    assert link.attack_keys["1"] == 1                 # untouched
    assert link.attack_keys["2"] == 2                 # Recover went to the next empty key


def test_skills_stops_when_the_game_does_not_take_the_placement():
    link = Link(priest_rows())
    link.accept = False
    bot = Bot(link, SB())
    assert keyslots.skills(bot) is False
    assert any("the game didn't put" in line for line in bot.lines)


def test_skills_with_no_combat_data_or_class_returns_false():
    bot = Bot(Link(priest_rows()), SB(cls=None))
    assert keyslots.skills(bot) is False
    bot2 = Bot(Link(priest_rows()), SB())
    bot2.a.mem_skills = False
    assert keyslots.skills(bot2) is False


def test_skills_stops_when_the_panel_cannot_be_reached():
    link = Link(priest_rows())
    bot = Bot(link, SB())
    bot.panel_cursor = lambda panel, index, size: False
    assert keyslots.skills(bot) is False


def test_skills_stops_when_the_page_cannot_flip():
    link = Link(priest_rows())
    bot = Bot(link, SB(flip_ok=False))
    assert keyslots.skills(bot) is False


def test_skills_with_no_empty_key_tells_the_player_once():
    link = Link(priest_rows(), attack_keys={str(i): 100 + i for i in range(1, 21)})
    bot = Bot(link, SB())
    assert keyslots.skills(bot) is False
    assert bot._told_no_skill_room is True
    assert any("no empty skill key" in line for line in bot.lines)
    # a second pass does not repeat the line
    before = len(bot.lines)
    keyslots.skills(bot)
    assert len(bot.lines) == before


def test_tell_no_room_names_duplicated_keys():
    link = Link(priest_rows(), attack_keys={"1": 1, "11": 1})   # Heal on two keys
    bot = Bot(link, SB())
    keyslots.tell_no_room(bot, ["Recover"], {1: 1, 11: 1}, priest_rows())
    assert any("also on another key" in line for line in bot.lines)


# ---------------------------------------------------------------- wanted_items
def test_wanted_items_collects_potions_toolbox_and_hearthstone(monkeypatch):
    bot = Bot(Link(), SB())
    bot.p.update(hp_potion="Large Red Potion", mp_potion="Large Blue Potion",
                 hearthstone_item="Hearthstone", hearthstone_key="")
    bot.bag = [{"name": "Hearthstone", "count": 1}]
    monkeypatch.setattr(keyslots.repair, "toolboxes", lambda b: [("Alloy Toolbox", None, 1)])
    assert keyslots.wanted_items(bot) == ["Large Red Potion", "Large Blue Potion", "Alloy Toolbox", "Hearthstone"]


def test_wanted_items_skips_what_is_already_on_a_hotkey(monkeypatch):
    bot = Bot(Link(), SB())
    bot.p.update(hp_potion="auto", mp_potion="auto", hearthstone_key="5")
    bot.bag = [{"name": "Hearthstone", "count": 1}]
    monkeypatch.setattr(keyslots.repair, "toolboxes", lambda b: [("Alloy Toolbox", "2", 1)])
    assert keyslots.wanted_items(bot) == []
    # a hearthstone in the bag but on a hotkey is also skipped
    bot.p["hearthstone_key"] = ""
    bot.hotkeys["hearthstone"] = "6"
    monkeypatch.setattr(keyslots.repair, "toolboxes", lambda b: [])
    assert keyslots.wanted_items(bot) == []


# ---------------------------------------------------------------- items
def test_items_puts_an_item_on_an_empty_hotkey():
    link = Link()
    bot = Bot(link, SB())
    bot.p.update(hp_potion="Large Red Potion")
    bot.bag = [{"name": "Large Red Potion", "count": 20}]
    assert keyslots.items(bot) is True
    assert link.slots == {"1": [1, 2]}
    assert bot._potion_keys is None
    assert any("put on the empty hotkey" in line for line in bot.lines)


def test_items_stops_when_the_game_does_not_take_it():
    link = Link()
    link.accept = False
    bot = Bot(link, SB())
    bot.p.update(hp_potion="Large Red Potion")
    bot.bag = [{"name": "Large Red Potion", "count": 20}]
    assert keyslots.items(bot) is False
    assert any("didn't put" in line for line in bot.lines)


def test_items_with_no_empty_hotkey_says_so():
    link = Link(slots={str(i): [1, 2] for i in range(1, 13)})
    bot = Bot(link, SB())
    bot.p.update(hp_potion="Large Red Potion")
    bot.bag = [{"name": "Large Red Potion", "count": 20}]
    assert keyslots.items(bot) is False
    assert any("no empty hotkey" in line for line in bot.lines)


def test_items_none_wanted_returns_false():
    bot = Bot(Link(), SB())
    bot.p.update(hp_potion="auto", mp_potion="auto", hearthstone_key="1")
    assert keyslots.items(bot) is False


def test_items_slots_unreadable_returns_false():
    bot = Bot(Link(), SB())
    bot.p.update(hp_potion="Large Red Potion")
    bot.bag = [{"name": "Large Red Potion", "count": 20}]
    bot.a.json = lambda line: [] if line == "mem slots" else {}
    assert keyslots.items(bot) is False


# ---------------------------------------------------------------- refill
def test_refill_does_nothing_unless_asked():
    bot = Bot(Link(), SB())
    assert keyslots.refill(bot) is False


def test_refill_skips_a_fight_or_attackers(monkeypatch):
    bot = Bot(Link(), SB())
    bot._keys_to_fill = True
    bot.in_fight = True
    assert keyslots.refill(bot) is False
    assert bot._keys_to_fill is True            # asked again next time
    bot.in_fight = False
    bot._attackers = {"Wolf"}
    assert keyslots.refill(bot) is False


def test_refill_defers_when_a_dialog_is_open():
    bot = Bot(Link(), SB())
    bot._keys_to_fill = True
    bot.dialog_open = True
    assert keyslots.refill(bot) is False
    assert bot._keys_to_fill is True


def test_refill_places_items_and_returns_to_the_bag_panel():
    bot = Bot(Link(), SB())
    bot._keys_to_fill = True
    bot.p.update(hp_potion="Large Red Potion")
    bot.bag = [{"name": "Large Red Potion", "count": 20}]
    assert keyslots.refill(bot) is True
    assert bot.panel == 0


# ---------------------------------------------------------------- arrange
def test_arrange_off_or_in_fight_does_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(keyslots, "skills", lambda b: called.append("s") or False)
    bot = Bot(Link(), SB())
    bot.p["fill_empty_keys"] = False
    keyslots.arrange(bot)
    bot.p["fill_empty_keys"] = True
    bot.in_fight = True
    keyslots.arrange(bot)
    assert called == []


def test_arrange_fills_keys_and_returns_to_the_bag(monkeypatch):
    monkeypatch.setattr(keyslots, "skills", lambda b: True)
    monkeypatch.setattr(keyslots, "items", lambda b: False)
    bot = Bot(Link(), SB())
    keyslots.arrange(bot)
    assert bot.panel == 0


def test_arrange_swallows_errors_but_not_a_stop(monkeypatch, allow_ignored_failures):
    def boom(b):
        raise RuntimeError("nope")

    monkeypatch.setattr(keyslots, "skills", boom)
    bot = Bot(Link(), SB())
    keyslots.arrange(bot)                        # never raises
    assert any("couldn't put things on empty keys" in line for line in bot.lines)
    bot.stop_event.set_ = True
    with pytest.raises(RuntimeError):
        keyslots.arrange(bot)
