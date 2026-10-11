"""Tests for tools/unpack/iso9660.py against a synthetic but structurally valid image.

The real disc must never enter the repo or CI, so every test builds its own volume via
`_iso_fixture`. These tests cover the reader contract that `unpack` depends on:
directory walking, nested paths, correct extent reading, and both-endian validation.
"""

from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import _iso_fixture
from tools.unpack.iso9660 import Iso9660, IsoFormatError, decode_name


def _open(path):
    fp = open(path, "rb")
    return fp, Iso9660(fp)


def test_volume_descriptor_is_parsed():
    with tempfile.TemporaryDirectory() as tmp:
        iso = os.path.join(tmp, "f.iso")
        _iso_fixture.build_iso(iso)
        fp, image = _open(iso)
        try:
            assert image.volume_id == "TEST_VOLUME", repr(image.volume_id)
            assert image.sector_size == 2048
            assert "iso9660" in image.roots
        finally:
            fp.close()


def test_walk_finds_all_files_and_directories():
    with tempfile.TemporaryDirectory() as tmp:
        iso = os.path.join(tmp, "f.iso")
        info = _iso_fixture.build_iso(iso)
        fp, image = _open(iso)
        try:
            files = {e.path: e for e in image.walk() if not e.is_dir}
            dirs = {e.path for e in image.walk() if e.is_dir}
        finally:
            fp.close()

        assert set(files) == set(info["files"]), sorted(files)
        assert "DATA/" in dirs, sorted(dirs)
        for path, entry in files.items():
            assert entry.size == len(info["files"][path]), (path, entry.size)


def test_extent_contents_match_the_source_bytes():
    with tempfile.TemporaryDirectory() as tmp:
        iso = os.path.join(tmp, "f.iso")
        info = _iso_fixture.build_iso(iso)
        fp, image = _open(iso)
        try:
            entries = {e.path: e for e in image.walk() if not e.is_dir}
            for path, expected in info["files"].items():
                entry = entries[path]
                data = b"".join(image.read_extent(lba, size) for lba, size in entry.segments)
                assert data == expected, path
        finally:
            fp.close()


def test_zero_length_file_is_reported_as_empty():
    with tempfile.TemporaryDirectory() as tmp:
        iso = os.path.join(tmp, "f.iso")
        _iso_fixture.build_iso(iso)
        fp, image = _open(iso)
        try:
            entries = {e.path: e for e in image.walk() if not e.is_dir}
            assert entries["EMPTY.DAT"].size == 0
            assert entries["EMPTY.DAT"].segments == [(0, 0)]
        finally:
            fp.close()


def test_rejects_a_non_iso_file():
    with tempfile.TemporaryDirectory() as tmp:
        bogus = os.path.join(tmp, "bogus.iso")
        with open(bogus, "wb") as fp:
            fp.write(b"\x00" * (2048 * 20))
        fp = open(bogus, "rb")
        try:
            try:
                Iso9660(fp)
            except IsoFormatError:
                pass
            else:
                raise AssertionError("a non-ISO image must raise IsoFormatError")
        finally:
            fp.close()


def test_decode_name_strips_version_suffix():
    assert decode_name(b"SYSTEM.CNF;1", joliet=False) == "SYSTEM.CNF"
    assert decode_name(b"DATA", joliet=False) == "DATA"
    assert decode_name(b"\x00", joliet=False) == "."
    assert decode_name(b"\x01", joliet=False) == ".."
