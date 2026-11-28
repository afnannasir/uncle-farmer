"""Tools, Buy collection items, the window (gui/buycollection.py): it shows the character's settings,
saves them with the day's count, and won't check or start with nothing to buy."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import settings
from unclefarmer.engine import buycollection as bc

wx = pytest.importorskip("wx")


@pytest.fixture
def frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setattr(bc, "_path", lambda: str(tmp_path / bc.BOUGHT_FILE))
    app = wx.GetApp() or wx.App(False)
    try:
        window = wx.Frame(None)
    except Exception as e:                      # no desktop to make windows on
        pytest.skip("no windows here: %s" % e)
    yield window
    window.Destroy()
    app.ProcessPendingEvents()


def test_the_window_shows_and_saves_the_settings(frame: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer.gui.buycollection import BuyCollectionDialog
    from unclefarmer.gui.crafting import START
    p = settings.load_profile("Hero")
    p["buy_collection"] = {"items": ["Milkfish", "Odd Pearl"], "max_price": 120, "daily": 300,
                           "sell_after": True, "when_heavy": "sell"}
    settings.save_profile("Hero", p)
    bc.set_bought_today("Hero", 40)
    d = BuyCollectionDialog(frame, "Hero")
    codes: list[int] = []
    d.EndModal = codes.append                 # type: ignore[method-assign]
    try:
        names = d.names
        assert d.item_list.IsChecked(names.index("Milkfish"))
        assert d.others.GetValue() == "Odd Pearl"            # not in the data: kept as typed
        assert (d.max_price.GetValue(), d.daily.GetValue(), d.today.GetValue()) == (120, 300, 40)
        assert d.sell_after.GetValue() and d.when_heavy.GetSelection() == 1
        d.item_list.Check(names.index("Red Shrimp"), True)
        d.others.SetValue("Odd Pearl, milkfish")              # a ticked one typed again: once
        d.today.SetValue(55)
        d.done(START)
    finally:
        d.Destroy()
    assert codes == [START]
    saved = settings.load_profile("Hero")["buy_collection"]
    assert saved["items"] == ["Milkfish", "Red Shrimp", "Odd Pearl"]
    assert bc.bought_today("Hero") == 55


def test_nothing_to_buy_is_refused_for_check_and_start_but_saves(frame: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer.gui import buycollection as gui
    from unclefarmer.gui.crafting import CHECK
    told: list[str] = []
    def box(text: str, *_a: object, **_k: object) -> int:
        told.append(text)
        return int(wx.OK)
    monkeypatch.setattr(gui.wx, "MessageBox", box)
    d = gui.BuyCollectionDialog(frame, "Hero")
    codes: list[int] = []
    d.EndModal = codes.append                 # type: ignore[method-assign]
    try:
        for i in range(d.item_list.GetCount()):
            d.item_list.Check(i, False)
        d.others.SetValue("")
        d.done(CHECK)
        assert codes == [] and told
        d.done(wx.ID_OK)
        assert codes == [wx.ID_OK]
    finally:
        d.Destroy()


def test_every_shortcut_letter_is_used_once(frame: Any) -> None:
    from unclefarmer.gui.buycollection import BuyCollectionDialog
    d = BuyCollectionDialog(frame, "Hero")
    try:
        texts: list[str] = []

        def walk(w: Any) -> None:
            for c in w.GetChildren():
                if isinstance(c, (wx.CheckBox, wx.StaticText, wx.Button)):
                    texts.append(c.GetLabel())
                walk(c)
        walk(d)
        letters = [t.replace("&&", "")[t.replace("&&", "").index("&") + 1].lower() for t in texts if "&" in t]
        assert len(letters) == len(set(letters)), sorted(letters)
    finally:
        d.Destroy()

