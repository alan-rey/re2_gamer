"""AFS archive format (Phase 2 spec + parser/serializer).

AFS is the container the CRILAYLA-era middleware on this disc uses to bundle many
members into one file. On this disc it is *the* dominant container: 12 archives holding
~99% of the disc's bytes (``RDX_LNK.AFS`` 588 MB down to ``ADV.AFS`` 7 MB). Phase 3's
``unpack`` ("ISO -> archives -> individual files") therefore cannot exist without it,
which is why it is the first format reversed.

Layout (little-endian throughout)::

    offset 0   : magic       4 bytes   b"AFS\\x00" or b"AFS " (space)
    offset 4   : count       u32       number of members
    offset 8   : table       count * (u32 offset, u32 size)  -- ascending offsets
    offset H   : padding     bytes up to the first member (not declared anywhere)
    offset ... : members     each at a 2048-byte-aligned offset, gaps between them
    EOF        : trailer     bytes after the final member

where ``H = 8 + 8 * count``. Sizes in the table are the *true* member lengths. Member
start offsets are 2048-aligned. The pre-first-member padding, the inter-member gaps and
the trailer are not described by any field, so this module records them verbatim and
writes them back, which is what makes ``serialize(parse(x)) == x`` byte for byte. That
matters for Phase 3: a repack that silently zeroed this padding would produce a
different file than the disc shipped, and the golden round-trip test would fail for a
reason that has nothing to do with our content.

Evidence for the layout is recorded in ``research/afs/spec.md``, measured from the
user's own disc, and re-derived mechanically by ``tests/test_afs.py``.

Usage::

    python -m tools.formats.afs list    data/raw/MRY.AFS
    python -m tools.formats.afs verify  data/raw/MRY.AFS
    python -m tools.formats.afs extract data/raw/MRY.AFS --out build/afs/mry
"""

from __future__ import annotations

import argparse
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

MAGIC_NULL = b"AFS\x00"
MAGIC_SPACE = b"AFS "
MAGIC_VARIANTS: tuple[bytes, ...] = (MAGIC_NULL, MAGIC_SPACE)
MAGIC_PREFIX = b"AFS"
HEADER_SIZE = 8
ENTRY_SIZE = 8
ALIGNMENT = 2048


class AfsFormatError(Exception):
    """Raised when bytes do not conform to the AFS layout described above."""


@dataclass
class AfsEntry:
    """One archive member.

    ``payload`` is ``None`` for a table-only parse (listing / streaming extraction).
    ``pad`` is the verbatim gap between this member's end and the next member's start;
    it is ``b""`` for the last member (any trailing bytes live in ``AfsArchive.trailer``)
    and for any table-only parse.
    """

    index: int
    offset: int
    size: int
    payload: bytes | None = None
    pad: bytes = b""

    @property
    def end(self) -> int:
        return self.offset + self.size


@dataclass
class AfsArchive:
    """A parsed AFS container.

    ``serialize`` recomputes the table from the current payload sizes, so it is correct
    after a member grows, while still reproducing an untouched archive exactly.
    """

    magic: bytes
    entries: list[AfsEntry] = field(default_factory=list)
    pad_pre: bytes = b""
    trailer: bytes = b""
    source_size: int | None = None

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def header_size(self) -> int:
        return HEADER_SIZE + ENTRY_SIZE * self.count

    def total_payload_bytes(self) -> int:
        return sum(e.size for e in self.entries)

    def first_offset(self) -> int:
        return self.entries[0].offset if self.entries else self.header_size

    def serialize(self) -> bytes:
        """Rebuild the container. Requires every entry to carry its payload."""
        missing = [e.index for e in self.entries if e.payload is None]
        if missing:
            raise AfsFormatError(
                f"cannot serialize: payloads not loaded for entries {missing[:5]}"
            )

        # Lay members out after the header and the preserved pre-padding, keeping each
        # member's own gap. For an unmodified archive this reproduces the original
        # offsets exactly, because the gaps are the originals.
        offset = self.header_size + len(self.pad_pre)
        table: list[tuple[int, int]] = []
        for entry in self.entries:
            payload = entry.payload
            assert payload is not None  # guarded above
            table.append((offset, len(payload)))
            offset += len(payload) + len(entry.pad)

        out = bytearray()
        out += self.magic
        out += struct.pack("<I", self.count)
        for off, size in table:
            out += struct.pack("<II", off, size)
        out += self.pad_pre
        for entry in self.entries:
            assert entry.payload is not None
            out += entry.payload
            out += entry.pad
        out += self.trailer
        return bytes(out)

    def verify(self) -> list[str]:
        """Structural self-checks. Returns a list of problems (empty means clean)."""
        problems: list[str] = []
        if self.magic[:3] != MAGIC_PREFIX:
            problems.append(f"magic {self.magic!r} is not an AFS signature")
        elif self.magic not in MAGIC_VARIANTS:
            problems.append(f"unexpected magic variant {self.magic!r}")

        header_end = self.header_size
        prev_end = header_end
        for entry in self.entries:
            if entry.offset < header_end:
                problems.append(
                    f"entry {entry.index}: offset {entry.offset} overlaps the header"
                )
            if entry.offset < prev_end:
                problems.append(
                    f"entry {entry.index}: offset {entry.offset} is not ascending"
                )
            if entry.offset % ALIGNMENT:
                problems.append(
                    f"entry {entry.index}: offset {entry.offset} is not "
                    f"{ALIGNMENT}-byte aligned"
                )
            if self.source_size is not None and entry.end > self.source_size:
                problems.append(
                    f"entry {entry.index}: end {entry.end} passes EOF {self.source_size}"
                )
            prev_end = max(prev_end, entry.end)
        return problems


def is_afs(head: bytes) -> bool:
    """Cheap signature test (needs only the first 4 bytes)."""
    return len(head) >= 4 and head[:3] == MAGIC_PREFIX


def _parse_table(head: bytes, count: int) -> list[tuple[int, int]]:
    """Unpack the ``count`` (offset, size) pairs from the header bytes."""
    header_end = HEADER_SIZE + ENTRY_SIZE * count
    if header_end > len(head):
        raise AfsFormatError(
            f"count {count} needs a {header_end}-byte header but only {len(head)} "
            "bytes are available"
        )
    pairs = struct.unpack_from(f"<{count * 2}I", head, HEADER_SIZE) if count else ()
    return [(pairs[i * 2], pairs[i * 2 + 1]) for i in range(count)]


def _assemble(
    magic: bytes,
    raw: list[tuple[int, int]],
    *,
    source_size: int,
    blob: bytes | None,
    load_payloads: bool,
) -> AfsArchive:
    """Validate table entries and build the archive.

    Shared by the full parse and the table-only parse so the validation rules exist in
    exactly one place. ``blob`` is the whole file when available; without it the
    payloads and padding are left unset rather than guessed.
    """
    archive = AfsArchive(magic=magic, entries=[], source_size=source_size)
    header_end = HEADER_SIZE + ENTRY_SIZE * len(raw)

    previous_end = header_end
    for index, (offset, size) in enumerate(raw):
        end = offset + size
        if end > source_size:
            raise AfsFormatError(
                f"entry {index}: offset {offset} + size {size} passes EOF {source_size}"
            )
        if offset < header_end:
            raise AfsFormatError(f"entry {index}: offset {offset} sits inside the header")
        if offset < previous_end:
            raise AfsFormatError(
                f"entry {index}: offset {offset} overlaps the previous member "
                f"(which ends at {previous_end})"
            )
        payload = None
        if blob is not None and load_payloads:
            payload = blob[offset:end]
        archive.entries.append(
            AfsEntry(index=index, offset=offset, size=size, payload=payload)
        )
        previous_end = end

    if blob is not None:
        # Everything that is not a member is preserved verbatim: the pre-padding, the
        # gap after each member, and the trailer after the last one.
        archive.pad_pre = blob[header_end : raw[0][0]] if raw else b""
        for index, entry in enumerate(archive.entries):
            if index + 1 < len(raw):
                entry.pad = blob[entry.end : raw[index + 1][0]]
        archive.trailer = blob[raw[-1][0] + raw[-1][1] :] if raw else blob[header_end:]
    return archive


def parse(blob: bytes, source_size: int | None = None, load_payloads: bool = True) -> AfsArchive:
    """Parse a whole AFS image held in memory."""
    if len(blob) < HEADER_SIZE:
        raise AfsFormatError(f"file is {len(blob)} bytes; too short to be AFS")
    magic = blob[:4]
    if not is_afs(magic):
        raise AfsFormatError(f"bad magic {magic!r}; expected one of {MAGIC_VARIANTS}")

    (count,) = struct.unpack_from("<I", blob, 4)
    raw = _parse_table(blob, count)
    return _assemble(
        magic,
        raw,
        source_size=source_size or len(blob),
        blob=blob,
        load_payloads=load_payloads,
    )


def parse_file(path: str | os.PathLike, load_payloads: bool = True) -> AfsArchive:
    """Parse an AFS file from disk.

    Reads the whole file, which is what ``serialize`` needs to reproduce the padding and
    trailer. Use `parse_table_file` when only the member table is wanted.
    """
    with open(path, "rb") as fp:
        blob = fp.read()
    return parse(blob, source_size=len(blob), load_payloads=load_payloads)


def parse_table_file(path: str | os.PathLike) -> AfsArchive:
    """Read only the header + table (``8 + 8 * count`` bytes) from an AFS file.

    Streaming-friendly: listing ``RDX_LNK.AFS`` costs ~1.6 KB of reads instead of 588 MB.
    The returned entries have no payload and no padding, which is enough for listing and
    for pulling members out one at a time with `read_entry`.
    """
    size = os.path.getsize(path)
    with open(path, "rb") as fp:
        head = fp.read(HEADER_SIZE)
        if len(head) < HEADER_SIZE:
            raise AfsFormatError(f"{path}: {size} bytes is too short to be AFS")
        if not is_afs(head):
            raise AfsFormatError(f"{path}: bad magic {head[:4]!r}")
        (count,) = struct.unpack_from("<I", head, 4)
        header_end = HEADER_SIZE + ENTRY_SIZE * count
        if header_end > size:
            raise AfsFormatError(
                f"{path}: count {count} needs a {header_end}-byte header but the file "
                f"is {size} bytes"
            )
        head += fp.read(header_end - HEADER_SIZE)

    raw = _parse_table(head, count)
    return _assemble(
        head[:4], raw, source_size=size, blob=None, load_payloads=False
    )


def read_entry(fp, entry: AfsEntry) -> bytes:
    """Read one member's bytes from an open file handle (streaming extraction)."""
    fp.seek(entry.offset)
    data = fp.read(entry.size)
    if len(data) != entry.size:
        raise AfsFormatError(
            f"entry {entry.index}: short read ({len(data)} of {entry.size} bytes)"
        )
    return data


def verify_roundtrip(path: str | os.PathLike) -> list[str]:
    """decode -> encode -> byte-equal, per docs/03-tech-stack-tools.md.

    This is the Phase 2 exit criterion for the format: no format may be injected into
    until it round-trips byte for byte. Returns problems; an empty list means clean.
    """
    try:
        archive = parse_file(path, load_payloads=True)
    except AfsFormatError as exc:
        return [str(exc)]

    problems = archive.verify()
    rebuilt = archive.serialize()
    with open(path, "rb") as fp:
        original = fp.read()
    if rebuilt != original:
        problems.append(
            f"round-trip mismatch: rebuilt {len(rebuilt)} bytes vs "
            f"{len(original)} on disk"
        )
        for i in range(min(len(rebuilt), len(original))):
            if rebuilt[i] != original[i]:
                problems.append(f"  first difference at offset {i}")
                break
    return problems


# --------------------------------------------------------------------------- CLI
def _cmd_list(paths: list[str]) -> int:
    for path in paths:
        archive = parse_table_file(path)
        print(
            f"{path}: magic={archive.magic!r} members={archive.count} "
            f"size={archive.source_size} payload={archive.total_payload_bytes()}"
        )
        for entry in archive.entries[:10]:
            aligned = "aligned" if entry.offset % ALIGNMENT == 0 else "MISALIGNED"
            print(f"  [{entry.index}] offset={entry.offset} size={entry.size} {aligned}")
        if archive.count > 10:
            print(f"  ... {archive.count - 10} more")
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
            archive = parse_table_file(path)
            print(f"ok   {path}: {archive.count} members, byte-exact round-trip")
    return 1 if failed else 0


def _cmd_extract(path: str, out: Path) -> int:
    archive = parse_table_file(path)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(path).stem.lower()
    with open(path, "rb") as fp:
        for entry in archive.entries:
            (out / f"{stem}_{entry.index:04d}.bin").write_bytes(read_entry(fp, entry))
    print(f"extracted {archive.count} members to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.formats.afs",
        description="Inspect, verify or unpack an AFS archive",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_list = sub.add_parser("list", help="print the member table")
    p_list.add_argument("paths", nargs="+")
    p_verify = sub.add_parser("verify", help="decode -> encode byte-exact check")
    p_verify.add_argument("paths", nargs="+")
    p_extract = sub.add_parser("extract", help="write members out as individual files")
    p_extract.add_argument("path")
    p_extract.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "list":
        return _cmd_list(args.paths)
    if args.command == "verify":
        return _cmd_verify(args.paths)
    return _cmd_extract(args.path, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
