"""Tools, Dungeons: the once-a-day caves (engine/dungeon.py) for one character.

Saved in the character's profile. Which caves, in what order (each once a game day); what to have
in the bag before setting out - the pet's blue potions and Rice Rolls, our own blues and reds - and
where to get what is short; how to go round the cave; and where the run ends, as a plan's end.
Check only says what is short and which caves are left today; Start tops up, goes in, kills all it
can until the game moves it out, and ends at the end place.
"""

from __future__ import annotations

from typing import Any

import wx

from .. import atlas, settings, travel
from ..engine import dungeon
from ..i18n import N_, _
from .common import Dialog, Reader, buttons, standing, with_code, enable_label, error, group, info, labeled, page, save_file, tabs, modal
from .crafting import CHECK, START
from .plan import END_CHOICES, NOT_SEEN

SOURCE_LABELS = (("both", N_("From the storehouse, then the shop")), ("storage", N_("From the storehouse only")),
                 ("buy", N_("From the shop only")))
WHEN_SHORT = ("stop", "go")
PATROL_LABELS = (("quick", N_("Quick loop (the whole cave, lanes further apart)")),
                 ("full", N_("Full loop (every lane, a longer lap)")),
                 ("short", N_("Short loop (the middle of the cave)")))


class DungeonDialog(Dialog):
    def __init__(self, parent: wx.Window, character: str, game: Reader) -> None:
        super().__init__(parent, title=_("{tool} - {name}", tool=_("Dungeons"), name=character),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.character = character
        self.game = game
        self.p = settings.load_profile(character)
        c = self.cfg = dungeon.config(self.p)
        self.order = [x for x in c["caves"] if x in dungeon.CAVES] + [x for x in dungeon.CAVES if x not in c["caves"]]
        self.ticked = set(x for x in c["caves"] if x in dungeon.CAVES)
        self.done = dungeon.done_today(character)
        root = wx.BoxSizer(wx.VERTICAL)
        nb = tabs(self)

        panel, box = page(nb, _("Caves"))
        f = group(panel, box, _("Caves, each once a game day, in this order"), 1)
        self.caves = labeled(f.parent, f.box, _("&Caves (tick the ones to go to):"),
                             lambda w: wx.CheckListBox(w, size=wx.Size(-1, 90)), 1)
        buttons(f.parent, f.box, (_("Move &up"), lambda e: self.move(-1)), (_("Move &down"), lambda e: self.move(1)))
        f = group(panel, box, _("Inside"))
        self.patrol = f.choice(_("&Going round:"), [_(t) for _k, t in PATROL_LABELS],
                               [k for k, _t in PATROL_LABELS].index(c["patrol"]) if c["patrol"] in dungeon.PATROLS else 0)
        self.pack = f.number(_("Use area &skills on this many monsters close together:"), c["pack"], 2, 6)
        known: list[dict[str, Any]] = self.p.get("pets") or []
        self.pet_names = [str(x["name"]) for x in known if x.get("name")]
        pet = str(c.get("pet") or "")
        if pet and pet != dungeon.NO_PET and pet not in self.pet_names:
            self.pet_names.append(pet)
        self.pet = f.choice(_("&Pet:"), [_("The pets ticked in Character, Settings (else the one out)"), _("No pet")]
                            + self.pet_names,
                            1 if pet == dungeon.NO_PET else self.pet_names.index(pet) + 2 if pet else 0)
        box.Add(wx.StaticText(panel, label=_(
            "Inside, the bot never stops: it goes round the whole cave, fights whatever is in reach, "
            "drinks instead of resting, keeps the pet out and fed, and stays until the game moves it "
            "out after 15 minutes. Monsters come back about 30 seconds after they die, anywhere in "
            "the cave.")), 0, wx.TOP, 10)
        self.fill_caves(0)

        panel, box = page(nb, _("Supplies"))
        f = group(panel, box, _("Before setting out, have at least (0: don't look)"), 1)
        self.rows: dict[str, tuple[wx.Choice, wx.SpinCtrl, list[str]]] = {}
        auto = _("What is on the hotkey (else the strongest in the bag)")
        for kind in dungeon.SUPPLIES:
            row = c["supplies"][kind]
            names = dungeon.known_items(kind)
            item: str = row.get("item") or ""
            if item and item not in names:
                names.append(item)
            label = _(dungeon.SUPPLY_NAMES[kind])
            first = _("Pet Rice Roll, or the pet food in Character, Settings") if kind == "pet_food" else auto
            choice = f.choice(_("{what}:", what=label), [first] + names, names.index(item) + 1 if item else 0)
            count = f.number(_("{what}, at least:", what=label), int(row.get("min") or 0), 0, 9999)
            self.rows[kind] = (choice, count, names)
        f = group(panel, box, _("What is short"))
        sources = [k for k, _t in SOURCE_LABELS]
        self.source = f.choice(_("&Get it:"), [_(t) for _k, t in SOURCE_LABELS],
                               sources.index(c["source"]) if c["source"] in sources else 0)
        self.short = f.choice(_("Still short &after that:"), [_("Don't go"), _("Go anyway")],
                              WHEN_SHORT.index(c["when_short"]) if c["when_short"] in WHEN_SHORT else 0)
        box.Add(wx.StaticText(panel, label=_(
            "The pet's rows count only with a pet ticked in Character, Settings, and the blue potions "
            "only for a character that uses MP. Between two caves the bot tops up again.")), 0, wx.TOP, 10)

        panel, box = page(nb, _("Where to end"))
        f = group(panel, box, _("When the last cave is done"))
        end = c["end"]
        self.end_how = f.choice(_("&End:"), [_("Back where the run started"), _("At a place I choose"),
                                             _("Home, with the hearthstone"), _("On the nearest map without monsters"),
                                             _("Where it is")],
                                END_CHOICES.index(end.get("how")) if end.get("how") in END_CHOICES else 0)
        self.all_maps = atlas.all_maps()
        ids = [m for m, _l in self.all_maps]
        self.end_map = f.choice(_("&Map:"), [lab for _m, lab in self.all_maps],
                                ids.index(end["map"]) if end.get("map") in ids else 0)
        cell = end.get("cell") or [0, 0]
        self.end_x = f.number(_("X:"), cell[0], 0, 2000)
        self.end_y = f.number(_("Y:"), cell[1], 0, 2000)
        self.end_buttons = buttons(f.parent, f.box, (_("Use where I &stand now"), self.on_end_here),
                                   (_("A city s&quare..."), self.on_end_square))
        self.end_how.Bind(wx.EVT_CHOICE, lambda e: self.sync_end())

        root.Add(nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, code in ((_("Save"), wx.ID_OK), (_("&Check only"), CHECK), (_("Sta&rt"), START)):
            btn = wx.Button(self, label=label)
            btn.Bind(wx.EVT_BUTTON, with_code(self.finish, code))
            row.Add(btn, 0, wx.RIGHT, 8)
        row.Add(wx.Button(self, wx.ID_CANCEL, _("Cancel")), 0)
        root.Add(row, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizerAndFit(root)
        self.SetMinSize(wx.Size(640, 520))
        self.sync_end()

    # ------------------------------------------------------------ caves
    def cave_line(self, cave: str) -> str:
        v = dungeon.CAVES[cave]
        today = _("done today") if cave in self.done else _("not yet today")
        return _("{cave} - level {n} - {where} - {today}", cave=_(cave), n=v["level"],
                 where=dungeon.entrance_text(cave), today=today)

    def fill_caves(self, select: int | None = None) -> None:
        self.keep_ticks()
        self.caves.Set([self.cave_line(x) for x in self.order])
        for i, x in enumerate(self.order):
            self.caves.Check(i, x in self.ticked)
        if select is not None and self.order:
            self.caves.SetSelection(max(0, min(select, len(self.order) - 1)))

    def keep_ticks(self) -> None:
        if self.caves.GetCount() == len(self.order):
            self.ticked = {x for i, x in enumerate(self.order) if self.caves.IsChecked(i)}

    def move(self, step: int) -> None:
        i = self.caves.GetSelection()
        j = i + step
        if i < 0 or not 0 <= j < len(self.order):
            return
        self.keep_ticks()
        self.order[i], self.order[j] = self.order[j], self.order[i]
        self.fill_caves(j)

    # ------------------------------------------------------------ the end
    def sync_end(self) -> None:
        on = END_CHOICES[max(0, self.end_how.GetSelection())] == "place"
        for c in (self.end_map, self.end_x, self.end_y):
            c.Enable(on)
            enable_label(c, on)
        for b in self.end_buttons:
            b.Enable(on)

    def on_end_here(self, _evt: wx.Event) -> None:
        here = standing(self.game())
        if here is None:
            error(self, NOT_SEEN)
            return
        mid, x, y = here
        ids = [m for m, _l in self.all_maps]
        if mid not in ids:
            error(self, _("This map can't be an end place. Choose one on the open world, or another end."))
            return
        self.end_map.SetSelection(ids.index(mid))
        self.end_x.SetValue(int(x))
        self.end_y.SetValue(int(y))
        info(self, _("The end place is now where you stand: {map} ({x},{y}).", map=atlas.label(mid),
                     x=int(x), y=int(y)))

    def on_end_square(self, _evt: wx.Event) -> None:
        names = sorted(travel.SQUARES)
        with modal(wx.SingleChoiceDialog(self, _("Choose a city square:"), _("City squares"),
                                   [n[:1].upper() + n[1:] for n in names])) as d:
            if d.ShowModal() != wx.ID_OK:
                return
            mid, x, y = travel.SQUARES[names[d.GetSelection()]]
        ids = [m for m, _l in self.all_maps]
        if mid in ids:
            self.end_map.SetSelection(ids.index(mid))
        self.end_x.SetValue(x)
        self.end_y.SetValue(y)

    # ------------------------------------------------------------ saving
    def finish(self, code: int) -> None:
        self.keep_ticks()
        caves = [x for x in self.order if x in self.ticked]
        if code != wx.ID_OK and not caves:
            error(self, _("Tick a cave to go to."))
            self.caves.SetFocus()
            return
        supplies: dict[str, dict[str, Any]] = {}
        for kind, (choice, count, names) in self.rows.items():
            i = choice.GetSelection()
            supplies[kind] = {"item": names[i - 1] if i > 0 else "", "min": count.GetValue()}
        i = self.pet.GetSelection()
        pet = dungeon.NO_PET if i == 1 else self.pet_names[i - 2] if i >= 2 else ""
        how = END_CHOICES[max(0, self.end_how.GetSelection())]
        end: dict[str, Any] = {"how": how}
        if how == "place":
            end["map"] = self.all_maps[self.end_map.GetSelection()][0]
            x, y = self.end_x.GetValue(), self.end_y.GetValue()
            end["cell"] = [x, y] if x or y else None
        self.cfg.update(caves=caves, supplies=supplies, source=SOURCE_LABELS[self.source.GetSelection()][0],
                        when_short=WHEN_SHORT[self.short.GetSelection()],
                        patrol=PATROL_LABELS[self.patrol.GetSelection()][0], pack=self.pack.GetValue(), pet=pet,
                        end=end)
        self.p["dungeon"] = self.cfg
        if save_file(self, settings.save_profile, self.character, self.p):
            self.EndModal(code)
