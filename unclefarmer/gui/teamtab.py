"""The character window's Team tab: leading a team (taking requests, removing members who went
offline, handing the team over, making it) and following one (healing teammates). Every choice of
the leader's is off until the player ticks it; the engine side is engine/teamlead.py."""

from __future__ import annotations

import re
from typing import Any, TypedDict, cast

import wx

from ..i18n import _
from .common import group

WHO = ("trusted", "friends", "list")
NAME = re.compile(r"^[A-Za-z0-9]{1,20}$")     # what the game took ("TestFarmer"); the script's rule too


class TeamUI(TypedDict):
    accept: wx.CheckBox
    who: wx.Choice
    accept_list: wx.TextCtrl
    remove_offline: wx.CheckBox
    offline_every: wx.SpinCtrl
    town_only: wx.CheckBox
    hand_over: wx.CheckBox
    hand_to: wx.TextCtrl
    create: wx.CheckBox
    name: wx.TextCtrl
    remake: wx.CheckBox
    leave_others: wx.CheckBox
    stay: wx.CheckBox
    heal: wx.CheckBox
    heal_below: wx.SpinCtrl


def names_text(values: object) -> str:
    """A saved list of names and ids, as the text box shows it."""
    if not isinstance(values, list):
        return ""
    return ", ".join(str(v) for v in cast(list[object], values))


def names_list(text: str) -> list[str | int]:
    """The text box's names and ids (commas or semicolons between them); a number is an id."""
    out: list[str | int] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if part:
            out.append(int(part) if part.isdigit() else part)
    return out


def _below(v: object, default: int) -> int:
    return default if v in (None, "", "auto") else int(float(str(v)))


def team_page(panel: wx.Window, box: wx.Sizer, p: dict[str, Any]) -> TeamUI:
    f = group(panel, box, _("Leading a team"))
    accept = f.check(_("&Accept requests to join my team"), p.get("team_accept", False))
    who = f.choice(_("&Who may join:"), [_("Trusted players (Private messages)"), _("My friends in the game"),
                                        _("The names or ids below")],
                   WHO.index(p.get("team_accept_who")) if p.get("team_accept_who") in WHO else 0)
    accept_list = f.text(_("Names or &ids who may join (separated by commas):"), names_text(p.get("team_accept_list")))
    remove_offline = f.check(_("Remove members who go &offline"), p.get("team_remove_offline", False))
    offline_every = f.number(_("Check for offline members every (minutes):"), p.get("team_offline_every", 10), 1, 120)
    town_only = f.check(_("Check for offline members only when the run &passes through a town"),
                        p.get("team_offline_town_only", False))
    hand_over = f.check(_("&Hand the team over when the run ends"), p.get("team_hand_over", False))
    hand_to = f.text(_("Hand it to (names or ids; the &first one in the team gets it):"),
                     names_text(p.get("team_hand_to")))
    create = f.check(_("&Create my team when the run starts, if I'm in none"), p.get("team_create", False))
    name = f.text(_("Team &name (letters and numbers):"), p.get("team_name", ""))
    remake = f.check(_("If my team has another name, &disband it and create it again"), p.get("team_remake", False))
    leave_others = f.check(_("&Leave a team another player leads when the run starts"),
                           p.get("team_leave_others", False))
    stay = f.check(_("After the run, &stay in town and manage the team until I stop it"),
                   p.get("team_stay_in_town", False))
    f = group(panel, box, _("Following a leader"))
    heal = f.check(_("Heal the player I follow and our &teammates with my healing skill (Priest, Sorceress)"),
                   p.get("team_heal", True))
    heal_below = f.number(_("Heal a teammate when their HP is below (%):"), _below(p.get("team_heal_below"), 70), 1, 99)
    return {"accept": accept, "who": who, "accept_list": accept_list, "remove_offline": remove_offline,
            "offline_every": offline_every, "town_only": town_only, "hand_over": hand_over, "hand_to": hand_to,
            "create": create, "name": name, "remake": remake, "leave_others": leave_others, "stay": stay,
            "heal": heal, "heal_below": heal_below}


def load_team(ui: TeamUI, p: dict[str, Any]) -> None:
    """The tab's choices again, from the profile (after a copy from another character)."""
    ui["accept"].SetValue(bool(p.get("team_accept", False)))
    ui["who"].SetSelection(WHO.index(p.get("team_accept_who")) if p.get("team_accept_who") in WHO else 0)
    ui["accept_list"].SetValue(names_text(p.get("team_accept_list")))
    ui["remove_offline"].SetValue(bool(p.get("team_remove_offline", False)))
    ui["offline_every"].SetValue(int(p.get("team_offline_every", 10)))
    ui["town_only"].SetValue(bool(p.get("team_offline_town_only", False)))
    ui["hand_over"].SetValue(bool(p.get("team_hand_over", False)))
    ui["hand_to"].SetValue(names_text(p.get("team_hand_to")))
    ui["create"].SetValue(bool(p.get("team_create", False)))
    ui["name"].SetValue(str(p.get("team_name", "")))
    ui["remake"].SetValue(bool(p.get("team_remake", False)))
    ui["leave_others"].SetValue(bool(p.get("team_leave_others", False)))
    ui["stay"].SetValue(bool(p.get("team_stay_in_town", False)))
    ui["heal"].SetValue(bool(p.get("team_heal", True)))
    ui["heal_below"].SetValue(_below(p.get("team_heal_below"), 70))


def save_team(p: dict[str, Any], ui: TeamUI) -> None:
    """The tab's choices into the profile. ValueError (nothing changed) when a ticked choice lacks
    what it needs: a team name the game takes, someone to hand the team to, names to let in."""
    name = ui["name"].GetValue().strip()
    who = WHO[max(0, ui["who"].GetSelection())]
    accept_list = names_list(ui["accept_list"].GetValue())
    hand_to = names_list(ui["hand_to"].GetValue())
    if (ui["create"].GetValue() or ui["remake"].GetValue()) and not NAME.match(name):
        raise ValueError(_("Team tab: the team name needs 1 to 20 letters and numbers, without spaces."))
    if ui["accept"].GetValue() and who == "list" and not accept_list:
        raise ValueError(_("Team tab: type the names or ids who may join, or choose another \"Who may join\"."))
    if ui["hand_over"].GetValue() and not hand_to:
        raise ValueError(_("Team tab: type who gets the team when the run ends."))
    p.update(team_accept=ui["accept"].GetValue(), team_accept_who=who, team_accept_list=accept_list,
             team_remove_offline=ui["remove_offline"].GetValue(), team_offline_every=ui["offline_every"].GetValue(),
             team_offline_town_only=ui["town_only"].GetValue(), team_hand_over=ui["hand_over"].GetValue(),
             team_hand_to=hand_to, team_create=ui["create"].GetValue(), team_name=name,
             team_remake=ui["remake"].GetValue(), team_leave_others=ui["leave_others"].GetValue(),
             team_stay_in_town=ui["stay"].GetValue(), team_heal=ui["heal"].GetValue(),
             team_heal_below=ui["heal_below"].GetValue())
