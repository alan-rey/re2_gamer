"""Paths, API endpoints and tunable defaults for the wikimap pipeline.

Single source of truth for locations, matching the house rule in
docs/08-conventions.md (no hardcoded paths scattered through the code).
"""

from __future__ import annotations

from pathlib import Path

# --- Layout ------------------------------------------------------------------
PKG_DIR: Path = Path(__file__).resolve().parent
BUILDER_DIR: Path = PKG_DIR.parent          # story-builder/
WORKSPACE_DIR: Path = BUILDER_DIR.parent    # repo root

CACHE_DIR: Path = BUILDER_DIR / "cache"     # raw API responses (gitignored)
OUT_DIR: Path = BUILDER_DIR / "out"         # generated maps
SEEDS_DIR: Path = BUILDER_DIR / "seeds"     # curated inputs
FIXTURES_DIR: Path = BUILDER_DIR / "tests" / "fixtures"

# --- Wiki --------------------------------------------------------------------
# Fandom serves every language from one host with a path prefix:
#   https://residentevil.fandom.com/api.php      (English)
#   https://residentevil.fandom.com/es/api.php   (Spanish)
# That is why the user-facing URL .../es/wiki/Claire_Redfield maps to /es/api.php.
WIKI_HOST: str = "residentevil.fandom.com"
ENGLISH_PREFIXES: frozenset[str] = frozenset({"", "en"})

# Languages known to exist on this wiki family. `check` verifies live.
SUPPORTED_LANGS: tuple[str, ...] = ("en", "es", "de", "fr", "it", "pt", "ru", "ja", "zh")

# Fandom's API terms ask for an identifying User-Agent and a low request rate.
# Bulk-crawling a fan wiki without this gets the project IP-banned.
DEFAULT_USER_AGENT: str = (
    "ps2_gamer-story-builder/0.1 "
    "(unofficial non-commercial RE:CV fan-reimagining research tool)"
)
DEFAULT_RATE_LIMIT_S: float = 1.0
DEFAULT_TIMEOUT_S: float = 30.0
DEFAULT_MAXLAG: int = 5

# --- Crawl bounds ------------------------------------------------------------
# The whole wiki is thousands of articles. These defaults deliberately keep a
# run small; the point is a bounded map, not a mirror.
MAX_PAGES_DEFAULT: int = 60
FETCH_CHUNK: int = 20          # titles per revisions query (API caps at 50)
PROP_LIMIT: int = 500          # per-module page limit for anonymous clients

# Namespaces we never want as graph nodes (files, categories, templates, ...).
SKIP_TITLE_PREFIXES: tuple[str, ...] = (
    "file:", "image:", "category:", "template:", "help:", "forum:",
    "user:", "talk:", "mediawiki:", "module:", "special:", "blog:",
    "w:", "wikipedia:", "wikt:",
)
