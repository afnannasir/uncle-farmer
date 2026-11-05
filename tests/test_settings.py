# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Settings and profiles: the defaults fill what a file is missing, unknown keys survive, and a
value of the wrong shape falls back to its default on its own (a copy of the file is kept aside)."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any, NoReturn

import pytest

from unclefarmer import paths, settings
from pathlib import Path


def test_app_settings_defaults_and_unknown_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "SETTINGS", str(tmp_path / "settings.json"))
    assert settings.load_app()["language"] == "en"
    with open(paths.SETTINGS, "w", encoding="utf-8") as f:
        json.dump({"language": "ru", "future_key": 7}, f)
    s = settings.load_app()
    assert s["language"] == "ru"
    assert s["future_key"] == 7          # a key this version doesn't know is kept
    assert s["speak"] is True


def test_app_settings_of_the_wrong_shape_are_kept_aside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "SETTINGS", str(tmp_path / "settings.json"))
    with open(paths.SETTINGS, "w", encoding="utf-8") as f:
        json.dump({"speak": "sometimes", "results_every": "soon", "language": "ru"}, f)
    s = settings.load_app()
    assert s["speak"] is True and s["results_every"] == 0
    assert s["language"] == "ru"         # the good values in the same file are kept
    assert os.path.exists(paths.SETTINGS + ".corrupt")
    assert os.path.exists(paths.SETTINGS)


def test_a_profile_of_the_wrong_shape_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    with open(tmp_path / "_bad_.json", "w", encoding="utf-8") as f:
        json.dump({"attack_gap": "wide", "chase_range": 25}, f)
    p = settings.load_profile("_bad_")
    assert p["attack_gap"] == settings.PROFILE_DEFAULTS["attack_gap"]
    assert p["chase_range"] == 25
    assert os.path.exists(str(tmp_path / "_bad_.json.corrupt"))


def test_the_defaults_pass_their_own_check() -> None:
    # live 2026-10-22: the pet potion defaults ("auto") failed the check, so every profile was
    # read as broken and the player's settings were replaced by the defaults
    settings.Profile.model_validate(settings.PROFILE_DEFAULTS)
    settings.AppSettings.model_validate(settings.APP_DEFAULTS)


def test_a_profile_with_auto_pet_potions_loads_as_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    with open(tmp_path / "pets.json", "w", encoding="utf-8") as f:
        json.dump({"pet_hp_below": "auto", "pet_hp_cooldown": "auto", "chase_range": 25}, f)
    p = settings.load_profile("pets")
    assert p["chase_range"] == 25 and p["pet_hp_below"] == "auto"
    assert not os.path.exists(str(tmp_path / "pets.json.corrupt"))


def test_a_locked_settings_file_is_moved_aside_and_the_save_goes_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "_unreadable", set[str]())
    p = tmp_path / "settings.json"
    p.write_text('{"language": "ru"}', encoding="utf-8")
    real_open: Callable[..., Any] = open

    def deny(file: str | os.PathLike[str], *a: Any, **k: Any) -> Any:
        if os.path.abspath(str(file)) == os.path.abspath(str(p)):
            raise PermissionError("in use")
        return real_open(file, *a, **k)

    monkeypatch.setattr("builtins.open", deny)
    assert settings._read(str(p), settings.APP_DEFAULTS, settings.AppSettings)["language"] == "en"
    assert os.path.exists(str(p) + ".corrupt")
    settings._write(str(p), {"language": "ru"})
    assert json.loads(p.read_text(encoding="utf-8"))["language"] == "ru"


def test_a_file_that_cannot_be_moved_aside_is_never_overwritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "_unreadable", set[str]())
    p = tmp_path / "settings.json"
    p.write_text('{"language": "ru"}', encoding="utf-8")
    real_open: Callable[..., Any] = open

    def deny(file: str | os.PathLike[str], *a: Any, **k: Any) -> Any:
        if os.path.abspath(str(file)) == os.path.abspath(str(p)):
            raise PermissionError("in use")
        return real_open(file, *a, **k)

    def locked(*_a: object, **_k: object) -> NoReturn:
        raise PermissionError("locked")

    monkeypatch.setattr("builtins.open", deny)
    monkeypatch.setattr(settings.os, "replace", locked)
    assert settings._read(str(p), settings.APP_DEFAULTS, settings.AppSettings)["language"] == "en"
    assert str(p) in settings._unreadable
    with pytest.raises(OSError, match="close whatever holds it"):
        settings._write(str(p), {"language": "en"})
    assert p.read_text(encoding="utf-8") == '{"language": "ru"}'


def test_every_profile_moves_onto_its_class_attack_range_once() -> None:
    # a range typed in by hand before the class's own distance became the rule goes back to auto;
    # one set after that is the player's and stays
    settings.save_profile("Old", dict(settings.PROFILE_DEFAULTS, attack_range=8.0))
    p = settings.load_profile("Old")
    assert p["attack_range"] == "auto" and p["class_reach"] is True
    p["attack_range"] = 12.0
    settings.save_profile("Old", p)
    assert settings.load_profile("Old")["attack_range"] == 12.0


def test_character_settings_export_round_trips_choices_but_not_game_facts(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path / "profiles"))
    original = dict(settings.PROFILE_DEFAULTS, attack_gap=0.7, bag_rules={"red potion": {"action": "keep", "keep": 4}},
                    scan={"class": "Hunter", "pets": ["Source pet"]}, hearth_home="Source town", future_key=7)
    settings.save_profile("Source", original)
    export = tmp_path / "source.unclefarmer-profile.json"
    settings.export_profile("Source", str(export))
    source, imported = settings.read_profile_export(str(export))
    assert source == "Source"
    assert imported["attack_gap"] == 0.7
    assert imported["bag_rules"] == {"red potion": {"action": "keep", "keep": 4}}
    assert imported["future_key"] == 7
    assert "scan" not in imported and "hearth_home" not in imported


def test_character_settings_import_keeps_destination_game_facts(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path / "profiles"))
    settings.save_profile("Destination", dict(settings.PROFILE_DEFAULTS, attack_gap=0.2,
                                                scan={"class": "Priest"}, hearth_home="Destination town"))
    settings.import_profile("Destination", dict(settings.PROFILE_DEFAULTS, attack_gap=0.8, scan={"class": "Hunter"},
                                                 hearth_home="Source town"))
    got = settings.load_profile("Destination")
    assert got["attack_gap"] == 0.8
    assert got["scan"] == {"class": "Priest"}
    assert got["hearth_home"] == "Destination town"


def test_plain_existing_profile_can_be_imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path / "profiles"))
    source_file = tmp_path / "okyanus.json"
    source_file.write_text(json.dumps(dict(settings.PROFILE_DEFAULTS, attack_gap=0.65,
                                           scan={"class": "Hunter"}, hearth_home="Source town")), encoding="utf-8")
    source, imported = settings.read_profile_export(str(source_file))
    assert source == "okyanus"
    assert imported["attack_gap"] == 0.65
    assert "scan" not in imported and "hearth_home" not in imported


def test_unrelated_plain_json_cannot_be_imported(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"language": "en", "speak": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="doesn't contain character settings"):
        settings.read_profile_export(str(path))


@pytest.mark.parametrize("raw, message", [
    ({}, "doesn't contain character settings"),
    ({"format": settings.PROFILE_EXPORT_FORMAT, "version": 99, "character": "A", "settings": {}}, "unsupported"),
    ({"format": settings.PROFILE_EXPORT_FORMAT, "version": 1, "character": "A", "settings": {"attack_gap": "wide"}},
     "invalid setting"),
])
def test_bad_character_settings_exports_are_rejected(tmp_path: Path, raw: dict[str, Any], message: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        settings.read_profile_export(str(path))
