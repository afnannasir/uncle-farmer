"""Restore saved Sound Silencer rules once per game/configuration, off the UI thread."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence

from . import settings, silencer
from .link import AgentLink


class SoundRestore:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.applied: dict[int, str] = {}
        self.retry: dict[int, float] = {}

    def sync(self, pids: Sequence[int]) -> None:
        if self.closed.is_set() or not self.lock.acquire(blocking=False):
            return

        def work() -> None:
            try:
                data = settings.load_app()
                if "sound_silencer" not in data:
                    return
                rules = silencer.validate(data["sound_silencer"])
                text = silencer.mute_text(rules)
                for pid in list(self.applied):
                    if pid not in pids:
                        self.applied.pop(pid, None)
                        self.retry.pop(pid, None)
                for pid in pids:
                    if self.closed.is_set():
                        break
                    if self.applied.get(pid) == text or self.retry.get(pid, 0) > time.monotonic():
                        continue
                    try:
                        with AgentLink(pid=pid, timeout=2) as link:
                            silencer.apply_saved(link)
                        self.applied[pid] = text
                        self.retry.pop(pid, None)
                    except (ValueError, RuntimeError, OSError) as error:
                        if pid not in self.retry:
                            logging.getLogger(__name__).warning("Sound Silencer restore: %s", error)
                        self.retry[pid] = time.monotonic() + 5
            except (ValueError, RuntimeError, OSError) as error:
                logging.getLogger(__name__).warning("Invalid Sound Silencer settings: %s", error)
            finally:
                self.lock.release()

        threading.Thread(target=work, name="sound-silencer-restore", daemon=True).start()

    def close(self) -> None:
        self.closed.set()
