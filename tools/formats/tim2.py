"""TIM2 texture records: parser/serializer for the Phase 2 texture family.

What the bytes actually look like on this disc
----------------------------------------------
Measurement over ``data/raw/{SYSTEM,ITEM1}.AFS`` and ``SYSEFF.ALD`` found 763
occurrences of a 32-byte *record prefix*:

    +0   "TIM2"              ASCII magic
    +4   u32                 id/size-ish value; NOT a reliable record length (see spec)
    +8   24 x b"CD"          fixed filler (0x43 0x44), never 0xCD single bytes

Each prefix is immediately followed (delta exactly 32 in every observed case) by a
TIM2 v4 file header, and 128 bytes into *that* header sits a coherent picture header:

    magic  "TIM2" | version=4 | format=1 | num_pictures=1 | 8 bytes reserved
    112 bytes of zero padding
    picture header @ +128: total, clut_size, image_size, header_size, clut_colors,
                           pict_format, mipmap_count, clut_type, image_type, w, h

The identity ``total_size == header_size + clut_size + image_size`` holds exactly at
that offset. Two *standard* TIM2 relations do **not** hold for this disc, so this
module deliberately does not enforce them (see ``picture_problems``):

  * ``image_size`` implies 0.5 bytes/pixel for a record whose ``pict_format`` is 5
    (16-bit indexed, which implies 2.0);
  * ``clut_colors`` x (bits per CLUT entry) does not equal ``clut_size``.

Accepting only the relations that were actually demonstrated is the point: a codec
that "fixes up" a field from a relation that does not hold would corrupt real data.
Everything not modelled is preserved byte for byte, so decode -> encode is
byte-identical even where the semantics are still unknown.

Layout this module models
-------------------------
    [pad_pre]  [prefix(32)] [body ...]  [prefix(32)] [body ...]  ...
Record boundaries are the next prefix; any trailing bytes belong to the last body, so
round-tripping never depends on a guessed record length.

CLI:
    python -m tools.formats.tim2 info   data/raw/SYSTEM.AFS
    python -m tools.formats.tim2 verify data/raw/SYSTEM.AFS
    python -m tools.formats.tim2 extract data/raw/SYSTEM.AFS --out build/tim2
"""

from __future__ import annotations

import argparse
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = b"TIM2"
FILL = b"CD"
PREFIX_SIZE = 32
FILL_SIZE = 24
FILE_HEADER_SIZE = 16
PICTURE_CORE_SIZE = 24
# Verified picture-header offset, relative to the file-header magic (403/416 records;
# the rest are located by the fit check below, not by this constant).
PICTURE_REL = 128
# Standard TIM2 pict_format -> pixels per byte-pair, kept for *reporting* only.
STANDARD_BPP = {0: 2.0, 1: 3.0, 2: 4.0, 3: 0.5, 4: 1.0, 5: 2.0}


class Tim2FormatError(ValueError):
    """Raised when a blob is not a TIM2 record stream at all."""


def is_prefix(head: bytes) -> bool:
    """True if ``head`` begins with the 32-byte TIM2 record prefix."""
    return len(head) >= PREFIX_SIZE and head[:4] == MAGIC and head[8:32] == FILL * 12


def is_tim2(head: bytes) -> bool:
    """Cheap signature test for a texture record stream (only the prefix is needed)."""
    return is_prefix(head)


def find_prefixes(blob: bytes) -> list[int]:
    """Offsets of every TIM2 record prefix. The prefix (not the bare magic) is the
    anchor because a bare ``TIM2`` also matches the file header 32 bytes later."""
    out: list[int] = []
    i = blob.find(MAGIC)
    while i >= 0:
        if blob[i + 8:i + PREFIX_SIZE] == FILL * 12:
            out.append(i)
            i = blob.find(MAGIC, i + PREFIX_SIZE)
        else:
            i = blob.find(MAGIC, i + 1)
    return out


@dataclass
class Tim2Picture:
    """A picture header, parsed at the offset where the size identity holds."""

    rel: int          # offset of the picture header, relative to the file-header magic
    total_size: int
    clut_size: int
    image_size: int
    header_size: int
    clut_colors: int
    pict_format: int
    mipmap_count: int
    clut_type: int
    image_type: int
    width: int
    height: int

    @property
    def extended_size(self) -> int:
        """Bytes after the 24-byte core (GS registers / user data)."""
        return max(self.header_size - PICTURE_CORE_SIZE, 0)

    @property
    def clut_offset(self) -> int:
        return self.rel + self.header_size

    @property
    def image_offset(self) -> int:
        return self.rel + self.header_size + self.clut_size

    def bpp(self) -> float | None:
        """Bytes per pixel implied by the declared width/height, or None."""
        if not self.width or not self.height:
            return None
        return self.image_size / (self.width * self.height)


@dataclass
class Tim2Record:
    """One texture: a 32-byte prefix plus its body (file header, headers, pixels)."""

    index: int
    offset: int            # absolute offset of the prefix in the source blob
    prefix: bytes
    body: bytes            # everything after the prefix, up to the next prefix
    picture: Tim2Picture | None = None

    @property
    def prefix_u32(self) -> int:
        return struct.unpack_from("<I", self.prefix, 4)[0]

    @property
    def file_magic(self) -> bytes:
        return self.body[:4]

    @property
    def version(self) -> int:
        return self.body[4] if len(self.body) > 4 else -1

    @property
    def num_pictures(self) -> int:
        return struct.unpack_from("<H", self.body, 6)[0] if len(self.body) >= 8 else -1

    def image_bytes(self) -> bytes | None:
        """The image payload slice, when a picture header was located."""
        if self.picture is None:
            return None
        start = self.picture.image_offset
        end = start + self.picture.image_size
        if end > len(self.body):
            return None
        return self.body[start:end]

    def clut_bytes(self) -> bytes | None:
        if self.picture is None:
            return None
        start = self.picture.clut_offset
        end = start + self.picture.clut_size
        if end > len(self.body):
            return None
        return self.body[start:end]

    def serialize(self) -> bytes:
        """Prefix + body verbatim: byte-exact by construction."""
        return self.prefix + self.body

    def problems(self) -> list[str]:
        """Structural problems that are *demonstrably* wrong (not unproven semantics)."""
        out: list[str] = []
        if self.file_magic != MAGIC:
            out.append(f"record {self.index}: body does not start with TIM2 magic")
        p = self.picture
        if p is None:
            out.append(f"record {self.index}: no picture header located")
            return out
        if p.total_size != p.header_size + p.clut_size + p.image_size:
            out.append(f"record {self.index}: size identity broken")
        if p.rel + p.total_size > len(self.body):
            out.append(
                f"record {self.index}: picture extent {p.rel + p.total_size} "
                f"passes the record length {len(self.body)}"
            )
        return out

    def picture_problems(self) -> list[str]:
        """Relations from the *standard* TIM2 tables that this disc violates.

        Reported, never enforced: see the module docstring.
        """
        p = self.picture
        if p is None:
            return []
        out: list[str] = []
        expected = STANDARD_BPP.get(p.pict_format)
        implied = p.bpp()
        if expected is not None and implied is not None and abs(implied - expected) > 1e-9:
            out.append(
                f"record {self.index}: pict_format {p.pict_format} implies {expected} "
                f"bytes/pixel but image_size/(w*h) is {implied}"
            )
        entry_bits = (4, 2, 1)[p.clut_type] if p.clut_type < 3 else None
        if entry_bits is not None:
            if p.clut_colors * entry_bits * 2 != p.clut_size:
                out.append(
                    f"record {self.index}: {p.clut_colors} colours x {entry_bits * 2} "
                    f"bytes != clut_size {p.clut_size}"
                )
        return out


@dataclass
class Tim2Archive:
    """A prefix/body record stream, including any bytes outside the records."""

    pad_pre: bytes = b""
    records: list[Tim2Record] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def total_size(self) -> int:
        return len(self.pad_pre) + sum(len(r.prefix) + len(r.body) for r in self.records)

    def serialize(self) -> bytes:
        out = bytearray(self.pad_pre)
        for record in self.records:
            out += record.serialize()
        return bytes(out)


def _read_picture(body: bytes, rel: int) -> Tim2Picture | None:
    if rel + PICTURE_CORE_SIZE > len(body):
        return None
    total, clut, image = struct.unpack_from("<III", body, rel)
    hdr, colors = struct.unpack_from("<HH", body, rel + 12)
    fmt, mip, ctype, itype = body[rel + 16:rel + 20]
    w, h = struct.unpack_from("<HH", body, rel + 20)
    p = Tim2Picture(rel=rel, total_size=total, clut_size=clut, image_size=image,
                    header_size=hdr, clut_colors=colors, pict_format=fmt,
                    mipmap_count=mip, clut_type=ctype, image_type=itype,
                    width=w, height=h)
    # Only relations demonstrated on real data are required, so that a spurious
    # arithmetic match (e.g. a shifted read yielding a 33 MB "total") is rejected.
    if not (PICTURE_CORE_SIZE <= hdr <= 4096 and hdr % 16 == 0):
        return None
    if not (1 <= w <= 4096 and 1 <= h <= 4096 and image >= 1):
        return None
    if total != hdr + clut + image:
        return None
    if rel + total > len(body):
        return None
    return p


def locate_picture(body: bytes, search: int = 256) -> Tim2Picture | None:
    """Find the picture header near the file header.

    Prefers the known offset (128) and, failing that, the first offset whose size
    identity holds *and* whose picture fits inside the record. The fit check is what
    keeps a byte-shifted coincidence from being mistaken for a header.
    """
    preferred = _read_picture(body, PICTURE_REL)
    if preferred is not None:
        return preferred
    for rel in range(0, min(search, len(body)) + 1):
        candidate = _read_picture(body, rel)
        if candidate is not None:
            return candidate
    return None


def parse(blob: bytes) -> Tim2Archive:
    """Split a blob into records. Raises if it holds no TIM2 prefix at all."""
    offsets = find_prefixes(blob)
    if not offsets:
        raise Tim2FormatError("no TIM2 record prefix found (magic + 24 x 'CD' filler)")

    archive = Tim2Archive(pad_pre=blob[: offsets[0]])
    for index, start in enumerate(offsets):
        end = offsets[index + 1] if index + 1 < len(offsets) else len(blob)
        prefix = blob[start:start + PREFIX_SIZE]
        body = blob[start + PREFIX_SIZE:end]
        archive.records.append(
            Tim2Record(index=index, offset=start, prefix=prefix, body=body,
                       picture=locate_picture(body))
        )
    return archive


def parse_file(path: str | os.PathLike) -> Tim2Archive:
    with open(path, "rb") as fp:
        return parse(fp.read())


def verify_roundtrip(path: str | os.PathLike) -> list[str]:
    """decode -> encode -> byte-equal, the Phase 2 exit criterion for a format.

    Also walks every AFS member when given an archive, since on this disc the texture
    records live inside .AFS members.
    """
    with open(path, "rb") as fp:
        blob = fp.read()

    problems: list[str] = []
    if len(blob) >= 4 and blob[:3] == b"AFS":
        # Imported lazily so tools/formats/tim2.py stays usable without the container.
        from tools.formats import afs

        try:
            archive = afs.parse(blob)
        except afs.AfsFormatError as exc:
            return [str(exc)]
        for entry in archive.entries:
            member = blob[entry.offset:entry.end]
            if not is_prefix(member):
                continue
            problems += _verify_blob(member, f"{os.path.basename(str(path))}[{entry.index}]")
        return problems
    return _verify_blob(blob, os.path.basename(str(path)))


def _verify_blob(blob: bytes, label: str) -> list[str]:
    try:
        archive = parse(blob)
    except Tim2FormatError as exc:
        return [f"{label}: {exc}"]
    rebuilt = archive.serialize()
    if rebuilt == blob:
        return []
    problems = [f"{label}: round-trip mismatch ({len(rebuilt)} vs {len(blob)} bytes)"]
    for i in range(min(len(rebuilt), len(blob))):
        if rebuilt[i] != blob[i]:
            problems.append(f"  first difference at offset {i}")
            break
    return problems


# --------------------------------------------------------------------------- CLI
def _describe(path: str) -> None:
    with open(path, "rb") as fp:
        blob = fp.read()
    if blob[:3] == b"AFS":
        from tools.formats import afs

        archive = afs.parse(blob)
        print(f"{path}: AFS archive, {archive.count} members")
        for entry in archive.entries:
            member = blob[entry.offset:entry.end]
            if is_prefix(member):
                print(f"  member [{entry.index}] size={entry.size}")
                _describe_blob(member, indent="    ")
        return
    _describe_blob(blob)


def _describe_blob(blob: bytes, indent: str = "") -> None:
    archive = parse(blob)
    print(f"{indent}records={archive.count} pad_pre={len(archive.pad_pre)}B "
          f"total={archive.total_size}B")
    for record in archive.records[:8]:
        p = record.picture
        bits = f"prefix_u32={record.prefix_u32} v={record.version} pics={record.num_pictures}"
        if p:
            bits += (f" fmt={p.pict_format} {p.width}x{p.height} "
                     f"total={p.total_size} clut={p.clut_size} image={p.image_size} "
                     f"hdr={p.header_size} colors={p.clut_colors}")
        else:
            bits += " (no picture header located)"
        print(f"{indent}  [{record.index}] @{record.offset} {bits}")
    if archive.count > 8:
        print(f"{indent}  ... {archive.count - 8} more")


def _cmd_info(paths: list[str]) -> int:
    for path in paths:
        _describe(path)
    return 0


def _cmd_verify(paths: list[str]) -> int:
    failed = 0
    for path in paths:
        problems = verify_roundtrip(path)
        if problems:
            failed += 1
            print(f"FAIL {path}")
            for problem in problems[:10]:
                print(f"  {problem}")
        else:
            print(f"ok   {path}: byte-exact round-trip")
    return 1 if failed else 0


def _cmd_extract(path: str, out: Path) -> int:
    with open(path, "rb") as fp:
        blob = fp.read()
    out.mkdir(parents=True, exist_ok=True)

    def emit(blob_: bytes, stem: str) -> int:
        archive = parse(blob_)
        written = 0
        for record in archive.records:
            image = record.image_bytes()
            if image is None:
                continue
            (out / f"{stem}_{record.index:04d}.raw").write_bytes(image)
            clut = record.clut_bytes()
            if clut:
                (out / f"{stem}_{record.index:04d}.clut").write_bytes(clut)
            written += 1
        return written

    if blob[:3] == b"AFS":
        from tools.formats import afs

        archive = afs.parse(blob)
        total = 0
        for entry in archive.entries:
            member = blob[entry.offset:entry.end]
            if is_prefix(member):
                total += emit(member, f"{entry.index:04d}")
        print(f"extracted {total} image payloads to {out}")
        return 0
    print(f"extracted {emit(blob, 'blob')} image payloads to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.formats.tim2",
        description="Inspect, verify or unpack TIM2 texture records",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_info = sub.add_parser("info", help="print the record table")
    p_info.add_argument("paths", nargs="+")
    p_verify = sub.add_parser("verify", help="decode -> encode byte-exact check")
    p_verify.add_argument("paths", nargs="+")
    p_extract = sub.add_parser("extract", help="write raw image/CLUT payloads out")
    p_extract.add_argument("path")
    p_extract.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "info":
        return _cmd_info(args.paths)
    if args.command == "verify":
        return _cmd_verify(args.paths)
    return _cmd_extract(args.path, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
