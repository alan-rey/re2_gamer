# The Bast Modding Pipeline

This document describes the mechanical loop the automation agent (**Bast**) owns end to
end. The goal is that every content change you (the author) make flows through a
deterministic, tested pipeline and comes out as a bootable, playable ISO — with no
hand-editing of binaries anywhere in the critical path.

## The loop at a glance

```
        ┌──────────────────────────────────────────────────────────────┐
        │                                                              │
        ▼                                                              │
  unpack ISO ──► decode binaries ──► decode to structured data         │
        ▲                              │                               │
        │                              ▼                               │
        │                    [ you author content here ]                │
        │                    story / items / doors / cutscenes          │
        │                              │                               │
        │                              ▼                               │
  test ◄── repack ISO ◄── inject new content ◄── validate + lint       │
        │                                                              │
        └────────────── (iterate until green) ──────────────────────────┘
```

Every stage is a small CLI under `tools/` with a single, obvious job. Each stage writes to
a clean, disposable directory so a failed stage never corrupts a source of truth.

## Stage-by-stage

### 1. `unpack` — disc image to file tree

- Input: the user's own PS2 ISO (never committed).
- Output: the main ELF (`SLUS_xxx.xx`) and every data archive, extracted to `data/raw/`,
  plus a `data/manifest.json` (path, size, SHA-256, guessed type).
- Rule: `unpack` is **read-only** with respect to the ISO. It never mutates the source.

### 2. `decode` — binary to structured data

- Input: raw files from `data/raw/`.
- Output: JSON/YAML in `data/decoded/`, one schema per format family.
- Key output families: scenario/event scripts (doors, items, triggers), text tables
  (dialogue, file notes), item/key/flag tables, cutscene event lists.
- Rule: a decode is only trusted if it has a matching `encode` and a **round-trip test**.

### 3. `author` — you write content (structured, not binary)

- You edit YAML/JSON in `content/`, not raw bytes. Bast provides schemas and a linter so a
  typo fails loudly instead of corrupting the build.
- The content model is expressed as a **diff against the world graph** (see
  `docs/02-world-inventory.md`): "relock this door", "move this key here", "swap this note".

### 4. `validate` — catch bad content before it touches the ISO

- Schema validation (every field present, correct type).
- Reference integrity (no item/door/trigger references an ID that doesn't exist).
- Progression-graph checks (the new key actually unlocks the new door; no unreachable room;
  no softlock edge).

### 5. `inject` — write new content into decoded/encoded data

- Takes validated content and re-encodes it into the target binary format, producing a
  modified file tree.
- Rule: injection only ever writes *into the same slot/offset* the original used, or a
  format-legal relocation — never a blind byte patch.

### 6. `repack` — file tree back to a bootable ISO

- Rebuilds archives (table-of-contents + payloads) and the ISO filesystem.
- **Critical:** rebuilds the ISO9660 **LBA (logical block address)** table correctly. A
  PS2 disc boots from physical block offsets, so a naive repack that shifts LBAs produces a
  disc that hangs on boot.
- Output: `build/recvx-<version>.iso`.

### 7. `test` — prove it works

Two tiers, in order of preference:

| Tier | What | Catches |
|---|---|---|
| **Offline** (pytest) | format round-trips, schema lint, world-graph integrity, diff-correctness | 90% of bugs — cheap and deterministic |
| **Emulator smoke** (PCSX2 headless) | boot the ISO, fast-forward, assert the game reaches a known screen/reachable state | boot failures, LBA errors, crash-on-load |

The emulator is a *smoke test*, not the primary verifier. If a bug needs the emulator to
reproduce, that's a signal the offline tests are missing coverage.

## The golden rule: round-trip before injection

Before ANY story content is injected, Bast must prove the toolchain is safe with a
**golden round-trip**:

```
unpack ISO -> repack ISO (no changes) -> boot in PCSX2 -> behavior-equivalent to original
```

Until that passes, every bug is ambiguous (is it my content or my tooling?). After it
passes, tooling is trusted and future bugs are attributable to content — which is a
completely different, much faster class of bug to fix.

## Why this shape (and not direct binary editing)

- **Determinism:** every stage is a pure-ish transform; same input → same output.
- **Diffability:** content lives in YAML/JSON, so the git history is a readable changelog
  of *what the story changed*, not opaque byte deltas.
- **Attribution:** with a golden round-trip, failures point at content, not tooling.
- **Reversibility:** because injection is expressed as a diff against the world graph,
  reverting a single change is a one-line content edit, not a binary rollback.

## Reproducibility contract

Any stage must be runnable by a fresh checkout with only these inputs:

1. A Python venv with `requirements.txt` installed.
2. The user's own ISO placed in a documented location (never committed).
3. `make <stage>` (or `just <stage>`).

No GUI, no manual hex editing, no undocumented environment variables in the critical path.
