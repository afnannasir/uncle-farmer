"""How to reach the developer: Help, Contact the developer, and the README's Contact section.

The address is the developer's public contact, there on purpose, and kept in this one place; the
translations only carry "{email}", never the address itself.
"""

from __future__ import annotations

EMAIL = "afnan.nasir2000@gmail.com"
ISSUES = "https://github.com/afnannasir/uncle-farmer/issues"


def mailto(subject: str = "") -> str:
    """A mailto: link to the developer, with a subject when one is given."""
    from urllib.parse import quote
    return "mailto:%s" % EMAIL + ("?subject=%s" % quote(subject) if subject else "")
