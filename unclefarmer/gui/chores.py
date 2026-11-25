"""Tools, Daily chores: what the bot does once a game day for one character (engine/chores.py).

Saved in the character's profile. Check only walks to each NPC and reads the menus without
collecting, spending or entering anything; Start does the chores that aren't done today.
"""

from __future__ import annotations

from typing import Any

import wx

from .. import settings
from ..engine import chores, constructionscan
from ..i18n import _
from .common import Dialog, copy_text, enable_label, with_code, Form, group, page, save_file, tabs
from .crafting import CHECK, START

DONATIONS = ("off", "10000", "20000", "diamonds")
SCAN_CONSTRUCTIONS, PUBLISH_REPORT = wx.ID_HIGHEST + 111, wx.ID_HIGHEST + 112
PUBLISH_KINDS = ("announcement", "broadcast", "system")


def donation_names():
    return [_("Don't donate"), _("10,000 arnebia (1,500 gang contribution)"),
            _("20,000 arnebia (3,000 gang contribution)"), _("20 diamonds (5,000 gang contribution)")]


def assistant_names():
    return [_("Snowflake prince"), _("Snowflake princess"), _("Snowball prince"), _("Snowball princess"),
            _("Snow mountain prince"), _("Snow mountain princess"), _("Glacier prince"), _("Glacier princess"),
            _("Ice prince"), _("Ice princess")]


class ChoresDialog(Dialog):
    def __init__(self, parent: wx.Window, character: str) -> None:
        super().__init__(parent, title=_("{tool} - {name}", tool=_("Daily chores"), name=settings.profile_display(character)),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.character = character
        self.p = settings.load_profile(character)
        c = self.cfg = chores.config(self.p)
        root = wx.BoxSizer(wx.VERTICAL)
        nb = tabs(self)

        panel, box = page(nb, _("City"))
        f = group(panel, box, _("Every day"))
        self.bank = f.check(_("Collect the &bank's deposit income and Value Product incomes"), c["bank"])
        f = group(panel, box, _("Pet rebirth (free ones only, never for diamonds)"))
        self.rebirth = f.check(_("Use the day's free &rebirths on one pet"), c["pet_rebirth"])
        scan: dict[str, Any] = self.p.get("scan") or {}
        pets: list[str] = list(scan.get("pets") or [])
        if c["pet"] and c["pet"] not in pets:
            pets.append(c["pet"])
        self.pet = f.add(_("&Pet:"), lambda w: wx.ComboBox(w, value=c["pet"], choices=pets))
        self.rebirths = f.number(_("Free rebirths a day (the game gives 2):"), c["rebirths"], 1, 2)
        f = group(panel, box, _("Contribution Chests"))
        self.chests = f.check(_("E&xchange contribution for Contribution Chests (5,000 each, at Guard Captain Suke)"),
                              c["contribution_chests"])
        self.chest_count = f.number(_("Contribution Chests a day (the game allows 2):"),
                                    max(1, min(int(c["chest_count"] or 2), 2)), 1, 2)

        panel, box = page(nb, _("Gang"))
        f = group(panel, box, _("Inner hall"))
        self.donation = f.choice(_("&Donation, once a day:"), donation_names(),
                                 DONATIONS.index(c["donation"]) if c["donation"] in DONATIONS else 0)
        self.construction = f.number(_("&Construction builds a day (the game allows 2; free, each pays contribution and arnebia):"),
                                     min(int(c["construction"] or 0), 2), 0, 2)
        self.build_highlands = f.check(_("Repair our gang's &highlands (first what is under 100%, what is at 100% last)"),
                                       c["build_highlands"])
        self.build_cannon = f.check(_("Build the Magic Ca&nnon under construction"), c["build_cannon"])
        self.build_supply = f.check(_("Build the S&upply Camp under construction"), c["build_supply"])
        self.build_posts = f.check(_("Build at the East and West &posts"), c["build_posts"])
        self.feed = f.check(_("&Feed the gang's breed beast at the Animal Shed"), c["feed"])
        self.keep_arnebia = f.number(_("Never spend arnebia belo&w:"), self.p.get("gem_keep_arnebia", 20000),
                                     0, 1000000000)

        panel, box = page(nb, _("Secret room"))
        f = group(panel, box, _("Sharing arnebia (both players get some; once in each half hour, 5 times a day)"))
        self.room_on = f.check(_("Share arnebia in a &secret room"), c["secret_room"])
        self.share_with = f.text(_("Share &with (names, commas between, first one in the room first):"),
                                 c["share_with"])
        self.share_anyone = f.check(_("If none of them is in the room, share with &anyone there"), c["share_anyone"])
        self.trips = f.check(_("While farming, go and share just before each half &hour, then come back"),
                             c["share_trips"])
        self.lead = f.number(_("Leave the farm this many &minutes before the half hour (the walk there included):"),
                             c["trip_lead"], 1, 29)
        f = group(panel, box, _("The room"))
        self.room = f.text(_("&Room (part of its name; empty: the highest level free room):"), c["room"])
        self.assistant = f.choice(_("&Assistant (it kills for you; you can't fight in there):"), assistant_names(),
                                  int(c["assistant"]) - 1)
        self.minutes = f.number(_("&Practise for (minutes; 0: in, share, out; time inside uses drop time):"),
                                c["practice_minutes"], 0, 180)

        panel, box = page(nb, _("When"))
        f = Form(panel)
        self.before = f.check(_("Do the day's chores at the start of the &first farming run each game day"),
                              c["before_farming"])
        done = chores.done_today(character)
        lines = ["%s: %s (%s)" % (chores.label(k), v.get("note", ""), v.get("at", "")[11:16])
                 for k, v in done.items() if k in chores.CHORES]
        made, _used = chores.shares(character)
        if made:
            lines.append(_("arnebia shared {n} of {all} times", n=made, all=chores.SHARES_A_DAY))
        box.Add(f.grid, 0, wx.EXPAND)
        box.Add(wx.StaticText(panel, label=_("Done this game day (it starts at 00:00 GMT):")), 0, wx.TOP, 12)
        box.Add(wx.TextCtrl(panel, value="\n".join(lines) or _("Nothing yet."),
                            style=wx.TE_MULTILINE | wx.TE_READONLY, size=wx.Size(-1, 120)), 1, wx.EXPAND)

        root.Add(nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        for label, code in ((_("Save"), wx.ID_OK), (_("&Check only"), CHECK), (_("Sta&rt"), START)):
            btn = wx.Button(self, label=label)
            btn.Bind(wx.EVT_BUTTON, with_code(self.done, code))
            buttons.Add(btn, 0, wx.RIGHT, 8)
        cancel = wx.Button(self, wx.ID_CANCEL, _("Cancel"))
        buttons.Add(cancel, 0)
        root.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizerAndFit(root)
        self.SetMinSize(wx.Size(620, 520))

    def done(self, code: int) -> None:
        pet = self.pet.GetValue().strip()
        if self.rebirth.GetValue() and not pet:
            wx.MessageBox(_("Choose the pet to rebirth, or untick pet rebirth."), _("Pet rebirth"),
                          wx.OK | wx.ICON_INFORMATION, self)
            self.pet.SetFocus()
            return
        self.cfg.update(bank=self.bank.GetValue(),
                        pet_rebirth=self.rebirth.GetValue(), pet=pet, rebirths=self.rebirths.GetValue(),
                        donation=DONATIONS[self.donation.GetSelection()],
                        construction=self.construction.GetValue(),
                        build_highlands=self.build_highlands.GetValue(), build_cannon=self.build_cannon.GetValue(),
                        build_supply=self.build_supply.GetValue(), build_posts=self.build_posts.GetValue(),
                        feed=self.feed.GetValue(), contribution_chests=self.chests.GetValue(),
                        chest_count=self.chest_count.GetValue(),
                        secret_room=self.room_on.GetValue(), room=self.room.GetValue().strip(),
                        assistant=self.assistant.GetSelection() + 1, practice_minutes=self.minutes.GetValue(),
                        share_with=self.share_with.GetValue().strip(), share_anyone=self.share_anyone.GetValue(),
                        share_trips=self.trips.GetValue(), trip_lead=self.lead.GetValue(),
                        before_farming=self.before.GetValue())
        for old in ("room_minutes", "share", "build_highland", "highland", "build_at", "bounty"):       # settings of earlier versions
            self.cfg.pop(old, None)
        self.p["chores"] = self.cfg
        self.p["gem_keep_arnebia"] = self.keep_arnebia.GetValue()
        if save_file(self, settings.save_profile, self.character, self.p):
            self.EndModal(code)


class BountiesDialog(Dialog):
    """Tools, Hunting bounties: which officers to ask. Each pays for bosses of his own, and only
    after the character landed the killing blow, so this runs when the player asks."""

    LABELS = {"Guard Captain Suke": "&Guard Captain Suke", "Chancellor Bren": "Chancellor &Bren",
              "General Paul": "General &Paul", "Colonel Demar": "Colonel &Demar"}

    def __init__(self, parent: wx.Window, character: str) -> None:
        super().__init__(parent, title=_("{tool} - {name}", tool=_("Hunting bounties"),
                                         name=settings.profile_display(character)))
        self.character = character
        self.p = settings.load_profile(character)
        self.cfg = chores.config(self.p)
        root = wx.BoxSizer(wx.VERTICAL)
        inner = wx.BoxSizer(wx.VERTICAL)
        f = group(self, inner, _("Officers to visit (each pays for his own bosses, after your killing blow)"))
        chosen: list[str] = list(self.cfg["bounty_officers"])
        self.officers = {n: f.check(self.LABELS[n], n in chosen) for n in chores.BOUNTY_OFFICERS}
        root.Add(inner, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        for label, code in ((_("Save"), wx.ID_OK), (_("&Check only"), CHECK), (_("Sta&rt"), START)):
            btn = wx.Button(self, label=label)
            btn.Bind(wx.EVT_BUTTON, with_code(self.done, code))
            buttons.Add(btn, 0, wx.RIGHT, 8)
        buttons.Add(wx.Button(self, wx.ID_CANCEL, _("Cancel")), 0)
        root.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizerAndFit(root)

    def done(self, code: int) -> None:
        chosen = [n for n, box in self.officers.items() if box.GetValue()]
        if not chosen and code != wx.ID_OK:
            wx.MessageBox(_("Choose at least one officer."), _("Hunting bounties"), wx.OK | wx.ICON_INFORMATION, self)
            return
        self.cfg["bounty_officers"] = chosen
        self.cfg.pop("bounty", None)
        self.p["chores"] = self.cfg
        if save_file(self, settings.save_profile, self.character, self.p):
            self.EndModal(code)


class ConstructionScanDialog(Dialog):
    """Tools, Construction scanner: the last reading of the gang's constructions, to read, copy or publish.
    Publishing takes a gang elder's rights, and always asks first. can_act: a character is logged in on
    the selected game and no bot runs there; without it the last report can still be read and copied."""

    def __init__(self, parent: wx.Window, can_act: bool = True) -> None:
        super().__init__(parent, title=_("Construction scanner"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.report = constructionscan.load()
        self.kind = PUBLISH_KINDS[0]
        root = wx.BoxSizer(wx.VERTICAL)
        if self.report is None:
            shown = _("No report yet. Scan the constructions: the bot visits each one and repairs nothing.")
        else:
            shown = "\n".join((_("Read {at} by {who}", at=self.report.get("at", "?"), who=self.report.get("by", "?")),
                               constructionscan.details(self.report)))
        root.Add(wx.StaticText(self, label=_("&Report:")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self.text = wx.TextCtrl(self, value=shown, style=wx.TE_MULTILINE | wx.TE_READONLY, size=wx.Size(520, 240))
        root.Add(self.text, 1, wx.EXPAND | wx.ALL, 12)
        form = Form(self)
        self.where = form.choice(_("Publish &as:"), [_("Gang announcement"), _("Broadcast (with a sound)"),
                                                     _("System message")], 0)
        self.where.Enable(can_act)
        enable_label(self.where, can_act)
        self.per_line = form.check(_("Put each highland on its own &line (gang announcement only)"),
                                   settings.load_app().get("construction_lines", False))
        self.per_line.Bind(wx.EVT_CHECKBOX, self.on_per_line)
        root.Add(form.grid, 0, wx.LEFT | wx.RIGHT, 12)
        if not can_act:
            root.Add(wx.StaticText(self, label=_("To scan or publish, log in to the character in the game and stop "
                                                 "its bot first.")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.buttons: dict[str, wx.Button] = {}
        for key, label, handler in (("scan", _("&Scan the constructions"), self.on_scan),
                                    ("copy", _("C&opy"), self.on_copy), ("publish", _("&Publish"), self.on_publish)):
            btn = wx.Button(self, label=label)
            btn.Bind(wx.EVT_BUTTON, handler)
            has_report = self.report is not None or key == "scan"
            btn.Enable(has_report and (can_act or key == "copy"))
            self.buttons[key] = btn
            buttons.Add(btn, 0, wx.RIGHT, 8)
        buttons.Add(wx.Button(self, wx.ID_CANCEL, _("Close")), 0)
        root.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizerAndFit(root)

    def on_scan(self, _event: wx.CommandEvent) -> None:
        self.EndModal(SCAN_CONSTRUCTIONS)

    def on_per_line(self, _event: wx.CommandEvent) -> None:
        save_file(self, settings.update_app, {"construction_lines": self.per_line.GetValue()})

    def report_text(self, announcement: bool = True) -> str:
        """The report as it would go out: a line to a place when ticked (and it is an announcement)."""
        if self.report is None:
            return ""
        if announcement and self.per_line.GetValue():
            return "\n".join(constructionscan.message_lines(self.report))
        return constructionscan.message(self.report)

    def on_copy(self, _event: wx.CommandEvent) -> None:
        if self.report is not None and not copy_text(self.report_text()):
            wx.MessageBox(_("The clipboard couldn't be opened."), _("Construction scanner"), wx.OK | wx.ICON_ERROR, self)

    def on_publish(self, _event: wx.CommandEvent) -> None:
        if self.report is None:
            return
        self.kind = PUBLISH_KINDS[self.where.GetSelection()]
        ask = _("Send this to the whole gang as: {where}?\n\n{report}", where=self.where.GetStringSelection(),
                report=self.report_text(self.kind == "announcement"))
        if wx.MessageBox(ask, _("Construction scanner"), wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) == wx.YES:
            self.EndModal(PUBLISH_REPORT)
