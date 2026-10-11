"""Bast pipeline - stage 1: `unpack` (ISO image -> file tree + manifest).

Modules:
    iso9660  - dependency-free ISO9660 (ECMA-119) reader
    typeid   - magic/heuristic file-type identification
    elfinfo  - minimal ELF header reader for the PS2 main executable
    extract  - CLI entry point (`python -m tools.unpack.extract`)
"""

__all__ = ["iso9660", "typeid", "elfinfo"]
