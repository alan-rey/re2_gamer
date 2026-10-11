# Format: AFS (archive container)

Status: **spec written + round-trip proven** (Phase 2, read *and* write path).
Owner module: `tools/formats/afs.py`.

## Why this format first

Phase 3's deliverable is `unpack` ("ISO -> archives -> individual files"), and that
cannot exist until the archives can be opened. AFS is the dominant container in this
disc: **12 archives holding 2,692 members and roughly 99% of the disc's bytes**
(`RDX_LNK.AFS` alone is 588 MB). ISO9660 got us to the archive files; AFS is what is
inside them.

## Scope: read now, write proven, injection later

Unlike ISO9660 — a public standard we can implement exactly — AFS is a de-facto
container format used by PS2-era middleware. There is no normative specification to
consult, so every claim below is **measured from the user's own disc** and re-derived
mechanically by `tests/test_afs.py`.

The module implements both directions. `parse()` is the read path; `serialize()` is the
write path, and it is proven byte-exact against all 12 real archives. That write path is
deliberately a *no-change* repacker for now: it rebuilds a container that is bit-identical
to the input. Actually changing a member's payload is Phase 3/7 work, and the serializer
already supports it because offsets are recomputed from current sizes rather than copied.

## Layout

Little-endian throughout.

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | magic: `AFS\x00` or `AFS ` (space) |
| 4 | 4 | member count `N` (u32) |
| 8 | 8 x `N` | table: `N` pairs of (offset u32, size u32), ascending offsets |
| `H` | var | pre-member padding |
| ... | var | `N` members, each at a 2048-aligned offset, with gaps between them |
| EOF | var | trailer |

where `H = 8 + 8 * N`. The `size` in the table is the member's **true** length. The bytes
between a member's end and the next member's start are not a member; they are a gap.

### Measured shape of the 12 real archives

| Archive | Magic | Members | Size (bytes) | First offset | Pre-pad | Trailer | Gap bytes |
|---|---|---:|---:|---:|---:|---:|---:|
| `ADV.AFS` | `AFS\0` | 33 | 7,477,248 | 32,768 | 32,496 | 1,376 | 43,934 |
| `BGM1.AFS` | `AFS\0` | 121 | 138,301,440 | 524,288 | 523,312 | 0 | 140,136 |
| `BGM2.AFS` | `AFS\0` | 126 | 121,860,096 | 524,288 | 523,272 | 786 | 159,078 |
| `ITEM1.AFS` | `AFS\0` | 282 | 44,926,976 | 524,288 | 522,024 | 1,852 | 357,544 |
| `ITEM2.AFS` | `AFS\0` | 278 | 41,773,056 | 524,288 | 522,056 | 1,852 | 350,136 |
| `MRY.AFS` | `AFS\0` | 157 | 9,908,224 | 524,288 | 523,024 | 448 | 192,104 |
| `MULTSPQ1.AFS` | `AFS ` | 166 | 115,970,048 | 2,048 | 712 | 0 | 0 |
| `MULTSPQ2.AFS` | `AFS ` | 144 | 84,549,632 | 2,048 | 888 | 0 | 0 |
| `RDX_LNK.AFS` | `AFS\0` | 205 | 587,816,960 | 524,288 | 522,640 | 597 | 204,653 |
| `SYSTEM.AFS` | `AFS\0` | 140 | 26,896,384 | 524,288 | 523,160 | 2,037 | 184,929 |
| `VOICE1.AFS` | `AFS\0` | 525 | 39,127,040 | 524,288 | 520,080 | 1,254 | 466,338 |
| `VOICE2.AFS` | `AFS\0` | 515 | 32,997,376 | 524,288 | 520,160 | 30 | 440,924 |

## The four rules that actually bite

1. **Two magic variants exist, and the second one is a space.** Ten archives start
   `AFS\x00`; `MULTSPQ1/2` start `AFS ` (0x20). A parser testing `blob[:4] == b"AFS\0"`
   silently rejects two 80–116 MB archives. `is_afs()` accepts either variant.

2. **The pre-member padding is not a constant, so it must be preserved, not recomputed.**
   It is 2,048 bytes in `MULTSPQ1/2`, 32,768 in `ADV.AFS`, and 524,288 in the other nine.
   A repacker that hard-codes "round the header up to 2,048" reproduces `MULTSPQ1.AFS`
   and corrupts the other ten by relocating every member. The parser records the actual
   pre-padding bytes and `serialize()` writes them back verbatim. The same applies to the
   inter-member gaps (up to 466 KB of them in `VOICE1.AFS`) and the trailer.

3. **Offsets are recomputed on write, which is what makes repacking possible.**
   `serialize()` emits the header, then walks members in order, re-emitting each offset as
   `H + len(pre-pad) + sum(previous member sizes + their gaps)`. In a no-change round-trip
   this reproduces the original numbers exactly; when a member's size later changes, every
   downstream offset stays consistent instead of pointing into the middle of the previous
   member.

4. **A truncated or hostile count must be rejected before allocating.** The count field is
   attacker-controlled input: `count = 10,000,000` implies an 80 MB table, and a naive
   reader allocates it and then indexes off the end. `_parse_entries()` checks
   `8 + 8 * count <= len(blob)` first and raises `AfsFormatError`. Likewise, entries whose
   offset lands inside the header, or before the end of the previous member, are rejected
   rather than parsed into overlapping payloads.

## Verification

- `tests/test_afs.py` (21 tests) runs under pytest *and* the stdlib fallback runner.
- **Synthetic tier**: archives assembled in memory, so the suite passes on a bare checkout
  with no copyrighted input. Covers both magics, non-zero gap filler and trailer bytes
  surviving verbatim, empty archives, and every rejection path above.
- **Real-disc tier**: every `data/raw/*.AFS` is decoded and re-encoded and must be
  byte-identical — `python -m tools.formats.afs verify data/raw/*.AFS` prints
  `ok <path>: N members, byte-exact round-trip` for all 12. This is the Phase 2 exit
  criterion ("decode -> encode -> byte-equal") met for this format.
- **Independent cross-check**: `SYSTEM.AFS` embeds loose disc files as members, so member
  0 equals `SYSMES.ALD` byte for byte (md5 match). Member 1 is the same length as loose
  `SYSEFF.ALD` (1,100,316 bytes) but differs in 59,256 scattered bytes — a *different
  revision* of the same asset, not a parse error. Contiguous-difference signatures mean
  a wrong offset; scattered same-length differences mean different content. The test
  asserts the revision relationship explicitly so the distinction is recorded rather
  than mistaken for a bug later.

## Open questions

- Member filenames are not stored; members are only indexed. Mapping them to real names
  (`SYSMES.ALD`, or `X_FILE`-style names) is Phase 3 work and will need either an external
  index or content sniffing.
- `SYSTEM.AFS` member 3 has size 11 and begins `dumm` (a placeholder), so zero-length and
  near-zero-length members are expected — extraction must not treat them as errors.
