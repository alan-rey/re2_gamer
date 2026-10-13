# 09 — Story map workflow (lore knowledge graph for RE: Code Veronica)

How the `story-builder/` pipeline turns the Resident Evil Fandom wiki into an
ordered map of the CV storyline: characters, connections, timeline, items.

## The question this answers

Rebuilding the CV story needs one source of truth for *who is connected to whom,
where, and when*. Hand-copying that from wiki pages does not scale, and scraping
the rendered HTML breaks every time the wiki's skin changes. So the pipeline reads
the **raw wikitext** through the MediaWiki API — the actual source the editors
write, and stable across cosmetic changes.

## The four-stage loop

Each stage is a command. The loop is *bounded*, not a mirror of the wiki: you
grow the map one deliberate batch at a time.

```
  1. CHECK      verify the wiki, licence and that one seed resolves
  2. CRAWL      fetch seeds + expand by references -> wikimap.json/.dot/.md
  3. FRONTIER   rank unfetched references by how strongly the corpus cites them
  4. PROMOTE    copy the best candidates into seeds/en.txt and re-run CRAWL
```

Stage 4 is the only manual act, and it is a one-line edit. Everything else is
automated, which is the point: an open-ended map stops feeling infinite once it
is *ranked*.

### 1. Check the target

```bash
cd story-builder
python -m wikimap check --lang en
```

Prints the endpoint, site name, licence (Fandom is CC-BY-SA) and the first lines
of `robots.txt`, then probes one seed title. Run this before a long crawl.

### 2. Crawl and build the map

```bash
python -m wikimap crawl --depth 1 --max-pages 60
```

- `--depth 1` = one expansion round: fetch the seeds, then fetch what *they*
  reference. `--depth 0` means seeds only.
- `--max-pages` is a hard cap. Keep it small; a 25-node corpus already produces
  107 typed/mention edges.
- Output lands in `out/<lang>/` as JSON (the data), Graphviz DOT (the picture),
  and Markdown (human-readable node cards).

Render the graph with Graphviz:

```bash
dot -Tsvg out/en/wikimap.dot -o out/en/map.svg
```

### 3. Rank the frontier — the important stage

```bash
python -m wikimap frontier --depth 1 --limit 25
```

A crawl leaves hundreds of referenced-but-unfetched titles. For the current
25-node corpus that set is **729 titles**, dominated by three kinds of noise:

| Noise | Example | Why it is not an entity |
| --- | --- | --- |
| Interlanguage link | `es:Rodrigo Juan Raval` | a link to the Spanish article |
| Media/scene page | `Albert Wesker (file)` | an image or gallery page |
| Bare year | `1971` | a date, not a subject |

`frontier` removes the noise and scores what remains on *evidence*:

- **typed reference** (`×3`) — a fetched page's infobox asserts a relationship
  (`family=`, `affiliation=`, `species=`) to the candidate.
- **prose mention** (`×1`) — a fetched page links it in body text. Template and
  navbox links are excluded, so a navbar cannot inflate the count.
- **story keyword** (`×2`) — the title contains a CV-specific name, place or term
  (`ashford`, `rockfort`, `t-veronica`, …). A tie-breaker only.
- **year article** (`×1`) — kept, but labelled `timeline`, because chronology is a
  first-class axis of a story map. A year article is a legitimate timeline anchor.

Every candidate carries its reasons:

```
   1. Umbrella Pharmaceuticals  [14] typed relationship from 1 page(s); mentioned by 9 page(s)
   2. H.C.F.                    [13] typed relationship from 1 page(s); mentioned by 8 page(s)
   3. Antarctic Base            [12] mentioned by 10 page(s); story keyword: antarctic
```

### 4. Promote

Open `out/en/frontier.md`, pick the candidates that belong to the story, add them
to `seeds/en.txt` (one title per line, `#` for comments), and re-run stage 2.
Each round the map grows toward the parts of the wiki that actually co-reference,
rather than outward in every direction.

## The five axes you asked for

The map is typed, so each axis reads off a different slice of the same data:

| Axis | Where it comes from |
| --- | --- |
| **Characters** | nodes whose `type` is `character`, from `Category:` or infobox |
| **Connections** | edges: `family`, `affiliation`, `species` (typed) + mentions |
| **Timeline** | each node's `years` list, plus `timeline` candidates |
| **Items / viruses** | nodes typed `item` / `virus` (`Combat Knife`, `T-Veronica Virus`) |
| **Locations** | nodes typed `location` (`Rockfort Island`, `Antarctic Base`) |

## Is it too much information? No — but do not try to hold it all at once

The wiki is thousands of articles. The pipeline is deliberately bounded: keep
`--max-pages` small, and let the frontier tell you what matters next. A ranked
queue of 25 is readable in a minute; 729 raw titles is not. Depth-first growth
from a curated seed list is what keeps the project finishable.

## Licensing — why `cache/` and `out/` are gitignored

Fandom content is **CC-BY-SA**, which is share-alike: redistributing it drags the
obligation onto whatever contains it. So bulk-fetched text is quarantine data:

- `cache/` — verbatim API responses (gitignored)
- `out/` — generated maps, which quote that text (gitignored)

Only material we write is committed: `content/` (our own summaries) and `seeds/`
(our own curated title lists), plus the code itself.

## A caveat worth knowing: the cache is keyed by request, not by page

Responses are cached under a SHA-1 of the serialised request parameters, which
include the exact pipe-joined chunk of titles. So `--offline` only replays a
request that was *issued identically before*. If you grow `seeds/en.txt` and then
crawl offline, the new chunk is a cache miss and the run fails loudly with
`offline and response not cached` rather than silently returning a stale map.
Fix: drop `--offline` for one run (or pass `--refresh`) to warm the cache.
