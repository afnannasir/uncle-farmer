"""Help, Contact the developer: the address, the email it opens, and the menu entry."""

from __future__ import annotations

from typing import Any

import pytest

from unclefarmer import __version__, contact

wx = pytest.importorskip("wx")

from unclefarmer.gui import contact as gui_contact, main  # noqa: E402


def test_the_mailto_link_is_the_address_with_an_optional_subject() -> None:
    assert contact.EMAIL
    assert contact.mailto() == "mailto:" + contact.EMAIL
    assert contact.mailto("Uncle Farmer 1.0") == "mailto:%s?subject=Uncle%%20Farmer%%201.0" % contact.EMAIL


def test_the_window_says_where_to_write_and_where_bugs_go() -> None:
    text = gui_contact.message()
    assert contact.EMAIL in text and contact.ISSUES in text and "donate" in text


def test_write_an_email_opens_a_message_naming_this_version(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []

    def open_url(url: str) -> bool:
        opened.append(url)
        return True
    monkeypatch.setattr(gui_contact.webbrowser, "open", open_url)
    assert gui_contact.write_email() is True
    assert opened == [contact.mailto("Uncle Farmer %s" % __version__)]


def test_the_help_menu_opens_the_contact_window(monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[str] = []

    class Shown:
        def __init__(self, parent: object) -> None:
            shown.append("open")

        def __enter__(self) -> "Shown":
            return self

        def __exit__(self, *exc: object) -> None:
            shown.append("closed")

        def ShowModal(self) -> int:
            shown.append("shown")
            return 0
    monkeypatch.setattr(gui_contact, "ContactDialog", Shown)
    handler: Any = main.MainFrame.contact_developer
    handler(object())
    assert shown == ["open", "shown", "closed"]


def test_the_address_is_never_in_the_translations() -> None:
    from pathlib import Path
    lang = Path(__file__).resolve().parent.parent / "lang"
    for name in ("unclefarmer.pot", "ur.po"):
        assert contact.EMAIL not in (lang / name).read_text(encoding="utf-8")
