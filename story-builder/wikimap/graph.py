"""Build a typed knowledge map (nodes + edges) from fetched wiki pages.

The map has two kinds of edges:

* **Typed edges** from `analysis.relationships` -- infobox fields such as
  `family=`, `affiliation=`, `occupation=` become labels like `family`,
  `member_of`, `occupation`. These are the *structured* connections.
* **`mentions` edges** from each page's own wikitext links -- the article
  prose references another article. Weak, but useful for "who appears where".

Why mentions come from wikitext and not the API's `prop=links` list: the API
includes links injected by transcluded templates, and Fandom articles carry
navigation boxes with hundreds of such links. Counting those would wire every
character to every other character and make the graph useless. `entity_links`
parses the stored wikitext with templates excluded, so a mention means the
editors actually wrote the link in the prose.

Only edges whose target is also a fetched node are kept, so the exported graph
is self-contained. Links to articles we have not fetched are recorded in
`unresolved` so the next seed list can grow from them (see cli.py `--depth`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .analysis import (
    ENTITY_TYPES,
    classify,
    relationships,
    summarise,
    years,
)
from .wikitext import entity_links, template_names


@dataclass(frozen=True)
class Node:
    id: str                       # article title, the stable node id
    type: str                     # one of ENTITY_TYPES
    summary: str = ""             # first-paragraph plain text
    years: tuple[int, ...] = ()   # plausible in-universe years mentioned
    categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str                    # 'family' | 'member_of' | 'mentions' | ...


@dataclass
class KnowledgeMap:
    lang: str
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    # Article titles referenced by fetched pages but not themselves fetched.
    unresolved: list[str] = field(default_factory=list)

    def node(self, node_id: str) -> Node | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        for node in self.nodes:
            by_type[node.type] = by_type.get(node.type, 0) + 1
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "by_type": by_type,
            "unresolved": len(self.unresolved),
        }


def _dedupe_edges(edges: Iterable[Edge]) -> list[Edge]:
    seen: set[tuple[str, str, str]] = set()
    out: list[Edge] = []
    for edge in edges:
        key = (edge.source, edge.target, edge.label)
        if key not in seen:
            seen.add(key)
            out.append(edge)
    return out


def build_map(pages: dict[str, dict[str, Any]], lang: str = "en") -> KnowledgeMap:
    """Turn `fetch_pages` output into a `KnowledgeMap`.

    `pages` maps title -> page dict with the keys produced by
    `wiki_client.WikiClient.fetch_pages` (wikitext, categories, links,
    missing, redirected_from).
    """
    title_set: set[str] = set()
    nodes: list[Node] = []
    for title, page in pages.items():
        if page.get("missing"):
            continue
        title_set.add(title)
        categories = tuple(page.get("categories", []) or [])
        wikitext = page.get("wikitext", "") or ""
        templates = template_names(wikitext)
        nodes.append(Node(
            id=title,
            type=classify(list(categories), templates),
            summary=summarise(wikitext),
            years=tuple(years(wikitext)),
            categories=categories,
        ))

    edges: list[Edge] = []
    unresolved: set[str] = set()
    for title, page in pages.items():
        if page.get("missing"):
            continue
        wikitext = page.get("wikitext", "") or ""
        for rel in relationships(title, wikitext):
            if rel.target in title_set:
                edges.append(Edge(title, rel.target, rel.label))
            else:
                unresolved.add(rel.target)
        for link in entity_links(wikitext):
            target = link.target
            if target == title:
                continue
            if target in title_set:
                edges.append(Edge(title, target, "mentions"))
            else:
                unresolved.add(target)

    return KnowledgeMap(
        lang=lang,
        nodes=nodes,
        edges=_dedupe_edges(edges),
        unresolved=sorted(unresolved),
    )


# --- Serialisation -----------------------------------------------------------

def to_dict(km: KnowledgeMap) -> dict[str, Any]:
    return {
        "lang": km.lang,
        "nodes": [asdict(n) for n in km.nodes],
        "edges": [asdict(e) for e in km.edges],
        "unresolved": km.unresolved,
        "stats": km.stats(),
    }


def from_dict(data: dict[str, Any]) -> KnowledgeMap:
    return KnowledgeMap(
        lang=data.get("lang", "en"),
        nodes=[
            Node(
                id=n["id"],
                type=n.get("type", "unknown"),
                summary=n.get("summary", ""),
                years=tuple(n.get("years", ())),
                categories=tuple(n.get("categories", ())),
            )
            for n in data.get("nodes", [])
        ],
        edges=[Edge(e["source"], e["target"], e["label"]) for e in data.get("edges", [])],
        unresolved=list(data.get("unresolved", [])),
    )


# --- Export views ------------------------------------------------------------

# Graphviz shapes/colours per entity type (no third-party lib: we emit DOT).
_DOT_ATTRS: dict[str, dict[str, str]] = {
    "character":    {"shape": "ellipse", "color": "#4c6ef5", "style": "filled"},
    "creature":     {"shape": "diamond", "color": "#e64980", "style": "filled"},
    "location":     {"shape": "box", "color": "#40c057", "style": "filled"},
    "organization": {"shape": "box", "color": "#fab005", "style": "filled"},
    "item":         {"shape": "component", "color": "#15aabf", "style": "filled"},
    "event":        {"shape": "hexagon", "color": "#fd7e14", "style": "filled"},
    "media":        {"shape": "note", "color": "#868e96", "style": "filled"},
    "unknown":      {"shape": "ellipse", "color": "#ced4da", "style": "filled"},
}


def _dot_id(value: str) -> str:
    # DOT ids cannot contain most punctuation; use a quoted, escaped string.
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_dot(km: KnowledgeMap) -> str:
    """Emit a Graphviz DOT digraph for visual inspection (e.g. `dot -Tsvg`)."""
    lines = ["digraph wikimap {", "  rankdir=LR;", '  node [fontname="Helvetica"];']
    for node in km.nodes:
        attrs = _DOT_ATTRS.get(node.type, _DOT_ATTRS["unknown"])
        attr_str = ", ".join(f'{k}="{v}"' for k, v in attrs.items())
        lines.append(f"  {_dot_id(node.id)} [{attr_str}];")
    for edge in km.edges:
        if edge.label == "mentions":
            style = 'style=dotted, color="#adb5bd"'
        else:
            style = "style=solid"
        lines.append(
            f"  {_dot_id(edge.source)} -> {_dot_id(edge.target)} "
            f'[label="{edge.label}", {style}];'
        )
    lines.append("}")
    return "\n".join(lines) + "\n"


def to_markdown(km: KnowledgeMap) -> str:
    """A human-readable wiki map grouped by entity type, with edge lists."""
    sections: list[str] = [f"# Knowledge map ({km.lang})", ""]
    stats = km.stats()
    sections.append(
        f"`{stats['nodes']}` nodes, `{stats['edges']}` edges, "
        f"`{stats['unresolved']}` unresolved references."
    )
    sections.append("")
    for entity_type in ENTITY_TYPES:
        group = [n for n in km.nodes if n.type == entity_type]
        if not group:
            continue
        sections.append(f"## {entity_type.capitalize()}s")
        sections.append("")
        for node in sorted(group, key=lambda n: n.id.lower()):
            years_str = f" ({', '.join(map(str, node.years))})" if node.years else ""
            sections.append(f"- **{node.id}**{years_str}")
            if node.summary:
                sections.append(f"  {node.summary}")
        sections.append("")

    sections.append("## Edges")
    sections.append("")
    for edge in sorted(km.edges, key=lambda e: (e.source.lower(), e.target.lower())):
        sections.append(f"- `{edge.source}` --{edge.label}--> `{edge.target}`")
    sections.append("")

    if km.unresolved:
        sections.append("## Unresolved references (candidate next seeds)")
        sections.append("")
        for title in km.unresolved[:200]:
            sections.append(f"- `{title}`")
        sections.append("")
    return "\n".join(sections)
