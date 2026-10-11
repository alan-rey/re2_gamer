# Format: MPAC (model packages / 3D geometry)

Status: **spec written + round-trip proven** (Phase 2, read *and* write path).
Owner module: `tools/formats/mpac.py`.

## Why this format next

Phase 2 lists "models & textures (rooms, props)" as a family we must understand before
Phase 3 tooling can touch it. Textures already had a spec and a proven round-trip
(`research/tim2/spec.md`), and containers had one (`research/afs/spec.md`). Geometry is
the piece the roadmap's later phases depend on hardest: Phase 4 (world inventory) needs to
locate rooms, doors and props in the data, and Phase 6 (content authoring) needs to know
which bytes a changed room would touch.

MPAC is the container the geometry arrives in. It is the *outer* layer: understand it
first, and the payload formats (`MDL`, `PMB`, `FLR`) become three separate, smaller
problems instead of one large one.

## Scope: framing now, geometry later

This spec documents the **envelope and framing** of an MPAC member and proves that
decoding and re-encoding it is byte-identical. It deliberately does **not** claim to
understand what is inside an `MDL` payload — no vertex count, no transform, no material
table. Interpreting those is the next Phase 2 target, and until it is done the module
carries every payload as raw bytes rather than guessing at its shape.

That restraint is the point. A codec that "fixes up" a field it cannot verify is how a
repack silently corrupts a level.

## Layout

Little-endian throughout. One MPAC member:

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | magic: ASCII `MPAC` |
| 4 | 4 | `H` — header size, u32 |
| 8 | `H - 4` | header payload (uninterpreted; carried verbatim) |
| `4 + H` | var | body — a sequence of framed records (below) |
| end | 4 | trailer: `FF FF FF FF` |

`H` is a **size, not a version**, and nothing in the module depends on its value. On this
disc it takes exactly two values, `4` (5 members) and `24` (119 members), which is why the
code is written to read whatever it finds rather than dispatch on a table.

A framed record ("chunk") inside the body:

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | `L` — record length, u32 |
| 4 | 4 | tag: three printable ASCII letters + one variant byte |
| 8 | `L - 4` | payload |

**`L` counts the tag**, so a chunk occupies `4 + L` bytes and its payload is `L - 4`
bytes. The next record begins at `tag_offset + L`. Getting this off by one four-byte
record is the single easiest way to mis-parse the format, and it is what the synthetic
tests pin down.

Observed tags on this disc (census over all 124 members, 353 chunks):

| Tag | Count | Notes |
|---|---:|---|
| `TIM2` | 124 | exactly one per member; payload is a TIM2 record (see below) |
| `MDL\xbc` | 85 | the common mesh record |
| `PMB\x01` | 65 | present in 65 of the 85 `MDL\xbc` members |
| `MDL\xac` | 54 | 4 per member in the 5-member family, 1 elsewhere |
| `PMB\x02` | 20 | variant of `PMB\x01` |
| `FLR\x00` | 5 | only in the 5-member family |

The tag's fourth byte is a variant selector — `MDL\xac` and `MDL\xbc` share three letters
and differ only there — so `is_tag()` checks the three letters are printable ASCII and
deliberately does *not* range-check the fourth. A parser that demanded `b"MDL\xac"` would
miss half the geometry on the disc.

## The four rules that actually bite

1. **The length field includes the tag.** `segment_size == 4 + L`, payload is `L - 4`.
   Measured on all 353 chunks.

2. **Not every member starts with a chunk.** A chunk chain is only accepted when it is
   self-evidently well formed, so 34 of the 124 members open with a structured
   non-tag preamble (one is ~800 bytes) that a naive "read a length, read a tag" loop
   rejects at byte 0. Segmentation therefore runs in two passes: walk the longest valid
   chain from the body start; if that chain does not land exactly on the body end, scan
   forward on 4-byte boundaries for the first offset whose own chain *does* land on the
   body end, and emit the gap in front of it as one opaque `raw` segment; anything still
   uncovered becomes a final `raw` segment.

3. **`raw` segments are how unknown bytes survive.** A `raw` segment is a run the module
   could not explain, stored verbatim. Every member carries exactly one; on this disc its
   size ranges from tens of bytes up to 3,644. Since segments tile the body exactly with
   no gaps and no overlaps, `parse -> serialize` is byte-identical **by construction** —
   it is arithmetic, not a lucky match on today's data.

4. **Fail loudly rather than half-parse.** `parse_member()` raises `MpacFormatError` for a
   blob without the `MPAC` magic, for a blob too short to hold the magic and a header
   size, and for an `H` that would place the body outside the blob — checked *before* any
   slicing, so a hostile header cannot make the parser allocate. `verify_roundtrip()`
   returns a list of problems and an empty list means clean.

## The cross-format result: a chunk tag is the payload's own magic

This is the most useful thing measured in this pass, and it is what makes the payloads
tractable later.

The `TIM2` chunk's payload begins `u32 17664`, then 24 bytes of `CD` filler, then the
ASCII `TIM2` again. Prefix the tag back onto the payload — `b"TIM2" + payload` — and you
have a complete TIM2 record exactly as `tools/formats/tim2.py` defines one: 4-byte magic,
u32 size, 24 `CD` filler, then the file header.

Measured on all 124 `TIM2` chunks in `MRY.AFS`:

| Check | Result |
|---|---|
| payload reconstitutes a TIM2 record that `tim2.parse()` accepts | 124 / 124 |
| records per chunk | 1×79, 2×28, 3×10, 4×2, 5×3, 19×2 |
| TIM2 prefix field equals `128 + picture.total_size` | 124 / 124 |

The last row is a genuinely independent confirmation across two modules written
separately: `tim2.py` locates the picture header at file-relative offset 128 and checks
that the prefix field agrees, while `mpac.py` never looks inside the payload at all. Both
agree on all 124 chunks.

Two practical consequences:

* A `TIM2` chunk holding 19 records is a texture atlas, so extracting "the texture of a
  model" is not a one-record operation.
* The variant byte is a *label*, not part of a payload: `MDL\xac` / `MDL\xbc` name two
  record kinds, which is why the tags are modelled in the framing layer rather than
  pushed down into a payload parser.

## What is known about the payloads (next targets, not yet modelled)

Recorded here so the next pass starts from measurement rather than a fresh file read.
None of this is trusted by the codec; it is preserved byte for byte.

* **`MDL\xac`** (54 chunks). Preamble is near-constant: a word that varies with the
  mesh, `0xFFFFFFFF`, a size-like word, then two constants — `0x00042513` (271,635) at
  offset 12 and `0xFFB2B2B2` at offset 16, the latter being the bytes
  `b2 b2 b2 ff`, i.e. what looks like an RGBA colour. Lengths run 232 to 7,224.
* **`MDL\xbc`** (85 chunks). Distinct preamble values and *embedded ASCII resource names*
  — `RM_000g`, `DR_001a`, `DR_002_1` have all been observed. Names inside the record make
  this the most promising next target: they give a label to attach to whatever geometry
  follows, and they are the same style of identifier Phase 4's world inventory will need.
* **`FLR\x00`** (5 chunks, 1,368 bytes each, all identical in length). One per member in
  the 5-member family.
* No arithmetic relation between the preamble words and the payload length held for
  either `MDL` variant — checked `(len - 16) % stride`, `(len - 20) % stride`,
  `(len - 24) % stride`, `(len - 32) % stride` against the offset-8 word for every
  chunk: 0 of 139 matched. So the mesh stride is not a single preamble word, and the
  vertex layout will have to be found by construction, not by division.

## Measured shape of the real archive

`data/raw/MRY.AFS` is the geometry archive: 157 AFS members, of which **124 are MPAC**
and 33 are 256 bytes of zero filler (the table-of-contents entries keep their slots).
Every MPAC member offset inside the AFS is 2048-aligned.

| Property | Measured |
|---|---|
| MPAC members | 124 |
| `H = 4` members | 5 (all size 39,096, all with the 6-chunk layout) |
| `H = 24` members | 119 |
| chunks total | 353 |
| trailer `FFFFFFFF` present | 124 / 124 |
| members whose first segment is a chunk | 90 |
| members whose first segment is raw | 34 |
| chunks per member | 2×34, 3×85, 6×5 |
| chunk sequences | `MDL,TIM2,PMB\x01` ×65; `MDL,TIM2` ×34; `MDL,TIM2,PMB\x02` ×20; `MDL,MDL,MDL,MDL,TIM2,FLR\x00` ×5 |
| round-trip byte-exact | 124 / 124 |

The four chunk sequences are the whole vocabulary: every member is a mesh plus its
texture plus (usually) one `PMB` record, and the 5-member family is the odd one out with
four meshes and a floor record. Nothing outside the six known tags occurs anywhere in the
archive.

## Verification

The Phase 2 exit criterion is *decode -> encode -> byte-equal*, and it is demonstrated
mechanically:

```
python -m tools.formats.mpac verify  data/raw/MRY.AFS    # prints per-member result; exit 0
python -m tools.formats.mpac info    data/raw/MRY.AFS    # tag census and family counts
python tests/run_tests.py                                # synthetic + real-disc tiers
```

`verify_roundtrip(path) -> list[str]` returns an empty list when every member re-encodes
byte for byte; the test suite asserts it is empty for `MRY.AFS`, and also asserts that the
segments tile each body exactly (`len(header) + sum(segment sizes) + len(trailer) == size`)
so a future change cannot pass the round-trip by losing bytes in two compensating places.

The synthetic tier builds members in memory from the framing rules above and needs no
disc data, so the suite runs on a bare checkout. The real-disc tier returns early when
`data/raw/MRY.AFS` is absent.
