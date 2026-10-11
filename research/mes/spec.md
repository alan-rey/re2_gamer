# Format: MES (message bank / text table, `.ALD`)

Status: **spec written + round-trip proven** (Phase 2, read *and* write path).
Owner module: `tools/formats/mes.py`.

## Why this format now

Phase 2 lists "text tables (dialogue, file notes) and their encoding + font constraints"
as a format family we must understand before touching it, and the roadmap's milestone
ordering is explicit: *formats before story*, because the story has to be shaped by what
the engine can express. `SYSMES.ALD` is the system message bank — the weapon names, menu
prompts and the "NOT USABLE" style feedback the player reads constantly. If we cannot
re-emit it byte-for-byte, no later phase can safely change a single word of UI text.

The sibling file on the disc, `SYSEFF.ALD`, is **not** a message bank (see "What this
format is not", below). Exactly one of the two loose `.ALD` files is text.

## Scope: read now, write proven, injection later

`SYSMES.ALD` is a de-facto engine-private table with no normative specification, so every
claim below is **measured from the user's own disc** and re-derived mechanically by
`tests/test_mes.py`.

The module implements both directions. `Mes.rebuild()` is the write path, proven
byte-exact against the real bank. It is deliberately a *no-change* repacker: it rebuilds
a bank that is bit-identical to the input. Editing a message is a separate, opt-in path
(`Message.set_text()` / `Message.set_visible_units()`) because the text view is lossy —
see rule 5.

## Layout

Little-endian throughout. A file holds **N sections**, concatenated, optionally followed
by gap bytes.

Per section:

| Offset | Size | Field |
|---|---|---|
| 0 | 4 | section size in bytes, `S`, including this header |
| 4 | 4 | message count `C` |
| 8 | 4 x `C` | `C` offsets, ascending |
| `4 + 4*C` | var | message bodies, packed back to back |
| `S` | var | (file level) gap bytes before the next section / EOF |

### The rules of the addressing scheme

1. **Offsets are measured from the count field, not from the section start.** The origin
   of every stored offset is the byte *after* the `size` field — i.e. the count field.
   `size` is the section's full length including its own header.

2. **Therefore the first offset is a constant given the count:** `offsets[0] == 4 + 4*C`.
   This is the parser's fingerprint — a position is a section start only if the four
   bytes there are a plausible size *and* the count's implied table fits *and*
   `offsets[0]` equals exactly `4 + 4*C`. Checked against the real bank:
   section 0 (`C=183`) has `first_rel=736 = 4 + 4*183`; section 1 (`C=494`) has
   `first_rel=1980 = 4 + 4*494`.

3. **Section size is recomputed on write, which is what makes editing possible.**
   `size = 4 + 4*C + sum(len(body))`, and each offset is re-emitted as
   `4 + 4*C + sum(previous body lengths)`. In a no-change round-trip this reproduces the
   original numbers exactly; when one message grows, every later offset and the section
   size stay consistent instead of pointing into the middle of the previous message.

4. **Bytes we did not interpret are preserved verbatim, never regenerated.** The message
   bodies are kept as their **stored code units**, so `Message.raw()` returns the original
   bytes and the trailing gap after the last section (`gaps == [0, 4]`) is written back
   unmodified. Regenerating the gap as zeros is the classic silent corruption. Because
   messages tile the section contiguously, there are no inter-message gaps to preserve.

5. **The visible-text view is lossy; the stored view is not.** The bank encodes characters
   in two banks: a unit below `0x40` is a bank-B glyph stored `0x20` lower than its ASCII
   value (`'N'` is stored as `0x2e`), and everything else is literal. Decoding is a
   threshold test (`unit < 0x40 -> unit + 0x20`). That test is *not* invertible for the 32 stored values in `0x40-0x5f`: `'N'`
   stored as `0x2e` decodes to `'N'` and re-encodes as bank-A `0x4e`. Stored `0x00-0x1f`
   *is* recoverable, because the encoder lowers any visible unit in `0x20-0x5f` by `0x20`
   (a space stored as `0x00` reads as `0x20` and writes back as `0x00`). The real bank
   keeps 30,093 units inside the ambiguous band across 592 of its 677 messages, so this
   is the common case, not a corner. A no-change `rebuild()` is immune (it writes
   stored units back untouched), but anything that round-trips `text -> set_text -> rebuild`
   can rewrite characters the author never touched. **Injection must edit stored units at
   the message level, not re-encode the visible text wholesale.**
   `tests/test_mes.py` asserts both halves of this: the lossiness, and that the no-change
   rebuild still round-trips exactly.

### Measured shape of the real bank

| File | Size (bytes) | Sections | Messages | Gaps | Round-trip |
|---|---:|---:|---:|---|---|
| `SYSMES.ALD` | 89,088 | 2 | 677 | `[0, 4]` | byte-exact |

| Section | Start | Size | Count | Text begins | First offset |
|---|---:|---:|---:|---:|---:|
| 0 | `0x0` | 5,452 | 183 | `0x2e4` | 736 |
| 1 | `0x1550` | 83,624 | 494 | `0x1d10` | 1980 |

Control-unit census (inline formatting, not text):

| Unit | Count | Reading |
|---|---:|---|
| `ff01` | 6,090 | line break (most common by far) |
| `ff00` | 1,777 | second-most-common separator |
| `ff02` | 99 | |
| `ff04` | 24 | |
| `ff03` | 4 | |

`0xffff` is the message terminator; it closes a body and is stripped from the text view.

### Glyph-rule evidence (why the threshold rule)

The shift rule was not guessed, it was **selected by printable-ASCII ratio** over the real
bank:

| Rule | Printable ratio | Sample decode of message 0 |
|---|---:|---|
| `threshold` (`< 0x40` -> `+ 0x20`) | **0.9998** | `NOT{ff01}USABLE` |
| `first` (shift only the first unit) | 0.9503 | `NOT{ff01}USABLE` |
| `none` (no shift) | 0.9500 | `.OT{ff01}USABLE` |

The two alternatives agree on the first character and then leave the bank-B glyphs sitting
in the control range, which is why they score ~0.95. The threshold rule turns `0x2e` back
into `'N'` and every other bank-B unit with it. Real decoded content confirms it is
recovering text and not noise: `ROCKET{ff01}LAUNCHER`, `ASSAULT{ff01}RIFLE`,
`SNIPER{ff01}RIFLE`, `SHOTGUN`, `HANDGUN` — a weapon-selection menu.

## What this format is not (the `SYSEFF.ALD` false negative)

`roundtrip data/raw` reports **1/2 files round-trip byte-exactly**, and that is the
expected result, not a bug. `SYSEFF.ALD` is a *model/skeleton* file, not a text bank:

- Its first eight bytes are `a8 04 00 00 4d 44 4c bc` — size 1,192 followed by the ASCII
  magic **`MDL`**, where a message bank would carry a message count.
- Its section fingerprint fails immediately: byte 8 is `0x40` (a nominal count of 64)
  which would require `offsets[0] == 260`, but the file holds `1072` there.
- Its only printable runs are `Shell_1` and `yakyo_1` — bone/part names, not localised UI
  text.

So `mes.find_sections()` returning `[]` for it is **correct rejection**. A parser that
invented a section here would be reading structure into binary geometry data. This is
recorded as a test (`test_real_syseff_is_not_a_message_bank`) so the 1/2 result is not
mistaken for a regression later.

## Verification

- `tests/test_mes.py` runs under pytest *and* the stdlib fallback runner
  (`python tests/run_tests.py`).
- **Synthetic tier**: banks assembled in memory so the suite passes on a bare checkout
  with no copyrighted input. Covers single- and multi-section layout, the count-field
  origin, the odd-length trailing byte, non-zero gap preservation, and every rejection
  path (hostile count, offset inside the table, descending/overlapping offsets, empty and
  all-`0xff` blobs).
- **Real-disc tier**: `data/raw/SYSMES.ALD` decodes and re-encodes byte-identically —
  `python -m tools.formats.mes roundtrip data/raw` prints
  `OK SYSMES.ALD  bytes=89,088 sections=2 messages=677`. This is the Phase 2 exit
  criterion met for this format.
- **Measured, not asserted**: the glyph rule is validated by the printable-ratio
  comparison above, and the section fingerprint by the real `first_rel` values.

## Open questions

- **Glyph coverage is unmapped.** The control census above lists the inline formatting
  units, but the full glyph repertoire (accented characters, symbols, any bank-B
  punctuation) is not yet enumerated. Phase 6 text authoring needs the complete map,
  because the font — not ASCII — decides what a valid character is.
- **`ff00`/`ff01` semantics are inferred from frequency and position**, not confirmed
  in-game. A line break is the obvious reading; it should be confirmed by patch-and-boot
  (Phase 3/7) before dialogue layout depends on it.
- **Length constraints are unknown.** If a message is fixed-length, or if the engine
  indexes text by a stored size rather than the table, growing a string could shift
  something else. Worth testing with a single one-character edit before volume authoring.
- **Which bank is which is only partly mapped.** `SYSMES.ALD` is system text. Where
  dialogue, item names and file notes live (loose, or as members of `SYSTEM.AFS`/
  `ITEM*.AFS`) is Phase 3 extraction work.
