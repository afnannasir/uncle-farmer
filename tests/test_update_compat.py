# pyright: reportPrivateUsage=false
"""Updates between versions: journals stay readable by older and newer builds.

Two builds share every update. The running app begins the journal, and the NEW release's copier
continues it. If something goes wrong, the copy's rescue runtime recovers it. That runtime is
published once and never updated, so it is often 1.2.0's code. A copier that wanted its own
"protected" list exactly once refused every 1.2.0 app's journal, so every update rolled back.

tests/data/compat/update_recovery_1_2_0.py.txt is update_recovery.py exactly as tag v1.2.0 has it,
the code inside every 1.2.0 rescue runtime. Never edit it: add the next frozen version beside it."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import updater
from test_updater_hardening import app as app, flat  # noqa: F401  (app is a fixture)

COMPAT = Path(__file__).parent / "data" / "compat"


def frozen_recovery(version: str) -> Any:
    """A released update_recovery.py, loaded as its own module."""
    source = (COMPAT / ("update_recovery_%s.py.txt" % version.replace(".", "_"))).read_text(encoding="utf-8")
    name = "update_recovery_" + version.replace(".", "_")
    spec = importlib.util.spec_from_loader(name, loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # dataclasses and typing look the module up by name
    exec(compile(source, str(COMPAT / name), "exec"), module.__dict__)
    return module


@pytest.fixture(scope="module")
def v1_2_0() -> types.ModuleType:
    return frozen_recovery("1.2.0")


def test_the_journal_stamp_is_the_one_1_2_0_writes(v1_2_0: Any) -> None:
    assert sorted(updater.recovery.JOURNAL_STAMP) == v1_2_0.protected_names()


@pytest.mark.parametrize("fails", [False, True])
def test_every_journal_this_build_writes_is_readable_by_a_1_2_0_rescue_runtime(
        app: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, v1_2_0: Any, fails: bool) -> None:  # noqa: F811
    """At every journal write of a whole update (and of a rollback), the 1.2.0 code can load it."""
    flat(app, "old")
    src = flat(tmp_path / "release")
    seen: list[str] = []
    original = updater.recovery.checkpoint

    def check(operation: str) -> None:
        journal = v1_2_0.load(str(app))        # raises if the 1.2.0 rescue runtime would refuse it
        if journal is not None:
            seen.append(journal["state"])
        original(operation)

    monkeypatch.setattr(updater.recovery, "checkpoint", check)
    if fails:
        copy, calls = updater.shutil.copy2, [0]

        def failing(source: Any, target: Any, **kwargs: Any) -> Any:
            calls[0] += 1
            if calls[0] == 3:
                raise OSError("injected copy failure")
            return copy(source, target, **kwargs)

        monkeypatch.setattr(updater.shutil, "copy2", failing)
        with pytest.raises((OSError, updater.UpdateError)):
            updater.apply(str(app), str(src), lambda text: None)
    else:
        updater.apply(str(app), str(src), lambda text: None)
    assert seen and seen[-1] == ("ROLLED_BACK" if fails else "COMMITTED")


def test_this_copier_continues_the_journal_a_1_2_0_app_began(app: Path, tmp_path: Path, v1_2_0: Any) -> None:  # noqa: F811
    """The real failure: a 1.2.0 app begins the journal while it downloads, then this build's
    copier carries the update out."""
    flat(app, "old")
    src = flat(tmp_path / "release")
    v1_2_0.begin(str(app), "0.4.0", "0.5.0", "DOWNLOADING")
    updater.apply(str(app), str(src), lambda text: None)
    journal = updater.recovery.load(str(app))
    assert journal is not None and journal["state"] == "COMMITTED"


@pytest.mark.parametrize("stamp", [
    ["agent/run", ".recovery", "bootstrap.exe", "data/game_texts.json", "logs", "market", "messages", "plans",
     "profiles", "results", "schedules.json", "scripts", "settings.json", "survey", "update"],   # dev.364-.370
    [".recovery", "agent/run", "data/game_texts.json", "logs", "messages", "plans", "profiles", "results",
     "schedules.json", "scripts", "settings.json", "survey", "unclefarmer.exe", "update"],       # front-door layout
    ["a-future-folder", "agent/run", ".recovery", "bootstrap.exe", "data/game_texts.json", "logs", "messages",
     "plans", "profiles", "results", "schedules.json", "scripts", "settings.json", "survey", "update"],  # later
])
def test_a_journal_another_build_stamped_is_continued(app: Path, stamp: list[str]) -> None:  # noqa: F811
    record = updater.recovery.begin(str(app), "1.2.0", "1.2.1", "DOWNLOADING")
    record["protected"] = stamp
    updater.recovery.save(str(app), record, "DOWNLOADING", "other-build")
    continued = updater.recovery.begin(str(app), "1.2.0", "1.2.1", "STAGING")
    assert continued["id"] == record["id"] and continued["state"] == "STAGING"


@pytest.mark.parametrize("stamp", [None, "logs", [], ["logs", "LOGS"], ["logs", 3], [""]])
def test_a_malformed_stamp_is_still_refused(app: Path, stamp: object) -> None:  # noqa: F811
    record = updater.recovery.begin(str(app), "1.2.0", "1.2.1")
    record["protected"] = stamp
    with pytest.raises(updater.recovery.RecoveryError, match="preservation rules"):
        updater.recovery.validate(record, str(app))
