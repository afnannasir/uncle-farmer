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


class KeysMixin(Parts):
    held: set[str]
    agent_keys: bool = False
    agent_slots: bool = False

    @property
    def _link(self) -> GameLink:
        return self.a

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
        and says so once."""
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
