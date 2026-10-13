---
name: re-lore-frontier-triage
description: Use when growing the Resident Evil lore knowledge map in story-builder/ — crawling the Fandom wiki, ranking the unfetched-reference frontier, or promoting candidates into seeds/en.txt. Covers the check/crawl/frontier/promote loop, the evidence-weighted scoring, and the SHA-1 request cache gotcha.
tags: resident-evil, wiki, knowledge-graph, story-builder, frontier
---

# RE lore frontier triage

## When to use

Any task that grows or refreshes the Code Veronica lore map under
`story-builder/`: adding characters/items/locations to the graph, deciding
"what should I fetch next", or re-running a crawl after editing `seeds/en.txt`.

## Preconditions

- Work from `story-builder/` (the package root; `python -m wikimap` needs it).
- No pip installs: the package is stdlib-only.
- Tests: `python -m unittest discover -s tests -t .` (pytest is NOT installed).

## Steps

1. **Verify the wiki before a long run.**
   ```bash
   python -m wikimap check --lang en
   ```
   Confirms endpoint, licence (CC-BY-SA) and that the first seed resolves.

2. **Crawl a bounded corpus.** Keep `--max-pages` small; the map is grown
   deliberately, not mirrored.
   ```bash
   python -m wikimap crawl --depth 1 --max-pages 60
   ```
   Writes `out/en/wikimap.json`, `.dot`, `.md`.

3. **Rank what to fetch next.**
   ```bash
   python -m wikimap frontier --depth 1 --limit 25
   ```
   Reads the corpus, drops interwiki / media / bare-year noise, and scores the
   rest: typed infobox relationship x3, prose mention x1 (template/navbox links
   excluded), story keyword x2, year article x1 (kept as a timeline anchor).
   Output: `out/en/frontier.md` + `frontier.json`, top 10 echoed to stdout.

4. **Promote by hand, then repeat.** Copy the story-relevant top candidates into
   `seeds/en.txt`, one title per line, and re-run step 2. Hand-editing is the
   loop's only manual act by design.

## Reading the graph

Nodes are dicts keyed `id` (not `title`), with `type`, `summary`, `years`,
`categories`. Map JSON top-level keys: `lang`, `nodes`, `edges`, `unresolved`,
`stats`. Useful baselines: a 25-node corpus yields ~107 edges and ~729
unresolved titles — that ratio is why ranking exists.

## Caveats

- **The cache is keyed by SHA-1 of the request params**, including the exact
  pipe-joined title chunk. `--offline` only replays a byte-identical request.
  After editing `seeds/en.txt`, an offline run fails with
  `offline and response not cached`. Fix: run once without `--offline`.
- **Never commit `cache/` or `out/`** — verbatim CC-BY-SA wiki text. Only
  `content/`, `seeds/` and code are committed. Check `.gitignore` before adding.
- Rate limit defaults to 1 req/s with an identifying User-Agent. Do not bulk-crawl
  faster; Fandom will ban the IP.
- The seeded corpus is cached for the *old* request chunk, so a stale cache is a
  normal, expected condition — not a bug in the ranker.
