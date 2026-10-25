"""The bag manager's decisions: what shard prices, buyers and sellers the game knows, the item
weights the bot learns on its own, and how a rule turns into keep / store / sell / throw on a bag
trip or a restock."""

import json

from unclefarmer.engine import bagrules


# ---------------------------------------------------------------- shard prices
def test_shard_price_reads_the_market_catalog():
    assert bagrules.shard_price("Level 1 Sapphire Shard") == 20


def test_shard_price_is_case_insensitive_and_none_for_other_items():
    assert bagrules.shard_price("level 1 sapphire shard") == 20
    assert bagrules.shard_price("Long-age Turtle") is None


def test_shard_price_none_when_the_catalog_is_empty(monkeypatch):
    monkeypatch.setattr("unclefarmer.engine.town.shard_catalog", lambda: ({}, {}))
    assert bagrules.shard_price("Level 1 Sapphire Shard") is None


# ---------------------------------------------------------------- buyers and sellers
def test_buyer_picks_the_best_price(monkeypatch):
    monkeypatch.setattr(bagrules.knowledge, "traders", lambda name, they: [
        ("m1", "Cheap", 1, 2, 100, "arnebia"),
        ("m2", "Rich", 3, 4, 900, "arnebia"),
    ])
    assert bagrules.buyer("Thing") == ("Rich", 900, "arnebia")


def test_buyer_and_seller_on_real_data():
    # a fish buyer in the game's own knowledge; nothing sells an unknown name
    assert bagrules.buyer("Long-age Turtle") == ("Aquaculture Buyer Luffy", 850, "arnebia")
    assert bagrules.buyer("No Such Item") is None
    assert bagrules.seller("No Such Item") is None


def test_seller_returns_the_cheapest_shop(monkeypatch):
    monkeypatch.setattr(bagrules.knowledge, "traders", lambda name, they: [
        ("m1", "Cheap", 1, 2, 100, "arnebia"),
        ("m2", "Pricey", 3, 4, 900, "arnebia"),
    ])
    assert bagrules.seller("Thing") == ("Cheap", 100, "arnebia")


def test_can_sell_covers_shards_buyers_and_roles(monkeypatch):
    monkeypatch.setattr(bagrules, "shard_price", lambda name: None)
    monkeypatch.setattr(bagrules, "buyer", lambda name: None)
    monkeypatch.setattr(bagrules.knowledge, "buyer_role", lambda name: None)
    assert bagrules.can_sell("Thing") is False

    monkeypatch.setattr(bagrules, "shard_price", lambda name: 5)
    assert bagrules.can_sell("Thing") is True

    monkeypatch.setattr(bagrules, "shard_price", lambda name: None)
    monkeypatch.setattr(bagrules, "buyer", lambda name: ("npc", 1, "arnebia"))
    assert bagrules.can_sell("Thing") is True

    monkeypatch.setattr(bagrules, "buyer", lambda name: None)
    monkeypatch.setattr(bagrules.knowledge, "buyer_role", lambda name: "herb_buyer")
    assert bagrules.can_sell("Thing") is True


def test_can_sell_real_data():
    assert bagrules.can_sell("Long-age Turtle") is True
    assert bagrules.can_sell("Level 1 Sapphire Shard") is True


# ---------------------------------------------------------------- learned weights
def test_weights_empty_when_the_file_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(bagrules, "WEIGHTS", str(tmp_path / "missing.json"))
    assert bagrules.weights() == {}


def test_weights_empty_when_the_file_is_broken(monkeypatch, tmp_path):
    path = tmp_path / "w.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(bagrules, "WEIGHTS", str(path))
    assert bagrules.weights() == {}


def test_learn_weight_writes_and_reads_back(monkeypatch, tmp_path):
    monkeypatch.setattr(bagrules, "WEIGHTS", str(tmp_path / "w.json"))
    assert bagrules.learn_weight("Wolf Pelt", 500, 480, 4) == 5.0
    assert bagrules.weights() == {"wolf pelt": 5.0}
    # lower-cased key: a differently cased lookup still finds it
    assert bagrules.weights()["wolf pelt"] == 5.0
    raw = json.loads((tmp_path / "w.json").read_text(encoding="utf-8"))
    assert raw == {"wolf pelt": 5.0}


def test_learn_weight_updates_an_existing_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(bagrules, "WEIGHTS", str(tmp_path / "w.json"))
    bagrules.learn_weight("Wolf Pelt", 500, 480, 4)
    assert bagrules.learn_weight("Wolf Pelt", 900, 800, 10) == 10.0
    assert bagrules.weights() == {"wolf pelt": 10.0}


def test_learn_weight_rejects_bad_measurements(monkeypatch, tmp_path):
    monkeypatch.setattr(bagrules, "WEIGHTS", str(tmp_path / "w.json"))
    assert bagrules.learn_weight("x", 100, 90, 0) is None          # nothing left
    assert bagrules.learn_weight("x", None, 90, 1) is None         # no before reading
    assert bagrules.learn_weight("x", 100, None, 1) is None        # no after reading
    assert bagrules.learn_weight("x", 100, 120, 1) is None         # load went up
    assert not (tmp_path / "w.json").exists()


def test_learn_weight_survives_an_unwritable_path(monkeypatch):
    monkeypatch.setattr(bagrules, "WEIGHTS", "Z:\\nope\\w.json")
    assert bagrules.learn_weight("Wolf Pelt", 500, 480, 4) == 5.0   # still returns the weight


# ---------------------------------------------------------------- about
def test_about_reports_what_is_known(monkeypatch, tmp_path):
    monkeypatch.setattr(bagrules, "WEIGHTS", str(tmp_path / "w.json"))
    bagrules.learn_weight("Long-age Turtle", 500, 480, 4)
    line = bagrules.about("Long-age Turtle", count=4, hotkey=True)
    assert "Aquaculture Buyer Luffy buys it for 850 arnebia" in line
    assert "weighs 5 each, 20 for all 4" in line
    assert "on a hotkey" in line


def test_about_shard_price_and_role(monkeypatch, tmp_path):
    monkeypatch.setattr(bagrules, "WEIGHTS", str(tmp_path / "w.json"))
    # observed: a shard gets both the market price and its buyer role
    assert bagrules.about("Level 1 Sapphire Shard") == (
        "Shard Market pays about 20 arnebia each; a shard market buys it")


def test_about_seller_and_role(monkeypatch, tmp_path):
    monkeypatch.setattr(bagrules, "WEIGHTS", str(tmp_path / "w.json"))
    monkeypatch.setattr(bagrules, "shard_price", lambda n: None)
    monkeypatch.setattr(bagrules, "buyer", lambda n: None)
    monkeypatch.setattr(bagrules, "seller", lambda n: ("Shop", 12, "arnebia"))
    monkeypatch.setattr(bagrules.knowledge, "buyer_role", lambda n: "herb_buyer")
    assert bagrules.about("Odd Herb") == "a herb buyer buys it; Shop sells it for 12 arnebia"


def test_about_nothing_known_yet(monkeypatch, tmp_path):
    monkeypatch.setattr(bagrules, "WEIGHTS", str(tmp_path / "w.json"))
    monkeypatch.setattr(bagrules, "shard_price", lambda n: None)
    monkeypatch.setattr(bagrules, "buyer", lambda n: None)
    monkeypatch.setattr(bagrules, "seller", lambda n: None)
    monkeypatch.setattr(bagrules.knowledge, "buyer_role", lambda n: None)
    assert bagrules.about("Mystery") == "nothing known yet"


# ---------------------------------------------------------------- suggestions and rules
def test_suggest_keeps_supplies_hotkeys_and_unknowns():
    # observed: every suggestion today is "keep, 0" (the smart sell/throw lists were taken out)
    assert bagrules.suggest("Large Red Potion") == ("keep", 0)     # supply
    assert bagrules.suggest("Hearthstone") == ("keep", 0)          # supply
    assert bagrules.suggest("Mystery Rock") == ("keep", 0)         # unknown
    assert bagrules.suggest("Mystery Rock", hotkey=True) == ("keep", 0)


def test_rule_uses_the_players_choice_first():
    p = {"bag_rules": {"wolf pelt": {"action": "sell", "keep": "2"}}}
    assert bagrules.rule(p, "Wolf Pelt") == {"action": "sell", "keep": 2, "chosen": True}
    # case-insensitive lookup of the rule
    assert bagrules.rule(p, "wolf pelt")["chosen"] is True


def test_rule_falls_back_to_the_suggestion():
    assert bagrules.rule({}, "Mystery") == {"action": "keep", "keep": 0, "chosen": False}
    # observed: an empty rule dict is falsy, so it is treated as "no rule" (chosen False)
    assert bagrules.rule({"bag_rules": {"x": {}}}, "X") == {"action": "keep", "keep": 0, "chosen": False}
    # a rule with at least one field is the player's choice
    assert bagrules.rule({"bag_rules": {"x": {"action": "throw"}}}, "X") == {
        "action": "throw", "keep": 0, "chosen": True}


# ---------------------------------------------------------------- plan
def test_plan_skips_kept_items():
    p = {"bag_rules": {}, "only_this_run": True}
    assert bagrules.plan(p, {"Mystery": 5}) == []


def test_plan_plans_store_sell_throw_with_keep():
    p = {"bag_rules": {
        "wolf pelt": {"action": "sell", "keep": 2},
        "old sword": {"action": "store", "keep": 1},
        "junk": {"action": "throw", "keep": 0},
    }, "only_this_run": False}
    assert bagrules.plan(p, {"Wolf Pelt": 5, "Old Sword": 3, "Junk": 4}) == [
        ("Junk", "throw", 4), ("Old Sword", "store", 2), ("Wolf Pelt", "sell", 3)]


def test_plan_respects_the_keep_count():
    p = {"bag_rules": {"wolf pelt": {"action": "sell", "keep": 5}}, "only_this_run": False}
    assert bagrules.plan(p, {"Wolf Pelt": 5}) == []          # keep count wipes it out
    assert bagrules.plan(p, {"Wolf Pelt": 8}) == [("Wolf Pelt", "sell", 3)]


def test_plan_only_this_run_limits_to_what_was_picked_up(monkeypatch):
    # an item with no player rule whose suggestion is to sell: limited to what this run picked up
    monkeypatch.setattr(bagrules, "suggest", lambda name, hotkey=False: ("sell", 0))
    p = {"bag_rules": {}, "only_this_run": True}
    assert bagrules.plan(p, {"Wolf Pelt": 10}, gained={"wolf pelt": 3}) == [("Wolf Pelt", "sell", 3)]


def test_plan_a_chosen_rule_ignores_only_this_run():
    p = {"bag_rules": {"wolf pelt": {"action": "sell", "keep": 0}}, "only_this_run": True}
    assert bagrules.plan(p, {"Wolf Pelt": 10}, gained={"wolf pelt": 1}) == [("Wolf Pelt", "sell", 10)]


def test_plan_gained_absent_counts_as_nothing_gained(monkeypatch):
    monkeypatch.setattr(bagrules, "suggest", lambda name, hotkey=False: ("sell", 0))
    p = {"bag_rules": {}, "only_this_run": True}
    assert bagrules.plan(p, {"Wolf Pelt": 10}, gained={}) == []
    # with only_this_run off, the whole stack is touched
    p["only_this_run"] = False
    assert bagrules.plan(p, {"Wolf Pelt": 10}, gained={}) == [("Wolf Pelt", "sell", 10)]


def test_plan_hotkey_items_are_kept():
    p = {"bag_rules": {"large red potion": {"action": "sell"}}, "only_this_run": False}
    # the player's own rule still wins over the hotkey protection
    assert bagrules.plan(p, {"Large Red Potion": 3}, hotkeys=["Large Red Potion"]) == [
        ("Large Red Potion", "sell", 3)]
    # without a rule, a hotkey item is a supply and is kept
    assert bagrules.plan({"bag_rules": {}, "only_this_run": False},
                         {"Large Red Potion": 3}, hotkeys=["Large Red Potion"]) == []


# ---------------------------------------------------------------- supply rules
def test_supply_rule_prefers_an_explicit_source():
    assert bagrules.supply_rule({"source": "buy", "storage_first": True, "buy": True}) == "buy"


def test_supply_rule_reads_the_old_flags():
    assert bagrules.supply_rule({"storage_first": True, "buy": True}) == "both"
    assert bagrules.supply_rule({"storage_first": True, "buy": False}) == "storage"
    assert bagrules.supply_rule({"storage_first": False, "buy": True}) == "buy"
    assert bagrules.supply_rule({"storage_first": False, "buy": False}) == "tell"
    assert bagrules.supply_rule({}) == "both"       # defaults are storage_first=True, buy=True


def test_suggest_supplies_offers_only_matching_hotkey_items():
    scan = {"hotkeys": [
        {"item": "Large Red Potion"},
        {"item": "Large Blue Potion"},
        {"item": "Rice Roll"},
        {"item": "Small Toolbox"},
        {"item": "Wolf Pelt"},          # not a supply
        {"item": ""},                    # no name
        {},                              # no item key
    ]}
    assert bagrules.suggest_supplies(scan) == [
        {"item": "Large Red Potion", "min": 30, "stock": 200, "source": "tell", "on": True},
        {"item": "Large Blue Potion", "min": 30, "stock": 200, "source": "tell", "on": True},
        {"item": "Rice Roll", "min": 30, "stock": 200, "source": "tell", "on": True},
        {"item": "Small Toolbox", "min": 30, "stock": 200, "source": "tell", "on": True},
    ]


def test_suggest_supplies_empty_scan():
    assert bagrules.suggest_supplies(None) == []
    assert bagrules.suggest_supplies({}) == []
    assert bagrules.suggest_supplies({"hotkeys": []}) == []
