"""Pragmatic wikitext utilities: links, templates/infoboxes, sections, prose.

This is a *subset* parser, not a spec-complete MediaWiki implementation. It
covers the constructs that actually carry signal for us -- infobox templates,
wiki links, section headings, tables, references, HTML comments -- and degrades
unknown markup to plain text instead of raising.

Design rule that matters downstream: link extraction ignores templates by
default. Fandom articles end in navigation boxes holding hundreds of links; if
those counted as "A mentions B", every character would appear connected to
every other character and the map would be useless.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

# --- Low-level scanning ------------------------------------------------------

COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
REF_RE = re.compile(r"<ref\b[^>]*?/>|<ref\b[^>]*?>.*?</ref>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
HEADING_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.M)
LINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")
EXT_LINK_RE = re.compile(r"\[(https?://[^\s\]]+)(?:\s+([^\]]*))?\]")
YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
PARAM_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\. ]{1,40}$")

# Link targets we never treat as story entities.
NON_ENTITY_LINK_PREFIXES = (
    "file:", "image:", "category:", "template:", "help:", "special:",
    "mediawiki:", "module:", "user:", "w:", "wikipedia:", "wikt:",
)


def find_balanced(text: str, start: int, opener: str, closer: str) -> int:
    """Return the index just past the matching `closer` for `opener` at `start`.

    Returns -1 if unbalanced. Used to walk `{{...}}` and `[[...]]` spans that
    contain arbitrary nesting, which regexes cannot do.
    """
    if not text.startswith(opener, start):
        raise ValueError(f"text at {start} does not start with {opener!r}")
    depth = 0
    i = start
    n = len(text)
    while i < n:
        if text.startswith(opener, i):
            depth += 1
            i += len(opener)
        elif text.startswith(closer, i):
            depth -= 1
            i += len(closer)
            if depth == 0:
                return i
        else:
            i += 1
    return -1


def split_top_level(text: str, sep: str = "|") -> list[str]:
    """Split on `sep`, ignoring separators nested inside {{ }} or [[ ]]."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        two = text[i:i + 2]
        if two in ("{{", "[["):
            depth += 1
            buf.append(two)
            i += 2
            continue
        if two in ("}}", "]]"):
            if depth:
                depth -= 1
            buf.append(two)
            i += 2
            continue
        if depth == 0 and text.startswith(sep, i):
            parts.append("".join(buf))
            buf = []
            i += len(sep)
            continue
        buf.append(text[i])
        i += 1
    parts.append("".join(buf))
    return parts


def remove_comments(text: str) -> str:
    return COMMENT_RE.sub("", text)


def remove_tables(text: str) -> str:
    """Drop `{| ... |}` tables, honouring one level of nesting."""
    out: list[str] = []
    i = 0
    depth = 0
    n = len(text)
    while i < n:
        if text.startswith("{|", i):
            depth += 1
            i += 2
            continue
        if text.startswith("|}", i) and depth:
            depth -= 1
            i += 2
            continue
        if depth == 0:
            out.append(text[i])
        i += 1
    return "".join(out)


def iter_templates(text: str, *, recurse: bool = False) -> list[str]:
    """Return raw template spans (`{{...}}`), outermost only by default."""
    found: list[str] = []
    i = 0
    n = len(text)
    while i < n - 1:
        if text.startswith("{{", i):
            end = find_balanced(text, i, "{{", "}}")
            if end == -1:
                break
            found.append(text[i:end])
            if recurse:
                found.extend(iter_templates(text[i + 2:end - 2], recurse=True))
            i = end
        else:
            i += 1
    return found


def remove_templates(text: str) -> str:
    # NOTE: the loop runs to `n`, not `n - 1`. With `i < n - 1` the final
    # character of the input is never appended, which silently truncated every
    # string this function returned -- most visibly, a wiki link sitting at the
    # very end of an article lost its closing `]]` and stopped matching
    # LINK_RE, so real mentions disappeared from the graph.
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("{{", i):
            end = find_balanced(text, i, "{{", "}}")
            if end == -1:      # unbalanced: drop the tail
                break
            i = end
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


@dataclass(frozen=True)
class Template:
    """A parsed template invocation: `{{name|positional|key=value}}`."""

    name: str
    positional: tuple[str, ...] = ()
    named: dict[str, str] = field(default_factory=dict)
    raw: str = ""

    def get(self, *keys: str, default: str = "") -> str:
        """Look up the first present key, case-insensitively and spacelessly."""
        wanted = {k.lower().replace("_", " ").strip() for k in keys}
        for key, value in self.named.items():
            normalised = key.lower().replace("_", " ").strip()
            if normalised in wanted:
                return value
        return default


def parse_template(raw: str) -> Template:
    """Parse one raw `{{...}}` span into name / positional / named parameters."""
    body = raw[2:-2] if raw.startswith("{{") else raw
    parts = split_top_level(body, "|")
    name = parts[0].strip()
    positional: list[str] = []
    named: dict[str, str] = {}
    for part in parts[1:]:
        eq_parts = split_top_level(part, "=")
        if len(eq_parts) > 1 and PARAM_NAME_RE.match(eq_parts[0].strip()):
            named[eq_parts[0].strip()] = "=".join(eq_parts[1:]).strip()
        else:
            positional.append(part.strip())
    return Template(name=name, positional=tuple(positional), named=named, raw=raw)


def iter_parsed_templates(text: str) -> list[Template]:
    return [parse_template(raw) for raw in iter_templates(text)]


def infoboxes(text: str) -> list[Template]:
    """Return templates whose name starts with `infobox` (case-insensitive).

    Infoboxes are the highest-value structured data on the wiki: they encode
    relatives, affiliation, occupation, appearances and status as key=value.
    """
    return [
        tmpl for tmpl in iter_parsed_templates(text)
        if tmpl.name.lower().startswith("infobox")
    ]


# --- Links -------------------------------------------------------------------

@dataclass(frozen=True)
class WikiLink:
    target: str     # article the link points at, without anchor
    anchor: str     # section anchor, empty if none
    label: str      # display text

    @property
    def is_entity(self) -> bool:
        lowered = self.target.strip().lower()
        return not lowered.startswith(NON_ENTITY_LINK_PREFIXES)


def extract_links(text: str, *, include_templates: bool = False) -> list[WikiLink]:
    """Extract internal `[[...]]` links, optionally from inside templates."""
    scope = text if include_templates else remove_templates(text)
    links: list[WikiLink] = []
    for match in LINK_RE.finditer(scope):
        inner = match.group(1)
        pieces = split_top_level(inner, "|")
        target_raw = pieces[0]
        label = pieces[-1] if len(pieces) > 1 else pieces[0]
        target_raw, _, anchor = target_raw.partition("#")
        # Strip a leading colon escape ([[:Category:X]]) and any bold/italic.
        target = target_raw.strip().lstrip(":").strip()
        anchor = anchor.strip()
        if not target:
            continue
        label = label.strip() or target
        links.append(WikiLink(target=target, anchor=anchor, label=label))
    return links


def entity_links(text: str, *, include_templates: bool = False) -> list[WikiLink]:
    """Like `extract_links`, minus File:/Category:/interwiki noise."""
    return [l for l in extract_links(text, include_templates=include_templates) if l.is_entity]


# --- Prose -------------------------------------------------------------------

def drop_media_links(text: str) -> str:
    """Delete `[[File:...]]` / `[[Image:...]]` spans entirely."""
    # NOTE: loop runs to `n` for the same reason as `remove_templates` -- the
    # `i < n - 1` form dropped the input's final character on every call.
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith("[[", i):
            end = find_balanced(text, i, "[[", "]]")
            if end == -1:
                break
            span = text[i:end]
            inner = span[2:-2].strip().lower()
            if inner.startswith(("file:", "image:")):
                i = end
                continue
            out.append(span)
            i = end
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def to_plaintext(text: str) -> str:
    """Flatten wikitext to readable prose. Templates and tables are removed."""
    text = remove_comments(text)
    text = REF_RE.sub("", text)
    text = drop_media_links(text)
    text = remove_tables(text)
    text = TAG_RE.sub("", text)
    text = LINK_RE.sub(lambda m: split_top_level(m.group(1), "|")[-1].strip(), text)
    text = remove_templates(text)
    text = EXT_LINK_RE.sub(lambda m: (m.group(2) or "").strip(), text)
    text = re.sub(r"'{2,5}", "", text)
    text = re.sub(r"^[*#:;]+\s*", "", text, flags=re.M)
    text = HEADING_RE.sub(lambda m: m.group(2), text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass(frozen=True)
class Section:
    level: int      # 0 = lead section before the first heading
    heading: str
    body: str


def split_sections(text: str, *, flatten: bool = False) -> list[Section]:
    """Split an article into (level, heading, body) sections."""
    source = to_plaintext(text) if flatten else text
    sections: list[Section] = []
    matches = list(HEADING_RE.finditer(source))
    if not matches:
        return [Section(level=0, heading="", body=source)]
    lead = source[:matches[0].start()]
    if lead.strip():
        sections.append(Section(level=0, heading="", body=lead))
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(source)
        sections.append(
            Section(
                level=len(match.group(1)),
                heading=match.group(2).strip(),
                body=source[match.end():end],
            )
        )
    return sections


def first_paragraph(text: str, *, max_chars: int = 400) -> str:
    """First real prose paragraph, trimmed -- used as a node summary."""
    plains = to_plaintext(text)
    for block in plains.split("\n\n"):
        block = block.strip()
        if len(block) < 40:
            continue
        if block.startswith(("{{", "|")):
            continue
        block = re.sub(r"\s+", " ", block)
        return block[:max_chars].rstrip()
    return ""


def extract_years(text: str, *, low: int = 1900, high: int = 2099) -> list[int]:
    """Sorted unique plausible years mentioned in the text."""
    years = {int(m) for m in YEAR_RE.findall(to_plaintext(text))}
    return sorted(y for y in years if low <= y <= high)


def template_names(text: str) -> list[str]:
    """Distinct template names used on a page, lowercased."""
    seen: list[str] = []
    for tmpl in iter_parsed_templates(text):
        name = tmpl.name.lower().strip()
        if name and name not in seen:
            seen.append(name)
    return seen
