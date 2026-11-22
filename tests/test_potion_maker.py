"""Tools, Potion Maker (engine/potionmaker.py): the plan from the bag, and a run against a pretend
gang Pharmacist that follows the recorded game (2026-11-21): "Make Medicine" makes a batch of 50 for
a fee read on the recipe's page, "not enough materials" makes nothing, "Fuse Medicine" turns 5 of a
size into 1 of the next for free, at most 100 at a time."""

from __future__ import annotations

import math
from typing import Any

import pytest

from conftest import Clock, as_bot
from test_crafting import Bot as CraftBot, add, entry
from unclefarmer.engine import activities, bagrules, chores, crafting, market, potionmaker as pm, script, town
from unclefarmer.engine.bot import Stopped

FEE = 608


class Bot(CraftBot):
    def __init__(self, bag: list[dict[str, Any]] | None = None, profile: dict[str, Any] | None = None) -> None:
        super().__init__(bag=bag, profile=profile)
        self.clock = Clock()

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        assert self.clock is not None
        self.clock.advance(seconds)


def count(b: Bot, name: str) -> int:
    return sum(r["count"] for r in b.bag if r["name"].lower() == name.lower())


class Pharmacist:
    """The gang Pharmacist's menus, from the recording."""

    def __init__(self, b: Bot) -> None:
        self.b = b
        self.page: pm.Recipe | None = None
        self.fusing = ""
        self.said: list[str] = []
        self.made: list[str] = []
        self.fused: list[tuple[str, int]] = []
        self.talks = 0
        self.fee = FEE
        b.on_choose = self.choose

    def show(self, rows: list[dict[str, Any]]) -> None:
        self.b.top = {"row": 0, "col": 0, "rows": [[e] for e in rows]}
        self.b.open = True

    def talk(self) -> None:
        self.talks += 1
        self.show([entry("Make Medicine"), entry("Fuse Medicine")])

    def choose(self, pattern: str) -> dict[str, Any] | None:
        b = self.b
        where = b.find_entry(b.top, pattern) if b.open and b.top else None
        if where is None:
            return None
        e: dict[str, Any] = (b.top or {})["rows"][where[0]][where[1]]
        text = str(e["options"][0])
        if text == "Make Medicine":
            self.show([{"flags": 8, "cmd": [1, r.id],
                        "options": ["synthesize %d %s" % (r.makes, r.name),
                                    "needs " + "".join("%d %s" % (k, m) for m, k in r.needs.items())]}
                       for r in pm.RECIPES])
        elif text == "Fuse Medicine":
            rows: list[dict[str, Any]] = []
            for i, (dst, src) in enumerate(self.fusions()):
                rows.append({"flags": 13, "cmd": [1, i, pm.BY_NAME[dst.lower()].id, 0],
                             "options": ["Please fill in the number of %s to be merged,up to 100 each time" % dst,
                                         "merge of 1 %s requires 5 %s" % (dst, src)]})
            self.show(rows)
        elif text.startswith("synthesize ") and ";" not in text:
            name = text.split(" ", 2)[2]
            self.page = pm.BY_NAME[name.lower()]
            self.show([entry("synthesize %d %s; cost %d arnebia" % (self.page.makes, name, self.fee)),
                       entry("start synthesis")])
        elif text == "start synthesis" and self.page is not None:
            self.start(self.page)
            b.close_dialogs()
        elif text.startswith("Please fill in"):
            merge = str(e["options"][1])
            self.fusing = merge.split("merge of 1 ", 1)[-1].split(" requires")[0]
        return e

    @staticmethod
    def fusions() -> list[tuple[str, str]]:
        return [(c[i], c[i - 1]) for c in pm.CHAINS for i in (1, 2)]

    def start(self, r: pm.Recipe) -> None:
        b = self.b
        if any(count(b, m) < k for m, k in r.needs.items()):
            self.said.append("not enough materials")
            return
        for m, k in r.needs.items():
            add(b, m, -k)
        add(b, "Arnebia", -self.fee)
        add(b, r.name, r.makes)
        self.made.append(r.name)

    def quantity(self, n: int) -> bool:
        b = self.b
        assert n <= pm.FUSE_MOST
        src = pm.FUSED_FROM[self.fusing.lower()]
        b.close_dialogs()
        if count(b, src) < n * 5:
            return True
        add(b, src, -n * 5)
        add(b, self.fusing, n)
        self.fused.append((self.fusing, n))
        return True


@pytest.fixture
def game(monkeypatch: pytest.MonkeyPatch) -> Any:
    def make(bag: dict[str, int], arnebia: int = 10 ** 7, storage: dict[str, int] | None = None,
             lots: list[market.Lot] | None = None, weights: dict[str, float] | None = None,
             maxload: float = 0) -> tuple[Bot, Pharmacist, dict[str, Any]]:
        b = Bot(bag=[{"name": "Arnebia", "count": arnebia}] + [{"name": n, "count": k} for n, k in bag.items()],
                profile={"gem_keep_arnebia": 0})
        ph = Pharmacist(b)
        seen: dict[str, Any] = {"storage": dict(storage or {}), "bought": [], "talks": 0}
        def enter_gang(bot: Any, gang: str | None = None, role: str = "") -> bool:
            return True
        monkeypatch.setattr(crafting, "enter_gang", enter_gang)

        def talk(name: str, at: tuple[float, float] | None = None, stand: tuple[float, float] | None = None) -> bool:
            ph.talk()
            return True
        b.talk = talk                                   # type: ignore[method-assign]

        def open_section(bot: Bot, pattern: str) -> bool:
            return bot.choose(pattern) is not None
        monkeypatch.setattr(town, "open_section", open_section)

        def pick(bot: Bot, pattern: str, quantity: int | None = None) -> bool:
            return bot.choose(pattern) is not None and quantity is not None and ph.quantity(quantity)
        monkeypatch.setattr(town, "pick", pick)
        def said_mark(bot: Any) -> int:
            return len(ph.said)

        def said_since(bot: Any, since: int) -> list[str]:
            return ph.said[since:]
        monkeypatch.setattr(chores, "said_mark", said_mark)
        monkeypatch.setattr(chores, "said_since", said_since)

        def take(bot: Any, item: str, n: int, check_only: bool = False) -> int:
            k = min(n, seen["storage"].get(item, 0))
            seen["storage"][item] = seen["storage"].get(item, 0) - k
            add(b, item, k)
            return k
        monkeypatch.setattr(crafting, "take_from_storage", take)

        _pretend_market(monkeypatch, b, seen, lots)
        _pretend_storehouse(monkeypatch, b, seen, weights, maxload)
        return b, ph, seen
    return make


def _pretend_market(monkeypatch: pytest.MonkeyPatch, b: Bot, seen: dict[str, Any], lots: list[market.Lot] | None) -> None:
    """The player market: one page, cheapest first; a lot sells out as it is bought."""
    def read_pages(bot: Bot, cat: int, order: int, on_page: Any, first: int = 1, most: int = 0) -> int:
        on_page(market.Page(cat=cat, order=order, page=1, last=1, unread=0, top={},
                            lots=sorted((x for x in lots or [] if x.qty > 0), key=lambda x: x.price)))
        return 1
    monkeypatch.setattr(market, "read_pages", read_pages)

    def buy(bot: Bot, orders: list[market.Order], cap: int = 0, check: bool = False) -> list[tuple[market.Order, str, str]]:
        out: list[tuple[market.Order, str, str]] = []
        spent = 0
        for o in orders:
            cost = o.qty * o.lot.price
            if cap and spent + cost > cap:
                out.append((o, market.CAP, "cap"))
                continue
            spent += cost
            o.lot.qty -= o.qty                      # sold: the market has that much less
            add(b, o.lot.name, o.qty)
            seen["bought"].append((o.lot.name, o.qty, o.lot.price))
            out.append((o, market.BOUGHT, "bought"))
        return out
    monkeypatch.setattr(market, "buy", buy)


def _pretend_storehouse(monkeypatch: pytest.MonkeyPatch, b: Bot, seen: dict[str, Any], weights: dict[str, float] | None,
                        maxload: float) -> None:
    """Item weights, the sheet's load (maxload 0: no sheet), deposits and the storehouse's list."""
    weight = {k.lower(): v for k, v in (weights or {}).items()}
    monkeypatch.setattr(bagrules, "weights", lambda: dict(weight))
    def learn_weight(*_args: Any) -> None:
        return None
    monkeypatch.setattr(bagrules, "learn_weight", learn_weight)

    def sheet(force: bool = False) -> dict[str, Any] | None:
        if not maxload:
            return None
        return {"maxload": maxload, "load": sum(r["count"] * weight.get(r["name"].lower(), 0) for r in b.bag)}
    b.refresh_sheet = sheet                         # type: ignore[method-assign]

    def deposit(bot: Any, item: str, n: int | None = None) -> int:
        k = min(count(b, item), n if n is not None else 10 ** 9)
        add(b, item, -k)
        seen["storage"][item] = seen["storage"].get(item, 0) + k
        seen.setdefault("deposits", []).append((item, k))
        return k
    monkeypatch.setattr(town, "deposit", deposit)

    def stored_counts(bot: Any, names: Any) -> dict[str, int]:
        return {n: seen["storage"].get(n, 0) for n in names}
    monkeypatch.setattr(pm, "stored_counts", stored_counts)


def lot(i: int, name: str, qty: int, price: int) -> market.Lot:
    return market.Lot(id=i, cat=100, name=name, qty=qty, price=price, order=market.PRICE, page=1)


def job(**over: Any) -> dict[str, Any]:
    return {**pm.config({}), "supply": "bag", **over}


# ---------------------------------------------------------------------------------------- the plan
def test_needs_read_as_the_game_runs_them_together() -> None:
    assert pm.parse_needs("needs 2 Lesser Blood1 medium Blood1 Higher blood") == {
        "Lesser Blood": 2, "medium Blood": 1, "Higher blood": 1}
    assert pm.parse_needs("needs 2 Lesser Blood1 medium Blood1 Higher blood2 Excellent Blood6 Premium Blood") == {
        "Lesser Blood": 2, "medium Blood": 1, "Higher blood": 1, "Excellent Blood": 2, "Premium Blood": 6}


def test_every_recorded_recipe_and_fusion_is_known() -> None:
    assert len(pm.RECIPES) == 28 and len(pm.FUSED_FROM) == 8
    assert pm.FUSED_FROM["large red potion"] == "Medium Red Potion"
    assert pm.FUSED_FROM["pet medium blue potion"] == "Pet Small Blue Potion"
    assert all(m in pm.MATERIALS for r in pm.RECIPES for m in r.needs)


def test_large_potions_from_smalls_fused_up() -> None:
    p = pm.plan({}, job(targets={"Large Red Potion": 100}))
    assert p.batches == {"Small Red Potion": 50}             # 100 Large = 500 Medium = 2,500 Small
    assert p.fuses == [("Medium Red Potion", 500), ("Large Red Potion", 100)]
    assert p.materials == {"Lesser Blood": 100} and p.missing == {"Lesser Blood": 100}
    assert p.after == {"Large Red Potion": 100}


def test_what_the_bag_has_is_used_first() -> None:
    p = pm.plan({"medium red potion": 30, "large red potion": 4, "small red potion": 12, "lesser blood": 60},
                job(targets={"Large Red Potion": 10}))
    # 6 Large more = 30 Medium: all there; nothing to make
    assert p.batches == {} and p.fuses == [("Large Red Potion", 6)]
    p = pm.plan({"medium red potion": 30}, job(targets={"Large Red Potion": 100}))
    short_small = (100 * 5 - 30) * 5
    assert p.batches == {"Small Red Potion": math.ceil(short_small / 50)}
    assert p.fuses == [("Medium Red Potion", 470), ("Large Red Potion", 100)]


def test_each_from_its_own_recipe() -> None:
    p = pm.plan({}, job(targets={"Large Red Potion": 100, "Plasma": 40}, way="make"))
    assert p.batches == {"Large Red Potion": 2, "Plasma": 1}
    assert p.materials == {"Lesser Blood": 5, "medium Blood": 3, "Higher blood": 4}
    assert p.fuses == []


def test_a_size_with_its_own_target_is_kept_while_the_one_above_is_fused() -> None:
    p = pm.plan({}, job(targets={"Medium Blue Potion": 20, "Large Blue Potion": 2}))
    # 2 Large = 10 Medium, plus the 20 Medium kept = 30 Medium = 150 Small = 3 batches
    assert p.batches == {"Small Blue Potion": 3}
    assert p.after["Medium Blue Potion"] == 20 and p.after["Large Blue Potion"] == 2


def test_spare_potions_fused_up_with_a_few_kept() -> None:
    p = pm.plan({"small red potion": 37, "pet small blue potion": 60}, job(fuse_up="medium", keep_small=10))
    assert p.batches == {} and p.fuses == [("Medium Red Potion", 5), ("Pet Medium Blue Potion", 10)]
    assert p.after == {"Small Red Potion": 12, "Medium Red Potion": 5, "Pet Small Blue Potion": 10,
                       "Pet Medium Blue Potion": 10}
    p = pm.plan({"small red potion": 130}, job(fuse_up="large"))
    assert p.fuses == [("Medium Red Potion", 26), ("Large Red Potion", 5)]
    assert p.after == {"Medium Red Potion": 1, "Large Red Potion": 5}


def test_arnebia_pays_for_smalls_first() -> None:
    cap = pm.affordable({"Large Blue Potion": 3, "Small Red Potion": 4}, {"Small Red Potion": 600,
                        "Large Blue Potion": 1000}, 600 * 4 + 1500)
    assert cap == {"Small Red Potion": 4, "Large Blue Potion": 1}
    assert pm.affordable({"Plasma": 2}, {}, 0) == {"Plasma": 2}       # fee not known yet: left as it is


def test_market_lots_cheapest_first_exact_name_and_price() -> None:
    lots = [lot(1, "Lesser Blood", 30, 7), lot(2, "Lesser Blood", 50, 5), lot(3, "Lesser Bloodstone", 99, 1),
            lot(4, "Lesser Blood", 80, 9)]
    orders = pm.lots_to_buy(lots, "Lesser Blood", 60, 7)
    assert [(o.lot.id, o.qty) for o in orders] == [(2, 50), (1, 10)]
    orders = pm.lots_to_buy(lots, "Lesser Blood", 200, 0)               # no most price: cheapest first, any
    assert [(o.lot.id, o.qty) for o in orders] == [(2, 50), (1, 30), (4, 80)]


# ---------------------------------------------------------------------------------------- the run
def test_makes_smalls_then_fuses_them_up(game: Any) -> None:
    b, ph, _seen = game({"Lesser Blood": 10})
    pm.run(b, job(targets={"Medium Red Potion": 20}))
    assert ph.made == ["Small Red Potion", "Small Red Potion"]            # 100 Smalls for 20 Mediums
    assert ph.fused == [("Medium Red Potion", 20)]
    assert count(b, "Medium Red Potion") == 20 and count(b, "Small Red Potion") == 0
    assert count(b, "Lesser Blood") == 6 and count(b, "Arnebia") == 10 ** 7 - 2 * FEE
    assert b.p["potion_fees"]["gang"]["Small Red Potion"] == FEE


def test_fusing_goes_a_hundred_at_a_time(game: Any) -> None:
    b, ph, _seen = game({"Small Blue Potion": 1200})
    pm.run(b, job(targets={"Medium Blue Potion": 240}))
    assert ph.made == [] and ph.fused == [("Medium Blue Potion", 100), ("Medium Blue Potion", 100),
                                         ("Medium Blue Potion", 40)]


def test_missing_materials_from_storage_then_the_market(game: Any) -> None:
    lots = [lot(1, "Lesser Blood", 3, 6), lot(2, "Lesser Blood", 5, 5), lot(3, "Lesser Blood", 40, 9)]
    b, ph, seen = game({}, storage={"Lesser Blood": 2}, lots=lots)
    pm.run(b, job(targets={"Small Red Potion": 300}, supply="both", prices={"Lesser Blood": 7}))
    # 6 batches need 12: 2 from storage, then 5 at 5 and 3 at 6; the lot at 9 is over the most price
    assert seen["bought"] == [("Lesser Blood", 5, 5), ("Lesser Blood", 3, 6)]
    assert ph.made.count("Small Red Potion") == 5                         # 10 Lesser Blood: 5 batches
    assert any("Lesser Blood still missing" in line for line in b.lines)


def test_gold_limit_stops_the_buying(game: Any) -> None:
    lots = [lot(1, "Lesser Pelt", 100, 10)]
    b, _ph, seen = game({}, lots=lots)
    pm.run(b, job(targets={"Small Blue Potion": 500}, supply="buy", gold_cap=50))
    assert seen["bought"] == []                                           # 20 for 200 is over the 50 limit


def test_not_enough_materials_moves_on(game: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, ph, _seen = game({"Lesser Blood": 4, "Lesser Pelt": 2})
    def every_batch(batches: dict[str, int], book: Any, have: Any) -> dict[str, int]:
        return dict(batches)                    # pretend the bag lied
    monkeypatch.setattr(pm, "_shorten", every_batch)
    pm.run(b, job(targets={"Small Red Potion": 150, "Small Blue Potion": 50}))
    assert sorted(ph.made) == ["Small Blue Potion", "Small Red Potion", "Small Red Potion"]
    assert any("aren't enough materials for Small Red Potion" in line for line in b.lines)


def test_never_below_the_arnebia_reserve(game: Any) -> None:
    b, ph, _seen = game({"Lesser Blood": 20}, arnebia=FEE * 2 + 100)
    b.p["potion_fees"] = {"gang": {"Small Red Potion": FEE}}
    pm.run(b, job(targets={"Small Red Potion": 500}))
    assert ph.made == ["Small Red Potion"] * 2
    assert any("pays for 2 of the 10 batches" in line for line in b.lines)


def test_check_only_reads_the_fee_and_makes_nothing(game: Any) -> None:
    b, ph, _seen = game({"Lesser Blood": 10})
    pm.run(b, job(targets={"Small Red Potion": 100}), check_only=True)
    assert ph.made == [] and count(b, "Arnebia") == 10 ** 7
    assert any("make 2 batches of Small Red Potion (608 arnebia each)" in line for line in b.lines)
    assert any("nothing was made (check only)" in line for line in b.lines)


def test_live_menu_wins(game: Any) -> None:
    b, ph, _seen = game({})
    ph.talk()
    b.choose(pm.MAKE)
    book = pm.live_recipes(b)
    assert book["pet globulin"].needs == pm.BY_NAME["pet globulin"].needs
    assert book["small red potion"].id == 23385 and book["plasma"].makes == 45


def test_set_it_up_first() -> None:
    with pytest.raises(Stopped):
        pm.run(as_bot(Bot()), pm.config({}))


def test_a_job_and_a_script_command() -> None:
    assert activities.get("potion_maker").checks
    main, _subs, _stop = script.parse("potion_maker check\npotion_maker\n")
    assert [(n.kind, n.opts.get("check")) for n in main] == [("potion_maker", True), ("potion_maker", False)]


# ---------------------------------------------------------------------------------------- a bagful at a time
def test_a_bagful_at_a_time_into_the_storehouse(game: Any) -> None:
    # 10 load free, a Lesser Blood weighs 1: 5 batches a bagful (10 Lesser Blood)
    b, ph, seen = game({}, storage={"Lesser Blood": 100}, weights={"Lesser Blood": 1.0}, maxload=10)
    pm.run(b, job(targets={"Small Red Potion": 300}, supply="storage", store=True))
    assert ph.made == ["Small Red Potion"] * 6
    assert seen["deposits"] == [("Small Red Potion", 250), ("Small Red Potion", 50)]
    assert seen["storage"]["Small Red Potion"] == 300 and count(b, "Small Red Potion") == 0
    assert any("round 2: 1 batches made" in line for line in b.lines)


def test_the_storehouse_counts_and_the_bags_own_potions_stay(game: Any) -> None:
    b, ph, seen = game({"Lesser Blood": 10, "Small Red Potion": 30}, storage={"Small Red Potion": 200})
    pm.run(b, job(targets={"Small Red Potion": 280}, store=True))
    assert ph.made == ["Small Red Potion"]                 # 30 in the bag + 200 stored + 50 made = 280
    assert seen["deposits"] == [("Small Red Potion", 50)] and count(b, "Small Red Potion") == 30


def test_smalls_fused_up_go_in_and_the_rest_waits_in_the_bag(game: Any) -> None:
    b, ph, seen = game({"Lesser Blood": 4}, weights={"Lesser Blood": 1.0}, maxload=100)
    pm.run(b, job(targets={"Medium Red Potion": 15}, store=True))
    assert ph.fused == [("Medium Red Potion", 15)]
    assert seen["deposits"] == [("Medium Red Potion", 15)]
    assert count(b, "Small Red Potion") == 25                # 100 made, 75 fused: the rest stays for next time


def test_making_and_fusing_go_on_in_a_bag_over_its_full_mark(game: Any) -> None:
    # the load is over the bag's limit: no room for more materials, but what is in the bag is still used
    b, ph, _seen = game({"Lesser Blood": 4, "Small Blue Potion": 50}, weights={"Lesser Blood": 30.0}, maxload=100)
    pm.run(b, job(targets={"Small Red Potion": 100, "Medium Blue Potion": 10}))
    assert ph.made == ["Small Red Potion"] * 2 and ph.fused == [("Medium Blue Potion", 10)]


def test_a_full_bag_with_nothing_to_use_says_so(game: Any) -> None:
    b, ph, _seen = game({"Small Red Potion": 1}, storage={"Lesser Blood": 50}, weights={"Small Red Potion": 200.0,
                        "Lesser Blood": 1.0}, maxload=100)
    pm.run(b, job(targets={"Small Red Potion": 500}, supply="storage"))
    assert ph.made == []
    assert any("no room for more Bloods and Pelts" in line for line in b.lines)


def test_the_gold_limit_counts_every_round(game: Any) -> None:
    lots = [lot(1, "Lesser Pelt", 1000, 10)]
    b, ph, seen = game({}, lots=lots, weights={"Lesser Pelt": 1.0}, maxload=4)
    pm.run(b, job(targets={"Small Blue Potion": 500}, supply="buy", gold_cap=100, store=True))
    # 4 Lesser Pelt a bagful for 40 gold: two bagfuls fit under 100, the third doesn't
    assert [q for _n, q, _p in seen["bought"]] == [4, 4]
    assert ph.made == ["Small Blue Potion"] * 4
