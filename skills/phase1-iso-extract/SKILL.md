---
name: phase1-iso-extract
description: Use when running, re-running, or verifying Phase 1 of the roadmap: extracting the user's own PS2 disc image into data/raw/ plus data/manifest.json, or checking that an existing extraction still matches its manifest.
tags: ps2,iso9660,extraction,phase1,pipeline
---

# Phase 1 — ISO extraction & verification

## When to use
- The user asks to run Phase 1 ("asset acquisition & extraction") or to re-extract the disc.
- You changed anything under `tools/unpack/` and need to re-verify against the real image.
- You need to confirm `data/raw/` still matches `data/manifest.json`.

Exit criteria (docs/01-phases-roadmap.md): `data/raw/` holds the extracted tree **and**
`data/manifest.json` lists every file with path, size, SHA-256 and a type guess.

## Steps
1. Confirm the image is present, never committed:
   `dir /b .game_iso` — expect the user's own `.iso`.
   It is matched by `*.iso` in `.gitignore`. Never stage it, never publish it.
2. Extract (stdlib-only; do **not** add pycdlib/pyelftools):
   `python -m tools.unpack.extract --iso ".game_iso/<disc>.iso"`
   Add `--no-iso-hash` to skip the extra ~2 GB pass when you only need the tree.
3. Prove integrity — this is the mechanical exit-criteria check:
   `python -m tools.unpack.extract --verify`
   Expect `verified: N ok, 0 missing, 0 mismatched`.
4. Cross-check manifest against disk (counts must agree):
   `python -c "import json,os;m=json.load(open('data/manifest.json'));print(m['totals']);print('on_disk',sum(len(f) for _,_,f in os.walk('data/raw')))"`
5. Confirm the boot executable resolved, e.g. `boot: {'found': True, ..., 'elf': {'machine': 'MIPS', ...}}`.
   A null/absent `elf` means boot resolution or ELF parsing regressed.

## Example
```
python -m tools.unpack.extract --iso ".game_iso/Resident Evil - Code - Veronica X.iso"
python -m tools.unpack.extract --verify
```
Observed on a valid Code: Veronica X disc: 51 files, 3 dirs, 1,883,239,982 bytes,
boot `SLUS_201.84` -> ELF32 MIPS EXEC, entry `0x00100008`.

## Caveats
- **`make` may not exist on Windows.** `make unpack` / `make verify` fail with
  "'make' is not recognized". Call `python -m tools.unpack.extract ...` directly; the
  Makefile is a convenience wrapper only.
- **Never quote `>` or `|` inside a `python -c` f-string on Windows cmd** — e.g.
  `f"{size:>12,}"` is parsed as a redirect and silently creates junk files. Use
  `print(rows)` with `chr(10)` joins instead, and delete any stray numbered file.
- `data/raw/` and the ISO are copyrighted: never commit them. `data/manifest.json`
  (metadata only) *is* committed per docs/06-repo-structure.md.
- The extractor is read-only w.r.t. the ISO by construction; do not add a write path.
- Regression that bit us once: `elfinfo.summarize()` used to hard-seek `fp` to 0, so
  calling it after seeking the ISO to the boot extent raised `ValueError: not an ELF
  file` and aborted the entire run. It now takes `base_offset`, and `detect_boot`
  swallows parse errors. If a whole extraction dies on ELF parsing, check this first.
