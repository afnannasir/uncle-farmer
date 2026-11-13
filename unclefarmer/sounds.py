"""Sound ID filters and checked replies from the game's sound observer.

WATCH and MUTE belong to the game. LISTEN belongs to one connection and is removed when
that connection closes; scripts use it without replacing a user's WATCH selection.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, TypedDict

from . import jsonio
from .i18n import _
from .link import AgentError, JsonLink


@dataclass(frozen=True)
class IdSet:
    ranges: tuple[tuple[int, int], ...]

    @classmethod
    def parse(cls, text: str) -> IdSet:
        text = text.strip().lower()
        if text == "all":
            return cls(((1, 99999),))
        if text in ("off", "none"):
            return cls(())
        if len(text) > 10000 or not text:
            raise ValueError(_("Enter sound IDs, ranges, all, or off."))
        spans: list[tuple[int, int]] = []
        for part in re.split(r"[,|;]", text):
            match = re.fullmatch(r"\s*([0-9]+)\s*(?:-\s*([0-9]+))?\s*", part)
            if match is None:
                raise ValueError(_("Enter sound IDs, ranges, all, or off."))
            low = int(match[1])
            high = int(match[2]) if match[2] else low
            if not 1 <= low <= high <= 99999:
                raise ValueError(_("Sound IDs must be between 1 and 99999; ranges must go upwards."))
            spans.append((low, high))
        merged: list[tuple[int, int]] = []
        for low, high in sorted(spans):
            if merged and low <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
            else:
                merged.append((low, high))
        return cls(tuple(merged))

    @property
    def text(self) -> str:
        if not self.ranges:
            return "off"
        if self.ranges == ((1, 99999),):
            return "all"
        return ",".join(str(low) if low == high else f"{low}-{high}" for low, high in self.ranges)

    def has(self, sound_id: int) -> bool:
        return any(low <= sound_id <= high for low, high in self.ranges)

    def size(self) -> int:
        return sum(high - low + 1 for low, high in self.ranges)

    def is_empty(self) -> bool:
        return not self.ranges


class SoundEvent(TypedDict):
    seq: int
    id: int
    x: float | None
    y: float | None
    t: int
    muted: bool


class SoundBatch(TypedDict):
    now: int
    events: list[SoundEvent]
    lost: bool


def _reply(link: JsonLink, command: str) -> dict[str, Any]:
    try:
        data = link.json(command)
    except AgentError as error:
        if str(error) == "bad reply":
            raise AgentError(_("Sound controls require an updated game agent. Install it and restart the game.")) from error
        raise
    if not jsonio.is_object(data):
        raise AgentError(_("Invalid sound reply from the game agent."))
    if data.get("ok") is False or "error" in data:
        raise AgentError(str(data.get("error") or _("Sound controls are unavailable.")))
    return data


def _status(link: JsonLink, command: str) -> dict[str, Any]:
    data = _reply(link, command)
    if not isinstance(data.get("hooked"), bool) or not all(isinstance(data.get(k), str) for k in ("watch", "mute", "reason")):
        raise AgentError(_("Sound controls require an updated game agent. Install it and restart the game."))
    if not all(type(data.get(k)) is int and data[k] >= 0 for k in ("observed", "dropped", "live_muted", "unsupported")):
        raise AgentError(_("Invalid sound reply from the game agent."))
    return data


def status(link: JsonLink) -> dict[str, Any]:
    return _status(link, "soundstatus")


def watch(link: JsonLink, text: str) -> dict[str, Any]:
    return _status(link, "soundwatch " + IdSet.parse(text).text)


def mute(link: JsonLink, text: str) -> dict[str, Any]:
    return _status(link, "soundmute " + IdSet.parse(text).text)


def listen(link: JsonLink, text: str) -> dict[str, Any]:
    return _status(link, "soundlisten " + IdSet.parse(text).text)


def events(link: JsonLink, after: int) -> SoundBatch:
    if type(after) is not int or after < 0:
        raise ValueError("sound cursor must be a non-negative integer")
    data = _reply(link, f"sounds {after}")
    now = data.get("now")
    rows = data.get("events")
    if type(now) is not int or now < after or not isinstance(data.get("lost"), bool) or not jsonio.is_array(rows):
        raise AgentError(_("Invalid sound reply from the game agent."))
    result: list[SoundEvent] = []
    previous = after
    for row in rows:
        if not jsonio.is_object(row) or not all(type(row.get(k)) is int for k in ("seq", "id", "t")):
            raise AgentError(_("Invalid sound reply from the game agent."))
        if not previous < row["seq"] <= now or not 1 <= row["id"] <= 99999 or not isinstance(row.get("muted"), bool):
            raise AgentError(_("Invalid sound reply from the game agent."))
        if not all(k in row and (row[k] is None or type(row[k]) in (int, float) and math.isfinite(row[k])) for k in ("x", "y")):
            raise AgentError(_("Invalid sound reply from the game agent."))
        previous = row["seq"]
        result.append(SoundEvent(seq=row["seq"], id=row["id"], x=float(row["x"]) if row["x"] is not None else None,
                                 y=float(row["y"]) if row["y"] is not None else None,
                                 t=row["t"], muted=row["muted"]))
    return SoundBatch(now=now, events=result, lost=data["lost"])
