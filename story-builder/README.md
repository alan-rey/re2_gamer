# story-builder — lore knowledge map for RE: Code Veronica X

This folder holds the tooling that turns the **Resident Evil Fandom wiki** into a
machine-readable map of the story: characters, creatures, locations, organisations,
items and viruses, plus the typed relationships between them (family, member-of,
mentions).

It exists because rebuilding the CV story needs a single ordered source of truth for
*who is connected to whom, where, and when* — and hand-copying that from wiki pages
does not scale.

## What it produces

A `KnowledgeMap`: a set of **nodes** (one per article) and **edges** (typed links
between them), exported three ways:

| File | Format | Use |
| --- | --- | --- |
| `out/<lang>/wikimap.json` | JSON | The data. Feed into later phases, diff across runs. |
| `out/<lang>/wikimap.dot` | Graphviz DOT | Render the graph (`dot -Tsvg wikimap.dot -o map.svg`). |
| `out/<lang>/wikimap.md` | Markdown | Human-readable node cards + unresolved references. |

## Why the MediaWiki API, not HTML scraping

The wiki runs MediaWiki. Scraping rendered HTML means parsing navigation chrome,
ads and infobox markup soup — and it breaks whenever the skin changes. The API
returns the **raw wikitext** for up to 50 titles per request, which is the actual
source of truth the editors write. So the pipeline uses
`https://residentevil.fandom.com/api.php` with `action=query&prop=revisions&rvprop=content`.

The language you were looking at, `.../es/wiki/Claire_Redfield`, maps to the API
endpoint `.../es/api.php` — same host, path prefix. That is what `--lang es` selects.

## Licensing — why `cache/` and `out/` are gitignored

Fandom content is **CC-BY-SA**. That is share-alike: redistributing it drags the
obligation onto whatever contains it. So the bulk-fetched text is treated as
quarantine data and never committed:

* `cache/` — verbatim API responses (the raw wikitext of thousands of articles)
* `out/` — generated maps, which quote that text

Only material **we** write goes into `content/` (our own summaries) and `seeds/`
(our own curated title lists). Those two are committed.

## Layout

```
story-builder/
  wikimap/            the package (stdlib only, no pip install)
    config.py         paths, endpoint, rate limit, crawl bounds
    wiki_client.py    cache-first, rate-limited API client
    wikitext.py       wikitext parsing: links, infoboxes, templates, years
    analysis.py       classify pages; extract typed relationships
    graph.py          build the map; export JSON / DOT / Markdown
    cli.py            command-line front end
  seeds/              curated seed titles (committed)
    en.txt  es.txt
  cache/              raw API responses        (gitignored)
  out/                generated maps            (gitignored)
  tests/              offline tests + captured fixture
```

## Usage

**Global flags come before the subcommand.** This is argparse behaviour, not a
bug, but it bites: `wikimap --offline check` works, `wikimap check --offline`
is rejected with `unrecognized arguments`.

```bash
# 1. Diagnose: endpoint, licence, and does a seed resolve?
python -m wikimap check

# 2. Same, on the Spanish wiki
python -m wikimap --lang es check

# 3. Find candidate seed titles when you are unsure of the exact name
python -m wikimap search "Rockfort Island"

# 4. Build the map from the curated seed list (depth 0 = seeds only)
python -m wikimap crawl --depth 0

# 5. Expand one hop out via referenced-but-unfetched articles
python -m wikimap crawl --depth 1 --max-pages 60

# 6. Re-run entirely from cache, no network
python -m wikimap --offline crawl --depth 1
```

Output lands in `out/<lang>/`. Re-runs reuse `cache/` unless you pass `--refresh`.

### A note on `--depth`

The frontier for each expansion round is the map's **unresolved list**: article
titles that fetched pages link to but which were not themselves fetched. So
`--depth 1` does not fetch "everything one link away" — it fetches the most
co-referenced missing titles, capped by `--max-pages`. It grows toward the part
of the wiki that actually hangs together, rather than spidering outward blindly.

### Politeness

Fandom's terms require an identifying `User-Agent` and a low request rate. The
defaults are a descriptive UA and **1 second between requests**. Bulk-crawling
without this gets the project IP-banned. Do not raise the rate limit.

## Tests

Fully offline — fixture-based, no network:

```bash
cd story-builder && python -m unittest discover -s tests -t . -v
```

## Known issues

* **Global flags must precede the subcommand** (argparse limitation above).
* **`robots.txt` returns HTTP 403** on this host. That is the CDN rejecting the
  path, not a licensing signal; the rate limit is the real politeness control.
* **Node resolution is title-exact.** `[[Chris Redfield]]` links resolve only if
  that exact title is fetched, so redirects and piped aliases appear under
  `unresolved` until added as seeds.
