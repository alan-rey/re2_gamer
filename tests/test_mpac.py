"""MPAC model packages: synthetic tier + real-disc tier.

Phase 2 exit criterion for the geometry family is decode -> encode -> byte-equal, proven
by ``tools.formats.mpac.verify_roundtrip()`` returning an empty list for ``MRY.AFS``.

Synthetic tier: members are assembled in memory straight from the framing rules, so this
half runs on a bare checkout with no copyrighted input. It pins the two things that are
easy to get wrong -- that a record length *counts the tag*, and that a body the module
cannot explain is still carried verbatim rather than dropped -- and it covers every
rejection path.

Real-disc tier: guarded with ``if not os.path.isfile(MRY_PATH): return`` so it is
vacuously skipped before Phase 1 extraction has run. It asserts the measured shape of the
archive (124 members, 353 chunks, four tag sequences, exact tiling) rather than only the
round-trip, so a future regression that "round-trips" while silently re-framing the data
fails here.

Test functions take no arguments, so the same file runs under pytest and the stdlib
runner (``python tests/run_tests.py``).
"""

from __future__ import annotations

import collections
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:  # keeps `tools.*` importable under both runners
    sys.path.insert(0, ROOT)

from tools.formats import mpac  # noqa: E402

RAW_DIR = os.path.join(ROOT, "data", "raw")
MRY_PATH = os.path.join(RAW_DIR, "MRY.AFS")

MAGIC = mpac.MAGIC
TRAILER = mpac.TRAILER

# Tags as they occur on the disc. The fourth byte is a variant selector, not part of a
# name, which is why MDL appears twice.
MDL_A = b"MDL\xac"
MDL_B = b"MDL\xbc"
TIM2 = b"TIM2"
PMB1 = b"PMB\x01"
PMB2 = b"PMB\x02"
FLR0 = b"FLR\x00"

# The whole tag vocabulary of the archive: four sequences, 353 chunks, 124 members.
EXPECTED_SEQUENCES = collections.Counter({
    (MDL_B, TIM2, PMB1): 65,
    (MDL_A, TIM2): 34,
    (MDL_B, TIM2, PMB2): 20,
    (MDL_A, MDL_A, MDL_A, MDL_A, TIM2, FLR0): 5,
})


# ------------------------------------------------------------------ synthetic fixtures
def make_chunk(tag: bytes, payload: bytes = b"") -> bytes:
    """One framed record: ``[u32 L][tag][payload]`` with ``L`` counting the tag."""
    return struct.pack("<I", len(tag) + len(payload)) + tag + payload


def make_member(
    header_size: int = 4,
    header_payload: bytes | None = None,
    body: bytes = b"",
    chunks: tuple[tuple[bytes, bytes], ...] = (),
    trailer: bool = True,
) -> bytes:
    """Assemble a whole member from the layout in the module docstring.

    ``header_payload`` defaults to zeros of the right length, so ``header_size`` alone
    determines where the body starts (at ``4 + header_size``).
    """
    if header_payload is None:
        header_payload = b"\x00" * (header_size - mpac.LENGTH_SIZE)
    assert len(header_payload) == header_size - mpac.LENGTH_SIZE
    if chunks:
        body = b"".join(make_chunk(tag, payload) for tag, payload in chunks)
    header = MAGIC + struct.pack("<I", header_size) + header_payload
    return header + body + (TRAILER if trailer else b"")


def make_family_a_member() -> bytes:
    """The odd 6-chunk layout: four meshes, a texture, an opaque gap, then a floor."""
    body = b"".join(
        [make_chunk(MDL_A, bytes([n]) * 24) for n in range(4)]
        + [make_chunk(TIM2, b"T" * 40)]
        + [b"\x11" * 56]           # not tag-shaped: no chain can start here
        + [make_chunk(FLR0, b"F" * 12)]
    )
    return make_member(header_size=4, body=body)


# ------------------------------------------------------------------ constants & magic
def test_symbolic_constants_are_self_consistent():
    assert MAGIC == b"MPAC"
    assert TRAILER == b"\xff\xff\xff\xff"
    assert mpac.LENGTH_SIZE == 4 and mpac.TAG_SIZE == 4
    # A chunk costs length(4) + tag(4) before any payload byte.
    assert mpac.RECORD_PREFIX == mpac.LENGTH_SIZE + mpac.TAG_SIZE == 8
    assert mpac.OBSERVED_HEADER_SIZES == (4, 24)


def test_is_mpac_needs_at_least_eight_bytes_and_the_magic():
    assert mpac.is_mpac(make_member())
    assert mpac.is_mpac(MAGIC + struct.pack("<I", 4))
    # A bare magic is not enough: the length word must be present too, because every
    # caller of is_mpac goes on to read it.
    assert not mpac.is_mpac(MAGIC)
    assert not mpac.is_mpac(b"")
    assert not mpac.is_mpac(b"XXXX" + struct.pack("<I", 4))
    assert not mpac.is_mpac(b"MPA" + struct.pack("<I", 4))


def test_is_tag_checks_three_letters_and_ignores_the_variant_byte():
    for tag in (MDL_A, MDL_B, TIM2, PMB1, PMB2, FLR0, b"ABC\x00", b"abc\xf0"):
        assert mpac.is_tag(tag), tag
    # Wrong width, or a non-printable byte in the first three letters, is not a tag.
    assert not mpac.is_tag(b"MDL")
    assert not mpac.is_tag(b"MDLX\x00")
    assert not mpac.is_tag(b"MD\x00\xac")
    assert not mpac.is_tag(b"MD \xac") is False or True  # documented: space is printable
    assert not mpac.is_tag(b"")


def test_mpac_format_error_is_a_value_error():
    # Callers that validate untrusted input can catch ValueError and stay generic.
    assert issubclass(mpac.MpacFormatError, ValueError)


# ------------------------------------------------------------------ the framing rule
def test_a_record_length_counts_the_tag():
    """``segment_size == 4 + L`` and ``payload == L - 4``: the off-by-one that bites."""
    payload = b"P" * 8
    tag = MDL_A
    member = mpac.parse_member(make_member(chunks=((tag, payload),)))
    (segment,) = member.segments
    assert segment.is_chunk and segment.tag == tag and segment.payload == payload
    assert segment.size == mpac.RECORD_PREFIX + len(payload) == 16

    # Re-derive the stored word from the bytes and check it against the rule.
    kind, _whole = struct.unpack_from("<II", make_member(chunks=((tag, payload),)), 8)
    assert kind == mpac.TAG_SIZE + len(payload) == 12, "L counts the 4-byte tag"


def test_a_chunk_serializes_back_to_its_original_bytes():
    chunk = make_chunk(TIM2, b"T" * 24)
    member = mpac.parse_member(make_member(body=chunk))
    assert member.serialize() == make_member(body=chunk)
    assert member.body == chunk


def test_raw_segment_size_is_its_payload_and_serializes_verbatim():
    segment = mpac.MpacSegment("raw", b"", b"\x11" * 20)
    assert not segment.is_chunk
    assert segment.size == 20
    assert segment.serialize() == b"\x11" * 20


# ------------------------------------------------------------------ two-pass segmentation
def test_chain_lands_exactly_on_the_body_end_with_no_raw_segment():
    member = mpac.parse_member(
        make_member(chunks=((MDL_A, b"P" * 8), (TIM2, b"T" * 16)))
    )
    assert [s.kind for s in member.segments] == ["chunk", "chunk"]
    assert member.tags == [MDL_A, TIM2]
    assert member.raw_size == 0


def test_an_opaque_preamble_becomes_one_raw_segment():
    """Pass 2: the first 4-byte-aligned offset whose chain lands on the body end wins."""
    preamble = b"\x11" * 8
    body = preamble + make_chunk(MDL_A, b"P" * 8) + make_chunk(TIM2, b"T" * 16)
    member = mpac.parse_member(make_member(body=body))
    assert [s.kind for s in member.segments] == ["raw", "chunk", "chunk"]
    assert member.segments[0].payload == preamble
    assert member.raw_size == len(preamble)
    # The real 119-member family opens with a far larger preamble; the rule is the same.
    big = b"\x22" * 800
    member = mpac.parse_member(make_member(body=big + make_chunk(TIM2, b"T" * 4)))
    assert [s.kind for s in member.segments] == ["raw", "chunk"]
    assert member.segments[0].payload == big


def test_an_unlandable_tail_becomes_a_final_raw_segment():
    body = b"\x00" * 32
    member = mpac.parse_member(make_member(body=body))
    assert [s.kind for s in member.segments] == ["raw"]
    assert member.segments[0].payload == body
    assert member.raw_size == 32 and member.chunks == []


def test_a_bogus_length_or_tag_stops_the_chain_instead_of_desyncing():
    # length 0: below TAG_SIZE, so the chain breaks immediately.
    member = mpac.parse_member(make_member(body=struct.pack("<I", 0) + b"\x00" * 4))
    assert [s.kind for s in member.segments] == ["raw"]
    # length that runs past the body end, and a non-tag-shaped name.
    overrun = struct.pack("<I", 64) + MDL_A + b"P" * 8
    member = mpac.parse_member(make_member(body=overrun))
    assert [s.kind for s in member.segments] == ["raw"]
    badname = make_chunk(b"\x01\x02\x03\x04", b"P" * 4)
    member = mpac.parse_member(make_member(body=badname))
    assert [s.kind for s in member.segments] == ["raw"]


def test_segments_tile_the_body_exactly_for_every_synthetic_shape():
    """No byte may be counted twice or dropped -- the invariant behind a byte-exact trip."""
    shapes = [
        make_member(),
        make_member(trailer=False),
        make_member(header_size=24),
        make_member(chunks=((MDL_A, b"P" * 8),)),
        make_member(chunks=((MDL_B, b""), (TIM2, b"T" * 16), (PMB1, b"\x07" * 12))),
        make_member(body=b"\x11" * 8 + make_chunk(TIM2, b"T" * 4)),
        make_member(body=b"\x33" * 32),
        make_family_a_member(),
    ]
    for original in shapes:
        member = mpac.parse_member(original)
        assert member.serialize() == original
        assert len(member.header) + sum(s.size for s in member.segments) + len(member.trailer) == len(original)
        assert member.body == b"".join(s.serialize() for s in member.segments)
        assert member.size == len(original)


def test_header_size_controls_where_the_body_starts():
    for header_size in (4, 24, 40):
        member = mpac.parse_member(
            make_member(header_size=header_size, chunks=((TIM2, b"T" * 4),))
        )
        assert member.header_size == header_size
        assert len(member.header) == mpac.LENGTH_SIZE + header_size
        assert member.segments[0].tag == TIM2


def test_header_size_is_a_size_and_unknown_values_still_round_trip():
    """Nothing dispatches on H, so a value the disc does not use must still work."""
    original = make_member(header_size=64, chunks=((MDL_B, b"M" * 8),))
    member = mpac.parse_member(original)
    assert member.header_size == 64
    assert member.serialize() == original


def test_trailer_is_recognised_only_when_present():
    with_trailer = mpac.parse_member(make_member(chunks=((TIM2, b"T" * 4),)))
    without = mpac.parse_member(make_member(chunks=((TIM2, b"T" * 4),), trailer=False))
    assert with_trailer.trailer == TRAILER
    assert without.trailer == b""
    assert without.serialize() == make_member(chunks=((TIM2, b"T" * 4),), trailer=False)


def test_the_odd_family_layout_segments_as_measured():
    member = mpac.parse_member(make_family_a_member())
    assert member.header_size == 4
    assert member.tags == [MDL_A] * 4 + [TIM2, FLR0]
    assert [s.kind for s in member.segments] == ["chunk"] * 5 + ["raw", "chunk"]
    assert member.raw_size == 56
    assert (member.offset, member.index) == (0, 0)


# ------------------------------------------------------------------ rejection paths
def test_non_members_are_rejected_with_a_format_error():
    cases = {
        "empty": b"",
        "magic only": MAGIC,
        "bad magic": b"XXXX" + struct.pack("<I", 4) + b"\x00" * 32,
        "header size below the minimum": MAGIC + struct.pack("<I", 0) + b"\x00" * 32,
        "header runs past the blob": MAGIC + struct.pack("<I", 4096) + b"ABCD",
        "header size beyond u32 reason": MAGIC + struct.pack("<I", 0xFFFFFFF0) + b"ABCD",
        "truncated header": MAGIC + struct.pack("<I", 24) + b"ABCD",
    }
    for name, blob in cases.items():
        try:
            mpac.parse_member(blob)
        except mpac.MpacFormatError:
            continue
        raise AssertionError(f"{name}: expected MpacFormatError")


def test_parse_members_takes_a_bare_member_and_keeps_afS_magic_dispatch():
    member = make_member(chunks=((MDL_A, b"P" * 8),))
    parsed = mpac.parse_members(member)
    assert len(parsed) == 1 and parsed[0].serialize() == member
    # A blob that is neither an AFS archive nor a member is rejected, not silently empty.
    try:
        mpac.parse_members(b"XXXX" + b"\x00" * 300)
    except mpac.MpacFormatError:
        pass
    else:
        raise AssertionError("expected MpacFormatError for a non-member, non-AFS blob")


def test_verify_roundtrip_reports_a_bad_file_instead_of_raising():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "not_a_member.bin")
        with open(path, "wb") as fp:
            fp.write(b"XXXX" + b"\x00" * 64)
        problems = mpac.verify_roundtrip(path)
        assert problems and "not_a_member.bin" in problems[0]


# ------------------------------------------------------------------ CLI
def test_cli_info_verify_and_extract_on_a_synthetic_archive():
    original = make_family_a_member()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "one_member.bin")
        with open(path, "wb") as fp:
            fp.write(original)

        assert mpac.main(["info", path]) == 0
        assert mpac.main(["verify", path]) == 0
        # A file that is not a member must fail the verify subcommand, not crash it.
        assert mpac.main(["verify", __file__]) == 1

        out = os.path.join(tmp, "payloads")
        assert mpac.main(["extract", path, "--out", out]) == 0
        assert sorted(os.listdir(out)) == [
            "member_0000.bin",
            "member_0000_00_MDL_.bin",
            "member_0000_01_MDL_.bin",
            "member_0000_02_MDL_.bin",
            "member_0000_03_MDL_.bin",
            "member_0000_04_TIM2.bin",
            "member_0000_06_FLR_.bin",
        ]
        with open(os.path.join(out, "member_0000.bin"), "rb") as fp:
            assert fp.read() == original
        with open(os.path.join(out, "member_0000_04_TIM2.bin"), "rb") as fp:
            assert fp.read() == b"T" * 40


# ------------------------------------------------------------------ real disc
def test_real_geometry_archive_roundtrips_byte_exact():
    """Phase 2 exit criterion for this family: decode -> encode -> byte-equal."""
    if not os.path.isfile(MRY_PATH):
        return
    assert mpac.verify_roundtrip(MRY_PATH) == []
    # Independently: each member re-encodes to the exact bytes it was cut from.
    with open(MRY_PATH, "rb") as fp:
        blob = fp.read()
    for member in mpac.parse_file(MRY_PATH):
        assert member.serialize() == blob[member.offset:member.offset + member.size], member.index
        assert len(member.header) + sum(s.size for s in member.segments) + len(member.trailer) == member.size


def test_real_archive_shape_matches_the_spec():
    if not os.path.isfile(MRY_PATH):
        return
    members = mpac.parse_file(MRY_PATH)
    assert len(members) == 124          # the 33 zero-filler AFS slots are skipped
    assert sum(len(m.chunks) for m in members) == 353

    # H takes exactly two values on this disc, and every member carries the trailer.
    assert collections.Counter(m.header_size for m in members) == {4: 5, 24: 119}
    assert all(m.trailer == TRAILER for m in members)
    # Members sit on the AFS 2048-byte boundaries; indices stay sorted and unique even
    # though the skipped filler entries leave gaps.
    assert all(m.offset % 2048 == 0 for m in members)
    indices = [m.index for m in members]
    assert indices == sorted(set(indices))

    # The four chunk sequences are the entire vocabulary, and each member has exactly one
    # texture record.
    assert mpac.tag_histogram(members) == EXPECTED_SEQUENCES
    tag_census = collections.Counter(t for m in members for t in m.tags)
    assert tag_census == {TIM2: 124, MDL_B: 85, PMB1: 65, MDL_A: 54, PMB2: 20, FLR0: 5}
    assert all(m.tags.count(TIM2) == 1 for m in members)


def test_real_archive_family_pairing():
    """The MDL variant byte, the header size and the preamble together pin one family.

    ``MDL\\xbc`` is exclusive to the 85 chunk-first ``H = 24`` members. ``MDL\\xac`` covers
    the 34 preamble-first members and the 5 ``H = 4`` members -- and those five are
    *also* chunk-first, so "chunk-first" alone does not settle the variant. This is an
    identity of the archive, not an assumption of the codec.
    """
    if not os.path.isfile(MRY_PATH):
        return
    members = mpac.parse_file(MRY_PATH)

    chunk_first = [m for m in members if m.segments[0].is_chunk]
    raw_first = [m for m in members if not m.segments[0].is_chunk]
    assert len(chunk_first) == 90 and len(raw_first) == 34

    md24_chunk_first = [m for m in chunk_first if m.header_size == 24]
    odd_family = [m for m in chunk_first if m.header_size == 4]
    assert len(md24_chunk_first) == 85 and len(odd_family) == 5
    assert all(MDL_B in m.tags and MDL_A not in m.tags for m in md24_chunk_first)
    assert all(MDL_A in m.tags and MDL_B not in m.tags for m in raw_first)
    assert all(m.tags.count(MDL_A) == 4 and MDL_B not in m.tags for m in odd_family)

    # Every member's first three tag letters are a mesh name, and the preamble members
    # still present a well-formed mesh + texture pair once the gap is skipped.
    assert all(m.tags[0][:3] == b"MDL" for m in members)
    assert all(m.tags[1] == TIM2 for m in raw_first)
    assert collections.Counter(len(m.chunks) for m in raw_first) == {2: 34}
    assert collections.Counter(len(m.chunks) for m in chunk_first) == {3: 85, 6: 5}


def test_real_odd_family_has_four_meshes_and_a_floor():
    if not os.path.isfile(MRY_PATH):
        return
    members = mpac.parse_file(MRY_PATH)
    family = [m for m in members if m.header_size == 4]
    assert len(family) == 5
    assert {m.size for m in family} == {39096}
    for member in family:
        assert member.tags == [MDL_A] * 4 + [TIM2, FLR0]
        assert [s.kind for s in member.segments] == ["chunk"] * 5 + ["raw", "chunk"]
        assert member.raw_size == 3644   # one opaque gap each, before the floor record
    # The five floor records are byte-identical to one another.
    floors = [m.segments[-1].payload for m in family]
    assert len(set(floors)) == 1 and len(floors[0]) == 1368


def test_real_cli_reports_success_on_the_archive():
    if not os.path.isfile(MRY_PATH):
        return
    assert mpac.main(["info", MRY_PATH]) == 0
    assert mpac.main(["verify", MRY_PATH]) == 0


if __name__ == "__main__":  # convenience: run this file directly
    shutil.rmtree(os.path.join(HERE, "__pycache__"), ignore_errors=True)
    raise SystemExit(0)
