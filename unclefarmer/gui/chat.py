"""The Chat window: the game's chat while the bot farms, read and written without the game's keys.

Two tabs, as in the game: Channels (All, Team, Gang, Nearby, Map, Global, International, the
Private channel players join, and the game's Notices) and Private chats (one conversation per
person, only the people this character has talked with). Ctrl+Tab moves between the tabs, Ctrl+1 ..
Ctrl+9 between the channels. Everything lives in memory only (chat.py) and is gone when Uncle
Farmer closes.
"""

import threading
import time
from typing import Any

import wx

from .. import chat, nearby, settings, tones
from ..i18n import _
from .common import labeled, ok_cancel, page, tabs

ALL = "all"
CLOCKS = ("24", "12")
READ = ("open", "always", "never")       # when new channel messages are read out


def channel_buffers():
    """(key, name) of the channel buffers, in the order Ctrl+1 .. Ctrl+9 reach them."""
    return [(ALL, _("All")), ("4", _("Team")), ("3", _("Gang")), ("2", _("Nearby")), ("1", _("Map")),
            ("0", _("Global")), ("5", _("International")), ("6", _("Private channel")),
            (chat.NOTICE, _("Notices"))]


def stamp(when, clock):
    """The time a line came: "16:05" or "4:05 PM"."""
    t = time.localtime(when)
    return time.strftime("%I:%M %p", t).lstrip("0") if clock == "12" else time.strftime("%H:%M", t)


def line_text(line, where=True, clock=None):
    """"Team, Ali: hello, 16:05" - the time after the words, so a screen reader starts with who and
    what; clock None leaves it out (speech, or the player turned it off). where=False inside a
    channel's own buffer or a conversation: "Ali: hello, 16:05", and our own lines read "you: ..."."""
    words = line["text"]
    if line.get("voice"):
        words = _("voice message") + (" " + words if words else "")
    if line.get("to"):
        who = _("to {name}", name=line["to"]) if where else _("you")
    else:
        who = line["name"] or ""
    if where:
        place = chat.channel_name(line["channel"])
        who = "%s, %s" % (place, who) if who else place
    text = "%s: %s" % (who, words) if who else words
    return "%s, %s" % (text, stamp(line["when"], clock)) if clock else text


def clock_of(s):
    """The clock the times are shown in, or None when the player turned the times off."""
    if not s.get("chat_time", True):
        return None
    return s.get("chat_clock") if s.get("chat_clock") in CLOCKS else "24"


def set_label(label_ctrl, ctrl, text):
    if label_ctrl.GetLabel() != text:
        label_ctrl.SetLabel(text)
        ctrl.SetName(text.replace("&", "").rstrip(":"))


class ChatFrame(wx.Frame):
    """host: the main window (its feeds, messengers, snapshot, say and settings)."""

    def __init__(self, host, pid, character):
        super().__init__(host, title=_("Chat - {name}", name=character or _("game")), size=wx.Size(640, 600))
        self.host, self.pid, self.character = host, pid, character
        self.s = host.s
        self.ch_keys = [k for k, _n in channel_buffers()]
        self.ch_names = dict(channel_buffers())
        self.unread = {k: 0 for k in self.ch_keys}
        self.ch_lines = []
        self.pm_unread = {}            # conversation key -> new messages not looked at
        self.started = {}              # conversations begun here before any message: key -> {...}
        self.convs = []
        self.pm_lines = []
        self.recording = False         # a voice message is being recorded (the game's microphone)
        self.record_buttons = []

        root = wx.BoxSizer(wx.VERTICAL)
        self.nb = tabs(self)
        root.Add(self.nb, 1, wx.EXPAND)

        panel, box = page(self.nb, _("Channels"))
        start = self.s.get("chat_buffer", ALL)
        self.channel = labeled(panel, box, _("C&hannel:"),
                               lambda w: wx.Choice(w, choices=[n for _k, n in channel_buffers()]))
        self.channel.SetSelection(self.ch_keys.index(start) if start in self.ch_keys else 0)
        self.ch_list = labeled(panel, box, _("&Messages:"), lambda w: wx.ListBox(w, style=wx.LB_SINGLE), 1)
        self.ch_label = wx.StaticText(panel, label=_("Messa&ge:"))
        box.Add(self.ch_label, 0, wx.TOP, 8)
        self.ch_text = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        box.Add(self.ch_text, 0, wx.EXPAND | wx.TOP, 4)
        self.buttons(panel, box)

        panel, box = page(self.nb, _("Private chats"))
        self.people = labeled(panel, box, _("&People:"), lambda w: wx.ListBox(w, style=wx.LB_SINGLE), 1)
        self.pm_list = labeled(panel, box, _("&Messages:"), lambda w: wx.ListBox(w, style=wx.LB_SINGLE), 2)
        self.pm_label = wx.StaticText(panel, label=_("Messa&ge:"))
        box.Add(self.pm_label, 0, wx.TOP, 8)
        self.pm_text = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        box.Add(self.pm_text, 0, wx.EXPAND | wx.TOP, 4)
        # An editable list, not a text box with the system's autocomplete: that one was set up
        # again on every focus (with a game reading on the window's thread), and the private chat
        # tab got stuck there until Escape closed the window.
        self.new_chat = labeled(panel, box, _("&New chat with (name or id):"),
                                lambda w: wx.ComboBox(w, style=wx.CB_DROPDOWN | wx.TE_PROCESS_ENTER))
        self.names: list[str] = []
        self.buttons(panel, box)
        self.SetSizer(root)

        self.channel.Bind(wx.EVT_CHOICE, lambda e: self.show_channel())
        self.ch_list.Bind(wx.EVT_LISTBOX, lambda e: self.label_channel_message())
        self.ch_text.Bind(wx.EVT_TEXT_ENTER, lambda e: self.send())
        self.people.Bind(wx.EVT_LISTBOX, lambda e: self.show_conversation())
        self.pm_text.Bind(wx.EVT_TEXT_ENTER, lambda e: self.send())
        # Enter (or a double click) on a voice message plays it
        self.ch_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.play())
        self.pm_list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.play())
        self.new_chat.Bind(wx.EVT_TEXT_ENTER, lambda e: self.start_chat())
        self.nb.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.on_tab)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.show_channel()
        self.refresh_people()
        self.ch_text.SetFocus()

    def buttons(self, panel, box):
        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in ((_("&Send"), lambda e: self.send()),
                               (_("Record &voice"), lambda e: self.record()),
                               (_("Pla&y"), lambda e: self.play()),
                               (_("Se&ttings..."), lambda e: self.settings()),
                               (_("Close"), lambda e: self.Close())):
            b = wx.Button(panel, label=label)
            b.Bind(wx.EVT_BUTTON, handler)
            row.Add(b, 0, wx.RIGHT, 8)
            if label == _("Record &voice"):
                self.record_buttons.append(b)
        box.Add(row, 0, wx.TOP, 10)

    def feed(self):
        return self.host.chats.get(self.pid)

    def on_private(self):
        return self.nb.GetSelection() == 1

    # ---------------------------------------------------------------- channels
    def channel_key(self):
        return self.ch_keys[self.channel.GetSelection()]

    def show_channel(self):
        k = self.channel_key()
        f = self.feed()
        self.ch_lines = f.buffer(k) if f else []
        self.ch_list.Set([line_text(x, where=k == ALL, clock=clock_of(self.s)) for x in self.ch_lines])
        if self.ch_lines:
            self.ch_list.SetSelection(len(self.ch_lines) - 1)
        self.unread[k] = 0
        self.label_channels()
        self.label_channel_message()
        self.s["chat_buffer"] = k

    def label_channels(self):
        for i, k in enumerate(self.ch_keys):
            n = self.unread[k]
            text = self.ch_names[k] if not n else _("{buffer} ({n} new)", buffer=self.ch_names[k], n=n)
            if self.channel.GetString(i) != text:
                self.channel.SetString(i, text)

    def channel_target(self):
        """The channel a line typed on the Channels tab goes to: the buffer's own, or in All the
        selected line's channel, else the last one used."""
        k = self.channel_key()
        if k in chat.CHANNEL_KEYS:
            return k
        i = self.ch_list.GetSelection()
        line = self.ch_lines[i] if 0 <= i < len(self.ch_lines) else None
        if k == ALL and line and line["channel"] in chat.CHANNEL_KEYS:
            return line["channel"]
        last = self.s.get("chat_channel", "4")
        return last if last in chat.CHANNEL_KEYS else "4"

    def label_channel_message(self):
        set_label(self.ch_label, self.ch_text,
                  _("Messa&ge to {where}:", where=chat.channel_name(self.channel_target())))

    # ---------------------------------------------------------------- private chats
    def conversations(self) -> list[dict[str, Any]]:
        f = self.feed()
        convs: list[dict[str, Any]] = f.conversations() if f else []
        have = {c["key"] for c in convs}
        extra = [dict(c, lines=[]) for k, c in self.started.items() if k not in have]
        # the latest activity first: a message, or a chat just started here
        return sorted(extra + convs, key=lambda c: (-c["last"], -c.get("order", -1)))

    def refresh_people(self, select_key=None):
        """The People list: one row per conversation, the latest first, with its new messages."""
        keep = select_key or self.peer_key()
        self.convs = self.conversations()
        rows = []
        for c in self.convs:
            n = self.pm_unread.get(c["key"], 0)
            rows.append(c["name"] if not n else _("{name} ({n} new)", name=c["name"], n=n))
        if self.people.GetItems() != rows:
            self.people.Set(rows)
        keys = [c["key"] for c in self.convs]
        if keep in keys:
            self.people.SetSelection(keys.index(keep))
        elif rows:
            self.people.SetSelection(0)
        self.show_conversation()

    def peer(self):
        i = self.people.GetSelection()
        return self.convs[i] if 0 <= i < len(self.convs) else None

    def peer_key(self):
        c = self.peer()
        return c["key"] if c else None

    def show_conversation(self):
        c = self.peer()
        lines = list(c["lines"]) if c else []
        old = self.pm_lines
        if old and lines[:len(old)] == old and self.pm_list.GetCount() == len(old):
            # the same conversation, with new lines: add them and leave the reader where they are
            at_end = self.pm_list.GetSelection() in (wx.NOT_FOUND, len(old) - 1)
            for x in lines[len(old):]:
                self.pm_list.Append(line_text(x, where=False, clock=clock_of(self.s)))
            self.pm_lines = lines
            if at_end and len(lines) > len(old) and not self.pm_list.HasFocus():
                self.pm_list.SetSelection(len(lines) - 1)
        else:
            self.pm_lines = lines
            self.pm_list.Set([line_text(x, where=False, clock=clock_of(self.s)) for x in lines])
            if lines:
                self.pm_list.SetSelection(len(lines) - 1)
        if c and self.on_private() and self.pm_unread.pop(c["key"], 0):
            self.people.SetString(self.people.GetSelection(), c["name"])
        set_label(self.pm_label, self.pm_text,
                  _("Messa&ge to {where}:", where=c["name"]) if c else _("Messa&ge:"))

    def on_tab(self, evt):
        if self.on_private():
            self.fill_names()
        evt.Skip()

    def fill_names(self):
        """The New chat list: the people talked with and the players in sight (the last reading the
        window already has - no new one is asked for). What is typed in it stays."""
        names = [c["name"] for c in self.conversations() if not c["name"].isdigit()]
        s = self.host.cached(self.pid, 60.0)
        if s:
            names += [n for n, _d in nearby.players(s)]
        names = sorted(set(names), key=str.lower)
        if names != self.names:
            self.names = names
            typed = self.new_chat.GetValue()
            self.new_chat.Set(names)
            self.new_chat.ChangeValue(typed)

    def start_chat(self):
        """New chat with a name or an id: a conversation of its own, ready to type into."""
        who = self.new_chat.GetValue().strip()
        f = self.feed()
        if not who or f is None:
            return
        pid = f.id_of(who)
        if not pid:
            s = self.host.snapshot(self.pid)
            hit = next((e for e in (s or {}).get("entities", []) if e.get("kind") == "player"
                        and (e.get("name") or "").strip().lower() == who.lower()), None)
            pid = hit["id"] if hit else None
        if not pid:
            self.host.say(_("The game hasn't shown {name} yet, so their id isn't known: type their id instead, "
                            "or wait until they are in sight or message you.", name=who))
            return
        key = "id:%d" % pid
        if key not in [c["key"] for c in self.conversations()]:
            self.started[key] = {"key": key, "name": who, "id": pid, "last": time.time()}
        self.new_chat.SetValue("")
        self.refresh_people(select_key=key)
        self.pm_text.SetFocus()

    # ---------------------------------------------------------------- arriving
    def add(self, line):
        """A new line from the feed (on the window's thread)."""
        if line["channel"] == chat.PRIVATE:
            key = chat.Feed.peer_key(line)
            self.started.pop(key, None)
            if (key != self.peer_key() or not self.on_private()) and not line.get("mine"):
                self.pm_unread[key] = self.pm_unread.get(key, 0) + 1
            self.refresh_people(select_key=self.peer_key() or key)
            return
        k = self.channel_key()
        if k == line["channel"] or (k == ALL and line["channel"] not in (chat.NOTICE, chat.PRIVATE)):
            at_end = self.ch_list.GetSelection() in (wx.NOT_FOUND, self.ch_list.GetCount() - 1)
            self.ch_lines.append(line)
            self.ch_list.Append(line_text(line, where=k == ALL, clock=clock_of(self.s)))
            if self.ch_list.GetCount() > chat.KEEP:
                self.ch_list.Delete(0)
                self.ch_lines.pop(0)
            if at_end and not self.ch_list.HasFocus():
                self.ch_list.SetSelection(self.ch_list.GetCount() - 1)
            return
        if not line.get("mine"):
            self.unread[line["channel"]] = self.unread.get(line["channel"], 0) + 1
            if line["channel"] != chat.NOTICE and k != ALL:
                self.unread[ALL] += 1
            self.label_channels()

    # ---------------------------------------------------------------- sending
    def send(self):
        private = self.on_private()
        box = self.pm_text if private else self.ch_text
        text = " ".join(box.GetValue().split())
        if not text:
            return
        f = self.feed()
        if f is None or f.unsupported:
            self.host.say(_("Chat needs the new agent in this game: restart the game."))
            return
        if private:
            why = self.send_private(f, text)
        else:
            k = self.channel_target()
            why = f.say(int(k), text)
            if not why:
                self.s["chat_channel"] = k
        if why:
            self.host.say(why)
            return
        n = len(chat.parts(text))
        if n > 1:
            self.host.say(_("That is longer than the game's 200 characters: it goes in {n} parts, "
                            "one every 2 seconds (3 on Global), as fast as the game allows.", n=n))
        box.SetValue("")

    def send_private(self, f, text):
        c = self.peer()
        if c is None:
            return _("Pick someone in People, or type a name or id in New chat first.")
        to = c.get("id") or f.id_of(c["name"])
        if not to:
            return _("The game hasn't shown {name} yet, so their id isn't known: type their id instead, "
                     "or wait until they are in sight or message you.", name=c["name"])
        m = self.host.messengers.get(self.pid)
        if m is None or not m.ready:
            return _("Private messages aren't ready in this game yet.")
        m.send(to, c["name"], text, first=True)    # it shows here when the game sends it (the agent sees it go)
        return None

    # ---------------------------------------------------------------- voice messages
    def voice_target(self):
        """Where a voice message from this tab goes: ("pm", id, name) in a private chat, ("channel",
        key, name) on Team or Gang, or (None, why, "") where the game doesn't take one."""
        f = self.feed()
        if self.on_private():
            c = self.peer()
            if c is None:
                return None, _("Pick someone in People, or type a name or id in New chat first."), ""
            to = c.get("id") or (f.id_of(c["name"]) if f else None)
            if not to:
                return None, _("The game hasn't shown {name} yet, so their id isn't known: type their id instead, "
                               "or wait until they are in sight or message you.", name=c["name"]), ""
            return "pm", to, c["name"]
        k = self.channel_target()
        if k not in chat.VOICE_CHANNELS:
            return None, _("The game sends voice messages only to Team, Gang and private chats."), ""
        return "channel", k, chat.channel_name(k)

    def record(self):
        """The voice button: the first press records from the game's microphone, the next sends it."""
        f = self.feed()
        if f is None:
            self.host.say(_("Chat needs the new agent in this game: restart the game."))
            return
        if not self.recording:
            where = self.voice_target()
            why = where[1] if where[0] is None else f.record_voice()
            if why:
                self.host.say(why)
                return
            self.set_recording(True)
            self.host.say(_("Recording for {where} - press Record voice again to send it, Escape to cancel. "
                            "Longer than 20 seconds goes in parts.", where=where[2]))
            return
        where = self.voice_target()
        self.set_recording(False)
        if where[0] is None:
            f.cancel_voice()
            self.host.say(where[1])
            return
        kind, target, name = where

        def go():
            # stopping waits for the recorder to finish: off the window's thread
            why = f.send_voice(to=target) if kind == "pm" else f.send_voice(channel=target)
            n = getattr(f, "voice_parts", 1)
            done = (_("Voice message sent to {where}.", where=name) if n <= 1
                    else _("Voice message sent to {where} in {n} parts.", where=name, n=n))
            wx.CallAfter(self.host.say, why or done)
        threading.Thread(target=go, daemon=True, name="unclefarmer-voice-send").start()

    def set_recording(self, on):
        self.recording = on
        for b in self.record_buttons:
            b.SetLabel(_("Stop and send &voice") if on else _("Record &voice"))

    def cancel_recording(self):
        f = self.feed()
        if self.recording and f is not None:
            f.cancel_voice()
        self.set_recording(False)

    def play(self):
        """Plays the voice message selected in this tab's Messages list."""
        lst, lines = (self.pm_list, self.pm_lines) if self.on_private() else (self.ch_list, self.ch_lines)
        i = lst.GetSelection()
        line = lines[i] if 0 <= i < len(lines) else None
        if not line or not line.get("voice"):
            self.host.say(_("Pick a voice message in Messages first."))
            return
        f = self.feed()
        why = f.play_voice(line["voice"]) if f else _("Chat needs the new agent in this game: restart the game.")
        if why:
            self.host.say(why)

    # ---------------------------------------------------------------- keys and settings
    def on_key(self, evt):
        code = evt.GetKeyCode()
        if code == wx.WXK_ESCAPE:
            if self.recording:
                self.cancel_recording()
                self.host.say(_("Recording cancelled."))
                return
            self.Close()
            return
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and wx.Window.FindFocus() in (self.ch_list, self.pm_list):
            # Enter on a message plays it when it is a voice message: a list box turns only a
            # double click into an activation, so Enter did nothing and only Alt+Y played it
            self.play()
            return
        if evt.ControlDown() and not evt.AltDown() and ord("1") <= code <= ord("9"):
            i = code - ord("1")
            if i < len(self.ch_keys):
                self.nb.SetSelection(0)
                self.channel.SetSelection(i)
                self.show_channel()
                self.ch_list.SetFocus()
            return
        evt.Skip()

    def settings(self):
        with ChatSettings(self, self.s) as d:
            if d.ShowModal() == wx.ID_OK:
                settings.save_app(self.s)
                self.show_channel()          # the times as now chosen
                self.show_conversation()

    def on_close(self, _evt):
        self.cancel_recording()
        settings.save_app(self.s)
        self.host.chat_windows.pop(self.pid, None)
        self.Destroy()


class ChatSettings(wx.Dialog):
    """When and which channels are read out, the private message tones, and the times."""

    def __init__(self, parent, app_settings):
        super().__init__(parent, title=_("Chat settings"))
        self.s = app_settings
        box = wx.BoxSizer(wx.VERTICAL)

        read = wx.StaticBoxSizer(wx.VERTICAL, self, _("Reading channels out"))
        sb = read.GetStaticBox()
        read.Add(wx.StaticText(sb, label=_("&Read new messages:")), 0, wx.LEFT | wx.TOP, 4)
        self.read = wx.Choice(sb, choices=[_("Only while the Chat window is open"), _("Always"), _("Never")])
        self.read.SetName(_("Read new messages"))
        self.read.SetSelection(READ.index(self.s.get("chat_read", "open")) if self.s.get("chat_read") in READ else 0)
        read.Add(self.read, 0, wx.ALL, 4)
        speak = set(self.s.get("chat_speak", chat.SPEAK_DEFAULT))
        self.checks = {}
        for k, name in channel_buffers():
            if k == ALL:
                continue
            c = wx.CheckBox(sb, label=name)
            c.SetValue(k in speak)
            read.Add(c, 0, wx.ALL, 4)
            self.checks[k] = c
        box.Add(read, 0, wx.EXPAND | wx.ALL, 12)

        pm = wx.StaticBoxSizer(wx.VERTICAL, self, _("Private messages to you"))
        sb = pm.GetStaticBox()
        self.tones = {}
        for key, label, default in (("chat_tone_trusted", _("Tone from &trusted players:"), "chime"),
                                    ("chat_tone_others", _("Tone from &everyone else:"), "ding")):
            pm.Add(wx.StaticText(sb, label=label), 0, wx.LEFT | wx.TOP, 4)
            c = wx.Choice(sb, choices=[tones.label(t) for t in tones.ORDER])
            c.SetName(label.replace("&", "").rstrip(":"))
            have = self.s.get(key, default)
            c.SetSelection(tones.ORDER.index(have) if have in tones.ORDER else 0)
            c.Bind(wx.EVT_CHOICE, lambda e, c=c: tones.play(tones.ORDER[c.GetSelection()]))   # hear it
            pm.Add(c, 0, wx.ALL, 4)
            self.tones[key] = c
        pm.Add(wx.StaticText(sb, label=_("Trusted players are the names and ids in Private messages (Ctrl+Shift+M), "
                                         "where reading private messages out is set too.")), 0, wx.ALL, 4)
        box.Add(pm, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        times = wx.StaticBoxSizer(wx.VERTICAL, self, _("Times"))
        sb = times.GetStaticBox()
        self.show_time = wx.CheckBox(sb, label=_("&Show the time after each message"))
        self.show_time.SetValue(self.s.get("chat_time", True))
        times.Add(self.show_time, 0, wx.ALL, 4)
        times.Add(wx.StaticText(sb, label=_("C&lock:")), 0, wx.LEFT | wx.TOP, 4)
        self.clock = wx.Choice(sb, choices=[_("24-hour (16:05)"), _("12-hour (4:05 PM)")])
        self.clock.SetName(_("Clock"))
        self.clock.SetSelection(1 if self.s.get("chat_clock") == "12" else 0)
        times.Add(self.clock, 0, wx.ALL, 4)
        self.show_time.Bind(wx.EVT_CHECKBOX, lambda e: self.clock.Enable(self.show_time.GetValue()))
        self.clock.Enable(self.show_time.GetValue())
        box.Add(times, 0, wx.EXPAND | wx.ALL, 12)

        ok_cancel(self, box)
        self.SetSizerAndFit(box)
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)

    def on_ok(self, _evt):
        self.s["chat_read"] = READ[self.read.GetSelection()]
        self.s["chat_speak"] = [k for k, c in self.checks.items() if c.GetValue()]
        for key, c in self.tones.items():
            self.s[key] = tones.ORDER[c.GetSelection()]
        self.s["chat_time"] = self.show_time.GetValue()
        self.s["chat_clock"] = CLOCKS[self.clock.GetSelection()]
        self.EndModal(wx.ID_OK)
