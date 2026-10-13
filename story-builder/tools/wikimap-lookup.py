# wikimap-lookup -- find a cached wikimap API response by title and print its wikitext.
#
# Why this exists: cache/<lang>/*.json is keyed by a SHA-1 of the request
# parameters, so a human cannot tell which file holds which article. This tool
# scans the cache, matches the requested title case-insensitively (substring by
# default), and prints the stored wikitext -- or just one section, or just the
# lines matching a keyword.
#
# Usage
# -----
#     python tools/wikimap-lookup.py "Rodrigo Juan Raval"
#     python tools/wikimap-lookup.py raval --section Biography
#     python tools/wikimap-lookup.py raval --grep "died" --context 3
#     python tools/wikimap-lookup.py raval --list          # titles only
#     python tools/wikimap-lookup.py raval --lang es
#
# Exit codes: 0 = at least one match, 1 = no match, 2 = bad usage / no cache.
#
# NOTE: written via write_file, not create_tool -- create_tool prepends its own
# name/description header as a docstring, which collides with a module docstring.
# Hence a comment header here.

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Wiki prose is UTF-8; the Windows console defaults to cp1252 and raises
# UnicodeEncodeError on characters it cannot map (e.g. the "II" ligature in
# "Wesker's Report"). Force UTF-8 out and degrade unknown chars to "?".
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

HERE = Path(__file__).resolve().parent
# tools/ usually sits directly beside the package, so the repo root is one up.
BUILDER_DIR = HERE.parent if (HERE.parent / "wikimap").is_dir() else HERE
CACHE_ROOT = BUILDER_DIR / "cache"

HEADING_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.M)


def iter_cached_pages(cache_dir: Path):
    """Yield (title, wikitext) for every cached article fetch in a directory."""
    for path in sorted(cache_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        pages = data.get("query", {}).get("pages")
        if not pages:
            continue
        for page in pages:
            if page.get("missing"):
                continue
            revisions = page.get("revisions") or [{}]
            slots = revisions[0].get("slots", {}).get("main", {})
            wikitext = slots.get("content") or ""
            if wikitext:
                yield page.get("title", ""), wikitext


def find_section(wikitext: str, wanted: str) -> str | None:
    """Return the body of the first heading containing `wanted`, case-insensitive."""
    matches = list(HEADING_RE.finditer(wikitext))
    for index, match in enumerate(matches):
        if wanted.lower() in match.group(2).lower():
            end = matches[index + 1].start() if index + 1 < len(matches) else len(wikitext)
            return match.group(2) + "\n" + wikitext[match.end():end]
    return None


def matching_lines(wikitext: str, needle: str, context: int) -> list[str]:
    """Lines containing `needle`, with `context` lines either side, de-overlapped."""
    lines = wikitext.splitlines()
    hits = [i for i, line in enumerate(lines) if needle.lower() in line.lower()]
    shown: list[str] = []
    emitted = -1
    for index in hits:
        low, high = max(0, index - context), min(len(lines), index + context + 1)
        if low > emitted:
            shown.append("---")
        for line_no in range(max(low, emitted + 1), high):
            shown.append(lines[line_no])
            emitted = line_no
    return shown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print the cached wikitext for a wikimap article title.",
    )
    parser.add_argument("title", help="Article title (substring match, case-insensitive).")
    parser.add_argument("--lang", default="en", help="Wiki language cache subdir (default en).")
    parser.add_argument("--cache-dir", default=None, help="Override the cache root.")
    parser.add_argument("--list", action="store_true", help="Only list matching titles.")
    parser.add_argument("--section", default=None, help="Print only this section (heading substring).")
    parser.add_argument("--grep", default=None, help="Print only lines matching this text.")
    parser.add_argument("--context", type=int, default=2, help="Context lines for --grep (default 2).")
    parser.add_argument("--raw", action="store_true", help="Full wikitext, untrimmed.")
    parser.add_argument("--chars", type=int, default=1200, help="Lead chars to show by default.")
    args = parser.parse_args(argv)

    cache_dir = Path(args.cache_dir) if args.cache_dir else CACHE_ROOT / args.lang
    if not cache_dir.is_dir():
        print(f"wikimap-lookup: no cache at {cache_dir}", file=sys.stderr)
        return 2

    found = False
    for title, wikitext in iter_cached_pages(cache_dir):
        if args.title.lower() not in title.lower():
            continue
        found = True
        print(f"=== {title} ({len(wikitext)} chars wikitext) ===")
        if args.list:
            continue
        if args.section:
            body = find_section(wikitext, args.section)
            print(body if body is not None else f"(no heading matching {args.section!r})")
        elif args.grep:
            lines = matching_lines(wikitext, args.grep, args.context)
            print("\n".join(lines) if lines else f"(no line matching {args.grep!r})")
        elif args.raw:
            print(wikitext)
        else:
            print("--- headings ---")
            for match in HEADING_RE.finditer(wikitext):
                print(f"  [{'=' * len(match.group(1))}] {match.group(2)}")
            print(f"--- lead (first {args.chars} chars) ---")
            print(wikitext[: args.chars])
        print()

    if not found:
        print(f"wikimap-lookup: no cached article matching {args.title!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
