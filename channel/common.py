"""Shared helpers for Pip's channel scripts. Standard library only.

Paths are resolved from the repo root (the folder that contains channel/ and site/),
so every script works no matter which folder you run it from.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # WebContent/ (holds channel/ and site/)
# Pip's own folders (logs/, parent/) live one level up when this folder is called WebContent
PIP = ROOT.parent if ROOT.name.lower() == "webcontent" else ROOT
CHANNEL = ROOT / "channel"
DAGBOEK = CHANNEL / "dagboek"
LOGS = PIP / "logs"
REQUESTS_DIR = PIP / "parent" / "requests"
SITE = ROOT / "site"

DISCLOSURE = ("Written by a language model from Pip's own sensor logs and checked by a human "
              "before publishing. Pip never reads this site.")


# ---------------------------------------------------------------- front matter
def read_post(path: Path) -> tuple[dict, str]:
    """Split a Markdown file into (front-matter dict, body). Simple `key: value` lines only."""
    text = path.read_text(encoding="utf-8")
    meta: dict = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                line = line.split("#", 1)[0].rstrip()
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"')
            text = text[end + 4:].lstrip("\n")
    return meta, text


def write_post(path: Path, meta: dict, body: str) -> None:
    head = "\n".join(f"{k}: {v}" for k, v in meta.items())
    path.write_text(f"---\n{head}\n---\n\n{body.strip()}\n", encoding="utf-8")


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- tiny Markdown
def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', s)
    return s


def markdown(text: str, shift: int = 1) -> str:
    """Enough Markdown for diary entries and blog posts:
    headings, paragraphs, lists, blockquotes, code blocks, bold/italic/code/links."""
    out: list[str] = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(html.escape(lines[i]))
                i += 1
            out.append("<pre><code>" + "\n".join(buf) + "</code></pre>")
            i += 1
            continue
        m = re.match(r"(#{1,4})\s+(.*)", line)
        if m:
            n = len(m.group(1)) + shift  # in posts the page title is h1, so headings start at h2
            out.append(f"<h{min(n, 5)}>{_inline(m.group(2))}</h{min(n, 5)}>")
            i += 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            head, body = rows[0], rows[1:]
            out.append('<div class="tablewrap"><table><thead><tr>' + "".join(f"<th>{_inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
                                                         for r in body) + "</tbody></table></div>")
            continue
        if re.match(r"\s*([-*]|\d+\.)\s+", line):
            ordered = bool(re.match(r"\s*\d+\.", line))
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines) and re.match(r"\s*([-*]|\d+\.)\s+", lines[i]):
                items.append("<li>" + _inline(re.sub(r"\s*([-*]|\d+\.)\s+", "", lines[i], 1)) + "</li>")
                i += 1
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i][1:].strip())
                i += 1
            out.append("<blockquote><p>" + _inline(" ".join(buf)) + "</p></blockquote>")
            continue
        if not line.strip():
            i += 1
            continue
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(r"(#{1,4}\s|```|>|\||\s*([-*]|\d+\.)\s)", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>" + _inline(" ".join(buf)) + "</p>")
    return "\n".join(out)
