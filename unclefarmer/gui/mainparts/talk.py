"""Host of the private messages (messages.py) and the game chat (chat.py, memory only)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TypeVar, TypeVarTuple

import wx

from ... import applog, chat, messages, settings, speech, tones, trust
from ...errors import carry_on
from ...i18n import _
from ..chat import ChatFrame, line_text
from ..common import error, save_file
from ...engine.botparts.parts import Sighting, Where
from ...engine.runner import Run
from .base import FrameParts, foreground_pid, saved_item

T = TypeVar("T")
Ts = TypeVarTuple("Ts")


class TalkMixin(FrameParts):
    # ------------------------------------------------------------ private messages (messages.py)
    def game_in_front(self, pid: int | None) -> bool:
        """That game's own window is the one in front: the player is in the game, reading and
        hearing it there - our reports and tones would only talk over it."""
        return pid is not None and foreground_pid() == pid

    def run_for(self, pid: int) -> Run | None:
        return self.runs.get(pid)

    def farm_start_for(self, pid: int) -> Where | None:
        """(map, x, y) where this game's last farming run started, or None: where `home` goes."""
        starts: dict[int, Where] = getattr(self, "farm_starts", {})
        return starts.get(pid)

    def locate(self, name: str) -> Where | None:
        """(map id, x, y) of a character one of our games is playing, read from that game; None
        when no game here plays them. Lets `come` and `follow` find our own characters anywhere."""
        want = (name or "").strip().lower()
        for pid, m in list(self.messengers.items()):
            link = m.link
            if link is None or (self.character(pid) or "").strip().lower() != want:
                continue
            try:
                s = link.json("mem")
                if s.get("ok"):
                    return s["map"]["id"], s["self"]["x"], s["self"]["y"]
            except Exception as e:
                carry_on(e, "locating a character")
                applog.write("locating %s: %r" % (want, e), "WARNING")
                return None
        return None

    SEEN_FOR = 600.0     # seconds a sighting of a player stays worth walking to

    def last_seen(self, name: str) -> Sighting | None:
        """(map id, x, y, seconds ago) where any of our games last saw this trusted player, or
        None. For a player we don't play here, who has walked out of view."""
        want = (name or "").strip().lower()
        best: Sighting | None = None
        for m in list(self.messengers.values()):
            hit = m.sightings.get(want)
            if hit and (best is None or hit[3] > best[3]):
                best = hit
        if best is None or self.clock.time() - best[3] > self.SEEN_FOR:
            return None
        return best[0], best[1], best[2], self.clock.time() - best[3]

    def character(self, pid: int) -> str | None:
        return next((g[1] for g in self.games if g[0] == pid), None)

    def call(self, fn: Callable[[*Ts], T], *args: *Ts) -> T | str | None:
        """Run fn on the window's thread (a messenger asks from its own) and return its result: what
        went wrong if it raised, None if the window didn't get to it within 15 s."""
        if threading.current_thread() is threading.main_thread():
            return fn(*args)
        box: list[T | str] = []
        done = threading.Event()

        def go() -> None:
            try:
                box.append(fn(*args))
            except Exception as e:
                carry_on(e, "calling the window thread")
                box.append(_("that didn't work: {err}", err=e))
            finally:
                done.set()
        wx.CallAfter(go)
        self.clock.wait(done, 15)
        return box[0] if box else None

    def start_bot(self, pid: int, plan: str | None) -> str | None:
        """'start [plan]' from a trusted player: that plan, or what the character ran last."""
        if plan:
            want = plan.lower()
            found = [it for it in self.items if it[1].lower() == want] or \
                    [it for it in self.items if want in it[1].lower()]
            if not found:
                return _("No plan called {name}.", name=plan)
            item = found[0]
        else:
            last = self.s.get("last_runs", {}).get(self.character(pid) or "")
            if not last or saved_item(last) not in self.items:
                return _("Which plan? Send: start <plan name>")
            item = saved_item(last)
        return self.start(pid, item, remote=True)

    def stop_bot(self, pid: int) -> None:
        self.stop(pid)

    def log(self, line: str, speak: bool = False, who: str | None = None, private: bool = False) -> None:
        wx.CallAfter(self._note, line, speak, who, private)

    def _note(self, line: str, speak: bool, who: str | None, private: bool = False) -> None:
        """A line for the window's log (and speech). private: someone's own words, shown and spoken
        but never written to the log file."""
        if self.destroyed:
            return
        stamp = time.strftime("%H:%M:%S")
        text = "%s  %s: %s" % (stamp, who, line) if who and self.tagged() else "%s  %s" % (stamp, line)
        if not private:
            applog.write("%s  %s: %s" % (stamp, who, line) if who else text)
        self._append(text, speak)

    def keep_messengers(self) -> None:
        """One messenger per game with the agent, started and ended with the game list."""
        live = {g[0] for g in self.games}
        for pid, m in list(self.messengers.items()):
            if pid not in live or (not m.is_alive() and not m.unsupported):
                m.stop()
                del self.messengers[pid]
        for pid in live:
            if pid not in self.messengers:
                m = messages.Messenger(pid, self, clock=self.clock)
                self.messengers[pid] = m
                m.start()

    # ------------------------------------------------------------ chat (chat.py: memory only)
    def keep_chats(self) -> None:
        """One chat feed per game, started and ended with the game list."""
        live = {g[0] for g in self.games}
        for pid, f in list(self.chats.items()):
            if pid not in live or (not f.is_alive() and not f.unsupported):
                f.stop()
                del self.chats[pid]
        for pid in live:
            if pid not in self.chats:
                f = chat.Feed(pid, lambda feed, line: wx.CallAfter(self.chat_line, feed.pid, line))
                self.chats[pid] = f
                f.start()

    def chat_line(self, pid: int, line: chat.Line) -> None:
        """A new chat line: into that game's Chat window if open; a private message to us plays its
        tone (trusted players' and everyone else's can differ); a channel line is read out when the
        player wants that channel read and the moment fits (by default only while the Chat window
        is open). Never into the log: chat is not kept."""
        w = self.chat_windows.get(pid)
        if w:
            w.add(line)
        me = (self.character(pid) or "").lower()
        mine = line.get("mine") or (line["name"] or "").lower() == me
        if mine or self.game_in_front(pid):
            return              # the player is in that game's window: no tones, nothing read out
        if line["channel"] == chat.PRIVATE:
            trusted = trust.from_settings(self.s).has(line["name"], line.get("peer_id"))
            tones.play(self.s.get("chat_tone_trusted", "chime") if trusted else self.s.get("chat_tone_others", "ding"))
            return              # read out (or not) by the private messages settings, as before
        when = self.s.get("chat_read", "open")
        if when == "never" or (when == "open" and not w):
            return
        if line["channel"] in self.s.get("chat_speak", chat.SPEAK_DEFAULT):
            speech.speak(line_text(line))           # no time: who and what is what matters

    def open_chat(self) -> None:
        pid = self.selected_pid()
        if pid is None:
            error(self, _("No game found. Start Mist World first."))
            return
        w = self.chat_windows.get(pid)
        if w is None:
            w = ChatFrame(self, pid, self.character(pid))
            self.chat_windows[pid] = w
        w.Show()
        w.Raise()

    def edit_messages(self) -> None:
        from ..messages import MessagesDialog
        with MessagesDialog(self, self.s) as d:
            if d.ShowModal() == wx.ID_OK:
                save_file(self, settings.save_app, self.s)

    def forget_answered(self) -> None:
        for m in self.messengers.values():
            m.book.forget()
        self.say(_("Everyone can get replies again."))
