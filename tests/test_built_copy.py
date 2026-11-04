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
from collections.abc import Callable
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

    def __init__(self, target: Any, daemon: bool = False, kwargs: dict[str, Any] | None = None) -> None:
        self.target, self.kwargs = target, kwargs or {}

    def start(self) -> None:
        self.target(**self.kwargs)


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

    def check(timeout: float = 10, update_channel: str = "master") -> updater.Release | None:
        said["checked"].append(update_channel)
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
    assert window["checked"] == ["master"]
    assert not any("source" in n for n in window["notes"])
    assert "update check (master channel) running in the background" in window["notes"]


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


# ------------------------------------------------------------ release channels in the window
@pytest.mark.usefixtures("built")
def test_the_start_check_asks_the_chosen_channel(window: dict[str, list[str]]) -> None:
    w = Window()
    w.s["update_channel"] = "dev"
    w.after_update()
    assert window["checked"] == ["dev"]
    assert "update check (dev channel) running in the background" in window["notes"]


@pytest.mark.usefixtures("built")
def test_an_unknown_channel_checks_master(window: dict[str, list[str]]) -> None:
    w = Window()
    w.s["update_channel"] = "nightly"
    w.after_update()
    assert window["checked"] == ["master"]


@pytest.mark.usefixtures("built")
def test_the_help_menu_check_asks_the_chosen_channel(window: dict[str, list[str]],
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def latest(timeout: float = 10, update_channel: str = "master") -> updater.Release | None:
        asked.append(update_channel)
        return None
    monkeypatch.setattr(updater, "latest", latest)
    w = Window()
    w.s["update_channel"] = "dev"
    from unclefarmer.gui.mainparts import updates

    def append(self: Window, text: str, keep: bool) -> None:
        pass

    def call_after(fn: Callable[..., object], *args: object) -> None:
        fn(*args)
    monkeypatch.setattr(Window, "_append", append, raising=False)
    monkeypatch.setattr(updates.wx, "CallAfter", call_after)
    w.check_updates()
    assert asked == ["dev"]
    assert window["info"] == ["No development build of Uncle Farmer can be seen on GitHub yet."]


@pytest.mark.usefixtures("built")
def test_no_dev_build_yet_says_so(window: dict[str, list[str]]) -> None:
    w = Window()
    w.s["update_channel"] = "dev"
    w._checked_updates(None, None)
    assert window["info"] == ["No development build of Uncle Farmer can be seen on GitHub yet."]


@pytest.mark.usefixtures("built")
def test_back_on_master_a_dev_build_is_told_why_nothing_is_offered(window: dict[str, list[str]],
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "installed_version", returning("0.9.0-dev.57"))
    w = Window()
    w._checked_updates({**REL, "version": "0.9.0"}, None)
    assert len(window["info"]) == 1 and "development build 0.9.0-dev.57" in window["info"][0]
    assert w.downloads == []


@pytest.mark.usefixtures("built")
def test_a_dev_build_is_offered_as_one(window: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer.gui.mainparts import updates
    asked: list[str] = []
    def ask(parent: object, text: str, title: str | None = None) -> bool:
        asked.append(text)
        return True
    monkeypatch.setattr(updates, "ask", ask)
    monkeypatch.setattr(updater, "installed_version", returning("0.9.0-dev.57"))
    w = Window()
    w.s["update_channel"] = "dev"
    rel: updater.Release = {**REL, "version": "0.9.0-dev.58", "notes": "the notes"}
    w._checked_updates(rel, None)
    assert len(asked) == 1 and "0.9.0-dev.58 is out (you have 0.9.0-dev.57)" in asked[0]
    assert "development build from the dev branch" in asked[0] and "the notes" in asked[0]
    assert w.downloads == [rel]
