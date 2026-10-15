# Verification Report: rc/v0.3.0 — Dragons (Enemies)

## Recommendation
PASS

## Systemic Risk
low

## Summary
Verified RC v0.3.0 of pgai-chomp-man against `requirements/v0.3.0-dragons.md`. The RC
adds dragon enemy entities, autonomous wall-respecting movement, dragon↔Chomp Man
collision, lives tracking (start 3), life-loss reset, a GAME OVER state, and a HUD lives
readout. The worktree HEAD matches `rc/v0.3.0` (0f7b79e), the tree is clean, and no
stashes are present. All three prerequisite CODER task branches are merged into the RC
(commits confirmed on the RC log; feature branches cleaned post-merge, which is normal).
The full test suite passes (107 passed, exit 0). All seven acceptance criteria are met,
confirmed by a combination of code inspection, executable checks, and live behavioral
exercise (collision→life-loss→reset→game-over cycle, distinct-dragon placement, and a
300-tick no-wall-crossing movement run, all run headless). No bugs, no gaps, no stale
assertions. Clean release.

## Test Suite Result
- Runner: `python3 -m pytest tests/` (project-native runner; this project is not the
  kanban self-build and ships no `run-unit-tests.sh` gated runner — pytest is the
  authoritative suite here). Exit code 0.
- Result: **107 passed** in ~1.9s (headless, `SDL_VIDEODRIVER=dummy`,
  `SDL_AUDIODRIVER=dummy`).
- New tests added this RC: `tests/test_dragons.py` (entities/placement, 13 tests),
  `tests/test_dragon_movement.py` (movement/junction/wall-respect, 15 tests),
  `tests/test_lives.py` (lives/collision/reset/game-over, 20 tests). All exercise real
  `Maze`/`Dragon`/`ChompMan`/`Lives` objects — no mocking-away of the breaking
  conditions (walls, junctions, final-life freeze).

## Code Coverage
coverage unavailable (pytest-cov not part of this project's suite; not an acceptance
criterion — informational only).

## Findings

### Finding 1: AC1 — all changed/added .py compile
**Category:** pass
`python3 -m py_compile` succeeds for every changed and project `.py` file
(dragons.py, lives.py, pgai-chomp-man.py, scoring.py, plus the rest of the tree and all
test files). Main module `pgai-chomp-man.py` also imports cleanly and exposes a callable
`run()`.

### Finding 2: AC2 — multiple distinct dragons render in the maze
**Category:** pass
`DRAGON_ROSTER` defines 4 dragons (Ember/Frost/Nightshade/Viper), each with a distinct
RGB color (matches requirements' suggested 3–4). `create_dragons()` places each on its
own distinct open cell, excluding Chomp Man's start. Behaviorally confirmed: 4 dragons,
4 distinct colors, 4 distinct cells, none on the player start. `Dragon.draw()` uses a
rounded-rect body + horn polygons + eyes — a deliberately different silhouette from
`ChompMan.draw()`'s circle-with-mouth-wedge, so dragons are distinguishable from the
player as well as from each other. `draw_dragons()` renders without error headlessly.

### Finding 3: AC3 — autonomous movement respecting walls
**Category:** pass
Dragon placement and movement both reuse `Maze.is_wall()` / `Maze.in_bounds()` via
`open_cells()` and `open_directions()` — the exact predicates `ChompMan.move()` checks
in player.py. No duplicate wall-collision implementation exists in dragons.py (grep
confirms it references `maze.is_wall`, never re-derives wall-ness from layout chars or
`CellType`). `choose_dragon_direction()` continues straight through corridors and picks
among open, non-reversing directions at junctions. Behaviorally confirmed: over 300
movement ticks with 4 dragons, no dragon ever landed on a wall cell; a T-junction test
confirms direction actually varies (not deterministic straight-only).

### Finding 4: AC4 — dragon↔Chomp Man collision costs a life
**Category:** pass
`check_dragon_collision()` is a cell-occupancy equality test, checked in the main loop
after every player movement step AND after every dragon movement step (both ways a catch
can occur). A collision calls `apply_life_loss()`, which decrements the counter.
Behaviorally confirmed via forced collisions.

### Finding 5: AC5 — lives start at 3, HUD display, decrement, GAME OVER at 0
**Category:** pass
`LIVES_START = 3`; `Lives()` defaults to it. `render_score_hud()` was extended to render
`SCORE: n   LIVES: n` on the single existing HUD line (reusing the existing HUD approach
per the requirements). `lose_life()` decrements floored at 0; `is_game_over()` fires at
0; the main loop sets `game_over` and gates all movement (player and dragon) on it, then
draws `render_game_over_message()`. Behaviorally confirmed: life cycle 3→2→1→0 with
game-over True exactly on the final loss; GAME OVER overlay renders headlessly.

### Finding 6: AC6 — non-final life loss resets positions and continues
**Category:** pass
`apply_life_loss()` resets Chomp Man and every dragon to captured start positions (and
clears each dragon's `direction` to None) on a non-final loss, and deliberately does NOT
reset on the final loss (freeze-on-game-over, matching board-clear freeze-on-win).
Behaviorally confirmed: after each non-final loss the player and all dragons returned to
their recorded start cells and play continued.

### Finding 7: AC7 — dragon state structured for a future "vulnerable" mode
**Category:** pass
`DragonState` is an `enum.Enum` with `NORMAL` today; each `Dragon` carries a plain
mutable `state` attribute (not behavior hardcoded into the class or `draw()`), and
movement never reads/branches on `state`. Adding a `VULNERABLE/FLEEING` member in v0.4.0
requires no restructuring of `Dragon` or `create_dragons()`. The structure is ready for
the v0.4.0 extension. `Dragon.direction` follows the same extensible plain-field shape.

### Finding 8: Constraint — reuse wall-collision, do not duplicate
**Category:** pass
Confirmed shared: both `player.ChompMan.move()` and dragon movement/placement route
through `maze.is_wall()` + `maze.in_bounds()`. No second wall implementation.

## Autonomous criterion (Step 6.12)
1. Tasks force-promoted WAITING→BACKLOG by hand? **NO** — this task's status shows the
   wake script performed the promotion when prerequisites were satisfied.
2. `release-state.md` edited outside the cm-* scripts? **NO** — RC was opened via
   `cm-open-rc.sh (manual)`; state fields are consistent (Active RC v0.3.0 > Last
   Released v0.2.1) with no hand-edit evidence.
3. Scripts patched mid-build? **NO** — the RC diff touches only game source
   (dragons.py, lives.py, pgai-chomp-man.py, scoring.py) and tests; no `team/scripts/`
   or tooling files.
4. Queue files manually edited? **NO** — no evidence of manual queue edits.

Manual interventions required: none.

## Caveats
- This project (pgai-chomp-man) is a pygame game, not the kanban self-build. It ships no
  `team/scripts/run-unit-tests.sh` / `run-integration-tests.sh` gated runners and no
  anti-pattern lint pre-flight — those are kanban-self-build mechanisms. The authoritative
  suite for this project is `python3 -m pytest tests/`, which exited 0. The PASS verdict
  rests on that project-native runner plus code inspection and behavioral verification.
- The `VERSION` file on the RC still reads `v0.2.1`. This is the established convention,
  not a defect: across every prior release, the version bump lands as a separate
  release-time commit on main ("Add committed VERSION for vX.Y.Z"), never on an RC
  feature branch. The v0.3.0 VERSION commit is expected to be added by the release step.
  It is not an acceptance criterion for this RC. Observation only.
- Visual/interactive criteria (on-screen dragon distinctness, live gameplay feel) were
  verified structurally and via headless rendering smoke tests; a true display was not
  available in the verification environment. The requirements doc anticipates this
  ("Headless: code inspection + compile; visual/interactive checks need a display").
