"""Hexdump, strings and u32-table inspector for Phase 2 binary format triage.

Reads a byte range of any file (including a slice of an AFS member) and prints it as
a hexdump, as printable ASCII runs with absolute offsets, and/or as little-endian u32s.
Stdlib only; never writes to the file.

Usage:
    python -m tools.hexdump FILE --length 256
    python -m tools.hexdump FILE --offset 4096 --length 64
    python -m tools.hexdump FILE --strings 6 --limit 80
    python -m tools.hexdump FILE --u32 32
    python -m tools.hexdump FILE --offset 2048 --length 512 --strings 5
"""

from __future__ import annotations

import argparse
import os
import struct


def read_slice(path: str, offset: int, length: int | None) -> bytes:
    size = os.path.getsize(path)
    if offset > size:
        raise SystemExit(f"offset {offset} is past EOF ({size} bytes)")
    with open(path, "rb") as fp:
        fp.seek(offset)
        return fp.read(length) if length is not None else fp.read()


def hexdump(data: bytes, base: int = 0, width: int = 16) -> None:
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"{base + i:08x}  {hexpart:<{width * 3}}|{text}|")


def strings(data: bytes, base: int = 0, minlen: int = 6, limit: int | None = None) -> int:
    """Print printable-ASCII runs of at least `minlen` bytes, with absolute offsets."""
    found = 0
    run_start = None
    buf = bytearray()
    for i, b in enumerate(data):
        if 32 <= b < 127:
            if run_start is None:
                run_start = i
            buf.append(b)
        else:
            if run_start is not None and len(buf) >= minlen:
                print(f"  {base + run_start:08x}  {len(buf):5}  {buf.decode('ascii')}")
                found += 1
                if limit and found >= limit:
                    return found
            run_start = None
            buf.clear()
    if run_start is not None and len(buf) >= minlen:
        print(f"  {base + run_start:08x}  {len(buf):5}  {buf.decode('ascii')}")
        found += 1
    return found


def u32_table(data: bytes, base: int = 0, count: int = 32) -> None:
    """Print the first `count` little-endian u32s, with delta from the previous value."""
    n = min(count, len(data) // 4)
    prev = None
    for i in range(n):
        (v,) = struct.unpack_from("<I", data, i * 4)
        delta = "" if prev is None else (f"  (+{v - prev})" if v >= prev else f"  (-{prev - v})")
        print(f"  [{i:4}] @{base + i * 4:08x}  {v:>12}  0x{v:08x}{delta}")
        prev = v
    if len(data) // 4 > n:
        print(f"  ... {len(data) // 4 - n} more u32s in range")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.hexdump", description=__doc__)
    ap.add_argument("path")
    ap.add_argument("--offset", type=lambda s: int(s, 0), default=0,
                    help="start offset (accepts 0x..)")
    ap.add_argument("--length", type=lambda s: int(s, 0), default=256,
                    help="bytes to read (0 = to EOF)")
    ap.add_argument("--strings", type=int, default=None, metavar="MINLEN",
                    help="also print printable ASCII runs of at least MINLEN bytes")
    ap.add_argument("--limit", type=int, default=None, help="max strings to print")
    ap.add_argument("--u32", type=int, default=None, metavar="COUNT",
                    help="also print first COUNT little-endian u32s")
    args = ap.parse_args(argv)

    length = args.length if args.length else None
    data = read_slice(args.path, args.offset, length)
    print(f"== {args.path}  offset={args.offset} read={len(data)} bytes "
          f"filesize={os.path.getsize(args.path)}")
    hexdump(data, base=args.offset)

    if args.u32:
        print("-- u32 table (little-endian) --")
        u32_table(data, base=args.offset, count=args.u32)

    if args.strings:
        print(f"-- printable runs >= {args.strings} bytes --")
        found = strings(data, base=args.offset, minlen=args.strings, limit=args.limit)
        print(f"-- {found} run(s) shown --")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
