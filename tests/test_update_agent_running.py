# pyright: reportPrivateUsage=false
"""A game's agent writing into agent/run while an update runs (2026-11-20: a 1.2.0 copy's update
to 1.2.1 failed "backup verification failed: agent", its rollback refused, and every later start
tried that rollback again and failed, so the app never opened)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import updater, update_recovery as recovery
from test_updater_hardening import app as app, flat, snapshot  # noqa: F401  (app is a fixture)

LOG = "agent/run/agent.log"


def _without_log(folder: Path) -> dict[str, bytes]:
    return {k: v for k, v in snapshot(folder).items() if k != LOG}


def _installed(folder: Path, tmp_path: Path) -> Path:
    """An installed copy whose agent has logged, and a release that changes a file sorted before agent/."""
    flat(folder, "old")
    (folder / "agent/run").mkdir()
    (folder / LOG).write_bytes(b"first line\n")
    (folder / "_bz2.pyd").write_bytes(b"old pyd")
    src = flat(tmp_path / "release")
    (src / "_bz2.pyd").write_bytes(b"new pyd")
    return src


def _game_logs_at(monkeypatch: pytest.MonkeyPatch, folder: Path, operation: str) -> None:
    """The game's agent appends a log line as the copier reaches operation."""
    original = recovery.save

    def save(app_dir: str, record: dict[str, Any], state: str, op: str) -> None:
        original(app_dir, record, state, op)
        if op == operation:
            with open(folder / LOG, "ab") as f:
                f.write(b"a line written during the update\n")

    monkeypatch.setattr(recovery, "save", save)


def test_agent_logging_during_backup_rolls_back_cleanly(app: Path, tmp_path: Path,  # noqa: F811
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    src = _installed(app, tmp_path)
    before = _without_log(app)
    _game_logs_at(monkeypatch, app, "backup-intent:agent")
    with pytest.raises(updater.UpdateError, match="backup verification failed: agent") as caught:
        updater.apply(str(app), str(src), lambda text: None)
    assert not isinstance(caught.value, updater.RollbackError)      # was: rollback incomplete, stuck for good
    assert _without_log(app) == before                               # _bz2.pyd is the old one again
    assert (app / LOG).read_bytes().endswith(b"during the update\n")  # the live log was left alone
    durable = recovery.load(str(app))
    assert durable is not None and durable["state"] == "ROLLED_BACK"


def test_startup_recovers_a_journal_stuck_on_a_changed_agent(app: Path, tmp_path: Path,  # noqa: F811
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """The player's state: the copier's rollback gave up (RECOVERY_REQUIRED) after the agent folder
    changed. The next start's recovery must now restore the copy instead of refusing again."""
    src = _installed(app, tmp_path)
    before = _without_log(app)
    _game_logs_at(monkeypatch, app, "backup-intent:agent")
    real_rollback = recovery.rollback

    def gave_up(*args: Any, **kwargs: Any) -> None:
        raise recovery.RecoveryError("cannot safely restore agent; preserve backups")

    monkeypatch.setattr(recovery, "rollback", gave_up)
    with pytest.raises(updater.RollbackError):
        updater.apply(str(app), str(src), lambda text: None)
    stuck = recovery.load(str(app))
    assert stuck is not None and stuck["state"] == "RECOVERY_REQUIRED"
    assert (app / "_bz2.pyd").read_bytes() == b"new pyd"            # a mixed copy, as the player had
    monkeypatch.setattr(recovery, "rollback", real_rollback)
    def owner_gone(pid: int, stamp: int) -> bool:
        return False

    monkeypatch.setattr(recovery, "owner_alive", owner_gone)
    assert recovery.recover(str(app)) == "ROLLED_BACK"
    report = updater.json.loads((app / "update/result.json").read_bytes())
    assert report["ok"] is False and "previous version restored" in report["error"]
    (app / "update/result.json").unlink()
    assert _without_log(app) == before


@pytest.fixture
def game() -> Iterator[subprocess.Popen[bytes]]:
    """A live process standing in for a game (its agent's port file is written by each test)."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield process
    finally:
        process.kill()
        process.wait()


def _port_file(folder: Path, pid: int) -> Path:
    path = folder / "agent/run" / ("agent.%d.port" % pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("17311 %d secret" % pid, encoding="utf-8")
    return path


def test_a_running_agent_stops_the_update_before_anything_is_copied(
        app: Path, tmp_path: Path, game: subprocess.Popen[bytes]) -> None:  # noqa: F811
    src = _installed(app, tmp_path)
    time.sleep(0.1)                         # the agent writes its port file after its game started
    _port_file(app, game.pid)
    before = snapshot(app)
    assert updater.agent_games(str(app)) == [game.pid]
    with pytest.raises(updater.UpdateError, match="Close every Mist World window") as caught:
        updater.apply(str(app), str(src), lambda text: None)
    assert str(game.pid) in str(caught.value)
    assert snapshot(app) == before


def test_left_over_port_files_do_not_block_updates(app: Path, tmp_path: Path,  # noqa: F811
                                                    game: subprocess.Popen[bytes]) -> None:
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    _port_file(app, ended.pid)              # a game that crashed and left its file behind
    reused = _port_file(app, game.pid)      # a file older than the process now holding that PID
    old = time.time() - 3600
    os.utime(reused, (old, old))
    assert updater.agent_games(str(app)) == []
    (app / "agent/run/agent.junk.port").write_text("not a port file", encoding="utf-8")
    assert updater.agent_games(str(app)) == []


def test_installed_files_in_use_are_not_flushed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A DLL a closing app still has loaded can't be opened for writing: the plan skips it
    (1.0.1 -> 1.2.0 failed once on python314.dll), while staged and backup copies still must flush."""
    folder = tmp_path / "entry"
    folder.mkdir()
    (folder / "python314.dll").write_bytes(b"loaded")
    (folder / "other.pyd").write_bytes(b"free")
    flushed: list[str] = []

    def flush(path: str) -> None:
        if path.endswith("python314.dll"):
            raise PermissionError(13, "Permission denied", path)
        flushed.append(os.path.basename(path))

    monkeypatch.setattr(recovery, "flush", flush)
    recovery.durable_tree(str(folder), in_use_ok=True)
    assert flushed == ["other.pyd"]
    with pytest.raises(PermissionError):
        recovery.durable_tree(str(folder))
