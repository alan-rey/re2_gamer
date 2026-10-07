# Asset & Legal Policy

Read this before adding, extracting, or committing anything.

## The bottom line

This project is a **derivative work** of a copyrighted Capcom title
(*Resident Evil: Code Veronica X*, PS2). We are legally allowed to *modify* our own
copy for personal use in most jurisdictions, but we are **not** allowed to distribute
Capcom's copyrighted data. Everything below follows from that one fact.

## Hard rules (never violate)

1. **Never commit the game disc image.** It stays in `.game_iso/` and is matched by
   `.gitignore` (`*.iso`, `*.bin`, `*.cue`, etc.).
2. **Never commit extracted game data.** Anything produced by unpacking the ISO goes to
   `data/raw/` or `data/extracted/` — both gitignored. This includes models, textures,
   audio (`.vag`, `.adpcm`), video (`.pss`, `.ipu`), scripts, and text tables.
3. **Never mirror Capcom data into Git LFS.** LFS is for *our own* large artifacts
   (research captures, our rendered output). Using LFS on game data just moves the
   infringement to a bigger pipe.
4. **Never commit build output.** `build/`, `dist/`, and any `*.xdelta`/`*.patch` that
   contains injected copyrighted bytes are gitignored. These are derivative artifacts.
5. **Never commit secrets.** `.env`, tokens, keys, and PEM files are gitignored. Auth
   comes from the `GITHUB_TOKEN` environment variable — never hard-code it.

## What we CAN commit

- Our **own original content**: story text, lore, dialogue, file-note prose, item/key
  design, progression graphs, and cutscene descriptions (`content/`).
- Our **own tools and code**: parsers, packers, injectors, test harnesses (`tools/`).
- Our **own research notes**: format specs, addresses, schemas, methodology (`research/`).
- Our **patch files** that contain *only our original deltas* (not the full game data).

## How we distribute (Phase 9)

We do **not** ship a patched ISO. We ship a **patch** (e.g. `xdelta3`) that the end user
applies to a disc image **they already own**:

```
original ISO (user's own)  +  our .xdelta patch  ->  modded ISO (user's own)
```

This is the standard, defensible posture for fan total-conversions: we distribute our
creative work, not Capcom's.

## Contamination check before every push

Bast must confirm no copyrighted/binary blobs are staged before pushing:

```bash
git status --porcelain          # nothing under data/raw, build/, .game_iso, *.iso
git ls-files | grep -iE '\.(iso|bin|vag|pss|adpcm|mpeg)$'   # must return nothing
```

If either check fails, fix `.gitignore`, `git rm --cached` the offending path, and re-check
before pushing.

## Disclaimer

This is an unofficial, non-commercial fan project. *Resident Evil* and *Code Veronica* are
trademarks of Capcom. No affiliation, sponsorship, or endorsement is claimed. If Capcom
objects, the project stops.
