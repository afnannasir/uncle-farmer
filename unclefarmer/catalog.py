"""gettext catalogs: extracting the strings, writing .pot/.po files and compiling .mo ones.

Translators edit lang/<code>.po. Running `python -m unclefarmer --strings` refreshes
lang/unclefarmer.pot from the source, merges every .po with it (keeping what is already
translated and marking strings that are gone as obsolete) and compiles
lang/<code>/LC_MESSAGES/unclefarmer.mo, which is what the app loads at run time (through gettext,
and through wx.Translations for wx's own strings).

The format written here is the subset of PO this project uses: one string per msgid, no plurals,
no contexts, no source references. A header field the translator adds - X-Language-Name - is the
name of the language in that language, shown in Settings."""

from __future__ import annotations

import ast
import os
import struct
from collections.abc import Iterable

DOMAIN = "unclefarmer"
TEMPLATE = DOMAIN + ".pot"

# Header fields, in the order written. Lower-case keys are used in memory.
HEADER_FIELDS = (
    ("Project-Id-Version", "Uncle Farmer"),
    ("Report-Msgid-Bugs-To", ""),
    ("Language", ""),
    ("Language-Team", ""),
    ("X-Language-Name", ""),
    ("MIME-Version", "1.0"),
    ("Content-Type", "text/plain; charset=UTF-8"),
    ("Content-Transfer-Encoding", "8bit"),
)


def extract(root: str) -> list[str]:
    """Every string passed to _() or N_() as a literal, sorted."""
    strings: set[str] = set()
    for folder, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id not in ("_", "N_") or not node.args:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    strings.add(first.value)
    return sorted(strings)


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            .replace("\t", "\\t").replace("\r", "\\r"))


def _unescape(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _unquote(text: str) -> str:
    """A PO quoted field: strip one surrounding quote pair, then unescape.

    str.strip('"') would also eat the quote of a value that ends in an escaped one
    (\"...\\\" at the end of a line), leaving a stray backslash.
    """
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return _unescape(text)


def _quoted(value: str) -> list[str]:
    """PO continuation lines for one value: a line per newline, escapes kept whole."""
    parts = value.split("\n")
    return ['"%s"' % _escape(part + ("\n" if i < len(parts) - 1 else ""))
            for i, part in enumerate(parts)]


def _field(name: str, value: str) -> list[str]:
    if "\n" not in value and len(value) < 72:
        return ['%s "%s"' % (name, _escape(value))]
    return ['%s ""' % name] + _quoted(value)


def header_text(header: dict[str, str]) -> str:
    """The msgid-"" msgstr: one 'Name: value' line per field, lower-case keys in, canonical out."""
    lines: list[str] = []
    for name, default in HEADER_FIELDS:
        lines.append("%s: %s\n" % (name, header.get(name.lower()) or (default if name != "Language" else "")))
    for name in sorted(header):
        if name.lower() not in {n.lower() for n, _default in HEADER_FIELDS}:
            lines.append("%s: %s\n" % (name, header[name]))
    return "".join(lines)


def render_po(header: dict[str, str], entries: Iterable[tuple[str, str]],
              obsolete: Iterable[tuple[str, str]] = ()) -> str:
    """A .pot/.po file: the header, entries sorted by msgid, then obsolete ones under #~."""
    out = ["# Uncle Farmer translation catalog. Edit the msgstr lines; keep the {placeholders}.\n",
           "msgid \"\"\n", "msgstr \"\"\n"]
    out += ['"%s"\n' % _escape(line) for line in header_text(header).splitlines(True)]
    out.append("\n")
    for msgid, msgstr in sorted(entries):
        out.append("\n")
        out.append("\n".join(_field("msgid", msgid)) + "\n")
        out.append("\n".join(_field("msgstr", msgstr)) + "\n")
    for msgid, msgstr in sorted(obsolete):
        out.append("\n")
        out.append("".join("#~ " + line + "\n" for line in _field("msgid", msgid)))
        out.append("".join("#~ " + line + "\n" for line in _field("msgstr", msgstr)))
    return "".join(out)


def _parse(text: str) -> tuple[dict[str, str], list[tuple[str, str]], list[tuple[str, str]]]:
    """(header, current entries, obsolete entries). Fuzzy entries are dropped."""
    r = _PoReader()
    for raw in text.splitlines():
        r.line(raw.strip())
    r.flush()
    return r.header, r.entries, r.obsolete_entries


class _PoReader:
    """Reads a .po file a line at a time: the entry being read (msgid, msgstr, which of them its
    continued lines belong to, fuzzy, obsolete), kept once a blank line or the next msgid ends it."""

    def __init__(self) -> None:
        self.header: dict[str, str] = {}
        self.entries: list[tuple[str, str]] = []
        self.obsolete_entries: list[tuple[str, str]] = []
        self.msgid: str | None = None
        self.msgstr = ""
        self.section = ""
        self.fuzzy = False
        self.obsolete = False

    def flush(self) -> None:
        if self.msgid is not None and not self.fuzzy:
            if self.msgid:
                (self.obsolete_entries if self.obsolete else self.entries).append((self.msgid, self.msgstr))
            elif not self.obsolete:
                for line in self.msgstr.splitlines():
                    if ":" in line:
                        key, value = line.split(":", 1)
                        self.header[key.strip().lower()] = value.strip()
        self.msgid, self.msgstr, self.section, self.fuzzy, self.obsolete = None, "", "", False, False

    def _more(self, value: str) -> None:
        """A continued line: more of the msgid or the msgstr."""
        if self.section == "id":
            self.msgid = (self.msgid or "") + value
        else:
            self.msgstr += value

    def line(self, line: str) -> None:
        if not line:
            self.flush()
        elif line.startswith("#~"):
            self._obsolete_line(line)
        elif line.startswith("#"):
            if line.startswith("#,") and "fuzzy" in line:
                self.fuzzy = True
        elif line.startswith("msgid "):
            if self.msgid is not None and self.section == "str":
                self.flush()                     # entries without a blank line between them
            self.msgid = _unquote(line[6:])
            self.section = "id"
        elif line.startswith("msgstr ") or line.startswith("msgstr["):
            if self.section == "id":
                self.msgstr = _unquote(line.split(" ", 1)[1])
                self.section = "str"
        elif line.startswith('"') and self.section and not self.obsolete:
            self._more(_unquote(line))

    def _obsolete_line(self, line: str) -> None:
        self.obsolete = True
        if line.startswith("#~ msgid "):
            self.msgid = _unquote(line[9:])
            self.section = "id"
        elif line.startswith("#~ msgstr "):
            self.msgstr = _unquote(line[10:])
            self.section = "str"
        elif line.startswith("#~ \"") and self.section:
            # "#~ " is three characters: the quote after it is the value's own opening quote.
            # Cutting four took that quote too, so _unquote kept the closing one and every
            # rewrite of the file added another \" to each long obsolete entry.
            self._more(_unquote(line[3:]))


def parse_po(text: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """(header, [(msgid, msgstr)]). Obsolete and fuzzy entries are dropped."""
    header, entries, _obsolete = _parse(text)
    return header, entries


def _prime(at_least: int) -> int:
    n = max(3, at_least)
    while any(n % d == 0 for d in range(2, int(n ** 0.5) + 1)):
        n += 1
    return n


def _hash_table(keys: list[bytes]) -> bytes:
    """The GNU .mo hash table (hashpjw, open addressing); 0 means empty, else index + 1."""
    size = _prime(2 * len(keys) + 1)
    slots = [0] * size
    for index, key in enumerate(keys):
        value = 0
        for byte in key:
            value = ((value << 4) + byte) & 0xFFFFFFFF
            high = value & 0xF0000000
            if high:
                value ^= high >> 24
            value ^= high
        slot = value % size
        step = 1 + value % (size - 2)
        while slots[slot]:
            slot = (slot + step) % size
        slots[slot] = index + 1
    return struct.pack("<%dI" % size, *slots)


def mo_bytes(header: dict[str, str], entries: Iterable[tuple[str, str]]) -> bytes:
    """The GNU .mo format (little endian), untranslated entries left out."""
    pairs = [("", header_text(header))]
    pairs += sorted((msgid, msgstr) for msgid, msgstr in entries if msgstr)
    keys = bytearray()
    values = bytearray()
    rows: list[tuple[int, int, int, int]] = []
    for msgid, msgstr in pairs:
        key, value = msgid.encode("utf-8"), msgstr.encode("utf-8")
        rows.append((len(key), len(keys), len(value), len(values)))
        keys += key
        values += value
    count = len(pairs)
    orig_table = 28
    trans_table = orig_table + count * 8
    table = _hash_table([msgid.encode("utf-8") for msgid, _msgstr in pairs])
    hash_offset = trans_table + count * 8   # GNU msgfmt puts the hash table before the strings
    keys_start = hash_offset + len(table)
    values_start = keys_start + len(keys)
    out = bytearray(struct.pack("<7I", 0x950412DE, 0, count, orig_table, trans_table,
                                len(table) // 4, hash_offset))
    for key_len, key_off, _value_len, _value_off in rows:
        out += struct.pack("<2I", key_len, keys_start + key_off)
    for _key_len, _key_off, value_len, value_off in rows:
        out += struct.pack("<2I", value_len, values_start + value_off)
    out += table
    out += keys
    out += values
    out += b"\0"                           # a terminator keeps the last string inside the file
    return bytes(out)


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _language_header(existing: dict[str, str], code: str) -> dict[str, str]:
    header = dict(existing)
    header["language"] = header.get("language") or code
    header["x-language-name"] = header.get("x-language-name") or code
    return header


def compile_catalog(lang_dir: str, code: str, header: dict[str, str],
                    entries: Iterable[tuple[str, str]]) -> str:
    """Write lang/<code>/LC_MESSAGES/unclefarmer.mo and return its path."""
    path = os.path.join(lang_dir, code, "LC_MESSAGES", DOMAIN + ".mo")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(mo_bytes(header, entries))
    return path


def write_template(lang_dir: str) -> tuple[str, int]:
    """Refresh the .pot, merge every .po with it and compile the .mo files.

    Returns (template path, number of strings). Translations of strings that no longer exist are
    kept as obsolete (#~) entries, so a string that comes back keeps its translation."""
    strings = extract(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(lang_dir, exist_ok=True)
    template = os.path.join(lang_dir, TEMPLATE)
    blank = {name.lower(): "" for name, _default in HEADER_FIELDS}
    blank["project-id-version"] = "Uncle Farmer"
    write(template, render_po(blank, [(s, "") for s in strings]))
    if os.path.isdir(lang_dir):
        for name in sorted(os.listdir(lang_dir)):
            if not name.endswith(".po"):
                continue
            code = name[:-3]
            path = os.path.join(lang_dir, name)
            old_header, old, old_obsolete = _parse(read(path))
            header = _language_header(old_header, code)
            known = dict(old)
            current = set(strings)
            entries = [(s, known.get(s, "")) for s in strings]
            obsolete = {msgid: msgstr for msgid, msgstr in old_obsolete if msgstr}
            obsolete.update({msgid: msgstr for msgid, msgstr in old
                             if msgid not in current and msgstr})
            write(path, render_po(header, entries, sorted(obsolete.items())))
            compile_catalog(lang_dir, code, header, entries)
    return template, len(strings)
