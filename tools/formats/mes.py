"""Message-bank (ALD) codec: parse, verify and edit PS2 text banks byte-exactly.

Layout (derived from the real disc; evidence in research/mes/spec.md):

    [u32 size][u32 count][count x u32 rel_offset][UTF-16LE message text]

A file holds one or more such sections back to back.  ``rel_offset[i]`` is the
byte distance from the *count* field to the start of message ``i``, so

    absolute_start[i] = (section_start + 4) + rel_offset[i]

and the layout fingerprint that identifies a section is
``rel_offset[0] == 4 + 4 * count`` (right after the offset array).  ``size`` is
the distance from the count field to the end of the section, i.e.
``4 + 4 * count + len(text)``.

Text encoding:
  * UTF-16LE units.
  * ``0xFFFF`` terminates a message.
  * other ``0xFFxx`` units are control codes (0xFF01 is a line break).
  * glyph shift: a text unit whose value is below 0x40 is stored with 0x20
    subtracted.  The stored byte's bit 6 acts as the "literal" flag: set means
    the unit is literal (e.g. 'B' = 0x42), clear means add 0x20 (e.g. 0x24 ->
    'D').  This is why "DUALSHOCK_" appears on disc as the low bytes
    24 35 21 2c 33 28 2f 23 2b.  The side effect is that characters below 0x40
    (space, digits, punctuation) are expressible too, as value - 0x20: a space is
    stored as 0x0000 and the digit 7 as 0x0017, both of which occur on disc.

Every byte outside a message span is preserved verbatim (section prefix and the
gaps between sections), so ``rebuild()`` reproduces the input byte for byte.
That is the Phase 2 bar: a format may only be injected into once decode ->
encode is byte-identical.

CLI::

    python -m tools.formats.mes info data/raw/SYSMES.ALD
    python -m tools.formats.mes decode data/raw/SYSMES.ALD --json out.json
    python -m tools.formats.mes roundtrip data/raw
    python -m tools.formats.mes census data/raw/SYSMES.ALD
    python -m tools.formats.mes edit data/raw/SYSMES.ALD --index 1 \
        --text "MISSILE{ff01}LAUNCHER" --out build/SYSMES.ALD
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import struct
import sys

TERMINATOR = 0xFFFF
SHIFT = 0x20
MAX_COUNT = 20000

RULE_THRESHOLD = "threshold"   # any unit < 0x40 is shifted  (confirmed on disc)
RULE_FIRST = "first"           # only a run's first unit is shifted (rejected)
RULE_NONE = "none"             # no shifting at all (rejected)
RULES = (RULE_THRESHOLD, RULE_FIRST, RULE_NONE)


# --------------------------------------------------------------------- primitives
def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def p32(value: int) -> bytes:
    return struct.pack("<I", value)


def is_control(unit: int) -> bool:
    """True for 0xFFxx units other than the 0xFFFF terminator."""
    return unit != TERMINATOR and (unit >> 8) == 0xFF


def bytes_to_units(blob: bytes) -> tuple[list[int], bytes]:
    """Split bytes into u16 units plus any odd trailing byte."""
    n = len(blob) // 2
    return list(struct.unpack_from("<%dH" % n, blob, 0)), blob[n * 2:]


def units_to_bytes(units: list[int], tail: bytes = b"") -> bytes:
    return struct.pack("<%dH" % len(units), *units) + tail


def decode_units(units: list[int], rule: str = RULE_THRESHOLD) -> list[int]:
    """Stored units -> visible units (the glyph shift, inverted)."""
    out = []
    for i, value in enumerate(units):
        if rule == RULE_THRESHOLD and value < 0x40:
            value += SHIFT
        elif rule == RULE_FIRST and i == 0 and value < 0x40:
            value += SHIFT
        out.append(value)
    return out


def encode_units(units: list[int], rule: str = RULE_THRESHOLD) -> list[int]:
    """Visible units -> canonical stored units (inverse of decode_units).

    The mapping is not injective on disc (0x24 and 0x44 both mean 'D'), so this
    picks the canonical form: a visible unit in 0x20..0x5F is stored as value - 0x20
    (bit 6 of the stored unit is clear).
    """
    out = []
    for i, value in enumerate(units):
        if rule == RULE_THRESHOLD and 0x20 <= value <= 0x5F:
            value -= SHIFT
        elif rule == RULE_FIRST and i == 0 and 0x20 <= value <= 0x5F:
            value -= SHIFT
        out.append(value)
    return out


def merge_stored_units(stored: list[int], visible: list[int],
                       rule: str = RULE_THRESHOLD) -> list[int]:
    """Re-encode an edit position by position, keeping untouched stored bytes.

    ``stored`` and ``visible`` must describe the same number of units. Where the
    original stored unit already decodes to the new visible unit, the original byte is
    kept verbatim; only a position whose letter actually changed is re-encoded.

    This is what stops a one-character edit from rewriting a whole message's bank-A
    glyphs (stored 0x41 for 'A') into the canonical bank B (0x21). Both render the same
    text, so the text view cannot tell them apart -- but 592 of the 677 messages on the
    real disc carry bank-A units, and re-encoding rewrote bytes the author never
    touched (measured on the real disc: 8 of message 0's 10 stored units
    change, i.e. 16 of its 20 text bytes).
    """
    canonical = encode_units(visible, rule)
    decoded = decode_units(stored, rule)
    return [old if old_visible == new_visible else new
            for old, old_visible, new_visible, new
            in zip(stored, decoded, visible, canonical)]


def units_to_text(units: list[int]) -> str:
    return "".join(chr(u) if 0x20 <= u < 0x7F else "\\u%04x" % u for u in units)


def text_to_units(text: str) -> list[int]:
    """Authoring text -> visible units; ``{xxxx}`` becomes a raw unit."""
    units: list[int] = []
    i = 0
    while i < len(text):
        if text[i] == "{" and "}" in text[i:]:
            end = text.index("}", i)
            try:
                units.append(int(text[i + 1:end], 16))
                i = end + 1
                continue
            except ValueError:
                pass
        units.append(ord(text[i]))
        i += 1
    return units


def render(units: list[int], rule: str = RULE_THRESHOLD) -> str:
    """Message units -> authoring text (stops at the terminator)."""
    out: list[str] = []
    run: list[int] = []

    def flush() -> None:
        if run:
            out.append(units_to_text(decode_units(run, rule)))
            del run[:]

    for unit in units:
        if unit == TERMINATOR:
            flush()
            break
        if is_control(unit):
            flush()
            out.append("{%04x}" % unit)
        else:
            run.append(unit)
    flush()
    return "".join(out)


# ----------------------------------------------------------------------- section
class Section:
    """One ``[size][count][offsets][text]`` block."""

    def __init__(self, data: bytes, start: int) -> None:
        self.start = start
        self.size = u32(data, start)
        self.count = u32(data, start + 4)
        self.count_off = start + 4          # offsets are relative to this field
        self.text_start = start + 8 + 4 * self.count
        self.end = self.count_off + self.size
        # Bounds-check BEFORE unpacking: a bogus count at a non-header offset would
        # otherwise read past EOF and raise struct.error out of the constructor.
        if not (1 <= self.count <= MAX_COUNT) or self.text_start > len(data):
            self.offsets = []
            self.count = 0
            return
        self.offsets = [u32(data, start + 8 + 4 * i) for i in range(self.count)]

    def valid(self, data: bytes) -> bool:
        filesize = len(data)
        if not (1 <= self.count <= MAX_COUNT):
            return False
        if self.text_start > filesize:
            return False
        if self.end > filesize or self.end <= self.text_start:
            return False
        if self.size <= 0:
            return False
        # the fingerprint: the first offset lands exactly after the offset array
        if self.offsets[0] != 4 + 4 * self.count:
            return False
        if any(b <= a for a, b in zip(self.offsets, self.offsets[1:])):
            return False
        if self.offsets[-1] >= self.size:
            return False
        return self.has_terminator(data)

    def has_terminator(self, data: bytes) -> bool:
        """True if any message span holds a 0xFFFF terminator.

        Compared as 16-bit units, not bytes: ``0xFFFF in b"..."`` raises because
        ``in`` on bytes wants a 0..255 value.
        """
        for a, b in self.spans():
            n = (b - a) // 2
            if n <= 0:
                continue
            a += a % 2
            units, _ = bytes_to_units(data[a:a + n * 2])
            if TERMINATOR in units:
                return True
        return False

    def spans(self) -> list[tuple[int, int]]:
        out = []
        for i, off in enumerate(self.offsets):
            a = self.count_off + off
            b = (self.count_off + self.offsets[i + 1]) if i + 1 < self.count else self.end
            out.append((a, b))
        return out

    def validate(self, data: bytes) -> bool:
        return self.valid(data)


def find_sections(data: bytes) -> list[Section]:
    """Scan for section headers using the layout fingerprint, left to right."""
    sections: list[Section] = []
    pos = 0
    limit = len(data)
    while pos + 12 <= limit:
        sec = Section(data, pos)
        if sec.validate(data):
            sections.append(sec)
            pos = sec.end
        else:
            pos += 2
    return sections


# ------------------------------------------------------------------------ codec
class Message:
    """One message span: raw stored units plus its byte range in the file."""

    __slots__ = ("section", "index", "start", "end", "units", "_tail")

    def __init__(self, section: int, index: int, start: int, end: int, blob: bytes) -> None:
        self.section = section
        self.index = index
        self.start = start
        self.end = end
        self.units, self._tail = bytes_to_units(blob)

    def raw(self) -> bytes:
        return units_to_bytes(self.units, self._tail)

    def text(self, rule: str = RULE_THRESHOLD) -> str:
        return render(self.units, rule)

    def visible_units(self, rule: str = RULE_THRESHOLD) -> list[int]:
        return decode_units(self.units, rule)

    def set_text(self, text: str, rule: str = RULE_THRESHOLD) -> None:
        """Replace the text part, keeping the terminator and any trailing units.

        Characters the edit does not change keep the stored bytes the disc gave them
        (see ``merge_stored_units``): a 'A' stored as bank-A 0x41 stays 0x41 when the
        edit touches a different character, instead of being canonicalised to bank-B
        0x21. Only positions whose *visible* unit changed are re-encoded.

        When the text changes length the positions no longer line up with the old
        stored units, so bank preservation is dropped for that message and the whole
        text is encoded canonically. An insertion or deletion shifts every later
        position, so a naive index-wise comparison would pair the wrong units; a
        length change is a genuine rewrite of the span either way.
        """
        visible = text_to_units(text)
        cut = self.units.index(TERMINATOR) if TERMINATOR in self.units else len(self.units)
        original = self.units[:cut]
        if len(original) == len(visible):
            stored = merge_stored_units(original, visible, rule)
        else:
            stored = encode_units(visible, rule)
        self.units = stored + self.units[cut:]
        self._tail = b""

    def set_visible_units(self, units: list[int]) -> None:
        """Replace the text part with visible units verbatim ('d' = 0x64)."""
        stored = [u if is_control(u) or u == TERMINATOR else u for u in units]
        cut = self.units.index(TERMINATOR) if TERMINATOR in self.units else len(self.units)
        self.units = stored + self.units[cut:]
        self._tail = b""


class Mes:
    """A parsed message bank: sections, messages, and verbatim filler between them."""

    def __init__(self, path: str, data: bytes) -> None:
        self.path = path
        self.data = data
        self.sections = find_sections(data)
        self.messages: list[Message] = []
        for si, sec in enumerate(self.sections):
            for i, (a, b) in enumerate(sec.spans()):
                self.messages.append(Message(si, i, a, b, data[a:b]))
        self.prefix = data[:self.sections[0].start] if self.sections else b""
        self.gaps: list[bytes] = []
        for i, sec in enumerate(self.sections):
            nxt = self.sections[i + 1].start if i + 1 < len(self.sections) else len(data)
            self.gaps.append(data[sec.end:nxt])

    # ---- views -------------------------------------------------------------
    def section_messages(self, si: int) -> list[Message]:
        return [m for m in self.messages if m.section == si]

    def texts(self, rule: str = RULE_THRESHOLD) -> list[str]:
        return [m.text(rule) for m in self.messages]

    def control_census(self) -> dict[str, int]:
        census: dict[str, int] = {}
        for m in self.messages:
            for u in m.units:
                if is_control(u):
                    key = "%04x" % u
                    census[key] = census.get(key, 0) + 1
        return dict(sorted(census.items(), key=lambda kv: -kv[1]))

    def printable_ratio(self, rule: str) -> float:
        good = total = 0
        for m in self.messages:
            for u in m.visible_units(rule):
                if u == TERMINATOR or is_control(u):
                    continue
                total += 1
                if 0x20 <= u < 0x7F:
                    good += 1
        return good / total if total else 0.0

    # ---- encoding ----------------------------------------------------------
    def rebuild(self) -> bytes:
        """Re-emit the file, recomputing every size and offset from scratch.

        Byte equality with the input is the Phase 2 exit criterion.
        """
        out = bytearray(self.prefix)
        for si, _sec in enumerate(self.sections):
            msgs = self.section_messages(si)
            body = b"".join(m.raw() for m in msgs)
            count = len(msgs)
            offsets = []
            acc = 0
            for m in msgs:
                offsets.append(4 + 4 * count + acc)
                acc += len(m.raw())
            size = 4 + 4 * count + acc
            out += p32(size) + p32(count)
            out += b"".join(p32(o) for o in offsets)
            out += body
            out += self.gaps[si]
        return bytes(out)


def verify_roundtrip(path: str) -> list[str]:
    """Return a list of problems; empty means the format round-tripped exactly."""
    with open(path, "rb") as fp:
        data = fp.read()
    mes = Mes(path, data)
    problems: list[str] = []
    if not mes.sections:
        problems.append("no sections found")
        return problems
    rebuilt = mes.rebuild()
    if rebuilt != data:
        problems.append(f"rebuild differs: {len(data)} -> {len(rebuilt)} bytes")
        for i, (a, b) in enumerate(zip(data, rebuilt)):
            if a != b:
                problems.append(f"first difference at byte {i} (0x{i:x})")
                break
    covered = sum(1 for m in mes.messages for _ in m.raw())
    total = sum(s.end - s.start for s in mes.sections)
    if covered > total:
        problems.append("message spans exceed section bytes")
    return problems


# --------------------------------------------------------------------- commands
def cmd_info(path: str) -> int:
    data = open(path, "rb").read()
    mes = Mes(path, data)
    print(f"== {path}  size={len(data):,}")
    print(f"   sections={len(mes.sections)}  messages={len(mes.messages)}")
    for si, sec in enumerate(mes.sections):
        print(f"   section {si}: start=0x{sec.start:x} size={sec.size:,} count={sec.count} "
              f"text@0x{sec.text_start:x} end=0x{sec.end:x} first_rel={sec.offsets[0]}")
    print(f"   controls: {mes.control_census()}")
    for rule in RULES:
        print(f"   printable ratio [{rule:<9}] = {mes.printable_ratio(rule):.4f}")
    for m in mes.messages[:6]:
        print(f"     [{m.index:4}] {m.text()}")
    return 0


def cmd_decode(path: str, json_out: str | None, rule: str) -> int:
    data = open(path, "rb").read()
    mes = Mes(path, data)
    texts = mes.texts(rule)
    if json_out:
        os.makedirs(os.path.dirname(json_out) or ".", exist_ok=True)
        with open(json_out, "w", encoding="utf-8") as fp:
            json.dump({"path": path, "count": len(texts),
                       "messages": [{"index": i, "text": t} for i, t in enumerate(texts)]},
                      fp, indent=2, ensure_ascii=False)
        print(f"wrote {len(texts)} messages to {json_out}")
    else:
        for i, t in enumerate(texts):
            print(f"[{i:4}] {t}")
    return 0


def cmd_roundtrip(target: str) -> int:
    paths = (sorted(glob.glob(os.path.join(target, "*.ALD"))) if os.path.isdir(target)
             else sorted(glob.glob(target)))
    if not paths:
        print(f"no ALD files matched {target!r}")
        return 1
    failures = 0
    for p in paths:
        problems = verify_roundtrip(p)
        if problems:
            failures += 1
        status = "OK " if not problems else "FAIL"
        data = open(p, "rb").read()
        mes = Mes(p, data)
        print(f"  {status} {os.path.basename(p):<16} bytes={len(data):>9,} "
              f"sections={len(mes.sections)} messages={len(mes.messages):>4} "
              f"{'; '.join(problems[:2])}")
    print(f"{len(paths) - failures}/{len(paths)} files round-trip byte-exactly")
    return 1 if failures else 0


def cmd_census(path: str) -> int:
    data = open(path, "rb").read()
    mes = Mes(path, data)
    print(f"== shift-rule census on {path} ({len(mes.messages)} messages)")
    for rule in RULES:
        print(f"   {rule:<10} printable ratio = {mes.printable_ratio(rule):.4f}")
    print("   evidence strings found per rule:")
    for rule in RULES:
        blob = "\n".join(mes.texts(rule))
        hits = [s for s in ("ROCKET", "LAUNCHER", "DUALSHOCK_", "BUTTON", "CONTINUE",
                            "ANALOG", "MEMORY", "FORMATTED", "START", "PRESS")
                if s in blob]
        print(f"     {rule:<10} {len(hits):>2} hits: {', '.join(hits)}")
    return 0


def cmd_edit(path: str, index: int, text: str, out: str | None, rule: str) -> int:
    data = open(path, "rb").read()
    mes = Mes(path, data)
    if not (0 <= index < len(mes.messages)):
        print(f"index {index} out of range (0..{len(mes.messages) - 1})")
        return 1
    msg = mes.messages[index]
    before = msg.text(rule)
    msg.set_text(text, rule)
    rebuilt = mes.rebuild()
    target = out or path
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "wb") as fp:
        fp.write(rebuilt)
    print(f"message {index}: {before!r} -> {msg.text(rule)!r}")
    print(f"wrote {target}  {len(data):,} -> {len(rebuilt):,} bytes")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.formats.mes",
                                 description="PS2 message-bank (ALD) codec",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_info = sub.add_parser("info"); p_info.add_argument("path")
    p_dec = sub.add_parser("decode")
    p_dec.add_argument("path")
    p_dec.add_argument("--json", dest="json_out")
    p_dec.add_argument("--rule", default=RULE_THRESHOLD, choices=RULES)
    p_rt = sub.add_parser("roundtrip"); p_rt.add_argument("target")
    p_cen = sub.add_parser("census"); p_cen.add_argument("path")
    p_ed = sub.add_parser("edit")
    p_ed.add_argument("path")
    p_ed.add_argument("--index", type=int, required=True)
    p_ed.add_argument("--text", required=True)
    p_ed.add_argument("--out")
    p_ed.add_argument("--rule", default=RULE_THRESHOLD, choices=RULES)
    args = ap.parse_args(argv)
    if args.cmd == "info":
        return cmd_info(args.path)
    if args.cmd == "decode":
        return cmd_decode(args.path, args.json_out, args.rule)
    if args.cmd == "roundtrip":
        return cmd_roundtrip(args.target)
    if args.cmd == "census":
        return cmd_census(args.path)
    return cmd_edit(args.path, args.index, args.text, args.out, args.rule)


if __name__ == "__main__":
    raise SystemExit(main())
