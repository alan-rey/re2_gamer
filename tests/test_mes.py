"""Tests for tools/formats/mes.py: the PS2 message-bank (.ALD) codec (Phase 2).

Written to run under pytest *and* under the stdlib fallback runner
(``python tests/run_tests.py``), so no pytest-only features are used and every test
takes no arguments.

Two tiers of evidence:

  * **Synthetic** — banks assembled in memory, so the suite runs on a bare checkout
    with no copyrighted input. Covers the section layout, the odd-length "tail" byte,
    preserved trailing gap bytes, and the rejection paths.
  * **Real disc** — if ``data/raw/SYSMES.ALD`` is present (the user's own extraction),
    it must round-trip byte for byte. This is the mechanical Phase 2 exit check:
    decode -> encode -> byte-equal.

Fixtures are built with ``store_from_text``, not raw ASCII: the disc stores a visible
unit ``c`` in ``0x20..0x5f`` as ``c - 0x20`` (a space is ``0x0000``), so a fixture built
from ASCII would not describe this format at all.

The glyph rule is also pinned here by measurement rather than assertion: the tests
require the ``threshold`` shift rule to *measurably* beat ``first`` and ``none`` on the
real bank, which is how the rule was discovered in the first place.

See ``research/mes/spec.md`` for the evidence behind the layout.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.formats import mes

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DIR = os.path.join(WORKSPACE, "data", "raw")

# Measured on the user's disc: data/raw/SYSMES.ALD
SYSMES_SECTIONS = 2
SYSMES_MESSAGES = 677
SYSMES_SIZE = 89088

# The visible band: units the disc is allowed to store shifted.
VISIBLE_BAND = range(0x20, 0x60)


# --------------------------------------------------------------------------- helpers
def p32(value):
    return struct.pack("<I", value)


def build_section(bodies):
    """Assemble one message section the way the real banks are laid out.

    Layout: ``[u32 size][u32 count][count x u32 rel_offset][body]`` where ``size`` is the
    whole section and every ``rel_offset`` is measured from the *count* field (i.e. the
    byte after ``size``), not from the start of the section. That origin is the single
    most error-prone detail of the format, so the fixture encodes it literally.
    """
    count = len(bodies)
    offsets = []
    acc = 0
    for body in bodies:
        offsets.append(4 + 4 * count + acc)
        acc += len(body)
    size = 4 + 4 * count + acc
    return p32(size) + p32(count) + b"".join(p32(o) for o in offsets) + b"".join(bodies)


def store_from_text(text):
    """Encode visible text into the bytes the *disc* actually stores.

    The disc is not ASCII: a visible unit in ``0x20..0x5f`` is stored ``0x20`` lower, so
    a space is ``0x0000`` and ``'N'`` is ``0x2e``. ``mes.encode_units`` is the canonical
    encoder for that, and routing the fixtures through it is what keeps them faithful to
    the format -- a fixture built from raw ASCII stores a space as ``0x20``, which the
    codec then decodes back to ``'@'``.
    """
    units = mes.encode_units(mes.text_to_units(text))
    return mes.units_to_bytes(units + [mes.TERMINATOR])


def parse(blob):
    return mes.Mes("<mem>", blob)


def _real_banks():
    """Every data/raw/*.ALD that actually parses as a message bank."""
    if not os.path.isdir(RAW_DIR):
        return []
    found = []
    for name in sorted(os.listdir(RAW_DIR)):
        if not name.upper().endswith(".ALD"):
            continue
        path = os.path.join(RAW_DIR, name)
        with open(path, "rb") as fp:
            data = fp.read()
        if mes.find_sections(data):
            found.append(path)
    return found


def _sysmes():
    """The real SYSMES.ALD as a parsed bank, or None on a bare checkout."""
    path = os.path.join(RAW_DIR, "SYSMES.ALD")
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fp:
        return mes.Mes(path, fp.read())


# ------------------------------------------------------------------- synthetic layout
def test_synthetic_single_section_roundtrips_byte_exactly():
    blob = build_section([store_from_text("HI"), store_from_text("NOT USABLE")])
    bank = parse(blob)
    assert len(bank.sections) == 1, len(bank.sections)
    assert len(bank.messages) == 2, len(bank.messages)
    assert bank.rebuild() == blob


def test_offsets_are_relative_to_the_count_field_not_the_section_start():
    # The count field sits 4 bytes into the section. If the origin were the section
    # start instead, every offset would be 4 too high and the first text byte would be
    # read as garbage -- this assertion pins the origin.
    blob = build_section([store_from_text("HI"), store_from_text("NOT USABLE")])
    section = parse(blob).sections[0]
    assert section.count == 2
    assert section.offsets[0] == 4 + 4 * 2
    # The first text byte is where the table says, measured from the count field.
    assert section.text_start == section.start + 4 + section.offsets[0]


def test_decoded_text_matches_what_was_encoded():
    blob = build_section([store_from_text("HI"), store_from_text("NOT USABLE")])
    bank = parse(blob)
    assert bank.texts() == ["HI", "NOT USABLE"]


def test_two_sections_are_both_found_and_roundtrip():
    blob = (build_section([store_from_text("FIRST")])
            + build_section([store_from_text("SECOND"), store_from_text("THIRD")]))
    bank = parse(blob)
    assert len(bank.sections) == 2, len(bank.sections)
    assert bank.texts() == ["FIRST", "SECOND", "THIRD"]
    assert bank.rebuild() == blob


def test_trailing_gap_bytes_are_preserved_verbatim():
    # The real SYSMES.ALD ends with a 4-byte gap after the last section (gaps [0, 4]).
    # Regenerating that gap as zeros would silently rewrite bytes the game may read.
    junk = b"\x11\x22\x33\x44"
    blob = build_section([store_from_text("HI")]) + junk
    bank = parse(blob)
    assert bank.gaps[-1] == junk
    assert bank.rebuild() == blob


def test_odd_length_body_keeps_its_trailing_byte():
    # A message body is UTF-16LE, so an odd span means one trailing byte that is not
    # half of a code unit. It must be stored and written back untouched.
    odd = mes.units_to_bytes([0x41], b"B")
    assert len(odd) % 2 == 1
    blob = build_section([store_from_text("HI"), odd])
    bank = parse(blob)
    assert bank.messages[-1].text() == "A"
    assert bank.messages[-1]._tail == b"B"
    assert bank.rebuild() == blob


# ------------------------------------------------------------- glyph rule / encoding
def test_encode_inverts_decode_across_the_whole_visible_band():
    """Regression: every visible unit must survive store -> read.

    ``decode_units`` shifts *every* stored unit below ``0x40``, so the encoder has to
    invert it over the whole visible band ``0x20..0x5f``. When it only shifted
    ``0x40..0x5f``, visible space through ``'?'`` had no inverse: space was stored as
    ``0x20`` and read back as ``'@'``.
    """
    for visible in VISIBLE_BAND:
        stored = mes.encode_units([visible])
        assert mes.decode_units(stored) == [visible], hex(visible)


def test_edited_text_stores_a_space_as_0x0000():
    """Regression for the space corruption, asserted on the stored units.

    ``set_text('OK GO')`` used to store the space as ``0x20``, which decodes to ``'@'``,
    so the message read back as ``'OK@GO'``. The disc stores a space as ``0x0000``, which
    is why the stored-unit assertion is here and not only the string comparison.
    """
    bank = parse(build_section([store_from_text("HI")]))
    bank.messages[0].set_text("OK GO")
    assert bank.messages[0].text() == "OK GO"
    assert 0x0000 in bank.messages[0].units


def test_editing_one_character_keeps_untouched_bank_a_bytes():
    """Regression: an edit must not rewrite glyphs the author never touched.

    The disc stores 'A' as bank-A 0x41 in many messages, and ``encode_units``
    canonicalises it to bank-B 0x21. Rewriting the whole text therefore made a
    one-character edit rewrite every bank-A byte in the message (measured on the real
    disc: 8 of message 0's 10 stored units change). Only the position whose letter actually
    changed may be re-encoded.
    """
    stored = [0x41, 0x42, 0x43]        # bank-A 'ABC' -- all three literals
    blob = build_section([mes.units_to_bytes(stored + [mes.TERMINATOR])])
    msg = parse(blob).messages[0]
    assert msg.text() == "ABC"
    msg.set_text("ABD")
    assert msg.text() == "ABD"
    # 'A' and 'B' keep their bank-A bytes; only 'C' -> 'D' is re-encoded (0x24).
    assert msg.units[:3] == [0x41, 0x42, 0x24], [hex(u) for u in msg.units]


def test_editing_across_a_length_change_encodes_canonically():
    """A length change shifts every later position, so preservation is dropped.

    Comparing old and new units index-wise would pair 'B' with 'C' and keep a byte
    that belongs to a different character. The safe result is a full canonical
    re-encode of the message.
    """
    stored = [0x41, 0x42]
    blob = build_section([mes.units_to_bytes(stored + [mes.TERMINATOR])])
    msg = parse(blob).messages[0]
    msg.set_text("ABC")
    assert msg.text() == "ABC"
    assert msg.units[:3] == [0x21, 0x22, 0x23], [hex(u) for u in msg.units]


def test_threshold_rule_decodes_bank_b_glyphs():
    # Bank B stores glyphs 0x20 below their ASCII value: 'N' is stored as 0x2e. The
    # threshold rule adds 0x20 to every unit below 0x40 and recovers the word.
    stored = [0x2E, 0x4F, 0x54]
    blob = build_section([mes.units_to_bytes(stored + [mes.TERMINATOR])])
    msg = parse(blob).messages[0]
    assert msg.text(mes.RULE_THRESHOLD) == "NOT"
    assert msg.text(mes.RULE_NONE) == ".OT"
    # 'first' shifts unit 0 only, and unit 0 *is* 0x2e here, so it agrees with threshold.
    # It only diverges when the shift starts after the first unit -- next test.
    assert msg.text(mes.RULE_FIRST) == "NOT"


def test_threshold_rule_beats_first_when_the_shift_starts_late():
    # 'A' is stored literally (0x41, a bank-A glyph) and 0x2e follows as bank B.
    # threshold shifts every unit < 0x40 -> 'AN'. 'first' shifts only unit 0, which is
    # literal here, so the 0x2e stays raw -> 'A.'. That is why the real bank scores
    # 'first' at 0.9503: the shift does not always start at the first unit.
    stored = [0x41, 0x2E]
    blob = build_section([mes.units_to_bytes(stored + [mes.TERMINATOR])])
    msg = parse(blob).messages[0]
    assert msg.text(mes.RULE_THRESHOLD) == "AN"
    assert msg.text(mes.RULE_FIRST) == "A."
    assert msg.text(mes.RULE_NONE) == "A."


def test_no_change_rebuild_is_immune_to_the_lossy_glyph_mapping():
    """A stored unit in 0x40..0x5f and its 0x20..0x3f twin decode to the same letter.

    'N' is stored as 0x2e by the disc, but 0x4e is an equally valid literal encoding of
    the same letter: decode(0x2e) == decode(0x4e) == 'N', while encode('N') canonicalises
    to 0x2e. So the *text* view cannot say which of the two was on disc, and re-encoding
    the visible text wholesale would move bytes the author never touched. rebuild() is
    immune because it writes the stored units back untouched.
    """
    stored = [0x2E, 0x4F, 0x54]
    blob = build_section([mes.units_to_bytes(stored + [mes.TERMINATOR])])
    bank = parse(blob)
    assert bank.messages[0].units[:3] == stored
    assert bank.rebuild() == blob
    # The lossiness itself, on the stored twin (0x40..0x5f is the only lossy band).
    assert mes.decode_units([0x4E]) == mes.decode_units([0x2E]) == [0x4E]
    assert mes.encode_units([0x4E]) == [0x2E]


# -------------------------------------------------------------------- rejection paths
def test_huge_count_is_rejected_before_allocating():
    # A count field implying a table larger than the file must not be trusted; the
    # guard has to fire before any table is allocated.
    blob = p32(64) + p32(mes.MAX_COUNT + 1) + b"\x00" * 64
    assert mes.find_sections(blob) == []


def test_offset_inside_the_header_is_rejected():
    # offsets[0] must equal 4 + 4*count; a smaller value points back into the table.
    blob = p32(4 + 4 * 2 + 4) + p32(2) + p32(0) + p32(8) + b"ABCD"
    assert mes.find_sections(blob) == []


def test_overlapping_offsets_are_rejected():
    # Descending offsets would make the second message read backwards over the first.
    good = store_from_text("HI")
    blob = p32(4 + 4 * 2 + 2 * len(good)) + p32(2) + p32(12 + len(good)) + p32(12) \
        + good + good
    assert mes.find_sections(blob) == []


def test_all_ff_blob_has_no_sections():
    assert mes.find_sections(b"\xff" * 512) == []


def test_empty_blob_has_no_sections():
    assert mes.find_sections(b"") == []


# ---------------------------------------------------------------------- real disc
def test_real_message_banks_roundtrip_byte_exact():
    """Phase 2 exit criterion: decode -> encode -> byte-equal, on the real disc.

    Vacuously passes when the user's extraction is absent, so the suite still runs on a
    bare checkout. Re-run locally where data/raw/ exists before claiming the format done.
    """
    paths = _real_banks()
    if not paths:
        return
    failures = {}
    for path in paths:
        problems = mes.verify_roundtrip(path)
        if problems:
            failures[os.path.basename(path)] = problems[:3]
    assert not failures, failures


def test_real_stored_units_are_canonical_outside_the_ambiguous_band():
    # Units outside 0x40..0x5f survive decode -> encode unchanged; the band does not.
    # Below 0x40 decode raises the unit into 0x20..0x5f and encode lowers it back, so
    # the pair is a true inverse: a space is stored 0x00, reads as 0x20, re-encodes
    # to 0x00. Above 0x5f both directions are identity. Stored units inside
    # 0x40..0x5f are the one documented ambiguity: decode leaves 0x41 alone (it
    # already reads as A) while encode canonicalises it to bank-A 0x21. That is why
    # message-level editing must write stored units instead of re-encoding visible
    # text wholesale. Measured on the real bank, the band holds 30,093 stored units
    # across 592 messages: not a corner case.
    bank = _sysmes()
    if bank is None:
        return
    inside = 0
    for msg in bank.messages:
        for unit in msg.units:
            if 0x40 <= unit <= 0x5F:
                inside += 1
                assert mes.decode_units([unit]) == [unit]
                assert mes.encode_units([unit]) == [unit - mes.SHIFT]
            else:
                assert mes.encode_units(mes.decode_units([unit])) == [unit]
    assert inside > 0


def test_real_sysmres_shape_is_what_the_spec_claims():
    bank = _sysmes()
    if bank is None:
        return
    assert len(bank.data) == SYSMES_SIZE
    assert len(bank.sections) == SYSMES_SECTIONS
    assert len(bank.messages) == SYSMES_MESSAGES
    for section in bank.sections:
        assert section.offsets[0] == 4 + 4 * section.count
        assert section.text_start == section.start + 4 + section.offsets[0]


def test_real_threshold_rule_measurably_beats_the_alternatives():
    """The shift rule was *discovered* by this measurement, so it is asserted here.

    On the real bank the threshold rule decodes 99.98% of visible units as printable
    ASCII; decoding only the first character, or applying no shift at all, leaves the
    bank-B glyphs as control-range junk and scores ~95%.
    """
    bank = _sysmes()
    if bank is None:
        return
    threshold = bank.printable_ratio(mes.RULE_THRESHOLD)
    first = bank.printable_ratio(mes.RULE_FIRST)
    none = bank.printable_ratio(mes.RULE_NONE)
    assert threshold > 0.999, threshold
    assert first < threshold and none < threshold, (first, none, threshold)


def test_real_known_menu_strings_are_decoded():
    bank = _sysmes()
    if bank is None:
        return
    blob = "\n".join(bank.texts())
    for expected in ("ROCKET", "LAUNCHER", "ASSAULT", "RIFLE", "SNIPER", "SHOTGUN",
                     "HANDGUN", "USABLE"):
        assert expected in blob, expected


def test_real_spaces_are_decoded_not_at_signs():
    """The disc really does contain spaces, stored as 0x0000.

    This guards the regression from the other direction: if ``decode_units`` or the
    encoder drifted back to treating 0x20 as a space, the real bank would come back full
    of '@' characters instead.
    """
    bank = _sysmes()
    if bank is None:
        return
    blob = " ".join(bank.texts())
    assert blob.count(" ") > 100, blob.count(" ")
    assert blob.count("@") == 0, blob.count("@")


def test_real_syseff_is_not_a_message_bank():
    """SYSEFF.ALD is an 'MDL' model/skeleton file, so rejecting it is correct.

    Its first bytes are size=1192 followed by the ASCII magic 'MDL', and its only
    printable runs are part names ('Shell_1', 'yakyo_1'). A parser that found "sections"
    in this file would be inventing structure, so the *absence* of sections is the
    correct result and the reason ``roundtrip data/raw`` reports 1/2 files.
    """
    path = os.path.join(RAW_DIR, "SYSEFF.ALD")
    if not os.path.isfile(path):
        return
    with open(path, "rb") as fp:
        data = fp.read()
    assert data[4:7] == b"MDL", data[:8].hex()
    assert mes.find_sections(data) == []


def test_real_no_op_edit_rewrites_no_bytes():
    """The whole point of bank preservation, measured on the real disc.

    Setting a message's text to the text it already has must leave every stored byte
    alone. Before preservation this rewrote every bank-A unit in the message (8 of message 0's 10
    stored units, every one a bank-A glyph).

    Messages whose text view is not lossless are skipped, not silently passed: where
    ``text_to_units(msg.text())`` does not reproduce the visible units, the text view
    cannot round-trip and a no-op edit is not a meaningful test of it.
    """
    bank = _sysmes()
    if bank is None:
        return
    checked = skipped = 0
    for msg in bank.messages:
        cut = (msg.units.index(mes.TERMINATOR) if mes.TERMINATOR in msg.units
               else len(msg.units))
        visible = mes.decode_units(msg.units[:cut])
        text = msg.text()
        if mes.text_to_units(text) != visible:
            skipped += 1
            continue
        before = msg.raw()
        msg.set_text(text)
        assert msg.raw() == before, (msg.index, text)
        checked += 1
    assert checked > 400, (checked, skipped)
