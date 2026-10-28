"""The game's own key bindings, from <game>\\config\\input.ini.

Each line is ACTION=KEYS, where KEYS may name another action (SHIFT+W_SELF_HP). We turn them into the
key names the agent understands, so a profile can be filled in from the player's real setup instead
of guessed defaults.
"""

from __future__ import annotations

import os

from . import settings

ATTACK_DEFAULTS = ["y", "u", "h", "j", "n", "m", "i", ",", ".", "/"]               # input.ini as shipped
SLOT_DEFAULTS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "="]
_read_cache: dict[str, tuple[float, dict[str, str]]] = {}      # input.ini path -> (modified time, bindings): read again only when it changes

_NAMES: dict[str, str] = {
    "SEMICOLON": ";", "DOT": ".", "COMMA": ",", "SLASH": "/", "QUOTE": "'", "MINUS": "-", "EQUAL": "=",
    "LBRACKET": "[", "RBRACKET": "]", "BACKSLASH": "\\", "SPACE": "space", "ENTER": "enter", "ESC": "esc",
    "TAB": "tab", "DEL": "delete", "BACKSPACE": "backspace", "UP": "up", "DOWN": "down", "LEFT": "left",
    "RIGHT": "right", "RCTRL": "rctrl", "LCTRL": "lctrl", "ALT": "alt", "SHIFT": "shift",
    "BACKWOTE": "`", "BACKQUOTE": "`", "CAPSLOCK": "capslock",   # input.ini spells it BACKWOTE
}


def _key(token: str) -> str | None:
    t = token.strip().upper()
    if t in _NAMES:
        return _NAMES[t]
    if len(t) == 1 and (t.isalpha() or t.isdigit()):
        return t.lower()
    # The game's own key table (game.aJ, javap 2026-10-04): NUM0-NUM9 are the NUMBER-ROW keys
    # (VK 48-57); the keypad is NUMPAD0-NUMPAD9 (VK 96-105). Pressing the keypad for "NUM1" does
    # nothing - that is why the HP potion never fired in the Orcus Tribe test.
    if t.startswith("NUMPAD") and t[6:].isdigit():
        return "num" + t[6:]
    if t.startswith("NUM") and t[3:].isdigit():
        return t[3:]
    if t.startswith("F") and t[1:].isdigit():
        return t.lower()
    return None


def read(game_folder: str | None) -> dict[str, str]:
    """{action: "key" or "mod+key"} for every binding we can express, or {} if there's no file."""
    path = os.path.join(game_folder or "", "config", "input.ini")
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return {}
    hit = _read_cache.get(path)
    if hit and hit[0] == stamp:
        return dict(hit[1])
    binds = _read(path)
    _read_cache[path] = (stamp, binds)
    return dict(binds)


def _read(path: str) -> dict[str, str]:
    raw: dict[str, str] = {}
    with open(path, "rb") as f:
        for line in f.read().decode("latin-1").splitlines():
            line = line.strip()
            if line.startswith("W_") and "=" in line:
                k, v = line.split("=", 1)
                raw[k.strip()] = v.strip()

    def resolve(action: str, depth: int = 0) -> str | None:
        if action not in raw or depth > 5:
            return None
        parts: list[str] = []
        for tok in raw[action].split("+"):
            if tok.startswith("W_"):
                sub = resolve(tok, depth + 1)
                if sub is None:
                    return None
                parts.append(sub)
            else:
                k = _key(tok)
                if k is None:
                    return None
                parts.append(k)
        return "+".join(parts)

    return {a: k for a in raw for k in [resolve(a)] if k}


def profile_keys(binds: dict[str, str]) -> dict[str, str | dict[str, str | None]]:
    """The profile settings these bindings give, for the ones that exist: a key, or (move_keys,
    target_keys) a key per direction."""
    out: dict[str, str | dict[str, str | None]] = {}
    move = {d: binds.get("W_MOVE_" + a) for d, a in (("up", "NORTH"), ("down", "SOUTH"), ("left", "WEST"),
                                                    ("right", "EAST"))}
    if all(move.values()):
        out["move_keys"] = move
    turn = {d: binds.get("W_%s_TURN" % d.upper()) for d in ("west", "north", "south", "east")}
    if all(turn.values()):
        out["target_keys"] = turn
    for setting, action in (("pk_key", "W_SET_ATTACKMODE"), ("target_mode_key", "W_SET_TURNMODE"),
                            ("bag_up", "W_UP"), ("bag_down", "W_DOWN"), ("bag_left", "W_LEFT"),
                            ("bag_right", "W_RIGHT"), ("use_key", "W_USE"), ("category_key", "W_WPTYPEDOWN"),
                            ("sheet_key", "W_SELF_INFO"), ("vitals_key", "W_SELF_HP"),
                            ("points_key", "W_SELF_LJ"), ("pet_vitals_key", "W_PET_HP"),
                            ("teleport_key", "W_AMOVE")):
        if binds.get(action):
            out[setting] = binds[action]
    return out


def slot_keys(binds: dict[str, str]) -> list[str | None]:
    """[key for hotkey slot 1..12] (None where unbound)."""
    return [binds.get("W_USE%d" % i) for i in range(1, 13)]


def phrase_keys(binds: dict[str, str]) -> list[str | None]:
    """[key for quick phrase 1..12] (W_MSG1..12, Shift + the number row)."""
    return [binds.get("W_MSG%d" % i) for i in range(1, 13)]


def phrase_key_list() -> list[str]:
    """The 12 quick phrase keys of the player's game, the default where one isn't bound."""
    return [k or "shift+" + d for k, d in zip(phrase_keys(_game()), SLOT_DEFAULTS)]


def attack_keys(binds: dict[str, str]) -> list[str | None]:
    """[key for attack 1..10]."""
    return [binds.get("W_ATTACK%d" % i) for i in range(1, 11)]


def _game() -> dict[str, str]:
    return read(settings.load_app().get("game_folder"))


def attack_key_list() -> list[str]:
    """The 10 skill keys of the player's game, the game's own default where one isn't bound."""
    return [k or d for k, d in zip(attack_keys(_game()), ATTACK_DEFAULTS)]


def slot_key_list() -> list[str]:
    """The 12 hotkeys (1 .. =) of the player's game, the default where one isn't bound. (Unbound
    used to come back as None: with no input.ini found, no potion was ever on a hotkey.)"""
    return [k or d for k, d in zip(slot_keys(_game()), SLOT_DEFAULTS)]
