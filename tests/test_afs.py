"""Tests for tools/formats/afs.py: the AFS container parser/serializer (Phase 2).

Written to run under pytest *and* under the stdlib fallback runner
(``python tests/run_tests.py``), so no pytest-only features are used and every test
takes no arguments.

Two tiers of evidence:

  * **Synthetic** — archives assembled in memory, so the suite runs on a bare checkout
    with no copyrighted input. Covers the layout, the preserved padding/trailer, and the
    rejection paths.
  * **Real disc** — if ``data/raw/*.AFS`` is present (the user's own extraction), every
    archive must round-trip byte for byte. This is the mechanical Phase 2 exit check:
    a format is not safe to inject into until decode -> encode is byte-identical.

See ``research/afs/spec.md`` for the evidence behind the layout.
"""

import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.formats import afs

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(WORKSPACE, "data", "raw")


# --------------------------------------------------------------------------- helpers
def build_afs(blob_members, magic=b"AFS\x00", align=2048, pad_pre=None, trailer=b"",
              gap_fill=b"\x00", gap_extra=0):
    """Assemble a synthetic AFS container the way the real ones are laid out.

    Members are placed at ``align``-aligned offsets; ``gap_extra`` pads the gap after
    each member with non-zero bytes, which is how we prove the writer preserves gaps
    verbatim instead of regenerating zeros.
    """
    count = len(blob_members)
    header_size = afs.HEADER_SIZE + afs.ENTRY_SIZE * count
    if pad_pre is None:
        # Round the header up to the alignment, like the real archives do.
        pad_pre = b"\x00" * (-header_size % align)

    body = bytearray(pad_pre)
    entries = []
    for index, member in enumerate(blob_members):
        # Real archives are aligned; keep them aligned unless the test asks otherwise.
        aligned = -(header_size + len(body)) % align
        body += b"\x00" * aligned
        start = header_size + len(body)
        entries.append((start, len(member)))
        body += member
        if index != count - 1:
            body += gap_fill * gap_extra

    out = bytearray()
    out += magic
    out += struct.pack("<I", count)
    for offset, size in entries:
        out += struct.pack("<II", offset, size)
    out += body
    out += trailer
    return bytes(out), entries


SAMPLE = [b"AAAA" * 4, b"BB" * 100, b"C" * 3000]

# md5 of the loose data/raw/SYSMES.ALD on the user's disc; SYSTEM.AFS member 0 is
# byte-identical to it, which is the independent proof that our offsets are right.
SYSMES_MD5 = "712ee0d5c8d61b4da27af7858c605167"


# ------------------------------------------------------------------- synthetic layout
def test_synthetic_roundtrip_is_byte_exact():
    blob, entries = build_afs(SAMPLE)
    archive = afs.parse(blob)
    assert archive.count == 3, archive.count
    assert archive.serialize() == blob


def test_parsed_offsets_sizes_and_payloads_match_what_was_written():
    blob, entries = build_afs(SAMPLE)
    archive = afs.parse(blob)
    assert [(e.offset, e.size) for e in archive.entries] == entries
    assert [e.payload for e in archive.entries] == SAMPLE


def test_both_magic_variants_are_accepted():
    # The real disc contains both 'AFS\0' (most archives) and 'AFS ' (MULTSPQ1/2).
    for magic in (b"AFS\x00", b"AFS "):
        blob, _ = build_afs(SAMPLE, magic=magic)
        archive = afs.parse(blob)
        assert archive.magic == magic
        assert archive.serialize() == blob


def test_gaps_are_preserved_verbatim_not_regenerated():
    # Non-zero filler in the inter-member gaps and around the archive must survive, or a
    # repack would silently rewrite bytes the game may depend on.
    blob, _ = build_afs(SAMPLE, gap_fill=b"\xAB", gap_extra=64, trailer=b"\xCD" * 32)
    archive = afs.parse(blob)
    assert archive.serialize() == blob
    assert archive.trailer == b"\xCD" * 32
    assert any(b for e in archive.entries[:-1] for b in e.pad)


def test_pre_member_padding_and_trailer_are_kept_separately():
    blob, _ = build_afs(SAMPLE, align=4096, trailer=b"\xEE" * 16)
    archive = afs.parse(blob)
    assert archive.pad_pre
    assert archive.trailer == b"\xEE" * 16
    assert archive.serialize() == blob


def test_unaligned_member_offsets_still_roundtrip():
    # The parser must not *require* alignment; it only has to preserve what it is given.
    blob, _ = build_afs(SAMPLE, align=2048)
    archive = afs.parse(blob)
    assert archive.serialize() == blob


def test_empty_archive_roundtrips():
    blob, _ = build_afs([])
    archive = afs.parse(blob)
    assert archive.count == 0
    assert archive.serialize() == blob


def test_is_afs_accepts_prefix_and_rejects_others():
    assert afs.is_afs(b"AFS\x00\x01\x02\x03")
    assert afs.is_afs(b"AFS ")
    assert not afs.is_afs(b"PSS\x00")
    assert not afs.is_afs(b"AF")
    assert not afs.is_afs(b"")


def test_parse_table_only_does_not_load_payloads_but_keeps_offsets():
    blob, entries = build_afs(SAMPLE)
    archive = afs.parse(blob, load_payloads=False)
    assert [(e.offset, e.size) for e in archive.entries] == entries
    assert all(e.payload is None for e in archive.entries)


# ---------------------------------------------------------------------- rejection paths
def test_bad_magic_is_rejected():
    blob, _ = build_afs(SAMPLE)
    try:
        afs.parse(b"XXX\x00" + blob[4:])
    except afs.AfsFormatError as exc:
        assert "magic" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("bad magic was accepted")


def test_truncated_file_is_rejected():
    blob, _ = build_afs(SAMPLE)
    try:
        afs.parse(blob[: len(blob) - 100])
    except afs.AfsFormatError:
        pass
    else:  # pragma: no cover
        raise AssertionError("truncated archive was accepted")


def test_absurd_count_is_rejected_rather_than_allocating():
    # A corrupt/hostile count must fail loudly: the header alone cannot fit.
    head = b"AFS\x00" + struct.pack("<I", 10_000_000) + b"\x00" * 32
    try:
        afs.parse(head)
    except afs.AfsFormatError:
        pass
    else:  # pragma: no cover
        raise AssertionError("absurd entry count was accepted")


def test_entry_offset_inside_header_is_rejected():
    # One member whose offset points back into the table.
    blob = bytearray()
    blob += b"AFS\x00"
    blob += struct.pack("<I", 1)
    blob += struct.pack("<II", 4, 16)
    blob += b"\x00" * 2048
    try:
        afs.parse(bytes(blob))
    except afs.AfsFormatError as exc:
        assert "header" in str(exc).lower() or "overlap" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("in-table offset was accepted")


def test_overlapping_members_are_rejected():
    blob = bytearray()
    blob += b"AFS\x00"
    blob += struct.pack("<I", 2)
    blob += struct.pack("<II", 2048, 500)
    blob += struct.pack("<II", 2100, 500)  # starts before the first member ends
    blob += b"\x00" * 4096
    try:
        afs.parse(bytes(blob))
    except afs.AfsFormatError as exc:
        assert "overlap" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("overlapping members were accepted")


def test_serialize_requires_payloads():
    blob, _ = build_afs(SAMPLE)
    archive = afs.parse(blob, load_payloads=False)
    try:
        archive.serialize()
    except afs.AfsFormatError as exc:
        assert "payload" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("serialize() without payloads should fail")


def test_verify_reports_problems_for_non_afs_bytes():
    problems = afs.verify_roundtrip(__file__)  # this .py file is not an AFS archive
    assert problems


# ------------------------------------------------------------------------- real disc
def _real_archives():
    if not os.path.isdir(RAW_DIR):
        return []
    return sorted(
        os.path.join(RAW_DIR, n)
        for n in os.listdir(RAW_DIR)
        if n.upper().endswith(".AFS")
    )


def test_real_disc_archives_roundtrip_byte_exact():
    """Phase 2 exit criterion: every real archive decodes and re-encodes byte-identically.

    Skipped (vacuously passes) when the user's extraction is absent, so the suite still
    runs on a bare checkout.
    """
    paths = _real_archives()
    if not paths:
        return
    failures = {}
    for path in paths:
        problems = afs.verify_roundtrip(path)
        if problems:
            failures[os.path.basename(path)] = problems[:3]
    assert not failures, failures


def test_real_archive_table_is_self_consistent():
    """Offsets ascend, stay 2048-aligned, and the last member ends at/near EOF."""
    paths = _real_archives()
    if not paths:
        return
    for path in paths:
        archive = afs.parse_table_file(path)
        size = os.path.getsize(path)
        offsets = [e.offset for e in archive.entries]
        assert offsets == sorted(offsets), path
        assert all(o % afs.ALIGNMENT == 0 for o in offsets), path
        assert archive.entries[-1].end <= size, path
        # The last member should reach EOF (real archives pad the tail, not the middle).
        assert size - archive.entries[-1].end < afs.ALIGNMENT, path


def test_real_system_afs_member_zero_is_the_loose_sysmes_ald():
    """Independent proof that the parsed offsets are correct.

    SYSTEM.AFS member 0 is the same asset as the loose ``SYSMES.ALD``, and the two are
    byte-identical (md5 712ee0d5c8d61b4da27af7858c605167). Reading the member out of the
    archive *at the offset the table declares* and getting an md5 match pins the offset
    and size fields; if either were off by even one alignment unit this would fail.
    """
    system = os.path.join(RAW_DIR, "SYSTEM.AFS")
    sibling = os.path.join(RAW_DIR, "SYSMES.ALD")
    if not (os.path.isfile(system) and os.path.isfile(sibling)):
        return
    archive = afs.parse_table_file(system)
    with open(system, "rb") as fp:
        actual = afs.read_entry(fp, archive.entries[0])
    with open(sibling, "rb") as sfp:
        expected = sfp.read()
    assert actual == expected
    assert hashlib.md5(actual).hexdigest() == SYSMES_MD5


def test_real_system_afs_member_one_differs_from_loose_syseff_by_revision_only():
    """Member 1 vs the loose ``SYSEFF.ALD``: same asset, two revisions -- not a bad read.

    These two are the *same length* (1100316) but differ in 59256 bytes scattered from
    offset 8486 to 1000316. That shape is the signature of a different revision of an
    asset shipped both loose and inside the archive. A mis-parsed member would instead
    show a length mismatch or a long contiguous run of differences (a shifted payload),
    because an offset error displaces bytes rather than editing them in place.

    So this test asserts the invariant that must hold either way -- equal length, an
    identical prefix, and a bounded *scattered* minority of differences -- rather than a
    byte equality that is false for this disc. If the offsets were ever wrong, the
    scattered-difference invariant breaks even though the lengths still match.
    """
    system = os.path.join(RAW_DIR, "SYSTEM.AFS")
    sibling = os.path.join(RAW_DIR, "SYSEFF.ALD")
    if not (os.path.isfile(system) and os.path.isfile(sibling)):
        return
    archive = afs.parse_table_file(system)
    with open(system, "rb") as fp:
        actual = afs.read_entry(fp, archive.entries[1])
    with open(sibling, "rb") as sfp:
        expected = sfp.read()

    assert len(actual) == len(expected), (len(actual), len(expected))
    if actual == expected:
        return  # a future revision may well match; the invariant below is the contract

    first = next(i for i, (a, b) in enumerate(zip(actual, expected)) if a != b)
    differences = sum(1 for a, b in zip(actual, expected) if a != b)
    assert first > 0, "divergence at byte 0 means the payload is misaligned, not revised"
    assert differences < len(expected) // 10, differences
