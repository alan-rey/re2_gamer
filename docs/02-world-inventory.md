# World Inventory — the three zones

We keep the geometry and rewrite everything that happens inside it. The world is three
macro-zones. The **authoritative** room/door/item/trigger list is a Phase 4 deliverable
extracted from the game data; this document defines the *shape* of that inventory and the
best-effort sub-area map, not the final ID table.

## The three macro-zones

| Zone | Canonical location | What a rewrite touches |
|---|---|---|
| **A. The Island** | Rockfort Island (prison, training facility, exterior) | Opening lore, starting items, first key, first puzzle |
| **B. The Mansion** | The Ashford palace / private residence | Central hub, file notes, piano/music puzzle, portrait/doll set pieces |
| **C. The Facility** | Antarctic base / lab | Late-game lore, final key chain, boss-trigger puzzles, ending |

## Zone A — The Island (Rockfort)

Sub-areas (best effort, confirm during Phase 4):
- Detention cells / prison block — where the player starts.
- Military training facility — first "teaching" zone for new mechanics.
- Island exterior / dock — connective tissue, first locked gate.

Rewrite targets:
- New opening note establishing the new premise.
- Move the first key from its original spot.
- Lock a formerly-open door to redirect the player.

## Zone B — The Mansion (Ashford palace)

Sub-areas (best effort):
- Main hall / grand staircase — the hub.
- Music room / piano — classic puzzle candidate.
- Portrait gallery / doll room — lore-rich set pieces.
- Private residence replica — a "mirror" mansion with a narrative payoff.

Rewrite targets:
- Replace all file notes with the new lore.
- Re-map the piano/music puzzle to a new melody/solution.
- Swap portrait/doll triggers to a new sequence.

## Zone C — The Facility (Antarctic base)

Sub-areas (best effort):
- Transport terminal / entry.
- Laboratories — where the new antagonist's plot resolves.
- Boss arena / ending area — final trigger chain.

Rewrite targets:
- New endgame notes and terminal text.
- New final key chain and boss-trigger conditions.
- New ending cutscene sequence (reuse existing cameras).

## What an "inventory entry" must capture (Phase 4 output)

For every interactable object, extract and record:

```yaml
id: DOOR_MANSION_EAST
type: door                  # door | item | trigger | event | text | key | lock
zone: mansion
room: music_room
position: { x, y, z }       # or model/instance reference
lock:
  key_id: KEY_PIANO
  flag: FLAG_PIANO_SOLVED   # the save-state flag that records "unlocked"
links: [ROOM_HALL]          # rooms reachable through this door
script_ref: SCD_0123        # the scenario/event script that drives it
```

This turns the game into a **graph** (rooms as nodes, doors/keys as edges, flags as state)
that both the design and the injection tooling consume. The rewrite is then expressed as a
*diff against this graph*, which is far less error-prone than editing binaries by hand.

## Progression model

Classic RE structure to preserve (it works well for pacing):

```
locked door -> key/puzzle -> unlocks door -> new room -> new note/lore -> ...
```

The rewrite changes *which* door is locked, *where* the key is, and *what* the puzzle is,
but keeps this loop — it is the engine's native shape and the least risky thing to
re-author.
