"""Command-line interface for the wikimap story-research pipeline.

Subcommands
-----------
check    Diagnose the target wiki (endpoint, licence, robots.txt) and verify
         that one seed title resolves on that language wiki.
crawl    Fetch seed articles, optionally expand by unresolved references, then
         export the knowledge map as JSON + Graphviz DOT + Markdown.
frontier Rank the unfetched references of the fetched corpus: which articles to
         seed next, and *why*, before spending any requests on them.
search   Discover candidate seed titles by full-text search.

The CLI is deliberately thin: all logic lives in the imported modules so the
same functions are testable without ever touching the network.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import frontier as frontier_mod
from .config import (
    DEFAULT_MAXLAG,
    DEFAULT_RATE_LIMIT_S,
    DEFAULT_TIMEOUT_S,
    DEFAULT_USER_AGENT,
    MAX_PAGES_DEFAULT,
    OUT_DIR,
    SEEDS_DIR,
    WIKI_HOST,
)
from .graph import build_map, to_dict, to_dot, to_markdown
from .wiki_client import WikiClient, WikiError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wikimap",
        description=(
            "Build a typed knowledge map of Resident Evil lore from a "
            "MediaWiki API (stdlib-only, cache-first, rate-limited)."
        ),
    )
    parser.add_argument("--lang", default="en", help="Wiki language prefix (en/es/de/...).")
    parser.add_argument("--host", default=WIKI_HOST, help="Wiki host (default residentevil.fandom.com).")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="HTTP User-Agent header.")
    parser.add_argument("--rate-limit", type=float, default=DEFAULT_RATE_LIMIT_S, help="Seconds between requests.")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="HTTP timeout in seconds.")
    parser.add_argument("--offline", action="store_true", help="Use the on-disk cache only; never hit the network.")
    parser.add_argument("--refresh", action="store_true", help="Ignore cached responses and re-fetch.")
    parser.add_argument("--cache-dir", default=None, help="Override the response cache directory.")
    parser.add_argument("--out-dir", default=None, help="Override the output directory (default story-builder/out/<lang>).")

    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="Diagnose the wiki and verify a seed resolves.")
    p_check.add_argument("--title", default=None, help="Seed title to probe (default: first line of the seed file).")

    p_crawl = sub.add_parser("crawl", help="Fetch seeds, build the map, export it.")
    p_crawl.add_argument("--seeds", default=None, help="Comma-separated seed titles.")
    p_crawl.add_argument("--seed-file", default=None, help="File with one title per line (# comments allowed).")
    p_crawl.add_argument("--depth", type=int, default=0, help="Expansion rounds via unresolved references (0 = seeds only).")
    p_crawl.add_argument("--max-pages", type=int, default=MAX_PAGES_DEFAULT, help="Hard cap on pages fetched.")
    p_crawl.add_argument("--formats", default="json,dot,md", help="Comma-separated exports: json,dot,md.")

    p_frontier = sub.add_parser(
        "frontier",
        help="Rank the unfetched references of the fetched corpus (what to fetch next, and why).",
    )
    p_frontier.add_argument("--seeds", default=None, help="Comma-separated seed titles.")
    p_frontier.add_argument("--seed-file", default=None, help="File with one title per line (# comments allowed).")
    p_frontier.add_argument("--depth", type=int, default=0, help="Expansion rounds before ranking (0 = seeds only).")
    p_frontier.add_argument("--max-pages", type=int, default=MAX_PAGES_DEFAULT, help="Hard cap on pages fetched.")
    p_frontier.add_argument("--limit", type=int, default=40, help="How many candidates to report (0 = all).")
    p_frontier.add_argument("--formats", default="md,json", help="Comma-separated exports: json,md.")

    p_search = sub.add_parser("search", help="Find candidate seed titles by full-text search.")
    p_search.add_argument("query", help="Search terms.")
    p_search.add_argument("--limit", type=int, default=20, help="Maximum results.")

    return parser


def make_client(args: argparse.Namespace) -> WikiClient:
    return WikiClient(
        lang=args.lang,
        host=args.host,
        user_agent=args.user_agent,
        rate_limit_s=args.rate_limit,
        timeout=args.timeout,
        offline=args.offline,
        refresh=args.refresh,
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        maxlag=DEFAULT_MAXLAG,
    )


def default_seed_file(lang: str) -> Path | None:
    exact = SEEDS_DIR / f"{lang}.txt"
    if exact.exists():
        return exact
    english = SEEDS_DIR / "en.txt"
    if english.exists():
        return english
    return None


def read_seeds(path: Path) -> list[str]:
    """One title per line; blank lines and `#` comments are ignored."""
    titles: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.lstrip().startswith("#"):
            titles.append(stripped)
    return titles


def resolve_seeds(args: argparse.Namespace) -> list[str]:
    if args.seeds:
        return [s.strip() for s in args.seeds.split(",") if s.strip()]
    if args.seed_file:
        return read_seeds(Path(args.seed_file))
    path = default_seed_file(args.lang)
    if path is None:
        raise WikiError(
            "no seeds given and no seed file found; pass --seeds or create "
            f"{SEEDS_DIR / '<lang>.txt'}"
        )
    return read_seeds(path)


def crawl_pages(
    client: WikiClient,
    seeds: list[str],
    depth: int = 0,
    max_pages: int = MAX_PAGES_DEFAULT,
) -> dict:
    """Fetch seeds, optionally expand via unresolved references, return pages.

    This is the shared engine behind `crawl` and the `frontier` command. It is
    split out from `crawl` because the frontier ranker needs the *raw* page
    dicts: it scores candidates by how many fetched pages reference them, and
    that reference evidence is thrown away once a `KnowledgeMap` is built.

    Returns the `fetch_extended` output, keyed by article title.
    """
    pages: dict = {}
    wanted = list(dict.fromkeys(t.strip() for t in seeds if t.strip()))
    for _ in range(max(0, depth) + 1):
        room = max(0, max_pages - len(pages))
        new = [t for t in wanted if t and t not in pages][:room]
        if not new:
            break
        fetched = client.fetch_extended(new)
        pages.update(fetched)
        wanted = build_map(pages, lang=client.lang).unresolved
    return pages


def crawl(
    client: WikiClient,
    seeds: list[str],
    depth: int = 0,
    max_pages: int = MAX_PAGES_DEFAULT,
):
    """Fetch seeds, optionally expand via unresolved references, build the map.

    Returns the `KnowledgeMap` after the final round. The frontier for each
    expansion round is the set of article titles referenced by fetched pages
    but not themselves fetched (the map's `unresolved` list), so a small seed
    set can grow toward the parts of the wiki that actually co-reference.
    """
    pages = crawl_pages(client, seeds, depth=depth, max_pages=max_pages)
    return build_map(pages, lang=client.lang)


def cmd_check(args: argparse.Namespace, client: WikiClient) -> int:
    print(f"endpoint : {client.endpoint}")
    info = client.siteinfo()
    general = info.get("general", {})
    print(f"site     : {general.get('sitename', '?')} (generator {general.get('generator', '?')})")
    licence = client.licence()
    print(f"licence  : {licence.get('text', '?')} -> {licence.get('url', '?')}")
    robots = client.robots_txt()
    print("robots.txt (first 400 chars):")
    print(robots[:400])

    title = args.title
    if title is None:
        path = default_seed_file(args.lang)
        if path is not None:
            seeds = read_seeds(path)
            title = seeds[0] if seeds else None
    if title:
        pages = client.fetch_pages([title])
        page = pages.get(title, {})
        if page.get("missing"):
            print(f"seed probe: '{title}' -> MISSING on this wiki")
        else:
            print(f"seed probe: '{title}' -> OK ({len(page.get('wikitext', '') or '')} chars wikitext)")
    print(f"requests  : {client.request_count} (cache hits: {client.cache_hits})")
    return 0


def cmd_crawl(args: argparse.Namespace, client: WikiClient) -> int:
    seeds = resolve_seeds(args)
    km = crawl(client, seeds, depth=args.depth, max_pages=args.max_pages)
    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR / client.lang
    out_dir.mkdir(parents=True, exist_ok=True)

    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    written: list[str] = []
    if "json" in formats:
        path = out_dir / "wikimap.json"
        path.write_text(
            json.dumps(to_dict(km), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written.append(str(path))
    if "dot" in formats:
        path = out_dir / "wikimap.dot"
        path.write_text(to_dot(km), encoding="utf-8")
        written.append(str(path))
    if "md" in formats:
        path = out_dir / "wikimap.md"
        path.write_text(to_markdown(km), encoding="utf-8")
        written.append(str(path))

    print(f"stats: {json.dumps(km.stats(), sort_keys=True)}")
    for path in written:
        print(f"wrote  {path}")
    return 0


def cmd_frontier(args: argparse.Namespace, client: WikiClient) -> int:
    seeds = resolve_seeds(args)
    pages = crawl_pages(client, seeds, depth=args.depth, max_pages=args.max_pages)

    # A seed that came back MISSING is a dead title, and an already-fetched page
    # is not a frontier at all. Excluding both means the queue offers only new,
    # reachable work rather than replaying questions already answered.
    dead = [title for title, page in pages.items() if page.get("missing")]
    limit = args.limit if args.limit > 0 else -1
    ranked = frontier_mod.rank_candidates(
        pages, limit=limit, exclude=list(seeds) + dead
    )

    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR / client.lang
    out_dir.mkdir(parents=True, exist_ok=True)
    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    written: list[str] = []
    if "md" in formats:
        path = out_dir / "frontier.md"
        path.write_text(frontier_mod.to_markdown(ranked), encoding="utf-8")
        written.append(str(path))
    if "json" in formats:
        path = out_dir / "frontier.json"
        path.write_text(
            json.dumps(frontier_mod.to_dict(ranked), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(str(path))

    fetched = sum(1 for page in pages.values() if not page.get("missing"))
    print(f"corpus : {fetched} page(s) fetched, {len(dead)} missing")
    print(f"queue  : {len(ranked)} candidate(s) ranked")
    for path in written:
        print(f"wrote  {path}")

    for index, candidate in enumerate(ranked[:10], start=1):
        why = "; ".join(candidate.reasons) or "-"
        print(f"  {index:>2}. {candidate.title}  [{candidate.score}] {why}")
    return 0


def cmd_search(args: argparse.Namespace, client: WikiClient) -> int:
    for title in client.search(args.query, limit=args.limit):
        print(title)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    client = make_client(args)
    try:
        if args.command == "check":
            return cmd_check(args, client)
        if args.command == "crawl":
            return cmd_crawl(args, client)
        if args.command == "frontier":
            return cmd_frontier(args, client)
        if args.command == "search":
            return cmd_search(args, client)
    except WikiError as exc:
        print(f"wikimap: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
