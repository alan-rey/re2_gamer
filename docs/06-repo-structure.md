# Repository Structure

The layout below separates **the user's original game data** (never committed) from
**authored content** (plain text, versioned) from **tooling** (Bast's code) from
**research** (reverse-engineering notes).

```
ps2_gamer/
├── README.md                    # index, strategy, legal warning
├── .gitignore                   # excludes data/raw, build/, *.iso, extracted assets
├── .gitattributes               # Git LFS rules for large research captures
│
├── docs/                        # planning documents (this set)
│   ├── 01-phases-roadmap.md
│   ├── 02-world-inventory.md
│   ├── 03-tech-stack-tools.md
│   ├── 04-bast-modding-pipeline.md
│   ├── 05-blockers-risks-legal.md
│   └── 06-repo-structure.md
│
├── skills/                      # Bast's reusable procedures (created via create_skill)
│   └── <name>/SKILL.md
│
├── data/                        # game data, split by provenance
│   ├── raw/                     #   extracted original files + manifest.json  (NOT committed)
│   ├── decoded/                 #   binary -> JSON/YAML (generated, machine-readable)
│   ├── world/                   #   Phase 4 inventory: rooms/doors/items/triggers graph
│   └── manifest.json            #   every extracted file: path, size, sha256, type
│
├── content/                     # AUTHORED (ours) — the actual new game content
│   ├── story/                   #   lore bible, script, dialogue, file notes
│   ├── items/                   #   item placements + new key/flag definitions
│   ├── doors/                   #   door lock/key/flag overrides
│   ├── triggers/                #   puzzle + event trigger definitions
│   ├── cutscenes/               #   cutscene storyboards + event lists
│   └── schema/                  #   JSON/YAML schemas that validate all of the above
│
├── tools/                       # Bast's pipeline code
│   ├── unpack/                  #   ISO + archive extraction
│   ├── formats/                 #   binary specs (construct / ksy) per format
│   ├── decode/                  #   binary -> structured
│   ├── encode/                  #   structured -> binary
│   ├── inject/                  #   place content at offsets / rebuild tables
│   ├── repack/                  #   archive + ISO rebuild (LBA-aware)
│   ├── test/                    #   PCSX2 headless boot + smoke tests
│   └── cli/                     #   unified entry points (make unpack/build/test)
│
├── tests/                       # pytest: round-trips, schema validation, world-graph checks
├── research/                    # reverse-engineering write-ups (text, permanent knowledge)
│   ├── <format-name>/           #   spec.md + parse PoC per format
│   └── script-vm/               #   opcode map, flag semantics, trigger model
│
└── build/                       # generated playable output (ISO, patches)  (NOT committed)
    └── release/                 #   xdelta patches for distribution
```

## Data-schema sketch (authoritative schemas live in `content/schema/`)

```yaml
# content/doors/doors.yaml (example entry)
- id: DOOR_MANSION_EAST
  zone: mansion
  room: music_room
  lock:
    key_id: KEY_PIANO
    flag: FLAG_PIANO_SOLVED
  links: [ROOM_HALL]
  script_ref: SCD_0123
```

```yaml
# content/items/items.yaml (example entry)
- id: KEY_PIANO
  zone: mansion
  room: portrait_gallery
  position: { x: 100, y: 0, z: 240 }
  pickup_flag: FLAG_GOT_PIANO_KEY
```

```yaml
# content/triggers/triggers.yaml (example entry)
- id: PUZZLE_PIANO
  zone: mansion
  room: music_room
  inputs: [NOTE_A, NOTE_C, NOTE_E]
  success_flag: FLAG_PIANO_SOLVED
  on_success: [UNLOCK DOOR_MANSION_EAST]
```

The world graph is **generated** from `data/world/*.yaml` by a `tools/` command, so it is
always consistent with the authored content (single source of truth).

## Git policy

- **Commit:** everything except `data/raw/`, `data/decoded/`, `build/`, `*.iso`, `*.bin`,
  extracted audio/video/texture assets, and any file whose origin is the Capcom disc.
- **Git LFS:** large *research* captures (memory dumps, Ghidra project exports, annotated
  screenshots) if we need them reproducible — but prefer text notes over binaries.
- **Never commit the original game.** The `README` legal note is enforced by `.gitignore`
  and by Bast's secret-file guard analog for game assets.
