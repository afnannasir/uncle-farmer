"""Pressing, holding and letting go of keys (all through the agent, the game's own key handler).

Part of the Bot class (engine/bot.py), which puts these mixins together; methods call each other
through self whatever part they live in.
"""

from __future__ import annotations



from ...i18n import _
from .base import (Stopped, agent_strike)
from ...link import GameLink
from .parts import Parts


# The attack keys' modifiers (config/input.ini: W_SUSTAIN_ATTACKn = ALT+W_ATTACKn, W_LOCK_ATTACKn =
# LCTRL+W_ATTACKn) as the words of agent 45's `slot`; Shift (W_SELF_ATTACKn) stays a key press.
SLOT_MODS = {"": "", "alt": " sustain", "lctrl": " lock"}
# The game's attack key handler (game.i.a(IZZ)) drops a press that comes within 80 ms (plus up to
# 10 more) of a press on a DIFFERENT attack key, sending only its fast-switch report (OUT op=17),
# and the dropped press becomes the one the next is measured from. Keys switched faster than that
# cast nothing at all: live in a dungeon, 65 presses in 4.7 s and not one cast. Pressing the same
# key again is never dropped, so only a switch waits.
SWITCH_GAP = 0.12
PAGE_TURN = "page"     # last_attack after Caps Lock: every key is on another slot now


class KeysMixin(Parts):
    held: set[str]
    agent_keys: bool = False
    agent_slots: bool = False
    last_attack: tuple[str, float] | None = None   # (attack key as the game counts it, when pressed)

    @property
    def _link(self) -> GameLink:
        return self.a

    def attack_slot(self, k: str) -> str | None:
        """Attack key k as the game's handler tells presses apart ("j"; Alt and Left Ctrl press the
        same slot, Shift is the self-attack path), or None when k is no attack key."""
        mods, _plus, base = k.lower().rpartition("+")
        game: list[str] = getattr(self, "_attack_keys", None) or []     # input.ini's names, once read
        mine: list[str] = self.p.get("attack_keys") or []
        keys = [str(x).lower() for x in mine] + game
        if base not in keys:
            return None
        return ("shift+" if "shift" in mods.split("+") else "") + base

    def switch_wait(self, k: str) -> None:
        """Before pressing attack key k: when the last attack press was on another key (or the page
        turned since), wait out SWITCH_GAP from it, so the game doesn't drop this press."""
        slot = self.attack_slot(k)
        if slot is None:
            return
        last = self.last_attack
        if last is not None and last[0] != slot:
            wait = last[1] + SWITCH_GAP - self.clock.time()
            if wait > 0:
                self.sleep(wait)
        self.last_attack = (slot, self.clock.time())

    def note_page_turn(self) -> None:
        """Caps Lock turned the skill page: the next attack press is on another slot."""
        self.last_attack = (PAGE_TURN, self.clock.time())

    def key(self, k: str, ms: int = 60) -> None:
        self.check()
        if self.press_slot(k):
            return
        self.wait_keys_free()
        r = self._link.send("key %s %d" % (k, ms))
        if not r.startswith("tapped"):
            raise Stopped(_("key {key} didn't work: {why}", key=k, why=r), final=True)

    def slot_command(self, k: str) -> str | None:
        """Agent 45's `slot` line for attack key k ("j", "alt+j", "lctrl+j"), or None when k is
        no attack key: what the game's attack key would do, on the page that is up, done through
        the game's own key method - no keyboard, so nothing the player holds can change it, and
        the client's own fast-switch report (OUT op=17) never fires."""
        if not self.agent_slots:
            return None
        mods, _plus, base = k.lower().rpartition("+")
        if mods not in SLOT_MODS:
            return None
        keys: list[str] | None = getattr(self, "_attack_keys", None)
        if keys is None:
            from ... import gamekeys
            keys = [str(x).lower() for x in gamekeys.attack_key_list()]
            self._attack_keys = keys
        if base not in keys:
            return None
        return "slot %d%s" % (keys.index(base) + 1, SLOT_MODS[mods])

    def press_slot(self, k: str) -> bool:
        """Press attack key k as a slot (True), or leave it to the keys (False). An agent that
        can't (AGENT_PATIENCE wrong answers in a row) goes back to keys for the rest of the run,
        and says so once. Either way a switch from another attack key waits first (switch_wait)."""
        self.switch_wait(k)
        line = self.slot_command(k)
        if line is None:
            return False
        r = self._link.send(line)
        if r.startswith("pressed"):
            agent_strike(self, "slot", True)
            return True
        if agent_strike(self, "slot", False):
            self.agent_slots = False
            self.log(_("skills go by keys again: {why}", why=r))
        return False

    def hold(self, k: str) -> None:
        self.wait_keys_free()
        r = self._link.send("keydown " + k)
        if not r.startswith("down"):
            raise Stopped(_("key {key} didn't work: {why}", key=k, why=r), final=True)
        self.held.add(k)

    def let_go(self, k: str | None = None) -> None:
        """Let go of this bot's own keys only. Another controller in the same game (the
        private-message helper, a second run) keeps walking: an older agent's `keysup` releases
        every key in the game, so there it is one `keyup` per key this bot holds."""
        if k:
            self._link.send("keyup " + k)
            self.held.discard(k)
            return
        if self.agent_keys:
            self._link.send("keysup")
        else:
            for name in sorted(self.held):
                self._link.send("keyup " + name)
        self.held.clear()

    def move_key(self, direction: str) -> str:
        move_keys: dict[str, str] = self.p["move_keys"]
        return move_keys[direction]
