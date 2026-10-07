# RE: Code Veronica X — Custom Story Total Conversion

A from-scratch, story-driven reimagining of **Resident Evil: Code Veronica X** for the
PlayStation 2. The goal is to **keep the existing world geometry** (the island, the
mansion, and the Antarctic facility) and **replace everything that happens inside it**:
new lore, new file notes, new dialogue, new cutscenes, a new item/key layout, and new
puzzle/trigger progression.

This repository is the **engineering + planning workspace** for the project. The actual
asset authoring (script, notes, item layouts) happens here as structured, versionable
content; an automated developer agent (**Bast**) owns the mechanical pipeline:

```
unpack game files -> decode to structured data -> inject new content -> repack ISO -> test on emulator
```

## Status

- [x] Phase 0 — Setup & governance
- [ ] Phase 1 — Asset acquisition & extraction
- [ ] Phase 2 — File-format reverse engineering
- [ ] Phase 3 — Tooling (unpack / repack / inject)
- [ ] Phase 4 — World inventory (rooms, doors, items, triggers)
- [ ] Phase 5 — Story & design
- [ ] Phase 6 — Content authoring
- [ ] Phase 7 — Injection & repack (playable build)
- [ ] Phase 8 — Testing & QA
- [ ] Phase 9 — Packaging & distribution
- [ ] Phase 10 — Polish & iteration

## Document index

| Document | Purpose |
|---|---|
| [docs/01-phases-roadmap.md](docs/01-phases-roadmap.md) | The 11 phases, deliverables, and exit criteria |
| [docs/02-world-inventory.md](docs/02-world-inventory.md) | The three zones and what a rewrite touches in each |
| [docs/03-tech-stack-tools.md](docs/03-tech-stack-tools.md) | Languages, libraries, emulator, and reverse-engineering tools |
| [docs/04-bast-modding-pipeline.md](docs/04-bast-modding-pipeline.md) | The Bast-owned unpack → decode → author → inject → repack → test loop |
| [docs/05-blockers-risks-legal.md](docs/05-blockers-risks-legal.md) | Blockers, risks, mitigations, and legal constraints |
| [docs/06-repo-structure.md](docs/06-repo-structure.md) | Proposed folder layout and data schemas |
| [docs/07-asset-policy.md](docs/07-asset-policy.md) | What may never be committed, and how to handle game assets |
| [docs/08-conventions.md](docs/08-conventions.md) | Naming conventions for rooms, items, keys, notes, and files |

## Repository layout

```
content/    authored, versionable content (script, notes, item layouts, progression)
data/       runtime data + extraction output (ignored: raw/extracted game data)
docs/       planning + design documentation
research/   reverse-engineering notes, format specs, memory captures
skills/     reusable Bast procedure packages
tools/      pipeline code (unpack, decode, inject, repack, test)
```

## The one-line strategy

**Ship a vertical slice first** — one zone, one new key, one new door, one rewritten note,
one new cutscene — end-to-end through the entire pipeline. That single spike de-risks every
hard problem (formats, scripts, LBA rebuilds, emulator testing) before any story is written
in volume.

## Legal note (read before contributing assets)

This is a derivative work of a copyrighted Capcom title. **Never commit, distribute, or
publish the original game files.** Distribution must be a patch (e.g. xdelta) that the end
user applies to a disc image they already own. See
[docs/07-asset-policy.md](docs/07-asset-policy.md) and
[docs/05-blockers-risks-legal.md](docs/05-blockers-risks-legal.md).
