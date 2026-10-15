# Verification Report: rc/v0.4.0 — Power-Ups (Eat the Dragons)

## Summary
Verified RC v0.4.0 of pgai-chomp-man against `requirements/v0.4.0-power-pellets.md`.
The release adds power-ups ("Dragon Bane"), a vulnerable-mode trigger + wall-clock timer,
dragon NORMAL<->VULNERABLE state transitions with a distinct render color, eat-vulnerable-dragon
(den respawn + escalating combo bonus 200/400/800/1600), and preserves the v0.3.0 normal-dragon
life-loss collision. Pre-flight passed (HEAD matches rc/v0.4.0, clean tree, no stashes, all three
CODER feature branches merged and cleaned up). All 8 acceptance criteria verified programmatically
and behaviorally; `python3 -m py_compile` passes for every changed file; the project's pytest suite
is 163 passed / 0 failed / 0 skipped; independent behavioral triggering of both collision branches,
the combo clamp, timer expiry, den respawn, and the v0.3.0 regression all confirmed the guard logic
fires correctly.

## Recommendation
PASS

## Systemic Risk
low

## Code Coverage
coverage unavailable (project ships no coverage-instrumented gated runner; not required by the
requirements doc -- informational only, never a finding).

## Test Suite Result
- Runner: `python3 -m pytest tests/` (headless: `SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy`).
- Result: 163 passed, 0 failed, 0 skipped (exit 0).
- Note on gated runners: this is a self-contained pygame project with no `team/` directory, so
  `team/scripts/run-unit-tests.sh` / `run-integration-tests.sh` do not exist here. Those gated
  runners are specific to the kanban-self build. The requirements doc's acceptance criteria for
  this project are `py_compile` + code/behavioral inspection, and the project ships its own pytest
  suite -- that suite is the authoritative test signal for this project. Absence of the kanban-self
  gated runners is by-design for a game project, not an infrastructure failure, so it is not a
  BLOCKED condition. See Caveats.

## Findings

### Finding 1: AC1 -- py_compile passes for all changed/added .py files
**Category:** pass
`python3 -m py_compile *.py tests/*.py` exits 0. All 11 changed files compile.

### Finding 2: AC2 -- Power-ups render distinctly and are placed on open cells
**Category:** pass
`maze.find_power_pellet_cells()` anchors on the four geometric corners and picks the nearest
eligible non-wall, non-excluded cell per corner. Behavioral run on the default maze returned 4
distinct corner-adjacent open cells [(1,1),(1,17),(13,1),(13,17)], none on walls.
`Maze.place_power_pellets()` only converts non-WALL cells (verified: a placement request on a wall
cell is skipped -- returned count 1 of 2 requested when one target was a wall). Distinct rendering
via `POWER_PELLET_COLOR (255,176,64)` + glow highlight and a larger radius
(`POWER_PELLET_RADIUS_DIVISOR=3` vs pellet's 8) in `maze.draw_power_pellet`.

### Finding 3: AC3 -- Eating a power-up sets dragons VULNERABLE and starts a timer
**Category:** pass
Main loop reads `maze.has_power_pellet()` before `eat_at()` clears the cell; on a power-pellet eat
it calls `set_dragons_vulnerable(dragons)`, sets
`vulnerable_mode_remaining_ms = VULNERABLE_MODE_DURATION_MS (8000)`, and resets the combo counter
to 0. State transition and timer start confirmed behaviorally.

### Finding 4: AC4 -- Vulnerable dragons are visually distinct and edible
**Category:** pass
`Dragon.draw()` renders body + horns in the shared `VULNERABLE_COLOR (72,108,224)` while
`state is VULNERABLE`, reverting to the roster color otherwise. Edibility confirmed via the
collision resolver branch (Finding 5).

### Finding 5: AC5 -- Eating a vulnerable dragon -> den + escalating combo bonus + named constants
**Category:** pass
Behavioral test of `resolve_dragon_collisions`: eating a VULNERABLE dragon sends it to its own den
(order-matched `dragon_start_positions`), flips it back to NORMAL, resets its direction to None, and
awards `dragon_eat_bonus(combo)`. Escalation confirmed: successive eats in one window awarded
200 -> 400 (combo 1 -> 2), and the sequence holds at 1600 past the 4th eat
(`dragon_eat_bonus(5)==1600`, no IndexError). Constants
`DRAGON_EAT_BONUS_VALUES=(200,400,800,1600)` and `POWER_PELLET_POINTS=50` are named; no stray combo
literals found outside the constant. Eating a vulnerable dragon does not cost a life.

### Finding 6: AC6 -- Vulnerable mode expires on the timer and dragons return to NORMAL
**Category:** pass
`update_vulnerable_timer()` counts `remaining_ms` down by `elapsed_ms`, floors at 0, and calls
`set_dragons_normal()` exactly on the expiry edge. Behavioral run: 100ms->(-40)=60ms keeps
VULNERABLE; 60ms->(-100)=0ms reverts to NORMAL. Combo counter is NOT reset on timer expiry (only on
a new power-pellet eat), matching the requirements doc.

### Finding 7: AC7 -- NORMAL dragon still costs a life (v0.3.0 regression preserved)
**Category:** pass
Behavioral test: a NORMAL dragon on Chomp Man's cell drives `apply_life_loss` (lives 3->2, no score
gain, "life-loss" cue). Life loss is applied at most once per call even with multiple NORMAL dragons
on the cell. Mixed case (one VULNERABLE + one NORMAL on the same cell) correctly eats the vulnerable
one (+200) AND loses a life (3->2) -- both branches fire independently, and the pre-mutation state
capture prevents an eaten dragon from double-counting as a NORMAL collision.

### Finding 8: AC8 -- Scoring additions use named constants
**Category:** pass
`PELLET_POINTS=10`, `POWER_PELLET_POINTS=50`, `DRAGON_EAT_BONUS_VALUES=(200,400,800,1600)`,
`VULNERABLE_MODE_DURATION_MS=8000` are all named. `POINT_VALUES` registry keyed by `CellType` keeps
the eat path generic. No bare scoring literals in the eat/collision paths.

### Finding 9: Test fidelity
**Category:** pass
`tests/test_dragon_eat_collision.py` (334 lines) exercises the real `resolve_dragon_collisions` with
real `Dragon`/`Lives`/`Score` objects (a stub SoundManager recording calls, no mocking of the branch
logic), covering the v0.3.0 life-loss regression, combo escalation, top-value clamp, den respawn,
and no-collision no-op. This corroborates my independent behavioral verification -- tests do not mock
away the critical conditions.

## Autonomous criterion (Step 6.12)
- Force-promoted WAITING->BACKLOG manually? NO
- release-state.md edited outside CM scripts? NO (Active RC v0.4.0, opened by
  CM-20260806-050-open-rc-0-4-0; schema well-formed)
- Scripts patched mid-build? NO (RC diff touches only game source + tests; no tooling files)
- Queue files manually edited? NO evidence

Manual interventions required: none. All six RC commits are the three CODER feature merges,
authored by the chain identity, sequenced 13:45-14:21 UTC on 2026-08-06.

## Caveats
- Gated-runner methodology N/A. TESTER.md Step 5a mandates the kanban-self gated runners as the
  authoritative PASS source. This game project has no `team/` tree; the requirements doc and task
  README are the more specific governing layers and specify `py_compile` + inspection. I used the
  project's own pytest suite (163 passed) plus independent behavioral triggering as the test signal.
  Verification completed fully -- not a BLOCKED condition.
- VERSION / CHANGELOG still read v0.3.0 on the RC. Expected: git history shows these are bumped at
  release time by dedicated release commits ("Add committed VERSION for vX.Y.Z"), not on the RC
  feature branches. They are not listed as RC deliverables in the requirements doc. No finding.
- Step 6.8 synthetic release simulation skipped -- the RC diff touches no release-lifecycle files.
- Manual/visual acceptance items (distinct power-up look, vulnerable color shift, on-screen
  dragon-eating) require a display; verified structurally in the render code and via the render unit
  tests, not by pixel inspection, per the requirements doc's "headless: inspection + compile"
  guidance.
