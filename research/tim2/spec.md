# Format: TIM2 (PlayStation 2 texture)

Status: **spec written + round-trip proven** (Phase 2, read *and* write path).
Owner module: `tools/formats/tim2.py`.

## Why this format next

Phase 2 lists "models & textures (rooms, props)" as a family we must understand before
Phase 3 tooling can touch it. Of the five families on the roadmap, AFS (containers) and
MES (text) already had a written spec and a proven round-trip. Textures are the third:
they are the largest payload class we will want to replace, and unlike the container they
have a published shape to measure against — which is useful precisely *because* this disc
does not follow it (see "The tables that do not hold").

## Scope: read now, write proven, injection later

TIM2 is Konami/Sony middleware middleware with no normative document in this repo, so
every claim below is **measured from the user's own disc** and re-derived mechanically by
`tests/test_tim2.py`.

Both directions are implemented. `parse()` splits a blob into records; `serialize()`
rebuilds it. `serialize()` is byte-exact by *construction*, not by cleverness: it stores
the 32-byte record prefix, the file header and every payload as raw bytes and writes them
back in order. That is deliberate. The point of this phase is to prove the *boundaries*
are understood — where a record starts, where its picture header sits, and how long each
payload is. Repainting pixels is Phase 3/7 work, and this serializer already supports it,
because the lengths it writes come from the bytes it holds rather than from constants.

## Layout

Little-endian throughout.

A TIM2 texture on this disc appears as a **record**: a 32-byte packing prefix, followed
by a standard-looking TIM2 file header and its picture.

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | prefix magic: `TIM2` |
| 4 | 4 | prefix field (u32) — see rule 2 below |
| 8 | 24 | filler: 24 repetitions of `CD` (0x43 0x44) |
| 32 | 16 | TIM2 file header: magic `TIM2`, version (u32), picture count (u32), reserved (u32) |
| 48 | var | picture header, located at file-relative offset `128` |
| ... | var | colour table (CLUT) payload, then image payload |

The file-relative picture header (offset 128 from the record's body start, i.e. 128 bytes
after the 32-byte prefix) holds the fields this spec measures:

| Rel | Size | Field |
|---|---|---|
| 0 | 4 | `total_size` |
| 4 | 4 | `clut_size` |
| 8 | 4 | `image_size` |
| 12 | 2 | `header_size` |
| 14 | 2 | `clut_colors` |
| 16 | 1 | `pict_format` |
| 17 | 1 | `mipmap_count` |
| 18 | 1 | `clut_type` |
| 19 | 1 | `image_type` |
| 20 | 2 | `width` |
| 22 | 2 | `height` |

`_read_picture()` reads these 24 bytes (`PICTURE_CORE_SIZE`) and then requires the size
identity before it will accept the candidate.

## The four rules that actually bite

1. **The magic alone is not the record boundary.** The four bytes `TIM2` occur inside
   ordinary texture data, so a scanner that splits on `b"TIM2"` finds garbage. A real
   record prefix is `TIM2` **plus 24 bytes of `CD` filler**; `is_prefix()` demands both.
   This is the single most important rule in the format: it is what turns "find the magic"
   into "find the records".

2. **The prefix field is the record's own size, and it never disagrees.** `prefix_u32`
   equals `128 + picture.total_size` in **1,075 of 1,075** records. Measured across all
   four files, zero exceptions. This is also what settles the picture header's position:
   a byte-shifted read starting one byte early yields `127 + total_size` and would miss
   every record, so the field is an independent confirmation that offset 128 is correct
   rather than an assumption.

3. **The size identity is mandatory, and constants are not.** A candidate picture header
   is accepted only when `total_size == header_size + clut_size + image_size` *and*
   `rel + total_size` fits inside the record. `locate_picture()` tries offset 128 first
   and only then scans, and the fit check is what stops a byte-shifted coincidence from
   being mistaken for a header. `parse()` raises `Tim2FormatError` if the blob contains no
   prefix at all, rather than returning an empty archive that looks like success.

4. **Bytes outside the records are preserved, not regenerated.** Every file has leading
   bytes before its first record — 622,892 in `SYSTEM.AFS`, 539,680 in `ITEM1/2.AFS`,
   8,492 in `SYSEFF.ALD`. The archive stores them as `pad_pre` and writes them back
   verbatim. Records also carry their own trailing bytes with them, because each record's
   extent runs to the next prefix (or to end of blob). A repacker that recomputed either
   would relocate content silently.

## Measured shape of the four real files

Counted with the shipped parser (`python research/tim2/census.py`):

| File | Bytes | Prefixes | Records | Pictures located | Structural problems | Std-table violations | `pad_pre` |
|---|---:|---:|---:|---:|---:|---:|---:|
| `SYSTEM.AFS` | 26,896,384 | 416 | 416 | 416 | 0 | 383 | 622,892 |
| `ITEM1.AFS` | 44,926,976 | 320 | 320 | 320 | 0 | 294 | 539,680 |
| `ITEM2.AFS` | 41,773,056 | 312 | 312 | 312 | 0 | 290 | 539,680 |
| `SYSEFF.ALD` | 1,100,316 | 27 | 27 | 27 | 0 | 23 | 8,492 |
| **total** | — | **1,075** | **1,075** | **1,075** | **0** | **990** | — |

A repeated field census over the same 1,075 records:

| Field | Values observed |
|---|---|
| `version` | 4 (x1,075) |
| picture count | 1 (x1,075) |
| `header_size` | 128 (x1,075) |
| `clut_type` | 3 (x1,075) |
| `image_type` | 5 (x812), 4 (x263) |
| `pict_format` | 1 (x497), 5 (x263), 3 (x203), 7 (x43), 2 (x27), 16 (x21), 4 (x15), 9 (x6) |
| `clut_colors` | 256 (x810), 16 (x249), 32 (x4), 64 (x4), 112 (x2), 48 (x2), 512 (x2), 96 (x2) |
| width x height | 256x256 (x458), 512x512 (x293), 128x128 (x265), 32x32 (x24), 64x64 (x17), 16x16 (x12), 256x512 (x6) |

Every dimension is a power of two, and the geometry set is small enough that a mipmap or
atlas generator can be written against a closed list rather than a parser.

## The tables that do not hold

This is the part that matters for Phase 3, and it is why `picture_problems()` exists
separately from `problems()`.

The standard TIM2 `pict_format` table binds that field to a bits-per-pixel value
(0=16bpp, 1=24bpp, 2=32bpp, 3=4bpp, 4=8bpp, 5=16bpp). On this disc the field **does not
predict the payload size**: 990 of 1,075 records disagree with it. Record 0 of
`SYSTEM.AFS` is the clearest case — `pict_format` 5 nominally means 2.0 bytes/pixel, but
`image_size / (width * height)` is 0.5, i.e. 4 bits per pixel. A tool that sized a buffer
from `pict_format` would allocate four times the payload on that texture.

The two checks are therefore kept apart:

- `problems()` reports only what is **demonstrably wrong** — a missing prefix, a broken
  size identity, or a picture that runs past its record. On the real disc this is **0**,
  and `verify_roundtrip()` returns empty for all four files.
- `picture_problems()` reports where the **published tables** disagree with the bytes.
  This is 990 and is *reported, never enforced*: the spec cannot say which is wrong, the
  field or the payload, and rejecting the file would be a guess. A rewrite tool must
  derive bpp from `image_size` and the dimensions, not from `pict_format`.

## Verification

- `tests/test_tim2.py` (16 tests) runs under pytest *and* the stdlib fallback runner.
- **Synthetic tier**: records assembled in memory, so the suite passes on a bare checkout
  with no copyrighted input. Covers prefix detection with and without the `CD` filler,
  the `pad_pre` and trailer surviving verbatim, the scan fallback when offset 128 is
  empty, `problems()` flagging a body that is not TIM2, and every rejection path above.
- **Real-disc tier**: `test_real_texture_files_roundtrip_byte_exact` decodes and
  re-encodes the real files and requires byte equality, and
  `test_real_records_are_structurally_sound_and_share_one_geometry` pins the 1,075 / 0
  figures above so a future parser change that silently stops finding records fails loudly.
- **Independent cross-check**: the 416 records inside `SYSTEM.AFS` were cross-read against
  the same file parsed as an AFS archive by `tools/formats/afs.py`. All 416 sit *inside* a
  member, none straddles a member boundary, and they are spread across 91 of the 140
  members — so the two views nest rather than being disjoint, and the boundaries the
  scanner reports agree with the container table rather than coinciding by accident.
- **CLI**: `python -m tools.formats.tim2 info <file>` prints a per-record summary;
  `python -m tools.formats.tim2 verify <file>...` prints `ok`/`FAIL` per file and is the
  Phase 2 exit criterion ("decode -> encode -> byte-equal") made runnable.

## Open questions

- **The colour-table check never fires on this disc.** `clut_type` is 3 in all 1,075
  records, and the `clut_colors x bytes-per-entry == clut_size` check in
  `picture_problems()` only runs for `clut_type` 0/1/2. So "0 clut violations" means the
  colour tables were *not* verified, not that they were verified and passed. Confirming
  CLUT sizing needs either a record with a lower `clut_type` or an explicit rule for what
  type 3 means here.
- `pict_format` values 4 and 9 are uncommon (15 and 6 records). Whether they are a second
  pixel encoding or a mislabelled variant of 1/2 is unresolved; the round-trip does not
  depend on the answer, but a pixel decoder will.
- `clut_colors` 512 appears twice and 48/96/112 are not powers of two. Those may be real
  palettes, or the field may be repurposed. Not needed for boundary correctness.
- Payload bytes are carried verbatim and never interpreted, so this spec says nothing
  about pixel swizzling or CLUT ordering. Decoding pixels to an image is Phase 3 work.
