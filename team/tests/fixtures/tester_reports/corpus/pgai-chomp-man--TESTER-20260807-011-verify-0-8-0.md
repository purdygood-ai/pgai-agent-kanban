# Verification Report: rc/v0.8.0 (pgai-chomp-man)

## Recommendation
PASS

## Systemic Risk
low

## Summary
Verified release candidate `rc/v0.8.0` of pgai-chomp-man ("Four Dragons with
Distinct Movement Personalities") against its requirements document
(`requirements/v0.8.0-chompman-dragon-personalities.md`). The RC adds four
per-dragon movement strategies (two independent random roamers, one
power-pellet-gated soft follower, one greedy chaser) via a grep-able
`DRAGON_ROSTER` -> `BEHAVIOR_STRATEGIES` dispatch abstraction. Pre-flight merge
verification passed (HEAD matches `rc/v0.8.0`, tree clean, no stashes, all three
prerequisite feature branches merged and cleaned up). The full test suite passes
(258 passed, exit 0). All nine acceptance criteria are met, confirmed by both the
project's own tests and independent behavioral verification I wrote and ran
against the real `dragons` module and a real maze. The game launches and exits
cleanly headless. No new external dependencies. Zero bugs, gaps, or
stale-assertions. No manual intervention detected in the build. Clean release.

## Test Suites
This project is a standalone pygame game repository -- it has no `team/` directory
and therefore no `run-unit-tests.sh` / `run-integration-tests.sh` gated runners
(those are a kanban-self construct). The authoritative suite for this project is
its own pytest suite. Result:

- `python3 -m pytest tests/` -> 258 passed, exit 0 (SDL dummy video/audio drivers).
- Targeted regression + new-test subset (warp tunnel, dragon render, board clear,
  dragon movement, hunter cadence, power pellets) -> 82 passed.
- No skipped tests anywhere in `tests/` (Step 6.13 skip-cites-real-bug gate: N/A, no skips).
- `py_compile` of all changed `.py` files: OK.

## Code Coverage
coverage unavailable (no coverage instrumentation configured for this project;
informational only, does not affect the recommendation).

## Findings

### Finding 1: Four distinct movement strategies wired per dragon (AC8) -- pass
`DRAGON_ROSTER` (dragons.py:126-131) assigns one `behavior` string per dragon:
`hunter` (Ember), `tracker` (Frost), `rover` (Nightshade), `drifter` (Viper).
`BEHAVIOR_STRATEGIES` (dragons.py:479-497) maps each to a strategy function;
`Dragon.step()` dispatches through it and raises `ValueError` on an unrecognized
behavior (fail-fast, verified). Behavioral check `four_behaviors_assigned: True`.

### Finding 2: rover/drifter independent random roamers that diverge (AC2) -- pass
Both point at `_random_roam_direction` but each `Dragon` gets its own OS-seeded
`random.Random()` in `__init__` (dragons.py:561) and `create_dragons()` passes no
shared `rng`. Behavioral check: starting rover and drifter from the same cell,
their 60-step paths differ (`rover_drifter_diverge: True`) and they hold distinct
RNG instances (`rover_drifter_independent_rng: True`).

### Finding 3: tracker soft-follows while NORMAL, stops while power pellet active (AC3) -- pass
`_tracker_direction` gates on `dragon.state is DragonState.VULNERABLE` at the top,
falling back to plain random-roam when a pellet is active. Behavioral checks:
`tracker_follows_direction_normal: True` (takes Chomp Man's facing direction when
legal) and `tracker_vulnerable_roams: True` (identical to `choose_dragon_direction`
roam output while VULNERABLE, ignoring Chomp Man's position).

### Finding 4: hunter greedy-closes distance while NORMAL (AC4) -- pass
`_hunter_direction` picks the non-reversing candidate minimizing Manhattan
distance to Chomp Man's tile. Behavioral check `hunter_closes_normal: (28, 0, True)`
-- from a far cell it drove distance to Chomp Man from 28 to 0 within 200 steps.

### Finding 5: all four dragons honor power-pellet/frightened state (AC5) -- pass
On power-pellet eat the game calls `set_dragons_vulnerable(dragons)`
(pgai-chomp-man.py:666), flipping every dragon to VULNERABLE. Both target-seeking
strategies gate on that state (`hunter_vulnerable_roams: True`,
`tracker_vulnerable_roams: True`); the two roamers never target Chomp Man at all.
No dragon ignores power pellets.

### Finding 6: v0.6.0 sprites and v0.7.0 warp tunnels not regressed (AC6) -- pass
`Dragon.draw()` preserves the winged-silhouette polygon/circle rendering and the
`VULNERABLE_COLOR` flee shift. `open_directions()`/`step()` still resolve moves
through `maze.wrap_target()`, so dragons traverse the warp tunnels.
`test_warp_tunnel.py` and `test_dragon_render.py` pass. The two edited test files
only adapt tuple unpacking to the new 3-element roster `(name, color, behavior)`
-- not a weakening.

### Finding 7: board remains clearable / game winnable (AC7) -- pass
`hunter` runs on a separate, slower cadence (`HUNTER_MOVE_STEP_INTERVAL_MS = 210`
vs the player's 150ms) specifically to keep the greedy chaser beatable -- the
requirements' "tune the chaser's decision cadence, not its identity" lever.
`test_hunter_cadence.py` and `test_board_clear.py` pass.

### Finding 8: game runs with four dragons active in normal play (AC1) -- pass
Headless launch (`SDL_VIDEODRIVER=dummy`) of `run()` initialized cleanly and exited
cleanly on a posted QUIT event ("GAME LAUNCH: clean exit OK"). All four dragons
spawn unconditionally via `create_dragons()` (no flag gate).

### Finding 9: no new external dependencies (AC9) -- pass
`requirements.txt` unchanged in the RC diff (still `pygame==2.6.1`). dragons.py
imports only stdlib (`enum`, `random`, `typing`) plus `pygame` and the existing
`maze` module.

## Autonomous criterion
Step 6.12 -- Manual interventions required: none.
1. Tasks force-promoted WAITING->BACKLOG manually? NO (this TESTER task's own
   promotion was performed by the wake script per its status Summary -- automatic,
   not a hand edit).
2. `release-state.md` edited outside CM scripts? NO -- it reflects `cm-open-rc.sh`
   output: `Active RC: v0.8.0`, opened by CM-20260807-010, `Last Released: v0.7.0`.
   Consistent with the live `rc/v0.8.0` branch; no drift.
3. Scripts patched mid-build? NO -- the RC diff touches only game files
   (`dragons.py`, `pgai-chomp-man.py`, `tests/*`, `release-notes/v0.8.0.md`); no
   tooling/subagent files.
4. Queue files manually edited? NO evidence.

## Caveats / Observations
- VERSION and CHANGELOG still read v0.7.0 on the RC. This is the established
  project convention, not a gap: every prior release carries a distinct
  "Add committed VERSION for vX.Y.Z" commit on `main` at release time, and
  `CHANGELOG.md` is auto-generated by `changelog_writer.py`. The v0.8.0 stamping
  happens at CM-release, not inside the RC. Noted so CM confirms it applies the
  version bump at ship time.
- Step 6.1 drift check: RC version `v0.8.0` is strictly greater than Last Released
  `v0.7.0` (semver). OK.
- Movement feel/difficulty is inherently observational; the operator will watch
  the four dragons. Code-level distinctness and the winnability lever are verified;
  final difficulty tuning is a future-release concern the release notes already flag.
