"""wikimap -- build a knowledge map of Resident Evil lore from a MediaWiki API.

This package is the *research* half of the RE:CV reimagining. It does not touch
the game disc and it does not produce shippable content. It downloads public
wiki articles, converts them into a typed graph (characters / creatures /
locations / items / events), and exports human- and machine-readable views.

Nothing here is stdlib-only by accident: the Phase 1 rule is that a fresh
checkout runs with no install step, and this tool follows the same rule.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
