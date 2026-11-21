# pyright: reportPrivateUsage=false
"""The shared GitHub changelog and the Help and update dialogs that display it."""

from __future__ import annotations

import io
import threading
import time
import urllib.error
from collections.abc import Iterator
from http.client import HTTPMessage
from types import SimpleNamespace
from typing import cast

import pytest
import wx

from conftest import returning
from unclefarmer import settings, updater
from unclefarmer.gui import changelog
from unclefarmer.gui.mainparts import updates

HISTORY = """# What's new

## 2.1.00
- A different release

## [v2.1.0] - 2026-11-21
### **Skills**
- Improved `casting`

## 2.0.0
- Older fixes
"""


class Response(io.BytesIO):
    def __init__(self, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
        super().__init__(body)
        self.headers = HTTPMessage()
        self.headers["Content-Type"] = content_type


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "_changelog_cache", None)


def test_successful_changelog_fetch_is_shared_until_it_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[str] = []

    def open_raw(url: str, accept: str, timeout: float) -> Response:
        assert "text/plain" in accept and timeout == 10
        requests.append(url)
        return Response(HISTORY.encode("utf-8"))

    monkeypatch.setattr(updater, "_open", open_raw)
    assert updater.read_changelog() == updater.read_changelog() == HISTORY.strip()
    url = "https://raw.githubusercontent.com/afnannasir/uncle-farmer/master/CHANGELOG.md"
    assert requests == [url]
    monkeypatch.setattr(updater, "_changelog_cache", (0.0, "expired"))
    assert updater.read_changelog() == HISTORY.strip()
    assert requests == [url, url]


@pytest.mark.parametrize(("body", "kind"), [(b"", "text/plain"), (b"<html>Not found</html>", "text/html"),
                                               (b"Not a changelog", "text/plain"), (b"\xff", "text/plain")])
def test_invalid_changelog_does_not_fill_the_cache(body: bytes, kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "_open", returning(Response(body, kind)))
    with pytest.raises(updater.UpdateError):
        updater.read_changelog()
    monkeypatch.setattr(updater, "_open", returning(Response(HISTORY.encode("utf-8"))))
    assert updater.read_changelog() == HISTORY.strip()


def test_truncated_changelog_is_an_update_error(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Response(HISTORY.encode("utf-8"))
    response.headers["Content-Length"] = str(len(response.getvalue()) + 100)
    monkeypatch.setattr(updater, "_open", returning(response))
    with pytest.raises(updater.UpdateError, match="incomplete"):
        updater.read_changelog()


@pytest.mark.parametrize("failure", [TimeoutError(), urllib.error.HTTPError(updater.CHANGELOG_URL, 404, "Not found",
                                                                           HTTPMessage(), None)])
def test_changelog_request_failure_is_an_update_error(failure: OSError, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: object) -> Response:
        raise failure

    monkeypatch.setattr(updater, "_open", fail)
    with pytest.raises(updater.UpdateError):
        updater.read_changelog()


def make_release(version: str, notes: str = "") -> updater.Release:
    return {"version": version, "notes": notes, "tag": "v" + version, "asset": "app.zip", "size": 0,
            "url": "", "sha256": ""}


@pytest.fixture
def frame() -> Iterator[wx.Frame]:
    app = wx.GetApp() or wx.App(False)
    parent = wx.Frame(None)
    yield parent
    parent.Destroy()
    app.ProcessPendingEvents()


@pytest.mark.parametrize(("version", "notes", "remote", "wanted", "absent"), [
    (None, "", HISTORY, "What's new", "###"),
    ("2.1.0", "Original notes", HISTORY, "Skills\n- Improved casting", "A different release"),
    ("2.1.0-dev.42", "# Dev notes\n- New build", HISTORY, "- New build", "Older fixes"),
    ("2.1.0-dev.42", "", HISTORY, "- Older fixes", "###"),
    ("2.1.0", "Existing release notes", None, "Couldn't load the changelog.", "Loading"),
    (None, "", None, "Couldn't load the changelog.", "Loading"),
])
def test_changelog_dialog_waits_without_blocking_and_shows_read_only_text(
        frame: wx.Frame, version: str | None, notes: str, remote: str | None, wanted: str, absent: str,
        monkeypatch: pytest.MonkeyPatch) -> None:
    started, respond = threading.Event(), threading.Event()

    def fetch() -> str:
        started.set()
        if not respond.wait(5) or remote is None:
            raise updater.UpdateError("offline")
        return remote

    monkeypatch.setattr(updater, "read_changelog", fetch)
    release = make_release(version, notes) if version is not None else None
    with changelog.ChangelogDialog(frame, release) as dialog:
        assert started.wait(2)
        assert dialog.text.IsMultiLine() and not dialog.text.IsEditable()
        assert "Loading the changelog" in dialog.text.GetValue()
        button = dialog.FindWindow(wx.ID_OK) if release is not None else dialog.FindWindow(wx.ID_CLOSE)
        assert button.IsEnabled() == (release is None)
        respond.set()
        app = wx.GetApp()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and wanted not in dialog.text.GetValue():
            app.ProcessPendingEvents()
            time.sleep(0.01)
        assert wanted in dialog.text.GetValue()
        assert absent not in dialog.text.GetValue()
        assert button.IsEnabled()
        if remote is None and notes:
            assert notes in dialog.text.GetValue()


@pytest.mark.parametrize(("at_start", "choice", "skipped"), [(True, wx.ID_CANCEL, True),
                                                             (False, wx.ID_CANCEL, False),
                                                             (True, wx.ID_OK, False)])
def test_update_confirmation_keeps_existing_install_and_skip_behavior(at_start: bool, choice: int, skipped: bool,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    installed: list[updater.Release] = []
    fake = SimpleNamespace(destroyed=False, s=settings.load_app(), install_update=installed.append)
    release = make_release("2.1.0")

    class Offer:
        def __init__(self, parent: object, received: updater.Release) -> None:
            assert parent is fake and received == release

        def __enter__(self) -> Offer:
            return self

        def __exit__(self, *_args: object) -> None:
            pass

        def ShowModal(self) -> int:
            return choice

    monkeypatch.setattr(updates, "ChangelogDialog", Offer)
    updates.UpdatesMixin.offer_update(cast(updates.UpdatesMixin, fake), release, at_start)
    assert installed == ([release] if choice == wx.ID_OK else [])
    assert (settings.load_app().get("skip_version") == release["version"]) == skipped
