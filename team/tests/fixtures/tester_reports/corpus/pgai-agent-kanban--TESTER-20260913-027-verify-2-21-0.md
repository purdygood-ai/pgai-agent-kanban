# Verification Report: ai_rc/v2.21.0

**Task:** TESTER-20260913-027-verify-2-21-0
**Project:** pgai-agent-kanban
**RC branch:** `ai_rc/v2.21.0` (HEAD `ac1c2b6`)
**Baseline:** `ai_main` (HEAD `cd82943`, v2.20.2)
**Requirements:** `projects/pgai-agent-kanban/requirements/v2.21.0-test-estate-robustness.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260913-027-verify-2-21-0` — detached HEAD at the RC commit, `git status --short` clean, `git stash list` empty.

## Summary

v2.21.0 is a test-estate robustness release: one shared external-binary guard
replacing four private tmux probes and adopted across 60+ docker/git-reaching
test modules; a self-located subprocess-cwd and diagnostic contract for the
doc-lint suite; a claim-then-gate rewrite of the gated runners' findings-file
handling with a run-identity sidecar and a dual-channel refusal; an AST-based
skip-citation scanner replacing the line-oriented grep on the Python surface;
acceptance-layer coverage that drives the real wake entry point from a foreign
cwd under a rebuilt environment; f-string coverage for the bare-`/tmp`
anti-pattern lint; a deflake of the transient-retry watchdog tests; a new
three-host green-matrix verb and a new order-independence verb; and a
substantial expansion of `docs/test-authoring-guidelines.md`.

Every deliverable in the requirements doc is present and, where it is
preventive logic, was exercised against the failure mode it prevents rather
than inspected: the skip gate was fed an inline `if x: pytest.skip("BUG-SKIP-…")`
and a multi-line citation (both caught) alongside a fixture string mentioning a
bug-shaped ID (correctly not flagged); the AP2 lint was fed all four f-string
forms (all four flagged); the findings-file claim was seeded with a stale
document and a simulated concurrent reclaim (cleared, then refused
dual-channel); the three-host matrix was forced down its not-attempted path
(named the unavailable resource, exited 1, never reported green); the doc-lint
suite returned identical verdicts from three invoking directories and, with an
assertion deliberately broken in a throwaway copy, quoted the subprocess's own
stderr in the failure message.

The unit gated runner exited 0 (4390 passed, 31 skipped). The **integration
gated runner exited 1**: one pre-existing, environment-coupled golden-file test
fails because the verifying host's `max_task_seconds` (10800) differs from the
value baked into the golden (5400). That single failure is what takes the
recommendation off PASS. It is not caused by this RC — the offending line and
the golden are byte-identical on `ai_main` — but it does mean the RC's own
acceptance criterion "full suite green on the dev tree" is not met as verified.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Test Suite Results

| Suite | Gated runner | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 4390 passed, 31 skipped, 640.21s. Lint pre-flight green (breakage-class, Dockerfile static, anti-pattern, skip-gate, orphan-test, API-parity, ICD, CHANGELOG, help-presence, dual-channel, runnable-remedy, census-compliance, governance byte-budget all OK; hygiene reported 3 quality-class findings, non-fatal). Post-suite bare-`/tmp` and listener cleanliness checks OK. |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **1** | 1 failed, 460 passed, 1 skipped, 597.85s. Lint pre-flight green (same classes; hygiene 3 quality-class findings). Failure is pytest-only, not the lint gate. |

Failing test, categorized per Step 5a:

- `tests/integration/test_engine_behavior_baseline_harness.py::test_compare_against_goldens_is_clean_on_unmodified_tree` — **pre-existing-bug**. See Finding 1.

## Code Coverage

Read from the `totals` object of the pytest-cov JSON reports
(`percent_statements_covered` / `percent_branches_covered`; field names
confirmed against the generated files, not assumed).

- Unit:        line 75%  |  branch 62%   (15806 stmts, 4744 branches; blended `TOTAL` 72%)
- Integration: line 7%   |  branch 6%    (15797 stmts, 4744 branches; blended `TOTAL` 7%)

Coverage is informational only and does not enter the recommendation. The
integration figure is low because that suite drives real subprocesses, whose
execution is not visible to in-process instrumentation.

## Findings

### Finding 1: integration gated runner exits 1 on an environment-coupled golden

**Category:** bug (pre-existing)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a real defect that ships and gets fixed next iteration — the estate's own instrument is red on a legitimately configured host, but nothing about the release is worse than `ai_main` in this respect; see the Systemic-Risk Rubric in `team/SOP.md`)
**Regression:** no (`git diff ai_main..ai_rc/v2.21.0 -- team/tests/integration/test_engine_behavior_baseline_harness.py` shows this RC added only an autouse `require_binary("git", …)` fixture; the `env = dict(os.environ)` line and `team/tests/fixtures/engine-baseline/wake-log.txt` are byte-identical on `ai_main`, and the golden was last touched at v2.14.0)
**Integrity Damage:** no
**Filed As:** BUG-0262 (Path C — autonomous follow-up filing, non-blocking; Path C-A, filed as BUG)

`_run_harness()` in the engine-behaviour baseline test builds its subprocess
environment with `env = dict(os.environ)` and overrides only
`PGAI_AGENT_KANBAN_TEMP_DIR`. The wake bootstrap it drives derives and logs a
background-wait ceiling from `MAX_TASK_SECONDS`, and the golden records the
5400-second default. The verifying host resolves `MAX_TASK_SECONDS=10800` from
`kanban.cfg [wake] max_task_seconds`, so the only differing line in the golden
diff is:

```
-… derived BASH_MAX_TIMEOUT_MS=5430000 from MAX_TASK_SECONDS=5400s + KILL_GRACE_SECONDS=30s
+… derived BASH_MAX_TIMEOUT_MS=10830000 from MAX_TASK_SECONDS=10800s + KILL_GRACE_SECONDS=30s
```

Proven to be environment coupling and nothing else, by running the single test
twice against the same tree:

```
python3 -m pytest …::test_compare_against_goldens_is_clean_on_unmodified_tree -q -p no:randomly
  -> 1 failed
MAX_TASK_SECONDS=5400 python3 -m pytest …::test_compare_against_goldens_is_clean_on_unmodified_tree -q -p no:randomly
  -> 1 passed
```

This is anti-pattern 2 (environment-coupled fixture) in the estate's own
guidelines — the class this very release set out to close — surviving in a
module the release touched only to add a binary guard. Until it is fixed, no
RC verified on this host can record a PASS, because the integration gated
runner is the PASS precondition.

### Finding 2: the new order-coupling checker does not control the order it compares

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (a diagnostic verb, run on demand; no shipped runtime path and neither gated runner depends on it)
**Regression:** yes (`team/scripts/verify-test-order-independence.sh` is new in this RC, so this RC's changes are what produced the defect; nothing regressed from v2.20.2, which had no such verb)
**Integrity Damage:** no
**Filed As:** BUG-0263 (Path C-A)

The script's header states its forward pass runs "in pytest's normal collection
order" and its reverse pass is that order reversed. `pytest-randomly` 4.1.0 is
installed in the venv the agents run under and re-seeds every session, and
nothing in `team/pyproject.toml`, either gated runner, or the script disables or
pins it. Both passes therefore start from independent random orders. Measured
directly — two consecutive `PGAI_TEST_ORDER=reverse` runs of the same file
executed different orders, each printing its own `Using --randomly-seed=` value.
The `shuffle:<seed>` reproducibility contract documented in
`team/tests/conftest.py` is defeated the same way.

Consequence: the check can still notice a coupling by luck, but it cannot make
the claim its own documentation and `docs/test-authoring-guidelines.md` rely on
— that a named divergence is reproducible, and that a clean run is evidence
about the two named orders. A related discrepancy worth recording: the plugin is
not declared in `requirements-test.txt`, so the dev host randomises while the
gitless/tmux-less container hosts built by `run-three-host-matrix.sh` (which
install only `requirements-test.txt`) do not.

### Finding 3: the blocking-site census rollup understates its own table

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (hygiene — a governance document's summary line lags its own table; no reader is steered into a wrong action, only a wrong count)
**Regression:** no (the drift predates this RC: `ai_main` already carries 59 table rows against the same `57 sites` rollup. This RC widened it by one by adding row 60 without touching the summary.)
**Integrity Damage:** no
**Filed As:** BUG-0264 (Path C-A)

`docs/blocking-site-census.md` reads `**Rollup: 57 sites, 0 unclassified.** 29
BLOCK-WORTHY, 26 CONVERT, 2 AGENT-RESOLVABLE.` while the table above it holds 60
numbered rows classified 32 / 26 / 2. The census lint reconciles anchors against
the tree and the Locator Index but not the rollup, so it reports `0 offenders`
with the summary wrong. This RC's new gate (`gated-runner-stale-findings-claim-01`)
correctly carried all three halves the SOP's new-gate rule requires — class
comment, dual-channel emitter, anchor plus Locator Index entry — and the rollup
is simply not part of that rule.

### Finding 4: environment-guarded fixtures — shared binary guard

**Category:** pass

`team/tests/fixtures/binary_guard.py` (new) provides `binary_available`,
`try_resolve_binary`, `require_binary`, `narrate_binary_skip`,
`docker_daemon_reachable`, `require_docker_daemon`, and
`BinaryGuard.teardown()`. Both `tests/unit/conftest.py` and
`tests/integration/conftest.py` re-export the surface. 64 test modules and
fixtures now route tmux/docker/git through it; `grep -rn 'shutil.which("tmux"|"docker"|"git"|"podman")' team/tests/`
returns nothing, so no private probe survives.

`teardown()` is a no-op when setup never resolved (the `FileNotFoundError`/
`TypeError`-in-`finally` class) and deliberately does not suppress a genuine
teardown fault. `require_docker_daemon()` keeps the absent-binary and
unreachable-daemon reasons distinct rather than blurring them.

`team/tests/unit/test_binary_guard_contract.py` (new, 325 lines) seeds a
violation of each guard class and pairs it with the guarded companion:
unguarded absent binary, a guard that raises instead of skipping, an
unconditional teardown after a skipped setup, present-docker/unreachable-daemon,
and absent git — satisfying the requirements' "seeded violations of each guard
class fail loud in their own suite."

`team/pgai_agent_kanban/ops/write.py` additionally degrades
`_git_delete_feature_branch` and `_git_worktree_prune` to warnings on a gitless
host, and `team/scripts/container-shell.sh` now validates `--mount` shape before
resolving the engine so a malformed value is a usage error on a dockerless host
rather than an unrelated exit 1.

### Finding 5: sandboxed doc-lint suite — cwd contract and surfaced stderr

**Category:** pass

`team/tests/unit/test_doc_lint_harness.py` routes every subprocess through one
`_run()` helper whose `cwd` defaults to a repo root computed from
`pathlib.Path(__file__).parent.parent.parent.parent` — not an environment
variable, not `git rev-parse` — and never calls `chdir`. Every
subprocess-dependent assertion routes its message through `_diagnostic()`.

Verified behaviorally, not by reading:

- Invoked from the repo root, from a directory outside the repository, and from
  `/` (with `PYTHONPATH` set to `team/` so the `tests` package resolves — a
  pre-existing estate property, unrelated to this change): **14 passed** in all
  three, identical verdicts.
- With one assertion deliberately flipped in a throwaway copy, the pytest
  failure message quoted the doc-lint subprocess's own `--- stdout ---` block
  *and* its `--- stderr ---` block (four `WARNING: audited file not found` lines)
  — the "no swallowed confessions" deliverable, demonstrated.

### Finding 6: findings files written on all exit paths (clear-then-gate)

**Category:** pass

`gated_runner_claim_findings_file` now runs at the top of both gated runners,
before the pytest-missing, breakage-class, and Dockerfile-static gates: it writes
a fresh empty findings document (so no prior invocation's file can survive an
abort) and records a run-identity token in a `<path>.runid` sidecar.
`gated_runner_record_finding` writes a `runner_incomplete` finding at each fatal
gate so an aborted run is not byte-identical to a clean one.
`gated_runner_assert_findings_claim` is the consumer-side refusal before the
aggregate bug filer runs.

Exercised directly:

- Seeded a stale `{"stale": true}` document at the target path, called the claim,
  and confirmed the file came back as the empty schema-1 document with a matching
  sidecar token — clear-then-gate ordering, proven.
- Asserted with the correct token: silent pass.
- Simulated a concurrent reclaim, then asserted with the original token: returned
  1 and emitted the full dual-channel contract (`error: gated_runner.stale_findings_claim`,
  a message naming both tokens, and a runnable remedy).
- Deleted the sidecar and asserted: refused, rendering the absent token as
  `<absent>`.

Real-world confirmation from this very verification: the integration runner's
pytest run failed, and the exit hook still wrote
`findings-integration.json (3 finding(s))` — the exact path that previously
skipped the write and left a stranger's file in place. `--help` and an
unknown-flag usage error also each produced a findings-file write.

The new refusal declares its census class in a code comment, emits through the
shared error-contract emitter, carries `# CENSUS-ANCHOR: gated-runner-stale-findings-claim-01`,
and has the matching Locator Index entry in `docs/blocking-site-census.md`. The
census lint passes with 0 offenders. (Only the document's rollup line was not
updated — Finding 3.)

### Finding 7: acceptance fixtures drive the production entry point

**Category:** pass

`team/tests/integration/wake_entrypoint_acceptance_harness.py` (new, 386 lines)
supplies `build_synthetic_kanban_root`, `build_stub_provider_cli`,
`scrubbed_wake_env`, `run_wake_entrypoint`, and `make_foreign_cwd`. The
environment is rebuilt from an explicit minimal name set rather than
`dict(os.environ)` with deletions — `HOME` points at an empty scratch directory
so no operator config can leak in, and `MAX_TASK_SECONDS` / `KILL_GRACE_SECONDS`
/ `BASH_MAX_TIMEOUT_MS` are deliberately absent unless a case adds them back.
(This is precisely the discipline Finding 1's older harness lacks.)

`team/tests/integration/test_wake_entrypoint_bg_ceiling_acceptance.py` (new, 521
lines) carries six cases, including the central negative proof
(`test_broken_wiring_fails_acceptance_layer_but_not_mechanism_fixture`): it
comments out the single `source .../wake_bootstrap.sh` line in a throwaway copy,
asserts the real entry point then fails and never logs the derivation, and
asserts the block-extraction mechanism fixture still passes against the
untouched dev-tree file — the ceiling-fix lesson, demonstrated rather than
asserted. A sixth case snapshots the live kanban root and the real `crontab -l`
before and after and asserts both are byte-identical. All six passed in the
integration run.

### Finding 8: skip-gates match statements, not line shapes

**Category:** pass

`team/scripts/lib/skip_bug_citation_scan.py` (new, 225 lines) parses each `*.py`
file with `ast` and treats only the source segment of a real skip `Call` node
(`pytest.skip`, bare `skip`, `mark.skip`, `mark.skipif`) as a citation
candidate; `lint_skip_bug_gate.sh` excludes `*.py` from its greps and merges the
structural results, keeping the line-oriented grep for `*.sh`/`*.md` where there
is no AST.

Exercised against a purpose-built corpus:

| Seeded case | Expected | Observed |
|---|---|---|
| `if x: pytest.skip("deferred: BUG-SKIP-1")` (inline, one line) | caught | caught |
| bare `skip("BUG-SKIPPY")` via `from pytest import skip` | caught | caught |
| `@pytest.mark.skipif(..., reason=("waiting on " "BUG-9999"))` across 6 lines | caught | caught |
| module-level string `reason=BUG-SKIP-placeholder`, and a returned string containing `pytest.skip(reason=BUG-Foo)` | **not** flagged | not flagged |
| `--tests-dir` that does not exist | exit 2 | exit 2 |

End-to-end through the gate itself: a clean tree containing a fixture string
mentioning `BUG-SKIP-…` passed (exit 0); an inline placeholder skip failed with
the offending line named (exit 1); a multi-line citation of a non-existent
`BUG-9999` failed with `no matching file found` (exit 1).

Step 6.13 standing check: no placeholder IDs anywhere under `team/tests/` outside
`artifacts/` run logs, and every `BUG-NNNN` the structural scanner extracts
resolves to a file in `bugs/` (the scanner returns no citations at all on this
tree). The corpus IDs `BUG-1234`…`BUG-1239` inside
`test_skip_bug_citation_scan.py` are string data, never skip-call arguments —
the old grep would have flagged them, which is the change working.

### Finding 9: suite hygiene — `/tmp` literals, order seam, deflake

**Category:** pass

`lint_test_anti_patterns.py` gained f-string-prefix coverage (`[a-zA-Z]{0,2}`
before the quote) on the `Path()`, `open()`, `os.makedirs()` and variable-assign
AP2 patterns, plus `{` in the trailing character class so an interpolation
starting immediately after `/tmp/` is still reachable. Verified by seeding all
four forms — `Path(f"/tmp/{name}")`, `open(f"/tmp/{name}.log","w")`,
`q = f"/tmp/{name}"`, `os.makedirs(f"/tmp/{name}/d")` — and getting exactly four
findings. The bare-`/tmp` subject-matter whitelist is deliberately narrow: it
drops only the `Path()`/`open()` checks, for one named file, whose own probe
helper appends a `uuid4` suffix per call. The full lint reports `0 findings
across 482 file(s)` on the RC, and a manual sweep of `team/tests/**/*.py` for
non-framework `/tmp/` literals leaves only sample-data strings, argument values
never used as paths, the whitelisted litter-check probe, and the pre-existing
deliberate bad-example fixture.

`team/tests/conftest.py` adds a `pytest_collection_modifyitems` seam keyed on
`PGAI_TEST_ORDER` (`reverse`, `shuffle:<seed>`), a no-op when unset, covered by
six unit tests. `team/scripts/verify-test-order-independence.sh` (new) drives it
— see Finding 2 for the defect in that verb.

`test_wake_telemetry_safeguard_retry.py` replaces `assert elapsed < 10` with an
injected `sleep` shell function that counts poll iterations and asserts
`sleep_calls <= 3`, removing the wall-clock dependence. `team/scripts/lib/temp.sh`
fixes a genuine strict-mode defect the new matrix verb exposed: both
`pgai_listener_snapshot` and `pgai_listener_cleanliness_check` piped/assigned
`_pgai_listener_scan_matches` in a form that let its documented return of 2
("neither ss nor lsof available") abort a caller running under `set -e -o pipefail`
before pytest ever launched.

### Finding 10: three-host green matrix verb

**Category:** pass-with-caveat

`team/scripts/run-three-host-matrix.sh` (new, 639 lines) runs the real
`run-unit-tests.sh` and `run-integration-tests.sh` — no second test-running path
— across the dev tree plus gitless and tmux-less containers layered on the
project's own shipped `docker/debian/Dockerfile`. `--help` names every argument
(coding-standards directive 6). Its bootstrap refusal carries a census class
comment and `# CENSUS-ANCHOR: run-three-host-matrix-env-bootstrap-01`.

Exercised down its not-attempted path with
`--host gitless --engine pgai-no-such-engine-xyz`: it reported
`verdict: not-attempted`, named the unavailable resource verbatim
(`configured --engine 'pgai-no-such-engine-xyz' is not an executable on PATH`),
printed the image/container before/after line, and exited **1** — a
not-attempted host is correctly never conflated with a pass.

**Caveat:** the gitless and tmux-less hosts were not built or run during this
verification. A full matrix is two complete suite runs per host on top of cold
container builds — roughly an hour of wall clock — which is DEEP-VERIFY-class
cost that `team/SOP.md` keeps out of per-RC verification (TESTER Step 5d: no
container build, no container engine started during an RC). The verb, its help
surface, its refusal path, and its reporting shape were verified; the claim
"full suite green on a gitless install and a tmux-less container" was not
independently reproduced here and rests on the CODER task's own record.

### Finding 11: documentation layer

**Category:** pass

`docs/test-authoring-guidelines.md` grows by 166 lines. Read in full. Every
factual claim it makes was checked against the implementation rather than taken
on trust: `team/tests/conftest.py` does set `PYTEST_DEBUG_TEMPROOT`; the
`# anti-pattern-allowlist: 2 (justification: …)` marker is a real opt-out the
lint honours; the whitelist really drops only the two constructor checks; the
four harness functions it names (`build_synthetic_kanban_root`,
`scrubbed_wake_env`, `run_wake_entrypoint`, `make_foreign_cwd`) all exist with
those spellings; `require_docker_daemon()` does probe the daemon after the binary
resolves; the guard is re-exported from both conftests. Every relative markdown
link in the file resolves.

`docs/README.md`, `team/TESTING.md` and `team/roles/TESTER.md` were each updated
to point at the new section, and `team/TESTING.md`'s helper table gained rows for
the binary guard and the acceptance harness. The four contracts the role file
advertises are the four the document delivers.

One accurate-but-untestable instruction, recorded because it misled this
verification briefly: the guidelines' "cheap confirmation" for the cwd contract
says to run the suite from the repo root, from `team/`, and from outside the
repository. Run literally, the first and third fail at collection with
`ModuleNotFoundError: No module named 'tests'`, because the estate requires
pytest to be invoked from `team/` (or `PYTHONPATH` to include it). That is a
pre-existing property of the estate, not of this change, and the contract itself
holds once the import path is supplied. Noted as an observation, not filed.

### Finding 12: release-state drift check

**Category:** pass

`## Active RC` is `v2.21.0`, matching the branch under test; `## RC Opened At`
and `## RC Opened By Task` are populated and name `CM-20260913-026-open-rc-2-21-0`,
a DONE task. `pp_last_released_version("pgai-agent-kanban")` returns `v2.20.2`
from git tags and `semver_gt v2.21.0 v2.20.2` holds. `## Last Released` reads
`v2.20.2`, a well-formed `vX.Y.Z` value — present by design, not drift, and not
compared against the tag-derived value. No HALT or HALT-AFTER flag exists at
either scope.

### Finding 13: pre-flight, prerequisite graph and RC hygiene

**Category:** pass

Pre-flight merge verification passed: no DONE task in the project has a
surviving local feature branch (CODER/WRITER delete theirs after merge), so
neither an unmerged branch nor a zero-unique-commit BUG-92 anomaly is present.
All ten prerequisite tasks plus `CM-20260913-026-open-rc-2-21-0` are DONE with
`Needs Human: no`.

The dependency graph is acyclic and correctly bookended: CM-open has no
prerequisites; each CODER task depends on CM-open and its predecessors; WRITER-025
depends on all nine CODER tasks; this TESTER task depends on all ten;
`CM-20260913-029-release-2-21-0` (WAITING) depends on all ten plus this task.
Every referenced task ID resolves to a real task folder.

`ai_main` has had no commits since the RC was opened (its tip is the v2.20.2
release-notes polish at 2026-09-13T08:49), so the fidelity gate has nothing
working against it. `lint_rc_empty_commits` reports no empty non-merge commits
across `ai_main..ai_rc/v2.21.0`. `bash -n` passes on all 8 changed shell scripts
and `py_compile` on all changed Python files. No hardcoded credentials appear in
the diff.

### Finding 14: synthetic release simulation (Step 6.8)

**Category:** pass

Skipped as a dedicated step with note: the RC's diff touches no
release-lifecycle script — `cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`
and `team/scripts/lib/project_paths.sh` are all unchanged. The only change to
`team/tests/integration/test_cm_release_synthetic.py` is an autouse
`require_binary("git", …)` fixture. The test itself ran as part of the
integration suite and passed.

## Lint Findings (non-blocking)

Ingested from `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` (3
findings, schema_version 1, runner `run-unit-tests.sh`) and
`findings-integration.json` (3 findings, runner `run-integration-tests.sh`). Both
files carry the same three instances, so they group into one lint class.

- `hygiene` (class `provenance`, severity `quality`, 3 instances) — internal
  task-ID references in shipped files — cites **BUG-0261** (open). One bug per
  lint class per run; BUG-0261 already lists all three instances verbatim and was
  auto-filed by the runners' own aggregate filer, so no duplicate was filed.
  Instances:
  - `team/tests/unit/test_operator_verb_manifest.py:159` — `[task-ID] 'CODER-20260911-066'`
  - `team/tests/unit/test_operator_verb_manifest.py:176` — `[task-ID] 'CODER-20260911-066'`
  - `release-notes/v2.20.2.md:58` — `[task-ID] 'TESTER-20260913-013'`

None of these is introduced by this RC, none elevates the recommendation, and
none raises `## Systemic Risk`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=6d freshness=stale — the docker and podman claude-abliterated pairs failed on a Dockerfile `FROM` parse error and podman/rocky9 on a `COPY --chmod` step; stale because v2.9.20 is not the v2.21.0 under verification (its 6-day age is still inside the 7-day window), and it cites BUG-0167, filed by that deep-verify run and since closed as done.

## Autonomous criterion (Step 6.12)

1. Any task force-promoted from WAITING to BACKLOG by hand? **NO** — every task
   in the RC reached DONE through the wake script; no rotation of prior
   artifacts was logged for this task, and this task's artifacts directory was
   empty at start (a genuine fresh start, not a re-run).
2. `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`? **NO** — its four fields are exactly what CM-open wrote.
3. Scripts patched mid-build? **NO** — every commit on `ai_rc/v2.21.0` belongs to
   a ticket's own feature branch and merged through `--no-ff`; `ai_main` received
   no commits while the RC was in flight.
4. Queue files manually edited? **NO** — queue mutations are consistent with wake
   activity (the tester queue's `[A]` marker on this task, the cm queue's `[W]`
   on the release task).

**Autonomous operation criterion: Manual interventions required: none.**

One operator action outside those four checks is recorded here because it is the
proximate trigger for Finding 1: `kanban.cfg` was edited at 2026-09-13T22:32,
while the RC was in flight, raising `[wake] max_task_seconds` to 10800. That is
a legitimate operator tuning change to the live install — not a task, queue,
release-state, or script edit, and not an intervention in the build. It changed
no code; it merely revealed a pre-existing environment coupling in a test.

## Caveats

- **The gitless and tmux-less hosts were not exercised.** See Finding 10. The
  requirements' acceptance criterion names three hosts; this verification
  covered the dev tree only, and the dev tree is not green (Finding 1). The
  omission is deliberate and stated loudly rather than implied: a full matrix
  costs roughly an hour of container builds plus six full suite runs, which
  `team/SOP.md` places in the DEEP-VERIFY tier and keeps out of per-RC
  verification. If an operator wants the criterion closed, the verb is
  `bash team/scripts/run-three-host-matrix.sh`.
- **`pytest-randomly` is active but undeclared.** Both gated-runner sessions in
  this report ran in a randomised collection order (unit seed `2765613342`).
  That is not a defect on its own — a green randomised run is a stronger result
  than a green fixed-order run — but it means neither suite result in this report
  is byte-reproducible, and it is the root of Finding 2.
- **`team/roles/TESTER.md` in the installed kanban root points at
  `team/pgai_agent_kanban/reports/lint_finding_groups.py` for lint-finding
  aggregation; that module does not exist in this project's dev tree** (it lives
  only on the separate `main` lineage, added at v2.7.6). Grouping was done by
  hand for this report — trivially, since all six findings share one `lint`
  value. Recorded as an observation about the installed governance copy, not
  filed against this RC, which does not reference the helper.
- **Deep-verify freshness is stale and its last verdict was red.** Per Step 5d
  that is context the rating is made with, not a finding: it does not move the
  recommendation or the systemic risk, the bug it cites (BUG-0167) is already
  closed, and TESTER files nothing for it.
- The RC's `VERSION` file still reads `v2.20.2`. That is expected — version
  stamping is CM's pre-squash step at release time, not an RC-branch state.
