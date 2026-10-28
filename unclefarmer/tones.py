"""Short notification tones, played in the background: a private message from a trusted player and
one from anyone else can sound different, so the player knows which it is without looking.

Plain beeps (winsound.Beep) rather than Windows' sound scheme: the same on every PC, and never
silenced by a sound theme.
"""

from __future__ import annotations

import threading

from .i18n import N_, _

# name -> [(frequency in Hz, milliseconds)]; a frequency of 0 is a pause
TONES: dict[str, list[tuple[int, int]]] = {
    "chime": [(880, 70), (1320, 110)],
    "ding": [(1047, 120)],
    "double": [(988, 60), (0, 50), (988, 60)],
    "rising": [(660, 50), (880, 50), (1175, 80)],
    "falling": [(1175, 50), (880, 50), (660, 80)],
    "low": [(440, 160)],
}
ORDER = ["", "chime", "ding", "double", "rising", "falling", "low"]
NAMES = {"": N_("No tone"), "chime": N_("Chime"), "ding": N_("Ding"), "double": N_("Double beep"),
         "rising": N_("Rising"), "falling": N_("Falling"), "low": N_("Low beep")}


def label(name: str) -> str:
    return _(NAMES.get(name, NAMES[""]))


def _play(steps: list[tuple[int, int]]) -> None:
    try:
        import time
        import winsound
        for freq, ms in steps:
            if freq:
                winsound.Beep(freq, ms)
            else:
                time.sleep(ms / 1000.0)
    except (ImportError, RuntimeError):
        pass                       # no sound device: nothing to play on


def play(name: str) -> None:
    """Play a tone by name ("" or an unknown name: nothing) without waiting for it."""
    steps = TONES.get(name)
    if steps:
        threading.Thread(target=_play, args=(steps,), daemon=True).start()
