# Tech Stack & Tools

This project is a reverse-engineering + data-pipeline + content-authoring problem wearing a
game-modding costume. The stack below is chosen around one principle: **everything Bast
does must be repeatable, diffable, and testable from a terminal.** No GUI-only tools in the
critical path.

---

## Primary language: Python 3.11+

Why Python:

- The entire pipeline is *data plumbing* (read binary, transform, write binary). Python's
  `struct` / `memoryview` / `bytes` are the least-friction way to do that with confidence.
- It's what Bast (the automation agent) can most reliably write, run, lint, and test.
- Rich ecosystem for every stage: `construct` (declarative binary parsing), `capstone`
  (disassembly), `click`/`typer` (CLIs), `pytest` (tests), `PyYAML` (content).

Fallback for hot paths: if archive rebuild or texture re-encoding is ever too slow, a
single compiled helper (Rust or C) is fine — but keep the *interface* in Python so the
pipeline stays composable.

---

## Binary parsing & serialization

| Tool / lib | Use |
|---|---|
| `construct` (Kaitai Struct as an alternative) | Declarative specs for each file format: archives, scenario scripts, text tables. Specs live in `tools/formats/` and are shared by `decode` and `encode`. |
| `kaitaistruct` | Optional: generates a Python parser from a `.ksy` spec. Nice for cross-language sharing, but `construct` is fine for Python-only. |
| `struct` / `memoryview` | Hand-rolled parsers for oddball formats that don't fit a declarative spec. |
| `capstone` | Disassemble the PS2 EE (MIPS R5900) executable when we must understand how a script opcode is interpreted. |
| `lief` / `pyelftools` | Parse and inspect the PS2 ELF main executable. |

Rule: **every format gets a spec + a round-trip test** (`decode -> encode -> byte-equal`).
If a format can't round-trip byte-for-byte yet, it is not safe to inject into it.

---

## Reverse-engineering tools (the "understand the game" stack)

| Tool | Use |
|---|---|
| **Ghidra** (with the PS2 / MIPS loader) | Static analysis of the main ELF — find the script VM, door/key/flag tables, text system. |
| **Cheat Engine** + PCSX2 | Runtime memory scanning to correlate in-game state (a door flag, an item ID) with memory addresses. |
| **ImHex / 010 Editor** (010 Editor is commercial) | Hex editing and binary template prototyping during format exploration. |
| **PCSX2 debugger** | Breakpoints and tracing while the game runs, to watch scenario scripts execute. |

These are *investigation* tools. Their output (addresses, meanings, schemas) gets recorded
into `research/` as text so the knowledge is permanent and reusable.

---

## The game side: PS2 / Code Veronica X specifics

- **CPU:** PS2 Emotion Engine (MIPS R5900, 64-bit, little-endian in practice for this title —
  confirm against the actual ELF).
- **Main executable:** `SLUS_xxx.xx` ELF — the thing we disassemble and possibly patch.
- **Media:** a PS2 ISO9660 disc image. Repacking means rebuilding the image **and its LBA
  (logical block address) table** correctly, or the PS2 won't boot.
- **Likely archive formats:** the game bundles rooms/scripts/text into archives with a
  table-of-contents. These are the primary format families to reverse in Phase 2.

---

## Emulator & testing stack

| Tool | Use |
|---|---|
| **PCSX2** | The emulator. Two modes: (1) interactive for manual playthroughs, (2) **headless** (no window, scripted boot) for CI-style smoke tests. |
| **PCSX2 CLI flags / save-state injection** | Boot an ISO, fast-forward, assert the game reaches a known screen. |
| **RetroArch + PCSX2 core** (optional) | Alternative automation surface if PCSX2's CLI is awkward. |
| `pytest` | Orchestrates all offline tests (format round-trips, content schema validation, world-graph integrity). |

Testing is covered in detail in `docs/04-bast-modding-pipeline.md`; the headline is that
**offline (no emulator) tests should catch 90% of bugs**, and the emulator is reserved for
"does it boot / is it reachable" checks.

---

## Build & orchestration tooling

| Tool | Use |
|---|---|
| `make` or `just` (task runner) | Thin, human-readable entry points: `make unpack`, `make decode`, `make build`, `make test`. |
| `nox` / `tox` (optional) | Reproducible environments. |
| `typer` or `click` | The `tools/*` CLIs. |
| **Git + Git LFS** | Version control. Large binaries (PS2 assets, research captures) go to LFS; authored content is plain text. |
| `xdelta3` | Producing the final patch for distribution (the user applies it to their own ISO). |

---

## Data & content formats

- **Authored content:** YAML for human-writable structured data (story, items, doors,
  cutscene events). YAML because it's the most readable and diff-friendly for prose-heavy
  content. JSON only where tooling demands it.
- **Extracted/decoded game data:** JSON by default (deterministic, tool-friendly).
- **Binary artifacts:** everything under `build/` and `data/raw/` — never committed except
  via LFS for research captures.

---

## Recommended toolchain summary (what to install first)

1. Python 3.11+ with a venv; `pip install construct capstone pyelftools pyyaml click pytest`
   (add `kaitaistruct` only if we adopt `.ksy` specs).
2. PCSX2 (headless-capable build).
3. Ghidra for the ELF disassembly phase.
4. A hex editor (ImHex is free/open).
5. Git + Git LFS.

Defer until needed: audio tooling (voice re-dub / music swap is a stretch goal, not MVP),
video/FMV re-encoding, model/texture editors.
