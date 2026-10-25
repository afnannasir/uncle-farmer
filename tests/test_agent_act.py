# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""Agent 40: a bag item used by its uid (no arrow keys, so in a fight too), the bot's pick made the
game's target by id (no target keys), and an agent that can't do either hands both back to the keys."""


from unclefarmer.engine.botparts.bag import BagMixin
from unclefarmer.engine.botparts.fighting import FightingMixin
from unclefarmer.engine.botparts.health import HealthMixin

BAG = [{"uid": 41, "name": "Large Red Potion", "count": 3}, {"uid": 42, "name": "Pet Large Red Potion", "count": 0},
       {"uid": 43, "name": "Hearthstone", "count": 1}]


class Link:
    def __init__(self, replies=None, target=None):
        self.sent, self.replies, self.target = [], dict(replies or {}), target

    def send(self, line):
        self.sent.append(line)
        verb = line.split()[0]
        reply = self.replies.get(verb, {"use": "used", "select": "selected"}.get(verb, "?"))
        if verb == "select" and reply == "selected":
            self.target = int(line.split()[1])
        return reply

    def json(self, line):
        if line == "mem bagfull":
            return [dict(r) for r in BAG]
        return {}


class Bot(BagMixin, FightingMixin, HealthMixin):
    def __init__(self, link, act=True):
        self.a, self.agent_act, self.p = link, act, {"target_keys": {"west": "a"}, "use_key": "enter"}
        self.lines, self.keys, self.cursor_asked = [], [], []
        self.in_fight = False

    def log(self, line, speak=False):
        self.lines.append(line)

    def key(self, k, ms=None):
        self.keys.append(k)

    def ui(self):
        return {"target": self.a.target}

    def bag_cursor(self, name):                 # the arrow-key way, stood in for
        self.cursor_asked.append(name)
        return name


def test_a_bag_item_is_used_by_its_uid_without_arrow_keys():
    b = Bot(Link())
    assert b.use_item("large red potion") is True
    assert b.a.sent == ["use 41"] and b.cursor_asked == [] and b.keys == []


def test_an_exact_name_wins_over_a_longer_one():
    b = Bot(Link())
    b.use_item("Hearthstone")
    assert b.a.sent == ["use 43"]


def test_an_empty_row_is_no_item():
    b = Bot(Link())
    assert b.use_item("Pet Large Red Potion") is False     # count 0
    assert b.a.sent == [] and any("no Pet Large Red Potion" in line for line in b.lines)


def test_an_agent_that_cant_use_items_hands_them_back_to_the_keys():
    b = Bot(Link({"use": "error: the game's connection wasn't found"}))
    assert b.use_item("Large Red Potion") is False          # one wrong answer: again next time
    assert b.use_item("Large Red Potion") is False
    assert b.cursor_asked == [] and b.agent_act is True
    assert b.use_item("Large Red Potion") is True           # the third: the arrow keys did it
    assert b.cursor_asked == ["Large Red Potion"] and b.agent_act is False
    assert any("by keys again" in line for line in b.lines)


def test_bag_potions_wait_for_the_fight_to_end_only_without_the_agent():
    b = Bot(Link())
    b.in_fight = True
    assert b.bag_in_fight() is True
    b.agent_act = False
    assert b.bag_in_fight() is False


def test_the_target_is_selected_by_id_without_target_keys():
    b = Bot(Link())
    assert b.select_target({"id": -9, "x": 1, "y": 1}) is True
    assert b.a.sent == ["select -9"] and b.keys == []


def test_a_monster_gone_from_sight_isnt_selected():
    b = Bot(Link({"select": "error: -9 isn't in sight"}))
    assert b.select_target({"id": -9, "x": 1, "y": 1}) is False
    assert b.keys == [] and b.agent_act is True


def test_an_agent_that_cant_select_hands_it_back_to_the_target_keys(monkeypatch):
    b = Bot(Link({"select": "error: fields moved: game.i.av is missing"}))
    b.me = lambda: {"x": 5, "y": 1}
    b.sleep = lambda s: None
    for _try in range(3):              # three wrong answers in a row before the agent is given up
        assert b.select_target({"id": -9, "x": 1, "y": 1}) is False   # the stand-in game never selects it
    assert b.keys and set(b.keys) == {"a"} and b.agent_act is False


def test_a_monster_the_target_keys_wouldnt_take_isnt_selected():
    # agent 41 runs the keys' own checks (reach, the game's line of sight, unselectable flags)
    b = Bot(Link({"select": "refused: -9 isn't in a clear line"}))
    assert b.select_target({"id": -9, "x": 1, "y": 1}) is False
    assert "clear line" in b.select_refused
    assert b.agent_act is True and b.keys == []      # the agent works: no fall back to the keys

