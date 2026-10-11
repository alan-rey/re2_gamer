"""End-to-end tests for the Phase 1 CLI: `python -m tools.unpack.extract`.

These exercise the real code path (walk -> stream extents -> hash -> manifest -> verify)
against the synthetic image, so the Phase 1 exit criteria are checkable without the
1.8 GB copyrighted disc.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import _iso_fixture
from tools.unpack import extract


def _extract(tmp):
    """Build the fixture image, run the CLI, return (info, out_dir, manifest)."""
    iso = os.path.join(tmp, "fixture.iso")
    info = _iso_fixture.build_iso(iso)
    out = os.path.join(tmp, "raw")
    manifest_path = os.path.join(tmp, "manifest.json")
    rc = extract.main(
        [
            "--iso", iso,
            "--out", out,
            "--manifest", manifest_path,
            "--no-iso-hash",
        ]
    )
    assert rc == 0, f"extract exited {rc}"
    with open(manifest_path, encoding="utf-8") as fp:
        return info, out, json.load(fp)


def test_manifest_lists_every_file_with_size_and_sha256():
    with tempfile.TemporaryDirectory() as tmp:
        info, _out, manifest = _extract(tmp)

        paths = {r["path"] for r in manifest["files"]}
        assert paths == set(info["files"]), sorted(paths)

        by_path = {r["path"]: r for r in manifest["files"]}
        for path, payload in info["files"].items():
            record = by_path[path]
            assert record["size"] == len(payload), path
            assert record["sha256"] == hashlib.sha256(payload).hexdigest(), path

        assert manifest["totals"]["files"] == len(info["files"])
        assert manifest["schema_version"] == extract.SCHEMA_VERSION


def test_extracted_tree_matches_the_manifest_byte_for_byte():
    with tempfile.TemporaryDirectory() as tmp:
        info, out, manifest = _extract(tmp)
        for record in manifest["files"]:
            rel = record.get("local_path", record["path"])
            target = os.path.join(out, *rel.split("/"))
            assert os.path.isfile(target), rel
            with open(target, "rb") as fp:
                data = fp.read()
            assert data == info["files"][record["path"]], rel


def test_boot_executable_is_identified_from_system_cnf():
    with tempfile.TemporaryDirectory() as tmp:
        info, _out, manifest = _extract(tmp)
        boot = manifest["boot"]

        assert boot.get("found") is True, boot
        # The name comes from SYSTEM.CNF's BOOT2 line, not from the directory entry.
        assert boot.get("name") == info["boot_file"], boot
        assert boot.get("boot_ref", "").upper().endswith("SLUS_215.02;1"), boot

        # The ELF header at the BOOT target's extent must have been parsed for real.
        elf = boot.get("elf")
        assert elf is not None, boot
        assert elf["class"] == "ELF32", elf
        assert elf["machine"] == "MIPS", elf
        assert elf["entry_hex"] == "0x00100000", elf
        assert [ph["type"] for ph in elf["program_headers"]] == ["LOAD"], elf


def test_verify_subcommand_accepts_a_good_extraction():
    with tempfile.TemporaryDirectory() as tmp:
        _info, out, manifest = _extract(tmp)
        manifest_path = os.path.join(tmp, "manifest.json")
        rc = extract.main(["--verify", "--out", out, "--manifest", manifest_path])
        assert rc == 0, rc


def test_verify_detects_a_corrupted_file():
    with tempfile.TemporaryDirectory() as tmp:
        _info, out, manifest = _extract(tmp)
        manifest_path = os.path.join(tmp, "manifest.json")

        victim = manifest["files"][0]
        rel = victim.get("local_path", victim["path"])
        target = os.path.join(out, *rel.split("/"))
        with open(target, "r+b") as fp:
            fp.seek(0)
            fp.write(b"\xff")

        rc = extract.main(["--verify", "--out", out, "--manifest", manifest_path])
        assert rc == 1, "verify must fail when a file no longer matches its hash"


def test_zero_length_file_extracts_without_error():
    with tempfile.TemporaryDirectory() as tmp:
        info, out, manifest = _extract(tmp)
        by_path = {r["path"]: r for r in manifest["files"]}
        record = by_path["EMPTY.DAT"]
        assert record["size"] == 0
        assert record["sha256"] == hashlib.sha256(b"").hexdigest()
        target = os.path.join(out, "EMPTY.DAT")
        assert os.path.isfile(target), "zero-length files must still be created"


def test_local_path_sanitization_is_deterministic_and_safe():
    assert extract.local_relpath("DATA/HELLO.TXT") == "DATA/HELLO.TXT"
    assert extract.local_relpath("A/B/C.BIN") == "A/B/C.BIN"
    # Characters Windows forbids are rewritten, never dropped.
    assert extract.local_relpath("bad:name*.t") == "bad_name_.t"
    # Traversal components cannot escape the output directory.
    assert extract.local_relpath("../evil") == "evil"
    assert extract.local_relpath("..") == ""
    # Reserved device names are neutralised.
    assert extract.local_relpath("CON") == "_CON"
