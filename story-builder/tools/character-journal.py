# character-journal -- assemble the chronological narrative spine of ONE character
# from the local wikimap cache, as a draft "journal" / story timeline.
#
# Why this exists: wikimap's knowledge map is RELATIONAL (who knows whom, who is
# a member of what), not CHRONOLOGICAL -- graph.Node keeps only a first-paragraph
# summary, and Edge labels are relationship types. But the raw wikitext sitting in
# cache/<lang>/ IS chronological: a Fandom character article's "Biography" section
# runs in story order, and each paragraph cites the in-game scene it came from via
# <ref> ... scene: "[[Cutscene Title]]" </ref>. Those cutscene articles carry their
# own {{Infobox cutscene}} (map, room, trigger, length) plus Plot and Transcript.
#
# Joining the two turns "a pile of facts about a character" into an ordered spine:
# where the character was, what triggered the scene, and what was actually said.
# That is the input a story/game-history draft needs.
#
# Usage
# -----
#     python tools/character-journal.py "Rodrigo Juan Raval"
#     python tools/character-journal.py raval --section Biography
#     python tools/character-journal.py raval --transcript 6
#     python tools/character-journal.py raval --json > rodrigo.json
#     python tools/character-journal.py raval --md out/rodrigo-journal.md
#     python tools/character-journal.py --list          # titles in cache
#
# Exit codes: 0 = ok, 1 = no matching cached title, 2 = bad usage / no cache.
#
# NOTE: written via write_file, not create_tool -- create_tool prepends its own
# name/description header as a docstring, which collides with a module docstring.

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Windows consoles default to cp1252 and raise UnicodeEncodeError on wiki text.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

HERE = Path(__file__).resolve().parent
# tools/ normally sits beside the package, so the repo root is one level up.
ROOT = HERE.parent if (HERE.parent / "wikimap").is_dir() else HERE
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wikimap import wikitext as wt  # noqa: E402  (path set above)

# A <ref> can be defined inline (>) or self-closing (/>) when it re-uses a named one.
REF_ANY_RE = re.compile(r"<ref\b[^>]*?/>|<ref\b[^>]*?>.*?</ref>", re.S | re.I)
REF_DEF_RE = re.compile(r"<ref\s+name\s*=\s*\"([^\"]+)\"\s*>(.*?)</ref>", re.S | re.I)
REF_USE_RE = re.compile(r"<ref\s+name\s*=\s*\"([^\"]+)\"\s*/>", re.I)
# The load-bearing pattern: scene: "[[Cutscene|Label]]" inside a citation.
SCENE_RE = re.compile(r"scene:\s*[\"\u201c]?\[\[([^\]|]+?)(?:\|([^\]]*))?\]\]")
MONTH_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(?:\d{1,2}(?:\s*[-\u2013]\s*\d{1,2})?,\s*)?(\d{4})\b"
)
META_PREFIXES = ("template:", "category:", "file:", "image:", "help:", "special:")

# Transcript dialogue is marked by a BOLDED speaker, not by quotation marks:
#   '''[[Rodrigo Juan Raval]]''': What are you doing here?
#   '''Chris''': "What?"
# Both forms occur in the same wiki, so match the bold marker, then read the rest
# of the line as the spoken text. Quoting is re-applied when formatting output.
SPEECH_LINK_RE = re.compile(
    r"^\s*'''\s*\[\[([^\]|]+)(?:\|([^\]]+))?\]\]\s*'''\s*:\s*(.+?)\s*$"
)
SPEECH_PLAIN_RE = re.compile(
    r"^\s*'''\s*(?!\[\[)([^'\[]+?)\s*'''\s*:\s*(.+?)\s*$"
)


def load_corpus(cache_dir: Path) -> dict[str, str]:
    """Return {title: wikitext} for every non-missing page in the cache."""
    corpus: dict[str, str] = {}
    for path in sorted(cache_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for page in (data.get("query", {}) or {}).get("pages", []) or []:
            if page.get("missing"):
                continue
            title = page.get("title", "")
            revisions = page.get("revisions") or [{}]
            slots = revisions[0].get("slots", {}).get("main", {}) or {}
            content = slots.get("content") or ""
            if title and content:
                corpus.setdefault(title, content)
    return corpus


def resolve(corpus: dict[str, str], needle: str) -> list[str]:
    """Match by exact title first, else case-insensitive substring, skipping meta."""
    wanted = needle.strip().lower()
    exact = [t for t in corpus if t.lower() == wanted]
    if exact:
        return exact
    return [
        t
        for t in corpus
        if wanted in t.lower() and not t.lower().startswith(META_PREFIXES)
    ]


def is_cutscene(wikitext: str) -> bool:
    """True when an article is a cutscene page (it carries {{Infobox cutscene}})."""
    return "infobox cutscene" in wikitext.lower()


def resolve_scene(corpus: dict[str, str], scene_title: str) -> str | None:
    """Bind a cited scene title to one cached page, deterministically.

    Fandom holds pages whose titles differ only by case -- the cutscene
    "Thrown into despair" and the music track "Thrown Into Despair". Comparing
    case-insensitively alone binds a citation to whichever colliding page the
    cache yields first, which swapped a cutscene for its own soundtrack track.
    Prefer the exact title, then a case-insensitive match that is a real
    cutscene, then any case-insensitive match.
    """
    wanted = scene_title.strip()
    if wanted in corpus:
        return wanted
    folded = wanted.lower()
    hits = [t for t in corpus if t.lower() == folded]
    if not hits:
        return None
    for title in hits:
        if is_cutscene(corpus[title]):
            return title
    return hits[0]


def scene_titles(citation: str) -> list[tuple[str, str]]:
    """Pull (scene title, display label) pairs out of one citation body."""
    found: list[tuple[str, str]] = []
    for match in SCENE_RE.finditer(citation):
        title = match.group(1).strip()
        label = (match.group(2) or "").strip()
        found.append((title, label or title))
    return found


def collect_scenes(block: str, definitions: dict[str, str]) -> list[tuple[str, str]]:
    """Scenes cited by a block, resolving inline definitions and named re-uses.

    Fandom defines a long citation once (often up in the infobox quote) and then
    re-uses it by name. Treating only inline citations as evidence silently drops
    those scenes, which is why definitions are gathered first.
    """
    scenes: list[tuple[str, str]] = []
    for match in REF_ANY_RE.finditer(block):
        raw = match.group(0)
        for title, label in scene_titles(raw):
            scenes.append((title, label))
    for name in REF_USE_RE.findall(block):
        for title, label in scene_titles(definitions.get(name, "")):
            scenes.append((title, label))
    return scenes


def ref_definitions(wikitext: str) -> dict[str, str]:
    """Map every named <ref name="x">…</ref> definition in the whole article."""
    return {m.group(1): m.group(2) for m in REF_DEF_RE.finditer(wikitext)}


def infobox_fields(wikitext: str) -> dict[str, str]:
    """First infobox as {lowercased param: plaintext value}."""
    boxes = wt.infoboxes(wikitext)
    if not boxes:
        return {}
    return {
        key.strip().lower(): wt.to_plaintext(value).strip().rstrip(",")
        for key, value in boxes[0].named.items()
    }


def paragraphs(body: str, definitions: dict[str, str]) -> list[dict]:
    """Split a section into prose blocks, each tagged with scenes and dates."""
    body = wt.remove_tables(wt.remove_comments(body))
    entries: list[dict] = []
    for block in re.split(r"\n\s*\n", body):
        if not block.strip():
            continue
        prose = re.sub(r"\s+", " ", wt.to_plaintext(block)).strip()
        scenes = collect_scenes(block, definitions)
        if len(prose) < 30 and not scenes:
            continue
        entries.append(
            {
                "prose": prose,
                "scenes": list(dict.fromkeys(scenes)),
                "dates": [f"{m.group(1)} {m.group(2)}" for m in MONTH_RE.finditer(prose)],
                "years": wt.extract_years(prose),
            }
        )
    return entries


def _quote(text: str) -> str:
    """Wrap a line of dialogue in quotes unless it already carries them."""
    text = text.strip()
    if text.startswith('"') or text.startswith("\u201c"):
        return text
    return f'"{text}"'


def transcript_dialogue(transcript: str, limit: int) -> tuple[list[str], list[str]]:
    """Return (speakers, formatted lines) from a cutscene Transcript section.

    Fandom keeps the English localisation and the Japanese original script in two
    <tabber> panes separated by a `|-|` line. Only the first pane is read, which
    keeps the English script and drops the Japanese one without a language test.
    """
    raw_lines = transcript.splitlines()
    cut = len(raw_lines)
    for index, line in enumerate(raw_lines):
        if line.strip().startswith("|-|"):
            cut = index
            break
    raw_lines = raw_lines[:cut]

    dialogue: list[tuple[str, str]] = []
    for line in raw_lines:
        match = SPEECH_LINK_RE.match(line)
        if match:
            speaker = (match.group(2) or match.group(1)).strip()
            text = wt.to_plaintext(match.group(3)).strip()
        else:
            match = SPEECH_PLAIN_RE.match(line)
            if not match:
                continue
            speaker = match.group(1).strip()
            text = wt.to_plaintext(match.group(2)).strip()
        if speaker and text:
            dialogue.append((speaker, text))

    # Fallback for transcripts that quote dialogue without a bolded speaker.
    if not dialogue:
        flattened = [
            re.sub(r"\s+", " ", wt.to_plaintext(line)).strip()
            for line in transcript.splitlines()
        ]
        for line in flattened:
            if ":" in line and '"' in line:
                speaker, text = line.split(":", 1)
                dialogue.append((speaker.strip(" *'\"") or "?", text.strip()))

    speakers = list(dict.fromkeys(speaker for speaker, _ in dialogue))
    lines = [f"{speaker}: {_quote(text)}" for speaker, text in dialogue[: max(0, limit)]]
    return speakers[:8], lines


def cutscene_detail(wikitext: str, transcript_lines: int) -> dict:
    """Summarise a cutscene article: infobox, plot, and speakers."""
    fields = infobox_fields(wikitext)
    sections = {s.heading.lower(): s.body for s in wt.split_sections(wikitext)}
    detail = {
        "map": fields.get("map", ""),
        "room": fields.get("room", ""),
        "trigger": fields.get("trigger", ""),
        "length": fields.get("length", ""),
        "game": fields.get("game", ""),
        "plot": "",
        "speakers": [],
        "lines": [],
    }
    for name, body_text in sections.items():
        if name.startswith("plot"):
            words = re.sub(r"\s+", " ", wt.to_plaintext(body_text)).strip()
            detail["plot"] = words
            break
    transcript = next(
        (b for h, b in sections.items() if h.startswith("transcript")), ""
    )
    if transcript:
        detail["speakers"], detail["lines"] = transcript_dialogue(
            transcript, transcript_lines
        )
    return detail


def build_journal(title: str, wikitext: str, corpus: dict[str, str], args) -> dict:
    fields = infobox_fields(wikitext)
    definitions = ref_definitions(wikitext)
    sections = wt.split_sections(wikitext)

    wanted = (args.section or "biography").lower()
    chosen = next(
        (s for s in sections if wanted in s.heading.lower()),
        next((s for s in sections if s.level > 0), None),
    )
    entries = paragraphs(chosen.body if chosen else "", definitions) if chosen else []

    spine = []
    for index, entry in enumerate(entries, start=1):
        row = dict(entry)
        detail = []
        for scene_title, scene_label in entry["scenes"]:
            cached = resolve_scene(corpus, scene_title)
            node = {"title": scene_title, "label": scene_label, "cached": bool(cached)}
            if cached:
                node["detail"] = cutscene_detail(corpus[cached], args.transcript)
            detail.append(node)
        row["scene_detail"] = detail
        row["index"] = index
        spine.append(row)

    return {
        "title": title,
        "identity": {
            key: fields.get(key, "")
            for key in (
                "name", "jp name", "date of birth", "date of death", "status",
                "occupation", "affiliation", "family", "place of birth",
                "place of death", "first", "sex",
            )
            if fields.get(key)
        },
        "section_used": chosen.heading or "(lead)",
        "all_sections": [s.heading for s in sections if s.heading],
        "spine": spine,
    }


def missing_scenes(journal: dict) -> list[str]:
    seen: list[str] = []
    for row in journal["spine"]:
        for node in row["scene_detail"]:
            if not node["cached"] and node["title"] not in seen:
                seen.append(node["title"])
    return seen


def print_journal(journal: dict) -> None:
    print(f"=== {journal['title']} -- narrative spine ===")
    if journal["identity"]:
        print("--- identity (from infobox) ---")
        for key, value in journal["identity"].items():
            print(f"  {key:<14}: {value}")
    print(f"--- section: {journal['section_used']} ---")
    for row in journal["spine"]:
        stamp = ", ".join(row["dates"]) or (", ".join(str(y) for y in row["years"]) or "date?")
        print(f"\n[{row['index']}] {stamp}")
        print(f"    {row['prose']}")
        for node in row["scene_detail"]:
            mark = "cached" if node["cached"] else "NOT CACHED"
            print(f"      - scene: {node['title']}  ({mark})")
            detail = node.get("detail") or {}
            where = " / ".join(p for p in (detail.get("map"), detail.get("room")) if p)
            if where:
                print(f"        where  : {where}")
            if detail.get("trigger"):
                print(f"        trigger: {detail['trigger']}")
            if detail.get("speakers"):
                print(f"        voices : {', '.join(detail['speakers'])}")
            for line in detail.get("lines", [])[:6]:
                print(f"        > {line}")
    gaps = missing_scenes(journal)
    if gaps:
        print("\n--- crawl targets (cited scenes not in cache) ---")
        for title in gaps:
            print(f"  {title}")


def to_markdown(journal: dict) -> str:
    """Render the journal as a readable draft document with an open-threads list."""
    out: list[str] = [f"# {journal['title']} -- journal draft", ""]
    if journal["identity"]:
        out.append("## At a glance")
        out.append("")
        for key, value in journal["identity"].items():
            out.append(f"- **{key}**: {value}")
        out.append("")
    out.append(f"## Narrative spine (from \"{journal['section_used']}\")")
    out.append("")
    for row in journal["spine"]:
        stamp = ", ".join(row["dates"]) or (
            ", ".join(str(y) for y in row["years"]) or "date unknown"
        )
        out.append(f"### {row['index']}. {stamp}")
        out.append("")
        out.append(row["prose"])
        out.append("")
        for node in row["scene_detail"]:
            mark = "" if node["cached"] else " _(not yet fetched)_"
            out.append(f"- **{node['label']}**{mark}")
            detail = node.get("detail") or {}
            where = " / ".join(p for p in (detail.get("map"), detail.get("room")) if p)
            if where:
                out.append(f"  - where: {where}")
            if detail.get("trigger"):
                out.append(f"  - trigger: {detail['trigger']}")
            if detail.get("plot"):
                out.append(f"  - plot: {detail['plot']}")
            for line in detail.get("lines", []):
                out.append(f"  > {line}")
        out.append("")
    gaps = missing_scenes(journal)
    if gaps:
        out.append("## Open threads (cited but not yet in cache)")
        out.append("")
        for title in gaps:
            out.append(f"- [ ] {title}")
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="character-journal",
        description="Assemble one character's chronological story spine from the wikimap cache.",
    )
    parser.add_argument("title", nargs="?", help="Article title (substring, case-insensitive).")
    parser.add_argument("--lang", default="en", help="Cache language subdir (default en).")
    parser.add_argument("--cache-dir", default=None, help="Override the cache root.")
    parser.add_argument("--section", default=None, help="Section heading to walk (default: Biography).")
    parser.add_argument("--transcript", type=int, default=3, help="Dialogue lines per scene (0 = none).")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON instead of text.")
    parser.add_argument("--md", default=None, help="Write a markdown journal draft to this path.")
    parser.add_argument("--list", action="store_true", help="List cached titles and exit.")
    args = parser.parse_args(argv)

    cache_root = Path(args.cache_dir) if args.cache_dir else ROOT / "cache"
    cache_dir = cache_root / args.lang
    if not cache_dir.is_dir():
        print(f"character-journal: no cache at {cache_dir}", file=sys.stderr)
        return 2

    corpus = load_corpus(cache_dir)
    if not corpus:
        print(f"character-journal: cache at {cache_dir} holds no articles", file=sys.stderr)
        return 2

    if args.list or not args.title:
        for title in sorted(corpus):
            print(title)
        return 0

    matches = resolve(corpus, args.title)
    if not matches:
        print(f"character-journal: '{args.title}' not found in cache ({len(corpus)} titles)", file=sys.stderr)
        return 1

    title = matches[0]
    if len(matches) > 1:
        print(f"[note] '{args.title}' matched {len(matches)} titles; using '{title}'", file=sys.stderr)

    journal = build_journal(title, corpus[title], corpus, args)
    if args.md:
        path = Path(args.md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(to_markdown(journal), encoding="utf-8")
        print(f"wrote {path}")
    if args.json:
        print(json.dumps(journal, ensure_ascii=False, indent=2))
    elif not args.md:
        print_journal(journal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
