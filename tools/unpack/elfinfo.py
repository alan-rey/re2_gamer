"""Minimal ELF32 header reader, used to summarise the PS2 main executable.

Phase 1's deliverable is "the main executable plus every archive, catalogued". That
means the manifest has to say *which* file is the PS2 boot executable and record its
entry point and load segments, so Phase 2 can point Ghidra / capstone at the right
thing without re-deriving it.

Scope: ELF32 little-endian (what the R5900 toolchain emits). No section parsing, no
symbol tables - program headers are all the pipeline needs at this stage.
"""

from __future__ import annotations

import struct

ELF_MAGIC = b"\x7fELF"

ELF_TYPES = {0: "NONE", 1: "REL", 2: "EXEC", 3: "DYN", 4: "CORE"}

ELF_MACHINES = {
    0: "No machine",
    3: "Intel 80386",
    8: "MIPS",
    20: "PowerPC",
    40: "ARM",
    62: "x86-64",
    183: "AArch64",
}

PROGRAM_HEADER_TYPES = {
    0: "NULL",
    1: "LOAD",
    2: "DYNAMIC",
    3: "INTERP",
    4: "NOTE",
    5: "SHLIB",
    6: "PHDR",
    7: "TLS",
    0x70000000: "PT_MIPS_REGINFO",
    0x70000001: "PT_MIPS_OPTIONS",
    0x70000002: "PT_MIPS_ABIFLAGS",
    0x70000003: "PT_MIPS_RTPROC",
}

EHDR32_SIZE = 52
PHDR32_SIZE = 32


def is_elf(head: bytes) -> bool:
    return head[:4] == ELF_MAGIC


def is_ps2_boot_name(name: str) -> bool:
    """PS2 boot executables are named SLUS_ / SLES_ / SLPS_ / SLPM_ / SCUS_ / SCES_."""
    base = name.rsplit("/", 1)[-1].upper()
    return base[:4] in {"SLUS", "SLES", "SLPS", "SLPM", "SCUS", "SCES", "SLED"}


def summarize(fp, base_offset: int = 0) -> dict:
    """Read the ELF header + program headers from an open binary file.

    ``fp`` must be seekable. ``base_offset`` is where the ELF starts inside ``fp``: the
    Phase 1 caller hands us the whole disc image with the file pointer parked at the
    executable's extent, so all header/segment offsets are relative to that base rather
    than to the start of the file. Raises ValueError when the bytes at ``base_offset``
    are not a supported ELF32.
    """
    fp.seek(base_offset)
    ident = fp.read(16)
    if len(ident) < 16 or ident[:4] != ELF_MAGIC:
        raise ValueError("not an ELF file")
    elf_class = ident[4]
    data_encoding = ident[5]
    if elf_class != 1:
        raise ValueError(f"unsupported ELF class {elf_class} (only ELF32 is handled)")
    if data_encoding not in (1, 2):
        raise ValueError(f"unsupported ELF data encoding {data_encoding}")

    endian = "<" if data_encoding == 1 else ">"
    rest = fp.read(EHDR32_SIZE - 16)
    if len(rest) < EHDR32_SIZE - 16:
        raise ValueError("truncated ELF header")
    (
        e_type,
        e_machine,
        e_version,
        e_entry,
        e_phoff,
        e_shoff,
        e_flags,
        e_ehsize,
        e_phentsize,
        e_phnum,
        e_shentsize,
        e_shnum,
        e_shstrndx,
    ) = struct.unpack(endian + "HHIIIIIHHHHHH", rest)

    program_headers: list[dict] = []
    if e_phoff and e_phnum and e_phentsize == PHDR32_SIZE:
        fp.seek(base_offset + e_phoff)
        raw = fp.read(e_phentsize * e_phnum)
        usable = len(raw) // PHDR32_SIZE
        for index in range(usable):
            chunk = raw[index * PHDR32_SIZE : (index + 1) * PHDR32_SIZE]
            p_type, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_flags, p_align = (
                struct.unpack(endian + "IIIIIIII", chunk)
            )
            program_headers.append(
                {
                    "index": index,
                    "type": PROGRAM_HEADER_TYPES.get(p_type, f"0x{p_type:08x}"),
                    "type_raw": p_type,
                    "offset": p_offset,
                    "vaddr": p_vaddr,
                    "paddr": p_paddr,
                    "filesz": p_filesz,
                    "memsz": p_memsz,
                    "flags": p_flags,
                    "align": p_align,
                }
            )

    return {
        "class": "ELF32",
        "endianness": "little" if data_encoding == 1 else "big",
        "type": ELF_TYPES.get(e_type, str(e_type)),
        "machine": ELF_MACHINES.get(e_machine, f"0x{e_machine:x}"),
        "machine_raw": e_machine,
        "entry": e_entry,
        "entry_hex": f"0x{e_entry:08x}",
        "flags": e_flags,
        "phoff": e_phoff,
        "phentsize": e_phentsize,
        "phnum": e_phnum,
        "shoff": e_shoff,
        "shnum": e_shnum,
        "shstrndx": e_shstrndx,
        "program_headers": program_headers,
    }
