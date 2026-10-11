# Format: ISO9660 / ECMA-119 (PS2 disc image)

Status: **spec written + round-trip proven** (Phase 1, read path).
Owner module: `tools/unpack/iso9660.py`.

## Why this format first

Every later stage is downstream of "get the files out of the disc". ISO9660 is the
outermost container on a PS2 disc, and unlike the inner Capcom formats it is a public
standard, so it can be implemented exactly and tested offline. It is the one format we
never have to guess about.

## Scope: read-only, deliberately

Phase 1 only ever *reads* the image. `docs/04-bast-modding-pipeline.md` (stage 1) requires
`unpack` to be read-only with respect to the ISO, and `docs/07-asset-policy.md` requires the
user's disc to stay untouched. There is therefore no writer in this module; ISO *repacking*
with LBA awareness is Phase 3/7 work and will be a separate spec + module.

## Structures

### Volume descriptors (start at LBA 16)

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | descriptor type: 1 = primary, 2 = supplementary, 255 = terminator |
| 1 | 5 | standard identifier, must be `CD001` |
| 6 | 1 | version, 1 |
| 40 | 32 | volume identifier, d-characters, space padded |
| 80 | 8 | volume space size (both-endian u32: LE then BE) |
| 88 | 3 | escape sequences — Joliet uses `%/@`, `%/C` or `%/E` |
| 120 | 4 | volume set size |
| 128 | 4 | logical block size, little-endian (authoritative sector size) |
| 132 | 4 | path table size |
| 156 | 34 | root directory record |

The 2048-byte, 16-sector offset for the PVD is not arbitrary: it is the "system area"
(32 KiB) of the disc, reserved so a bootable volume can put a platform loader ahead of the
filesystem. PS2 discs use `SYSTEM.CNF` inside the filesystem instead.

### Directory record

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | record length (0 = end of the records in this logical sector) |
| 1 | 1 | extended attribute record length, in **blocks**, immediately before the data |
| 2 | 8 | location of extent (both-endian u32: LE then BE) |
| 10 | 8 | data length (both-endian u32) |
| 18 | 7 | recording date and time |
| 25 | 1 | file flags: bit0 hidden, bit1 directory, bit3 record, bit7 multi-extent |
| 26 | 1 | file unit size (interleaved files) |
| 27 | 1 | interleave gap size |
| 28 | 4 | volume sequence number (both-endian u16) |
| 32 | 1 | file identifier length |
| 33 | n | file identifier, plus a pad byte when `n` is odd |

## The four rules that actually bite

1. **Both-endian fields must be cross-checked, not trusted.** Every 32-bit location and
   length is stored twice, little-endian first. Our parser raises `IsoFormatError` when the
   two disagree. Silently picking one half is how extraction produces plausible garbage that
   only fails much later.

2. **Records never straddle a logical sector.** A zero length byte means "the remaining
   bytes of this sector are padding — continue at the next sector", *not* "end of
   directory". Treating 0 as end-of-directory truncates any directory large enough to need
   padding, which on a 1.8 GB disc is most of them.

3. **Extended attribute records shift the data by `ext_attr_len` blocks.** The extent LBA
   points at the attribute record when `ext_attr_len > 0`; payload starts at
   `lba + ext_attr_len`. Rare on real discs, free to handle, catastrophic to ignore.

4. **Multi-extent files arrive as several records with the same name.** Only the last has
   flag bit 7 clear. They must be concatenated in order; a naive reader silently returns
   just the first fragment, and `SLUS_*.XX` is exactly the kind of large file that gets
   split.

Also: volume descriptor numbering is not fixed. The supplementary descriptor must be found
by scanning, because its index varies between discs.

## Joliet

PS2 discs commonly carry a Joliet (UCS-2BE) supplementary descriptor alongside the
primary one, for long filenames with mixed case. The reader records both roots and
defaults to the primary. The manifest stores the ISO9660 path, so the record is stable
regardless of which layer a tool reads.

## Verification

- `tests/_iso_fixture.py` builds a synthetic image in memory (PVD + terminator + nested
  directory + the shapes above) so the reader is tested without the real 1.8 GB disc.
- `tests/test_iso9660.py` asserts: sector-boundary padding walk, both-endian mismatch
  raises, directory flag, name `;1` version stripping, nested traversal, root dirs excluded.
- `tests/test_extract.py` asserts end-to-end: extraction, SHA-256 agreement with the
  written bytes, `SYSTEM.CNF` boot detection (`BOOT2 = cdrom0:\SLUS_215.02;1` → the boot
  executable name), and that `--verify` detects drift.

Golden round-trip (Phase 3) does not apply to this module: we never re-encode the
filesystem yet. The read path is proven by re-hashing extracted bytes against the
manifest.
