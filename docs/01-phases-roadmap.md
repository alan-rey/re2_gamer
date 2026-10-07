# Phases & Roadmap

Each phase has an explicit **deliverable** and **exit criteria**. A phase is not "done"
until its exit criteria can be demonstrated mechanically (a command runs, an ISO boots,
a round-trip test passes).

The MVP milestone is **Phase 7 with Act 1 complete** — a playable first slice. Everything
after that is breadth and polish.

---

## Phase 0 — Setup & governance
**Goal:** a legal, reproducible, self-documenting workspace.

Deliverables:
- Git repo initialized; `.gitignore` and `.gitattributes` (Git LFS) in place.
- `README.md`, `docs/` skeleton, and a `skills/` directory for Bast's reusable procedures.
- A **Legal/Asset policy** written down (see `05-blockers-risks-legal.md`).
- Naming conventions for `data/`, `content/`, `tools/`, and `research/`.

Exit criteria:
- `git_status` clean; LFS tracks large binaries; a new contributor can read `README.md`
  and understand the repo layout.

---

## Phase 1 — Asset acquisition & extraction
**Goal:** turn the user's own disc into a catalogued, checksummed file tree.

Deliverables:
- Disc dump procedure (user supplies their own ISO — never committed).
- Extraction of the PS2 main executable (ELF) and all data archives.
- Full file manifest: path, size, SHA-256, type guess.

Exit criteria:
- `data/raw/` contains the extracted tree; `data/manifest.json` lists every file.

---

## Phase 2 — File-format reverse engineering
**Goal:** understand every format we need to touch, before touching it.

Likely format families to identify (confirm against the actual disc):
- Archives / containers (table-of-contents + payloads).
- Models & textures (rooms, props).
- Scenario/event scripts (doors, items, triggers, cutscenes).
- Text tables (dialogue, file notes) and their encoding + font constraints.
- Audio (streamed voice, music, SFX) and video (FMV).

Deliverables:
- A `research/<format>/` folder per format with a written spec + parse/serialize PoC.

Exit criteria:
- Each format has a documented schema and a round-trip test (decode → encode → byte-equal
  or behavior-equal).

---

## Phase 3 — Tooling (Bast's core)
**Goal:** the mechanical pipeline exists and is trusted.

Deliverables:
- `tools/unpack` — ISO → ELF + archives → individual files.
- `tools/decode` / `tools/encode` — binary ↔ structured JSON/YAML per format.
- `tools/inject` — place new content at correct offsets.
- `tools/repack` — rebuild archives + ISO, with LBA (logical block address) handling.
- `tools/test` — PCSX2 headless boot + smoke checks.

Exit criteria:
- **Golden round-trip:** unpack → repack with *no changes* → boot in PCSX2 → byte-equivalent
  behavior. This proves the toolchain is safe before any content is injected.

---

## Phase 4 — World inventory
**Goal:** produce a machine-readable map of the existing world so we can rewrite it.

Deliverables:
- For all three zones: rooms, doors (and their lock/key/flag), items (and positions/IDs),
  triggers, events, and the progression graph (what unlocks what).
- `data/world/*.yaml` + a generated world graph.

Exit criteria:
- Any door/item/trigger can be located in the binary data by ID from the inventory.

---

## Phase 5 — Story & design
**Goal:** the creative blueprint, kept separate from code.

Deliverables:
- Lore bible (characters, factions, timeline, tone).
- Full new script + dialogue + file-note text.
- New item/key/puzzle design and a revised progression graph.
- Cutscene storyboards (what happens, where, using which existing cameras).

Exit criteria:
- The design is complete enough that content authoring is mechanical.

---

## Phase 6 — Content authoring
**Goal:** all new content exists as validated, structured data.

Deliverables:
- `content/` YAML/JSON for text, item placements, door locks, triggers, cutscenes.

Exit criteria:
- Schema validation + linter pass clean; no references to undefined IDs.

---

## Phase 7 — Injection & repack (playable build)
**Goal:** the new content is actually in a bootable ISO.

Deliverables:
- `build/` produces a playable ISO with injected content.

Exit criteria:
- ISO boots in PCSX2 and the new Act 1 content is reachable.

---

## Phase 8 — Testing & QA
**Goal:** no softlocks, no broken progression.

Deliverables:
- Automated smoke tests (boot, key areas) + scripted playthroughs.
- Save-state regression suite; bug triage log.

Exit criteria:
- Act 1 is playable end-to-end with no softlock and no missing references.

---

## Phase 9 — Packaging & distribution
**Goal:** ship a patch, not a pirated game.

Deliverables:
- xdelta patch + install guide + save-compatibility notes.

Exit criteria:
- A user with their own ISO can apply the patch and reach the new content.

---

## Phase 10 — Polish & iteration
**Goal:** v1.0 quality.

Deliverables:
- Final cutscene pass, audio pass (if in scope), balancing, bug fixes.

Exit criteria:
- v1.0 tag with a complete, tested Act 1–N playthrough.

---

## Milestone ordering (why this order)

1. **Formats before story** (Phase 2 before 5) — the story must be shaped by what the
   engine can actually express (text length, glyph set, trigger model, cutscene skeleton).
2. **Golden round-trip before any injection** (Phase 3) — a trusted toolchain is the only
   thing that makes every later bug attributable to *content*, not *tooling*.
3. **Vertical slice before volume** — one zone, one key, one door, one note, one cutscene,
   end-to-end, before authoring the whole game.
