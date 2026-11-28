"""Tools, Daily chores: what the bot does once a game day for one character (engine/chores.py).

Saved in the character's profile. Check only walks to each NPC and reads the menus without
collecting, spending or entering anything; Start does the chores that aren't done today.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any, Generic, TypeVar

import wx

from .. import settings
from ..engine import chores, constructionscan, turretfire
from ..i18n import _
from .common import (Dialog, buttons, copy_text, enable_label, error, info, labeled, modal, opens, popup_actions,
                     ok_cancel, with_code, Form, group, page, save_file, tabs)
from .crafting import CHECK, START

T = TypeVar("T")

DONATIONS = ("off", "10000", "20000", "diamonds")
SCAN_CONSTRUCTIONS, PUBLISH_REPORT = wx.ID_HIGHEST + 111, wx.ID_HIGHEST + 112
PUBLISH_KINDS = ("announcement", "broadcast", "system")


def donation_names():
    return [_("Don't donate"), _("10,000 arnebia (1,500 gang contribution)"),
            _("20,000 arnebia (3,000 gang contribution)"), _("20 diamonds (5,000 gang contribution)")]


def aim_names() -> list[str]:
    return [_("Our gang's area"), _("Other gangs' highlands")]


def area_names() -> list[str]:
    return [_("Main mansion"), _("Mining area"), _("Lake"), _("Herbal garden")]


def part_names() -> list[str]:
    return [_("Hit the turret"), _("Hit the wall"), _("Hit the highest")]


Action = tuple[str, Callable[[wx.Event], None]]


class EditList(Generic[T]):
    """A labelled list in order, with an Add button (and extra ones); the list's context menu adds,
    moves, removes (and does the extra actions), and Delete removes the selected entry. ask() asks
    for a new entry (None: nothing added); show() is its line in the list."""

    def __init__(self, form: Form, label: str, add_label: str, items: list[T],
                 ask: Callable[[], T | None], show: Callable[[T], str], extra: Sequence[Action] = (),
                 menu_only: Sequence[Action] = ()) -> None:
        self.items, self.ask, self.show = list(items), ask, show
        self.extra, self.menu_only = list(extra), list(menu_only)
        self.changed: Callable[[], None] = lambda: None      # after every change of the list
        self.list = labeled(form.parent, form.box, label, lambda w: wx.ListBox(w, size=wx.Size(-1, 80)))
        self.label = label_before(self.list)
        self.buttons = buttons(form.parent, form.box, (opens(add_label), self.on_add),
                               *((opens(text), handler) for text, handler in self.extra))
        self.list.Bind(wx.EVT_CONTEXT_MENU, self.on_menu)
        self.list.Bind(wx.EVT_KEY_DOWN, self.on_key)
        self.fill(0)

    def fill(self, select: int | None = None) -> None:
        self.list.Set([self.show(x) for x in self.items])
        if self.items and select is not None:
            self.list.SetSelection(max(0, min(select, len(self.items) - 1)))
        self.changed()

    def refill(self) -> None:
        """Show the entries again, keeping the selection (another list they depend on changed)."""
        selected = self.list.GetSelection()
        self.list.Set([self.show(x) for x in self.items])
        if self.items and selected != wx.NOT_FOUND:
            self.list.SetSelection(min(selected, len(self.items) - 1))

    def on_add(self, _evt: wx.Event) -> None:
        item = self.ask()
        if item is None:
            return
        if item in self.items:
            info(self.list, _("{entry} is in the list already.", entry=self.show(item)))
            return
        self.items.append(item)
        self.fill(len(self.items) - 1)
        self.list.SetFocus()                      # on the new entry, not back on the Add button

    def selected(self) -> int:
        """The selected entry's index, or -1."""
        i = self.list.GetSelection()
        return i if 0 <= i < len(self.items) else -1

    def move(self, step: int) -> None:
        i = self.selected()
        j = i + step
        if i < 0 or not 0 <= j < len(self.items):
            return
        self.items[i], self.items[j] = self.items[j], self.items[i]
        self.fill(j)

    def on_remove(self, _evt: wx.Event) -> None:
        i = self.list.GetSelection()
        if 0 <= i < len(self.items):
            del self.items[i]
            self.fill(i)

    def on_key(self, event: wx.KeyEvent) -> None:
        if event.GetKeyCode() == wx.WXK_DELETE:
            self.on_remove(event)
        else:
            event.Skip()

    def on_menu(self, event: wx.ContextMenuEvent) -> None:
        pos = event.GetPosition()
        if pos != wx.DefaultPosition:
            index = self.list.HitTest(self.list.ScreenToClient(pos))
            if index != wx.NOT_FOUND:
                self.list.SetSelection(index)
        i = self.selected()
        popup_actions(self.list, [
            (opens(_("&Add")), self.on_add, True),
            (_("Move u&p"), lambda _e: self.move(-1), i > 0),
            (_("Move do&wn"), lambda _e: self.move(1), 0 <= i < len(self.items) - 1),
            *((opens(text), handler, i >= 0) for text, handler in self.extra),
            *((text, handler, i >= 0) for text, handler in self.menu_only),
            (_("&Remove"), self.on_remove, i >= 0)])

    def enable(self, on: bool) -> None:
        for w in (self.list, self.label, *self.buttons):
            if w is not None:
                w.Enable(on)


class TargetDialog(Dialog):
    """A highland to fire at: the gang's name, the highland's number, and whether to fire at it when
    another gang occupies it."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, title=_("Add a highland"))
        self.target: turretfire.Target | None = None
        root = wx.BoxSizer(wx.VERTICAL)
        f = Form(self)
        self.gang = f.text(_("&Gang's name, exactly as the turret's list of gangs shows it, capitals too:"),
                           size=wx.Size(240, -1))
        self.number = f.text(_("&Highland number:"))
        self.occupied = f.check(_("&Fire at it even when another gang occupies it"), False)
        root.Add(f.grid, 0, wx.EXPAND | wx.ALL, 12)
        ok_cancel(self, root, _("&Add"))
        self.SetSizerAndFit(root)
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self.gang.SetFocus()                      # the first field, not the Add button

    def on_ok(self, _evt: wx.CommandEvent) -> None:
        gang = " ".join(self.gang.GetValue().split())
        if not gang:
            error(self, _("Type the gang's name."), _("Add a highland"))
            self.gang.SetFocus()
            return
        n = turretfire.highland_number(self.number.GetValue())
        if n is None:
            error(self, _("\"{typed}\" isn't a highland number.", typed=self.number.GetValue().strip()),
                  _("Add a highland"))
            self.number.SetFocus()
            return
        self.target = turretfire.Target(gang, n, occupied=self.occupied.GetValue())
        self.EndModal(wx.ID_OK)


def label_before(ctrl: wx.Window) -> wx.Window | None:
    """The static text labeled() put just before ctrl (its label), to enable with it."""
    siblings = list(ctrl.GetParent().GetChildren())
    i = siblings.index(ctrl)
    return siblings[i - 1] if i > 0 and isinstance(siblings[i - 1], wx.StaticText) else None


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

        self.turret_page(nb, c)

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
        lines += list(turretfire.fired_today(character).values())
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

    def turret_page(self, nb: wx.Notebook, c: dict[str, Any]) -> None:
        """The Turret firing page: the daily shot of each turret this character keeps."""
        s = turretfire.settings_of(c)
        panel, box = page(nb, _("Turret firing"))
        f = group(panel, box, _("Your turrets"))
        self.fire = f.check(_("&Fire each turret once a game day"), c["fire"])
        self.turrets = EditList(f, _("Highlands where this character is the &turret administrator:"), _("&Add a turret"),
                                s.turrets, self.ask_turret, lambda n: turretfire.turret_line(n, self.pairs()))
        f = group(panel, box, _("What to fire at"))
        self.aim = f.choice(_("A&im at:"), aim_names(), turretfire.AIMS.index(s.aim))
        self.aim.Bind(wx.EVT_CHOICE, lambda _e: self.sync_aim())
        f = group(panel, box, _("Firing at our gang's area (a shot kills the player it hits)"))
        self.area = f.choice(_("Ar&ea:"), area_names(), turretfire.AREAS.index(s.area))
        self.spare = EditList(f, _("Players never to &shoot (allies):"), _("Add a pla&yer"),
                              s.spare, self.ask_player, str)
        f = group(panel, box, _("Firing at other gangs' highlands"))
        self.part = f.choice(_("&Part to hit:"), part_names(), turretfire.PARTS.index(s.part))
        self.limit = f.number(_("Don't fire at a highland at or be&low this percent:"), s.limit, 1, 100)
        self.targets = EditList(f, _("Tar&gets, tried in order:"),
                                _("A&dd a target"), s.targets, self.ask_target, turretfire.target_line,
                                [(_("Pair with a t&urret"), self.on_pair)],
                                [(_("Fire even when &occupied (on or off)"), lambda _e: self.toggle(self.targets))])
        self.targets.changed = self.turrets.refill        # the turret list shows each turret's pairs
        self.turrets.refill()
        self.fallback = EditList(f, _("Ra&ndom gangs, when every target is at the limit (no limit):"),
                                 _("Add a random gang's &highland"), s.fallback, self.ask_target, turretfire.target_line,
                                 menu_only=[(_("Fire even when &occupied (on or off)"),
                                             lambda _e: self.toggle(self.fallback))])
        box.Add(wx.StaticText(panel, label=_(
            "A list's context menu moves the selected entry up or down, or removes it; Delete removes it too. "
            "A turret fires at the targets paired with it, then at the targets with no pair, then at the "
            "random gangs. A highland another gang occupies is left alone, unless its entry says to fire even "
            "when occupied (the context menu turns that on or off).")), 0, wx.TOP, 10)
        self.sync_aim()

    def pairs(self) -> list[turretfire.Target]:
        """The targets, as far as the page has made them (the turret list is made before them)."""
        targets: EditList[turretfire.Target] | None = getattr(self, "targets", None)
        return targets.items if targets is not None else []

    def sync_aim(self) -> None:
        own = self.aim.GetSelection() == 0
        for ctrl in (self.area,):
            ctrl.Enable(own)
            enable_label(ctrl, own)
        for ctrl in (self.part, self.limit):
            ctrl.Enable(not own)
            enable_label(ctrl, not own)
        self.spare.enable(own)
        self.targets.enable(not own)
        self.fallback.enable(not own)

    def ask_text(self, prompt: str, title: str) -> str | None:
        with modal(wx.TextEntryDialog(self, prompt, title)) as d:
            if d.ShowModal() != wx.ID_OK:
                return None
            return " ".join(d.GetValue().split()) or None

    def ask_number(self, prompt: str, title: str) -> int | None:
        text = self.ask_text(prompt, title)
        if text is None:
            return None
        n = turretfire.highland_number(text)
        if n is None:
            error(self, _("\"{typed}\" isn't a highland number.", typed=text), title)
        return n

    def ask_turret(self) -> int | None:
        return self.ask_number(_("The number of a highland whose turret this character keeps:"), _("Add a turret"))

    def ask_target(self) -> turretfire.Target | None:
        with TargetDialog(self) as d:
            return d.target if d.ShowModal() == wx.ID_OK else None

    @staticmethod
    def toggle(entries: EditList[turretfire.Target]) -> None:
        """Turn "fire even when occupied" on or off for the selected entry."""
        i = entries.selected()
        if i < 0:
            return
        t = entries.items[i]
        entries.items[i] = replace(t, occupied=not t.occupied)
        entries.fill(i)

    def on_pair(self, _evt: wx.Event) -> None:
        """Pair the selected target with one of the turrets (or none); a turret can have several."""
        i = self.targets.selected()
        if i < 0:
            return
        turrets = list(self.turrets.items)
        if not turrets:
            error(self, _("Add a turret first."), _("Pair with a turret"))
            return
        choices = [_("No turret")] + [_("Turret {n}", n=n) for n in turrets]
        now = self.targets.items[i].turret
        with modal(wx.SingleChoiceDialog(self, _("Which turret fires first at {entry}?",
                                                 entry=turretfire.entry_label(self.targets.items[i])),
                                         _("Pair with a turret"), choices)) as d:
            d.SetSelection(turrets.index(now) + 1 if now in turrets else 0)
            if d.ShowModal() != wx.ID_OK:
                return
            k = d.GetSelection()
        self.targets.items = turretfire.pair(self.targets.items, i, turrets[k - 1] if k > 0 else None)
        self.targets.fill(i)
        self.targets.list.SetFocus()

    def ask_player(self) -> str | None:
        return self.ask_text(_("The player's name, as the game writes it:"), _("Never shoot"))

    def fire_settings(self) -> dict[str, Any] | None:
        """The page's settings, or None (said) when firing is ticked without what it needs."""
        aim = turretfire.AIMS[self.aim.GetSelection()]
        if self.fire.GetValue() and not self.turrets.items:
            error(self, _("Add a turret this character keeps, or untick firing."), _("Turret firing"))
            return None
        if self.fire.GetValue() and aim == "other" and not (self.targets.items or self.fallback.items):
            error(self, _("Add a highland to fire at, or aim at our gang's area."), _("Turret firing"))
            return None

        def saved(entries: list[turretfire.Target]) -> list[dict[str, Any]]:
            """Pairs with a turret no longer listed are dropped."""
            return turretfire.saved_targets(turretfire.targets(turretfire.saved_targets(entries), self.turrets.items))
        return {"fire": self.fire.GetValue(), "fire_turrets": list(self.turrets.items), "fire_aim": aim,
                "fire_area": turretfire.AREAS[self.area.GetSelection()],
                "fire_part": turretfire.PARTS[self.part.GetSelection()], "fire_limit": self.limit.GetValue(),
                "fire_targets": saved(self.targets.items), "fire_fallback": saved(self.fallback.items),
                "fire_spare": list(self.spare.items)}

    def done(self, code: int) -> None:
        fire = self.fire_settings()
        if fire is None:
            return
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
                        before_farming=self.before.GetValue(), **fire)
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
