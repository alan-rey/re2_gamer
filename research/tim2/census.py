"""Census of the TIM2 texture records actually present in the user's disc extract.

This is the *evidence* script behind every number quoted in ``research/tim2/spec.md``.
It is deliberately small and dependency-free: it runs the shipped parser over the real
files and prints what it finds, so any figure in the spec can be re-derived rather than
believed.

Usage::

    python research/tim2/census.py                       # the four measured files
    python research/tim2/census.py data/raw/MRY.AFS      # any files you name

Nothing here is part of the library: ``tools/formats/tim2.py`` never imports it. The
tests in ``tests/test_tim2.py`` assert the same relations mechanically, and the CLI
(``python -m tools.formats.tim2 verify``) is the byte-exact check.

Vocabulary used below:
  prefix    - the 32 bytes ("TIM2" + a u32 + 24 bytes of 0xCD) that begin a record.
  record    - one prefix plus everything up to the next prefix.
  located   - a record whose 128-byte picture header was found and validated.
  structural problem - a relation that is *demonstrably* violated (size identity, or a
              picture running past the end of its record). Expected to be 0.
  standard-table violation - a relation from the public TIM2 documentation that this
              disc breaks (e.g. a declared format that disagrees with image_size/width/
              height). Reported for context, never treated as an error: see the spec.
"""

from __future__ import annotations

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.formats import tim2  # noqa: E402

RAW_DIR = os.path.join("data", "raw")
DEFAULT_FILES = ("SYSTEM.AFS", "ITEM1.AFS", "ITEM2.AFS", "SYSEFF.ALD")


def census(path: str) -> dict:
    """Measure one file with the shipped parser."""
    with open(path, "rb") as fp:
        blob = fp.read()
    prefixes = tim2.find_prefixes(blob)
    archive = tim2.parse(blob) if prefixes else None

    located = structural = standard = 0
    geometry: collections.Counter = collections.Counter()
    formats: collections.Counter = collections.Counter()
    clut_colors: collections.Counter = collections.Counter()
    image_types: collections.Counter = collections.Counter()
    versions: collections.Counter = collections.Counter()
    pic_counts: collections.Counter = collections.Counter()
    invariants: collections.Counter = collections.Counter()
    pictured = 0

    for record in archive.records if archive else ():
        versions[record.version] += 1
        pic_counts[record.num_pictures] += 1
        structural += len(record.problems())
        standard += len(record.picture_problems())
        picture = record.picture
        if picture is None:
            continue
        pictured += 1
        located += 1
        geometry[(picture.width, picture.height)] += 1
        formats[picture.pict_format] += 1
        clut_colors[picture.clut_colors] += 1
        image_types[picture.image_type] += 1
        # The invariant the tests assert: the prefix's own u32 equals the picture
        # header offset plus the picture's declared total size.
        invariants[
            "prefix_u32 == rel + total_size"
            if record.prefix_u32 == picture.rel + picture.total_size
            else "prefix_u32 != rel + total_size"
        ] += 1

    return {
        "path": path,
        "size": len(blob),
        "prefixes": len(prefixes),
        "records": archive.count if archive else 0,
        "located": located,
        "pictured": pictured,
        "structural": structural,
        "standard": standard,
        "pad_pre": len(archive.pad_pre) if archive else 0,
        "geometry": geometry,
        "formats": formats,
        "clut_colors": clut_colors,
        "image_types": image_types,
        "versions": versions,
        "pic_counts": pic_counts,
        "invariants": invariants,
    }


def _histogram(counter: collections.Counter, label: str, limit: int = 12) -> str:
    """Render a counter, newest-largest first, and say so when it is truncated."""
    items = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    shown = ", ".join(f"{key}x{value}" for key, value in items[:limit])
    hidden = len(items) - limit
    if hidden > 0:
        # Never let a truncated tail be mistaken for the whole distribution: the
        # earlier draft of this script printed 8 groups and a reader added them up to
        # a total that silently excluded 22 records.
        shown += f", ... {hidden} more distinct {label}"
    return shown


def main(argv: list[str]) -> int:
    names = argv or list(DEFAULT_FILES)
    rows = []
    for name in names:
        path = name if os.path.exists(name) else os.path.join(RAW_DIR, name)
        if not os.path.exists(path):
            print(f"missing: {path}", file=sys.stderr)
            return 2
        rows.append(census(path))

    print(f"{'file':16} {'bytes':>12} {'prefix':>7} {'recs':>6} {'loc':>5} "
          f"{'prob':>5} {'std':>5} {'pad_pre':>9}")
    for row in rows:
        print(f"{os.path.basename(row['path']):16} {row['size']:>12,} "
              f"{row['prefixes']:>7} {row['records']:>6} {row['located']:>5} "
              f"{row['structural']:>5} {row['standard']:>5} {row['pad_pre']:>9,}")

    total = lambda key: sum(row[key] for row in rows)  # noqa: E731
    print(f"\nfiles: {len(rows)}   prefixes: {total('prefixes')}   "
          f"records: {total('records')}   located pictures: {total('located')}   "
          f"structural problems: {total('structural')}")
    print(f"standard-table violations (reported, never enforced): {total('standard')}")

    merged = lambda key: sum((row[key] for row in rows), collections.Counter())  # noqa: E731
    print(f"  prefix u32 vs rel+total_size : {_histogram(merged('invariants'), 'outcome', 4)}")
    print(f"  version                      : {_histogram(merged('versions'), 'version')}")
    print(f"  num_pictures                 : {_histogram(merged('pic_counts'), 'count')}")
    print(f"  pict_format                  : {_histogram(merged('formats'), 'format')}")
    print(f"  image_type                   : {_histogram(merged('image_types'), 'type')}")
    print(f"  clut_colors                  : {_histogram(merged('clut_colors'), 'size')}")
    print(f"  width x height               : {_histogram(merged('geometry'), 'geometry')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
