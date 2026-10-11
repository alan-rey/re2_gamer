"""MPAC model packages: parser/serializer for the Phase 2 geometry family.

What the bytes actually look like on this disc
----------------------------------------------
``data/raw/MRY.AFS`` is the geometry archive. Its 124 non-zero members all begin
with ``MPAC``; the 33 remaining members are 256 bytes of zero filler.

Envelope (verified on 124/124 members)::

    +0   "MPAC"           ASCII magic
    +4   u32  H           header size; the body starts at 4+H
    +8   H-4 bytes        header payload (uninterpreted here)
    ...  body
    end  u32 0xFFFFFFFF   trailer, present in 124/124 members

``H`` takes exactly two values on this disc: ``4`` (5 members, size 39096) and
``24`` (119 members).  It is a *size*, not a version, so nothing here depends on
those particular numbers.

Body framing (verified 124/124): a sequence of records, each either

    chunk:  u32 L | 4-byte tag | L bytes      next record at tag_offset + L

The length counts the tag, so a chunk occupies ``4 + L`` bytes and its payload is
``L - 4`` bytes.  Tags are three printable ASCII letters plus one variant byte,
e.g. ``MDL\\xac``, ``MDL\\xbc``, ``TIM2``, ``PMB\\x01``, ``PMB\\x02``, ``FLR\\x00``.
Only four tag sequences occur in the whole archive::

    65x  MDL.. TIM2 PMB\\x01
    34x  MDL.. TIM2
    20x  MDL.. TIM2 PMB\\x02
     5x  MDL.. MDL.. MDL.. MDL.. TIM2 FLR\\x00

Not every member starts with a record.  A chunk chain is only accepted if it is
self-evidently well formed, so members whose body opens with ~800 bytes of
structured non-tag data (the 119-member family) would otherwise yield nothing at
all.  The segmentation therefore runs in two passes -- the same algorithm that was
validated across all 124 members:

  1. walk the longest valid chunk chain forward from the body start;
  2. if that chain does not reach the body end, scan forward (4-byte aligned) for
     the first offset whose own chain lands *exactly* on the body end, and emit
     the gap before it as one opaque ``raw`` segment;
  3. anything still uncovered becomes a final ``raw`` segment.

Because every byte of the body is placed in exactly one segment,
``parse -> serialize`` is byte-identical by construction; anything the module does
not understand is carried verbatim rather than guessed at.  That is the same
contract as ``tools/formats/tim2.py``: a codec that "fixed up" a field it cannot
verify would corrupt real data.

Not modelled (deliberately): the contents of ``MDL``/``PMB``/``FLR`` payloads, and
the H-4 header bytes.  Those are the next Phase 2 targets, and they are preserved
byte for byte until they are understood.

CLI::

    python -m tools.formats.mpac info    data/raw/MRY.AFS
    python -m tools.formats.mpac verify  data/raw/MRY.AFS
    python -m tools.formats.mpac extract data/raw/MRY.AFS --out build/mpac
"""

from __future__ import annotations

import argparse
import collections
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = b"MPAC"
LENGTH_SIZE = 4
TAG_SIZE = 4
RECORD_PREFIX = LENGTH_SIZE + TAG_SIZE  # a chunk costs this much before its payload
TRAILER = b"\xff\xff\xff\xff"
OBSERVED_HEADER_SIZES = (4, 24)


class MpacFormatError(ValueError):
    """Raised when a blob is not an MPAC member, or not a container of one."""


def _u32(blob: bytes, off: int) -> int:
    return struct.unpack_from("<I", blob, off)[0]


def is_tag(tag: bytes) -> bool:
    """True if ``tag`` is shaped like an observed chunk tag.

    Three printable ASCII letters plus an arbitrary variant byte -- the variant is
    what separates ``MDL\\xac`` from ``MDL\\xbc``, so it is deliberately not range
    checked.
    """
    return len(tag) == TAG_SIZE and all(0x20 <= c < 0x7F for c in tag[:3])


def is_mpac(head: bytes) -> bool:
    """Cheap signature test; only the 4-byte magic is needed."""
    return len(head) >= 8 and head[:4] == MAGIC


@dataclass
class MpacSegment:
    """One framed record of a member body.

    ``kind`` is ``"chunk"`` for a ``[u32 L][tag][payload]`` record and ``"raw"`` for
    bytes that no chain explained.  ``raw`` segments exist so that unknown regions
    survive a round trip untouched instead of being dropped.
    """

    kind: str
    tag: bytes
    payload: bytes

    @property
    def is_chunk(self) -> bool:
        return self.kind == "chunk"

    @property
    def size(self) -> int:
        """Bytes this segment occupies in the member."""
        return RECORD_PREFIX + len(self.payload) if self.is_chunk else len(self.payload)

    def serialize(self) -> bytes:
        if self.is_chunk:
            # The stored length counts the tag, so it is TAG_SIZE + payload.
            return struct.pack("<I", TAG_SIZE + len(self.payload)) + self.tag + self.payload
        return self.payload


@dataclass
class MpacMember:
    """One ``MPAC`` member: header, framed body and optional trailer."""

    index: int
    offset: int          # offset inside the enclosing AFS, 0 for a bare member
    header: bytes        # b"MPAC" + u32 H + (H-4) bytes
    segments: list[MpacSegment] = field(default_factory=list)
    trailer: bytes = b""

    @property
    def header_size(self) -> int:
        return _u32(self.header, LENGTH_SIZE)

    @property
    def body(self) -> bytes:
        return b"".join(s.serialize() for s in self.segments)

    @property
    def size(self) -> int:
        return len(self.header) + sum(s.size for s in self.segments) + len(self.trailer)

    @property
    def tags(self) -> list[bytes]:
        return [s.tag for s in self.segments if s.is_chunk]

    @property
    def chunks(self) -> list[MpacSegment]:
        return [s for s in self.segments if s.is_chunk]

    @property
    def raw_size(self) -> int:
        """Bytes carried verbatim -- the size of what the module does not explain."""
        return sum(s.size for s in self.segments if not s.is_chunk)

    def serialize(self) -> bytes:
        return self.header + self.body + self.trailer


def _chain(blob: bytes, start: int, limit: int) -> tuple[list[MpacSegment], int]:
    """Longest valid chunk chain from ``start``, never reading past ``limit``.

    Stops at the first record whose length is impossible or whose tag is not
    tag-shaped.  Returns the segments and the offset it stopped at.
    """
    segments: list[MpacSegment] = []
    pos = start
    while pos + RECORD_PREFIX <= limit:
        size = _u32(blob, pos)
        tag = blob[pos + TAG_SIZE:pos + RECORD_PREFIX]
        if size < TAG_SIZE or pos + LENGTH_SIZE + size > limit or not is_tag(tag):
            break
        payload_end = pos + LENGTH_SIZE + size
        segments.append(MpacSegment("chunk", bytes(tag), blob[pos + RECORD_PREFIX:payload_end]))
        pos = payload_end
    return segments, pos


def segment_body(blob: bytes, body_start: int, body_end: int) -> list[MpacSegment]:
    """Segment ``blob[body_start:body_end]`` so the segments cover it exactly.

    See the module docstring for the two-pass rule; it is deterministic and was
    validated against all 124 members of ``MRY.AFS``.
    """
    segments, cursor = _chain(blob, body_start, body_end)

    if cursor < body_end:
        # Second pass: the first 4-byte-aligned offset whose chain lands exactly on
        # the body end. Requiring an exact landing is what keeps this from picking a
        # bogus start inside unknown data.
        first = cursor
        if (first - body_start) % LENGTH_SIZE:
            first += (body_start - first) % LENGTH_SIZE
        for candidate in range(first, body_end, LENGTH_SIZE):
            records, end = _chain(blob, candidate, body_end)
            if records and end == body_end:
                if candidate > cursor:
                    segments.append(MpacSegment("raw", b"", blob[cursor:candidate]))
                segments.extend(records)
                cursor = body_end
                break

    if cursor < body_end:
        segments.append(MpacSegment("raw", b"", blob[cursor:body_end]))
    return segments


def parse_member(blob: bytes, index: int = 0, offset: int = 0) -> MpacMember:
    """Parse a single MPAC member. Raises :class:`MpacFormatError` if it is not one."""
    if not is_mpac(blob):
        raise MpacFormatError("not an MPAC member (bad magic)")

    header_size = _u32(blob, LENGTH_SIZE)
    body_start = LENGTH_SIZE + header_size
    if header_size < LENGTH_SIZE or body_start > len(blob):
        raise MpacFormatError(
            f"header size {header_size} puts the body outside the member ({len(blob)} bytes)"
        )

    trailer = TRAILER if blob[-LENGTH_SIZE:] == TRAILER else b""
    body_end = len(blob) - len(trailer)

    return MpacMember(
        index=index,
        offset=offset,
        header=blob[:body_start],
        segments=segment_body(blob, body_start, body_end),
        trailer=trailer,
    )


def parse_members(blob: bytes) -> list[MpacMember]:
    """Parse every MPAC member in ``blob``.

    ``blob`` may be a raw ``MRY.AFS``-style archive, in which case only the members
    that actually carry the ``MPAC`` magic are returned (the zero filler members are
    skipped, not reported as errors), or a single bare member.
    """
    if len(blob) >= 3 and blob[:3] == b"AFS":
        from tools.formats import afs

        try:
            archive = afs.parse(blob)
        except afs.AfsFormatError as exc:  # pragma: no cover - depends on input file
            raise MpacFormatError(str(exc)) from exc

        members: list[MpacMember] = []
        for entry in archive.entries:
            member_blob = blob[entry.offset:entry.end]
            if not is_mpac(member_blob):
                continue
            members.append(parse_member(member_blob, index=entry.index, offset=entry.offset))
        return members

    return [parse_member(blob)]


def parse_file(path: str | os.PathLike) -> list[MpacMember]:
    with open(path, "rb") as fp:
        return parse_members(fp.read())


def verify_roundtrip(path: str | os.PathLike) -> list[str]:
    """decode -> encode -> byte-equal, the Phase 2 exit criterion for a format.

    Empty list means every member round-tripped byte for byte.
    """
    with open(path, "rb") as fp:
        blob = fp.read()

    try:
        members = parse_members(blob)
    except MpacFormatError as exc:
        return [f"{os.path.basename(str(path))}: {exc}"]

    label = os.path.basename(str(path))
    problems: list[str] = []
    for member in members:
        rebuilt = member.serialize()
        original = blob[member.offset:member.offset + member.size] if member.offset or True else b""
        if rebuilt == original:
            continue
        problems.append(
            f"{label}[{member.index}]: round-trip mismatch "
            f"({len(rebuilt)} vs {len(original)} bytes)"
        )
        for i in range(min(len(rebuilt), len(original))):
            if rebuilt[i] != original[i]:
                problems.append(f"  first difference at member offset {i}")
                break
    return problems


def tag_histogram(members: list[MpacMember]) -> collections.Counter:
    """Counter of tag sequences, e.g. ``{('MDL\\xac','TIM2'): 34}``."""
    return collections.Counter(tuple(m.tags) for m in members)


# --------------------------------------------------------------------------- CLI
def _tag_slug(tag: bytes) -> str:
    return "".join(chr(c) if 32 <= c < 127 and c not in (0x2F, 0x5C) else "_" for c in tag)


def _describe(path: str) -> None:
    members = parse_file(path)
    print(f"{path}: {len(members)} MPAC member(s)")
    print("header sizes: %s" % dict(collections.Counter(m.header_size for m in members)))
    print("tag sequences:")
    for tags, count in tag_histogram(members).most_common():
        print("  x%-4d %s" % (count, [t.decode("latin-1") for t in tags]))
    for member in members[:6]:
        parts = []
        for segment in member.segments:
            parts.append(
                "%r(%d)" % (segment.tag.decode("latin-1"), len(segment.payload))
                if segment.is_chunk else f"raw({len(segment.payload)})"
            )
        print("  [%d] @%d size=%d H=%d: %s"
              % (member.index, member.offset, member.size, member.header_size, " ".join(parts)))
    if len(members) > 6:
        print(f"  ... {len(members) - 6} more")


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
            for line in problems[:10]:
                print(f"  {line}")
        else:
            members = parse_file(path)
            chunks = sum(len(m.chunks) for m in members)
            raw = sum(m.raw_size for m in members)
            print(f"ok   {path}: {len(members)} members, {chunks} chunks, "
                  f"{raw}B carried verbatim, all byte-exact")
    return 1 if failed else 0


def _cmd_extract(path: str, out_dir: str) -> int:
    members = parse_file(path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for member in members:
        (out / f"member_{member.index:04d}.bin").write_bytes(member.serialize())
        for n, segment in enumerate(member.segments):
            if not segment.is_chunk:
                continue
            name = f"member_{member.index:04d}_{n:02d}_{_tag_slug(segment.tag)}.bin"
            (out / name).write_bytes(segment.payload)
    print(f"extracted {len(members)} member(s) to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MPAC model package codec (Phase 2)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_info = sub.add_parser("info", help="summarise members, header sizes and tag sequences")
    p_info.add_argument("paths", nargs="+")

    p_verify = sub.add_parser("verify", help="decode -> encode -> byte-equal over all members")
    p_verify.add_argument("paths", nargs="+")

    p_extract = sub.add_parser("extract", help="write each member and each chunk to files")
    p_extract.add_argument("path")
    p_extract.add_argument("--out", default="build/mpac")

    args = parser.parse_args(argv)
    if args.command == "info":
        return _cmd_info(args.paths)
    if args.command == "verify":
        return _cmd_verify(args.paths)
    if args.command == "extract":
        return _cmd_extract(args.path, args.out)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
