"""The app-wide named sound list, stored with the existing application settings.

Saving stores the user's choice before applying it to the selected game. A failed agent
request never discards saved choices, and never claims that live muting succeeded.
"""

from __future__ import annotations

import gzip
from pathlib import Path
import threading
from typing import TypedDict
import unicodedata

from . import applog, gameinstall, jsonio, settings, sounds
from .i18n import _
from .link import AgentError, JsonLink

KEY = "sound_silencer"
_save_lock = threading.RLock()


class SoundRow(TypedDict):
    id: int
    name: str
    muted: bool


def validate(rows: object) -> list[SoundRow]:
    if not jsonio.is_array(rows) or len(rows) > 99999:
        raise ValueError(_("The sound list must be a list of sound entries."))
    result: list[SoundRow] = []
    seen: set[int] = set()
    for row in rows:
        if not jsonio.is_object(row):
            raise ValueError(_("Each sound needs an ID, a name, and a mute setting."))
        sound_id, name, muted = row.get("id"), row.get("name"), row.get("muted")
        if type(sound_id) is not int or not 1 <= sound_id <= 99999:
            raise ValueError(_("Sound IDs must be whole numbers between 1 and 99999."))
        if sound_id in seen:
            raise ValueError(_("Sound ID {id} is already in the list.", id=sound_id))
        if not isinstance(name, str) or len(name.strip()) > 100 or any(unicodedata.category(c).startswith("C") for c in name):
            raise ValueError(_("Sound names must be at most 100 characters without control characters."))
        if type(muted) is not bool:
            raise ValueError(_("A sound's mute setting must be on or off."))
        seen.add(sound_id)
        result.append(SoundRow(id=sound_id, name=name.strip(), muted=muted))
    return result


def load() -> list[SoundRow]:
    try:
        return validate(settings.load_app().get(KEY, []))
    except (OSError, ValueError) as error:
        applog.write(_("Could not read the saved sound list: {error}", error=error), "WARNING")
        return []


def mute_text(rows: object) -> str:
    muted = [str(row["id"]) for row in validate(rows) if row["muted"]]
    return sounds.IdSet.parse(",".join(muted) if muted else "off").text


def _apply(link: JsonLink, text: str) -> None:
    status = sounds.mute(link, text)
    if not status["hooked"]:
        raise AgentError(_("Sound observer is unavailable: {reason}", reason=status["reason"]))


def save(rows: object, link: JsonLink) -> None:
    normalized = validate(rows)
    with _save_lock:
        app = settings.load_app()
        app[KEY] = normalized
        settings.save_app(app)
        try:
            _apply(link, mute_text(normalized))
        except (AgentError, OSError, ValueError) as error:
            raise AgentError(_("Sound settings were saved, but active muting could not be confirmed: {error}",
                               error=error)) from error


def apply_saved(link: JsonLink) -> None:
    """Apply the named list on game discovery/restart; the caller decides when to retry."""
    _apply(link, mute_text(load()))


def known_ids(pid: int | None) -> set[int]:
    """Use the same sound index/asset rules as the Java observer, scoped to this game copy."""
    app = settings.load_app()
    folder = gameinstall.folder_of(pid, str(app.get("game_folder") or ""))
    if not folder:
        return set()
    game = Path(folder)
    ids: set[int] = set()
    try:
        index = game / "sound-index.csv.gz"
        if index.is_file():
            with gzip.open(index, "rt", encoding="utf-8") as stream:
                for line in stream:
                    _add_known(ids, line.split(",", 1)[0].strip())
        else:
            for asset in (game / "sound").iterdir():
                if asset.is_file() and asset.suffix:
                    _add_known(ids, asset.stem)
    except (OSError, UnicodeError, EOFError) as error:
        applog.write(_("Could not read known sound IDs: {error}", error=error), "WARNING")
        return set()
    return ids


def _add_known(ids: set[int], text: str) -> None:
    try:
        sound_id = int(text)
        if 1 <= sound_id <= 99999:
            ids.add(sound_id)
    except ValueError:
        pass
