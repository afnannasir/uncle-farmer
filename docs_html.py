"""Turn the guides in docs/ (Markdown) into web pages a screen reader can move through by heading.

    python docs_html.py            writes build/docs_html/*.html (build_exe.py ships them)

Each page gets a language, a title, a table of contents (a "Contents" navigation landmark with a
link to every section) and real headings, so H and 1-3 jump between sections and lists and tables
read as lists and tables. It understands the Markdown the guides use: # headings, paragraphs,
- and 1. lists (with lines that carry on and one level of lists inside), | tables |, code indented
by four spaces or between ``` lines, `code`, **bold**, *italic* and [links](url). Links between the
guides (GAME_GUIDE.md) point at their pages (GAME_GUIDE.html).
"""

from __future__ import annotations

import html
import os
import re
import sys
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
OUT = os.path.join(ROOT, "build", "docs_html")

CSS = """
:root { --bg: #ffffff; --fg: #1d1d1f; --muted: #5a5a60; --line: #d8d8dc; --code: #f2f2f5; --link: #0b57d0; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #17171a; --fg: #ececf0; --muted: #a8a8b0; --line: #3a3a40; --code: #24242a; --link: #8ab4f8; }
}
html { background: var(--bg); color: var(--fg); }
body { font: 1.05rem/1.6 "Segoe UI", system-ui, sans-serif; max-width: 48rem; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
a { color: var(--link); }
h1 { font-size: 1.9rem; margin: 0 0 .25rem; }
h2 { font-size: 1.4rem; margin: 2.2rem 0 .5rem; padding-top: .8rem; border-top: 1px solid var(--line); }
h3 { font-size: 1.15rem; margin: 1.6rem 0 .4rem; }
nav { border: 1px solid var(--line); border-radius: 8px; padding: .5rem 1.2rem; margin: 1.2rem 0 2rem; }
nav h2 { border: 0; margin: .5rem 0; padding: 0; font-size: 1.1rem; }
nav ul { margin: .2rem 0; padding-left: 1.2rem; }
code, pre { font-family: Consolas, "Cascadia Mono", monospace; background: var(--code); border-radius: 4px; }
code { padding: 0 .25rem; }
pre { padding: .7rem 1rem; overflow-x: auto; line-height: 1.4; }
pre code { padding: 0; background: none; }
table { border-collapse: collapse; margin: .8rem 0; }
th, td { border: 1px solid var(--line); padding: .35rem .7rem; text-align: left; vertical-align: top; }
.lead { color: var(--muted); margin-top: 0; }
"""


def slug(text: str, used: set[str]) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", re.sub(r"<[^>]+>", "", text).lower()).strip("-") or "section"
    base, n = s, 2
    while s in used:
        s, n = "%s-%d" % (base, n), n + 1
    used.add(s)
    return s


def inline(text: str) -> str:
    """Markdown inside a line -> HTML (escaped first; code spans kept as they are, even inside bold)."""
    codes: list[str] = []

    def hold(m: re.Match[str]) -> str:
        codes.append("<code>%s</code>" % html.escape(m.group(1), quote=False))
        return "\0%d\0" % (len(codes) - 1)
    part = html.escape(re.sub(r"`([^`]+)`", hold, text), quote=False)
    def link(m: re.Match[str]) -> str:
        target = re.sub(r"^(?:docs/)?([A-Z_]+)\.md(#.*)?$", r"\1.html\2", m.group(2))
        return '<a href="%s">%s</a>' % (html.escape(target), m.group(1))

    def code(m: re.Match[str]) -> str:
        return codes[int(m.group(1))]
    part = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, part)
    part = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", part)
    part = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", part)
    return re.sub(r"\0(\d+)\0", code, part)


LIST = re.compile(r"^( *)([-*]|\d+\.) +(.*)$")


def convert(md: str, title_fallback: str) -> tuple[str, str, list[tuple[int, str, str]]]:
    """(title, body html, [(level, id, text)])"""
    lines = md.replace("\r\n", "\n").split("\n")
    body: list[str] = []
    toc: list[tuple[int, str, str]] = []
    used: set[str] = set()
    title: str | None = None
    para: list[str] = []
    i = 0

    def flush() -> None:
        if para:
            body.append("<p>%s</p>" % inline(" ".join(l.strip() for l in para)))
            para.clear()

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush()
            i += 1
            continue
        if line.startswith("```"):
            flush()
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            body.append("<pre><code>%s</code></pre>" % html.escape("\n".join(code), quote=False))
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            flush()
            level, text = len(m.group(1)), inline(m.group(2))
            if level == 1 and title is None:
                title = re.sub(r"<[^>]+>", "", text)
                body.append("<h1>%s</h1>" % text)
            else:
                hid = slug(text, used)
                body.append('<h%d id="%s">%s</h%d>' % (level, hid, text, level))
                toc.append((level, hid, text))
            i += 1
            continue
        if line.startswith("    ") and not para and not LIST.match(line):
            code = []
            while i < len(lines) and (lines[i].startswith("    ") or not lines[i].strip()):
                code.append(lines[i][4:])
                i += 1
            while code and not code[-1].strip():
                code.pop()
            body.append("<pre><code>%s</code></pre>" % html.escape("\n".join(code), quote=False))
            continue
        if line.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            flush()
            head = _cells(line)
            rows: list[list[str]] = []
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            t = ["<table>", "<thead><tr>%s</tr></thead>" % "".join('<th scope="col">%s</th>' % inline(c) for c in head),
                 "<tbody>"]
            t += ["<tr>%s</tr>" % "".join("<td>%s</td>" % inline(c) for c in r) for r in rows]
            body.append("\n".join(t + ["</tbody></table>"]))
            continue
        m = LIST.match(line)
        if m and len(m.group(1)) < 4:
            flush()
            i = _list(lines, i, body)
            continue
        para.append(line)
        i += 1
    flush()
    return title or title_fallback, "\n".join(body), toc


def _cells(line: str) -> list[str]:
    """The cells of a table row."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


@dataclass
class _Item:
    """One item of a list: its text lines, the items of the list inside it, and whether that one is numbered."""
    text: list[str]
    subs: list[list[str]] = field(default_factory=list[list[str]])
    sub_ordered: bool = False


def _list(lines: list[str], i: int, body: list[str]) -> int:
    """A list starting at lines[i], with one level of lists inside. Returns the next line."""
    first = LIST.match(lines[i])
    assert first is not None              # the caller only starts a list on a matching line
    indent, ordered = len(first.group(1)), first.group(2)[0].isdigit()
    tag = "ol" if ordered else "ul"
    items: list[_Item] = []
    while i < len(lines):
        line = lines[i]
        m = LIST.match(line)
        if m and len(m.group(1)) == indent:
            items.append(_Item([m.group(3)]))
        elif m and len(m.group(1)) > indent and items:
            items[-1].subs.append([m.group(3)])
            items[-1].sub_ordered = m.group(2)[0].isdigit()
        elif line.strip() and line.startswith(" " * (indent + 2)) and items:
            (items[-1].subs[-1] if items[-1].subs else items[-1].text).append(line)
        else:
            break
        i += 1
    out = ["<%s>" % tag]
    for item in items:
        text, subs, sub_ordered = item.text, item.subs, item.sub_ordered
        li = inline(" ".join(t.strip() for t in text))
        if subs:
            st = "ol" if sub_ordered else "ul"
            li += "<%s>%s</%s>" % (st, "".join("<li>%s</li>" % inline(" ".join(t.strip() for t in s)) for s in subs), st)
        out.append("<li>%s</li>" % li)
    body.append("\n".join(out + ["</%s>" % tag]))
    return i


def page(title: str, body: str, toc: list[tuple[int, str, str]]) -> str:
    nav: list[str] = []
    if len(toc) > 2:
        nav = ['<nav aria-label="Contents">', "<h2>Contents</h2>", "<ul>"]
        open_sub = False
        for level, hid, text in toc:
            if level == 2:
                if open_sub:
                    nav.append("</ul></li>")
                    open_sub = False
                elif len(nav) > 3:
                    nav.append("</li>")
                nav.append('<li><a href="#%s">%s</a>' % (hid, text))
            elif level == 3 and len(nav) > 3:
                if not open_sub:
                    nav.append("<ul>")
                    open_sub = True
                nav.append('<li><a href="#%s">%s</a></li>' % (hid, text))
        nav.append("</ul></li></ul></nav>" if open_sub else "</li></ul></nav>")
    # the table of contents goes after the title and the lines under it, before the first section
    first = body.find("<h2")
    head, rest = (body[:first], body[first:]) if first >= 0 else (body, "")
    head = head.replace("<p>", '<p class="lead">', 1) if head.count("<p>") == 1 else head
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s</title>
<style>%s</style>
</head>
<body>
<main>
%s
%s
%s
</main>
</body>
</html>
""" % (html.escape(title), CSS, head, "\n".join(nav), rest)


def build(out: str = OUT, names: list[str] | None = None) -> dict[str, str]:
    """Write a page for each guide; returns {guide file: page path}."""
    os.makedirs(out, exist_ok=True)
    done: dict[str, str] = {}
    for name in names or sorted(n for n in os.listdir(DOCS) if n.endswith(".md")):
        with open(os.path.join(DOCS, name), encoding="utf-8") as f:
            title, body, toc = convert(f.read(), name[:-3].replace("_", " ").title())
        path = os.path.join(out, name[:-3] + ".html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(page(title, body, toc))
        done[name] = path
    return done


if __name__ == "__main__":
    for src, dst in build(names=sys.argv[1:] or None).items():
        print(src, "->", dst)
