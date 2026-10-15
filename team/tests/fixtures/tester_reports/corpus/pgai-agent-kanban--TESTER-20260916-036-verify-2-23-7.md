# Verification Report: rc/v2.23.7

## Summary

Verified release candidate `ai_rc/v2.23.7` (HEAD `37544e9`) against
`requirements/v2.23.7-bugfix-bundle-20260916.md`, a single-item BUG bundle
carrying `BUG-0303-wake-flag-scrub-has-no-guard`. The RC diff against `ai_main`
is two test files (+154/-10) and no production code: unit coverage for
`scrub_agent_scoped_env` and `AGENT_SCOPED_ENV_NAMES` in
`team/tests/unit/test_scrubbed_subprocess_env.py` (CODER-20260916-033), and a
call-site guard in `team/tests/integration/test_cm_release_synthetic.py` that
factors `_run_cm_script`'s environment construction into `_build_cm_script_env`
and asserts the scrub guarantee directly (CODER-20260916-034). Both gated
runners exited 0. The fix was verified behaviourally, not structurally: five
independent mutations of the guarded surface were injected into a throwaway
clone and every one turned a new test red — including the exact failure mode
BUG-0303 described, where deleting the scrub call was previously undetectable
on a host without `PGAI_AGENT_WAKE_ACTIVE` in the ambient shell. The build ran
without manual intervention.

## Recommendation
PASS

## Systemic Risk
low

## Findings

### Finding 1: Pre-flight — RC intact, worktree matches the RC commit

**Category:** pass

`git rev-parse HEAD` = `git rev-parse ai_rc/v2.23.7` =
`37544e9862daf178b25a90009d913c89f0098075`. `git status --porcelain` empty,
`git stash list` empty. Scanned every task folder under
`$PGAI_PROJECT_ROOT/tasks/`; every `DONE` task naming a `## Feature Branch`
other than `none` has had that branch deleted post-merge (the normal shape),
so Checks 1 and 2 pass trivially with zero anomalies. The two contributing
tasks' merges are both present on the RC:

```
37544e9 Merge ai_feature/CODER-20260916-034-cm-fixture-env-scrub-guard into ai_rc/v2.23.7
93cae7f Expose and guard the environment _run_cm_script builds for its child
df7a8e5 Merge ai_feature/CODER-20260916-033-scrub-helper-unit-guard into ai_rc/v2.23.7
c6ea2b8 Add unit coverage for scrub_agent_scoped_env and AGENT_SCOPED_ENV_NAMES
```

Both merges are `--no-ff` merge commits, as the branch-and-merge law requires.

### Finding 2: `release-state.md` schema and version ordering (Step 6.1)

**Category:** pass

All four fields present and well-formed: `## Active RC` = `v2.23.7`,
`## RC Opened At` = `2026-09-16T15:39:16+00:00`, `## RC Opened By Task` =
`CM-20260916-035-open-rc-2-23-7`, `## Last Released` = `v2.23.6`.
`pp_last_released_version "pgai-agent-kanban"` returns `v2.23.6`; `semver_gt
"v2.23.7" "v2.23.6"` returns true. Version comparison was made with the shared
helper (`team/scripts/lib/semver.sh`), never inline. The Active RC value is
consistent with the actual branch state — `ai_rc/v2.23.7` exists locally and is
what the worktree is detached at. No drift.

### Finding 3: Gated unit suite (Step 5a / 6.6)

**Category:** pass

```
bash team/scripts/run-unit-tests.sh --coverage
UNIT_EXIT=0
4644 passed, 31 skipped in 843.59s (0:14:03)
Unit tests PASSED
[gated-runner] bare_tmp_cleanliness: OK
[gated-runner] listener_cleanliness: OK
[gated-runner] findings file written: /tmp/pgai_kanban_tmp/lint/findings-unit.json (0 finding(s))
```

Output captured at `/tmp/pgai_kanban_tmp/tester/unit.log`. The lint pre-flight
passed as part of the runner, including `lint_skip_bug_gate: OK — all skip
annotations are compliant.`

### Finding 4: Gated integration suite (Step 5a / 6.7)

**Category:** pass

```
bash team/scripts/run-integration-tests.sh --coverage
INTEGRATION_EXIT=0
515 passed, 1 skipped in 553.92s (0:09:13)
Integration tests PASSED
[gated-runner] bare_tmp_cleanliness: OK
[gated-runner] listener_cleanliness: OK
[gated-runner] findings file written: /tmp/pgai_kanban_tmp/lint/findings-integration.json (0 finding(s))
```

Output captured at `/tmp/pgai_kanban_tmp/tester/integration.log`. The PASS
evidence for this report is these two runner exit codes, not any direct pytest
line.

### Finding 5: RC acceptance criterion — "All Python files compile cleanly"

**Category:** pass

`python3 -m compileall -q team/` → exit 0, no output. The two changed files
also pass individually: `python3 -m compileall -q
tests/integration/test_cm_release_synthetic.py` and
`... tests/unit/test_scrubbed_subprocess_env.py` both report no errors (this is
CODER-20260916-034's acceptance criterion 6 verbatim).

### Finding 6: RC acceptance criterion — "All bash files pass syntax check"

**Category:** pass

`bash -n` over every `*.sh` under `team/`: 278 files checked, 0 failures.

### Finding 7: RC acceptance criterion — "Each bundled item is addressed"

**Category:** pass

`BUG-0303-wake-flag-scrub-has-no-guard` asked for four specific things under
its `## Expected` and `## Files Involved`. Each is delivered:

1. *A unit test asserting a base dict carrying every name in
   `AGENT_SCOPED_ENV_NAMES` comes back without them* —
   `test_scrub_agent_scoped_env_removes_every_agent_scoped_name`, driven by a
   comprehension over the tuple itself rather than a transcribed literal.
2. *That unrelated names survive untouched* —
   `test_scrub_agent_scoped_env_leaves_unrelated_names_untouched`.
3. *That the dict is mutated in place and also returned* —
   `test_scrub_agent_scoped_env_mutates_in_place_and_returns_same_object`
   (`assert result is env`).
4. *That the tuple is non-empty* — `test_agent_scoped_env_names_is_non_empty`.

The bug's stronger ask — "an assertion that the environment this helper hands
to a child never contains an agent-scoped name unless the caller passed one
through `extra_env`" — is delivered by `TestRunCmScriptEnvironmentScrub` in
`test_cm_release_synthetic.py`, built on the new `_build_cm_script_env()`
factoring. The bug explicitly permitted "factoring the env construction into a
small named function the test can call directly"; that is the route taken, and
`_run_cm_script`'s signature and return type are unchanged, so the two
ship-policy tests at `test_ship_policy_acceptance.py:759` and `:866` are
untouched — the bug asked for "a guard beside them, not a replacement", and
that is what landed.

The fixture module `team/tests/fixtures/scrubbed_subprocess_env.py` is
byte-for-byte unchanged in this RC (confirmed against
`git diff ai_main..ai_rc/v2.23.7`), so CODER-20260916-033's "no production
behaviour change" constraint holds.

### Finding 8: Behavioral verification — the guard fires (Step 6.5, Principle 4)

**Category:** pass

This is the substance of the RC: BUG-0303 is a bug about a test that cannot
detect its own regression, so structural confirmation that tests exist proves
nothing. Five mutations were injected into a **throwaway `git clone`** of the
worktree (never a `cp -r`, never the live tree) at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/guard-probe-274548`, each
reverted with `git checkout --` before the next. All runs used a sandboxed
root per the live-install safety rule.

**Mutation 1 — delete `scrub_agent_scoped_env(base_env)` (the exact revert
BUG-0303 says was invisible), run under `env -u PGAI_AGENT_WAKE_ACTIVE`:**

```
FAILED tests/integration/test_cm_release_synthetic.py::TestRunCmScriptEnvironmentScrub::test_ambient_agent_scoped_signal_is_absent_from_built_env
E  AssertionError: PGAI_AGENT_WAKE_ACTIVE was present in the current process and must be
   scrubbed from the environment _run_cm_script builds, but was found in the result: '1'
1 failed, 1 passed in 0.29s
```

In the same mutated clone and the same environment, the old indirect coverage
stayed green — `tests/integration/test_ship_policy_acceptance.py::TestOperatorForceShipsOverriddenTrigger`
→ `1 passed in 3.81s`. That contrast is the fix: the pre-RC estate could not
see the deletion on this host; the new guard does.

**Mutation 2 — `AGENT_SCOPED_ENV_NAMES = ()`:**
`FAILED ...::test_agent_scoped_env_names_is_non_empty` → `1 failed, 11 passed`.
(This is exactly why criterion 4 exists: with an empty tuple the removal test
would otherwise pass vacuously — and it did pass, while test 4 caught it.)

**Mutation 3 — `scrub_agent_scoped_env` body reduced to `return env`:**
`FAILED ...::test_scrub_agent_scoped_env_removes_every_agent_scoped_name` →
`1 failed, 11 passed`.

**Mutation 4 — a second name added to the tuple but only the first wired into
the removal loop (`AGENT_SCOPED_ENV_NAMES[:1]`), i.e. BUG-0303's "a future edit
that ... adds a name without wiring it in is equally invisible":**
`FAILED ...::test_scrub_agent_scoped_env_removes_every_agent_scoped_name` →
`1 failed, 11 passed`.

**Mutation 5 — scrub moved to *after* the `extra_env` merge, breaking the
explicit opt-in path:**
`FAILED ...::TestRunCmScriptEnvironmentScrub::test_extra_env_still_delivers_agent_scoped_signal_explicitly`
→ `1 failed, 1 passed`.

Per Anti-pattern 6 (verify the original failure mode *and* the new normal
case), both directions were exercised: the original failure mode is Mutation 1,
and the new normal case is the unmutated tree passing under both ambient
conditions (Finding 9). After every mutation the clone was restored and
`git status --porcelain` confirmed clean.

### Finding 9: Host-independence — the verdict is a property of the tree, not the shell

**Category:** pass

This is BUG-0303's central complaint, and the per-ticket acceptance criteria
for both CODER tasks state it as a runnable command. Both run from `team/`:

```
env -u PGAI_AGENT_WAKE_ACTIVE python3 -m pytest tests/unit/test_scrubbed_subprocess_env.py -q
  → 12 passed in 0.12s
PGAI_AGENT_WAKE_ACTIVE=1  python3 -m pytest tests/unit/test_scrubbed_subprocess_env.py -q
  → 12 passed in 0.14s

env -u PGAI_AGENT_WAKE_ACTIVE python3 -m pytest tests/integration/test_cm_release_synthetic.py \
    "tests/integration/test_ship_policy_acceptance.py::TestOperatorForceShipsOverriddenTrigger" -q
  → 18 passed in 40.01s
PGAI_AGENT_WAKE_ACTIVE=1  python3 -m pytest tests/integration/test_cm_release_synthetic.py \
    "tests/integration/test_ship_policy_acceptance.py::TestOperatorForceShipsOverriddenTrigger" -q
  → 18 passed in 38.73s
```

Identical counts under both ambient conditions, in both suites. The count moved
from the 16 recorded in BUG-0303 to 18 — the two new guard tests. These were
run as acceptance-criteria checks, not as PASS evidence; PASS evidence is
Findings 3 and 4.

The mechanism is right, not just the outcome: the unit tests build their own
dict and never read `os.environ`, and the integration guard uses
`monkeypatch.setenv` / `monkeypatch.delenv` to *establish* the condition it
needs rather than inheriting it. That is the structural fix for anti-pattern 2,
not a workaround for it.

### Finding 10: Constraint — no provenance tokens in shipped test code (Step 6.9)

**Category:** pass

Both CODER tasks forbid a bug identifier, task ID, or RC version in the shipped
test code, docstrings, or comments; `SOP-core.md` ("Naming") binds this
tree-wide. Grepped the added regions for `BUG-\d`, `CODER-\d`, `TESTER-\d`,
`PM-\d`, `CM-\d`, `v2.N.N`, `rc/v`: **zero hits in any line this RC added**.
The pre-existing matches in `test_cm_release_synthetic.py` (lines 2084-2753 —
`v2.3.5`, `CODER-20260101-042`, `BUG-0001-...`) are all synthetic fixture data
and usage examples in cases (j) and (k), a sanctioned exception, and all
predate this RC. The gated runners' `provenance` hygiene class also passed with
zero findings.

### Finding 11: Skip-cites-real-bug gate (Step 6.13)

**Category:** pass

Both runners report `lint_skip_bug_gate: checking for placeholder skip IDs ...`
/ `BUG-NNNN file-existence check: 0 missing files` / `OK — all skip annotations
are compliant.` Confirmed by hand: no `BUG-SKIP` or `BUG-<letters>` placeholder
shape appears in a skip annotation in `team/tests/unit` or
`team/tests/integration`. The `BUG-NNNN` strings that do appear in those
directories (`BUG-0404`, `BUG-0405`, `BUG-0503`, `BUG-1234`–`BUG-1237`) are
synthetic fixture values constructed inside tests of the bug-filing machinery,
not citations in skip annotations. This RC adds no skipped test.

### Finding 12: Synthetic release simulation (Step 6.8)

**Category:** pass

Strictly, the trigger condition is not met — the RC diff touches no
release-lifecycle production file (`cm-release.sh`, `cm-open-rc.sh`,
`cm-cancel-rc.sh`, `team/scripts/lib/project_paths.sh`, nothing under
`team/scripts/` at all). It was run anyway, because the RC modifies the
synthetic-release test file itself:
`team/tests/integration/test_cm_release_synthetic.py` ran clean in full (18
passed with the ship-policy class, under both ambient conditions), inside the
gated integration suite and again standalone against an isolated temp root. No
real branch was touched.

### Finding 13: Prerequisite dependency graph (Step 6.10)

**Category:** pass

`CM-20260916-035-open-rc-2-23-7` → {`CODER-20260916-033`,
`CODER-20260916-034`} → `TESTER-20260916-036` → `CM-20260916-038-release-2-23-7`.
Acyclic; every referenced task ID resolves to a real task folder; the
lifecycle bookends are correctly linked (CM-open at the start, TESTER plus
CM-release at the end). Sequence numbers are non-colliding; the gap at 037 is
the unused optional-approval slot, not a dangling reference. Queue markers
match task state in every case: CM-035 `[x]`/DONE, CODER-033 `[x]`/DONE,
CODER-034 `[x]`/DONE, TESTER-036 `[A]`/WORKING (this run), CM-038 `[W]`/WAITING.

### Finding 14: Coding-standards spot-check (Step 6.14)

**Category:** pass

No new script under `team/scripts/`, so the `--help` requirement is not
engaged. No new operational value hardcoded where an operator would want it
configurable (the added literals are test fixtures). `_build_cm_script_env()`
is a factoring of existing inline construction, not a newly duplicated
implementation of anything in `lib/`. The citation
`docs/test-authoring-guidelines.md` resolves: "Anti-pattern 2 —
Environment-coupled fixtures" is at line 49 of that file, so the citation is
accurate rather than merely plausible.

### Finding 15: Rationale is co-located as a comment rather than a docstring

**Category:** pass-with-caveat

CODER-20260916-033's last acceptance criterion reads: "The new tests name the
environment-coupled-fixtures anti-pattern from docs/test-authoring-guidelines.md
in a **docstring** so the next author sees why the guard exists." The citation
landed in a module-level `#` comment block immediately above the four new tests
(`test_scrubbed_subprocess_env.py:145-151`) rather than inside a docstring; the
individual test docstrings explain each test's own contract but do not name the
anti-pattern.

Recorded as `pass-with-caveat`, not a gap: the criterion's stated purpose —
"so the next author sees why the guard exists" — is fully met, and a block
comment introducing all four tests arguably serves it better than repeating the
citation in four separate docstrings. The distinction is
comment-versus-docstring placement of prose that is present, correct, and
correctly located. No follow-up is filed and the recommendation is unaffected.

## Lint Findings (non-blocking)

None. `/tmp/pgai_kanban_tmp/lint/findings-unit.json` and
`/tmp/pgai_kanban_tmp/lint/findings-integration.json` were both present,
schema_version 1, each with `"findings": []` — 0 findings for both suites, as
the runners' own trailer lines also report.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=9d freshness=stale — Stale on both counts: the record is 9 days old against a 7-day window, and it was taken at v2.9.20 rather than the v2.23.7 under verification; its failed-to-build verdict (docker and podman claude-abliterated Dockerfile parse error, podman rocky9 COPY step) was already filed by that run as BUG-0167-cold-build-claude-abliterated-failure, which now reads Status done, so nothing is re-filed here.

Read through `deep_verify_ledger_read_latest` in
`$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/lib/deep_verify_ledger.sh` (reader exit
0); the ledger markdown was not parsed by hand and the verdict was not
re-derived. The tree ships three Dockerfiles (`docker/claude-abliterated`,
`docker/debian`, `docker/rocky9`) and does carry
`team/scripts/deep-verify.sh`, so neither Step 5d gap condition applies. Per
Step 5d this line is a reported fact, not a finding: it does not change the
recommendation or the systemic risk. No container build, container engine, or
`deep-verify.sh` invocation was performed by this verification.

## Autonomous criterion

**Manual interventions required: none.**

1. **Were any tasks force-promoted from WAITING to BACKLOG by hand, bypassing
   the wake script's prerequisite evaluation?** **NO.** The one promotion in
   this cycle is the wake script's own:
   `[2026-09-16T15:51:50+00:00] wake(tester): WAITING task
   TESTER-20260916-036-verify-2-23-7: prerequisites satisfied, promoting to
   BACKLOG` in `logs/cron-tester.log`, with the matching task `## Summary`
   attributing it to the wake script.
2. **Was `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`?** **NO.** Its mtime (15:39) matches the RC-open timestamp
   it records and the `release-state/v2.23.7.json` snapshot written at the same
   minute by `open-rc.sh`, whose exit-0 run CM-20260916-035 documents.
3. **Were any scripts patched mid-build?** **NO.** `git diff --name-only
   ai_main..ai_rc/v2.23.7` filtered for `team/scripts/`, `scripts/`, and
   `subagents/` returns nothing; the entire RC is two files under `team/tests/`.
4. **Were any queue files edited by hand?** **NO.** Every marker in
   `cm_backlog.md`, `coder_backlog.md`, and `tester_backlog.md` matches the
   corresponding `status.md`, and each transition has a wake-script log line
   behind it.

The full cycle — RC open 15:39, CODER-033 15:40→15:43 DONE, CODER-034
15:43→15:51 DONE, TESTER-036 promoted and started 15:51 — ran unattended, with
worktree teardown and canonical-dev-tree restoration logged after each task.

## Caveats

- **Coverage (informational only; never enters the verdict).** Unit suite:
  line/statement 77 %, branch 63 % (blended 73 %). Integration suite:
  line/statement 10 %, branch 8 % (blended 9 %). Taken from the `totals` object
  of `/tmp/pgai_kanban_tmp/coverage/unit.json` and `.../integration.json`, using
  the `percent_statements_covered_display` and
  `percent_branches_covered_display` keys verified against the actual generated
  JSON rather than assumed. The low integration figure is the expected shape for
  a suite that drives bash scripts through subprocesses, where the Python line
  counter sees almost nothing.
- **Test fidelity.** The concern this RC exists to answer is the one worth
  asking of it: do the new tests exercise the real breaking condition, or a
  sanitized proxy? They exercise the real one. `_build_cm_script_env` is the
  actual function `_run_cm_script` now calls — not a reimplementation — so a
  change to the real construction path is what the assertion sees. The one
  residual proxy is that the guard asserts on a returned dict rather than on
  what a spawned child process observes; that is a deliberate and correct trade
  (both task READMEs forbid driving a real CM script), and the end-to-end path
  remains covered by the two unchanged ship-policy tests. Not a concrete
  concern, so not a finding.
- **Observation, outside the spec and not a finding.**
  `projects/pgai-agent-kanban/project.cfg` ends with a bare
  `defer_piggyback = any` line that falls under the `[training]` section header
  rather than `[release]`. `SOP-core.md` accepts only `true`/`false` for that
  key. As written it is inert — `[release] defer_piggyback` is absent, so
  `_disc_bug_deferral_filter` takes its `true` default, and the discovery run
  that produced this very bundle at 15:33 completed normally. It is live-install
  operator config, not version-controlled, untouched by this RC and outside its
  spec, so it is recorded here as an observation for operator awareness only. It
  fits none of the three Step 6.11 spot-check categories cleanly and is demoted
  accordingly.
- **Step 6.11 spot-check, time-boxed.** No state drift (Active RC matches the
  real branch; `Last Released` matches `pp_last_released_version`). No untested
  new mechanism (the RC's only new code path, `_build_cm_script_env`, is itself
  directly covered by the two tests added beside it). No regression of a prior
  gap — the guarded surface is exactly the one the previous cycle's report left
  open as BUG-0303, and it is now closed.
- **Prior-run artifacts.** None. The task's `artifacts/` directory was empty at
  start and no `report.md.previous-RUN-N` exists, so this was a fresh start with
  no prior verdict available to re-issue.
- **Scope note.** Performance, load, security audit, and cross-platform
  compatibility are out of TESTER's scope and were not assessed; the
  requirements doc states no criterion in any of those areas.
