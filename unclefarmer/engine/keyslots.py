"""Putting the skills and items the bot relies on onto EMPTY keys - never onto a key in use.

The game's own ways (help, "Game Hotkeys"):
  - a skill: select it in the combat skills panel (panel 4 of the backpack), then left Ctrl+Alt +
    the skill key; it goes on the skill page that is up (Caps Lock flips the page).
  - an item: select it in the backpack (panel 0), then left Ctrl + 1 .. =.

A key counts as empty only when the game has nothing on it at all (`mem skills` attack_keys
missing or 0; `mem slots` missing). A slot kept for a potion that ran out is the player's, not
empty. Every placement is checked against the game afterwards.

With no empty key: items are used from the backpack as before (between fights). Skills can only be
cast from a skill key - the game's use key does nothing in the skills panel - so the bot names what
is missing and which keys hold a skill that is also on another key, for the player to decide.
"""

from typing import Any

from .. import gamekeys
from ..i18n import _
from . import combat, pets, repair
from .botparts.base import STOPPING
from ..errors import carry_on



def arrange(bot):
    """At the start of a run, standing somewhere quiet: fill empty keys. Never raises."""
    if not bot.p.get("fill_empty_keys", True) or bot.in_fight:
        return
    try:
        if bot.dialog().get("open"):
            return
        moved = skills(bot)
        moved = items(bot) or moved
        if moved:
            bot.goto_panel(0)
    except STOPPING:
        raise                       # a stop, a death or a lost agent is not a key failure
    except Exception as e:          # placing keys must never stop a run
        carry_on(e, "placing keys")
        if bot.stop_event.is_set():
            raise
        bot.log(_("couldn't put things on empty keys: {err}", err=e))


def refill(bot: Any) -> bool:
    """During a run, between fights: a red or blue potion lost its hotkey (used up, or the game
    put something else there) while the bag still holds some - put them on an empty hotkey, or
    the bot can only drink them between fights. True when it did something. Never raises."""
    if not getattr(bot, "_keys_to_fill", False):
        return False
    if not bot.p.get("fill_empty_keys", True) or bot.in_fight or bot.attackers():
        return False             # (asked again next time round)
    bot._keys_to_fill = False
    try:
        if bot.dialog().get("open"):
            bot._keys_to_fill = True
            return False
        if items(bot):
            bot.goto_panel(0)
            return True
    except STOPPING:
        raise
    except Exception as e:
        carry_on(e, "refilling keys")
        if bot.stop_event.is_set():
            raise
        bot.log(_("couldn't put things on empty keys: {err}", err=e))
    return False


# ------------------------------------------------------------ skills
def wanted_skills(cls):
    """The class's skills the bot uses for itself, most needed first."""
    S = combat.SkillBook
    order = (S.quick_heal, lambda x: S.shield(x) == "free", S.big_heal, S.self_dispel,
             lambda x: S.shield(x) in ("attack", "move"), S.long_buff, S.fight_buff)
    know = combat.knowledge().get(cls, [])
    out = []
    for test in order:
        for x in know:
            if x["name"] not in out and test(x):
                out.append(x["name"])
    return out


def skills(bot):
    """Put the class's heals, shields, dispels and buffs that are on no key onto empty skill keys.
    True when anything was placed."""
    sb = bot.skillbook or combat.SkillBook(bot)
    bot.skillbook = sb
    sk = bot.a.json("mem skills")
    if not isinstance(sk, dict) or "combat" not in sk or not sb.cls:
        return False
    rows = sk["combat"]
    ids = {r["name"]: r["id"] for r in rows}
    keyed = {int(i): sid for i, sid in sk.get("attack_keys", {}).items() if sid}
    on = {sid for sid in keyed.values()}
    # a skill the player set to "never" stays off the keys
    want = [n for n in wanted_skills(sb.cls) if n in ids and ids[n] not in on and sb.use_of(n) != "never"]
    if not want:
        return False
    empty = [i for i in range(1, 21) if i not in keyed]
    if not empty:
        tell_no_room(bot, want, keyed, rows)
        return False
    keys = gamekeys.attack_key_list()
    placed = False
    for name in want:
        if not empty:
            tell_no_room(bot, want[want.index(name):], keyed, rows)
            break
        slot = empty[0]
        page, n = divmod(slot - 1, 10)
        index = next(i for i, r in enumerate(rows) if r["name"] == name)
        if not bot.panel_cursor(4, index, len(rows)):
            bot.log(_("couldn't reach {skill} in the skills panel", skill=name))
            break
        if not sb.flip_to(page):
            break
        bot.key("lctrl+alt+" + keys[n])
        bot.sleep(0.6)
        now = bot.a.json("mem skills")
        got = (now.get("attack_keys") or {}).get(str(slot)) if isinstance(now, dict) else None
        if got != ids[name]:
            bot.log(_("the game didn't put {skill} on key {key} - leaving the keys as they are",
                      skill=name, key=keys[n]), speak=True)
            break
        empty.pop(0)
        keyed[slot] = ids[name]
        placed = True
        bot.log(_("{skill} put on the empty skill key {key} (page {page})", skill=name, key=keys[n], page=page + 1),
                speak=True)
    sb.flip_to(0)
    if placed:
        bot.skillbook = combat.SkillBook(bot)    # read the keys again
    return placed


def tell_no_room(bot, want, keyed, rows):
    """No empty skill key: say what the bot can't use, and which keys hold a skill that is also on
    another key (one of those could make room), once a run."""
    if getattr(bot, "_told_no_skill_room", False):
        return
    bot._told_no_skill_room = True
    names = {r["id"]: r["name"] for r in rows}
    keys = gamekeys.attack_key_list()
    seen, twice = {}, []
    for slot in sorted(keyed):
        sid = keyed[slot]
        if sid in seen:
            page, n = divmod(slot - 1, 10)
            twice.append(_("page {page} key {key} ({skill})", page=page + 1, key=keys[n],
                           skill=names.get(sid, "?")))
        seen[sid] = slot
    line = _("no empty skill key for {skills}, so the bot can't use them", skills=", ".join(want))
    if twice:
        line += _("; these keys hold a skill that is also on another key: {keys}", keys="; ".join(twice))
    bot.log(line, speak=True)


# ------------------------------------------------------------ items
def wanted_items(bot):
    """Items the bot uses that are in the backpack but on no hotkey, most needed first: the
    chosen HP and MP potions, the pet's (with a pet ticked: in a fight only a hotkey can give the
    pet a potion, and pets died with plenty in the bag), the toolbox that fits, the hearthstone."""
    out = []
    kinds = ("hp", "mp") + (("pet_hp", "pet_mp") if pets.chosen(bot) else ())
    for kind in kinds:
        key, name = bot.potion(kind)
        if name and not key:
            out.append(name)
    tools = repair.toolboxes(bot)
    if tools and not tools[0][1]:          # the toolbox to use isn't on a hotkey
        out.append(tools[0][0])
    hs = bot.p.get("hearthstone_item") or "Hearthstone"
    if not bot.p.get("hearthstone_key") and not bot.hotkey_of(hs):
        if any((r.get("name") or "").lower() == hs.lower() and r.get("count") for r in bot.bag_rows()):
            out.append(hs)
    return out


def items(bot):
    """Put those items on empty hotkeys (1 .. =). True when anything was placed."""
    want = wanted_items(bot)
    if not want:
        return False
    slots = bot.a.json("mem slots")
    if not isinstance(slots, dict):
        return False
    keys = gamekeys.slot_key_list()
    empty = [i for i in range(1, 13) if str(i) not in slots]
    placed = False
    for name in want:
        if not empty:
            bot.log(_("no empty hotkey for {items}: used from the backpack between fights",
                      items=", ".join(want[want.index(name):])))
            break
        slot = empty[0]
        row = bot.bag_cursor(name)
        if row is None:
            continue
        bot.key("lctrl+" + keys[slot - 1])
        bot.sleep(0.5)
        now = bot.a.json("mem slots")
        if not isinstance(now, dict) or str(slot) not in now:
            bot.log(_("the game didn't put {item} on hotkey {key} - leaving the hotkeys as they are",
                      item=name, key=keys[slot - 1]), speak=True)
            break
        empty.pop(0)
        placed = True
        bot.log(_("{item} put on the empty hotkey {key}", item=row, key=keys[slot - 1]), speak=True)
    if placed:
        bot._potion_keys = None       # read the hotkeys again
    return placed
