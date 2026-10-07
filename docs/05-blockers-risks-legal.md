# Blockers, Risks & Legal

This document separates three things that are easy to conflate:

- **Blockers** — hard facts that, if not resolved, stop the project entirely.
- **Risks** — things that could go wrong, with a mitigation each.
- **Legal** — constraints that shape how we work and how we ship.

---

## Blockers (must resolve, in rough order of threat)

### B1. There is no public "Code Veronica X mod SDK"
Capcom never shipped mod tools for this title. Every archive, script, and text format must
be reverse-engineered from scratch.

- *Threat:* unknown effort; a single opaque format can stall everything.
- *Resolution path:* Phase 2. The format families are finite and shared across RE-era
  games; the community (PSX/PS2 RE modding) has prior art for several of them.

### B2. Repacking a bootable PS2 image (LBA / file layout)
The PS2 reads files by **logical block address** and often expects specific file ordering.
Re-inserting files that changed size can invalidate those addresses and produce a disc
that won't boot.

- *Threat:* "content is correct but the ISO doesn't boot" — the classic silent killer.
- *Resolution path:* the Phase 3 **golden round-trip** (unpack → repack with *no changes* →
  boot) proves the LBA/rebuild logic before any content changes size.

### B3. Script/trigger VM semantics are unknown
Doors, keys, flags, puzzles, and cutscenes run on an in-engine script/event system. We can
find the data, but we must also understand *how it is interpreted* to author new logic
rather than just re-point IDs.

- *Threat:* we can move a key but not express a *new* puzzle sequence.
- *Resolution path:* Ghidra + PCSX2 debugging (Phase 2) to map the script VM's opcode set,
  then re-author within it.

### B4. Text encoding & font/glyph coverage
Dialogue and file notes are stored in a specific encoding and rendered with a fixed font
whose glyph set may not cover everything we want to write (lengths, punctuation, accented
characters).

- *Threat:* new text that truncates, corrupts, or can't render certain characters.
- *Resolution path:* Phase 2 identifies the encoding + width/height constraints; content
  authoring (Phase 6) lints against them (length caps, allowed glyph set).

### B5. Audio/voice (if in scope) has no trivial injection path
Replacing voice or adding new lines means re-authoring streamed audio and re-timing
cutscenes. This is a *big* lift.

- *Threat:* scope creep.
- *Resolution path:* **defer to a stretch goal.** MVP uses subtitle/text swaps only; reuse
  existing audio where possible.

---

## Risks (with mitigations)

| Risk | Impact | Mitigation |
|---|---|---|
| Format RE underestimates effort | Schedule slip | Vertical-slice spike first (Phase 3→7) to expose the real cost early |
| Injecting invalid data corrupts the build | Lost work / hard-to-debug | Golden round-trip + per-format round-trip tests + content schema validation |
| Softlocks from re-keyed progression | Unplayable sections | World graph (Phase 4) + reachability checks: every locked door's key must be reachable |
| Cutscene re-authoring exceeds the engine's capabilities | New scenes look broken | Storyboard within *existing* cameras/anims; avoid net-new cinematic tech |
| Save-file incompatibility after layout changes | Crashes on old saves | Treat saves as volatile during dev; document "start a new game" requirement |
| Emulator vs. real hardware divergence | "works in PCSX2, breaks on PS2" | Final QA on real hardware (or at minimum a second emulator) before v1.0 |
| Scope creep (voice, new models, new maps) | Never ships | The "no new geometry/audio in MVP" rule in README is a hard line |

---

## Legal (shapes how we work)

1. **This is a derivative work** of a Capcom-copyrighted game. We do not own it, and
   "fan remake" is not a legal safe harbor by itself.
2. **Never commit, distribute, or publish original game files.** `data/raw/`, the ISO, and
   extracted assets stay out of git (`.gitignore` + never LFS the originals).
3. **Ship a patch, not a game.** Distribution is an **xdelta** patch the end user applies
   to *their own* disc image. We ship only the *delta* (and any genuinely original text /
   tooling), never Capcom's bytes.
4. **Keep authored content original.** New lore, dialogue, file notes, item layouts, and
   tooling are ours and can be licensed/released; the underlying game is not.
5. **Trademarks:** do not use the title/branding to imply endorsement; label clearly as an
   unofficial fan project.
6. **Respect the owner's wishes:** if Capcom requests a takedown, comply. Do not monetize
   the project.

---

## Open questions to resolve early

- Exact PS2 region/version of the disc (affects ELF name, text encoding, save format).
- Whether the game's archives share a common container (fast path) or are bespoke per type.
- Whether the script VM can express the new puzzle sequences we design, or whether design
  must bend to the VM (it will, and that's fine — see Phase 2 before Phase 5).
