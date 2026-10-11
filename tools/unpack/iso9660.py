"""Dependency-free ISO9660 (ECMA-119) reader for the Bast `unpack` stage.

Why hand-rolled: the Phase 1 contract (`docs/04-bast-modding-pipeline.md`, stage 1)
requires a fresh checkout to run with nothing but Python + `requirements.txt`. No
third-party ISO library is assumed to be present, so this module implements the subset
the pipeline actually needs:

  * volume descriptor scan starting at LBA 16 (primary + optional Joliet supplementary),
  * directory record parsing (both-endian field validation),
  * recursive directory walking with multi-extent file merging and cycle protection.

Read-only by construction: this module only ever seeks and reads from the source image,
so `unpack` cannot mutate the user's disc.

Format reference (offsets within a 2048-byte logical sector):

  PVD @ LBA 16
    0   type (1=primary, 2=supplementary, 255=terminator)
    1-5 'CD001'
    40-71  volume identifier (d-characters, space padded)
    80-87  volume space size, both-endian u32 LE then BE
    88-119 escape sequences (Joliet uses %/@ %/C %/E)
    128-131 logical block size, LE
    156-189 root directory record

  Directory record
    0   length of record
    1   extended attribute record length (in *blocks*, precedes the data)
    2-9  location of extent, both-endian u32 LE then BE
    10-17 data length, both-endian u32 LE then BE
    25  file flags (bit1 = directory, bit7 = multi-extent)
    32  length of file identifier
    33+ file identifier ('NAME.EXT;1', or UCS-2BE when the record came from Joliet)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

PVD_LBA = 16
MAX_VOLUME_DESCRIPTORS = 64
MAX_DIR_DEPTH = 32

VOLUME_DESC_PRIMARY = 1
VOLUME_DESC_SUPPLEMENTARY = 2
VOLUME_DESC_TERMINATOR = 255

FLAG_HIDDEN = 0x01
FLAG_DIRECTORY = 0x02
FLAG_ASSOCIATED = 0x04
FLAG_RECORD = 0x08
FLAG_PROTECTION = 0x10
FLAG_MULTI_EXTENT = 0x80

JOLIET_ESCAPES = (b"%/@", b"%/C", b"%/E")


class IsoFormatError(Exception):
    """The image is not ISO9660, or a structure inside it is malformed."""


@dataclass
class DirRecord:
    """One parsed directory record (a single file, or one extent of a file)."""

    name: str
    raw_name: bytes
    is_dir: bool
    flags: int
    ext_attr_len: int
    lba: int
    size: int

    @property
    def multi_extent(self) -> bool:
        return bool(self.flags & FLAG_MULTI_EXTENT)


@dataclass
class FileEntry:
    """A logical file or directory in the image, with all of its extents.

    `lba`/`size` are the raw on-disc values taken from the first directory record.
    `segments` holds the *data* extents (extended-attribute blocks already skipped),
    which is what a reader must actually concatenate.
    """

    path: str
    is_dir: bool
    size: int
    lba: int
    ext_attr_len: int
    segments: list[tuple[int, int]] = field(default_factory=list)


def decode_name(raw: bytes, *, joliet: bool) -> str:
    """Turn a raw file identifier into a plain name.

    Strips the ISO9660 version suffix (``;1``) and, for Joliet records, decodes
    UCS-2 big-endian. NUL padding is dropped.
    """
    if raw == b"\x00":
        return "."
    if raw == b"\x01":
        return ".."
    text = raw.decode("utf-16-be" if joliet else "ascii", errors="replace")
    text = text.replace("\x00", "")
    if ";" in text:
        text = text.split(";", 1)[0]
    return text


def parse_dir_record(buf: bytes, off: int, *, joliet: bool = False) -> DirRecord | None:
    """Parse the directory record at `off`, or return None if it is unusable.

    Both-endian pairs are cross-checked: a mismatch means the record is corrupt, and
    silently trusting one half is how you end up extracting garbage.
    """
    if off + 33 > len(buf):
        return None
    length = buf[off]
    if length < 33:
        return None
    if off + length > len(buf):
        return None

    ext_attr_len = buf[off + 1]

    lba_le = int.from_bytes(buf[off + 2 : off + 6], "little")
    lba_be = int.from_bytes(buf[off + 6 : off + 10], "big")
    if lba_le != lba_be:
        raise IsoFormatError(
            f"extent LBA both-endian mismatch at offset {off}: {lba_le} != {lba_be}"
        )

    size_le = int.from_bytes(buf[off + 10 : off + 14], "little")
    size_be = int.from_bytes(buf[off + 14 : off + 18], "big")
    if size_le != size_be:
        raise IsoFormatError(
            f"data length both-endian mismatch at offset {off}: {size_le} != {size_be}"
        )

    flags = buf[off + 25]
    name_len = buf[off + 32]
    if off + 33 + name_len > len(buf):
        return None
    raw_name = bytes(buf[off + 33 : off + 33 + name_len])

    return DirRecord(
        name=decode_name(raw_name, joliet=joliet),
        raw_name=raw_name,
        is_dir=bool(flags & FLAG_DIRECTORY),
        flags=flags,
        ext_attr_len=ext_attr_len,
        lba=lba_le,
        size=size_le,
    )


class Iso9660:
    """Read-only ISO9660 image reader.

    Usage::

        with open(iso_path, "rb") as fp:
            image = Iso9660(fp)
            for entry in image.walk():
                ...
    """

    def __init__(self, fp, *, sector_size: int = 2048):
        self.fp = fp
        self.sector_size = sector_size
        self.roots: dict[str, DirRecord] = {}
        self.volume_id = ""
        self.volume_space_size = 0
        self.joliet = False
        self.warnings: list[str] = []
        self._read_volume_descriptors()

    # ---------------------------------------------------------------- descriptors
    def _read_volume_descriptors(self) -> None:
        seen_terminator = False
        for i in range(MAX_VOLUME_DESCRIPTORS):
            lba = PVD_LBA + i
            sector = self._read_sector(lba)
            if sector[1:6] != b"CD001":
                if i == 0:
                    raise IsoFormatError(
                        f"no ISO9660 primary volume descriptor at LBA {lba} "
                        f"(magic was {sector[1:6]!r})"
                    )
                break
            vd_type = sector[0]
            if vd_type == VOLUME_DESC_PRIMARY:
                self.volume_id = sector[40:72].decode("ascii", "replace").strip()
                self.volume_space_size = int.from_bytes(sector[80:84], "little")
                block_size = int.from_bytes(sector[128:130], "little")
                if block_size:
                    self.sector_size = block_size
                root = parse_dir_record(sector, 156)
                if root is None:
                    raise IsoFormatError("primary volume descriptor has no root record")
                self.roots["iso9660"] = root
            elif vd_type == VOLUME_DESC_SUPPLEMENTARY:
                escapes = sector[88:91]
                if escapes in JOLIET_ESCAPES:
                    root = parse_dir_record(sector, 156, joliet=True)
                    if root is not None:
                        self.roots["joliet"] = root
                        self.joliet = True
            elif vd_type == VOLUME_DESC_TERMINATOR:
                seen_terminator = True
                break
        if "iso9660" not in self.roots:
            raise IsoFormatError("no primary volume descriptor found")
        if not seen_terminator:
            self.warnings.append("no volume descriptor terminator found")

    # ---------------------------------------------------------------------- io
    def _read_sector(self, lba: int) -> bytes:
        self.fp.seek(lba * self.sector_size)
        return self.fp.read(self.sector_size)

    def read_extent(self, lba: int, size: int) -> bytes:
        """Read `size` bytes of payload starting at logical block `lba`."""
        if size < 0:
            raise IsoFormatError(f"negative extent size {size}")
        self.fp.seek(lba * self.sector_size)
        data = self.fp.read(size)
        if len(data) != size:
            raise IsoFormatError(
                f"short read at LBA {lba}: wanted {size} bytes, got {len(data)}"
            )
        return data

    # --------------------------------------------------------------- traversal
    def iter_dir(self, rec: DirRecord, *, layer: str = "iso9660") -> list[DirRecord]:
        """Parse one directory's contents (not recursive)."""
        data_lba = rec.lba + rec.ext_attr_len
        data = self.read_extent(data_lba, rec.size)
        joliet = layer == "joliet"
        out: list[DirRecord] = []
        pos = 0
        ss = self.sector_size
        total = len(data)
        while pos < total:
            length = data[pos]
            if length == 0:
                # Records never straddle a sector boundary: 0 means "skip the rest".
                pos = (pos // ss + 1) * ss
                continue
            if length < 33:
                raise IsoFormatError(
                    f"illegal directory record length {length} at offset {pos}"
                )
            if pos // ss != (pos + length - 1) // ss:
                pos = (pos // ss + 1) * ss
                continue
            parsed = parse_dir_record(data, pos, joliet=joliet)
            if parsed is not None:
                out.append(parsed)
            pos += length
        return out

    def walk(self, *, layer: str = "iso9660") -> Iterator[FileEntry]:
        """Depth-first walk of the whole tree, sorted, with multi-extent merging.

        Multi-extent files (flag bit 7) are stored as several consecutive records with
        the same name; only the final record clears the bit. Their extents are merged
        into a single entry so downstream code never sees a half file.
        """
        root = self.roots.get(layer)
        if root is None:
            raise IsoFormatError(f"image has no {layer} root directory")

        stack: list[tuple[str, DirRecord, int]] = [("", root, 0)]
        visited: set[int] = set()

        while stack:
            prefix, rec, depth = stack.pop()
            if depth > MAX_DIR_DEPTH:
                raise IsoFormatError(f"directory nesting exceeds {MAX_DIR_DEPTH}: {prefix!r}")
            if rec.lba in visited:
                self.warnings.append(f"cycle skipped at {prefix!r}")
                continue
            visited.add(rec.lba)

            children = sorted(
                self.iter_dir(rec, layer=layer),
                key=lambda c: (c.name != ".", c.name != "..", c.name),
            )
            pending: dict[str, list[DirRecord]] = {}
            subdirs: list[tuple[str, DirRecord]] = []

            for child in children:
                if child.name in (".", ".."):
                    continue
                if child.is_dir:
                    path = f"{prefix}{child.name}/"
                    yield FileEntry(
                        path=path,
                        is_dir=True,
                        size=child.size,
                        lba=child.lba,
                        ext_attr_len=child.ext_attr_len,
                        segments=[(child.lba + child.ext_attr_len, child.size)],
                    )
                    subdirs.append((path, child))
                    continue

                chain = pending.setdefault(child.name, [])
                chain.append(child)
                if child.multi_extent:
                    continue

                segments = [
                    (c.lba + c.ext_attr_len, c.size)
                    for c in chain
                ]
                first = chain[0]
                yield FileEntry(
                    path=prefix + child.name,
                    is_dir=False,
                    size=sum(c.size for c in chain),
                    lba=first.lba,
                    ext_attr_len=first.ext_attr_len,
                    segments=segments,
                )
                pending.pop(child.name, None)

            for name, chain in pending.items():
                self.warnings.append(
                    f"{prefix}{name}: multi-extent chain never terminated; "
                    f"{len(chain)} extent(s) kept"
                )
                segments = [(c.lba + c.ext_attr_len, c.size) for c in chain]
                yield FileEntry(
                    path=prefix + name,
                    is_dir=False,
                    size=sum(c.size for c in chain),
                    lba=chain[0].lba,
                    ext_attr_len=chain[0].ext_attr_len,
                    segments=segments,
                )

            for path, child in reversed(subdirs):
                stack.append((path, child, depth + 1))
