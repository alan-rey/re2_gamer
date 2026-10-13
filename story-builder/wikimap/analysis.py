"""Classify wiki pages into story entities and extract typed relationships.

Classification leans on the wiki's own structured signals -- infobox template
names and category names -- rather than guessing from prose. That keeps the
step dependency-free and auditable: every node type traces back to a specific
template or category string.

Two rules keep those signals honest:

1. Only the *head noun* (the final word) of a category or template name decides
   the type, because the head noun is what actually names the kind of thing.
   ``Category:Resident Evil CODE:Veronica characters`` is therefore a
   *character*, while ``Category:Resident Evil Outbreak lore`` is **not** an
   *event* -- its head noun is "lore" and "Outbreak" is merely part of a game's
   title.
2. Only ``Infobox ...`` templates are consulted. Citation and formatting
   templates such as ``Cite game`` record *where a fact came from*, not what the
   subject *is*; counting them as signals made every cited virus look like a
   piece of media.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .wikitext import extract_years, first_paragraph, infoboxes

# Category/template head noun -> entity type. The head noun is the final word
# of the normalised name. Plurals are listed explicitly because English
# pluralisation is irregular ("companies", "viruses"), and mapping them by hand
# keeps every type decision auditable rather than hidden in a stemmer.
_HEAD_NOUN_TYPES: dict[str, str] = {
    "character": "character",
    "characters": "character",
    "creature": "creature",
    "creatures": "creature",
    "location": "location",
    "locations": "location",
    "island": "location",
    "islands": "location",
    "area": "location",
    "areas": "location",
    "organisation": "organization",
    "organisations": "organization",
    "organization": "organization",
    "organizations": "organization",
    "company": "organization",
    "companies": "organization",
    "corporation": "organization",
    "corporations": "organization",
    "faction": "organization",
    "factions": "organization",
    "team": "organization",
    "teams": "organization",
    "agency": "organization",
    "agencies": "organization",
    "department": "organization",
    "departments": "organization",
    "item": "item",
    "items": "item",
    "weapon": "item",
    "weapons": "item",
    "virus": "item",
    "viruses": "item",
    "agent": "item",
    "agents": "item",
    "mutagen": "item",
    "mutagens": "item",
    "organism": "item",
    "organisms": "item",
    "event": "event",
    "events": "event",
    "incident": "event",
    "incidents": "event",
    "outbreak": "event",
    "outbreaks": "event",
    "manga": "media",
    "comic": "media",
    "comics": "media",
    "novel": "media",
    "novels": "media",
    "film": "media",
    "films": "media",
    "game": "media",
    "games": "media",
}

# Compound signals more precise than a bare head noun. Checked first, so a
# "Bio-Organic Weapon" is a creature rather than the generic "weapon" -> item.
_COMPOUND_TYPES: tuple[tuple[str, str], ...] = (
    ("bio-organic weapon", "creature"),
    ("bio organic weapon", "creature"),
    ("b.o.w", "creature"),
)

# When a page carries signals for several types the earlier type wins. A
# character who also has a monster form (Wesker, Alexia, Alexander Ashford) is
# a *character* for story purposes, so "character" deliberately outranks
# "creature" instead of the reverse.
_TYPE_PRIORITY: tuple[str, ...] = (
    "character",
    "location",
    "organization",
    "creature",
    "item",
    "event",
    "media",
)

# Infobox parameter keys that carry a relationship to another entity.
_RELATIONSHIP_KEYS: tuple[str, ...] = (
    "family", "relatives", "relationships", "affiliation", "affiliations",
    "organization", "organisation", "faction", "employer", "occupation",
    "creator", "creators", "created by", "species", "based on",
)

# Normalised relationship key -> edge label.
_REL_LABEL: dict[str, str] = {
    "family": "family",
    "relatives": "family",
    "relationships": "related_to",
    "affiliation": "member_of",
    "affiliations": "member_of",
    "organization": "member_of",
    "organisation": "member_of",
    "faction": "member_of",
    "employer": "member_of",
    "occupation": "occupation",
    "creator": "created_by",
    "creators": "created_by",
    "created by": "created_by",
    "species": "species",
    "based on": "based_on",
}

ENTITY_TYPES: tuple[str, ...] = (
    "character", "creature", "location", "organization",
    "item", "event", "media", "unknown",
)


def _normalise(value: str) -> str:
    return value.lower().replace("_", " ").strip()


def _strip_namespace(name: str) -> str:
    """Drop a leading ``Category:`` (any casing) from a category title."""
    text = name.strip()
    if text[:9].lower() == "category:":
        return text[9:].strip()
    return text


def _head_noun(name: str) -> str:
    """Final whitespace-separated token of a normalised name."""
    tokens = name.replace(":", " ").split()
    return tokens[-1] if tokens else ""


def classify(categories: list[str], templates: list[str]) -> str:
    """Pick the most specific entity type from categories and templates."""
    category_needles = [_normalise(_strip_namespace(c)) for c in categories]

    # Only infobox templates say what a subject *is*; citations and formatting
    # templates are noise (see module docstring).
    template_needles = [
        needle
        for needle in (_normalise(t) for t in templates)
        if needle.startswith("infobox")
    ]

    # 1. Compound signals are the most precise, so they win outright.
    for text in category_needles + template_needles:
        for phrase, entity_type in _COMPOUND_TYPES:
            if phrase in text:
                return entity_type

    # 2. Head-noun signals, resolved by type priority so a character who also
    #    has a creature category still classifies as a character.
    seen_types: set[str] = set()
    for text in category_needles + template_needles:
        entity_type = _HEAD_NOUN_TYPES.get(_head_noun(text))
        if entity_type:
            seen_types.add(entity_type)
    for entity_type in _TYPE_PRIORITY:
        if entity_type in seen_types:
            return entity_type
    return "unknown"


@dataclass(frozen=True)
class Relationship:
    source: str
    target: str
    label: str


def _link_targets(value: str) -> list[str]:
    """Article titles linked from an infobox value ([[Target|label]])."""
    targets: list[str] = []
    for match in re.finditer(r"\[\[([^\[\]]+?)\]\]", value):
        inner = match.group(1)
        target = inner.split("|", 1)[0].strip().lstrip(":").partition("#")[0].strip()
        lowered = target.lower()
        if target and not lowered.startswith(("file:", "image:", "category:")):
            targets.append(target)
    return targets


def relationships(title: str, wikitext: str) -> list[Relationship]:
    """Typed relationships derived from relationship-bearing infobox fields.

    The infobox value is itself wikitext, so we reuse link parsing to recover
    the *article title* (a stable node id) rather than raw prose.
    """
    found: list[Relationship] = []
    for infobox in infoboxes(wikitext):
        for key, value in infobox.named.items():
            label = _REL_LABEL.get(_normalise(key))
            if label is None:
                continue
            for target in _link_targets(value):
                if target.lower() != title.lower():
                    found.append(Relationship(title, target, label))
    seen: set[tuple[str, str, str]] = set()
    unique: list[Relationship] = []
    for rel in found:
        key = (rel.source, rel.target, rel.label)
        if key not in seen:
            seen.add(key)
            unique.append(rel)
    return unique


def summarise(wikitext: str) -> str:
    """One-paragraph plain-text summary for a node card."""
    return first_paragraph(wikitext, max_chars=500)


def years(wikitext: str) -> list[int]:
    """Plausible in-universe years mentioned on the page."""
    return extract_years(wikitext)
