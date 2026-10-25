"""Trusted players: the ones who may give commands by private message, whose team invites are taken
and whose messages are read out. The player lists them by name or by the game's id, mixed in one
list ("Kim, 2001"); either matches.
"""

from typing import Any, Iterable


class Trusted:
    def __init__(self, entries: Iterable[Any] = ()):
        self.names: set[str] = set()
        self.ids: set[int] = set()
        for e in entries:
            e = str(e).strip()
            if e.isdigit():
                self.ids.add(int(e))
            elif e:
                self.names.add(e.lower())

    def __bool__(self):
        return bool(self.names or self.ids)

    def has(self, name: str | None = None, pid: Any = None, link: Any = None) -> bool:
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


def from_settings(app):
    return Trusted(app.get("pm_trusted", []))
