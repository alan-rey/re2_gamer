"""Rank the crawl frontier: which unfetched articles to fetch next, and why.

Why this module exists
----------------------
``cli.crawl`` expands by fetching *every* title referenced by an already-fetched
page but not itself fetched (``KnowledgeMap.unresolved``). On a Fandom wiki that
set is both huge and noisy. A 25-node Code Veronica corpus produced 729
unresolved titles, dominated by three kinds of junk:

* interlanguage links -- ``es:Rodrigo Juan Raval`` (a link to the Spanish
  article, not a new entity),
* media/scene pages -- ``Albert Wesker (file)``,
* bare year articles -- ``1971``.

Fetching that set in file order is what makes an open-ended map feel infinite
and unusable. It is not infinite; it is *unranked*.

What this module does
---------------------
It turns the flat set into an ordered work queue whose score is evidence, not
guesswork. For each unfetched title it counts how the *already fetched* corpus
refers to it:

* ``typed_refs``   -- pages whose infobox carries a relationship to it
                      (``family=``, ``affiliation=``, ``species=``). A stated
                      relationship is a strong signal.
* ``mention_refs`` -- pages whose prose links it (template links excluded, the
                      same rule ``graph.py`` uses, so navboxes cannot inflate
                      the count).

Noise is removed before scoring (``interwiki``, ``media``, ``meta``), and bare
years are kept but labelled ``timeline`` because chronology is a first-class
axis of a story map -- a year article is a legitimate timeline anchor.

Every candidate carries human-readable ``reasons``, so an agent or a person can
accept or reject the next batch without rebuilding the graph to find out why a
name was proposed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .analysis import relationships
from .wikitext import entity_links

# --- Tuning constants --------------------------------------------------------

TYPED_WEIGHT = 3        # an infobox relationship pointing at the candidate
MENTION_WEIGHT = 1      # one page mentioning the candidate in prose
KEYWORD_BONUS = 2       # per story keyword appearing in the title
TIMELINE_BONUS = 1      # a bare year article, kept as a chronology anchor

# Link prefixes that point at another *language edition*, not a new entity.
INTERWIKI_PREFIXES: tuple[str, ...] = (
    "es:", "it:", "pt:", "pt-br:", "fr:", "de:", "ja:", "ru:", "zh:", "pl:",
    "nl:", "ko:", "fi:", "sv:", "tr:", "ar:", "hu:", "cs:", "el:", "he:",
    "uk:", "vi:", "th:", "id:", "no:", "da:", "ro:", "bg:", "hr:", "sk:",
)

# Title suffixes that denote a file/gallery/scene page rather than an entity.
MEDIA_SUFFIXES: tuple[str, ...] = (
    " (file)", " (image)", " (scene)", " (soundtrack)", " (music)",
    " (video)", " (photo)", " (audio)", " (gallery)", " (disambiguation)",
)

# Namespace prefixes that should never have reached the frontier at all.
META_PREFIXES: tuple[str, ...] = (
    "template:", "category:", "file:", "image:", "help:", "special:",
    "user:", "wikipedia:", "talk:", "mediawiki:",
)

YEAR_TITLE_RE = re.compile(r"^(1[89]\d{2}|20\d{2})$")

# Names and places that carry the Code Veronica storyline. Used only as a
# tie-breaker between candidates that the corpus references equally.
STORY_KEYWORDS: tuple[str, ...] = (
    "ashford", "redfield", "wesker", "rockfort", "umbrella", "veronica",
    "t-virus", "t-veronica", "progenitor", "bandersnatch", "tyrant", "hunter",
    "h.c.f.", "alexander", "alexia", "alfred", "steve", "burnside", "claire",
    "chris", "raval", "antonio", "taylor", "antarctic", "antarctica",
    "prison", "island", "virus", "s.t.a.r.s.",
)


@dataclass(frozen=True)
class Candidate:
    """One unfetched article proposed as the next thing to fetch."""

    title: str
    score: int
    kind: str                                   # article | timeline
    typed_refs: tuple[str, ...] = ()            # pages asserting a relationship
    mention_refs: tuple[str, ...] = ()          # pages linking it in prose
    reasons: tuple[str, ...] = ()

    @property
    def refs(self) -> int:
        """Number of distinct fetched pages that reference this candidate."""
        return len(set(self.typed_refs) | set(self.mention_refs))


def classify_candidate(title: str) -> str:
    """Guess what a frontier title is: article, timeline, media, interwiki, meta.

    This runs on raw link targets, before any fetch, so it can only use the
    title's shape. It is used to *filter* noise, not to type entities -- the
    real typing happens in ``analysis.classify`` once a page is fetched.
    """
    lowered = title.strip().lower()
    if lowered.startswith(INTERWIKI_PREFIXES):
        return "interwiki"
    if lowered.startswith(META_PREFIXES):
        return "meta"
    if lowered.endswith(MEDIA_SUFFIXES):
        return "media"
    if YEAR_TITLE_RE.match(title.strip()):
        return "timeline"
    return "article"


def _reference_counts(
    pages: dict[str, dict[str, Any]], excluded_lower: set[str]
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Build typed- and mention-reference maps for every unfetched target."""
    typed: dict[str, set[str]] = {}
    mention: dict[str, set[str]] = {}
    for title, page in pages.items():
        if page.get("missing"):
            continue
        wikitext = page.get("wikitext", "") or ""
        for rel in relationships(title, wikitext):
            if rel.target.lower() not in excluded_lower:
                typed.setdefault(rel.target, set()).add(title)
        for link in entity_links(wikitext):
            if link.target.lower() not in excluded_lower:
                mention.setdefault(link.target, set()).add(title)
    return typed, mention


def rank_candidates(
    pages: dict[str, dict[str, Any]],
    *,
    limit: int = 40,
    exclude: Iterable[str] = (),
) -> list[Candidate]:
    """Rank unfetched referenced titles as a work queue, best first.

    `pages` is the ``fetch_pages``/``fetch_extended`` output. `exclude` holds
    titles already handled elsewhere -- promoted seeds, or seeds that came back
    MISSING -- so a resolved question does not reappear as a recommendation.
    """
    fetched_lower = {
        title.lower() for title, page in pages.items() if not page.get("missing")
    }
    excluded_lower = fetched_lower | {e.strip().lower() for e in exclude}
    excluded_lower.discard("")

    typed, mention = _reference_counts(pages, excluded_lower)

    candidates: list[Candidate] = []
    for title in set(typed) | set(mention):
        kind = classify_candidate(title)
        if kind in ("interwiki", "media", "meta"):
            continue

        typed_refs = sorted(typed.get(title, ()))
        mention_refs = sorted(mention.get(title, ()))

        score = len(typed_refs) * TYPED_WEIGHT + len(mention_refs) * MENTION_WEIGHT
        reasons: list[str] = []
        if typed_refs:
            reasons.append(f"typed relationship from {len(typed_refs)} page(s)")
        if mention_refs:
            reasons.append(f"mentioned by {len(mention_refs)} page(s)")

        lowered = title.lower()
        hits = sorted({word for word in STORY_KEYWORDS if word in lowered})
        if hits:
            score += KEYWORD_BONUS * len(hits)
            reasons.append("story keyword: " + ", ".join(hits))

        if kind == "timeline":
            score += TIMELINE_BONUS
            reasons.append("year article (timeline anchor)")

        candidates.append(
            Candidate(
                title=title,
                score=score,
                kind=kind,
                typed_refs=tuple(typed_refs),
                mention_refs=tuple(mention_refs),
                reasons=tuple(reasons),
            )
        )

    # Deterministic: score descending, then title ascending. Without the title
    # tie-break, equal-scored candidates would order differently per run and
    # the exported queue would not be reproducible.
    candidates.sort(key=lambda c: (-c.score, c.title))
    return candidates[:limit] if limit >= 0 else candidates


def to_dict(candidates: list[Candidate]) -> list[dict[str, Any]]:
    """JSON-serialisable form of a ranked queue."""
    return [
        {
            "title": c.title,
            "score": c.score,
            "kind": c.kind,
            "refs": c.refs,
            "typed_refs": list(c.typed_refs),
            "mention_refs": list(c.mention_refs),
            "reasons": list(c.reasons),
        }
        for c in candidates
    ]


def to_markdown(candidates: list[Candidate], *, limit: int | None = None) -> str:
    """Render the queue as a reviewable report."""
    rows = candidates if limit is None else candidates[:limit]
    lines = [
        "# Story map frontier",
        "",
        "Unfetched articles referenced by the fetched corpus, ranked by how",
        "strongly it references them. Promote the top of this list into",
        "`seeds/<lang>.txt` and re-run `crawl` to grow the map.",
        "",
        "| # | Title | Score | Kind | Refs | Why |",
        "|---|-------|-------|------|------|-----|",
    ]
    for index, candidate in enumerate(rows, start=1):
        why = "; ".join(candidate.reasons) or "-"
        title = candidate.title.replace("|", "\\|")
        lines.append(
            f"| {index} | {title} | {candidate.score} | {candidate.kind} "
            f"| {candidate.refs} | {why} |"
        )
    lines.append("")
    return "\n".join(lines)
