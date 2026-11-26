"""A character whose server can't be read takes its one saved profile with a server.

A profiles folder copied to another PC holds Hero--S1--42.json; when that PC's bot can't read the
game's server (a port it doesn't know), the key named only the id and the run started from the
defaults beside the complete file.
"""
from __future__ import annotations

import os

from unclefarmer import paths, settings


def _save(name: str, entity_id: int, server: str, **values: object) -> str:
    key = settings.character_profile(name, entity_id, server)
    p = settings.load_profile(key)
    p.update(values)
    settings.save_profile(key, p)
    return key


def test_the_one_profile_with_a_server_is_taken() -> None:
    saved = _save("Hero", 42, "S1", drop_keep_minutes=10)
    key = settings.live_profile("Hero", 42, "")
    assert key == saved
    assert settings.load_profile(key)["drop_keep_minutes"] == 10


def test_it_wins_over_an_id_only_file_made_from_the_defaults() -> None:
    saved = _save("Hero", 42, "S1", drop_keep_minutes=10)
    _save("Hero", 42, "", drop_keep_minutes=0)
    assert settings.live_profile("Hero", 42, "") == saved


def test_a_read_server_is_kept() -> None:
    _save("Hero", 42, "S1")
    assert settings.live_profile("Hero", 42, "S2") == settings.character_profile("Hero", 42, "S2")


def test_one_per_server_stays_by_id() -> None:
    _save("Hero", 42, "S1")
    _save("Hero", 42, "S2")
    assert settings.live_profile("Hero", 42, "") == settings.character_profile("Hero", 42)


def test_nothing_saved_stays_by_id() -> None:
    assert settings.live_profile("Hero", 42, "") == settings.character_profile("Hero", 42)


def test_another_character_or_id_is_not_taken() -> None:
    _save("Hero", 7, "S1")
    _save("Heroine", 42, "S1")
    assert settings.live_profile("Hero", 42, "") == settings.character_profile("Hero", 42)


def test_a_file_without_identity_is_not_taken() -> None:
    with open(os.path.join(paths.PROFILES, "Hero--S1--42.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    assert settings.live_profile("Hero", 42, "") == settings.character_profile("Hero", 42)


def test_no_id_stays_the_name() -> None:
    assert settings.live_profile("Hero", None, "") == "Hero"
