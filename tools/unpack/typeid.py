"""File-type identification for the PS2 disc tree.

Phase 1 only needs a *guess* for each extracted file, and it must be honest about how
strong that guess is. Tiers, strongest first:

  1. ``magic``      - the file starts with a known signature. Trustworthy.
  2. ``heuristic``  - the bytes match a structural pattern (text, offset table, ...).
  3. ``extension``  - nothing matched, so we fall back to the filename suffix.
  4. ``unknown``    - nothing matched and the suffix is unfamiliar.

Confidence is recorded in the manifest so Phase 2 knows which files still need a real
format spec instead of a guess.
"""

from __future__ import annotations

import os

# (signature, kind, label). Order matters: longest / most specific first.
MAGIC_TABLE: tuple[tuple[bytes, str, str], ...] = (
    (b"\x7fELF", "executable", "ELF executable"),
    (b"\x89PNG\r\n\x1a\n", "image", "PNG image"),
    (b"IPUM", "video", "PS2 IPU video"),
    (b"PSS", "video", "PS2 streamed video (PSS)"),
    (b"VAGp", "audio", "PS2 VAG audio"),
    (b"VAGi", "audio", "PS2 VAG audio (interleaved)"),
    (b"TIM2", "image", "PS2 TIM2 texture"),
    (b"SQ\x00", "archive", "SQ container (unverified magic)"),
    (b"\x00\x00\x01\xba", "video", "MPEG program stream"),
    (b"\x00\x00\x01\xb3", "video", "MPEG video stream"),
    (b"RIFF", "container", "RIFF container (WAV/AVI)"),
    (b"PK\x03\x04", "archive", "ZIP archive"),
    (b"DDS ", "image", "DirectDraw surface texture"),
    (b"\xff\xd8\xff", "image", "JPEG image"),
    (b"GIF8", "image", "GIF image"),
    (b"BM", "image", "BMP image"),
)

# Fallback for files whose contents told us nothing.
EXTENSION_KINDS: dict[str, tuple[str, str]] = {
    ".cnf": ("config", "PS2 boot configuration (SYSTEM.CNF)"),
    ".elf": ("executable", "ELF executable"),
    ".irx": ("module", "PS2 IOP module"),
    ".bmp": ("image", "BMP image"),
    ".png": ("image", "PNG image"),
    ".jpg": ("image", "JPEG image"),
    ".vag": ("audio", "PS2 VAG audio"),
    ".adpcm": ("audio", "PS2 ADPCM audio"),
    ".pss": ("video", "PS2 streamed video"),
    ".ipu": ("video", "PS2 IPU video"),
    ".pak": ("archive", "unidentified archive by extension"),
    ".dat": ("data", "unidentified data by extension"),
    ".bin": ("data", "unidentified binary by extension"),
    ".mdl": ("model", "unidentified model by extension"),
    ".msg": ("text", "unidentified message/text table by extension"),
    ".txt": ("text", "plain text"),
    ".s": ("data", "unidentified by extension"),
}

TEXT_SAMPLE = 512


def _looks_like_ascii_text(head: bytes) -> bool:
    """Printable-ASCII text check: no control bytes beyond tab/CR/LF, no NULs."""
    if not head:
        return False
    sample = head[:TEXT_SAMPLE]
    if b"\x00" in sample:
        return False
    printable = sum(1 for b in sample if 32 <= b < 127 or b in (9, 10, 13))
    return printable == len(sample)


def _looks_like_offset_table(head: bytes, file_size: int) -> bool:
    """Weak structural test for "table of contents + payload" archives.

    Interpreted as little-endian u32s: the first word is a plausible entry count
    (2..256), and most of the following words are offsets pointing past the table but
    inside the file. This is the shape of a PS2 archive header, and also of plenty of
    unrelated data, hence the low confidence.
    """
    if len(head) < 16 or file_size <= 0:
        return False
    count = int.from_bytes(head[0:4], "little")
    if not (2 <= count <= 256):
        return False
    need = 4 * min(count, 32)
    if len(head) < need:
        return False
    values = [int.from_bytes(head[i : i + 4], "little") for i in range(0, need, 4)]
    offsets = values[1:]
    if not offsets:
        return False
    table_end = need
    plausible = sum(1 for v in offsets if table_end <= v < file_size)
    return plausible >= max(1, len(offsets) // 2)


def identify(head: bytes, name: str, file_size: int = -1) -> tuple[str, str, str]:
    """Return ``(kind, label, confidence)`` for one file.

    `head` is the first few KB of the file (empty for zero-length files). A zero-length
    file is reported as such rather than being mislabelled by its extension.
    """
    if file_size == 0:
        return "empty", "zero-length file", "magic"

    for magic, kind, label in MAGIC_TABLE:
        if head.startswith(magic):
            return kind, label, "magic"

    if _looks_like_ascii_text(head):
        return "text", "ASCII text", "heuristic"

    if _looks_like_offset_table(head, file_size):
        return "archive", "offset-table container (unverified)", "heuristic"

    ext = os.path.splitext(name)[1].lower()
    if ext in EXTENSION_KINDS:
        kind, label = EXTENSION_KINDS[ext]
        return kind, label, "extension"

    return "unknown", "unidentified", "unknown"
