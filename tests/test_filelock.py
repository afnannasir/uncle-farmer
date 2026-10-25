"""User data file locks: an open handle blocks deletion on Windows."""

import os
import sys

import pytest

from unclefarmer import filelock, jsonio, paths

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows file sharing")


@pytest.fixture(autouse=True)
def release_locks():
    yield
    filelock.release_all()


def test_protected_paths_are_the_user_files():
    assert filelock.is_protected(paths.SETTINGS)
    assert filelock.is_protected(os.path.join(paths.PLANS, "plan.json"))
    assert filelock.is_protected(os.path.join(paths.PROFILES, "character.json"))
    assert filelock.is_protected(os.path.join(paths.SCRIPTS, "plan.mwf"))
    assert filelock.is_protected(os.path.join(paths.APP, "results", "character.jsonl"))
    assert filelock.is_protected(os.path.join(paths.APP, "messages", "replies.txt"))
    assert not filelock.is_protected(os.path.join(paths.LOGS, "day.log"))
    assert not filelock.is_protected(os.path.join(paths.APP, "data", "knowledge.json"))


def test_an_open_handle_blocks_deletion(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    assert filelock.acquire(str(path))
    with pytest.raises(PermissionError):
        path.unlink()
    assert filelock.release(str(path))
    path.unlink()


def test_save_releases_the_handle_and_takes_it_again(tmp_path, monkeypatch):
    path = tmp_path / "plan.json"
    path.write_text("old", encoding="utf-8")
    monkeypatch.setattr(filelock, "is_protected", lambda _path: True)
    assert filelock.acquire(str(path))
    jsonio.save_text(str(path), "new")
    assert path.read_text(encoding="utf-8") == "new"
    with pytest.raises(PermissionError):
        path.unlink()
    assert filelock.release(str(path))
    path.unlink()


def test_delete_releases_before_removing(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text("{}", encoding="utf-8")
    assert filelock.acquire(str(path))
    filelock.delete(str(path))
    assert not path.exists()
