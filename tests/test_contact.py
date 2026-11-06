"""Help, Contact the developer: the address, the email it opens, and the menu entry; Help, Join the
Telegram group."""

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
    assert contact.TELEGRAM in text


def browser(monkeypatch: pytest.MonkeyPatch, works: bool) -> tuple[list[str], list[str]]:
    """(links the browser was asked to open, error messages shown)."""
    opened: list[str] = []
    errors: list[str] = []

    def open_url(url: str) -> bool:
        opened.append(url)
        return works

    def show_error(parent: object, msg: str) -> None:
        errors.append(msg)
    monkeypatch.setattr(gui_contact.webbrowser, "open", open_url)
    monkeypatch.setattr(gui_contact, "error", show_error)
    return opened, errors


def test_join_the_telegram_group_opens_its_invite_link(monkeypatch: pytest.MonkeyPatch) -> None:
    opened, errors = browser(monkeypatch, works=True)
    gui_contact.join_telegram(None)
    assert opened == ["https://t.me/+55NtoqsY7u1lMzNk"] == [contact.TELEGRAM] and errors == []


def test_with_no_browser_the_link_is_shown_to_type_in(monkeypatch: pytest.MonkeyPatch) -> None:
    _opened, errors = browser(monkeypatch, works=False)
    gui_contact.join_telegram(None)
    assert len(errors) == 1 and contact.TELEGRAM in errors[0]


def test_the_help_menu_entry_joins_the_telegram_group(monkeypatch: pytest.MonkeyPatch) -> None:
    joined: list[object] = []

    def join(parent: object) -> None:
        joined.append(parent)
    monkeypatch.setattr(gui_contact, "join_telegram", join)
    frame = object()
    handler: Any = main.MainFrame.join_telegram
    handler(frame)
    assert joined == [frame]


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
        text = (lang / name).read_text(encoding="utf-8")
        assert contact.EMAIL not in text and contact.TELEGRAM not in text
