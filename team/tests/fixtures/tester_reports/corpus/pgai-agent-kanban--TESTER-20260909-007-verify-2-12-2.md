# Verification Report: ai_rc/v2.12.2

## Summary

Verified release candidate `ai_rc/v2.12.2` (commit `ea86cc9`) against
`requirements/v2.12.2-bugfix-bundle-20260909.md` — a nine-item BUG bundle
(BUG-0202 through BUG-0210) implemented by four tickets: CODER-002
(dual-channel probe skip), WRITER-003 (PM ride-along rule), CODER-004
(HALT-scope lint narrowing + branch-prefix routing), CODER-005 (shipped-text
hygiene). Verification ran in the isolated detached-HEAD worktree at
/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260909-007-verify-2-12-2,
whose HEAD matches `ai_rc/v2.12.2` exactly; the worktree was clean before and
after every check and no stashes were present.

Both gated runners exit 0. Every fix was verified behaviorally, not
structurally: the dual-channel probe skip was exercised with all four probe
spellings plus a mixed file carrying a genuinely malformed emitter call; the
HALT-scope narrowing was exercised with three project-scoped negative shapes
and seven fleet-scoped positives spanning all five declaration keywords; the
branch-prefix routing was exercised through the `_is_rc_branch` matrix and the
new synthetic `foo_rc/*` stale-worktree fixture; the hygiene work was verified
by running its acceptance greps and `intake.sh --help` directly. All nine
bundled items are accounted for — six by a landed fix, three (BUG-0207,
BUG-0209, BUG-0210) by verified absence of their subject from this release
base, which is what PM scoped them to.

Two defects were found outside the bundle's own acceptance criteria and are
filed via Path C-A (filed as BUG — code defect / regression / test failure).
Neither prevents the release from working: one is a fallback-path divergence
visible only on an install with no readable `project.cfg`, the other a
pre-existing comment-hygiene class that this RC neither introduced nor was
asked to clear.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Test Suites

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3442 passed, 3 skipped in 386.56s; all quality-class lints OK |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 398 passed, 1 skipped in 278.48s; bare-/tmp and listener cleanliness OK |

Both PASS verdicts are the gated runners' own exit codes, not a direct pytest
invocation. Breakage-class gate: 347 python + 232 shell files, 0 failures.
The synthetic release simulation (`tests/integration/test_cm_release_synthetic.py`,
15 cases) ran green inside the integration suite.

## Code Coverage

- Unit:        line 74%  |  branch 61%   (14233 stmts, 4356 branches; blended TOTAL 71%)
- Integration: line 8%   |  branch 6%    (blended 7% — integration exercises subprocesses, so in-process coverage is structurally low)

Source: coverage/unit.json and coverage/integration.json under the framework
temp root, fields `totals.percent_statements_covered_display` and
`totals.percent_branches_covered_display`. Informational only; coverage did not
enter the PASS precondition.

## Findings

### Finding 1: BUG-0206 — dual-channel lint no longer reads an availability probe as an emitter call

**Category:** pass

`team/scripts/lint_dual_channel_pair.py` gained `_PROBE_TAIL_PATTERNS` and
`_is_availability_probe_operand()`, applied in `_scan_bash_file` against the
line text preceding the identifier match. Verified behaviorally:

- Probe-only fixture containing all four supported spellings (`command -v X`,
  `type -t X`, `type X`, `declare -F X`) produced `OK — 1 file(s) checked,
  0 offenders`, exit 0.
- The pre-change scanner (`git show ai_main:...`) flags all four of those lines,
  proving the fixture exercises the defect rather than passing vacuously.
- Mixed fixture (probe + a real call missing `remedy` + a well-formed call)
  produced exactly one finding, at the malformed call's line 6, not at the
  probe's line 2. The check was narrowed correctly, not disabled.
- Whole tree: `OK — 209 file(s) checked, 0 offenders`.
- The originally failing test,
  `tests/unit/test_lint_dual_channel_pair.py::test_real_tree_produces_zero_findings`,
  passes; the file gained 106 lines of regression coverage.

Note on context: the site that originally tripped the lint
(`config_loader.sh:153`) belonged to the cancelled v2.13.0 RC and does not exist
on this base. The fix is therefore forward-looking rather than symptom-clearing,
which is the right shape — the guarded self-source idiom will recur.

### Finding 2: BUG-0204 — HALT-scope lint stops flagging correct project-scoped writes

**Category:** pass

`_KANBAN_ROOT_HALT_GAP = r'[^A-Za-z0-9/]*'` replaces the unrestricted `.*` in
all three bash patterns. Verified behaviorally against synthetic trees driven
with `--team-dir`:

- Negative fixture with all three write shapes of
  `${KANBAN_ROOT}/projects/${name}/HALT` (assignment+touch, literal touch,
  literal redirect) inside non-`global`-named functions produced
  `OK (no unnamed fleet-scope HALT writers found)`, exit 0. This is the exact
  fixture BUG-0204 reported as a false positive.
- Positive fixture with seven fleet-scope writes covering all five declaration
  keywords (local, declare, readonly, export, typeset) and the quoting variants
  the `.*` was originally written to tolerate (`"${KANBAN_ROOT}"/HALT`,
  `${KANBAN_ROOT:?}/HALT`) produced `FAIL — 7 violation(s)`; all seven are still
  reported.
- Shipped tree: exit 0.

### Finding 3: BUG-0203 — RC-branch detection routed through the configured branch_prefix

**Category:** pass-with-caveat

Four sites changed. Verified:

- `lint_changelog_freshness._is_rc_branch` matrix: ('foo_rc/v1.0.0','foo_')
  True, ('rc/v1.0.0','foo_') True, ('feature/x','foo_') False,
  ('ai_rc/v2.12.2','ai_') True, ('rc/v1.0.0','') True. The bare `rc/`
  alternative is kept everywhere, as the constraint required.
- `_resolve_branch_prefix()` against the live install returns 'ai_', matching
  `project.cfg`.
- `check-stale-worktrees.sh` threads a resolved prefix into
  `_csw_worktree_has_extra_commits`; the new TEST 9 in
  `team/scripts/lib/overwatch-checks/tests/test-check-stale-worktrees.sh`
  configures `branch_prefix = foo_`, merges the feature branch into
  `foo_rc/v1.0.0`, and asserts the worktree is pruned rather than bug-filed.
  Full script: PASS 20, FAIL 0.
- Both gated runners set `PGAI_LINT_CHANGELOG_MODE=rc` correctly on this
  configured install (`changelog_freshness: OK` in both pre-flights).

Caveat: the shell half of this fix diverges from the Python half on the
unconfigured-install fallback. Filed separately as Finding 6 / BUG-0211 — the
happy path verified here is correct.

### Finding 4: BUG-0205 + BUG-0202 — placeholder phrase and unfollowable pointers cleared from shipped text

**Category:** pass

- `grep -rn 'an earlier defect' team/scripts/ team/pgai_agent_kanban/` returns
  no matches (exit 1). 23 occurrences across 9 files were rewritten; every value
  position now carries a value a reader could type (`BUG-NNNN-some-slug.md`,
  `BUG-0042`, `PRIORITY-0007`, `malformed-intake-foo.md`) and every prose
  position states the reasoning inline.
- `bash team/scripts/intake.sh --help` Examples block reads
  `intake.sh --project myproject $PGAI_AGENT_KANBAN_TEMP_DIR/BUG-NNNN-some-slug.md`
  — no embedded spaces, no bare `/tmp` literal.
- `grep -nEi 'this ticket|the task this|this task.s status|a separate ticket|the ticket that' team/scripts/*.py team/scripts/lib/*.sh install.sh`
  returns no matches (exit 1).
- `wake_lifecycle.sh:477`'s contentless four-item `Reference:` line is deleted;
  `wake_task_transition.sh`'s "as this ticket's function" clause is gone.
- `lint_comment_provenance` reports `OK — 0 findings across 547 file(s)` in the
  gated pre-flight.
- The declined lint half (BUG-0202's durable half) is explicitly permitted by
  CODER-005's `## Notes` fallback ("if you decline it, say so in your status
  Summary with the hit count that decided it, and the fourth acceptance
  criterion is then satisfied by recording that evidence instead"), and the
  CODER's status Summary carries the hit counts. Compliant.

140 occurrences of the placeholder phrase remain under `team/tests/`, which is
outside both the ticket's and BUG-0205's acceptance scope (both name
`team/scripts/` and `team/pgai_agent_kanban/` only). Recorded as an observation,
not a shortfall.

### Finding 5: BUG-0208 — PM.md ride-along consolidation rule

**Category:** pass

`team/roles/PM.md` gains "Ride-alongs consolidate into at most two hygiene
tickets" inside PHASE 3. Read in full against WRITER-003's acceptance criteria:

- The annotation contract is stated with its fixed wording, including the em
  dash and the `hygiene ride-along (piggyback on <TRIGGER-ID>)` form, plus the
  three disambiguations (a line ending at its closing parenthesis is headline
  work; every ride-along in one bundle names the same trigger; PM never writes,
  edits or removes the annotation).
- K = 2 is stated as a ceiling on tickets, never on bugs, with the
  twelve-ride-alongs-are-still-two-tickets example that forecloses the exact
  misreading the constraint named, and PM is told not to raise it on its own
  judgment.
- Group-by-area, the fixed decomposition-note sentence, placement as the last
  work tickets ahead of the closing bookends, the rationale (release cost scales
  with agent wakes), and all three boundaries (anti-role split wins; hygiene
  tickets count against the plan-wide cap; the cap governs ride-alongs alone)
  are all present.
- `bash team/scripts/lint_governance_byte_budgets.sh --kanban-cfg kanban.cfg_example`
  prints `under` for all seven roles; pm assembles to 71704 against 73728. No
  budget key was raised.
- `git diff --name-only` for the WRITER commit names `team/roles/PM.md` alone.
- BUG-0208's third acceptance criterion ("no shipped document claims PM behavior
  that PM's role file does not define") holds: grep for `ride-along` and
  `piggyback` across `team/` and `docs/` returns hits in PM.md only. The
  over-claiming sentences BUG-0208 quoted (`discovery_state.sh:629`,
  `SOP-core.md:285`) belonged to the cancelled v2.13.0 tree and are absent here.

### Finding 6: Runner-side branch_prefix fallback drops RC mode where the Python side keeps it

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (a quality-class lint runs in the wrong mode on an unconfigured install; no runtime failure, no fatal exit)
**Regression:** yes (before this RC the runners matched `^(rc/|ai_rc/)` unconditionally; they now match `^(rc/)` when the prefix cannot be read)
**Integrity Damage:** no
**Filed As:** BUG-0211 (Path C-A — filed as BUG: code defect / regression / test failure)

The two halves of the same fix disagree about what to assume when
`projects/<name>/project.cfg` is unreadable or carries no `branch_prefix`:
`lint_changelog_freshness._resolve_branch_prefix()` falls back to `"ai_"`;
`run-unit-tests.sh` and `run-integration-tests.sh` fall back to `""`, which
assembles the pattern `^(rc/)` and no longer matches `ai_rc/v2.12.2`.

Verified by running the runner's own block against a throwaway root with no
project.cfg: NO-MATCH for `ai_rc/v2.12.2`, while the Python half under the same
root returns 'ai_'. The consequence is that the CHANGELOG freshness gate runs
strict where it used to run RC-tolerant, producing a false quality-class
staleness finding. The lint is quality-class, so nothing becomes fatal.

Same site, secondary point: the runner reads
`${TEAM_ROOT}/projects/pgai-agent-kanban/project.cfg` — a hardcoded project name
replacing a hardcoded branch prefix, so the site is still not general.

This is invisible on the operator's own install, which carries
`branch_prefix = ai_`; both runners entered RC mode correctly during this
verification.

### Finding 7: Twenty-two shipped source files point the reader at a requirements/ directory that does not ship

**Category:** bug
**Fix Effort:** medium (3-5 CODER tasks — 22 sites plus the lint half and its unit cases)
**Systemic Risk:** low (comment hygiene; no behavior is affected)
**Regression:** no (identical count on ai_main; `git diff ai_main..ai_rc/v2.12.2 | grep '^+' | grep -c 'requirements/v[0-9]'` returns 0 — this RC added none)
**Integrity Damage:** no
**Filed As:** BUG-0212 (Path C-A)

`docs/coding-standards.md` directive 8 forbids citing requirement versions or
internal framework versions in shipped code. `ls requirements` fails in the
shipped tree, yet 22 files under `team/scripts/` and `team/pgai_agent_kanban/`
carry header comments reading `see requirements/v2.9.20-engine-decomposition.md`
or similar. This is precisely BUG-0202's stated class — "a shipped shell library
still explains one of its design choices by telling the reader to go look at the
internal work ticket behind it, a document nobody holding the release can open"
— surviving the fix pass, because the pass was scoped to a narrower grep.

The sharpest instance is `team/scripts/lib/wake_task_transition.sh:6`: this RC
rewrote the very sentence it sits in and left `v2.9.20-engine-decomposition`
standing in the clause immediately above the words it cleared.

`lint_comment_provenance.py` reports 0 findings on this tree because it matches
identifier shapes, not paths, so nothing mechanical will catch the class or stop
it recurring in the next file that copies the header.

`docs/audits/vX.Y.Z-*.md` citations are explicitly excluded from this finding —
`docs/audits/` does ship, so those pointers are followable.

### Finding 8: BUG-0207, BUG-0209 and BUG-0210 close on verified absence, not on a fix

**Category:** pass-with-caveat

All three describe defects in code that belonged to the cancelled v2.13.0 RC and
does not exist on this release base. PM scoped them to verification rather than
change, and the evidence is recorded:

- BUG-0207 — `grep -n 'CODER-20260908-04' team/tests/integration/test_operator_commands.py`
  returns nothing (exit 1); `lint_comment_provenance` reports 0 findings across
  547 files in the gated pre-flight. Confirmed absent.
- BUG-0209 — `grep -n 'nothing reads them for behavior yet' kanban.cfg_example
  project.cfg_example team/scripts/lib/config_loader.sh` returns nothing
  (exit 1). Confirmed absent.
- BUG-0210 — `team/scripts/lib/defer_low_bugs.sh` does not exist in this tree.
  Confirmed absent.

Caveat for CM: `cm-release.sh` Step 16b will promote all nine bundled items,
including these three, to `## Status: done` on ship. For BUG-0207 and BUG-0209
that is honest — the defect provably does not exist in what ships. BUG-0210 is
the one worth noting: its subject (`defer_low_bugs.sh`) is not merely fixed but
absent, and the v2.13.0 requirements document PM pointed at as the place that
work will return has been renamed to
`v2.13.0-posture-config-deferral-and-piggyback.SUPERSEDED-on-cancel-2026-09-09T00:37:04+00:00.md`,
a shape discovery's strict regex does not match. If the deferral-posture feature
is ever rebuilt, BUG-0210's defect can return with no open ticket tracking it.
Nothing in the shipped tree is wrong; this is a tracking observation, not a
defect in the RC, and it is recorded here rather than filed so it is not
double-counted against the recommendation.

### Finding 9: Structural, syntax and constraint checks

**Category:** pass

- `python3 -m compileall -q team/` exits 0. Requirements AC "All Python files
  compile cleanly": met.
- `bash -n` over every tracked `*.sh` (232 files): 0 failures. Requirements AC
  "All bash files pass syntax check": met.
- All 13 changed Python files individually py_compile clean; all 9 changed shell
  files individually `bash -n` clean.
- Skip-cites-real-bug gate (Step 6.13): no placeholder skip IDs in `team/tests/`
  outside archived coverage logs; every cited BUG-NNNN resolves to a real file.
  The gated pre-flight agrees: `lint_skip_bug_gate: OK`.
- Release-state drift (Step 6.1): `pp_last_released_version` returns `v2.12.1`;
  `semver_gt v2.12.2 v2.12.1` is true; `## Last Released` in `release-state.md`
  is the well-formed `v2.12.1`; `## Active RC` is `v2.12.2` and the branch
  `ai_rc/v2.12.2` exists.
- Pre-flight merge verification: passed. Every DONE task's feature branch is
  either merged into the RC or already deleted post-merge (normal lifecycle);
  no live `ai_feature/*` branches remain and no zero-commits-ahead anomaly.
- Prereq dependency graph (Step 6.10): acyclic, every referenced task ID exists,
  bookends correct (CM-006 open-rc is a prerequisite of all four work tickets;
  TESTER-007 depends on all four; CM-009 release depends on all five). The
  numeric gap at 008 and the bookend numbered above its dependents match the
  established convention in every prior cycle (v2.12.0, v2.12.1) — not drift.
- ICD: `lint_icd_freshness` ok, `lint_icd_compat` ok against all four supported
  baselines. The RC's edit to `docs/api/baselines/icd-v1.3.0.json` is required
  by the shipped byte-identity tests at
  `team/tests/unit/test_lint_icd_compat.py:188` and
  `team/tests/unit/test_review_cmds.py:603`; compat also passes against the
  pre-RC baselines, so nothing was weakened.
- Worktree clean and stash-free before and after all checks; the operator's live
  install and the dev tree were left untouched (`git status --short` empty in
  both).

## Lint Findings (non-blocking)

None.

The unit findings file (generated 2026-09-09T02:46:36+00:00) and the integration
findings file (generated 2026-09-09T02:51:36+00:00) each contain 0 findings.
All quality-class lints reported OK in the unit pre-flight: breakage_class,
dockerfile_static, test_anti_patterns, skip_cites_real_bug, orphan_tests,
api_parity, upgrade_no_temp_copy_guard, icd_freshness, icd_compat,
changelog_freshness, engine_size_dupe, env_bootstrap, help_presence,
comment_provenance, python_import_completeness, dashboard_network_git_gate,
halt_scope_writers, raw_marker_read, dual_channel_pair, gate_census_compliance.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=1d freshness=stale — the docker/claude-abliterated, podman/claude-abliterated and podman/rocky9 pairs failed at their Dockerfile steps; cites BUG-0167, filed by that deep-verify run and since closed as done, so the recorded red predates its own fix; stale because v2.9.20 is not the v2.12.2 under verification, so the images have never been built cold against this tree.

## Autonomous criterion

Evaluated over this RC's build window — `ai_rc/v2.12.2` opened
2026-09-09T00:50:13Z, verification started 2026-09-09T02:37Z.

1. Were any tasks force-promoted from WAITING to BACKLOG manually? NO.
   TESTER-20260909-007's status records "Promoted from WAITING to BACKLOG by
   wake script: prerequisites satisfied." No task in the cycle carries a
   hand-edit marker.
2. Was `release-state.md` edited outside the CM scripts? NO for this RC.
   `## Active RC: v2.12.2`, `RC Opened At: 2026-09-09T00:50:13+00:00`,
   `RC Opened By Task: CM-20260909-006-open-rc-2-12-2` are all consistent with
   `cm-open-rc.sh`, and `release-state/v2.12.2.json` records
   `outcome: in_progress` with the nine-item cargo list.
3. Were any scripts patched mid-build? NO. Every commit on the RC is a `--no-ff`
   merge of a task's own feature branch or a commit on one of those branches.
   Ten commits, four merges, four task IDs, no loose commits.
4. Were any queue files manually edited? NO. Every entry for this cycle carries
   a wake-script-written marker in the standard shape.

Autonomous operation criterion: Manual interventions required: none.

Context, not an intervention against this RC: the preceding RC v2.13.0 was
HALTed by CM on 2026-09-08 (ship-policy matrix, regression=yes on BUG-0206) and
then unwound by the operator with `team/scripts/unwind-rc.sh --key v2.13.0` at
2026-09-09T00:37:04Z — a sanctioned shipped operator verb, executed 13 minutes
before this RC opened. Its residue is clean: the v2.13.0 requirements document
is renamed to the SUPERSEDED-on-cancel form, `release-state/v2.13.0.json`
records `outcome: cancelled`, the `ai_rc/v2.13.0` branch is gone, and no HALT
file exists at either scope. The bugs that RC's TESTER filed were then bundled
into this RC by the ordinary discovery pipeline, which is the loop working as
designed.

## Caveats

- Coverage numbers are informational. Integration line coverage of 8% is
  structurally low because the integration suite exercises scripts as
  subprocesses; it is not a signal about this RC.
- `VERSION` still reads `v2.12.1` on the RC. Expected — `cm-release.sh` stamps
  the version at ship time, not at RC open. Not drift.
- 140 `an earlier defect` occurrences remain under `team/tests/`. Outside both
  the ticket's and BUG-0205's acceptance scope. Not a finding; noted so a future
  hygiene pass knows where the remainder lives.
- Three skipped unit tests and one skipped integration test. The
  skip-cites-real-bug gate passes on all of them; no placeholder IDs, no missing
  bug files.
- The operator's live install carries files this release base does not
  (`scripts/lib/defer_low_bugs.sh` among them), i.e. it is installed from the
  cancelled v2.13.0 line. That is kanban-install state, not RC state, and it did
  not affect any check here — every run was executed inside the RC worktree.
