"""Trusted players: the ones who may give commands by private message, whose team invites are taken
and whose messages are read out. The player lists them by name or by the game's id, mixed in one
list ("Kim, 2001"); either matches.

A name repeats from one server to the next, and an id isn't known to be unique across them: an entry
may name its server after an @ ("Kim@S1", "2001@S2"), and then counts on that server only. One
without a server counts on every server, as before - a stranger of the same name on another server
passed as the trusted player. The private messages window adds the selected character's server to
the names typed in it (with_server).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .link import SendLink


def split(entry: object) -> tuple[str, str]:
    """(name or id, server) of one entry; the server is "" when it names none."""
    text = str(entry).strip()
    who, at, server = text.rpartition("@")
    return (who.strip(), server.strip()) if at and who.strip() else (text, "")


def with_server(entries: Iterable[object], before: Iterable[object], server: str) -> list[str]:
    """The entries as saved: the ones that weren't there before (before) get the server, when one
    is known and they name none; an entry already listed stays as the player had it."""
    old = {str(e).strip().lower() for e in before}
    out: list[str] = []
    for e in entries:
        text = str(e).strip()
        if not text:
            continue
        if server and text.lower() not in old and not split(text)[1]:
            text = "%s@%s" % (text, server)
        out.append(text)
    return out


class Trusted:
    def __init__(self, entries: Iterable[object] = (), server: str = ""):
        """server: the one this game plays on ("" when not known: an entry naming a server then
        doesn't count - it may be another server's player)."""
        self.names: set[str] = set()
        self.ids: set[int] = set()
        here = server.strip().lower()
        for e in entries:
            who, where = split(e)
            if where and where.lower() != here:
                continue                # another server's player of that name or id
            if who.isdigit():
                self.ids.add(int(who))
            elif who:
                self.names.add(who.lower())

    def __bool__(self):
        return bool(self.names or self.ids)

    def has(self, name: str | None = None, pid: int | str | None = None, link: SendLink | None = None) -> bool:
        """Trusted by name or by id. With only a name and ids in the list, the agent's `idof` (the
        players the game has shown) can tell whose name it is."""
        name = (name or "").strip()
        if name and name.lower() in self.names:
            return True
        if pid:
            try:
                return int(pid) in self.ids
            except (TypeError, ValueError):
                return False
        if name and self.ids and link is not None:
            try:
                return int(link.send("idof " + name)) in self.ids
            except Exception:       # an older agent, or the game closed: the name didn't match
                return False
        return False


def from_settings(app: dict[str, Any], server: str = "") -> Trusted:
    return Trusted(app.get("pm_trusted", []), server)
