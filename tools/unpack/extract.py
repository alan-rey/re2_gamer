"""Phase 1 `unpack` stage: PS2 disc image -> catalogued, checksummed file tree.

Contract (docs/04-bast-modding-pipeline.md, stage 1):

  * Input: the user's own PS2 ISO (never committed, matched by .gitignore).
  * Output: every file in the image extracted to ``data/raw/`` plus a
    ``data/manifest.json`` recording path, size, SHA-256 and a type guess.
  * Rule: **read-only with respect to the ISO**. The source image is never written.

The image is opened read-only ("rb") and this module owns no code path that opens the ISO
for writing, so the "never mutates the source" rule is enforced by construction rather
than by discipline.

Usage::

    python -m tools.unpack.extract --iso .game_iso/<disc>.iso
    python -m tools.unpack.extract --iso <disc>.iso --out data/raw --manifest data/manifest.json
    python -m tools.unpack.extract --verify          # integrity-check against manifest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from tools.unpack import elfinfo, typeid
from tools.unpack.iso9660 import FileEntry, Iso9660, IsoFormatError

SCHEMA_VERSION = 1
CHUNK = 1 << 20          # 1 MiB streaming read
HEAD_BYTES = 4096        # how much of each file feeds the type identifier

ILLEGAL_WINDOWS_CHARS = '<>:"/\\|?*'
RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


# --------------------------------------------------------------------------- paths
def safe_component(name: str) -> str:
    """Map one ISO path component onto a name that is legal on this filesystem.

    ISO9660 allows characters Windows rejects (``:``, ``*``, ``?`` ...). We do not
    silently drop files: the component is rewritten deterministically and the original
    is preserved in the manifest's ``path`` field, so the disc tree stays reconstructible.
    """
    out = []
    for ch in name:
        out.append("_" if ch in ILLEGAL_WINDOWS_CHARS or ord(ch) < 32 else ch)
    cleaned = "".join(out).rstrip(" .")
    if not cleaned:
        cleaned = "_"
    stem = cleaned.split(".", 1)[0].upper()
    if stem in RESERVED_WINDOWS_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def local_relpath(iso_path: str) -> str:
    """Translate a whole ISO path into a sanitized, relative local path.

    Traversal is impossible in ISO9660 (no ``..`` entries that point outside), but the
    components are still filtered so a malformed image cannot escape the output dir.
    """
    parts = []
    for raw in iso_path.split("/"):
        if raw in ("", "."):
            continue
        if raw == "..":
            continue
        parts.append(safe_component(raw))
    return "/".join(parts)


def uniquify(rel: str, taken: set[str]) -> str:
    """Resolve collisions introduced by sanitization (``A*B`` and ``A?B`` -> same name)."""
    if rel not in taken:
        taken.add(rel)
        return rel
    stem, ext = os.path.splitext(rel)
    i = 1
    while f"{stem}~{i}{ext}" in taken:
        i += 1
    resolved = f"{stem}~{i}{ext}"
    taken.add(resolved)
    return resolved


# ------------------------------------------------------------------------ hashing
def stream_extracts(fp, segments, sector_size: int, dest: Path) -> tuple[str, bytes, int]:
    """Write one file from its data extents, hashing as we go.

    Returns ``(sha256_hex, head_bytes, total_written)``. Streaming rather than
    ``read_extent`` matters because Phase 1 touches ~1.8 GB and some single extents are
    hundreds of MB; buffering those whole would be wasteful and can fail outright.

    Hashing and writing happen in the same pass so the digest always describes the bytes
    that actually landed on disk.
    """
    digest = hashlib.sha256()
    head = b""
    written = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        for lba, size in segments:
            if size <= 0:
                continue
            fp.seek(lba * sector_size)
            remaining = size
            while remaining > 0:
                block = fp.read(min(CHUNK, remaining))
                if not block:
                    raise IsoFormatError(
                        f"short read in extent at LBA {lba}: {remaining} bytes missing"
                    )
                remaining -= len(block)
                digest.update(block)
                out.write(block)
                written += len(block)
                if len(head) < HEAD_BYTES:
                    head += block[: HEAD_BYTES - len(head)]
    return digest.hexdigest(), head, written


# --------------------------------------------------------------------------- boot
def detect_boot(fp, image: Iso9660, entries: dict[str, FileEntry], sector_size: int) -> dict:
    """Locate and describe the PS2 boot executable referenced by ``SYSTEM.CNF``.

    PS2 discs boot via ``SYSTEM.CNF`` whose ``BOOT``/``BOOT2`` line names the ELF to run.
    Recording it in the manifest gives Phase 2 a named target for disassembly instead of
    a 1.8 GB haystack.
    """
    cnf = entries.get("SYSTEM.CNF")
    if cnf is None:
        return {"found": False, "reason": "SYSTEM.CNF not present in image"}

    data = b"".join(image.read_extent(lba, size) for lba, size in cnf.segments)
    text = data.decode("ascii", errors="replace")
    boot_ref = None
    for line in text.splitlines():
        line = line.strip()
        upper = line.upper()
        for key in ("BOOT2", "BOOT"):
            if upper.startswith(key):
                _, _, value = line.partition("=")
                boot_ref = value.strip()
                break
        if boot_ref:
            break

    if not boot_ref:
        return {"found": False, "reason": "no BOOT/BOOT2 line in SYSTEM.CNF"}

    # 'cdrom0:\SLUS_200.06;1' -> 'SLUS_200.06'
    name = boot_ref.replace("\\", "/").rsplit("/", 1)[-1]
    name = name.split(";", 1)[0].strip()
    entry = next(
        (e for p, e in entries.items() if os.path.basename(p).upper() == name.upper()),
        None,
    )
    info: dict = {
        "found": entry is not None,
        "boot_ref": boot_ref,
        "name": name,
        "system_cnf": {
            "text": text.strip(),
            "size": cnf.size,
        },
    }
    if entry is None:
        info["reason"] = f"BOOT target {name} not present in image"
        return info

    info["iso_path"] = entry.path
    info["size"] = entry.size

    # The ELF lives at this extent, not at offset 0 of the image, so hand elfinfo the
    # base offset. A malformed or non-ELF boot file must not abort the whole extraction -
    # it is recorded as a warning-level fact and the rest of the catalogue still lands.
    base_offset = entry.segments[0][0] * sector_size
    try:
        info["elf"] = elfinfo.summarize(fp, base_offset=base_offset)
    except ValueError as exc:
        info["elf"] = None
        info["reason"] = f"BOOT target {name} is present but unreadable as ELF32: {exc}"
    return info


# --------------------------------------------------------------------------- core
def build_manifest(iso_path: Path, out_dir: Path, *, hash_iso: bool = True,
                   dry_run: bool = False, log=print) -> dict:
    """Extract the image and return the manifest dictionary."""
    started = time.time()
    iso_stat = iso_path.stat()
    warnings: list[str] = []

    with iso_path.open("rb") as fp:
        image = Iso9660(fp)
        sector_size = image.sector_size

        dir_entries: list[str] = []
        file_entries: list[FileEntry] = []
        for entry in image.walk():
            if entry.is_dir:
                dir_entries.append(entry.path)
            else:
                file_entries.append(entry)
        file_entries.sort(key=lambda e: e.path)

        log(f"image      : {iso_path}")
        log(f"volume id  : {image.volume_id!r}")
        log(f"joliet     : {image.joliet}")
        log(f"sector     : {sector_size}")
        log(f"directories: {len(dir_entries)}")
        log(f"files      : {len(file_entries)}")

        taken: set[str] = set()
        records: list[dict] = []
        total_bytes = 0
        kind_counts: dict[str, int] = {}
        renamed = 0

        for entry in file_entries:
            rel = uniquify(local_relpath(entry.path), taken)
            if rel != entry.path:
                renamed += 1
            dest = out_dir / rel

            if dry_run:
                head = b""
                if entry.segments and entry.size > 0:
                    fp.seek(entry.segments[0][0] * sector_size)
                    head = fp.read(min(HEAD_BYTES, entry.size))
                digest = None
                written = entry.size
            else:
                digest, head, written = stream_extracts(
                    fp, entry.segments, sector_size, dest
                )
                if written != entry.size:
                    warnings.append(
                        f"{entry.path}: extracted {written} bytes but the directory "
                        f"record declares {entry.size}"
                    )

            kind, label, confidence = typeid.identify(head, entry.path, entry.size)
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            total_bytes += written

            record = {
                "path": entry.path,
                "size": entry.size,
                "sha256": digest,
                "kind": kind,
                "label": label,
                "confidence": confidence,
                "ext": os.path.splitext(entry.path)[1].lower(),
                "extent_lba": entry.lba,
                "extents": len(entry.segments),
                "extended_attr_len": entry.ext_attr_len,
            }
            if rel != entry.path:
                record["local_path"] = rel
            if entry.segments:
                record["data_lba"] = entry.segments[0][0]
            records.append(record)

        by_path = {e.path: e for e in file_entries}
        boot = detect_boot(fp, image, by_path, sector_size)

        warnings.extend(image.warnings)

        iso_hash = None
        if hash_iso and not dry_run:
            iso_hash = _hash_file(iso_path)

    elapsed = time.time() - started
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "stage": "unpack",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "name": iso_path.name,
            "size": iso_stat.st_size,
            "sha256": iso_hash,
        },
        "volume": {
            "id": image.volume_id,
            "sector_size": sector_size,
            "joliet": image.joliet,
        },
        "boot": boot,
        "totals": {
            "files": len(records),
            "directories": len(dir_entries),
            "bytes": total_bytes,
            "renamed": renamed,
            "by_kind": dict(sorted(kind_counts.items())),
            "elapsed_seconds": round(elapsed, 2),
        },
        "warnings": warnings,
        "files": records,
    }
    return manifest


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            block = fp.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verify(out_dir: Path, manifest_path: Path, log=print) -> int:
    """Re-hash every extracted file and compare against the manifest.

    This is the Phase 1 mechanical exit check: ``data/raw/`` really does match
    ``data/manifest.json``, byte for byte.
    """
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    missing, mismatched, ok = [], [], 0
    for record in manifest["files"]:
        rel = record.get("local_path", record["path"])
        target = out_dir / rel
        if not target.is_file():
            missing.append(rel)
            continue
        actual = _hash_file(target)
        if record["sha256"] and actual != record["sha256"]:
            mismatched.append(rel)
        else:
            ok += 1
    log(f"verified: {ok} ok, {len(missing)} missing, {len(mismatched)} mismatched")
    for name in missing[:20]:
        log(f"  MISSING    {name}")
    for name in mismatched[:20]:
        log(f"  MISMATCH   {name}")
    return 0 if not missing and not mismatched else 1


# ---------------------------------------------------------------------- discovery
def default_iso(workspace: Path) -> Path | None:
    """Best guess at the user's disc image: ``$RECVX_ISO``, else the one ISO present."""
    env = os.environ.get("RECVX_ISO")
    if env:
        return Path(env)
    for folder in (workspace / ".game_iso", workspace):
        if not folder.is_dir():
            continue
        isos = sorted(folder.glob("*.iso"))
        if len(isos) == 1:
            return isos[0]
        if isos:
            return max(isos, key=lambda p: p.stat().st_size)
    return None


def main(argv: list[str] | None = None) -> int:
    workspace = Path.cwd()
    parser = argparse.ArgumentParser(
        prog="python -m tools.unpack.extract",
        description="Phase 1: extract a PS2 disc image to data/raw/ + data/manifest.json",
    )
    parser.add_argument("--iso", type=Path, default=None,
                        help="path to the user's own ISO (default: $RECVX_ISO or .game_iso/*.iso)")
    parser.add_argument("--out", type=Path, default=Path("data/raw"),
                        help="extraction root (default: data/raw)")
    parser.add_argument("--manifest", type=Path, default=Path("data/manifest.json"),
                        help="manifest output path (default: data/manifest.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="walk and hash-free catalogue only; write nothing")
    parser.add_argument("--force", action="store_true",
                        help="extract into a non-empty output directory")
    parser.add_argument("--no-iso-hash", action="store_true",
                        help="skip hashing the full ISO (saves a pass over ~2 GB)")
    parser.add_argument("--verify", action="store_true",
                        help="re-hash extracted files against the manifest and exit")
    args = parser.parse_args(argv)

    if args.verify:
        if not args.manifest.is_file():
            print(f"error: no manifest at {args.manifest}", file=sys.stderr)
            return 1
        return verify(args.out, args.manifest)

    iso_path = args.iso or default_iso(workspace)
    if iso_path is None:
        print(
            "error: no ISO given. Pass --iso <path> or place the disc image in .game_iso/",
            file=sys.stderr,
        )
        return 1
    if not iso_path.is_file():
        print(f"error: ISO not found: {iso_path}", file=sys.stderr)
        return 1

    if not args.dry_run:
        if args.out.exists() and any(args.out.iterdir()) and not args.force:
            print(
                f"error: {args.out} is not empty (use --force to extract over it)",
                file=sys.stderr,
            )
            return 1
        args.out.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(
        iso_path, args.out, hash_iso=not args.no_iso_hash, dry_run=args.dry_run
    )

    if args.dry_run:
        print("\n[dry-run] nothing written")
    else:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=False), encoding="utf-8"
        )

    totals = manifest["totals"]
    print(f"\nfiles      : {totals['files']}  ({totals['bytes']:,} bytes)")
    print(f"renamed    : {totals['renamed']}")
    print(f"by kind    : {totals['by_kind']}")
    boot = manifest["boot"]
    if boot.get("found"):
        print(f"boot       : {boot['iso_path']}  ->  {boot.get('elf', {}).get('machine')}")
    else:
        print(f"boot       : not resolved ({boot.get('reason')})")
    if manifest["warnings"]:
        print(f"warnings   : {len(manifest['warnings'])}")
        for warning in manifest["warnings"][:10]:
            print(f"  ! {warning}")
    if not args.dry_run:
        print(f"manifest   : {args.manifest}")
        print(f"output     : {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
