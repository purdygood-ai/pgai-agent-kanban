# Verification Report: rc/v0.2.0 — Pellets and Scoring (pgai-chomp-man)

## Recommendation
PASS

## Systemic Risk
low

## Summary
Verified release candidate `rc/v0.2.0` of the pgai-chomp-man game against its
requirements document (`requirements/v0.2.0-dots-and-scoring.md`). The RC adds
collectible pellets to the maze corridors, eat-on-enter logic that removes a
pellet and adds to a running score, a live score HUD, and a board-clear (win)
condition with an on-screen "BOARD CLEAR" message. Verification ran the project
test suite (51 tests, all pass), compiled every changed/added .py file, executed
the headless module sanity checks, and ran an end-to-end behavioral simulation
eating every pellet on the shipped default maze. All six acceptance criteria are
met. No bugs, gaps, or stale assertions were found. The build shows no evidence
of manual intervention.

## Environment / Pre-flight
- Worktree: /tmp/pgai_kanban_tmp/projects/pgai-chomp-man/worktrees/TESTER-20260806-031-verify-0-2-0
- HEAD b46adf9 matches rc/v0.2.0 exactly (detached HEAD, as expected).
- Working tree clean; `git stash list` empty.
- Pre-flight merge verification: PASS. All three prerequisite CODER feature
  branches (feature/CODER-20260806-027/028/029) are merged into the RC (merge
  commits present in `git log main..rc/v0.2.0`) and were deleted post-merge --
  normal cleanup, so Check 1 / Check 2 are skipped per the missing-branch rule.
- python3 3.12.12, pytest 8.4.2, pygame 2.6.1 all present. SDL dummy
  video/audio drivers used for headless runs.

## Note on the gated-runner precondition
The TESTER.md PASS precondition names team/scripts/run-unit-tests.sh and
team/scripts/run-integration-tests.sh. Those runners belong to the
kanban-self-build's test infrastructure and do not exist in this game project --
pgai-chomp-man has no team/ tree. This project's authoritative test harness is
its own tests/ pytest suite, which is exactly what the requirements doc's
"Notes for TESTER" section directs (py_compile + code inspection + pytest).
Treating the absence of self-build-specific scripts as an infrastructure BLOCK
would contradict the requirements doc; the project's pytest suite is the correct
signal here and it exits 0. Recorded as a caveat, not a finding.

## Test Suite
- `python3 -m pytest tests/ -v` -> 51 passed, exit 0 (output captured at
  /tmp/pgai_kanban_tmp/tester/pytest_output.txt).
  - New this RC: tests/test_maze_pellets.py (10), tests/test_scoring.py (9),
    tests/test_board_clear.py (4). Pre-existing: display-scaling and
    player-direction suites, all still green.
- No skipped or xfail tests (Step 6.13 grep clean -- nothing to cite a bug for).

## Code Coverage
coverage unavailable -- this project ships no coverage-instrumented runner and
--coverage is not part of its pytest invocation. Informational only; does not
affect the recommendation.

## Findings

### Finding 1: AC1 -- py_compile passes for all changed/added .py files
Category: pass
`python3 -m py_compile *.py tests/*.py` exited 0. Every source and test file
compiles.

### Finding 2: AC2 -- pellets placed on open corridor cells only, never in walls
Category: pass
Maze.place_pellets() converts only CellType.OPEN cells to CellType.DOT; walls
are untouched by construction. Behavioral check on the shipped DEFAULT_LAYOUT:
138 pellets placed, 0 pellets on any wall cell, and Chomp Man's start cell (1,1)
correctly excluded (no free pellet at spawn).
tests/test_maze_pellets.py::test_place_pellets_only_converts_open_cells confirms
the same invariant.

### Finding 3: AC3 -- moving onto a pellet removes it and increases score
Category: pass
scoring.eat_at() is a cell-occupancy test that reads the cell's CellType, looks
up its point value, clears the pellet, and adds the points. Wired into the main
movement loop in pgai-chomp-man.py (eat_at(maze, player.row, player.col, score)
after each step). End-to-end sim eating all 138 pellets produced a final score of
1380 (138 x 10). Re-eating an already-eaten cell is a no-op (returns False, score
unchanged) -- verified the Step 6.5 double-count failure mode.

### Finding 4: AC4 -- score displayed on HUD and updates live
Category: pass
scoring.render_score_hud() renders "SCORE: <total>" and is called every frame in
the main loop, positioned inside the top-left margin strip so it never overlaps a
corridor cell. Confirmed the renderer runs headlessly without error.

### Finding 5: AC5 -- board-clear fires when the last pellet is eaten
Category: pass
board_clear.is_board_clear() reads Maze.remaining_pellet_count() live off the
grid (single source of truth -- no parallel counter to drift). The main loop sets
board_cleared only when an eat drops the count to zero, then halts movement and
draws the "BOARD CLEAR" overlay. Behavioral sim: the win condition read False for
every pellet eaten except the last, and flipped True exactly on the final eat.

### Finding 6: AC6 -- scoring values are named constants, not magic numbers
Category: pass
PELLET_POINTS = 10 and the POINT_VALUES registry (keyed by CellType) are the
single source of point values. eat_at() resolves points via a generic dict lookup
(points_for_cell_type()) with no bare literals in the eat path, structured so
v0.4.0 power-ups / v0.5.0 fruit register a new CellType + entry without touching
the eat logic. Grep confirmed no stray magic numbers in the scoring logic (only
match was a "60 FPS" mention inside a docstring).

## Autonomous criterion (Step 6.12)
1. Tasks force-promoted WAITING->BACKLOG manually? NO -- this task's status.md
   records automatic promotion "by wake script."
2. release-state.md edited outside cm-open/release/cancel scripts? NO -- it was
   populated by cm-open-rc.sh; no hand-edit evidence.
3. Scripts patched mid-build? NO -- the RC diff touches only game source files
   and their tests; no tooling files changed.
4. Queue files manually edited? NO -- no evidence.

Manual interventions required: none.

## Caveats / Observations
- VERSION reads v0.1.2 and CHANGELOG.md has no v0.2.0 entry on the RC. This is
  the expected shape: past releases show CM authors the "Add committed VERSION
  for vX.Y.Z" commit and changelog_writer.py regenerates the CHANGELOG at release
  time. These are CM release-lifecycle artifacts, not RC feature work, so their
  pre-bump state is not a finding.
- Visual acceptance items (pellets visibly rendered in corridors, HUD
  legibility, message legibility) were verified by code inspection plus headless
  renderer execution -- a true pixel check needs a display, unavailable in this
  headless environment. The rendering code reads purely from the grid and passes
  its headless runs; no concern surfaced.

## Conclusion
All acceptance criteria met, full test suite green, behavioral integration
verified end-to-end, no manual intervention. Clean release -- recommend PASS.
