"""The games' own speech, as the player set it (settings.game_speech_mode): each character's own
choice (Character settings, Overview) or the app's (File, Settings, Speech and language).

- "always": each game is silenced once, when it comes up (Ctrl+U still lets it talk);
- "front": a game talks only while its window is in front. Players with several games open asked
  for it: switching to a game lets it talk, switching away silences it. Only a change of the window
  in front is acted on, so Ctrl+M or Ctrl+U holds until the next switch;
- "running" and "never": the runs see to it (engine/runner.py), as before.

A game this window silenced for "front" talks again when it leaves that mode and when Uncle Farmer
closes. The window in front is looked at four times a second (a Windows call, nothing else); each
game's mode is read again every few seconds, its profile only when the file changed.
"""

from __future__ import annotations

import os
import threading

import wx

from ... import settings
from ...link import AgentError, AgentLink
from .base import FrameParts, foreground_pid

FOLLOW_EVERY = 250          # ms between looks at the window in front
FRONT = "front"


class GameSpeechMixin(FrameParts):
    speech_modes: dict[int, str]              # pid -> its game's mode (refreshed with the game list)
    speech_profiles: dict[str, tuple[float, str]]   # profile -> (its file's time, its own choice)
    front_said: dict[int, str]                # pid -> "on"/"off", what follow_front last told it
    front_busy: set[int]
    focus_timer: wx.Timer

    def start_following_front(self) -> None:
        self.speech_state()
        self.focus_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self.follow_front(), self.focus_timer)
        self.focus_timer.Start(FOLLOW_EVERY)

    def speech_state(self) -> None:
        """This part's own state, made the first time it is needed (a stand-in window has none)."""
        if getattr(self, "speech_modes", None) is None:
            self.speech_modes, self.speech_profiles, self.front_said, self.front_busy = {}, {}, {}, set()

    def speech_mode_of(self, profile: str | None) -> str:
        """The game speech mode for this character (the app's when no character is logged in)."""
        if not profile:
            return settings.game_speech_mode(self.s, None)
        path = settings.profile_path(profile)
        try:
            when = os.path.getmtime(path)
        except OSError:
            when = -1.0
        self.speech_state()
        known = self.speech_profiles.get(profile)
        if known is None or known[0] != when:
            own = str(settings.load_profile(profile).get("game_speech") or "") if when >= 0 else ""
            known = self.speech_profiles[profile] = (when, own)
        return settings.game_speech_mode(self.s, {"game_speech": known[1]})

    def keep_silent(self) -> None:
        """Each game's mode, read again (with the game list, every few seconds): "always" silences a
        game once, each time one comes up; a game this window silenced for "front" that left that
        mode talks again (unless it is "always" now). On a worker: a slow game must not hold the
        status fields up."""
        self.speech_state()
        modes = {pid: self.speech_mode_of(profile) for pid, profile, _w in list(self.games)}
        before = self.speech_modes
        self.speech_modes = modes
        said = self.front_said
        todo: list[tuple[int, str]] = []
        for pid, mode in modes.items():
            if mode == "always" and pid not in self.silenced:
                self.silenced.add(pid)      # once per game (an older agent can't, and isn't asked again)
                todo.append((pid, "off"))
            if mode != FRONT and before.get(pid) == FRONT and said.pop(pid, None) == "off" and mode != "always":
                todo.append((pid, "on"))
        for pid in [p for p in said if p not in modes]:
            said.pop(pid)                   # gone
        if todo:
            threading.Thread(target=self._tell_speech, args=(todo,), daemon=True).start()

    def follow_front(self) -> None:
        """A game in "front" mode talks while its window is in front and is silent otherwise: what
        changed since the last look is sent, on a worker."""
        if self.destroyed or not getattr(self, "speech_modes", None):
            return
        front = foreground_pid()
        todo: list[tuple[int, str]] = []
        for pid, mode in self.speech_modes.items():
            if mode != FRONT or pid in self.front_busy:
                continue
            want = "on" if pid == front else "off"
            if self.front_said.get(pid) != want:
                self.front_said[pid] = want
                self.front_busy.add(pid)
                todo.append((pid, want))
        if todo:
            threading.Thread(target=self._tell_speech, args=(todo, True), daemon=True).start()

    def _tell_speech(self, todo: list[tuple[int, str]], following: bool = False) -> None:
        for pid, want in todo:
            try:
                with AgentLink(pid=pid, timeout=3) as a:
                    if a.send("speech") in ("on", "off"):       # an older agent can't: left alone
                        a.send("speech " + want)
            except (AgentError, OSError):
                pass
            finally:
                if following:
                    wx.CallAfter(self.front_busy.discard, pid)

    def speech_back(self) -> None:
        """Uncle Farmer closes: the games "front" silenced talk again (at once, not on a worker)."""
        said: dict[int, str] = getattr(self, "front_said", {})
        self._tell_speech([(pid, "on") for pid, now in said.items() if now == "off"])
        said.clear()
