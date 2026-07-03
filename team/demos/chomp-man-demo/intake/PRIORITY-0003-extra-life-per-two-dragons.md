# PRIORITY-0003-extra-life-per-two-dragons

## Status
open

## Priority
PRIORITY-0003

## Overview
Reward aggressive power-pellet play: each time Chomp-Man eats 2 dragons
(cumulative across the whole game, while dragons are in their edible/fleeing
state during a power-pellet window), award 1 extra life. This gives skilled
players a renewable life source and makes power-pellet timing strategically
valuable.

## Goal
Track the total number of dragons eaten across the game. Every time that count
crosses a multiple of 2 (2, 4, 6, ...), grant +1 life.

## Behavior
- Maintain a cumulative `dragons_eaten` counter for the current game (resets only
  on a new game / full restart, NOT on death and NOT per power-pellet).
- Each dragon eaten increments the counter by 1.
- When `dragons_eaten` becomes an even number (every 2nd dragon), award +1 life.
  (Equivalently: award a life whenever `dragons_eaten % 2 == 0` at the moment of
  eating.)
- The life award should reflect in the existing lives counter / HUD immediately.
- A reasonable upper cap is fine if one already exists for lives; otherwise no cap
  is required for this change.

## Acceptance Criteria
1. Eating 2 dragons (in one or across multiple power-pellet windows) yields exactly
   +1 life; eating 4 yields +2; odd counts (1, 3) yield no new life until the next
   even dragon.
2. The `dragons_eaten` counter persists across deaths within the same game and
   resets on a new game.
3. The lives HUD updates immediately when a life is awarded.
4. `python3 -m py_compile` on the changed file(s) passes.
5. No regression: existing dragon-eating scoring, power-pellet timing, dragon
   respawn, and the board-clearable invariant are unchanged.

## Notes for CODER
This builds on the existing power-pellet / edible-dragon mechanic — do not change
how dragons become edible or how eating them scores; only ADD the cumulative
counter + the every-2 life award. Keep it small and self-contained. Guard the
main loop behavior so existing play is unaffected when the player eats 0 or 1
dragons.

## Notes for TESTER
Programmatic checks: confirm a `dragons_eaten`-style counter exists and increments
per dragon; confirm the +1-life-per-2 logic (even-count award); confirm the counter
does not reset on death. The visual life-award is manual confirmation. Verify the
board is still clearable and dragon-eating still scores as before.
