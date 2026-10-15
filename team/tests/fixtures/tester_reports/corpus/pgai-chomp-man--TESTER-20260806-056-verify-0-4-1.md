# Verification Report: rc/v0.4.1

## Summary

Verified release candidate `rc/v0.4.1` of pgai-chomp-man against its requirements
doc (`requirements/v0.4.1-bugfix-bundle-20260806.md`), a single-item bug bundle
containing `BUG-0002-no-color-change-power-pellet`. The RC adds a powered-up color
cue on the Chomp-Man sprite while the dragon-edible (power-pellet) timer is active.
The diff is small and self-contained: `player.py` (new `POWERED_UP_COLOR` constant
and a `powered_up` flag on `ChompMan.draw()`), `pgai-chomp-man.py` (passes
`powered_up=vulnerable_mode_remaining_ms > 0` -- the same accumulator that gates
dragon edibility), and a new headless pixel-inspection test `tests/test_player_render.py`.
Changed files compile, the full test suite passes (168 passed, including the 5 new
tests), and all bug acceptance criteria are met, including the "same timer, not a
parallel one" constraint. No bugs, gaps, or stale assertions found.

## Recommendation

PASS

## Systemic Risk

low

## Code Coverage

coverage unavailable (game project has no gated `--coverage` runner; plain pytest
is the authoritative suite here -- see Caveats). Suite result: 168 passed, exit 0.

## Findings

### Finding 1: Pre-flight -- worktree identity, cleanliness, stashes
**Category:** pass
Worktree HEAD `481646b` matches `rc/v0.4.1`. `git status --short` clean. `git stash
list` empty. RC version `v0.4.1` is strictly greater than Last Released `v0.4.0`
(git tags confirm v0.4.0 is the highest existing tag). CODER-20260806-054's fix
commit `0fde641` is an ancestor of the RC (merged via `--no-ff` merge `481646b`).
Feature branch deleted post-merge (normal CODER cleanup). No merge anomalies.

### Finding 2: Bundled item BUG-0002 addressed
**Category:** pass
`ChompMan.draw()` now selects `POWERED_UP_COLOR (255,64,224)` over `COLOR
(255,208,64)` when `powered_up` is true, and reverts to `COLOR` otherwise. The call
site in `pgai-chomp-man.py` passes `powered_up=vulnerable_mode_remaining_ms > 0`.

### Finding 3: BUG-0002 AC1/AC2 -- distinct color active, normal color inactive
**Category:** pass
Behaviorally verified via `tests/test_player_render.py`, which opens a real
`pygame.Surface` under the SDL dummy driver and samples the actual drawn body
pixel. `powered_up=True` renders `POWERED_UP_COLOR`; `powered_up=False` (and the
omitted-argument default) renders `COLOR`. Reproduced both the original failure
mode (normal case unchanged) and the new powered-up case per Step 6.5 -- both pass.
Colors confirmed distinct: `(255,208,64) != (255,64,224)`.

### Finding 4: BUG-0002 AC3 -- same timer, no parallel timer
**Category:** pass
`vulnerable_mode_remaining_ms` is the single accumulator set to
`VULNERABLE_MODE_DURATION_MS` at the same site that calls
`set_dragons_vulnerable(dragons)` (pgai-chomp-man.py ~L456-457) and counted down by
`update_vulnerable_timer(...)` (~L539-540), the dragons' own edible-state timer. The
new `powered_up` gate reads that identical value. No new/parallel timer introduced.

### Finding 5: BUG-0002 AC4 -- Python compiles cleanly
**Category:** pass
`python3 -m py_compile player.py pgai-chomp-man.py tests/test_player_render.py` -> OK.

### Finding 6: Bundle criterion -- bash syntax check
**Category:** pass (vacuous)
No bash/shell files are in the RC diff (only `.py`). Nothing to check.

### Finding 7: BUG-0002 AC5 -- no regression
**Category:** pass
Full suite `python3 -m pytest tests/` -> 168 passed, exit 0. Power-pellet timing,
dragon edible appearance, dragon-eat scoring, and board-clear invariant tests all
pass. `powered_up` defaults to `False`, so any un-updated call site keeps prior
behavior (covered by `test_powered_up_defaults_to_false_when_not_specified`).

### Finding 8: Skip-cites-real-bug gate
**Category:** pass
No pytest skip/xfail annotations in the suite. The two grep hits for "skip" in
`tests/test_power_pellets.py` are function/docstring names, not skip markers.

### Finding 9: Test fidelity
**Category:** pass
The new test exercises the real `pygame.draw` path and inspects actual surface
pixels rather than mocking the color choice -- high fidelity. Sampling point offset
left of center to avoid the mouth wedge, with a docstring explaining why.

## Autonomous criterion

Manual interventions required: none.

1. Tasks force-promoted WAITING->BACKLOG manually? NO -- CM-058-release-0-4-1 sits
   at WAITING as expected; no evidence of manual promotion.
2. `release-state.md` edited outside cm-open/release/cancel? NO -- normal cm-open-rc
   population (Active RC v0.4.1, opened by CM-055).
3. Scripts patched mid-build? NO -- RC diff touches only player.py,
   pgai-chomp-man.py, and tests/test_player_render.py; no tooling/scripts.
4. Queue files manually edited? NO -- no evidence.

## Caveats

- No gated test runner in this project. The TESTER role's Step 5a gated-runner PASS
  precondition (team/scripts/run-unit-tests.sh) is kanban-self-build machinery and
  does not exist in the pgai-chomp-man dev tree. For this game project the
  authoritative suite is plain `python3 -m pytest tests/`, which exited 0 (168
  passed). Coverage is recorded as unavailable; informational only.
- VERSION / CHANGELOG unchanged on the RC. `VERSION` reads `v0.4.0` and
  `CHANGELOG.md` tops out at v0.4.0 on the RC branch, matching the project's
  convention: the "Add committed VERSION for vX.Y.Z" commits and the auto-generated
  changelog are produced by CM at release time, not on the RC branch. Not a gap.
