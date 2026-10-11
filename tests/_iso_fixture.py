"""Build a small synthetic ISO9660 image for tests.

Real Phase 1 runs against a 1.8 GB copyrighted disc image that must never enter the
repository or CI. The tests therefore construct their own minimal but *structurally
valid* ISO9660 volume (primary volume descriptor + terminator + directory records with
correct both-endian fields), so `tools/unpack/iso9660.py` and the `extract` CLI can be
verified offline and deterministically.

The fixture deliberately includes the shapes that matter:

  * a nested directory,
  * a plain ASCII text file,
  * an offset-table-like binary (archive heuristic),
  * a VAG-magic audio file,
  * a zero-length file,
  * a PS2 ``SYSTEM.CNF``,
  * the ELF32 boot executable that ``SYSTEM.CNF`` names, so the boot-resolution and
    ELF-header paths in `extract.detect_boot` / `elfinfo.summarize` are exercised too.
"""

from __future__ import annotations

import struct

SECTOR = 2048

# Contents of the fixture volume. Paths use '/' and are relative to the root.
SYSTEM_CNF = b"BOOT2 = cdrom0:\\SLUS_215.02;1\r\nVER = 1.10\r\nVMODE = NTSC\r\n"
HELLO_TXT = b"Hello, world!\nSecond line.\n"
# count=4, then offsets that land inside the file -> triggers the archive heuristic.
BLOB_DAT = (
    (4).to_bytes(4, "little")
    + (16).to_bytes(4, "little")
    + (100).to_bytes(4, "little")
    + (150).to_bytes(4, "little")
    + b"\x00" * (512 - 16)
)
VAG_VAG = b"VAGp" + b"\x00" * 60
EMPTY_DAT = b""


def _build_boot_elf() -> bytes:
    """A minimal but structurally valid ELF32/little-endian MIPS ``EXEC``.

    This is the shape of a real PS2 boot executable, reduced to what
    `elfinfo.summarize` actually reads: the 52-byte ELF header plus one PT_LOAD program
    header. ``p_offset`` is 0 so the segment covers the headers themselves, which is how
    PS2 boot ELFs are actually laid out, and the entry point is the conventional
    0x00100000. A small zeroed body stands in for the code.
    """
    body = b"\x00" * 256
    elf_header = (
        b"\x7fELF"
        + bytes([1, 1, 1, 0])  # ELF32, little-endian, version 1, SYSV ABI
        + b"\x00" * 8  # padding to 16 bytes
        + struct.pack(
            "<HHIIIIIHHHHHH",
            2,  # e_type  = EXEC
            8,  # e_machine = MIPS
            1,  # e_version
            0x00100000,  # e_entry
            52,  # e_phoff (immediately after the ELF header)
            0,  # e_shoff (no section headers)
            0x20924001,  # e_flags (typical R5900/MIPS flags)
            52,  # e_ehsize
            32,  # e_phentsize
            1,  # e_phnum
            0,  # e_shentsize
            0,  # e_shnum
            0,  # e_shstrndx
        )
    )
    total = len(elf_header) + 32 + len(body)
    program_header = struct.pack(
        "<IIIIIIII",
        1,  # p_type = LOAD
        0,  # p_offset
        0x00100000,  # p_vaddr
        0x00100000,  # p_paddr
        total,  # p_filesz
        total,  # p_memsz
        5,  # p_flags = R|X
        4,  # p_align
    )
    return elf_header + program_header + body


BOOT_ELF = _build_boot_elf()


def _both16(value: int) -> bytes:
    return value.to_bytes(2, "little") + value.to_bytes(2, "big")


def _both32(value: int) -> bytes:
    return value.to_bytes(4, "little") + value.to_bytes(4, "big")


def dir_record(name: bytes, lba: int, size: int, is_dir: bool) -> bytes:
    """One directory record, padded to an even length as the standard requires."""
    flags = 0x02 if is_dir else 0x00
    length = 33 + len(name)
    if length % 2:
        length += 1
    rec = bytearray(length)
    rec[0] = length
    rec[1] = 0  # no extended attribute record
    rec[2:10] = _both32(lba)
    rec[10:18] = _both32(size)
    rec[18:25] = b"\x00" * 7  # recording date/time
    rec[25] = flags
    rec[26] = 0  # file unit size
    rec[27] = 0  # interleave gap size
    rec[28:32] = _both16(1)  # volume sequence number
    rec[32] = len(name)
    rec[33 : 33 + len(name)] = name
    return bytes(rec)


def _dir_block(records: list[bytes]) -> bytes:
    """Lay out directory records, never letting one straddle a sector boundary."""
    out = bytearray()
    for rec in records:
        if (len(out) % SECTOR) + len(rec) > SECTOR:
            out += b"\x00" * (SECTOR - (len(out) % SECTOR))
        out += rec
    if len(out) % SECTOR:
        out += b"\x00" * (SECTOR - (len(out) % SECTOR))
    return bytes(out)


# Fixed layout: LBA 16 PVD, 17 terminator, 18 root dir, 19 DATA dir, 20+ payloads.
LBA_ROOT = 18
LBA_DATA = 19
LBA_CNF = 20
LBA_HELLO = 21
LBA_BLOB = 22
LBA_VAG = 23
LBA_BOOT = 24
TOTAL_SECTORS = 25


def build_iso(path) -> dict:
    """Write the synthetic image to `path`. Returns a description of its contents."""
    root_records = [
        dir_record(b"\x00", LBA_ROOT, SECTOR, True),
        dir_record(b"\x01", LBA_ROOT, SECTOR, True),
        dir_record(b"DATA", LBA_DATA, SECTOR, True),
        dir_record(b"SYSTEM.CNF;1", LBA_CNF, len(SYSTEM_CNF), False),
        dir_record(b"BLOB.DAT;1", LBA_BLOB, len(BLOB_DAT), False),
        dir_record(b"VAGTEST.VAG;1", LBA_VAG, len(VAG_VAG), False),
        dir_record(b"EMPTY.DAT;1", 0, 0, False),
        dir_record(b"SLUS_215.02;1", LBA_BOOT, len(BOOT_ELF), False),
    ]
    data_records = [
        dir_record(b"\x00", LBA_DATA, SECTOR, True),
        dir_record(b"\x01", LBA_ROOT, SECTOR, True),
        dir_record(b"HELLO.TXT;1", LBA_HELLO, len(HELLO_TXT), False),
    ]

    pvd = bytearray(SECTOR)
    pvd[0] = 1  # primary volume descriptor
    pvd[1:6] = b"CD001"
    pvd[6] = 1
    pvd[40:72] = b"TEST_VOLUME".ljust(32, b" ")
    pvd[80:88] = _both32(TOTAL_SECTORS)
    pvd[128:132] = SECTOR.to_bytes(4, "little")  # logical block size
    root = dir_record(b"\x00", LBA_ROOT, SECTOR, True)
    pvd[156 : 156 + len(root)] = root

    terminator = bytearray(SECTOR)
    terminator[0] = 255
    terminator[1:6] = b"CD001"
    terminator[6] = 1

    sectors = {16: bytes(pvd), 17: bytes(terminator)}
    sectors[LBA_ROOT] = _dir_block(root_records)
    sectors[LBA_DATA] = _dir_block(data_records)
    sectors[LBA_CNF] = SYSTEM_CNF.ljust(SECTOR, b"\x00")
    sectors[LBA_HELLO] = HELLO_TXT.ljust(SECTOR, b"\x00")
    sectors[LBA_BLOB] = BLOB_DAT.ljust(SECTOR, b"\x00")
    sectors[LBA_VAG] = VAG_VAG.ljust(SECTOR, b"\x00")
    sectors[LBA_BOOT] = BOOT_ELF.ljust(SECTOR, b"\x00")

    with open(path, "wb") as fp:
        for lba in range(TOTAL_SECTORS):
            fp.write(sectors.get(lba, b"\x00" * SECTOR))

    return {
        "path": str(path),
        "files": {
            "SYSTEM.CNF": SYSTEM_CNF,
            "BLOB.DAT": BLOB_DAT,
            "VAGTEST.VAG": VAG_VAG,
            "EMPTY.DAT": EMPTY_DAT,
            "SLUS_215.02": BOOT_ELF,
            "DATA/HELLO.TXT": HELLO_TXT,
        },
        "dirs": ["DATA"],
        "boot_file": "SLUS_215.02",
    }


if __name__ == "__main__":  # pragma: no cover - manual smoke use
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "test_fixture.iso"
    info = build_iso(target)
    print(f"wrote {info['path']} with {len(info['files'])} files")
