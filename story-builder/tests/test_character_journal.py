"""Offline tests for tools/character-journal.py's scene binding.

The module filename contains a hyphen, so it cannot be imported by name; it is
loaded from its path with importlib instead.

These tests pin the fix for a real wiki collision: the cutscene page
"Thrown into despair" and the music page "Thrown Into Despair" differ only by
case, and the old case-insensitive-only binder attached a music track (no Plot,
no Transcript) to a cutscene citation, emptying the most important beat of
Rodrigo's journal. resolve_scene() must always choose the cutscene.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CUTSCENE = "{{Infobox cutscene\n|name = Thrown into despair\n}}\n== Plot ==\nRodrigo opens her cell."
MUSIC = "{{Infobox music\n|name = Thrown Into Despair\n}}\nIt's a piece of music."


def _load_module():
    path = ROOT / "tools" / "character-journal.py"
    spec = importlib.util.spec_from_file_location("character_journal", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["character_journal"] = module
    spec.loader.exec_module(module)
    return module


cj = _load_module()


class ResolveSceneTests(unittest.TestCase):
    def test_prefers_cutscene_over_case_colliding_music_page(self):
        # Order A: the music page is reached first, which is what used to break.
        corpus = {"Thrown Into Despair": MUSIC, "Thrown into despair": CUTSCENE}
        self.assertEqual(cj.resolve_scene(corpus, "Thrown into despair"), "Thrown into despair")

        # Order B: reversed insertion order must give the same answer.
        corpus = {"Thrown into despair": CUTSCENE, "Thrown Into Despair": MUSIC}
        self.assertEqual(cj.resolve_scene(corpus, "Thrown into despair"), "Thrown into despair")

    def test_exact_title_wins_outright(self):
        corpus = {"Thrown into despair": CUTSCENE, "thrown INTO despair": MUSIC}
        self.assertEqual(cj.resolve_scene(corpus, "Thrown into despair"), "Thrown into despair")

    def test_unmatched_title_returns_none(self):
        self.assertIsNone(cj.resolve_scene({"Something else": CUTSCENE}, "Not cached"))

    def test_is_cutscene_detects_infobox(self):
        self.assertTrue(cj.is_cutscene(CUTSCENE))
        self.assertFalse(cj.is_cutscene(MUSIC))


if __name__ == "__main__":
    unittest.main()
