# pyright: reportPrivateUsage=false
# White-box tests: _open is replaced by a fake GitHub (a leading-underscore name of the updater).
"""Release channels (updater.py): Master reads GitHub's latest stable release, Dev the newest CI
pre-release of the dev branch, and neither ever takes the other's. Also the setting itself, the
built copy's own version (build.json) and how versions of both channels compare. No network."""

from __future__ import annotations

import io
import json
import os
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import __version__, settings, updater

BASE = ".".join(str(n) for n in updater.version_tuple(__version__)[:3])


def asset(name: str, **over: Any) -> dict[str, Any]:
    a: dict[str, Any] = {"name": name, "size": 10, "digest": "sha256:" + "ab" * 32, "state": "uploaded",
                         "browser_download_url": "https://dl/" + name, "url": "https://api/assets/" + name}
    a.update(over)
    return a


def stable(version: str, **over: Any) -> dict[str, Any]:
    rel: dict[str, Any] = {"tag_name": "v" + version, "body": "stable notes", "draft": False, "prerelease": False,
                           "assets": [asset("UncleFarmer-%s.zip" % version)]}
    rel.update(over)
    return rel


def dev(version: str, **over: Any) -> dict[str, Any]:
    rel: dict[str, Any] = {"tag_name": "v" + version, "body": "dev notes", "draft": False, "prerelease": True,
                           "assets": [asset("UncleFarmer-%s.zip" % version),
                                      asset("UncleFarmer-%s.zip.sha256" % version)]}
    rel.update(over)
    return rel


class GitHub:
    """A pretend GitHub API: the latest stable release at LATEST, the release list at RELEASES."""

    def __init__(self, latest: Any = None, releases: Any = None) -> None:
        self.latest: Any = latest
        self.releases: Any = releases if releases is not None else []
        self.asked: list[str] = []

    def open(self, url: str, accept: str, timeout: float) -> io.BytesIO:
        self.asked.append(url)
        answer: Any
        if url == updater.LATEST % updater.REPO:
            answer = self.latest
        elif url == updater.RELEASES % (updater.REPO, updater.DEV_PAGE):
            answer = self.releases
        else:
            raise AssertionError("asked an unknown address: " + url)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)    # type: ignore[arg-type]
        return io.BytesIO(json.dumps(answer).encode())


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> GitHub:
    gh = GitHub()
    monkeypatch.setattr(updater, "_open", gh.open)
    monkeypatch.setattr(updater, "REPO", "owner/uncle-farmer")
    return gh


@pytest.fixture
def copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """This copy's folder, where build.json says which build it is."""
    monkeypatch.setattr(updater.paths, "APP", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------- the setting
@pytest.mark.parametrize("value, want", [(None, "master"), ("", "master"), ("beta", "master"), (1, "master"),
                                         ("master", "master"), ("dev", "dev")])
def test_the_channel_is_master_unless_dev_is_chosen(value: object, want: str) -> None:
    assert updater.channel(value) == want


def test_existing_settings_without_a_channel_are_on_master() -> None:
    with open(updater.paths.SETTINGS, "w", encoding="utf-8") as f:
        json.dump({"language": "en", "check_updates": True}, f)        # written by an older version
    assert settings.load_app()["update_channel"] == "master"
    assert settings.APP_DEFAULTS["update_channel"] == "master"


def test_dev_stays_chosen_after_a_restart() -> None:
    s = settings.load_app()
    s["update_channel"] = "dev"
    settings.save_app(s)
    assert settings.load_app()["update_channel"] == "dev"            # read again, as the next start does
    s["update_channel"] = "master"
    settings.save_app(s)
    assert settings.load_app()["update_channel"] == "master"


def test_an_unknown_channel_in_the_file_falls_back_to_master_and_keeps_the_rest() -> None:
    with open(updater.paths.SETTINGS, "w", encoding="utf-8") as f:
        json.dump({"update_channel": "nightly", "language": "ur"}, f)
    s = settings.load_app()
    assert s["update_channel"] == "master" and s["language"] == "ur"


# ---------------------------------------------------------------- master
def test_master_reads_the_latest_stable_release(github: GitHub) -> None:
    github.latest = stable("0.10.0")
    rel = updater.latest(update_channel="master")
    assert rel is not None and rel["version"] == "0.10.0" and rel["asset"] == "UncleFarmer-0.10.0.zip"
    assert github.asked == [updater.LATEST % updater.REPO]


def test_master_is_the_default_channel(github: GitHub) -> None:
    github.latest = stable("0.10.0")
    rel = updater.latest()
    assert rel is not None and rel["version"] == "0.10.0"
    assert github.asked == [updater.LATEST % updater.REPO]


@pytest.mark.parametrize("rel", [
    dev("0.10.0-dev.5"),                                   # a pre-release
    dev("0.10.0-dev.5", prerelease=False),                 # a dev build that wasn't marked pre-release
    stable("0.10.0", assets=[asset("UncleFarmer-0.10.0-dev.5.zip")]),    # only a dev zip on it
    stable("0.10.0", draft=True),
], ids=["pre-release", "dev tag", "dev asset", "draft"])
def test_master_never_takes_a_dev_build(github: GitHub, rel: dict[str, Any]) -> None:
    github.latest = rel
    assert updater.latest(update_channel="master") is None


def test_master_with_no_release_is_none(github: GitHub) -> None:
    assert updater.latest(update_channel="master") is None


# ---------------------------------------------------------------- dev
def test_dev_takes_the_newest_dev_build_and_nothing_else(github: GitHub) -> None:
    github.releases = [
        stable("0.11.0"),                                   # newer, but stable: not on this channel
        dev("0.10.0-dev.99", draft=True),
        dev("0.10.0-dev.98", assets=[asset("UncleFarmer-0.10.0-dev.98.zip", state="new")]),   # still uploading
        dev("0.10.0-dev.97", assets=[asset("UncleFarmer-0.10.0.zip")]),    # asset doesn't match the tag
        dev("0.10.0-dev.96", prerelease=False),
        dev("0.10.0-beta.1"),                               # a pre-release, but not a dev build
        dev("0.10.0-dev.9"),
        dev("0.10.0-dev.10"),                               # newer than dev.9 by number, not by text
        dev("0.9.0-dev.80"),
    ]
    rel = updater.latest(update_channel="dev")
    assert rel is not None
    assert rel["version"] == "0.10.0-dev.10" and rel["tag"] == "v0.10.0-dev.10"
    assert rel["asset"] == "UncleFarmer-0.10.0-dev.10.zip" and rel["sha256"] == "ab" * 32
    assert rel["url"] == "https://dl/UncleFarmer-0.10.0-dev.10.zip"
    assert github.asked == [updater.RELEASES % (updater.REPO, updater.DEV_PAGE)]


def test_dev_with_only_stable_releases_is_none(github: GitHub) -> None:
    github.releases = [stable("0.10.0"), stable("0.9.0")]
    assert updater.latest(update_channel="dev") is None


def test_dev_with_no_releases_at_all_is_none(github: GitHub) -> None:
    github.releases = None                                 # 404
    assert updater.latest(update_channel="dev") is None
    github.releases = []
    assert updater.latest(update_channel="dev") is None


@pytest.mark.parametrize("answer, match", [
    ({"message": "odd"}, "isn't a list"),
    (urllib.error.HTTPError("u", 500, "boom", {}, None), "500"),    # type: ignore[arg-type]
    (urllib.error.URLError("no dns"), "no dns"),
])
def test_a_failed_dev_check_is_an_update_error(github: GitHub, answer: Any, match: str) -> None:
    github.releases = answer
    with pytest.raises(updater.UpdateError, match=match):
        updater.latest(update_channel="dev")


def test_check_swallows_a_failed_dev_check_and_logs_it(github: GitHub) -> None:
    github.releases = urllib.error.URLError("offline")
    assert updater.check(update_channel="dev") is None
    with open(os.path.join(updater.paths.LOGS, "update.log"), encoding="utf-8") as f:
        assert "update check (dev) failed: offline" in f.read()


def test_no_repository_asks_nobody_on_either_channel(github: GitHub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "REPO", "")
    assert updater.latest(update_channel="dev") is None and updater.latest(update_channel="master") is None
    assert github.asked == []


# ---------------------------------------------------------------- switching
def test_switching_channels_switches_the_source(github: GitHub, copy: Path) -> None:
    github.latest = stable("99.0.0")
    github.releases = [dev("99.0.0-dev.3"), stable("99.0.0")]
    on_dev = updater.check(update_channel="dev")
    on_master = updater.check(update_channel="master")
    assert on_dev is not None and on_dev["version"] == "99.0.0-dev.3"
    assert on_master is not None and on_master["version"] == "99.0.0"
    assert github.asked == [updater.RELEASES % (updater.REPO, updater.DEV_PAGE), updater.LATEST % updater.REPO]


# ---------------------------------------------------------------- this copy and comparing
@pytest.mark.parametrize("newer, older", [
    ("0.9.0-dev.57", "0.9.0"), ("0.10.0", "0.9.0-dev.57"), ("0.9.0-dev.58", "0.9.0-dev.57"),
    ("0.9.0-dev.100", "0.9.0-dev.99"), ("1.0.0-dev.1", "0.99.0-dev.500"),
])
def test_dev_builds_come_after_their_release_and_before_the_next(newer: str, older: str) -> None:
    assert updater.is_newer(newer, older) and not updater.is_newer(older, newer)


def test_a_copy_without_build_json_is_its_own_version(copy: Path) -> None:
    assert updater.installed_version() == __version__


def test_a_dev_build_knows_its_build_number(copy: Path) -> None:
    updater.write_build(str(copy), BASE + "-dev.57", "dev", "0" * 40)
    assert updater.installed_version() == BASE + "-dev.57"
    with open(copy / updater.BUILD_FILE, encoding="utf-8") as f:
        assert json.load(f) == {"version": BASE + "-dev.57", "channel": "dev", "commit": "0" * 40}


def test_a_stable_build_says_its_version(copy: Path) -> None:
    updater.write_build(str(copy), __version__, "master")
    assert updater.installed_version() == __version__


@pytest.mark.parametrize("content", [
    json.dumps({"version": "0.0.1-dev.5"}),             # another version's leftover
    json.dumps({"version": "999.0.0"}),
    json.dumps({"version": "junk"}),
    "{broken", "[]",
])
def test_a_build_json_that_isnt_this_copys_counts_for_nothing(copy: Path, content: str) -> None:
    (copy / updater.BUILD_FILE).write_text(content, encoding="utf-8")
    assert updater.installed_version() == __version__


def test_a_dev_build_is_not_offered_itself_again(github: GitHub, copy: Path) -> None:
    updater.write_build(str(copy), BASE + "-dev.57", "dev")
    github.releases = [dev(BASE + "-dev.57")]
    assert updater.check(update_channel="dev") is None
    github.releases = [dev(BASE + "-dev.58"), dev(BASE + "-dev.57")]
    newer = updater.check(update_channel="dev")
    assert newer is not None and newer["version"] == BASE + "-dev.58"


def test_back_on_master_a_dev_build_waits_for_a_newer_stable_release(github: GitHub, copy: Path) -> None:
    updater.write_build(str(copy), BASE + "-dev.57", "dev")
    github.latest = stable(BASE)                           # the release this dev build came after
    assert updater.check(update_channel="master") is None
    major, minor = updater.version_tuple(BASE)[:2]
    github.latest = stable("%d.%d.0" % (major, minor + 1))
    newer = updater.check(update_channel="master")
    assert newer is not None and not updater.is_dev(newer["version"])


def test_the_channel_shapes_are_the_ci_builds() -> None:
    import ci_build
    assert ci_build.DEV_VERSION.pattern == updater.DEV_VERSION.pattern
    for v in ("0.9.0-dev.57", "10.20.30-dev.0"):
        assert updater.is_dev(v) and ci_build.DEV_TAG.match("v" + v)
    for v in ("0.9.0", "0.9.0-beta.1", "0.9.0-dev", "0.9-dev.1", "v0.9.0-dev.1"):
        assert not updater.is_dev(v)
