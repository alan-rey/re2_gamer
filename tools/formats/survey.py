"""Survey the extracted disc tree: AFS member inventory + magic-signature clustering.

Purpose (Phase 2 triage): decide *which* format family to reverse-engineer next by
counting how many members on the real disc share each leading-byte signature, and by
dumping real hex when the coarse signature is not specific enough.

Accepts either an AFS archive (each member is classified) or a loose file (the file
itself is classified), so `data/raw/SYSMES.ALD` and `data/raw/MRY.AFS` go through the
same path.

Usage:
    python -m tools.formats.survey                        # every data/raw file
    python -m tools.formats.survey data/raw/ITEM1.AFS     # one archive
    python -m tools.formats.survey --heads 8 data/raw/MRY.AFS
    python -m tools.formats.survey --json build/survey.json
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import struct
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from tools.formats import afs  # noqa: E402

RAW_DIR = os.path.join("data", "raw")
HEAD_LEN = 32

# Leading-byte magics worth naming. Order matters: longest/most specific first.
KNOWN_MAGICS: tuple[tuple[bytes, str], ...] = (
    (b"AFS\x00", "AFS-null"),
    (b"AFS ", "AFS-space"),
    (b"\x7fELF", "ELF"),
    (b"RIFF", "RIFF"),
    (b"TIM2", "TIM2-texture"),
    (b"VAGp", "VAG-audio"),
    (b"VAGi", "VAG-audio"),
    (b"SShd", "IPU-stream"),
    (b"SSbd", "IPU-stream"),
    (b"PSS\x00", "PSS-stream"),
    (b"PS2\x00", "PS2-magic"),
    (b"CLT\x00", "CLT"),
    (b"MDL\x00", "MDL"),
    (b"pBAV", "pBAV"),
)


def read_head(path: str, offset: int, size: int, n: int = HEAD_LEN) -> bytes:
    """Read at most `n` bytes of member data without reading the whole member.

    May return fewer than `n` bytes: members smaller than the head probe are short by
    nature (MRY.AFS has members under 16 bytes), so callers must not assume a length.
    """
    with open(path, "rb") as fp:
        fp.seek(offset)
        return fp.read(min(n, size))


def signature(head: bytes) -> str:
    """Classify a member head of *any* length into a coarse signature token."""
    if not head:
        return "EMPTY"
    for magic, name in KNOWN_MAGICS:
        if head.startswith(magic):
            return name
    # A PSS stream seen at +4 (RIFF-style wrapper) shows up on some discs.
    if len(head) >= 8 and head[4:8] == b"PSS\x00":
        return "PSS-stream@4"
    if len(set(head)) == 1:
        return "const:%02x" % head[0]
    short = len(head) < 16
    if short:
        if all(32 <= b < 127 for b in head):
            return "ascii-short:" + head[:8].decode("ascii", "replace").strip()
        return "short(%d)" % len(head)
    printable = sum(1 for b in head if 32 <= b < 127)
    if printable == len(head):
        return "ascii:" + head[:8].decode("ascii", "replace").strip()
    u32s = struct.unpack_from("<4I", head)
    if all(v == 0 for v in u32s):
        return "zeros"
    first = u32s[0]
    # A plausible small little-endian count/pointer table at offset 0.
    if 0 < first < 1 << 20 and u32s[1] == 0:
        return "table?(n=%d)" % first
    if head[0] == 0 and head[1] == 0 and head[2] == 0:
        return "u24be/ptr"
    return "raw"


def hexdump(data: bytes, prefix: str = "      ") -> str:
    """One-line hex + ascii rendering of a head probe."""
    hexpart = " ".join(f"{b:02x}" for b in data)
    asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{prefix}{hexpart:<{HEAD_LEN * 3}} |{asciipart}|"


def survey_archive(path: str) -> dict:
    table = afs.parse_table_file(path)
    sigs: collections.Counter = collections.Counter()
    sizes: collections.Counter = collections.Counter()
    members = []
    for entry in table.entries:
        head = read_head(path, entry.offset, entry.size)
        sig = signature(head)
        sigs[sig] += 1
        sizes[sig] += entry.size
        members.append(
            {
                "index": entry.index,
                "offset": entry.offset,
                "size": entry.size,
                "head": head.hex(),
                "sig": sig,
            }
        )
    return {
        "path": path,
        "container": "afs",
        "size": os.path.getsize(path),
        "members": len(table.entries),
        "signatures": dict(sigs),
        "bytes_by_signature": dict(sizes),
        "detail": members,
    }


def survey_loose(path: str) -> dict:
    head = read_head(path, 0, os.path.getsize(path))
    sig = signature(head)
    return {
        "path": path,
        "container": "loose",
        "size": os.path.getsize(path),
        "members": 1,
        "signatures": {sig: 1},
        "bytes_by_signature": {sig: os.path.getsize(path)},
        "detail": [
            {
                "index": 0,
                "offset": 0,
                "size": os.path.getsize(path),
                "head": head.hex(),
                "sig": sig,
            }
        ],
    }


def survey(path: str) -> dict:
    with open(path, "rb") as fp:
        magic = fp.read(4)
    if afs.is_afs(magic):
        return survey_archive(path)
    return survey_loose(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", help="files (default: everything in data/raw)")
    ap.add_argument("--json", metavar="PATH", help="write full survey as JSON")
    ap.add_argument("--detail", action="store_true", help="list every member row")
    ap.add_argument(
        "--heads",
        type=int,
        default=0,
        metavar="N",
        help="hexdump the first N members of every distinct signature per file",
    )
    args = ap.parse_args(argv)

    targets = args.paths
    if not targets:
        if not os.path.isdir(RAW_DIR):
            print(f"no {RAW_DIR}/ - run Phase 1 extraction first", file=sys.stderr)
            return 2
        targets = sorted(os.path.join(RAW_DIR, n) for n in os.listdir(RAW_DIR))

    total: collections.Counter = collections.Counter()
    total_bytes: collections.Counter = collections.Counter()
    reports = []
    for t in targets:
        rep = survey(t)
        reports.append(rep)
        total.update(rep["signatures"])
        total_bytes.update(rep["bytes_by_signature"])
        print(f"\n== {os.path.basename(t)}  {rep['size']:,} bytes  {rep['members']} members")
        for sig, n in sorted(rep["signatures"].items(), key=lambda kv: -kv[1]):
            print(f"   {sig:22} {n:5}  {rep['bytes_by_signature'][sig]:>13,} bytes")
        if args.heads:
            shown: collections.Counter = collections.Counter()
            for m in rep["detail"]:
                if shown[m["sig"]] >= args.heads:
                    continue
                shown[m["sig"]] += 1
                print(f"     [{m['index']:4}] size={m['size']:>9} {m['sig']}")
                print(hexdump(bytes.fromhex(m["head"])))
        if args.detail:
            for m in rep["detail"]:
                print(
                    f"     [{m['index']:4}] off={m['offset']:>10} "
                    f"size={m['size']:>10} {m['sig']:22} {m['head']}"
                )

    print("\n===== TOTALS across %d files" % len(targets))
    for sig, n in sorted(total.items(), key=lambda kv: -kv[1]):
        print(f"   {sig:22} {n:5}  {total_bytes[sig]:>15,} bytes")

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fp:
            json.dump(
                {
                    "files": reports,
                    "totals": dict(total),
                    "totals_bytes": dict(total_bytes),
                },
                fp,
                indent=1,
            )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
