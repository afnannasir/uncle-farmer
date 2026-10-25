"""Translation. Every user-visible string goes through _("English text").

The catalogs are gettext ones. Translators edit lang/<code>.po; `python -m unclefarmer --strings`
refreshes lang/unclefarmer.pot, merges the .po files with it and compiles
lang/<code>/LC_MESSAGES/unclefarmer.mo. At run time the app reads that .mo with gettext, and the
windows ask wx to load the same file (plus wx's own catalog, for standard buttons and menus), so
the two agree. English needs no file; a missing or broken catalog falls back to English.
"""

import gettext
import os
import struct
from typing import Any

from . import paths
from .catalog import DOMAIN, write_template as _write_template

_translation: gettext.NullTranslations = gettext.NullTranslations()
_wx_locale: Any = None          # wx.Locale, kept alive for the life of the app


def load(code: str) -> None:
    """Switch language. 'en' or a code with no catalog means English."""
    global _translation
    _translation = gettext.NullTranslations()
    if not code or code == "en":
        return
    try:
        _translation = gettext.translation(DOMAIN, paths.LANG, languages=[code], fallback=True)
    except (OSError, ValueError, struct.error):
        _translation = gettext.NullTranslations()   # a broken catalog: English, not a crash


def _(text: str, **values: Any) -> str:
    translated = _translation.gettext(text) or text
    if not values:
        return translated
    try:
        return translated.format(**values)
    except (KeyError, IndexError, ValueError):
        return text.format(**values)    # a translation with a mistyped {name}: English, not a crash


def N_(text: str) -> str:
    """Marks a string kept in a table for translation; _() translates it where it is shown."""
    return text


def languages() -> list[tuple[str, str]]:
    """(code, name) for English plus every compiled catalog carrying a name."""
    found = [("en", "English")]
    if not os.path.isdir(paths.LANG):
        return found
    for code in sorted(os.listdir(paths.LANG)):
        if code == "en":
            continue
        path = os.path.join(paths.LANG, code, "LC_MESSAGES", DOMAIN + ".mo")
        if not os.path.isfile(path):
            continue
        name = code
        try:
            with open(path, "rb") as f:
                name = gettext.GNUTranslations(f).info().get("x-language-name") or code
        except (OSError, ValueError, struct.error):
            continue                    # a broken catalog is not offered in Settings
        found.append((code, name))
    return found


def attach_wx(code: str) -> bool:
    """Let wx translate too: its standard buttons and menus from wx's catalog, ours from the same
    .mo gettext reads. Call after the wx.App exists; returns False when wx (or a catalog) is not
    available, which is never fatal."""
    global _wx_locale
    try:
        import wx
    except ImportError:
        return False
    try:
        # Urdu here is Roman Urdu, written in Latin letters: wx's Urdu locale would lay every
        # window out right-to-left and load Nastaliq wx strings. Keep wx in its English LTR
        # locale, but point wx.Translations at our Roman Urdu catalogs for button labels.
        roman_urdu = code == "ur"
        if roman_urdu:
            _wx_locale = wx.Locale(wx.LANGUAGE_ENGLISH)
            wx_code = "ur"
        else:
            wx_code = code or "en"
            info = wx.Locale.FindLanguageInfo(wx_code) if wx_code != "en" else None
            _wx_locale = wx.Locale(info.Language) if info is not None else wx.Locale(wx.LANGUAGE_ENGLISH)
        for folder in (os.path.join(os.path.dirname(os.path.abspath(wx.__file__)), "locale"), paths.LANG):
            if os.path.isdir(folder):
                wx.FileTranslationsLoader.AddCatalogLookupPathPrefix(folder)
        translations = wx.Translations.Get()
        if translations is None:
            translations = wx.Translations()
            wx.Translations.Set(translations)
        translations.SetLanguage(wx_code)
        if roman_urdu:
            translations.AddCatalog("wxstd")       # our Roman Urdu standard buttons
        else:
            translations.AddStdCatalog()
        if os.path.isdir(paths.LANG):
            translations.AddCatalog(DOMAIN)
        return True
    except (OSError, ValueError, RuntimeError):
        return False


def write_template() -> tuple[str, int]:
    """Refresh the .pot, merge the .po files and compile the .mo files (for --strings)."""
    return _write_template(paths.LANG)
