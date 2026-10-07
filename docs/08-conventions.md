# 08 — Conventions & Naming Standards

Rules that keep the project navigable. Every tool, data file, and content file
must follow these. When in doubt, match what is already in the repo.

---

## 1. Repository naming

- **Directories:** lowercase, hyphen-separated. `data/`, `content/scripts/`,
  `tools/roundtrip/`.
- **Docs:** `NN-kebab-title.md`, numbered in reading order (`01-...` through
  `08-...`). Never renumber an existing doc; append the next number.
- **Python modules/packages:** lowercase, underscores. `iso_reader.py`,
  `scenario_parser.py`.
- **Python classes:** `CapWords`. **Functions/variables:** `snake_case`.
  **Constants:** `UPPER_SNAKE`.

## 2. Game-domain identifiers

These IDs are the contract between tools, data files, and content. Never
invent a new scheme per tool.

| Thing         | Format                | Example            |
|---------------|-----------------------|--------------------|
| Zone          | `z<NN>_<slug>`        | `z02_mansion`      |
| Room          | `<zone>/<NNN>`        | `z02_mansion/114`  |
| Door          | `door_<room>_<n>`     | `door_114_2`       |
| Item          | `item_<slug>`         | `item_gold_key`    |
| Puzzle/trigger| `trg_<slug>`          | `trg_portrait`     |
| Note/file     | `note_<slug>`         | `note_ashford_note`|
| Cutscene      | `cs_<slug>`           | `cs_intro_arrival` |

- Room numbers are **the game's own room IDs where known**, zero-padded to 3.
  If we must invent one for a new sub-area, reserve a documented block.
- Slugs are lowercase, underscore-separated, no spaces.

## 3. Content file conventions

- One entity per entry; stable `id` field that matches the naming table above.
- Authored content lives in `content/` as YAML (`*.yaml`), never inline in code.
- Every content file starts with a header comment: purpose, author, status
  (`draft` / `review` / `final`).
- Text strings are UTF-8, LF endings. Keep lines reasonably short; note the
  in-game font/glyph limits from `05-blockers-risks-legal.md`.

## 4. Code conventions

- Python 3.11+, formatted with `ruff format`, linted with `ruff check`.
- Type hints on all public functions. Docstrings on modules and public funcs.
- No hardcoded paths: read workspace-relative paths from a single
  `tools/paths.py` constants module.
- Every parser/injector is **round-trip tested**: decode then re-encode must
  produce byte-identical output for unmodified input.

## 5. Commits & branches

- Branch: `main` is stable. Work on `feature/<slug>`, `fix/<slug>`,
  `tools/<slug>`.
- Commit messages are imperative and specific:
  `Add scenario parser round-trip test`, not `update`.
- One logical change per commit. Reference the phase: `[P2] ...` optional prefix.

## 6. What must NEVER be committed

Enforced by `.gitignore` and `.gitattributes` — see `docs/07-asset-policy.md`:

- The game ISO and anything extracted from it (`data/raw/`, `data/extracted/`).
- Build output and patches (`build/`, `dist/`, `*.xdelta`).
- Secrets and credentials (`.env`, `*.key`, `*.pem`).

If a tool needs copyrighted input to run, that input is supplied by the user at
runtime from a gitignored path — never checked in.
