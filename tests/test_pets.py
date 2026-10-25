"""The pet out is looked after: found by its id, a red potion by its HP, a blue one by its MP
(the pet HP key's answer), and a Rice Roll when its amiability is low. Live, pets died again
and again all night with over a hundred pet potions in the bag and none ever used."""

import pytest

from unclefarmer.engine import pets

PET_ID = 10000001


class Bot:
    """Just what pets.look_after uses."""

    def __init__(self, hotkeys=None, bag=None, in_fight=False):
        self.p = {"pets": [{"name": "Wild Eagle Sky", "use": True}], "vitals_key": "r", "points_key": "t",
                  "pet_food_below": 70, "pet_food": "Pet Rice Roll", "bag_down": "down"}
        self.hotkeys = hotkeys or {}           # item name -> key
        self.bag = bag or {}                   # item name -> count
        self.in_fight = in_fight
        self.pet_out = {"id": PET_ID, "name": "Wild Eagle Sky", "usable_at": -1}
        self.pet_described = {PET_ID: 1e12}    # no visit to the pet panel in these tests
        self.lines, self.pressed, self.used = [], [], []

    def log(self, line, speak=False):
        self.lines.append(line)

    def key(self, k, ms=None):
        self.pressed.append(k)

    def use_item(self, name):
        self.used.append(name)
        return True

    def bag_rows(self):
        return [{"name": n, "count": c} for n, c in self.bag.items()]

    def hotkey_of(self, name):
        return self.hotkeys.get(name)

    def attackers(self):
        return set()

    def potion(self, kind):
        names = {"pet_hp": "Pet Large Red Potion", "pet_mp": "Pet Large Blue Potion"}
        name = names.get(kind)
        if name in self.hotkeys:
            return self.hotkeys[name], name
        return (None, name) if self.bag.get(name) else (None, None)


def snap(hp=120000, maxhp=120000, owner="Hero", name="Wild Eagle Sky belongs to someone", **self_extra):
    me = {"id": 4242, "name": owner, "hp": 90000, "maxhp": 90000}
    me.update(self_extra)
    pet = {"id": PET_ID, "kind": "player", "name": name, "hp": hp, "maxhp": maxhp, "x": 10, "y": 10}
    return {"self": me, "entities": [pet]}


@pytest.fixture(autouse=True)
def pet_potion_texts(monkeypatch):
    texts = {"Pet Large Red Potion": {"pct": 15.0, "flat": 3000, "cd": 22.0, "from": "Pet Large Red Potion"},
             "Pet Large Blue Potion": {"pct": 15.0, "flat": 3000, "cd": 11.0, "from": "Pet Large Blue Potion"}}
    monkeypatch.setattr(pets.potions, "info", lambda name: texts.get(name))


def test_the_pet_is_found_by_its_id_even_when_its_name_says_nothing_of_us():
    b = Bot(hotkeys={"Pet Large Red Potion": "5"})
    pets.look_after(b, snap(hp=60000))
    assert "5" in b.pressed
    assert any("pet HP 50%" in line for line in b.lines), b.lines


def test_a_pet_red_potion_goes_as_soon_as_one_potion_s_worth_is_missing():
    # Pet Large Red: 15% + 3000 of 120,000 is 17.5%, so it is drunk below 82.5%
    b = Bot(hotkeys={"Pet Large Red Potion": "5"})
    pets.look_after(b, snap(hp=100000))       # 83.3%
    assert "5" not in b.pressed
    pets.look_after(b, snap(hp=98000))        # 81.7%
    assert "5" in b.pressed
    b.pressed.clear()
    pets.look_after(b, snap(hp=90000))        # within the potion's 22 s cooldown: not again
    assert "5" not in b.pressed


def test_a_pet_blue_potion_by_the_mp_the_pet_hp_key_reads():
    b = Bot(hotkeys={"Pet Large Blue Potion": "6"})
    info = {"name": "Wild Eagle Sky", "amiability": 90.0, "hp": 120000, "maxhp": 120000, "mp": 100000,
            "maxmp": 110000, "age_ms": 5000}
    pets.look_after(b, snap(pet_info=info, pet_vitals={"hp": 120000, "mp": 60000, "age_ms": 400}))
    assert "shift+r" in b.pressed             # the pet HP key, for the MP
    assert "6" in b.pressed
    assert any(line.startswith("pet MP 54%") for line in b.lines), b.lines


def test_no_pet_potion_from_the_bag_in_a_fight_but_after_it():
    b = Bot(bag={"Pet Large Red Potion": 113}, in_fight=True)
    pets.look_after(b, snap(hp=50000))
    assert b.used == []
    b.in_fight = False
    pets.look_after(b, snap(hp=50000))
    assert b.used == ["Pet Large Red Potion"]


def test_a_rice_roll_when_the_amiability_is_low():
    b = Bot(bag={"Pet Rice Roll": 57})
    pets.look_after(b, snap(points={"raw": "120", "fusion": 99.0, "pet": 65.0, "age_ms": 2000}))
    assert b.used == ["Pet Rice Roll"]
    b.used.clear()
    pets.look_after(b, snap(points={"raw": "120", "fusion": 99.0, "pet": 65.0, "age_ms": 2500}))
    assert b.used == []                        # not again before a new reading can show it worked


def test_no_rice_roll_while_the_amiability_is_fine_or_feeding_is_off():
    b = Bot(bag={"Pet Rice Roll": 57})
    pets.look_after(b, snap(points={"raw": "1", "fusion": 99.0, "pet": 85.0, "age_ms": 2000}))
    assert b.used == []
    b.p["pet_food_below"] = 0
    pets.look_after(b, snap(points={"raw": "1", "fusion": 99.0, "pet": 20.0, "age_ms": 2000}))
    assert b.used == []


def test_the_newer_amiability_reading_wins():
    b = Bot()
    info = {"name": "Wild Eagle>><< Sky", "amiability": 40.0, "hp": 1, "maxhp": 1, "mp": 1, "maxmp": 1,
            "age_ms": 600000}
    n = pets.numbers(b, snap(points={"raw": "1", "fusion": 1.0, "pet": 80.0, "age_ms": 1000}, pet_info=info),
                     snap()["entities"][0])
    assert n["amiability"] == 80.0 and n["maxmp"] == 1 and n["described"]
