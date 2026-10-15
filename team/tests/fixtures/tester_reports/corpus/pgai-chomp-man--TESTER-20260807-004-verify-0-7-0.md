# Verification Report: rc/v0.7.0 — chomp-man warp tunnels

## Summary

Verified release candidate `rc/v0.7.0` of pgai-chomp-man against its requirements
doc (`requirements/v0.7.0-chompman-warp-tunnels.md`). The RC adds left<->right warp
tunnels: an opening in the left and right maze walls on one row so a character
moving off one side reappears on the opposite side at the same row. The change is
tightly scoped to three source files (`maze.py`, `player.py`, `dragons.py`) plus a
new test file (`tests/test_warp_tunnel.py`); no AI, speed, scoring, fruit, lives,
sound, or unrelated-wall code was touched. The full project test suite passes
(237 tests, exit 0), the game launches headless without error, and every
chain-verifiable acceptance criterion (1-6) was independently confirmed against the
shipped `DEFAULT_LAYOUT`, including the critical board-clear-integrity regression
check (140 pellets, 0 orphaned). Criterion 7 is an operator play-test that the
requirements doc itself marks NOT chain-verifiable -- flagged for the operator, not a
defect. Zero bugs, gaps, or stale assertions found.

## Recommendation
PASS

## Systemic Risk
low

## Code Coverage
coverage unavailable (informational only -- not gathered; the game project ships no
gated coverage runner and coverage does not affect the recommendation). The full
suite of 237 tests executed and exited 0.

## Test Suite Result

- Authoritative signal: this is a standalone game project, not the kanban
  self-build. It ships no `team/scripts/run-unit-tests.sh` / `run-integration-tests.sh`
  gated runners; the project's own pytest suite under `tests/` is the authoritative
  pass/fail signal. Confirmed the gated-runner scripts are absent in the repo, so the
  direct-pytest-is-not-a-PASS-source rule (written for the kanban self-build) does not
  apply here -- there is no lint pre-flight to bypass.
- `python3 -m pytest tests/` -> 237 passed, exit 0 (SDL_VIDEODRIVER=dummy,
  SDL_AUDIODRIVER=dummy).
- `python3 -m pytest tests/test_warp_tunnel.py -v` -> 17 passed, exit 0.
- `python3 -m py_compile maze.py player.py dragons.py pgai-chomp-man.py` -> OK.

## Pre-flight

- Worktree HEAD `0e42a20` matches `rc/v0.7.0`. Working tree clean; no stashes.
- Feature branch `feature/CODER-20260807-002-warp-tunnel-wraparound` is merged into
  the RC (merge commit present in RC log; branch itself deleted as normal post-merge
  cleanup) -- pre-flight merge verification passes.
- Semver ordering: `v0.7.0 > v0.6.0` (Last Released). Active RC in release-state.md
  is `v0.7.0`, consistent with the RC under test.
- `VERSION` file reads `v0.6.0` in the RC -- this is EXPECTED, not a gap: VERSION is
  bumped by CM at release time in a separate "Add committed VERSION for vX.Y.Z" commit
  (confirmed for every prior release v0.4.0-v0.6.0), never inside the feature branch.

## Findings

### Finding 1: Criterion 1 -- game launches and runs without error
Category: pass
Timed headless launch (`timeout 3 python3 pgai-chomp-man.py`, dummy SDL drivers) ran
the event loop for 3s and was killed by timeout (exit 124) with no traceback,
exception, or error in output. Existing tests pass.

### Finding 2: Criterion 2 -- maze opening at left AND right on the tunnel row
Category: pass
`DEFAULT_LAYOUT` row 7 (of 15, the vertical middle) changed from `#.#.......#.....#.#`
to `..#.......#.....#..`: col 0 and col 18 opened from WALL to OPEN. Independently
confirmed exactly one tunnel row (row 7), it is interior (0 < 7 < 14), and BOTH
boundary columns are passable. Every other interior row still walls both boundary
columns -- no other wall became passable.

### Finding 3: Criterion 3 -- position wrap on the tunnel row
Category: pass
`Maze.wrap_target()` returns `(7, 18)` for an off-left step `(7, -1)` and `(7, 0)`
for an off-right step `(7, 19)` -- same row, on-grid. Returns `None` on every
non-tunnel row's off-edge step and when the column is already in range or the row is
out of bounds. The tunnel row is derived from the grid itself (both boundary columns
open), not a separately tracked index -- consistent with the maze's single-source-of-
truth convention.

### Finding 4: Criterion 4 -- wrap applies to BOTH player and enemies
Category: pass
`player.ChompMan.move()` and `dragons.open_directions()`/`Dragon.step()` all call the
same `maze.wrap_target()` boundary-wrap code path. Independently confirmed: a player
and a dragon both wrap from `(7, 0)` off the left edge to `(7, 18)`. `open_directions`
correctly treats a tunnel-mouth off-edge step as open and a non-tunnel-row off-edge
step as blocked; `Dragon.step` resolves the wrapped target so a dragon never lands
off-grid (200-tick fuzz test + shipped-layout test both green).

### Finding 5: Criterion 5 -- board-clear still achievable (CRITICAL regression)
Category: pass
BFS reachability over the shipped `DEFAULT_LAYOUT` from the player start cell (walls
block, tunnel wraps connect): 140 pellets placed, 0 unreachable. The two
tunnel-mouth edge cells hold ordinary collectible pellets after `place_pellets()`.
The tunnel change adds connectivity only -- it cannot orphan a pellet or soft-lock the
win condition. A dedicated regression test asserts this in the suite.

### Finding 6: Criterion 6 -- py_compile and game test suite
Category: pass
`py_compile` on all changed files succeeds; full suite 237/237 pass.

### Finding 7: Scope discipline -- no forbidden changes
Category: pass
Diff touches only `maze.py` (wrap_target + one layout row), `player.py` (move() wrap
branch), `dragons.py` (open_directions/step wrap branches), and the new test file.
`choose_dragon_direction` (AI weighting), speed, scoring, fruit, lives, sound, and the
entrypoint are unchanged. No unrelated wall opened. Matches the requirements doc's
scope guard exactly.

## Autonomous criterion (Step 6.12)

Manual interventions required: none that indicate a defect.
1. Tasks force-promoted WAITING->BACKLOG manually? NO -- this task's promotion was
   by the wake script (recorded in status.md), automatic.
2. `release-state.md` edited outside CM scripts? NO -- populated by `cm-open-rc.sh`.
   (Field records "RC Opened By Task: cm-open-rc.sh (manual)", the normal
   operator-driven RC open for this sandbox project -- an intended action, not a
   mid-build state edit.)
3. Scripts patched mid-build? NO -- diff touches only game source + test files.
4. Queue files manually edited? NO -- no evidence.

## Caveats

- Operator action item (Criterion 7, NOT a gap): the requirements doc explicitly
  classifies the play-test as operator-verifiable and NOT chain-verifiable. Movement
  feel, visual smoothness of the wrap, and confirming enemies visibly chase through
  the tunnel to a cleared board are the operator's judgment. This is a normal handoff,
  not a defect against the RC -- it does not affect the recommendation.
- Coverage was not gathered (informational only; no gated coverage runner in this
  game project). This is never a finding.
