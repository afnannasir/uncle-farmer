"""Named sound choices survive restarts and failed agent requests."""

import gzip
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import jsonio, settings, silencer
from unclefarmer.link import AgentError


class Link:
    def __init__(self, error: Exception | None = None, hooked: bool = True):
        self.commands: list[str] = []
        self.error = error
        self.hooked = hooked

    def json(self, line: str) -> Any:
        self.commands.append(line)
        if self.error:
            raise self.error
        return dict(hooked=self.hooked, reason="test", watch="off", mute=line.split(" ", 1)[-1],
                    observed=0, dropped=0, live_muted=0, unsupported=0)


@pytest.fixture
def app_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings.paths, "SETTINGS", str(path))
    return path


def row(sound_id: int = 7, name: str = "Fireball", muted: bool = True) -> dict[str, Any]:
    return dict(id=sound_id, name=name, muted=muted)


def test_save_and_reload_preserves_names_flags_and_other_settings(app_file: Path):
    settings.save_app(dict(language="ur", speak=False, unrelated={"keep": 1}))
    link = Link()
    silencer.save([row(name="  Fireball  "), row(8, "Bird", False)], link)
    assert silencer.load() == [row(), row(8, "Bird", False)]
    assert link.commands == ["soundmute 7"]
    app = settings.load_app()
    assert app["language"] == "ur" and app["speak"] is False and app["unrelated"] == {"keep": 1}
    assert app_file.exists()


@pytest.mark.parametrize("rows", [None, {}, [row(True)], [row(0)], [row(100000)], [row(), row()],
                                  [row(name="a\nb")], [row(name="x" * 101)], [row() | {"muted": 1}],
                                  [{"id": 7, "muted": True}], [row(name="a\x00b")]])
def test_invalid_rows_rejected_before_save(app_file: Path, rows: object):
    link = Link()
    with pytest.raises(ValueError):
        silencer.save(rows, link)
    assert not app_file.exists() and not link.commands


def test_renaming_reordering_and_removing_preserve_matching_id_data(app_file: Path):
    silencer.save([row(1, "First"), row(2, "Second", False)], Link())
    rows = silencer.load()
    rows[1]["name"] = "Renamed second"
    silencer.save(list(reversed(rows)), Link())
    assert silencer.load() == [row(2, "Renamed second", False), row(1, "First")]
    link = Link()
    silencer.save(silencer.load()[:1], link)
    assert link.commands == ["soundmute off"]


def test_cancelled_local_edits_do_not_persist(app_file: Path):
    silencer.save([row()], Link())
    before = app_file.read_bytes()
    pending = silencer.load()
    pending[0]["muted"] = False
    pending[0]["name"] = "Pending"
    assert app_file.read_bytes() == before
    assert silencer.load() == [row()]


@pytest.mark.parametrize("link", [Link(AgentError("closed")), Link(hooked=False)])
def test_agent_failure_keeps_saved_choices_and_reports_unconfirmed(app_file: Path, link: Link):
    with pytest.raises(AgentError, match="saved.*could not be confirmed"):
        silencer.save([row()], link)
    assert silencer.load() == [row()]


def test_disk_failure_does_not_apply_mute(app_file: Path, monkeypatch: pytest.MonkeyPatch):
    def fail(_data: dict[str, Any]) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(settings, "save_app", fail)
    link = Link()
    with pytest.raises(OSError, match="disk full"):
        silencer.save([row()], link)
    assert not link.commands and not app_file.exists()


def test_corrupt_sound_list_loads_empty_without_rewriting(app_file: Path, monkeypatch: pytest.MonkeyPatch):
    messages: list[str] = []
    def log(line: str, level: str = "INFO") -> None:
        messages.append(line)
    monkeypatch.setattr(silencer.applog, "write", log)
    jsonio.save(str(app_file), {"sound_silencer": [row(), row()]})
    before = app_file.read_bytes()
    assert silencer.load() == []
    assert app_file.read_bytes() == before
    assert messages and "sound list" in messages[0]


def test_apply_saved_replays_only_muted_ids(app_file: Path):
    silencer.save([row(7), row(8), row(10, muted=False)], Link())
    link = Link()
    silencer.apply_saved(link)
    assert link.commands == ["soundmute 7-8"]


def test_known_ids_assets_and_index_precedence(tmp_path: Path, app_file: Path, monkeypatch: pytest.MonkeyPatch):
    game = tmp_path / "game"
    assets = game / "sound"
    assets.mkdir(parents=True)
    for name in ("7.wav", "8.ogg", "0.wav", "100000.wav", "wrong.wav", "42"):
        (assets / name).write_bytes(b"sound")
    (assets / "9.wav").mkdir()
    def folder(pid: int | None, fallback: str = "") -> str:
        return str(game)
    monkeypatch.setattr(silencer.gameinstall, "folder_of", folder)
    assert silencer.known_ids(123) == {7, 8}
    with gzip.open(game / "sound-index.csv.gz", "wt", encoding="utf-8") as stream:
        stream.write("id,name\n11,Bell\n12,Clang\n-1,Invalid\n100000,Invalid\n")
    assert silencer.known_ids(123) == {11, 12}


def test_bad_index_is_not_silently_replaced_with_assets(tmp_path: Path, app_file: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "sound-index.csv.gz").write_bytes(b"broken")
    def folder(pid: int | None, fallback: str = "") -> str:
        return str(tmp_path)
    def log(line: str, level: str = "INFO") -> None:
        pass
    monkeypatch.setattr(silencer.gameinstall, "folder_of", folder)
    monkeypatch.setattr(silencer.applog, "write", log)
    assert silencer.known_ids(123) == set()


def test_cleared_names_survive_reopening_with_original_ids_and_mute_flags(app_file: Path):
    silencer.save([row(7, "First", True), row(8, "Second", False)], Link())
    draft = silencer.load()
    for entry in draft:
        entry["name"] = ""
    assert silencer.load()[0]["name"] == "First"
    link = Link()
    silencer.save(draft, link)
    assert silencer.load() == [row(7, "", True), row(8, "", False)]
    assert link.commands == ["soundmute 7"]
