"""Team chores a trusted player can ask for by private message, done through the game's own menus.

An invite arrives as a server dialog (IN op=62):
    <name> invites you to join the "<team>" team. Do you want to accept it?   [yes] [no]
The rest goes through the Action menu (W_LOCAL_OPTION, Ctrl+Enter), whose team row reads
"<name> team"; a leader's row carries "apply to join: N" while applications wait. A step that
isn't found ends the chore and says which step it was.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from ..i18n import _

if TYPE_CHECKING:
    from .bot import Bot

INVITE = re.compile(r'^\s*(.+?) invites you to join the "?(.*?)"? team', re.I)


def invite_in(texts: Sequence[str | None]) -> tuple[str, str] | None:
    """(inviter, team) from a dialog's texts, or None."""
    for t in texts:
        m = INVITE.match(t or "")
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return None


def accept_invite(bot: Bot) -> bool:
    """Answer the open invite with yes."""
    return bot.choose(r"^\s*yes\s*$") is not None


def _menu(bot: Bot, steps: Sequence[str], close: bool = True) -> int | None:
    """Open the Action menu and pick each step in turn. None when done, else the step that failed.
    close=False leaves whatever the last step opened (a question to answer) open."""
    bot.close_dialogs()
    bot.key(bot.p.get("action_menu_key", "lctrl+enter"))
    for i, pattern in enumerate(steps):
        if bot.wait_dialog(3.0) is None:
            return i
        if bot.choose(pattern) is None:
            return i
        bot.sleep(0.4)
    if close:
        bot.close_dialogs()
    return None


def _name(name: str):
    return r"^\s*%s(\W|$)" % re.escape(name)


def accept_application(bot: Bot, name: str):
    """Our team: take name's application to join."""
    failed = _menu(bot, [r"apply to join", _name(name), r"agree"])
    if failed is None:
        return True, _("accepted {name} into the team", name=name)
    return False, [_("there are no team applications waiting"),
                   _("I don't see an application from {name}", name=name),
                   _("the game didn't offer to agree")][failed]


TRANSFER = r"^\s*transfer team leader"
YES = r"^\s*(yes|confirm|ok|sure)\b"


def make_leader(bot: Bot, name: str):
    """Hand the team's leadership to name. The team row opens the member list, one row per member:
    "name;id" first, then (for the leader only) "Transfer team leader" and "remove" for that member.
    The transfer entry of name's own row is the one: the first "transfer" or "leader" anywhere was
    another member's, or our own row's "leader" (live 2026-10-22: the leadership never moved)."""
    if _menu(bot, [r"\bteam\b"], close=False) is not None:
        return False, _("I'm not in a team")
    d = bot.wait_dialog(3.0)
    top = (d or {}).get("top")
    if bot.find_in_row(top, _name(name), TRANSFER) is None:
        bot.close_dialogs()
        if top and any(row and row[0] and any(re.match(_name(name), t, re.I) for t in bot.entry_texts(row[0]))
                       for row in top["rows"]):
            return False, _("I'm not the team leader")
        return False, _("{name} isn't in my team", name=name)
    if bot.choose(TRANSFER, row=_name(name)) is None:
        return False, _("{name} isn't in my team", name=name)
    # the game asks "Are you sure you want to transfer the team leader to this member?"
    asked = None
    for _try in range(10):
        d = bot.wait_dialog(1.0)
        top = (d or {}).get("top")
        if top and bot.find_entry(top, TRANSFER) is None and bot.find_entry(top, YES):
            asked = top
            break
        bot.sleep(0.3)
    if asked is None or bot.choose(YES) is None:
        bot.close_dialogs()
        return False, _("the game didn't ask to confirm")
    bot.close_dialogs()
    return True, _("{name} leads the team now", name=name)


def leave(bot: Bot):
    """The Action menu's team row holds "<name> team" (opening it lists the members) and, beside
    it, "quit" with the question "Do you want to quit the <name> team?". The guild's row has its
    own "quit", so the question picks the team's."""
    failed = _menu(bot, [r"quit the .*\bteam\b"], close=False)
    if failed is not None:
        return False, _("I'm not in a team")
    d = bot.wait_dialog(2.0)
    top = (d or {}).get("top")
    if top and bot.find_entry(top, r"^\s*(yes|confirm|ok|sure)\b"):
        if bot.choose(r"^\s*(yes|confirm|ok|sure)\b") is None:
            return False, _("the game didn't ask to confirm")
    bot.close_dialogs()
    return True, _("left the team")
