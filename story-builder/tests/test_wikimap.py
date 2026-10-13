"""Offline tests for the wikimap package.

Every test here runs with no network access. Two strategies:

* **Synthetic pages** are built inline so the expected graph is fully known.
* **The fixture** (``tests/fixtures/pages_en.json``) is a small captured corpus
  used for structure-level invariants (node counts, edge validity), never for
  exact prose, so refreshing it does not break the suite.

Run with::

    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Make `import wikimap` work regardless of the discovery start directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wikimap import analysis, graph, wikitext  # noqa: E402
from wikimap import wiki_client  # noqa: E402
from wikimap.config import FIXTURES_DIR  # noqa: E402


class WikitextTests(unittest.TestCase):
    def test_template_names_reads_leading_name(self):
        # template_names() is documented to lowercase names.
        text = "{{Infobox character\n|name=Claire\n}}\n{{Navbox RE}}"
        self.assertEqual(wikitext.template_names(text), ["infobox character", "navbox re"])

    def test_entity_links_filters_file_and_category(self):
        text = "See [[Chris Redfield]] and [[File:x.png]] and [[Category:People]]"
        targets = [link.target for link in wikitext.entity_links(text)]
        self.assertEqual(targets, ["Chris Redfield"])

    def test_entity_links_excludes_templates_by_default(self):
        # A navbox lives inside a template; its links must not count as prose
        # mentions of this page, or every page links to every other page.
        text = "Body text.\n{{Navbox|[[Ghost Article]]}}\n"
        self.assertEqual(wikitext.entity_links(text), [])
        with_templates = wikitext.entity_links(text, include_templates=True)
        self.assertEqual([link.target for link in with_templates], ["Ghost Article"])

    def test_link_target_strips_anchor_and_label(self):
        # Regression: a link at the very end of the text must not lose its
        # trailing brackets, or its target is silently dropped.
        (link,) = wikitext.entity_links("[[Rockfort Island#History|the island]]")
        self.assertEqual(link.target, "Rockfort Island")
        self.assertEqual(link.anchor, "History")
        self.assertTrue(link.is_entity)

    def test_extract_years_filters_out_of_range(self):
        # Regression: the final characters of the text used to be truncated,
        # turning a trailing "2002." into "200" and losing the year.
        years = wikitext.extract_years("In 1998 the outbreak began; released 1899; 2002.")
        self.assertIn(1998, years)
        self.assertIn(2002, years)
        self.assertNotIn(1899, years)

    def test_to_plaintext_preserves_trailing_text(self):
        self.assertEqual(
            wikitext.to_plaintext("Released in 2002."), "Released in 2002."
        )

    def test_first_paragraph_strips_markup(self):
        text = "'''Claire Redfield''' is a [[survivor]]. She rides a bike.\n\nSecond."
        para = wikitext.first_paragraph(text)
        self.assertIn("Claire Redfield", para)
        self.assertIn("survivor", para)
        self.assertNotIn("'''", para)
        self.assertNotIn("[[", para)
        self.assertNotIn("Second", para)

    def test_remove_comments_and_tables(self):
        self.assertEqual(wikitext.remove_comments("a<!-- hidden -->b"), "ab")
        stripped = wikitext.remove_tables("{| class=x\n| cell\n|}\nafter")
        self.assertNotIn("cell", stripped)
        self.assertIn("after", stripped)

    def test_infobox_named_params(self):
        text = "{{Infobox character\n| name = Claire\n| family = [[Chris Redfield]]\n}}"
        (box,) = wikitext.infoboxes(text)
        self.assertEqual(box.get("name"), "Claire")
        self.assertEqual(box.get("family"), "[[Chris Redfield]]")


class AnalysisTests(unittest.TestCase):
    def test_classify_prefers_specific_category(self):
        self.assertEqual(analysis.classify(["Category:Bio-Organic Weapons"], []), "creature")
        self.assertEqual(analysis.classify(["Category:Characters"], []), "character")
        self.assertEqual(analysis.classify(["Category:Locations"], []), "location")

    def test_classify_from_template_when_no_category(self):
        # With no category, the infobox template name is the fallback signal
        # (documented in classify()'s module docstring); with neither, unknown.
        self.assertEqual(analysis.classify([], ["Infobox item"]), "item")
        self.assertEqual(analysis.classify([], []), "unknown")

    def test_classify_character_outranks_creature(self):
        # Regression: a character who also carries a creature/monster category
        # (Wesker, Alexia Ashford) must stay a character for story purposes,
        # because "character" outranks "creature" in the type priority.
        self.assertEqual(
            analysis.classify(
                [
                    "Category:Resident Evil CODE:Veronica characters",
                    "Category:Resident Evil 5 creatures",
                ],
                [],
            ),
            "character",
        )

    def test_classify_ignores_citation_templates(self):
        # Regression: citation/formatting templates record where a fact came
        # from, not what the subject is. Counting "Cite game" once made every
        # cited virus classify as media instead of item.
        self.assertEqual(analysis.classify([], ["Cite game"]), "unknown")
        self.assertEqual(analysis.classify([], ["Cite book", "Infobox item"]), "item")

    def test_classify_uses_head_noun_of_category(self):
        # Regression: only the final word of a category names the kind of
        # thing, so a game title inside the category must not lend its own
        # type. "Outbreak" is part of a game's name, while "...lore" is not an
        # event at all.
        self.assertEqual(
            analysis.classify(["Category:Resident Evil Outbreak lore"], []), "unknown"
        )
        self.assertEqual(
            analysis.classify(["Category:Resident Evil Outbreak characters"], []),
            "character",
        )

    def test_classify_strips_namespace_and_reads_slash_head_noun(self):
        # Regression: the "Category:" namespace must be stripped before the
        # head noun is read, and a slash inside a category name must not hide
        # it ("Viral/Biological agents" -> "agents" -> item).
        self.assertEqual(
            analysis.classify(["Category:Viral/Biological agents"], []), "item"
        )

    def test_classify_compound_beats_generic_weapon(self):
        # A Bio-Organic Weapon is a creature, not the generic "weapon" -> item.
        self.assertEqual(
            analysis.classify(["Category:Weapons", "Category:Bio-Organic Weapons"], []),
            "creature",
        )

    def test_relationships_from_infobox_fields(self):
        text = (
            "{{Infobox character\n"
            "| family = [[Chris Redfield]]\n"
            "| affiliation = [[S.T.A.R.S.]]\n"
            "| occupation = Engineer\n"
            "}}\n"
        )
        rels = {(r.target, r.label) for r in analysis.relationships("Claire Redfield", text)}
        self.assertIn(("Chris Redfield", "family"), rels)
        self.assertIn(("S.T.A.R.S.", "member_of"), rels)

    def test_relationships_ignore_self_links_and_dupes(self):
        text = (
            "{{Infobox character\n"
            "| family = [[Claire Redfield]]\n"
            "| relatives = [[Chris Redfield]], [[Chris Redfield]]\n"
            "}}\n"
        )
        rels = analysis.relationships("Claire Redfield", text)
        self.assertEqual([(r.target, r.label) for r in rels], [("Chris Redfield", "family")])


def _page(wikitext_text, categories=(), links=(), missing=False):
    page = {"wikitext": wikitext_text, "categories": list(categories), "links": list(links)}
    if missing:
        page["missing"] = True
    return page


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.pages = {
            "Claire Redfield": _page(
                "{{Infobox character\n| family = [[Chris Redfield]]\n| affiliation = [[S.T.A.R.S.]]\n}}\n"
                "'''Claire''' arrives at [[Rockfort Island]].\n{{Navbox|[[Zombie]]}}",
                categories=["Category:Characters"],
            ),
            "Chris Redfield": _page(
                "{{Infobox character\n| family = [[Claire Redfield]]\n}}\n",
                categories=["Category:Characters"],
            ),
            "Rockfort Island": _page(
                "'''Rockfort Island''' is a prison.\n",
                categories=["Category:Locations"],
            ),
            "Zombie": _page(
                "{{Infobox creature\n| species = [[T-Virus]]\n}}\n'''Zombies''' are made by the [[T-Virus]].\n",
                categories=["Category:Creatures", "Category:Bio-Organic Weapons"],
            ),
            "T-Virus": _page(
                "'''T-Virus''' is a biological agent.\n",
                categories=["Category:Items", "Category:Viruses"],
            ),
            "Ghost": _page("", missing=True),
        }

    def test_nodes_skip_missing_and_classify(self):
        km = graph.build_map(self.pages)
        types = {n.id: n.type for n in km.nodes}
        self.assertNotIn("Ghost", types)
        self.assertEqual(types["Claire Redfield"], "character")
        self.assertEqual(types["Zombie"], "creature")
        self.assertEqual(types["Rockfort Island"], "location")
        self.assertEqual(types["T-Virus"], "item")

    def test_typed_edges_only_to_fetched_nodes(self):
        km = graph.build_map(self.pages)
        typed = {(e.source, e.target, e.label) for e in km.edges if e.label != "mentions"}
        self.assertIn(("Claire Redfield", "Chris Redfield", "family"), typed)
        self.assertIn(("Zombie", "T-Virus", "species"), typed)
        # S.T.A.R.S. is not fetched, so it must not be an edge target.
        self.assertNotIn(("Claire Redfield", "S.T.A.R.S.", "member_of"), typed)
        self.assertIn("S.T.A.R.S.", km.unresolved)

    def test_mentions_come_from_wikitext_not_templates(self):
        km = graph.build_map(self.pages)
        mentions = {(e.source, e.target) for e in km.edges if e.label == "mentions"}
        # Prose link from Claire's body text is kept.
        self.assertIn(("Claire Redfield", "Rockfort Island"), mentions)
        # The navbox-only link to Zombie must NOT become a mention.
        self.assertNotIn(("Claire Redfield", "Zombie"), mentions)

    def test_every_edge_endpoint_is_a_node(self):
        km = graph.build_map(self.pages)
        ids = {n.id for n in km.nodes}
        for edge in km.edges:
            self.assertIn(edge.source, ids)
            self.assertIn(edge.target, ids)

    def test_roundtrip_serialisation(self):
        km = graph.build_map(self.pages)
        again = graph.from_dict(graph.to_dict(km))
        self.assertEqual(km.stats(), again.stats())
        self.assertEqual(
            sorted((e.source, e.target, e.label) for e in km.edges),
            sorted((e.source, e.target, e.label) for e in again.edges),
        )

    def test_export_views_render(self):
        km = graph.build_map(self.pages)
        dot = graph.to_dot(km)
        self.assertIn("digraph wikimap {", dot)
        self.assertIn('"Claire Redfield"', dot)
        markdown = graph.to_markdown(km)
        self.assertIn("# Knowledge map", markdown)
        self.assertIn("Unresolved references", markdown)

    def test_fixture_builds_a_valid_graph(self):
        data = json.loads((FIXTURES_DIR / "pages_en.json").read_text(encoding="utf-8"))
        km = graph.build_map(data["pages"], lang="en")
        ids = {n.id for n in km.nodes}
        self.assertEqual(len(km.nodes), 6)
        for edge in km.edges:
            self.assertIn(edge.target, ids)
        self.assertIn("S.T.A.R.S.", km.unresolved)


class WikiClientTests(unittest.TestCase):
    def test_chunks_splits_evenly(self):
        parts = list(wiki_client.chunks(["a", "b", "c", "d", "e"], 2))
        self.assertEqual(parts, [["a", "b"], ["c", "d"], ["e"]])

    def test_chunks_empty(self):
        self.assertEqual(list(wiki_client.chunks([], 2)), [])

    def test_offline_without_cache_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = wiki_client.WikiClient(
                lang="en", cache_dir=Path(tmp), offline=True, rate_limit_s=0.0
            )
            with self.assertRaises(wiki_client.WikiError):
                client.fetch_pages(["Claire Redfield"])


class CliTests(unittest.TestCase):
    def test_read_seeds_skips_comments_and_blanks(self):
        from wikimap import cli

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "en.txt"
            path.write_text(
                "# a comment\n\nClaire Redfield\n  Chris Redfield  \n# trailing\n",
                encoding="utf-8",
            )
            self.assertEqual(cli.read_seeds(path), ["Claire Redfield", "Chris Redfield"])

    def test_parser_accepts_crawl(self):
        from wikimap import cli

        args = cli.build_parser().parse_args(["crawl", "--seeds", "Claire Redfield", "--depth", "1"])
        self.assertEqual(args.command, "crawl")
        self.assertEqual(args.seeds, "Claire Redfield")
        self.assertEqual(args.depth, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
