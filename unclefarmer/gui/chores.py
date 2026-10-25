"""Tools, Daily chores: what the bot does once a game day for one character (engine/chores.py).

Saved in the character's profile. Check only walks to each NPC and reads the menus without
collecting, spending or entering anything; Start does the chores that aren't done today.
"""

import wx

from .. import settings
from ..engine import chores
from ..i18n import _
from .common import Form, group, page, save_file, tabs
from .crafting import CHECK, START

DONATIONS = ("off", "10000", "20000", "diamonds")


def donation_names():
    return [_("Don't donate"), _("10,000 arnebia (1,500 gang contribution)"),
            _("20,000 arnebia (3,000 gang contribution)"), _("20 diamonds (5,000 gang contribution)")]


def assistant_names():
    return [_("Snowflake prince"), _("Snowflake princess"), _("Snowball prince"), _("Snowball princess"),
            _("Snow mountain prince"), _("Snow mountain princess"), _("Glacier prince"), _("Glacier princess"),
            _("Ice prince"), _("Ice princess")]


class ChoresDialog(wx.Dialog):
    def __init__(self, parent, character):
        super().__init__(parent, title=_("{tool} - {name}", tool=_("Daily chores"), name=character),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.character = character
        self.p = settings.load_profile(character)
        c = self.cfg = chores.config(self.p)
        root = wx.BoxSizer(wx.VERTICAL)
        nb = tabs(self)

        panel, box = page(nb, _("City"))
        f = group(panel, box, _("Every day"))
        self.bank = f.check(_("Collect the &bank's deposit income and Value Product incomes"), c["bank"])
        self.bounty = f.check(_("Collect &hunting bounties from a city officer"), c["bounty"])
        f = group(panel, box, _("Pet rebirth (free ones only, never for diamonds)"))
        self.rebirth = f.check(_("Use the day's free &rebirths on one pet"), c["pet_rebirth"])
        pets = list((self.p.get("scan") or {}).get("pets") or [])
        if c["pet"] and c["pet"] not in pets:
            pets.append(c["pet"])
        self.pet = f.add(_("&Pet:"), lambda w: wx.ComboBox(w, value=c["pet"], choices=pets))
        self.rebirths = f.number(_("Free rebirths a day (the game gives 2):"), c["rebirths"], 1, 2)

        panel, box = page(nb, _("Gang"))
        f = group(panel, box, _("Inner hall"))
        self.donation = f.choice(_("&Donation, once a day:"), donation_names(),
                                 DONATIONS.index(c["donation"]) if c["donation"] in DONATIONS else 0)
        self.construction = f.number(_("&Construction builds a day (10,000 arnebia, 2,500 gang contribution each):"),
                                     c["construction"], 0, 50)
        self.highland = f.check(_("Build in the gang's &highland too (the outer and inner halls always)"),
                                c["build_highland"])
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
            btn.Bind(wx.EVT_BUTTON, lambda e, c=code: self.done(c))
            buttons.Add(btn, 0, wx.RIGHT, 8)
        cancel = wx.Button(self, wx.ID_CANCEL, _("Cancel"))
        buttons.Add(cancel, 0)
        root.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizerAndFit(root)
        self.SetMinSize(wx.Size(620, 520))

    def done(self, code):
        pet = self.pet.GetValue().strip()
        if self.rebirth.GetValue() and not pet:
            wx.MessageBox(_("Choose the pet to rebirth, or untick pet rebirth."), _("Pet rebirth"),
                          wx.OK | wx.ICON_INFORMATION, self)
            self.pet.SetFocus()
            return
        self.cfg.update(bank=self.bank.GetValue(), bounty=self.bounty.GetValue(),
                        pet_rebirth=self.rebirth.GetValue(), pet=pet, rebirths=self.rebirths.GetValue(),
                        donation=DONATIONS[self.donation.GetSelection()],
                        construction=self.construction.GetValue(), build_highland=self.highland.GetValue(),
                        secret_room=self.room_on.GetValue(), room=self.room.GetValue().strip(),
                        assistant=self.assistant.GetSelection() + 1, practice_minutes=self.minutes.GetValue(),
                        share_with=self.share_with.GetValue().strip(), share_anyone=self.share_anyone.GetValue(),
                        share_trips=self.trips.GetValue(), trip_lead=self.lead.GetValue(),
                        before_farming=self.before.GetValue())
        for old in ("room_minutes", "share"):       # settings of the first version
            self.cfg.pop(old, None)
        self.p["chores"] = self.cfg
        self.p["gem_keep_arnebia"] = self.keep_arnebia.GetValue()
        if save_file(self, settings.save_profile, self.character, self.p):
            self.EndModal(code)
