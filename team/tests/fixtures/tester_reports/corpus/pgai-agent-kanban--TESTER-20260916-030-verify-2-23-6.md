# Verification Report: rc/v2.23.6

## Summary

Verified release candidate `ai_rc/v2.23.6` of `pgai-agent-kanban` at commit
`92f5dfa` against `requirements/v2.23.6-bugfix-bundle-20260916.md`, a four-item
BUG bundle (BUG-0299 recaptured golden, BUG-0300 wake-flag scrub in the CM-script
test env helper, BUG-0301 stale-literal advisory blind to recorded-output
fixtures, BUG-0302 provenance citation in an assertion message plus the lint
narrowing behind it). Work was done in the detached read-only worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260916-030-verify-2-23-6`.
Both gated runners exited 0. All four bundled items are implemented and were
verified behaviourally, not structurally: each fix was exercised against the
failure mode it was filed for, and three of the four were additionally confirmed
to be regression-guarded by reverting the production change and watching the
suite go red. The fourth — the BUG-0300 scrub — is present and correct but has
no guard: removing it leaves the estate fully green on the default invocation
path. That single gap is the only finding, filed as BUG-0303.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Findings

### Finding 1: the agent-wake scrub ships without a regression guard

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (the inherit-then-delete environment pattern recurs in any fixture that drives a real subprocess, so the same blind spot can be reintroduced elsewhere)
**Filed As:** BUG-0303-wake-flag-scrub-has-no-guard (Path C-A — filed as BUG: code defect / regression / test failure)

The BUG-0300 fix is one line —
`scrub_agent_scoped_env(base_env)` at
`team/tests/integration/test_cm_release_synthetic.py:277` — and nothing in the
suite asserts it happens. I proved the exposure rather than inferring it: in a
throwaway clone I deleted that line and re-ran the affected modules.

```
# fix reverted, no wake flag (ordinary developer / cron / CI path)
env -u PGAI_AGENT_WAKE_ACTIVE python3 -m pytest \
  "tests/integration/test_ship_policy_acceptance.py::TestOperatorForceShipsOverriddenTrigger" \
  tests/integration/test_cm_release_synthetic.py -q
16 passed in 41.67s

# fix reverted, wake flag present
PGAI_AGENT_WAKE_ACTIVE=1 python3 -m pytest \
  "tests/integration/test_ship_policy_acceptance.py::TestOperatorForceShipsOverriddenTrigger" -q
1 failed   (tests/integration/test_ship_policy_acceptance.py:866)
```

The two runs differ only in an inherited environment variable, so whether the
estate notices the fix disappearing is a property of the host, not of the tree —
the same coupling class the fix exists to remove, and the reason BUG-0300 stayed
invisible until an agent wake ran the suite. `AGENT_SCOPED_ENV_NAMES` carries the
same exposure: no test asserts its contents or that every name in it is removed.

This is a spot-check finding under Step 6.11 ("untested new mechanisms"), not an
unmet acceptance criterion — BUG-0300 itself *is* addressed (Finding 3). Nothing
is broken today; the risk is a silent regression resurfacing as an unexplained
red line in a future RC verification.

### Finding 2: BUG-0299 — engine-baseline board-render golden recaptured

**Category:** pass

- `team/tests/fixtures/engine-baseline/board-render.txt:2` now reads
  `no fresh snapshot (missing or unreadable) -- rendering directly`, matching the
  shipped narration at `team/scripts/dashboard/lib/snapshot.sh:285` byte for byte.
- The diff against `ai_main` touches exactly one line of one golden; `wake-log.txt`
  and `ship-summary.txt` come back byte-identical, as the bug required.
- The recaptured line is the normal-volume missing/unreadable sentence, not the
  `-debug` routine-expiry line — the bug's warm-cache failure mode did not occur.
- `python3 -m pytest tests/integration/test_engine_behavior_baseline_harness.py -q`
  → `4 passed`, including the two assertions named in the bug.
- Guard confirmed: restoring the pre-release golden turns
  `test_compare_against_goldens_is_clean_on_unmodified_tree` and
  `test_compare_is_stable_across_invoking_shell_budget_values` red (`2 failed, 2 passed`).

### Finding 3: BUG-0300 — CM-script test env helper no longer inherits the wake signal

**Category:** pass

The fix routes through the tree's existing shared scrubbed-environment helper
rather than adding a one-off `pop`, as the bug asked:
`team/tests/fixtures/scrubbed_subprocess_env.py` gains `scrub_agent_scoped_env`
and `AGENT_SCOPED_ENV_NAMES` beside the pre-existing `build_scrubbed_env`, and
`_run_cm_script` calls it on its inherited base *before* layering `extra_env`, so
a caller that wants the wake context still gets it explicitly.

Behavioural verification, both directions (Step 6.5 — original failure mode and
new normal case, plus proof the guard was not merely disabled):

| Case | Command | Result |
|---|---|---|
| Original failure mode | `PGAI_AGENT_WAKE_ACTIVE=1 pytest ...::TestOperatorForceShipsOverriddenTrigger` | 1 passed |
| New normal case | `env -u PGAI_AGENT_WAKE_ACTIVE pytest ...::TestOperatorForceShipsOverriddenTrigger` | 1 passed |
| Guard still enforced | `PGAI_AGENT_WAKE_ACTIVE=1 pytest ... -k force_from_agent_wake_context` | 1 passed (still refuses the ship) |

The whole gated integration suite also ran from inside this agent wake with
`PGAI_AGENT_WAKE_ACTIVE=1` present and exited 0 — the invocation path that was
red on the previous RC.

The absence of a regression guard for this fix is Finding 1.

### Finding 4: BUG-0301 — stale-literal advisory now sees recorded-output fixtures

**Category:** pass

`literal_grep_recorded_output` is added to
`team/scripts/lib/literal-extraction.sh` as a second, independent search surface
(bare `grep -rn -F`, `--binary-files=without-match`), and
`coder-stale-literal-check.sh` drives it over `fixtures/engine-baseline` and
`fixtures/golden` under each test directory — selected by location, explicitly,
not by guessing at content. The assertion-context search is left untouched, as
the bug required.

Verified against this release's own historical case, which is the strongest
available evidence. In a throwaway clone checked out at `origin/ai_main` — where
the production narration is already the new sentence and the golden still holds
the old one — I ran the advisory over the commit that changed the narration
(`4398161^..4398161`), first with the pre-release scripts, then with only the two
v2.23.6 scripts overlaid and nothing else changed:

| Scripts | board-render hits |
|---|---|
| pre-release | 0 |
| v2.23.6 | `team/tests/fixtures/engine-baseline/board-render.txt:2` naming the stale line |

Other required properties confirmed: the check still exits 0 in both runs (it
stays advisory, not a gate); a range touching no test-visible literal still
prints the `(none …)` form; `--help` documents both search surfaces. Guard
confirmed: reverting the two scripts to their pre-release state turns the new
`test_literal_extraction_bash.py` and
`test_coder_stale_literal_check_recorded_output.py` red (`7 failed, 3 passed`).

### Finding 5: BUG-0302 — provenance citation removed and the lint narrowed

**Category:** pass

`team/tests/unit/test_dashboard_activation_hook_lifecycle.py:382` no longer
carries the internal identifier; the assertion's logic is untouched and the file
now holds no `BUG-NNNN` token at all. `python3 team/scripts/lint_hygiene.py
--classes provenance` reports `OK — 0 findings` across all 8 scan directories.

The optional narrowing was taken, and the required sweep-first ordering was
honoured — `c335323` reworks assertion messages across 20 test modules in the
same commit as the lint change, touching no assertion's logic (spot-read the
hunks in `test_discovery_pipeline.py`, `test_changelog_writer.py`,
`test_ledger_filename_contract.py`: message prose only).

Behavioural verification of the narrowing (Step 6.5), against a synthetic probe
module covering each position the exemption must and must not still cover:

| Probe | Position | Expected | Observed |
|---|---|---|---|
| `assert bug_id == "BUG-0001"` | assert condition (test data) | exempt | not flagged |
| `fixture_name = "BUG-0001-some-slug.md"` | ordinary literal (test data) | exempt | not flagged |
| `assert value == 1, "... (BUG-0298 ADDENDUM) ..."` | assert message | flagged | flagged (line 17) |
| `pytest.fail("... (BUG-0298) ...")` | fail argument | flagged | flagged (line 23) |
| `assert sorted(items) == ["a","b"], "..."` | comma nested in condition | no false positive | not flagged |

The lint still exits 0 with findings present, so it remains quality-class and
non-blocking. The skip-annotation exception is intact:
`team/scripts/lint_skip_bug_gate.sh` reports `OK — all skip annotations are
compliant`, 0 missing files. Guard confirmed: reverting `lint_hygiene.py` turns
`test_test_tree_surface_flags_bug_shaped_token_in_assert_message` and
`..._in_pytest_fail` red (`2 failed, 55 passed`).

### Finding 6: requirements acceptance criteria

**Category:** pass

- "Each bundled item is addressed in the implementation" — met; Findings 2-5.
- "All Python files compile cleanly" — `python3 -m py_compile` over all 487
  tracked `.py` files: 0 failures.
- "All bash files pass syntax check" — `bash -n` over all 280 tracked `.sh`
  files: 0 failures.

### Finding 7: Step 2 pre-flight — the RC is intact

**Category:** pass

Every task folder under `projects/pgai-agent-kanban/tasks/` was scanned. Every
`DONE` task naming a `## Feature Branch` other than `none` has had that branch
deleted locally — the normal post-merge shape — so Checks 1 and 2 pass trivially
for all of them, with no anomaly. The four prerequisite CODER tasks are all
`## State: DONE` and all four merges are on the RC:

```
92f5dfa Merge ai_feature/CODER-20260916-028-provenance-sweep-test-tree
e4694fc Merge ai_feature/CODER-20260916-027-literal-check-sees-goldens
88e8ae1 Merge ai_feature/CODER-20260916-026-scrub-wake-flag-in-fixture
7aea059 Merge ai_feature/CODER-20260916-025-recapture-baseline-golden
```

### Finding 8: Step 3 working tree

**Category:** pass

`git rev-parse HEAD` and `git rev-parse ai_rc/v2.23.6` are both
`92f5dfa6bc4124663a48c6358a37a0022c534325`. `git status --porcelain` empty.
`git stash list` empty.

### Finding 9: Step 5a gated suites

**Category:** pass

Run from the dev-tree root, output teed under `/tmp/pgai_kanban_tmp/tester/`.

| Runner | Exit | Result |
|---|---|---|
| `bash team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=0` | 4640 passed, 31 skipped (842.44s) |
| `bash team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=0` | 513 passed, 1 skipped (621.70s) |

Lint pre-flight green on both; post-run `bare_tmp_cleanliness` and
`listener_cleanliness` OK on both. These two exit codes are the PASS-precondition
evidence; no pytest "N passed" line is cited as a suite verdict anywhere in this
report — the direct pytest runs above are targeted behavioural probes.

### Finding 10: Step 5b coverage (informational only)

**Category:** pass

Source: the `totals` object of `/tmp/pgai_kanban_tmp/coverage/unit.json` and
`.../integration.json`; field names were read off the generated files rather than
recalled (`percent_statements_covered`, `percent_branches_covered`,
`percent_covered`).

| Suite | line (statement) % | branch % | blended % |
|---|---|---|---|
| unit | 76.55 | 62.99 | 73.42 |
| integration | 9.88 | 8.00 | 9.45 |

Coverage did not enter the verdict.

### Finding 11: Step 6.1 release-state.md

**Category:** pass

All four fields present and well-formed — `## Active RC` `v2.23.6`,
`## RC Opened At` `2026-09-16T13:55:12+00:00`, `## RC Opened By Task`
`CM-20260916-029-open-rc-2-23-6`, `## Last Released` `v2.23.5`.
`pp_last_released_version "pgai-agent-kanban"` returns `v2.23.5`, and
`semver_gt "v2.23.6" "v2.23.5"` (the shared helper in
`team/scripts/lib/semver.sh`, never an inline compare) returns true.

### Finding 12: Step 6.8 synthetic release simulation

**Category:** pass

The diff touches `team/tests/integration/test_cm_release_synthetic.py` — the
shared driver for every CM script — so the simulation was run rather than
skipped: `python3 -m pytest tests/integration/test_cm_release_synthetic.py -q`
→ `15 passed in 36.80s`, against fresh isolated temp repos. No production
release-lifecycle script (`cm/release.sh`, `cm/open-rc.sh`, `cm/cancel-rc.sh`,
`lib/project_paths.sh`) changed in this RC. No real branch was touched.

### Finding 13: Step 6.9 constraint negative checks

**Category:** pass

`set -euo pipefail` still present in both modified shell files. No hardcoded
credential shape in any added line. The only bug-shaped tokens in added lines are
usage examples and constructed test data inside the new lint and
literal-extraction tests — the first sanctioned exception in `SOP-core.md`
("Naming: describe behavior, not scaffolding or provenance").

### Finding 14: Step 6.10 prerequisite dependency graph

**Category:** pass

`CM-20260916-029-open-rc-2-23-6` (no prerequisites) → `CODER-…-025`, `-026`,
`-027` (each on the CM-open) → `CODER-…-028` (on the CM-open plus the other
three) → `TESTER-20260916-030` (on all four CODERs) →
`CM-20260916-032-release-2-23-6` (on all four CODERs plus this TESTER task). No
cycles, every referenced task ID resolves to a real folder, sequence numbers are
non-colliding, and both lifecycle bookends are linked correctly. Queue markers
match task state in every case (`[x]` for the five DONE tasks, `[A]` for this
one, `[W]` for the pending release).

### Finding 15: Step 6.13 skip-cites-a-real-bug

**Category:** pass

No `BUG-SKIP` placeholder anywhere under `team/tests/`. No skip annotation cites
a letter-suffixed `BUG-` shape. `bash team/scripts/lint_skip_bug_gate.sh` →
`OK — all skip annotations are compliant`, `BUG-NNNN file-existence check: 0
missing files`. The residual grep hits are the gate's own fixture strings in
`test_skip_bug_citation_scan.py` and historical run logs under
`team/tests/artifacts/`, neither of which is a live skip annotation.

### Finding 16: Step 6.14 coding-standards spot-check

**Category:** pass-with-caveat

`coder-stale-literal-check.sh --help` and `lint_hygiene.py --help` both run and
name every argument, and both had their help text updated in the same commit as
the behaviour change, satisfying the Documentation Discipline Rule. The
recorded-output directory list is a named array at the top of the caller rather
than a buried literal. The new `scrub_agent_scoped_env` extends the tree's
existing shared fixture module instead of duplicating a scrub list, which is what
BUG-0300 asked for.

The caveat: `coder-stale-literal-check.sh` still prints its empty-result sentence
as `(none — no test assertions match literals changed in this task's production
diff)` at three sites (lines 30, 259, 271, 314). After this RC the check also
searches recorded-output fixtures, which carry no assertions, so that sentence
now understates what ran — a CODER reading it could conclude goldens were not
covered, which is the exact false confidence BUG-0301 was filed about. No test
asserts the string, and the behaviour itself is correct, so this is a wording
shortfall rather than a defect: recorded here, not filed.

## Lint Findings (non-blocking)

None.

`/tmp/pgai_kanban_tmp/lint/findings-unit.json` — `{"findings": [], "schema_version": 1, "runner": "run-unit-tests.sh"}`, 0 findings.
`/tmp/pgai_kanban_tmp/lint/findings-integration.json` — `{"findings": [], "schema_version": 1, "runner": "run-integration-tests.sh"}`, 0 findings.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=9d freshness=stale — the last cold build was red on the claude-abliterated Dockerfile and two podman flavors and already filed BUG-0167-cold-build-claude-abliterated-failure (now closed), and the record is stale on both counts (9d exceeds the 7-day window, and v2.9.20 is not the v2.23.6 under verification), so nothing is currently known about whether this tree's images build.

## Autonomous criterion

Manual interventions required: none.

| # | Check | Answer |
|---|---|---|
| 1 | Any task force-promoted from WAITING to BACKLOG by hand? | NO |
| 2 | Was `release-state.md` edited outside the three CM scripts? | NO |
| 3 | Were any scripts patched mid-build? | NO |
| 4 | Were any queue files edited by hand? | NO |

Evidence. (1) Every task in the cycle carries `Zero Progress reason_code: none,
count: 0` and `Needs Human: no`; none shows a hand-forced transition in its audit
trail. (2) `release-state.md`'s mtime is `2026-09-16 13:55:12.680902900 +0000`,
matching its own `## RC Opened At` value to the second — written by
`cm/open-rc.sh` and not touched since. (3) The RC's commits do change files under
`team/scripts/` — `lint_hygiene.py`, `coder-stale-literal-check.sh`,
`lib/literal-extraction.sh` — but every one of them is a CODER feature-branch
commit merged into the RC under its own ticket, which is this bundle's assigned
deliverable, not an out-of-band patch to the tooling while a build was in flight.
No commit touches tooling outside a ticket's scope, and none touches `subagents/`.
(4) Queue markers are all derived correctly from task state (five `[x]`, this task
`[A]`, the pending release `[W]`), and every queue file's mtime falls inside the
agent activity window for its lane. No HALT or HALT-AFTER flag is present at
either scope.

## Caveats

- **Deep-verify lane was not run**, per Step 5d. TESTER starts no container
  engine during RC verification; the freshness line above reports the ledger's
  own record. The stale red record raises no systemic risk here on its own and
  did not influence the recommendation.
- **The new literal-check tests embed the real superseded narration string** as
  synthetic fixture data (`test_coder_stale_literal_check_recorded_output.py`
  lines 114/149/175, `test_literal_extraction_bash.py` lines 45/53/151/159/176).
  That is legitimate test data illustrating the interface, but it does mean the
  old sentence still appears under `team/tests/` and will show up in a future
  tree-wide grep for it. Not a finding; noted so the next verifier does not
  mistake it for an unswept golden.
- **`scrub_agent_scoped_env` has no dedicated unit test**, which is the
  substance of Finding 1 / BUG-0303 rather than a separate concern.
- **Test fidelity.** I checked whether the tests covering this RC's changes
  exercise the real breaking condition or a sanitized proxy. Three of the four
  do — each fix goes red when reverted. The fourth does not, and that is the one
  concrete fidelity concern in this RC, filed as BUG-0303 rather than left here.
- **Prior-run artifacts.** The artifacts directory was empty at start; this is a
  fresh verification, not a resumed one. The previous cycle's report
  (`TESTER-20260916-022`, recommendation SHIP-WITH-CONCERNS) was consulted only
  to confirm that the four bugs it filed are the four this bundle closes.
