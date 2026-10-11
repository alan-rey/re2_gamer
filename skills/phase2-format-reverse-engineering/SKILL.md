---
name: phase2-format-reverse-engineering
description: Use when starting, continuing or verifying Phase 2 of the roadmap: reverse-engineering a PS2 data format into a parse/serialize PoC. Covers the house layout (tools/formats/<fmt>.py + research/<fmt>/spec.md + tests), the byte-exact round-trip exit criterion, and how to verify against the user's real disc files.
tags: phase2, formats, reverse-engineering, tests
---

# Phase 2 — Format reverse engineering

## When to use

Any time the task is "understand format X on the disc" or "add/verify a decoder for X".
Applies to archives (done: AFS), models, textures, scenario scripts, text tables, audio.

Phase 2 = *read the format before writing to it*. Do not reach for injection/repack work
(that is Phase 3/7) while a format lacks a proven round-trip.

## Deliverables per format (roadmap `docs/01-phases-roadmap.md`)

| Artifact | Path | Purpose |
|---|---|---|
| Parser/serializer | `tools/formats/<fmt>.py` | the PoC; importable module + `__main__` CLI |
| Written spec | `research/<fmt>/spec.md` | schema, field tables, gotchas, verification |
| Tests | `tests/test_<fmt>.py` | synthetic + real-disc tiers |

## Exit criterion (the only thing that counts)

**decode -> encode -> byte-equal.** Not "it parses", not "fields look right".
Implement `verify_roundtrip(path) -> list[str]` (empty list = clean) and assert it is
empty for every real archive. This is the mechanical proof the roadmap demands.

## Steps

1. **Shape the evidence from the real disc, not from memory.** Read the first bytes of the
   target files; do not trust a wiki. Count entries, check offset alignment, check where
   the last member ends.
2. **Write the parser against the bytes you actually observed.** Preserve *everything*
   you did not explicitly interpret - pre-header padding, inter-member gaps, tail trailer.
   Regenerating padding as zeros is the classic silent corruption: real archives use
   2048, 32768 and 524288 bytes of pre-padding depending on the file, so it must be kept
   verbatim, never recomputed.
3. **Fail loudly on malformed input.** Reject bad magic, an entry count whose header cannot
   fit the file (before allocating), offsets inside the header, and overlapping members.
   A hostile count must raise, not allocate.
4. **Provide a cheap table-only path.** `parse_table_file()` reads just `header + 8*count`
   bytes. Essential: listing a 588 MB archive must not read 588 MB.
5. **Two-tier tests.**
   - *Synthetic*: build fixtures in memory so the suite runs on a bare checkout with no
     copyrighted input. Cover layout, preserved padding, and every rejection path.
   - *Real disc*: guard with `if not os.path.isdir(RAW_DIR): return` so it is vacuously
     skipped when the user has not extracted yet.
6. **Run the suite**: `python tests/run_tests.py` (stdlib runner; pytest also works).
7. **Write `research/<fmt>/spec.md`** in the house style of `research/iso9660/spec.md`:
   status line, owner module, why-this-first, field tables, "the rules that actually bite",
   verification section. Cite measured numbers (member counts, observed padding), not
   generalities.

## Example (AFS, Phase 2 first format)

```
tools/formats/afs.py     parse / parse_file / parse_table_file / verify_roundtrip
research/afs/spec.md     layout + gotchas
tests/test_afs.py        synthetic + real-disc byte-exact
python -m tools.formats.afs list   data/raw/MULTSPQ1.AFS
python -m tools.formats.afs verify data/raw/*.AFS
```

Measured: 12 archives, 2,692 members, both `AFS\0` and `AFS ` magics, all entry offsets
2048-aligned, every archive byte-exact on round-trip.

## Caveats

- **Do not assert that a loose file equals an in-archive member.** `SYSEFF.ALD` (loose) and
  `SYSTEM.AFS` member 1 have the *same length* but differ in 59,256 scattered bytes: they
  are two revisions of the same asset. Byte-differences spread across a file at equal
  length means "different build", while a contiguous shifted run or a length mismatch means
  "your offset is wrong". Diagnose which before blaming the parser.
- Real archives are LFS/ignored; never commit disc data. Commit only code, specs, tests.
- A test that silently returns when files are absent is right for portability but proves
  nothing on CI - re-run it locally where `data/raw/` exists before claiming the format done.
- `is_afs()` needs only the first 4 bytes; use it to sniff before committing to a full parse.
