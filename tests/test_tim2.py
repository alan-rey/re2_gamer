"""TIM2 texture records: synthetic tier + real-disc tier.

Phase 2 exit criterion for the texture family is decode -> encode -> byte-equal, proven
by ``tools.formats.tim2.verify_roundtrip()`` returning an empty list for every real file.

Synthetic tier: records are assembled in memory, so this half of the suite runs on a bare
checkout with no copyrighted input. It covers the record layout, the verbatim preservation
of padding/trailer bytes, and every rejection path.

Real-disc tier: guarded with ``if not os.path.isdir(RAW_DIR): return`` so it is vacuously
skipped before Phase 1 extraction has run. It proves the byte-exact round-trip and the
geometry invariant that pins the picture-header offset.

Test functions take no arguments, so the same file runs under pytest and the stdlib
runner (``python tests/run_tests.py``).
"""

from __future__ import annotations

import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:  # keeps `tools.*` importable under both runners
    sys.path.insert(0, ROOT)

from tools.formats import tim2  # noqa: E402

RAW_DIR = os.path.join(ROOT, "data", "raw")
MAGIC = tim2.MAGIC
FILL = tim2.FILL


# ------------------------------------------------------------------ synthetic fixtures
def make_prefix(u32: int) -> bytes:
    """The 32-byte record prefix: magic, a u32, then 24 bytes of 'CD' filler."""
    return MAGIC + struct.pack("<I", u32) + FILL * 12


def make_body(
    width: int = 4,
    height: int = 4,
    pict_format: int = 4,
    clut_size: int = 0,
    clut_colors: int = 0,
    image_size: int | None = None,
    header_size: int = tim2.PICTURE_REL,
    version: int = 4,
    num_pictures: int = 1,
    rel: int = tim2.PICTURE_REL,
    clut: bytes = b"",
    image: bytes = b"",
    trailer: bytes = b"",
) -> bytes:
    """A TIM2 file header with one picture header at ``rel`` (128 by default)."""
    if image_size is None:
        image_size = width * height
    total = header_size + clut_size + image_size
    buf = bytearray(rel + total)
    buf[0:4] = MAGIC
    buf[4] = version
    buf[5] = 1  # format (unmodelled, kept for realism)
    struct.pack_into("<H", buf, 6, num_pictures)
    struct.pack_into("<III", buf, rel, total, clut_size, image_size)
    struct.pack_into("<HH", buf, rel + 12, header_size, clut_colors)
    buf[rel + 16] = pict_format
    buf[rel + 17] = 1  # mipmap_count
    buf[rel + 18] = 3  # clut_type
    buf[rel + 19] = 5  # image_type
    struct.pack_into("<HH", buf, rel + 20, width, height)
    if clut:
        assert len(clut) <= clut_size
        buf[rel + header_size: rel + header_size + len(clut)] = clut
    if image:
        assert len(image) <= image_size
        start = rel + header_size + clut_size
        buf[start:start + len(image)] = image
    return bytes(buf) + trailer


def make_record(**kwargs) -> bytes:
    """A prefix + body, with the prefix u32 set the way the real disc sets it.

    On this disc the prefix u32 equals ``rel + total_size`` for every record measured,
    so the fixture mirrors that rather than inventing a value.
    """
    body = make_body(**kwargs)
    picture = tim2.locate_picture(body)
    u32 = (picture.rel + picture.total_size) if picture else 0
    return make_prefix(u32) + body


# ------------------------------------------------------------------ prefix discovery
def test_is_prefix_requires_the_24_byte_cd_filler():
    assert tim2.is_prefix(make_prefix(0))
    # A bare magic is NOT a prefix: the file header 32 bytes later also starts with TIM2,
    # which is exactly why the filler is what anchors a record.
    assert not tim2.is_prefix(MAGIC + b"\x00" * 28)
    assert not tim2.is_prefix(MAGIC)
    assert not tim2.is_prefix(b"")


def test_find_prefixes_skips_bare_magic_and_finds_every_record():
    loose = MAGIC + b"\x00" * 64
    blob = loose + make_prefix(0) + b"body-junk" + make_prefix(7)
    assert tim2.find_prefixes(blob) == [len(loose), len(loose) + 32 + 9]


def test_parse_rejects_a_blob_with_no_prefix():
    for blob in (b"", b"not a texture at all", MAGIC + b"\x00" * 64):
        try:
            tim2.parse(blob)
        except tim2.Tim2FormatError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"accepted a non-TIM2 blob: {blob[:16]!r}")


# ------------------------------------------------------------------ layout + round-trip
def test_parse_splits_on_prefixes_and_keeps_bytes_before_the_first_one():
    pad = b"\xA5" * 100
    blob = pad + make_record(width=8, height=8) + make_record(width=16, height=16)
    archive = tim2.parse(blob)

    assert archive.count == 2
    assert archive.pad_pre == pad
    assert [r.index for r in archive.records] == [0, 1]
    assert archive.records[0].offset == len(pad)
    assert archive.records[0].file_magic == MAGIC
    assert archive.records[0].version == 4
    assert archive.records[0].num_pictures == 1
    assert archive.records[0].picture.width == 8
    assert archive.records[1].picture.width == 16
    assert archive.total_size == len(blob)


def test_roundtrip_is_byte_exact_including_pad_pre_and_trailer():
    trailer = bytes(range(64))
    blob = b"\x11" * 32 + make_record() + trailer
    archive = tim2.parse(blob)

    assert archive.serialize() == blob
    assert archive.records[0].body.endswith(trailer), "trailer must belong to the last body"


def test_picture_header_is_located_at_the_known_offset():
    picture = tim2.locate_picture(make_body(width=64, height=32))

    assert picture is not None
    assert picture.rel == tim2.PICTURE_REL == 128
    assert picture.total_size == picture.header_size + picture.clut_size + picture.image_size
    assert (picture.width, picture.height) == (64, 32)
    assert picture.bpp() == 1.0


def test_picture_offsets_and_payload_slices():
    clut = bytes(range(16))
    image = bytes((i * 7) % 256 for i in range(64))
    body = make_body(width=8, height=8, clut_size=16, clut_colors=8,
                     image_size=64, clut=clut, image=image)
    record = tim2.parse(make_prefix(0) + body).records[0]
    picture = record.picture

    assert picture.clut_offset == picture.rel + picture.header_size
    assert picture.image_offset == picture.clut_offset + picture.clut_size
    assert picture.extended_size == picture.header_size - tim2.PICTURE_CORE_SIZE
    assert record.clut_bytes() == clut
    assert record.image_bytes() == image


def test_locate_picture_falls_back_to_a_scan_when_128_is_empty():
    """The known offset is preferred, but a header at another offset is still found."""
    buf = bytearray(256)
    buf[0:4] = MAGIC
    rel = 64
    struct.pack_into("<III", buf, rel, 80, 0, 16)  # total, clut, image
    struct.pack_into("<HH", buf, rel + 12, 64, 0)  # header_size, clut_colors
    buf[rel + 16] = 4
    buf[rel + 17] = 1
    buf[rel + 18] = 3
    buf[rel + 19] = 5
    struct.pack_into("<HH", buf, rel + 20, 4, 4)

    picture = tim2.locate_picture(bytes(buf))
    assert picture is not None and picture.rel == rel


# ------------------------------------------------------------------ rejection paths
def test_problems_flags_a_body_that_is_not_tim2():
    record = tim2.parse(make_prefix(0) + b"XXXX" + b"\x00" * 300).records[0]

    assert record.picture is None
    problems = record.problems()
    assert any("magic" in m for m in problems)
    assert any("picture" in m for m in problems)


def test_broken_size_identity_is_rejected_rather_than_guessed():
    """An absurd total_size must not be 'repaired' into a picture header.

    0xDEADBEEF is chosen so that no scan window can satisfy the fit check: any window
    covering those bytes fails the length test, and every other window is zeros, whose
    header_size of 0 fails the plausibility band.
    """
    buf = bytearray(256)
    buf[0:4] = MAGIC
    struct.pack_into("<I", buf, tim2.PICTURE_REL, 0xDEADBEEF)

    record = tim2.parse(make_prefix(0) + bytes(buf)).records[0]
    assert record.picture is None
    assert any("no picture header" in m for m in record.problems())


def test_standard_table_relations_are_reported_but_never_enforced():
    """The disc violates standard TIM2 tables; the codec must report, not 'fix'.

    pict_format 5 implies 2.0 bytes/pixel in the standard table, but this record stores
    16 bytes for 64 pixels (0.25). A parser that recomputed image_size from the table
    would corrupt real data, so the structural rules stay satisfied and the standard-table
    disagreement is surfaced separately.
    """
    blob = make_prefix(0) + make_body(width=8, height=8, pict_format=5, image_size=16)
    record = tim2.parse(blob).records[0]

    assert record.picture is not None
    assert record.problems() == []
    assert record.picture_problems()


def test_verify_roundtrip_reports_problems_for_a_non_tim2_file():
    # This test file is not a texture stream, so the verifier must say so, not pass.
    assert tim2.verify_roundtrip(__file__)


# ------------------------------------------------------------------ CLI
def test_cli_verify_and_extract_on_a_synthetic_record():
    out = os.path.join(ROOT, "build", "test_tim2")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "synthetic.tim2")
    clut = bytes(range(16))
    image = bytes((i * 3) % 256 for i in range(64))
    with open(path, "wb") as fp:
        fp.write(make_record(width=8, height=8, clut_size=16, clut_colors=8,
                             image_size=64, clut=clut, image=image))

    try:
        assert tim2.main(["verify", path]) == 0
        assert tim2.main(["verify", __file__]) == 1

        payloads = os.path.join(out, "payloads")
        assert tim2.main(["extract", path, "--out", payloads]) == 0
        assert sorted(os.listdir(payloads)) == ["blob_0000.clut", "blob_0000.raw"]
        with open(os.path.join(payloads, "blob_0000.raw"), "rb") as fp:
            assert fp.read() == image
        with open(os.path.join(payloads, "blob_0000.clut"), "rb") as fp:
            assert fp.read() == clut
    finally:
        shutil.rmtree(out, ignore_errors=True)


# ------------------------------------------------------------------ real disc
TIM2_ORDER = ("SYSTEM.AFS", "ITEM1.AFS", "ITEM2.AFS", "SYSEFF.ALD")


def _raw_texture_files():
    """Real files known to carry texture records, in a fixed order (empty if absent)."""
    if not os.path.isdir(RAW_DIR):
        return []
    return [p for p in (os.path.join(RAW_DIR, n) for n in TIM2_ORDER) if os.path.isfile(p)]


def _all_raw_files():
    """Every loose archive-ish file in data/raw (AFS containers and .ALD bundles)."""
    if not os.path.isdir(RAW_DIR):
        return []
    return sorted(
        os.path.join(RAW_DIR, n)
        for n in os.listdir(RAW_DIR)
        if n.upper().endswith((".AFS", ".ALD"))
    )


def test_real_texture_files_roundtrip_byte_exact():
    """Phase 2 exit criterion for this family: decode -> encode -> byte-equal."""
    paths = _raw_texture_files()
    if not paths:
        return
    failures = {}
    for path in paths:
        problems = tim2.verify_roundtrip(path)
        if problems:
            failures[os.path.basename(path)] = problems[:3]
    assert not failures, failures


def test_every_raw_file_with_a_prefix_roundtrips_byte_exact():
    """Same criterion applied to the whole extraction, read once per file."""
    paths = _all_raw_files()
    if not paths:
        return
    for path in paths:
        with open(path, "rb") as fp:
            blob = fp.read()
        if not tim2.find_prefixes(blob):
            continue  # no texture records in this file; nothing to prove here
        rebuilt = tim2.parse(blob).serialize()
        assert rebuilt == blob, path


def test_real_records_are_structurally_sound_and_share_one_geometry():
    """The measured invariants, asserted mechanically against the user's own disc.

    1. every record locates a picture header, at rel 128, satisfying
       total_size == header_size + clut_size + image_size;
    2. every prefix u32 equals rel + total_size -- this is what pins offset 128 (a
       one-byte-shifted read would produce 127 + total_size instead);
    3. no record reports a structural problem.
    """
    paths = _raw_texture_files()
    if not paths:
        return

    total = 0
    for path in paths:
        archive = tim2.parse_file(path)
        assert archive.count > 0, path
        assert archive.records[0].offset == len(archive.pad_pre), path
        for record in archive.records:
            total += 1
            assert record.problems() == [], (path, record.index, record.problems())
            picture = record.picture
            assert picture is not None, (path, record.index)
            assert picture.rel == tim2.PICTURE_REL, (path, record.index, picture.rel)
            assert picture.total_size == (
                picture.header_size + picture.clut_size + picture.image_size
            ), (path, record.index)
            assert record.prefix_u32 == picture.rel + picture.total_size, (
                path, record.index, record.prefix_u32, picture.total_size)
            assert record.version == 4, (path, record.index, record.version)
            assert record.num_pictures == 1, (path, record.index, record.num_pictures)
            assert 1 <= picture.width <= 4096 and 1 <= picture.height <= 4096

    assert total >= 100  # sanity: the corpus really is non-trivial
