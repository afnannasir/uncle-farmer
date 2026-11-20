"""Keyboard Manager for the selected character."""

from __future__ import annotations

from collections import Counter

import wx

from .. import settings
from ..engine import keyboard
from ..i18n import _
from ..link import AgentError, AgentLink
from .common import Dialog, ask, buttons, error, labeled, page, tabs


class KeyboardDialog(Dialog):
    def __init__(self, parent: wx.Window | None, character: str, pid: int) -> None:
        super().__init__(parent, title=_("Keyboard Manager"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.character, self.pid = character, pid
        self.keys = keyboard.Keys.read(keyboard.client_folder(pid))
        self.apply_name = ""
        self.selected = ""
        self.draft = keyboard.Profile()
        self.controls: dict[str, wx.ListBox] = {}
        self.cfg = settings.load_profile(character)
        box = wx.BoxSizer(wx.VERTICAL)
        self.profiles = labeled(self, box, _("Saved keyboard &profiles:"), lambda w: wx.Choice(w))
        self.profiles.Bind(wx.EVT_CHOICE, self._choose)
        buttons(self, box, (_("&New profile"), self._new), (_("Capture current &keys"), self._capture),
                (_("&Delete profile"), self._delete))
        self.profile_name = labeled(self, box, _("Profile na&me:"), lambda w: wx.TextCtrl(w))
        note = wx.StaticText(self, label=_("Saved bindings replace their game slots. Slots marked Leave unchanged keep their current bindings."))
        note.Wrap(510)
        box.Add(note, 0, wx.EXPAND | wx.ALL, 12)
        notebook = tabs(self)
        for kind, title in (("skills", _("Skills")), ("profession", _("Profession skills")),
                            ("items", _("Items")), ("phrases", _("Quick phrases"))):
            panel, body = page(notebook, title)
            slots = labeled(panel, body, _("Key &bindings:"), lambda w: wx.ListBox(w, size=wx.Size(500, 235)), 1)
            slots.Bind(wx.EVT_LISTBOX_DCLICK, lambda e, k=kind: self._edit(k))
            self.controls[kind] = slots
            buttons(panel, body, (_("&Edit binding..."), lambda e, k=kind: self._edit(k)))
            if kind == "skills":
                buttons(panel, body, (_("Toggle p&reparation"), self._toggle_prepare))
        box.Add(notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        buttons(self, box, (_("&Save profile"), self._save), (_("&Apply profile"), self._apply))
        close = wx.Button(self, wx.ID_CANCEL, _("&Close"))
        box.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 12)
        self.SetSizerAndFit(box)
        self._load(next(iter(keyboard.names(self.cfg)), ""))
        self.profiles.SetFocus()

    def _game(self) -> keyboard.Capture:
        with wx.BusyCursor(), AgentLink(pid=self.pid, timeout=3) as agent:
            return keyboard.capture(agent, self.keys.folder, self.character)

    def _load(self, name: str) -> None:
        saved = self.cfg.get("keyboard_profiles", {})
        self.selected = name
        try:
            self.draft = keyboard.Profile.model_validate(saved.get(name, {}))
        except ValueError as e:
            error(self, str(e))
            self.draft = keyboard.Profile()
        names = keyboard.names(self.cfg)
        self.profiles.Set([_("New profile"), *names])
        self.profiles.SetSelection(names.index(name) + 1 if name else 0)
        self.profile_name.SetValue(name)
        self._fill()

    def _choose(self, _event: wx.Event) -> None:
        index = self.profiles.GetSelection()
        self._load(keyboard.names(self.cfg)[index - 1] if index > 0 else "")

    def _new(self, _event: wx.Event) -> None:
        self._load("")
        self.profile_name.SetFocus()

    def _capture(self, _event: wx.Event) -> None:
        try:
            self.draft = self._game().profile
        except (AgentError, OSError, ValueError) as e:
            error(self, str(e))
            return
        self._fill()
        self.profile_name.SetFocus()

    def _fill(self) -> None:
        for kind, control in self.controls.items():
            selected = control.GetSelection()
            rows: list[str] = []
            bindings = getattr(self.draft, kind)
            for slot in range(1, keyboard.SLOT_COUNTS[kind] + 1):
                binding = bindings.get(slot)
                text = binding.name if isinstance(binding, keyboard.Binding) else binding or _("Leave unchanged")
                if kind == "skills" and slot in self.draft.prepare:
                    text = _("{skill} (prepare when applied)", skill=text)
                rows.append(self.keys.label(kind, slot) + " — " + text)
            control.Set(rows)
            control.SetSelection(max(0, selected))

    def _edit(self, kind: str) -> None:
        slot = self.controls[kind].GetSelection() + 1
        if not slot:
            return
        label = self.keys.label(kind, slot)
        if kind == "phrases":
            self._edit_phrase(slot, label)
        else:
            self._edit_binding(kind, slot, label)
        self._fill()

    def _edit_phrase(self, slot: int, label: str) -> None:
        dialog = wx.TextEntryDialog(self, _("Quick phrase for {key} (blank leaves it unchanged)", key=label),
                                    _("Edit quick phrase"), self.draft.phrases.get(slot, ""))
        with dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            value = dialog.GetValue()
        try:
            if value.strip():
                self.draft.phrases[slot] = keyboard.phrase_text(value)
            else:
                self.draft.phrases.pop(slot, None)
        except ValueError as e:
            error(self, str(e))

    def _edit_binding(self, kind: str, slot: int, label: str) -> None:
        try:
            current_game = self._game()
        except (AgentError, OSError, ValueError) as e:
            error(self, str(e))
            return
        bindings: dict[int, keyboard.Binding] = getattr(self.draft, kind)
        current = bindings.get(slot)
        available = {"skills": current_game.skills, "profession": current_game.profession, "items": current_game.items}
        options = {binding.id: binding for binding in available[kind]}
        if current is not None:
            options.setdefault(current.id, current)
        choices = sorted(options.values(), key=lambda binding: (binding.name.casefold(), binding.id))
        counts = Counter(binding.name for binding in choices)
        labels = [f"{binding.name} ({binding.id})" if counts[binding.name] > 1 else binding.name for binding in choices]
        dialog = wx.SingleChoiceDialog(self, _("Binding for {key}", key=label), _("Edit binding"),
                                       [_("Leave unchanged"), *labels])
        with dialog:
            dialog.SetSelection(next((i for i, binding in enumerate(choices, 1)
                                      if current and binding.id == current.id), 0))
            if dialog.ShowModal() != wx.ID_OK:
                return
            choice = dialog.GetSelection()
        if choice:
            bindings[slot] = choices[choice - 1]
        else:
            bindings.pop(slot, None)
        if kind == "skills" and slot in self.draft.prepare and bindings.get(slot) != current:
            self.draft.prepare.remove(slot)

    def _toggle_prepare(self, _event: wx.Event) -> None:
        slot = self.controls["skills"].GetSelection() + 1
        if slot in self.draft.prepare:
            self.draft.prepare.remove(slot)
        else:
            binding = self.draft.skills.get(slot)
            if not binding or not keyboard.can_prepare(binding.name):
                error(self, _("Choose a bound self-buff or shield to prepare."))
                return
            self.draft.prepare.append(slot)
        self._fill()

    def _save(self, _event: wx.Event) -> None:
        try:
            name = keyboard.profile_name(self.profile_name.GetValue())
            cfg = settings.load_profile(self.character)
            profiles = cfg.setdefault("keyboard_profiles", {})
            if name != self.selected and name in profiles and not ask(
                    self, _("Replace keyboard profile {name}?", name=name)):
                return
            profiles[name] = self.draft.model_dump(mode="json")
            settings.save_profile(self.character, cfg)
        except (OSError, ValueError) as e:
            error(self, str(e))
            return
        self.cfg = cfg
        self._load(name)

    def _delete(self, _event: wx.Event) -> None:
        if not self.selected or not ask(self, _("Delete keyboard profile {name}?", name=self.selected)):
            return
        try:
            cfg = settings.load_profile(self.character)
            cfg["keyboard_profiles"].pop(self.selected, None)
            settings.save_profile(self.character, cfg)
        except (OSError, ValueError) as e:
            error(self, str(e))
            return
        self.cfg = cfg
        self._load("")

    def _apply(self, _event: wx.Event) -> None:
        if (not self.selected or self.profile_name.GetValue().strip() != self.selected or
                self.draft.model_dump(mode="json") != self.cfg["keyboard_profiles"].get(self.selected)):
            error(self, _("Save the keyboard profile before applying it."))
            return
        self.apply_name = self.selected
        self.EndModal(wx.ID_OK)
