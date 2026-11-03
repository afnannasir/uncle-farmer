# pyright: reportPrivateUsage=false
# White-box tests: they call __main__'s _about and the update window's helpers directly.
"""The built UncleFarmer.exe knows it is built, and every module agrees (paths.frozen).

build_exe.py builds with Nuitka, which never sets sys.frozen: it puts __compiled__ in the
globals of each module it compiles (checked against a real Nuitka 4.3 standalone build with
build_exe.py's flags). The check used to be sys.frozen alone, so the built exe called itself
"from source" and never offered an update. Here __compiled__ is put into paths' globals by hand,
the way Nuitka has it there; nothing is built.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import __main__ as start, applog, paths, selftest, updater
from conftest import ROOT, failing, returning

PATHS_PY = os.path.join(ROOT, "unclefarmer", "paths.py")


@pytest.fixture
def source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running from the checkout: no Nuitka marker, no sys.frozen."""
    monkeypatch.delitem(vars(paths), "__compiled__", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch, source: None, tmp_path: Path) -> Path:
    """The built exe's view: Nuitka's marker in paths' globals, and its sys.executable (a python.exe
    beside UncleFarmer.exe) in a folder of its own. Returns that folder."""
    folder = tmp_path / "Uncle Farmer"
    folder.mkdir()
    monkeypatch.setitem(vars(paths), "__compiled__", object())
    monkeypatch.setattr(sys, "executable", str(folder / "python.exe"))
    return folder


def same(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# ------------------------------------------------------------ the one answer
@pytest.mark.usefixtures("source")
def test_from_source_it_is_not_frozen() -> None:
    assert paths.frozen() is False
    assert updater.frozen() is False


@pytest.mark.usefixtures("built")
def test_nuitkas_marker_makes_it_frozen_without_sys_frozen() -> None:
    # the original bug: the built exe has no sys.frozen, and was taken for a source checkout
    assert not hasattr(sys, "frozen")
    assert paths.frozen() is True
    assert updater.frozen() is True


@pytest.mark.usefixtures("built")
def test_a_false_sys_frozen_does_not_override_nuitkas_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert paths.frozen() is True


@pytest.mark.usefixtures("source")
def test_sys_frozen_from_another_build_tool_still_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert paths.frozen() is True
    assert updater.frozen() is True


@pytest.mark.usefixtures("source")
def test_the_environment_and_working_folder_do_not_make_a_build(monkeypatch: pytest.MonkeyPatch,
                                                                tmp_path: Path) -> None:
    (tmp_path / "UncleFarmer.exe").write_bytes(b"")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NUITKA_ONEFILE_PARENT", "1")
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "UncleFarmer.exe")])
    assert paths.frozen() is False


# ------------------------------------------------------------ the app folder
@pytest.mark.usefixtures("source")
def test_from_source_the_app_folder_is_the_checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert same(paths.app_folder(), ROOT)
    assert same(paths.APP, ROOT)
    monkeypatch.chdir(tmp_path)
    assert same(paths.app_folder(), ROOT)


def test_in_a_build_the_app_folder_is_the_exes_wherever_it_starts(built: Path, monkeypatch: pytest.MonkeyPatch,
                                                                  tmp_path: Path) -> None:
    assert same(paths.app_folder(), built)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert same(paths.app_folder(), built)


def test_a_build_keeps_every_folder_and_file_beside_the_exe(built: Path, monkeypatch: pytest.MonkeyPatch,
                                                            tmp_path: Path) -> None:
    """paths.py run as the built exe loads it (its marker in its globals), started from elsewhere:
    settings, profiles, plans, scripts, logs, data and the agent keep their names, beside the exe."""
    monkeypatch.chdir(tmp_path)
    loaded = runpy.run_path(PATHS_PY, init_globals={"__compiled__": object()})
    assert same(loaded["APP"], built)
    for name, rel in (("DATA", "data"), ("LANG", "lang"), ("DOCS", "docs"), ("AGENT_JAR", "agent/unclefarmer-agent.jar"),
                      ("PROFILES", "profiles"), ("PLANS", "plans"), ("SCRIPTS", "scripts"), ("LOGS", "logs"),
                      ("SETTINGS", "settings.json")):
        assert same(loaded[name], built / rel), name
    for made in ("profiles", "plans", "scripts", "logs"):
        assert (built / made).is_dir()
    assert not (tmp_path / "profiles").exists()      # nothing in the working folder


# ------------------------------------------------------------ what uses the answer
def test_the_startup_log_names_the_kind_of_copy(monkeypatch: pytest.MonkeyPatch, source: None) -> None:
    assert "from source" in start._about()
    monkeypatch.setitem(vars(paths), "__compiled__", object())
    assert "packaged build" in start._about()


@pytest.mark.usefixtures("built")
def test_the_self_test_of_a_build_says_frozen_and_needs_the_agent(tmp_path: Path) -> None:
    assert dict((name, required) for name, _check, required in selftest.checks())["game agent"] is True
    report = tmp_path / "report.json"
    selftest.run(str(report))
    assert '"frozen": true' in report.read_text(encoding="utf-8")


# ------------------------------------------------------------ the update window
class Window:
    """A stand-in main window for the update part; the rest is MainFrame's own."""

    def __init__(self) -> None:
        self.s: dict[str, Any] = {}
        self.updating = False
        self.runs: dict[int, Any] = {}
        self.destroyed = False
        self.downloads: list[updater.Release] = []
        self.status: list[str] = []

    def __getattr__(self, name: str) -> Any:
        from unclefarmer.gui import main
        attr = getattr(main.MainFrame, name)
        return attr.__get__(self) if callable(attr) and hasattr(attr, "__get__") else attr

    def _download_update(self, rel: updater.Release) -> None:
        self.downloads.append(rel)

    def SetStatusText(self, text: str) -> None:
        self.status.append(text)


class Started:
    """threading.Thread for the update check, run at once on start()."""

    def __init__(self, target: Any, daemon: bool = False) -> None:
        self.target = target

    def start(self) -> None:
        self.target()


REL: updater.Release = {"version": "99.0.0", "tag": "v99.0.0", "notes": "", "asset": "UncleFarmer-99.0.0.zip",
                        "size": 1, "url": "u", "sha256": ""}


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """The update window's messages and start notes, caught; no real dialog, thread or network."""
    pytest.importorskip("wx")
    from unclefarmer.gui.mainparts import updates
    said: dict[str, list[str]] = {"info": [], "error": [], "notes": [], "checked": []}

    def info(parent: object, message: str, title: str | None = None) -> None:
        said["info"].append(message)

    def error(parent: object, message: str, title: str | None = None) -> None:
        said["error"].append(message)

    def note(text: str, level: str = "INFO") -> None:
        said["notes"].append(text)

    def check(timeout: float = 10) -> updater.Release | None:
        said["checked"].append("yes")
        return None                       # no newer release, or GitHub not reached: check() says None for both
    monkeypatch.setattr(updates, "info", info)
    monkeypatch.setattr(updates, "error", error)
    monkeypatch.setattr(applog.bootup, "note", note)
    monkeypatch.setattr(updates.threading, "Thread", Started)
    monkeypatch.setattr(updater, "take_result", returning(None))
    monkeypatch.setattr(updater, "clean", returning(None))
    monkeypatch.setattr(updater, "check", check)
    monkeypatch.setattr(updates.agentfix, "running_pids", returning([]))
    return said


@pytest.mark.usefixtures("built")
def test_a_build_looks_for_updates_at_start(window: dict[str, list[str]]) -> None:
    Window().after_update()
    assert window["checked"] == ["yes"]
    assert not any("source" in n for n in window["notes"])
    assert "update check running in the background" in window["notes"]


@pytest.mark.usefixtures("source")
def test_a_source_copy_says_so_and_never_asks_github(window: dict[str, list[str]]) -> None:
    Window().after_update()
    assert window["checked"] == []
    assert "no update check: this copy runs from source" in window["notes"]


@pytest.mark.usefixtures("built")
def test_a_build_installs_the_update(window: dict[str, list[str]]) -> None:
    w = Window()
    w.install_update(REL)
    assert w.downloads == [REL]
    assert window["info"] == []


@pytest.mark.usefixtures("source")
def test_a_source_copy_is_told_to_use_git_and_downloads_nothing(window: dict[str, list[str]]) -> None:
    w = Window()
    w.install_update(REL)
    assert w.downloads == []
    assert window["info"] == ["This copy runs from its source code: update it with git pull."]


@pytest.mark.usefixtures("built")
def test_a_build_that_cannot_reach_github_says_that_not_source(window: dict[str, list[str]],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "latest", failing("the message is about the error handed in"))
    Window()._checked_updates(None, updater.UpdateError("timed out"))
    assert len(window["error"]) == 1 and "Couldn't reach GitHub" in window["error"][0]
    assert not any("source" in m for m in window["info"] + window["error"])
