# Verification Report: rc/v0.5.0 — Bonus Fruit

## Recommendation
PASS

## Systemic Risk
low

## Summary
Verified RC v0.5.0 ("Bonus Fruit") for pgai-chomp-man against
`requirements/v0.5.0-fruit-bonus.md`. The RC adds one new module (`fruit.py`)
and makes purely additive changes to `scoring.py` and `pgai-chomp-man.py`,
plus three new/extended test files. Pre-flight merge verification passed (all
three CODER feature branches merged into the RC; branches deleted post-merge
as normal). Worktree HEAD matches `rc/v0.5.0`, tree clean, no stashes.
`python3 -m py_compile` passes for every changed/added `.py` file. The full
project pytest suite runs headless with 220 passed, exit 0. Behavioral
verification of the spawn trigger, despawn-on-timeout, cell-entry eat
detection (including no-double-count), and tier scoring floor/cap all pass.
All six acceptance criteria are met. One pass-with-caveat is recorded around a
documented internal inconsistency in the requirements doc's own point-value
guidance; it does not affect operability. Recommendation: PASS.

## Test Suite Result
- Command: `python3 -m pytest tests/` (headless, `SDL_VIDEODRIVER=dummy`,
  `SDL_AUDIODRIVER=dummy`), run from the RC worktree.
- Result: **220 passed, exit 0.**
- Note on gated runners: `team/scripts/run-unit-tests.sh` /
  `run-integration-tests.sh` are kanban-self infrastructure and do not exist in
  this game project. The authoritative pass/fail signal for pgai-chomp-man is
  the project's own pytest suite, which exited 0. This project has no
  anti-pattern lint pre-flight to bypass.

## Code Coverage
coverage unavailable (this project ships no coverage-instrumented runner;
coverage is informational only and never a finding).

## Findings

### Finding 1: AC1 — all changed/added .py files compile
**Category:** pass
`python3 -m py_compile fruit.py scoring.py pgai-chomp-man.py
tests/test_fruit.py tests/test_fruit_render.py tests/test_scoring.py`
returned success.

### Finding 2: AC2 — fruit spawns on progress/timer trigger and despawns after window
**Category:** pass
`should_trigger_fruit_spawn()` fires on either the pellet-progress threshold
(`FRUIT_SPAWN_PELLET_THRESHOLD = 30`) OR the elapsed-time threshold
(`FRUIT_SPAWN_INTERVAL_MS = 20000`), whichever is reached first — matching the
requirements' "progress- and/or timer-based" wording. `spawn_fruit()` sets the
fruit active at the fixed center-nearest cell with a full
`FRUIT_VISIBLE_WINDOW_MS = 10000` window; `update_fruit_timer()` counts it down
each frame and despawns the instant the window reaches 0 (floored, never
negative; overshoot handled). Behavioral run confirmed spawn-then-timeout
despawn. The main loop wires this per-frame, gated on not-board-cleared and
not-game-over, and resets the trigger accumulators only when a spawn fires.

### Finding 3: AC3 — fruit renders distinctly from pellets and power-ups
**Category:** pass
`draw_fruit()` renders a diamond (rotated square via `pygame.draw.polygon`)
with a pale highlight diamond on top — a distinct *silhouette*, not just a
distinct color, from the circles `maze.draw_pellet()` /
`maze.draw_power_pellet()` use. `FRUIT_COLOR = (48, 220, 176)` (jade/teal) is
verified distinct from `PELLET_COLOR` and `POWER_PELLET_COLOR`.
`tests/test_fruit_render.py` inspects real drawn pixels under the dummy video
driver: body color, highlight color, no-op when inactive, distinct color, and
diamond-not-circle (corner pixels left as background). Manual live visual check
requires a display and was not run headless; the pixel-level tests cover the
structural claim.

### Finding 4: AC4 — eating fruit awards high points via named constants
**Category:** pass
`fruit_points_for_tier()` resolves the eaten fruit's tier into a value from the
named, ordered `FRUIT_TIER_VALUES = (100, 300, 500)`, built from
`FRUIT_TIER_1_POINTS` / `FRUIT_TIER_2_POINTS` / `FRUIT_TIER_3_POINTS`. Every
tier value exceeds `PELLET_POINTS (10)` and `POWER_PELLET_POINTS (50)` — the
highest standard *collectible* values in the game. The main loop awards
`score.add(fruit_points_for_tier(fruit.tier))` and plays the registered
`"fruit"` sound cue only when `eat_fruit()` returns True; `fruit.tier` remains
readable after the eat (neither `eat_fruit()` nor `despawn_fruit()` clears it).
The CODER-2 placeholder `FRUIT_POINTS` constant was fully removed — no live
code references remain (only historical mentions in comments/docstrings).

### Finding 5: AC5 — fruit values escalate across tiers as the player progresses
**Category:** pass
`Fruit.tier` is set from `spawn_fruit(..., tier=...)`; the main loop tracks a
cumulative, never-reset `fruit_spawn_count` and passes it as the tier on each
spawn, so successive spawns advance tier 1 -> 2 -> 3. `fruit_points_for_tier()`
holds at the top value (500) past the sequence length and floors tiers <= 0 to
tier 1 — mirroring `dragon_eat_bonus()`'s established floor/cap contract.
Verified behaviorally: tiers 1/2/3 -> 100/300/500; tier 4 and tier 100 -> 500;
tiers 0 and -5 -> 100.

### Finding 6: AC6 — no regression to prior mechanics
**Category:** pass
The diff is purely additive: `fruit.py` and two test files are new;
`scoring.py`, `pgai-chomp-man.py`, and `test_scoring.py` gain fruit-only
additions. No prior gameplay module (`maze.py`, `dragons.py`, `lives.py`,
`player.py`, `board_clear.py`, `sound.py`) was modified. All 220 tests pass,
including the full pre-existing regression set (pellets, power-pellets,
dragons, dragon-eat/vulnerable mode, lives, board-clear, player direction,
display scaling, sound wiring). The `"fruit"` sound cue was already registered
in `sound.SOUND_DEFINITIONS`.

### Finding 7: Goal 2 point-value phrasing vs. suggested tier values
**Category:** pass-with-caveat
Goal 2 of the requirements states the fruit should award "more than pellets or
single dragon-eats." A single dragon-eat is `DRAGON_EAT_BONUS_VALUES[0] = 200`,
so **tier 1 (100 points) is below a single dragon-eat (200)**. However, the
same requirements doc's Design/Theming section and Acceptance criteria
explicitly suggest the concrete progression "100, 300, 500," which the
implementation follows exactly, and describe the fruit as "the highest standard
*collectible* value in the game" (all tiers exceed pellets and power-pellets).
The doc is internally inconsistent on this point (Goal 2 vs. its own suggested
numbers). The authoritative acceptance criteria (AC4/AC5) require only "high
points using named constants" and "values escalate across tiers" — both met.
The top tier (500) does exceed a single dragon-eat. Requirements-quality
reconciliation is out of TESTER scope; recorded as a caveat, not a defect. No
Path C filing.

## Autonomous criterion (Step 6.12)
Manual interventions required: none.
1. Tasks force-promoted WAITING->BACKLOG by hand? **NO** — no evidence in task
   logs or status trails.
2. `release-state.md` edited outside the CM scripts? **NO** — populated by
   `cm-open-rc.sh` (`RC Opened By Task: cm-open-rc.sh (manual)` = the correct
   script invoked by the operator, not a hand-edit).
3. Scripts patched mid-build? **NO** — the RC diff touches only game source and
   tests; this project hosts no `team/scripts/` tooling.
4. Queue files hand-edited? **NO** — no evidence.

## Pre-flight
- Worktree HEAD `a1dca86` == `rc/v0.5.0`. Tree clean. No stashes.
- Merge verification: CODER-064, CODER-065, CODER-066 all DONE; their merge
  commits are present on the RC; feature branches deleted post-merge (normal).
- Drift check: RC v0.5.0 > Last Released v0.4.2 (semver). release-state.md
  `Active RC = v0.5.0` consistent with the branch under test.

## Caveats
- The `VERSION` file on the RC reads `v0.4.2`, not `v0.5.0`. This is expected,
  not a defect: git history shows `VERSION` is committed per-release ("Add
  committed VERSION for v0.4.2/.1/.0/...") at ship time by CM, not during the
  CODER/RC phase. Prior RCs followed the same pattern. Flagged only for CM
  awareness that the v0.5.0 VERSION bump is a ship-time step.
- Live interactive visual confirmation (AC3 "manual visual check") needs a
  display and was not performed; the headless pixel-inspection tests cover the
  distinctness claim structurally.
- Two pre-existing test docstrings (`test_player_render.py`,
  `test_player_direction.py`) mention BUG-0001/BUG-0002. These files are not
  part of the v0.5.0 diff and the mentions are not skip annotations, so the
  skip-cites-real-bug gate does not apply. Out of scope for this RC.

## Next Recommended Step
CM may ship v0.5.0. No follow-up bugs or priorities filed.
