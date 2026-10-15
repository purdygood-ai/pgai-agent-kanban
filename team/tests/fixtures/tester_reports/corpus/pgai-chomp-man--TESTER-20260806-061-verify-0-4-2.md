# Verification Report: rc/v0.4.2 (pgai-chomp-man)

## Recommendation
PASS

## Systemic Risk
low

## Summary
Verified release candidate `rc/v0.4.2` of the pgai-chomp-man game against its
requirements bundle (`requirements/v0.4.2-priority-bundle-20260806.md`), which
bundles the single priority item `PRIORITY-0003-extra-life-per-two-dragons`.
The RC adds a cumulative `dragons_eaten_total` accumulator to
`resolve_dragon_collisions()` in `pgai-chomp-man.py` and a `Lives.add_life()`
method in `lives.py`, awarding one extra life every time the cumulative count of
eaten vulnerable dragons reaches a multiple of `DRAGONS_PER_EXTRA_LIFE` (2).
The worktree HEAD (`ab19ad6`) matches `rc/v0.4.2`, the tree is clean, no stashes
are present, and both feature commits merged cleanly. `python3 -m py_compile`
passes on every changed file. The full pytest suite is green (177 passed), and
an independent behavioral re-derivation of the award logic confirmed the
floor(total/2) award schedule for 1..8 dragons, no-award on odd counts, and a
single award when two dragons are eaten in one tick. No bugs, gaps, or stale
assertions were found.

## Note on suite verdict source
pgai-chomp-man is a standalone game project, not the kanban self-build. It ships
no `team/scripts/run-unit-tests.sh` / `run-integration-tests.sh` gated runners and
no anti-pattern lint pre-flight (there are no `.sh` files in the dev tree at all).
The gated-runner PASS precondition in TESTER.md applies to the kanban-self project;
for this project the authoritative test infrastructure is `python3 -m pytest`,
which was run directly with the SDL dummy video/audio drivers. This is disclosed
so the PASS is honest: there is no failing gated runner being masked -- there is no
gated runner for this project.

## Findings

### Finding 1: PRIORITY-0003 award logic (AC1)
**Category:** pass
Eating 2 dragons yields exactly +1 life; 4 yields +2; odd counts (1, 3) yield no
new life until the next even dragon. Verified three ways: (a) the production
logic `if dragons_eaten_total % DRAGONS_PER_EXTRA_LIFE == 0: lives.add_life()`
after a per-dragon increment; (b) the dedicated test suite
`tests/test_dragon_eat_life_award.py` (odd/even, two-at-once, 3-yields-1,
4-yields-2); (c) an independent TESTER-run re-derivation:
`(total_eaten, lives_gained)` for 1..8 == `[(1,0),(2,1),(3,1),(4,2),(5,2),(6,3),(7,3),(8,4)]`,
matching floor(total/2). Two vulnerable dragons eaten in a single tick correctly
produce total=2, +1 life.

### Finding 2: Counter persistence across death, reset on new game (AC2)
**Category:** pass
`dragons_eaten_total` is a `run()`-local threaded through the loop and back
through `resolve_dragon_collisions()`; a NORMAL-dragon collision (death) costs a
life via `apply_life_loss()` and resets positions but does not touch the counter.
`test_dragons_eaten_total_persists_across_a_death_and_still_awards_the_2nd_life`
exercises eat->die->eat and confirms the 2nd eat still fires the award. Reset on a
new game is satisfied by the plain `dragons_eaten_total = 0` local re-init on each
fresh `run()` call (no restart flow exists this release);
`test_a_new_game_starts_the_counter_back_at_zero` pins the accumulator contract.

### Finding 3: Lives HUD updates immediately (AC3)
**Category:** pass
`Lives.add_life()` increments `self.remaining`, and `scoring.render_score_hud()`
reads `lives.remaining` live, so the award reflects on the next HUD render with no
separate display path. Tests assert `lives.remaining` directly after each award.
The literal on-screen visual is manual confirmation per the priority's own
TESTER notes; the programmatic path is verified.

### Finding 4: Compilation (AC4)
**Category:** pass
`python3 -m py_compile lives.py pgai-chomp-man.py` and `python3 -m py_compile *.py`
both succeed. Bundle AC "all bash files pass syntax check" is trivially satisfied
(no bash files in the project).

### Finding 5: No regression (AC5)
**Category:** pass
Full suite: 177 passed in 2.00s. Existing dragon-eat scoring/combo, power-pellet,
dragon respawn (send-to-den), and board-clear behavior are unchanged -- the diff
only ADDs the cumulative counter and threads a third tuple element; the
`score.add(dragon_eat_bonus(...))` scoring line is untouched.
`tests/test_dragon_eat_collision.py` was updated to the new 3-tuple signature and
adds assertions that the two accumulators (combo bonus vs. cumulative total) do not
interfere -- legitimate same-RC CODER changes, not stale assertions.

### Finding 6: Bundle acceptance criteria
**Category:** pass
Each bundled item (PRIORITY-0003) is addressed in the implementation; all Python
files compile cleanly; there are no bash files to syntax-check.

## Test Fidelity
The new tests call the real `resolve_dragon_collisions()` production function
directly, threading both accumulators exactly as `run()`'s main loop does, with a
recording fake only for the sound manager (no game logic mocked away). The
death-persistence test drives a real NORMAL-dragon collision through the real
life-loss path. Fidelity is high for a headless grid/data function. One minor,
acceptable gap: the "new game reset" test pins the accumulator contract (a fresh
0 local) rather than invoking `run()` itself, which is reasonable since `run()`
opens a pygame window and there is no restart flow this release; the production
re-init is a plain `= 0` local and is trivially correct by inspection.

## Autonomous criterion (Step 6.12)
1. Tasks force-promoted WAITING->BACKLOG manually? NO -- no evidence in task logs/status.
2. release-state.md edited outside cm-open/release/cancel scripts? NO -- `RC Opened By Task` reads `cm-open-rc.sh (manual)`, i.e. the sanctioned CM open script invoked manually, not a hand-edit of the file.
3. Scripts patched mid-build? NO -- the RC diff touches only `lives.py`, `pgai-chomp-man.py`, and two test files; no tooling/script files.
4. Queue files manually edited? NO -- no evidence.

Manual interventions required: none. The RC opening was a manual invocation of
the standard `cm-open-rc.sh` script (a sanctioned path), which does not indicate a
defect.

## Caveats / Observations (non-findings)
- VERSION lags at v0.4.1 in the RC. The `VERSION` file and `CHANGELOG.md` are
  not touched by this RC. Prior-release history (`v0.4.0..v0.4.1`) shows VERSION and
  CHANGELOG are bumped by CM at release time on `main` after the squash ("Add
  committed VERSION for v0.4.1", "Update CHANGELOG.md for v0.4.1"), not within RC
  feature commits. So the lag is the expected shape, not a regression. Not a finding.
- PRIORITY-0003 cited in code/test comments. Production comments in
  `pgai-chomp-man.py` and docstrings in the test files reference `PRIORITY-0003`.
  The kanban's coding-standards directive 8 (Step 6.14) discourages citing a
  task/bug/version in production strings, but that standard governs the kanban
  self-build; this game project ships no coding-standards doc and already carries a
  pre-existing PRIORITY reference on `main`, so the citation follows established
  project convention. Noted as an observation, not elevated to a finding.

## Pre-flight
- Worktree HEAD `ab19ad6` == `rev-parse rc/v0.4.2` -- match.
- `git status --short` clean; `git stash list` empty.
- Merge verification: the single DONE prerequisite task's work
  (`CODER-20260806-059-extra-life-per-two-dragons`) is present on the RC as commit
  `73bc6ae`, merged via `ab19ad6` -- no unmerged/zero-commit anomalies.
- Semver drift: RC `v0.4.2` > Last Released `v0.4.1` -- OK.

## Pass-verdict basis
- All acceptance criteria (bundle + PRIORITY-0003 AC1-AC5) met.
- `python3 -m pytest`: 177 passed, exit 0 (authoritative test infrastructure for
  this project -- no gated runner exists in the dev tree).
- No `bug`, `gap`, or `stale-assertion` findings.
- Autonomous criterion: no manual intervention indicating a defect.
