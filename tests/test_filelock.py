"""User data file locks: an open handle blocks deletion on Windows."""

from __future__ import annotations

import os
import sys

import pytest

from unclefarmer import filelock, jsonio, paths
from pathlib import Path
from conftest import stand_in
from collections.abc import Iterator

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing")


@pytest.fixture(autouse=True)
def release_locks() -> Iterator[None]:
    yield
    filelock.release_all()


def test_protected_paths_are_the_user_files() -> None:
    assert filelock.is_protected(paths.SETTINGS)
    assert filelock.is_protected(os.path.join(paths.PLANS, "plan.json"))
    assert filelock.is_protected(os.path.join(paths.PROFILES, "character.json"))
    assert filelock.is_protected(os.path.join(paths.SCRIPTS, "plan.mwf"))
    assert filelock.is_protected(os.path.join(paths.APP, "results", "character.jsonl"))
    assert filelock.is_protected(os.path.join(paths.APP, "messages", "replies.txt"))
    assert not filelock.is_protected(os.path.join(paths.LOGS, "day.log"))
    assert not filelock.is_protected(os.path.join(paths.APP, "data", "knowledge.json"))


def test_an_open_handle_blocks_deletion(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    assert filelock.acquire(str(path))
    with pytest.raises(PermissionError):
        path.unlink()
    assert filelock.release(str(path))
    path.unlink()


def test_save_releases_the_handle_and_takes_it_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "plan.json"
    path.write_text("old", encoding="utf-8")
    monkeypatch.setattr(filelock, "is_protected", stand_in(filelock.is_protected, lambda path: True))
    assert filelock.acquire(str(path))
    jsonio.save_text(str(path), "new")
    assert path.read_text(encoding="utf-8") == "new"
    with pytest.raises(PermissionError):
        path.unlink()
    assert filelock.release(str(path))
    path.unlink()


def test_delete_releases_before_removing(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_text("{}", encoding="utf-8")
    assert filelock.acquire(str(path))
    filelock.delete(str(path))
    assert not path.exists()


def test_a_missing_file_is_not_held(tmp_path: Path) -> None:
    assert filelock.acquire(str(tmp_path / "nope.json")) is False
    assert filelock.release(str(tmp_path / "nope.json")) is False


def test_holding_twice_is_one_handle(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_text("{}", encoding="utf-8")
    assert filelock.acquire(str(path)) and filelock.acquire(str(path))
    assert filelock.release(str(path)) is True
    assert filelock.release(str(path)) is False          # only one was held
    path.unlink()                                         # free again


def test_a_failed_delete_takes_the_handle_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "profile.json"
    path.write_text("{}", encoding="utf-8")
    filelock.acquire(str(path))

    def refuse(p: str) -> None:
        raise PermissionError("in use")
    monkeypatch.setattr(filelock.os, "remove", refuse)
    with pytest.raises(PermissionError):
        filelock.delete(str(path))
    monkeypatch.undo()
    with pytest.raises(PermissionError):                  # held again: still protected
        path.unlink()


def test_move_releases_moves_and_a_failed_move_takes_the_handle_back(tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    path, target = tmp_path / "a.json", tmp_path / "b.json"
    path.write_text("{}", encoding="utf-8")
    filelock.acquire(str(path))
    filelock.move(str(path), str(target))
    assert target.exists() and not path.exists()
    target.write_text("{}", encoding="utf-8")
    filelock.acquire(str(target))

    def refuse(a: str, b: str) -> None:
        raise PermissionError("in use")
    monkeypatch.setattr(filelock.os, "replace", refuse)
    with pytest.raises(PermissionError):
        filelock.move(str(target), str(path))
    monkeypatch.undo()
    with pytest.raises(PermissionError):
        target.unlink()


def test_the_start_holds_every_users_file_and_release_all_lets_go(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PLANS", "PROFILES", "SCRIPTS"):
        folder = tmp_path / name.lower()
        folder.mkdir()
        (folder / "one.json").write_text("{}", encoding="utf-8")
        (folder / "sub").mkdir()                          # a folder inside is never opened
        monkeypatch.setattr(paths, name, str(folder))
    monkeypatch.setattr(paths, "APP", str(tmp_path))      # results/ and messages/ missing: skipped
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paths, "SETTINGS", str(settings_file))
    filelock.acquire_startup()
    for path in (settings_file, tmp_path / "plans" / "one.json", tmp_path / "scripts" / "one.json"):
        with pytest.raises(PermissionError):
            path.unlink()
    filelock.release_all()
    settings_file.unlink()
    (tmp_path / "plans" / "one.json").unlink()
