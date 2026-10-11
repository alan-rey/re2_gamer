"""Phase 2 probe for SYSMES.ALD: derive message boundaries from bytes, then fit tables.

Ground truth first: every message is a run of UTF-16LE units terminated by 0xFFFF.
We locate those terminators mechanically, reconstruct message spans, and only then ask
which table (and which base offset) reproduces the observed span starts.

Usage:
    python research/mes/probe.py [data/raw/SYSMES.ALD]
"""

from __future__ import annotations

import collections
import struct
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "data/raw/SYSMES.ALD"


def load(path: str) -> bytes:
    with open(path, "rb") as fp:
        return fp.read()


def terminator_runs(data: bytes) -> list[int]:
    """Return start indices (in u16 units) of maximal runs of 0xFFFF."""
    n = len(data) // 2
    u16 = struct.unpack_from("<%dH" % n, data, 0)
    runs: list[int] = []
    i = 0
    while i < n:
        if u16[i] == 0xFFFF:
            j = i
            while j < n and u16[j] == 0xFFFF:
                j += 1
            runs.append(i)
            i = j
        else:
            i += 1
    return runs, u16


def textiness(u16, start_unit: int, end_unit: int) -> float:
    """Fraction of units that look like text: ASCII-printable or a 0xFFxx / 0x000x code."""
    if end_unit <= start_unit:
        return 0.0
    good = 0
    total = end_unit - start_unit
    for k in range(start_unit, end_unit):
        v = u16[k]
        if 0x20 <= v < 0x7F or 0xFF01 <= v <= 0xFFFE or v < 0x0020:
            good += 1
    return good / total


def decode(u16, start_unit: int, end_unit: int, limit: int = 60) -> str:
    out = []
    for k in range(start_unit, end_unit):
        v = u16[k]
        if 0x20 <= v < 0x7F:
            out.append(chr(v))
        else:
            out.append("<%04x>" % v)
        if len(out) >= limit:
            out.append("...")
            break
    return "".join(out)


def u32s(data: bytes, offset: int, count: int) -> list[int]:
    return list(struct.unpack_from("<%dI" % count, data, offset))


def fit(table: list[int], starts: list[int], label: str) -> None:
    """Report how a table lines up with observed message starts."""
    if not table or not starts:
        return
    print(f"\n  fit vs {label}: table={len(table)} spans={len(starts)}")
    deltas = collections.Counter()
    for t, s in zip(table, starts):
        deltas[s - t] += 1
    for delta, hits in deltas.most_common(4):
        print(f"    start - value == {delta:>8} (0x{delta & 0xFFFFFFFF:08x}) "
              f"for {hits} of {min(len(table), len(starts))}")
    # absolute test
    exact = sum(1 for t, s in zip(table, starts) if t == s)
    print(f"    exact absolute matches: {exact}")


def main() -> int:
    data = load(PATH)
    runs, u16 = terminator_runs(data)
    print(f"== {PATH}  size={len(data):,}  u16 units={len(u16):,}")
    print(f"   terminator runs: {len(runs)}  (first 12 unit idx: {runs[:12]})")

    # Reconstruct spans: from just after the previous terminator run to the next run.
    spans = []
    prev_end = 0
    for r in runs:
        # skip past padding 0xFFFFs the same way (already grouped)
        if r > prev_end:
            spans.append((prev_end, r))
        prev_end = r + 1
    # merge: the group end is unknown here, so recompute by scanning forward
    spans = []
    prev_end = 0
    for r in runs:
        e = r
        while e < len(u16) and u16[e] == 0xFFFF:
            e += 1
        if e > prev_end:
            spans.append((prev_end, e))
        prev_end = e

    scored = []
    for (a, b) in spans:
        sc = textiness(u16, a, b)
        scored.append((sc, a, b))
    texty = [s for s in scored if s[0] >= 0.9]
    print(f"   spans total={len(scored)}  texty(>=0.9)={len(texty)}")
    print(f"   first 8 spans (score, byte_start, byte_end, preview):")
    for sc, a, b in scored[:8]:
        print(f"     {sc:4.2f}  {a*2:>7} .. {b*2:<7}  {decode(u16, a, b)}")

    starts = [a * 2 for (sc, a, b) in texty]
    print(f"\n   texty span byte-lengths (first 20): "
          f"{[b*2 - a*2 for (sc, a, b) in texty[:20]]}")

    head0 = struct.unpack_from("<I", data, 0)[0]
    print(f"\n   u32[0] = {head0} (0x{head0:x})")

    t1 = u32s(data, 4, 184)
    print(f"   table1: 184 u32 at byte 4, range {t1[0]}..{t1[-1]}, "
          f"even={sum(1 for v in t1 if v % 2 == 0)}/184")
    fit(t1, starts, "table1")

    # the second ascending run the earlier probe found:
    t2 = u32s(data, 0x1554, 495)
    print(f"\n   table2: 495 u32 at byte 0x1554, range {t2[0]}..{t2[-1]}, "
          f"even={sum(1 for v in t2 if v % 2 == 0)}/495")
    fit(t2, starts, "table2")

    print("\n   u32 window around 0x154C:")
    for off in range(0x1540, 0x1570, 4):
        (v,) = struct.unpack_from("<I", data, off)
        print(f"     0x{off:04x}  {v:>10}  0x{v:08x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
