"""Tests for tools/unpack/typeid.py: the Phase 1 file-type guesser.

Written to run under pytest *and* under the stdlib fallback runner
(``python tests/run_tests.py``), so no pytest-only features are used.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.unpack import typeid


def test_elf_signature_is_trusted():
    kind, label, confidence = typeid.identify(b"\x7fELF\x01\x01\x01\x00", "SLUS_200.06", 512)
    assert kind == "executable", kind
    assert confidence == "magic", confidence
    assert "ELF" in label


def test_ps2_signatures_are_recognised():
    cases = [
        (b"VAGp" + b"\x00" * 60, "audio"),
        (b"PSS" + b"\x00" * 60, "video"),
        (b"IPUM" + b"\x00" * 60, "video"),
        (b"TIM2" + b"\x00" * 60, "image"),
    ]
    for head, expected_kind in cases:
        kind, _, confidence = typeid.identify(head, "x.dat", len(head))
        assert (kind, confidence) == (expected_kind, "magic"), (head[:4], kind, confidence)


def test_ascii_text_is_heuristic_not_magic():
    head = b"BOOT2 = cdrom0:\\SLUS_200.06;1\r\nVER = 1.00\r\n"
    kind, _, confidence = typeid.identify(head, "SYSTEM.CNF", len(head))
    assert kind == "text", kind
    assert confidence == "heuristic", confidence


def test_binary_garbage_falls_back_to_extension():
    # No magic, not text (contains NULs), not an offset table (leading u32 is huge),
    # so the filename suffix is the only remaining evidence.
    head = bytes(range(256)) * 4
    kind, label, confidence = typeid.identify(head, "DATA.BIN", len(head))
    assert confidence == "extension", confidence
    assert kind == "data", kind
    assert "extension" in label, label


def test_ps2_boot_name_suffix_is_not_a_real_extension():
    # 'SLUS_200.06' has no extension: splitext reads '.06' as one, and it is absent
    # from EXTENSION_KINDS, so an unrecognised body stays honestly 'unknown' rather
    # than being guessed from a version number.
    head = bytes(range(256)) * 4
    kind, _, confidence = typeid.identify(head, "SLUS_200.06", len(head))
    assert (kind, confidence) == ("unknown", "unknown"), (kind, confidence)


def test_unknown_extension_and_no_signature_is_unknown():
    head = bytes(range(256)) * 4
    kind, _, confidence = typeid.identify(head, "mystery.xyz", len(head))
    assert (kind, confidence) == ("unknown", "unknown"), (kind, confidence)


def test_zero_length_file_is_classified_before_extension_fallback():
    kind, label, confidence = typeid.identify(b"", "EMPTY.FOO", 0)
    assert kind == "empty", kind
    assert "zero-length" in label
    assert confidence == "magic", confidence


def test_offset_table_heuristic_matches_archive_shape():
    # count=4 then three little-endian offsets past the 16-byte table, inside the file.
    head = (4).to_bytes(4, "little") + (16).to_bytes(4, "little") + \
           (64).to_bytes(4, "little") + (128).to_bytes(4, "little") + b"\x00" * 480
    kind, _, confidence = typeid.identify(head, "DATA.BIN", 1024)
    assert kind == "archive", kind
    assert confidence == "heuristic", confidence


def test_offset_table_heuristic_rejects_implausible_count():
    head = (999999).to_bytes(4, "little") + b"\x00" * 508
    kind, _, confidence = typeid.identify(head, "DATA.BIN", 512)
    assert confidence != "heuristic", confidence


def test_identify_never_raises_on_empty_input():
    for name in ("", "x", "a.VAG", "noext"):
        kind, label, confidence = typeid.identify(b"", name, -1)
        assert kind and label and confidence
