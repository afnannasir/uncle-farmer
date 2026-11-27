"""Keyboard Manager for the selected character."""

from __future__ import annotations

from collections import Counter
from typing import Any

import wx

from .. import settings
from ..engine import keyboard
from ..i18n import _
from ..link import AgentError, AgentLink
from .common import Dialog, Form, ask, buttons, error, labeled, page, tabs


def binding_text(binding: keyboard.Binding) -> str:
    """A number key's binding as the lists show it: a fusion beast or pet says so."""
    if binding.kind == "beast":
        return _("{name} (fusion beast)", name=binding.name)
    if binding.kind == "pet":
        return _("{name} (pet)", name=binding.name)
    return binding.name


class KeyboardDialog(Dialog):
    def __init__(self, parent: wx.Window | None, character: str, pid: int) -> None:
        super().__init__(parent, title=_("Keyboard Manager"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.character, self.pid = character, pid
        self.keys = keyboard.Keys.read(keyboard.client_folder(pid))
        self.apply_name = ""
        self.selected = ""
        self.selected_scope = "character"
        self.entries: list[tuple[str, str]] = []
        self.draft = keyboard.Profile()
        self.controls: dict[str, wx.ListBox] = {}
        self.cfg = settings.load_profile(character)
        self.cls = keyboard.class_of(self.cfg)
        box = wx.BoxSizer(wx.VERTICAL)
        notebook = tabs(self)
        for kind, title in (("skills", _("Skills")), ("profession", _("Profession skills")),
                            ("items", _("Number keys")), ("phrases", _("Quick phrases"))):
            panel, body = page(notebook, title)
            slots = labeled(panel, body, _("Key &bindings:"), lambda w: wx.ListBox(w, size=wx.Size(500, 235)), 1)
            slots.Bind(wx.EVT_LISTBOX_DCLICK, lambda e, k=kind: self._edit(k))
            self.controls[kind] = slots
            buttons(panel, body, (_("&Edit binding..."), lambda e, k=kind: self._edit(k)))
            if kind == "skills":
                buttons(panel, body, (_("Toggle p&reparation"), self._toggle_prepare))
        box.Add(notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        profile = wx.Panel(self)
        profile_box = wx.BoxSizer(wx.VERTICAL)
        fields = Form(profile)
        self.profiles = fields.choice(_("Saved keyboard &profiles:"), [])
        self.profiles.Bind(wx.EVT_CHOICE, self._choose)
        self.profile_name = fields.text(_("Profile na&me:"))
        self.scope = fields.choice(_("S&hared with:"), self._scope_labels())
        profile_box.Add(fields.grid, 0, wx.EXPAND)
        buttons(profile, profile_box, (_("&New profile"), self._new), (_("Capture current &keys"), self._capture),
                (_("&Delete profile"), self._delete))
        note = wx.TextCtrl(profile, value=_(
            "Saved bindings replace their game slots. Slots marked Leave unchanged keep their current bindings. "
            "A profile shared with the class or with every character is in each of their Keyboard Managers; "
            "applying it puts on what the character has and names what it couldn't."),
                           style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP,
                           size=self.FromDIP(wx.Size(-1, 62)))
        note.SetName(_("Keyboard profile information"))
        profile_box.Add(note, 0, wx.EXPAND | wx.TOP, 8)
        profile.SetSizer(profile_box)
        box.Add(profile, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)

        footer = wx.BoxSizer(wx.HORIZONTAL)
        for label, action in ((_("&Save profile"), self._save), (_("&Apply profile"), self._apply)):
            button = wx.Button(self, label=label)
            button.Bind(wx.EVT_BUTTON, action)
            footer.Add(button, 0, wx.RIGHT, 8)
        footer.Add(wx.Button(self, wx.ID_CANCEL, _("&Close")))
        box.Add(footer, 0, wx.ALIGN_RIGHT | wx.ALL, 12)
        self.SetSizerAndFit(box)
        first = next(iter(keyboard.layouts(self.cfg, self.cls)), ("character", ""))
        self._load(*first)
        notebook.SetFocus()

    def _scope_labels(self) -> list[str]:
        return [_("This character only"),
                _("Every {cls} character", cls=self.cls) if self.cls else _("Every character of its class"),
                _("Every character")]

    def _game(self) -> keyboard.Capture:
        with wx.BusyCursor(), AgentLink(pid=self.pid, timeout=3) as agent:
            return keyboard.capture(agent, self.keys.folder, self.character)

    def _saved(self, scope: str, name: str) -> dict[str, Any] | None:
        found = keyboard.find(self.cfg, name, self.cls) if name else None
        return found[1] if found and found[0] == scope else None

    def _entry_label(self, scope: str, name: str) -> str:
        if scope == "class":
            return _("{name} (shared with every {cls})", name=name, cls=self.cls)
        if scope == "all":
            return _("{name} (shared with every character)", name=name)
        return name

    def _load(self, scope: str, name: str) -> None:
        self.selected, self.selected_scope = name, scope
        try:
            self.draft = keyboard.Profile.model_validate(self._saved(scope, name) or {})
        except ValueError as e:
            error(self, str(e))
            self.draft = keyboard.Profile()
        self._list_profiles()
        self.profile_name.SetValue(name)
        self.scope.SetSelection(keyboard.SCOPES.index(scope))
        self._fill()

    def _list_profiles(self) -> None:
        entry = (self.selected_scope, self.selected)
        self.entries = keyboard.layouts(self.cfg, self.cls)
        self.profiles.Set([_("New profile"), *(self._entry_label(*e) for e in self.entries)])
        self.profiles.SetSelection(self.entries.index(entry) + 1 if entry in self.entries else 0)

    def _choose(self, _event: wx.Event) -> None:
        index = self.profiles.GetSelection()
        if index > 0:
            self._load(*self.entries[index - 1])
        else:
            self._load("character", "")

    def _new(self, _event: wx.Event) -> None:
        self._load("character", "")
        self.profile_name.SetFocus()

    def _capture(self, _event: wx.Event) -> None:
        try:
            captured = self._game()
        except (AgentError, OSError, ValueError) as e:
            error(self, str(e))
            return
        self.draft = captured.profile
        if captured.cls and captured.cls != self.cls:
            self.cls = captured.cls
            chosen = self.scope.GetSelection()
            self.scope.Set(self._scope_labels())
            self.scope.SetSelection(chosen)
            self._list_profiles()           # that class's shared profiles show now
        self._fill()
        self.profile_name.SetFocus()

    def _fill(self) -> None:
        for kind, control in self.controls.items():
            selected = control.GetSelection()
            rows: list[str] = []
            bindings = getattr(self.draft, kind)
            for slot in range(1, keyboard.SLOT_COUNTS[kind] + 1):
                binding = bindings.get(slot)
                text = binding_text(binding) if isinstance(binding, keyboard.Binding) else binding or _("Leave unchanged")
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
        options = {(binding.kind, binding.id): binding for binding in available[kind]}
        if current is not None:
            options.setdefault((current.kind, current.id), current)
        choices = sorted(options.values(), key=lambda binding: (binding_text(binding).casefold(), binding.id))
        texts = [binding_text(binding) for binding in choices]
        counts = Counter(texts)
        labels = [f"{text} ({binding.id})" if counts[text] > 1 else text for text, binding in zip(texts, choices)]
        dialog = wx.SingleChoiceDialog(self, _("Binding for {key}", key=label), _("Edit binding"),
                                       [_("Leave unchanged"), *labels])
        with dialog:
            dialog.SetSelection(next((i for i, binding in enumerate(choices, 1)
                                      if current and (binding.kind, binding.id) == (current.kind, current.id)), 0))
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
        scope = keyboard.SCOPES[max(0, self.scope.GetSelection())]
        try:
            name = keyboard.profile_name(self.profile_name.GetValue())
            if scope == "class" and not self.cls:
                raise ValueError(_("The bot doesn't know this character's class yet. Capture its keys first."))
            cfg = settings.load_profile(self.character)
            here = keyboard.SCOPES.index(scope)
            moved = name == self.selected and scope != self.selected_scope
            # Asked first, naming who has it: a profile of that name in the scope saved to (hidden or
            # not), and one closer to this character that would hide the new one (so it goes).
            replaced = [(scope, name)] if keyboard.saved_in(cfg, scope, name, self.cls) \
                and (scope, name) != (self.selected_scope, self.selected) else []
            hiding = [(closer, name) for closer in keyboard.SCOPES[:here]
                      if keyboard.saved_in(cfg, closer, name, self.cls)
                      and not (moved and closer == self.selected_scope)]
            asked = replaced + hiding
            if asked and not ask(self, _("Replace keyboard profile {name}?",
                                         name=", ".join(self._entry_label(*entry) for entry in asked))):
                return
        except (OSError, ValueError) as e:
            error(self, str(e))
            return
        try:
            keyboard.store(self.character, scope, name, self.draft.model_dump(mode="json"), self.cls)
            for old_scope, old_name in hiding + ([(self.selected_scope, name)] if moved else []):
                keyboard.store(self.character, old_scope, old_name, None, self.cls)
        except (OSError, ValueError) as e:
            error(self, str(e))
        finally:
            self.cfg = settings.load_profile(self.character)
            self._load(scope, name)

    def _delete(self, _event: wx.Event) -> None:
        if not self.selected or not ask(self, _("Delete keyboard profile {name}?",
                                                name=self._entry_label(self.selected_scope, self.selected))):
            return
        try:
            self.cfg = keyboard.store(self.character, self.selected_scope, self.selected, None, self.cls)
        except (OSError, ValueError) as e:
            error(self, str(e))
            return
        self._load("character", "")

    def _apply(self, _event: wx.Event) -> None:
        saved = self._saved(self.selected_scope, self.selected)
        try:
            unchanged = saved is not None and keyboard.Profile.model_validate(saved) == self.draft
        except ValueError:
            unchanged = False
        if (not self.selected or self.profile_name.GetValue().strip() != self.selected or not unchanged
                or keyboard.SCOPES[max(0, self.scope.GetSelection())] != self.selected_scope):
            error(self, _("Save the keyboard profile before applying it."))
            return
        self.apply_name = self.selected
        self.EndModal(wx.ID_OK)
