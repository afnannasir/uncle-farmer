"""Results, and the game's own voice (muting it while the bot runs)."""

from __future__ import annotations

import os
import threading


from ... import speech, stats
from ...stats import Tally
from ...i18n import _
from ...link import AgentError, AgentLink
from ..results import ResultsDialog
from .base import FrameParts, foreground_pid


class ResultsMixin(FrameParts):
    # ------------------------------------------------------------ results and the game's voice
    def live_tally(self, character: str) -> Tally | None:
        run = next((r for r in self.runs.values() if r.character == character and r.tally), None)
        return run.tally if run else None

    def show_results(self) -> None:
        with ResultsDialog(self, self.selected_character(), self.live_tally) as d:
            d.ShowModal()

    def open_results_folder(self) -> None:
        os.makedirs(stats.RESULTS, exist_ok=True)
        os.startfile(stats.RESULTS)

    def speak_results(self, front: bool = False) -> None:
        """Shift+Pause from anywhere (the game in front, else the selected one), or the menu."""
        pid = foreground_pid() if front else None
        if pid not in {g[0] for g in self.games}:
            pid = self.selected_pid()
        name = next((g[1] for g in self.games if g[0] == pid), None)
        run = self.runs.get(pid) if pid is not None else None
        if run and run.tally:
            speech.speak(_("{name}: {results}", name=run.character or name, results=stats.short(run.tally.result())))
            return
        past = stats.runs(name) if name else []
        if past:
            speech.speak(_("{name}, last run: {results}", name=name, results=stats.short(past[-1])))
        else:
            speech.speak(_("No results yet."))

    def progress(self) -> None:
        """A short spoken line per running bot every results_every minutes (0 = never)."""
        every = self.s.get("results_every", 0) or 0
        if every <= 0:
            return
        for run in list(self.runs.values()):
            t = run.tally
            if t and self.clock.time() - t.spoken >= every * 60:
                t.spoken = self.clock.time()
                speech.speak(_("{name}: {results}", name=run.character, results=stats.short(t.result())))

    def keep_silent(self) -> None:
        """game_speech "always": silence every game with the agent, each time one starts. On a
        worker: a slow game must not hold the status fields up."""
        if self.s.get("game_speech") != "always":
            return
        todo = [pid for pid, _n, _w in list(self.games) if pid not in self.silenced]
        if not todo:
            return
        self.silenced.update(todo)          # once per game (an older agent can't, and isn't asked again)

        def work() -> None:
            for pid in todo:
                try:
                    with AgentLink(pid=pid, timeout=3) as a:
                        if a.send("speech") == "on":
                            a.send("speech off")
                except (AgentError, OSError):
                    pass
        threading.Thread(target=work, daemon=True).start()

    def keep_voice_fix(self) -> None:
        """fix_voice: tell each game's agent whether to mend the game's voice message player. A game
        starts mended, so only a change is sent: to every game when the setting changes, and to each
        game that starts while it is off. Once per game and change (an older agent can't, and isn't
        asked again). On a worker, like keep_silent."""
        want = bool(self.s.get("fix_voice", True))
        todo = [pid for pid, _n, _w in list(self.games) if self.voice_fixed.get(pid, True) != want]
        if not todo:
            return
        for pid in todo:
            self.voice_fixed[pid] = want

        def work() -> None:
            for pid in todo:
                try:
                    with AgentLink(pid=pid, timeout=3) as a:
                        a.send("voicefix " + ("on" if want else "off"))
                except (AgentError, OSError):
                    pass
        threading.Thread(target=work, daemon=True).start()

    def set_game_speech(self, talk: bool, every: bool = False) -> None:
        """Mute (talk False) or unmute the selected game - each game on its own, so muting one that
        is botting leaves the one being played talking - or, with every, all of them. Two commands,
        not a toggle: a run mutes its game when it starts, so a toggle pressed "to mute" turned the
        speech back on. A run that ends doesn't undo the player's own choice."""
        want = "on" if talk else "off"
        if every:
            games = list(self.games)
        else:
            i = self.selected_index()
            if i is None and len(self.games) == 1:
                i = 0
            if i is None:
                self.say(_("No game with the agent is running.") if not self.games else _("Select a game first."))
                return
            games = [self.games[i]]
        changed = same = old = 0
        for pid, _name, _where in games:
            try:
                with AgentLink(pid=pid, timeout=3) as a:
                    now = a.send("speech")
                    if now not in ("on", "off"):
                        old += 1
                        continue
                    if now == want:
                        same += 1
                    elif a.send("speech " + want) == want:
                        changed += 1
                    else:
                        continue
            except (AgentError, OSError):
                continue
            run = self.runs.get(pid)
            if run is not None:
                run.speech_choice = want      # the run's end leaves it like this
            self.silenced.add(pid)            # and "always mute" doesn't overrule it either
        if not (changed or same or old):
            self.say(_("No game with the agent is running."))
        elif old and not (changed or same):
            self.say(_("This game started with an older agent. Restart it to mute it."))
        elif not every:
            name = games[0][1] or _("This game")
            if talk:
                self.say(_("{name} unmuted.", name=name) if changed else _("{name} was already talking.", name=name))
            else:
                self.say(_("{name} muted.", name=name) if changed else _("{name} was already muted.", name=name))
        elif talk:
            self.say(_("Games unmuted.") if changed else _("The games were already talking."))
        else:
            self.say(_("Games muted.") if changed else _("The games were already muted."))
