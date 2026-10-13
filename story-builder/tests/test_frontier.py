"""Offline tests for the crawl-frontier ranker.

The ranker decides *what to fetch next*, so its tests assert the two things
that matter operationally:

* **noise never reaches the queue** (interlanguage links, ``(file)`` pages,
  ``Template:``/``Category:`` namespaced titles), and
* **ordering is evidence-based and reproducible** -- typed infobox
  relationships outrank prose mentions, and equal scores break ties on title
  so two runs produce the identical queue.

Run with::

    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# Make `import wikimap` work regardless of the discovery start directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wikimap import frontier  # noqa: E402
from wikimap.config import FIXTURES_DIR  # noqa: E402


def _page(wikitext, categories=(), links=(), missing=False):
    """Minimal stand-in for one `fetch_extended` result entry."""
    return {
        "title": "",
        "missing": missing,
        "wikitext": wikitext,
        "categories": list(categories),
        "links": list(links),
        "redirected_from": None,
    }


class ClassifyCandidateTests(unittest.TestCase):
    def test_interwiki_link_is_recognised(self):
        # A Spanish-edition link is the same entity in another language, so
        # fetching it would duplicate a node rather than add information.
        self.assertEqual(frontier.classify_candidate("es:Claire Redfield"), "interwiki")

    def test_media_page_is_recognised(self):
        self.assertEqual(frontier.classify_candidate("Albert Wesker (file)"), "media")

    def test_meta_namespace_is_recognised(self):
        self.assertEqual(frontier.classify_candidate("Template:Navbox"), "meta")
        self.assertEqual(frontier.classify_candidate("Category:Characters"), "meta")

    def test_bare_year_is_a_timeline_anchor(self):
        # Years are kept: chronology is a first-class axis of the story map.
        self.assertEqual(frontier.classify_candidate("1998"), "timeline")

    def test_ordinary_title_is_an_article(self):
        self.assertEqual(frontier.classify_candidate("Rockfort Island"), "article")

    def test_media_suffix_match_is_case_insensitive(self):
        self.assertEqual(frontier.classify_candidate("Chris Redfield (FILE)"), "media")


class RankCandidateTests(unittest.TestCase):
    def test_typed_relationship_outranks_prose_mention(self):
        pages = {
            "Hub": _page(
                "{{Infobox character\n| family = [[Beta]]\n}}\n"
                "'''Hub''' travelled with [[Gamma]].\n"
            )
        }
        ranked = {c.title: c for c in frontier.rank_candidates(pages, limit=-1)}

        # Beta is referenced by an infobox field (a stated relation); Gamma only
        # by a prose link. The stated relation must win.
        self.assertIn("Beta", ranked)
        self.assertIn("Gamma", ranked)
        self.assertGreater(ranked["Beta"].score, ranked["Gamma"].score)
        self.assertEqual(ranked["Beta"].typed_refs, ("Hub",))
        self.assertEqual(ranked["Beta"].mention_refs, ())
        self.assertEqual(ranked["Gamma"].mention_refs, ("Hub",))

    def test_noise_is_filtered_out_of_the_queue(self):
        pages = {
            "Hub": _page(
                "'''Hub''' saw [[es:Claire Redfield]], [[Albert Wesker (file)]], "
                "[[Category:Characters]] and [[Rockfort Island]].\n"
            )
        }
        titles = [c.title for c in frontier.rank_candidates(pages, limit=-1)]
        self.assertEqual(titles, ["Rockfort Island"])

    def test_years_are_kept_but_labelled_timeline(self):
        pages = {"Hub": _page("'''Hub''' arrived in [[1998]].\n")}
        ranked = frontier.rank_candidates(pages, limit=-1)
        self.assertEqual([c.title for c in ranked], ["1998"])
        self.assertEqual(ranked[0].kind, "timeline")
        self.assertIn("year article (timeline anchor)", ranked[0].reasons)

    def test_fetched_pages_never_appear_as_candidates(self):
        pages = {
            "Hub": _page("'''Hub''' met [[Beta]].\n"),
            "Beta": _page("'''Beta''' is a person.\n"),
        }
        self.assertEqual(frontier.rank_candidates(pages, limit=-1), [])

    def test_missing_pages_do_not_count_as_references(self):
        pages = {
            "Hub": _page("'''Hub''' met [[Beta]].\n"),
            "Ghost": _page("", missing=True),
        }
        ranked = frontier.rank_candidates(pages, limit=-1)
        self.assertEqual([c.title for c in ranked], ["Beta"])
        self.assertEqual(ranked[0].mention_refs, ("Hub",))

    def test_exclude_drops_named_titles(self):
        pages = {"Hub": _page("'''Hub''' met [[Beta]] and [[Gamma]].\n")}
        ranked = frontier.rank_candidates(pages, limit=-1, exclude=["Beta"])
        self.assertEqual([c.title for c in ranked], ["Gamma"])

    def test_ordering_is_deterministic_on_ties(self):
        # Equal evidence, so the title tie-break decides -- otherwise the
        # exported queue would shuffle between runs.
        pages = {"Hub": _page("'''Hub''' met [[Zeta]] and [[Alpha]].\n")}
        first = [c.title for c in frontier.rank_candidates(pages, limit=-1)]
        second = [c.title for c in frontier.rank_candidates(pages, limit=-1)]
        self.assertEqual(first, ["Alpha", "Zeta"])
        self.assertEqual(first, second)

    def test_limit_truncates_and_negative_limit_returns_all(self):
        pages = {"Hub": _page("'''Hub''' met [[Alpha]], [[Beta]] and [[Gamma]].\n")}
        self.assertEqual(len(frontier.rank_candidates(pages, limit=2)), 2)
        self.assertEqual(len(frontier.rank_candidates(pages, limit=-1)), 3)

    def test_refs_counts_distinct_referencing_pages(self):
        pages = {
            "Hub": _page("'''Hub''' met [[Beta]].\n"),
            "Aide": _page("'''Aide''' also met [[Beta]].\n"),
        }
        ranked = frontier.rank_candidates(pages, limit=-1)
        self.assertEqual(ranked[0].refs, 2)

    def test_story_keyword_lifts_a_tie(self):
        pages = {"Hub": _page("'''Hub''' met [[Alpha]] and [[Umbrella Corporation]].\n")}
        titles = [c.title for c in frontier.rank_candidates(pages, limit=-1)]
        self.assertEqual(titles[0], "Umbrella Corporation")


class ReportTests(unittest.TestCase):
    def setUp(self):
        pages = {"Hub": _page("'''Hub''' met [[Beta]] in [[1998]].\n")}
        self.ranked = frontier.rank_candidates(pages, limit=-1)

    def test_to_dict_is_json_serialisable_with_expected_keys(self):
        payload = json.dumps(frontier.to_dict(self.ranked))
        decoded = json.loads(payload)
        self.assertTrue(decoded)
        self.assertEqual(
            set(decoded[0]),
            {"title", "score", "kind", "refs", "typed_refs", "mention_refs", "reasons"},
        )

    def test_to_markdown_renders_a_table_row_per_candidate(self):
        text = frontier.to_markdown(self.ranked)
        self.assertIn("| # | Title | Score | Kind | Refs | Why |", text)
        for candidate in self.ranked:
            self.assertIn(candidate.title, text)

    def test_to_markdown_limit_restricts_rows(self):
        text = frontier.to_markdown(self.ranked, limit=1)
        self.assertIn(self.ranked[0].title, text)
        for later in self.ranked[1:]:
            self.assertNotIn(later.title, text)


class FixtureFrontierTests(unittest.TestCase):
    """Structure invariants against the captured corpus, not exact prose."""

    def setUp(self):
        raw = (FIXTURES_DIR / "pages_en.json").read_text(encoding="utf-8")
        self.pages = json.loads(raw)["pages"]

    def test_fixture_yields_a_nonempty_queue_of_unfetched_titles(self):
        ranked = frontier.rank_candidates(self.pages, limit=-1)
        self.assertTrue(ranked)
        for candidate in ranked:
            self.assertNotIn(candidate.title, self.pages)

    def test_fixture_queue_is_sorted_by_descending_score(self):
        scores = [c.score for c in frontier.rank_candidates(self.pages, limit=-1)]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_fixture_queue_contains_no_noise_kinds(self):
        ranked = frontier.rank_candidates(self.pages, limit=-1)
        self.assertTrue({c.kind for c in ranked} <= {"article", "timeline"})

    def test_every_candidate_carries_a_reason(self):
        for candidate in frontier.rank_candidates(self.pages, limit=-1):
            self.assertTrue(candidate.reasons)


if __name__ == "__main__":
    unittest.main()
