# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Translation: gettext catalogs, the .po/.pot round trip, .mo compiling, loading, and wx."""

from __future__ import annotations

import gettext
import os
import re
import sys
import types
from typing import ClassVar

import pytest

from unclefarmer import catalog, i18n, paths
from pathlib import Path
from collections.abc import Iterator


@pytest.fixture(autouse=True)
def fresh_language() -> Iterator[None]:
    i18n.load("en")
    yield
    i18n.load("en")


def test_a_missing_string_falls_back_to_english_and_formats() -> None:
    assert i18n._("Hello") == "Hello"
    assert i18n._("Hello {name}", name="Ann") == "Hello Ann"


def test_a_translation_is_used_and_formatted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "LANG", str(tmp_path))
    catalog.compile_catalog(str(tmp_path), "ru", {"language": "ru", "x-language-name": "Русский"},
                            [("Hello", "Привет"), ("Hello {name}", "Привет, {name}")])
    i18n.load("ru")
    assert i18n._("Hello") == "Привет"
    assert i18n._("Hello {name}", name="Ann") == "Привет, Ann"
    assert i18n._("Not translated") == "Not translated"


def test_a_mistyped_translation_falls_back_to_english(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "LANG", str(tmp_path))
    catalog.compile_catalog(str(tmp_path), "ru", {"language": "ru"},
                            [("Hello {name}", "Привет, {nom}"), ("Cost {name}", "Цена {name} {0}")])
    i18n.load("ru")
    # a translation naming a placeholder the caller didn't pass: English, not a crash
    assert i18n._("Hello {name}", name="Ann") == "Hello Ann"
    # a translation with a stray positional placeholder (IndexError): English too
    assert i18n._("Cost {name}", name="Ann") == "Cost Ann"


def test_english_needs_no_file_at_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "LANG", str(tmp_path / "missing"))
    i18n.load("en")
    assert i18n._("Anything") == "Anything"


def test_a_code_with_no_catalog_means_english(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "LANG", str(tmp_path))
    i18n.load("de")
    assert i18n._("Anything") == "Anything"


def test_a_broken_catalog_is_english_not_a_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "LANG", str(tmp_path))
    folder = tmp_path / "de" / "LC_MESSAGES"
    folder.mkdir(parents=True)
    (folder / "unclefarmer.mo").write_bytes(b"not a catalog")
    i18n.load("de")
    assert i18n._("Anything") == "Anything"


def test_n_marker_is_identity() -> None:
    assert i18n.N_("Kept for later") == "Kept for later"


def test_languages_lists_english_plus_the_catalogs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "LANG", str(tmp_path))
    catalog.compile_catalog(str(tmp_path), "de", {"language": "de", "x-language-name": "Deutsch"},
                            [("Hi", "Hallo")])
    catalog.compile_catalog(str(tmp_path), "fr", {"language": "fr"}, [("Hi", "Salut")])
    broken = tmp_path / "zz" / "LC_MESSAGES"
    broken.mkdir(parents=True)
    (broken / "unclefarmer.mo").write_bytes(b"not a catalog")
    (tmp_path / "en" / "LC_MESSAGES").mkdir(parents=True)          # English is not listed twice
    assert i18n.languages() == [("en", "English"), ("de", "Deutsch"), ("fr", "fr")]


def test_languages_without_a_folder_is_just_english(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "LANG", str(tmp_path / "nope"))
    assert i18n.languages() == [("en", "English")]


def test_roman_urdu_catalog_is_roman_and_keeps_placeholders() -> None:
    path = os.path.join(paths.LANG, "ur.po")
    header, entries = catalog.parse_po(open(path, encoding="utf-8").read())
    assert header["x-language-name"] == "Urdu (Roman)"
    translated = {msgid: msgstr for msgid, msgstr in entries if msgstr}
    assert len(translated) == len(entries)                  # every current string is translated
    assert translated["Ready"] == "Tayyar"
    assert translated["Running {what}"] == "{what} chal raha hai"
    assert translated["&Start"] == "&Shuru"
    assert translated["Settings"] == "Tarteeb"
    assert translated["Save"] == "Mahfooz"
    assert translated["&Delete"] == "&Hataayein"
    assert translated["&Cancel the shutdown"] == "Shutdown &mansookh karein"
    arabic = re.compile(r"[\u0600-\u06ff\u0750-\u077f\ufb50-\ufdff\ufe70-\ufeff]")
    for msgid, msgstr in translated.items():
        assert not arabic.search(msgstr), msgid
        assert set(re.findall(r"\{[^{}]*\}", msgid)) == set(re.findall(r"\{[^{}]*\}", msgstr)), msgid
        if "\t" in msgid:
            assert msgstr.split("\t", 1)[1] == msgid.split("\t", 1)[1], msgid
        if "&" in msgid:
            assert "&" in msgstr, msgid
        assert msgstr.count("\n") == msgid.count("\n"), msgid
        assert not re.search(r"\b(Settings|Save|Cancel)\b", msgstr), msgid


def test_roman_urdu_compiles_and_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = os.path.join(paths.LANG, "ur.po")
    header, entries = catalog.parse_po(open(path, encoding="utf-8").read())
    monkeypatch.setattr(paths, "LANG", str(tmp_path))
    catalog.compile_catalog(str(tmp_path), "ur", header, entries)
    i18n.load("ur")
    assert i18n._("Ready") == "Tayyar"
    assert i18n._("Running {what}", what="plan") == "plan chal raha hai"
    assert i18n._("Not translated") == "Not translated"      # everything else falls back to English


@pytest.mark.skipif(not os.path.isfile(os.path.join(paths.LANG, "ur", "LC_MESSAGES", catalog.DOMAIN + ".mo")),
                    reason="run --strings to compile the catalogs")
def test_roman_urdu_is_offered() -> None:
    assert ("ur", "Urdu (Roman)") in i18n.languages()


def test_roman_urdu_standard_buttons_are_translated() -> None:
    path = os.path.join(paths.LANG, "ur", "LC_MESSAGES", "wxstd.mo")
    with open(path, "rb") as f:
        translation = gettext.GNUTranslations(f)
    assert translation.gettext("Cancel") == "Mansookh"
    assert translation.gettext("&Cancel") == "&Mansookh"
    assert translation.gettext("&Save") == "&Mahfooz"


def test_write_template_collects_strings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "LANG", str(tmp_path / "lang"))
    path, count = i18n.write_template()
    assert path == str(tmp_path / "lang" / "unclefarmer.pot")
    assert count > 0
    header, entries = catalog.parse_po(open(path, encoding="utf-8").read())
    assert header["project-id-version"] == "Uncle Farmer"
    assert len(entries) == count
    assert all(msgstr == "" for _msgid, msgstr in entries)          # the template is untranslated
    assert "dungeon - you leave by its chest, not on foot" in {msgid for msgid, _msgstr in entries}


def test_extract_reads_literal_calls(tmp_path: Path) -> None:
    (tmp_path / "some.py").write_text(
        'from x import _, N_\n_("Plain")\nN_("Table")\n_("Place {n}", n=1)\n_(name)\n', encoding="utf-8")
    assert catalog.extract(str(tmp_path)) == ["Place {n}", "Plain", "Table"]


def test_po_round_trip_keeps_quotes_and_newlines() -> None:
    entries = [('A "quoted" line', 'Eine "zitierte" Zeile'),
               ('choosing "{entry}"', 'chun raha hoon "{entry}"'),
               ("two\nlines", "zwei\nZeilen"),
               ("{n} things", "{n} Dinge")]
    text = catalog.render_po({"language": "de", "x-language-name": "Deutsch"}, entries,
                             [("Gone", "Weg")])
    header, parsed = catalog.parse_po(text)
    assert dict(parsed) == dict(entries)
    assert header["language"] == "de"
    assert header["x-language-name"] == "Deutsch"
    assert "#~ msgid \"Gone\"" in text


def test_a_compiled_catalog_is_read_by_gettext(tmp_path: Path) -> None:
    entries = [("Ready", "Bereit"), ("Hello {name}", "Hallo {name}"), ("Unused", "")]
    path = catalog.compile_catalog(str(tmp_path), "de", {"language": "de", "x-language-name": "Deutsch"}, entries)
    with open(path, "rb") as f:
        translation = gettext.GNUTranslations(f)
    assert translation.gettext("Ready") == "Bereit"
    assert translation.gettext("Hello {name}") == "Hallo {name}"
    assert translation.gettext("Unused") == "Unused"        # left out: untranslated
    assert translation.info()["x-language-name"] == "Deutsch"


def test_write_template_merges_translations_and_compiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "LANG", str(tmp_path))
    po = tmp_path / "de.po"
    po.write_text(catalog.render_po({"language": "de", "x-language-name": "Deutsch"},
                                    [("Ready", "Bereit"), ("Gone", "Weg")]), encoding="utf-8")
    _pot, _count = i18n.write_template()
    header, entries = catalog.parse_po(po.read_text(encoding="utf-8"))
    assert dict(entries)["Ready"] == "Bereit"
    assert "Gone" not in dict(entries)
    assert "#~ msgid \"Gone\"" in po.read_text(encoding="utf-8")
    assert header["language"] == "de"
    i18n.load("de")
    assert i18n._("Ready") == "Bereit"
    assert (tmp_path / "de" / "LC_MESSAGES" / "unclefarmer.mo").is_file()
    _pot, _count = i18n.write_template()               # a second run keeps the obsolete translation
    assert "#~ msgid \"Gone\"" in po.read_text(encoding="utf-8")
    assert "#~ msgstr \"Weg\"" in po.read_text(encoding="utf-8")


def test_long_obsolete_entries_survive_every_rewrite_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Long entries are written as continued "#~ ..." lines; reading those back cut the opening
    # quote, and each rewrite added another \" to the end of the text.
    monkeypatch.setattr(paths, "LANG", str(tmp_path))
    gone = ("When nothing is in sight, walk to where a monster is due back (learned while farming), "
            'then say "back" - twice\nand once more')
    weg = 'Wenn nichts zu sehen ist, dorthin gehen, wo ein Monster zurückkommt - "zurück" sagen\nund noch einmal'
    po = tmp_path / "de.po"
    po.write_text(catalog.render_po({"language": "de"}, [("Ready", "Bereit"), (gone, weg)]), encoding="utf-8")
    i18n.write_template()
    first = po.read_text(encoding="utf-8")
    for _run in range(3):
        i18n.write_template()
    assert po.read_text(encoding="utf-8") == first
    _header, _entries, obsolete = catalog._parse(first)
    assert obsolete == [(gone, weg)]


def test_wx_is_pointed_at_the_same_catalogs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Info:
        Language = 42

    class Locale:
        def __init__(self, language: int) -> None:
            self.language = language

        @staticmethod
        def FindLanguageInfo(code: str) -> Info:
            return Info()

    class Translations:
        instance: ClassVar[Translations | None] = None

        def __init__(self) -> None:
            self.language: str | None = None
            self.std = False
            self.domains: list[str] = []
            Translations.instance = self

        @classmethod
        def Get(cls) -> Translations | None:
            return cls.instance

        @classmethod
        def Set(cls, value: Translations | None) -> None:
            cls.instance = value

        def SetLanguage(self, code: str) -> None:
            self.language = code

        def AddStdCatalog(self) -> bool:
            self.std = True
            return True

        def AddCatalog(self, domain: str) -> bool:
            self.domains.append(domain)
            return True

    class Loader:
        prefixes: ClassVar[list[str]] = []

        @staticmethod
        def AddCatalogLookupPathPrefix(path: str) -> None:
            Loader.prefixes.append(path)

    wx_dir = tmp_path / "wx"
    (wx_dir / "locale").mkdir(parents=True)
    fake = types.SimpleNamespace(__file__=str(wx_dir / "__init__.py"), LANGUAGE_ENGLISH=2,
                                 Locale=Locale, Translations=Translations, FileTranslationsLoader=Loader)
    monkeypatch.setitem(sys.modules, "wx", fake)
    assert i18n.attach_wx("de") is True
    instance = Translations.instance
    assert instance is not None
    assert instance.language == "de" and instance.std
    assert instance.domains == ["unclefarmer"]
    assert str(wx_dir / "locale") in Loader.prefixes

    Translations.instance = None
    assert i18n.attach_wx("ur") is True
    assert Translations.instance is not None
    # Roman Urdu is Latin script: wx stays in English/LTR, but its buttons use our Roman Urdu
    # wxstd catalog instead of wx's Nastaliq Urdu strings.
    assert Translations.instance.language == "ur"
    assert not Translations.instance.std
    assert Translations.instance.domains == ["wxstd", "unclefarmer"]
