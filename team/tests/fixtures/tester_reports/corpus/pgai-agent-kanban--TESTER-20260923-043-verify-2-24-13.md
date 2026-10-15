# Verification Report: rc/v2.24.13

## Summary

Verification of release candidate `ai_rc/v2.24.13` for project `pgai-agent-kanban`
against `requirements/v2.24.13-bugfix-bundle-20260923.md` — a discovery-minted BUG
bundle carrying eight items (BUG-0375 … BUG-0382) — and the nine tickets PM
decomposed from it. All reads and runs happened in the detached worktree at
`/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260923-043-verify-2-24-13`,
HEAD `dfdd8a0` == `ai_rc/v2.24.13`, clean tree, no stashes. Seven of the eight
bundled items are implemented and behaviourally verified; one (BUG-0379, the
dashboard switch-storm pane death) was never implemented because its fix ticket
ended `WONT-DO`.

Both gated runners were run with `--coverage`: the integration runner exited 0
(547 passed), the unit runner exited 1 on twelve failures that reproduce
identically on the pre-RC `ai_main` tip and are traced to the wake layer's
git-guard environment pin rather than to anything in this RC. Six findings were
recorded and all six filed via Path C as BUG-0383 … BUG-0388. The RC's own
content is clean under every lint and every other test; the recommendation is
driven by a framework-level defect the RC neither introduced nor can fix, which
Finding 1 argues explicitly so CM can weigh it.

## Recommendation

SHIP-WITH-SERIOUS-CONCERNS

## Systemic Risk

high

## Pre-flight (Steps 2–3)

- `git rev-parse HEAD` = `dfdd8a0e…` = `git rev-parse ai_rc/v2.24.13`. **pass**
- `git status --porcelain` empty; `git stash list` empty. **pass**
- Every task folder whose `status.md` reads `DONE` and whose README names a
  feature branch: all such branches are already deleted post-merge (the normal
  shape), so Checks 1 and 2 pass trivially. The two surviving `ai_feature/*`
  branches belong to `CODER-20260923-038` and `CODER-20260923-041`, both
  `WONT-DO`, and both sit at zero commits ahead of the RC. **pass**
- RC history carries seven `--no-ff` merges, one per DONE ticket
  (033, 034, 035, 036, 037, 039, 040). **pass**

## Requirements-document acceptance criteria

| Criterion | Result |
|---|---|
| Each bundled item is addressed in the implementation | **gap** — 7 of 8. BUG-0379 unaddressed (Finding 1) |
| All Python files compile cleanly | **pass** — `python3 -m py_compile` over all 533 tracked `.py` files, exit 0 |
| All bash files pass syntax check | **pass** — `bash -n` over all 294 tracked `.sh` files plus every extensionless shebang script, 0 failures |

## Per-ticket acceptance criteria

**CODER-033 — provenance lint coverage.**
- docs/ surface reached: appending `# BUG-9999 probe` to `docs/blocking-site-census.md`
  makes `python3 team/scripts/lint_hygiene.py --classes provenance` report exactly one
  finding naming `docs/blocking-site-census.md:1307`; removing the line restores the
  clean state. **pass** (behavioural probe, Step 6.5)
- String-literal content: appending `PROBE = ("# kanban.cfg -- BUG-9999 render-path fixture\n")`
  to `team/tests/fixtures/dashboard_render_fixture.py` produces **no** finding, and the
  module docstring ("What changed in the move", item 3) states that decision and gives two
  measured reasons (PEP 701 f-string tokenization misses 3 of the 9 known citations; the
  wider matcher produced 63 findings dominated by synthetic Markdown test data). The
  criterion explicitly sanctions this alternative resolution. **pass**
  Control: the same citation written as a real `#` comment in the same file IS reported
  (`dashboard_render_fixture.py:396`), so the boundary is where the docstring says it is.
- Test coverage for both probe shapes: `team/tests/unit/test_lint_hygiene.py` grew 137
  lines covering the docs surface and the string-literal exemption. **pass**
- Sidecar justifications: `team/scripts/hygiene-lint-allowlist.json` holds 85 entries,
  0 with a blank or missing `justification`. **pass**

**CODER-034 — clear fixture citations.**
- `git grep -nE 'BUG-[0-9]{4}|PRIORITY-[0-9]{4}|(CODER|WRITER|TESTER|PM|CM)-[0-9]{8}-[0-9]{3}' -- team/tests/fixtures/`
  returns only bug-ledger corpus files, the engine-baseline wake-log's synthetic task IDs,
  the `fixture_*` synthetic lint corpora, and golden JSON task IDs — exactly the carve-out
  the criterion names. **pass**
- `git grep -nE 'BUG-[0-9]{4}' -- team/tests/fixtures/container_engine_free/` returns
  nothing. **pass**
- Behavioural statements preserved in every reworded line (read in context). **pass**

**WRITER-035 — shipped documentation citations.**
- `python3 team/scripts/check_docs_citations.py` exits 0 from the repository root
  (24 governance files scanned, 17 docs/ citations checked). **pass**
- `grep -n 'operator-troubleshooting' docs/OPERATIONS.md docs/operator-commands.md`
  returns nothing. **pass**
- `git grep -nE 'BUG-[0-9]{4}' -- docs/blocking-site-census.md` returns nothing. **pass**
- Census rollup: 64 table rows; rollup line reads "64 sites, 0 unclassified" and
  "35 BLOCK-WORTHY, 27 CONVERT, 2 AGENT-RESOLVABLE" (35+27+2=64). The
  gate-census compliance lint is green (0 offenders, 180 anchored sites exempted).
  **pass**
- `docs/README.md` now says "Six common symptoms" and names all six; the document
  carries exactly six `##` symptom sections. **pass**
- `run-unit-tests.sh` exit 0: **fail** — see Finding 2 (pre-existing, environmental).

**CODER-036 — discovery bundle cargo.**
- The narration-channel separation and the `_disc_write_bundle` no-valid-items guard
  were already present from v2.24.12; the new work is the static audit. Read the guard
  in `team/scripts/lib/discovery_state.sh`: it counts entries resolving to a regular file,
  and at zero emits the dual-channel contract `discovery.bundle-write-no-valid-items`
  with a `CENSUS-ANCHOR` and returns 1 before writing anything. **pass**
- Static grep audit is part of the suite: `TestBugDiscoveryStepWritesNoNarrationToStdout`
  in `team/tests/integration/test_discovery_narration_channel.py`. Behaviourally verified
  (Step 6.5): baseline passes; inserting a bare `echo "regression probe narration line"`
  as the first line of `discovery_step_bugs` makes it FAIL naming the offending line;
  reverting restores green. **pass**

**CODER-037 — discovery mint governor.**
- Behaviourally verified (Step 6.5) by extracting `_disc_pending_bundle_of_type` and
  `_disc_read_bundle_field` into an isolated sandbox and driving the predicate directly:
  a bundle with `## Status: running` is returned (rc=0); flipping it to `done` returns
  rc=1; flipping it to `wont-do` returns rc=1; a pending `bugfix` bundle does not block
  the `priority` slug; an empty directory returns rc=1. The terminal set matches
  `discovery_items.sh`'s two-value `is_terminal()`. **pass**
- Decline narrates the pending bundle's path via `log()` and
  `pgai_emit_error_contract` (`discovery.bug-bundle-already-pending` /
  `discovery.priority-bundle-already-pending`), and returns 1 to
  `discovery.sh`'s `if discovery_step_bugs …` caller, which reads a non-zero return as
  "produced no work" and continues the pipeline — so the pass still exits zero. **pass**
- Both refusal sites carry a `CENSUS-ANCHOR` comment with a matching Locator Index
  entry (`discovery-steps-bug-mint-governor-01`, `discovery-steps-priority-mint-governor-01`,
  both CONVERT); `lint_gate_census_compliance` is green. **pass**

**CODER-038 — dashboard pane race.** Not implemented. See Finding 1.

**CODER-039 — discovery engine size.**
- `wc -l team/scripts/lib/discovery_state.sh` = 517 (was 814);
  `discovery_steps.sh` = 589 (was 868); two new stages at 336 lines each. **pass**
- `lint_engine_size_dupe: ok -- 39 engine file(s) within the 800-line ceiling, no
  cross-file function-name collisions found` in the unit runner's pre-flight;
  `findings-unit.json` contains 0 findings. **pass**

**CODER-040 — docs citation scan widened.**
- `team/tests/unit/test_docs_citation_scan_relaxed_forms.py`: 4 passed (bare filename,
  relative markdown link counted once, out-of-docs scope guard, restore-the-old-citations
  red case). **pass**
- Behaviourally verified (Step 6.5): appending
  `See operator-troubleshooting.md for the stuck-state walkthrough.` — a bare sibling
  filename, the exact spelling the old scanner could not see — to `docs/OPERATIONS.md`
  makes `check_docs_citations.py` exit 1 and report
  `docs/OPERATIONS.md:3260 cites 'docs/operator-troubleshooting.md', which … does not name`.
  Reverting restores exit 0. **pass**

**CODER-041 — provenance lint sweep.** `WONT-DO`, on the grounds that the sibling
tickets already drove the lint to zero. Independently confirmed:
`python3 team/scripts/lint_hygiene.py --classes provenance` reports
`OK — 0 findings across 9 scan director(y/ies)`. The ticket's sole acceptance criterion
holds on the RC. **pass**

## Other methodology steps

- **6.1 release-state.md.** Four fields, all well formed: `Active RC v2.24.13`,
  `RC Opened At 2026-09-23T13:39:28+00:00`, `RC Opened By Task CM-20260923-042-open-rc-2-24-13`,
  `Last Released v2.24.12`. v2.24.13 > v2.24.12. No drift. **pass**
- **6.8 synthetic release simulation.** The diff touches no release-lifecycle file
  (`cm/release.sh`, `cm/open-rc.sh`, `cm/cancel-rc.sh`, `lib/project_paths.sh` are all
  untouched); `test_cm_release_synthetic.py` runs inside the integration suite regardless.
  Skipped as a standalone step, with note.
- **6.9 constraint negative checks.** No hardcoded secrets or banned patterns introduced;
  both new discovery stages keep `set -euo pipefail` conventions of the files they were
  split from, and the extractions are byte-identical moves.
- **6.10 prereq dependency graph.** No cycles; every referenced task ID resolves to a real
  folder; bookends correct (CM-042 open → seven build tickets → TESTER-043 → CM-045
  release, currently `WAITING`). Sequence 044 is unused, which is not a collision. **pass**
- **6.13 skip-cites-real-bug.** `lint_skip_bug_gate: OK — all skip annotations are
  compliant`, `BUG-NNNN file-existence check: 0 missing files`. Manual grep found no
  placeholder shapes outside archived measurement artifacts and synthetic fixtures.
  **pass**
- **6.14 coding-standards spot-check.** The RC adds no new CLI script under
  `team/scripts/` (`lint_help_presence` is green over 120 scripts). It does add one new
  comment/docstring citing an internal bug identifier — Finding 3.

## Gated suites (Step 5a)

| Suite | Command | Exit | Result |
|---|---|---|---|
| unit | `bash team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=1` | 12 failed, 4953 passed, 31 skipped in 808.26s |
| integration | `bash team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=0` | 547 passed, 1 skipped in 659.20s |

Every lint pre-flight in both runners was green, including `check_breakage_class`,
`lint_container_engine_free`, `lint_test_anti_patterns`, `lint_skip_bug_gate`,
`lint_orphan_tests`, `lint_api_parity`, `lint_doc_command_truth`,
`lint_engine_size_dupe`, `lint_help_presence`, `lint_hygiene`,
`lint_dual_channel_pair` and `lint_gate_census_compliance`. The twelve unit
failures came from pytest, not the pre-flight.

`PASS` is therefore not available (Step 8 suite-pass precondition). Output teed
to `${PGAI_AGENT_KANBAN_TEMP_DIR}/tester/unit.log` and `.../integration.log`.

## Coverage (Step 5b — informational only)

Read from the runners' JSON at `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/`, field
names verified against the generated files.

| Suite | line (statement) % | branch % | blended % |
|---|---|---|---|
| unit | 76 % (12965/16971) | 63 % (3238/5122) | 73 % |
| integration | 11 % | 9 % | 11 % |

Coverage did not enter the verdict.

## Findings

Six findings: one `gap`, five `bug`. All six are filed via Path C (autonomous
follow-up filing — non-blocking), each as Path C-A. Per-finding detail with
reproduction steps is in `artifacts/gaps.md`.

### Finding 1: gated unit runner cannot exit 0 inside an agent wake session

**Category:** bug (pre-existing)
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** high (a framework-level regression outside this RC that
breaks the one authoritative pass/fail signal for every non-CM role, on every
cycle)
**Regression:** no
**Integrity:** breakage
**Filed As:** BUG-0383 (Path C-A — filed as BUG: code defect / regression / test failure)

`UNIT_EXIT=1`, with all twelve failures in
`team/tests/unit/test_git_boundary_guard.py`. Isolated to two variables the
wake layer exports into every non-CM agent's environment: `GIT_GUARD_REAL_GIT`
(pinned by `scripts/lib/wake_guard_wiring.sh` to the real git binary before the
shim directory is prepended to `PATH`) and `PGAI_GIT_GUARD_DEPTH` (the
recursion sentinel). Those tests install their own stub `git` ahead of
everything on `PATH` and assert on what the shim does with it; the shim reads
`_gbg_real_git="${GIT_GUARD_REAL_GIT:-}"` before consulting `PATH`, so the stub
is never reached.

Evidence, in order:

1. `bash team/scripts/run-unit-tests.sh --coverage` → exit 1, the twelve names
   listed in `gaps.md`.
2. `cd team && python3 -m pytest tests/unit/test_git_boundary_guard.py -q
   -p no:cacheprovider -p no:randomly` → 12 failed, 24 passed.
3. The same command under
   `env -u GIT_GUARD_REAL_GIT -u PGAI_GIT_GUARD_DEPTH` → **36 passed**.
4. A pristine clone of `ai_main` at `700b42d` (the shipped v2.24.12 tip), run
   under the same ambient environment → the same twelve failures. This is what
   establishes `Regression: no`: the RC's diff touches no guard file and cannot
   reach this.

`run-unit-tests.sh` already scrubs three variables from the pytest environment
(`env -u PYTHONHASHSEED -u PGAI_LINT_CHANGELOG_MODE -u KANBAN_ROOT`) and does
not scrub these two, which is the cheapest place to fix it.

**Why this is rated `high` rather than `medium`, stated plainly for CM.** The
finding is not about v2.24.13's content, which is clean. It is that the
framework currently cannot produce a green gated unit suite from inside its own
wake harness. Every CODER and WRITER acceptance criterion that reads "exits 0"
is unsatisfiable there; TESTER's `PASS` precondition is unreachable, so every
future cycle inherits a red signal it must re-diagnose; and three separate
agents in this RC (WRITER-035, CODER-039, CODER-040) each burned effort proving
the failures were not theirs. That is a broader framework regression breaking
multiple components, which is the rubric's definition of `high`.

**And the counter-argument, also stated plainly.** Stopping v2.24.13 does not
fix this. The defect predates the RC, lives in already-shipped framework code,
and reproduces identically with the RC's work reverted. A clean signal is
obtainable today — 36/36 with two variables unset — and the RC's own diff is
green under every lint and every other test. Shipping v2.24.13 and taking
BUG-0383 as the next cycle's first ticket costs nothing and removes the defect
sooner than a halt does. The rating is the honest reading of the rubric; the
disposition is CM's.

### Finding 2: one of eight bundled items was never implemented

**Category:** gap
**Fix Effort:** medium (3-5 CODER tasks — the fault is non-deterministic and the
reproduction is constrained by a hard resource-safety envelope)
**Systemic Risk:** medium (could recur in similar RCs: a ticket whose
verification method is barred by the host's resource envelope will stall the
same way again)
**Filed As:** BUG-0384 (Path C-A)

The requirements document's first acceptance criterion is "Each bundled item is
addressed in the implementation." Seven of the eight are; the dashboard
switch-storm pane death is not. `git log ai_main..ai_rc/v2.24.13 --
team/scripts/dashboard/` is empty, and the owning ticket
`CODER-20260923-038-dashboard-pane-race` ended `WONT-DO` after its agent was
killed (exit 137) attempting to force the race.

The defect is intermittent. `test_thirty_immediate_window_switches_leave_every_pane_alive`
passed in this verification's integration run; that is recorded as an
observation, not as evidence the fault is gone.

BUG-0384 is a carry-forward re-file rather than a duplicate: without it,
Finding 3 closes the original report at ship time and the defect leaves the
queue.

### Finding 3: the ship will mark the unfixed bundled item done

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (structural — it applies to every bundle whose fix
ticket ends `WONT-DO`, not just this one)
**Filed As:** BUG-0385 (Path C-A)

`team/scripts/cm/promote_bundled_items.py` (the cm-release Step 16b helper)
promotes every `BUG-*.md` enumerated in the bundle's `## Bundled Items` from
`running` to `done`, consulting only the enumeration and the item's current
status. The unfixed item from Finding 2 is enumerated and still `running`, so
the tag will close it. Discovery treats `done` as terminal and unconditionally
skips it, so a wrong promotion is invisible afterwards.

The script's second, task-scanning derivation path already applies the correct
rule — its own invariants say it "ONLY promotes a bug if it is explicitly
referenced by a DONE task README" — so the fix is to hold the primary
enumeration path to the same standard and report carried items in the post-ship
summary. `tasks_dir` is already passed in, so the data needed is on hand.

### Finding 4: the hygiene lint's own docstring ships an internal bug identifier

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one line of one file)
**Filed As:** BUG-0386 (Path C-A)

`team/scripts/lint_hygiene.py:183` reads
"(BUG-0377 already tracks them by name for a follow-on ticket to reword or
allowlist)". The line was added on this RC (commit `6d6720e`). It matches none
of `SOP-core.md`'s three sanctioned exceptions — the neighbouring `BUG-9999`
and `BUG-9001` tokens in the same docstring are worked examples and are fine;
this one is a provenance citation.

The lint reports zero findings tree-wide because `lint_hygiene.py` is a member
of its own `_FILE_WHITELIST` and is excluded from every surface. Verified by
probe: appending `# BUG-9998 probe comment` to `lint_hygiene.py` yields zero
findings, while the identical comment in a sibling file under `team/scripts/`
is reported. There is also no sidecar entry for the line, so it is neither
reported nor deliberately retained with a justification — the two dispositions
the ticket's own constraints allow.

The self-exclusion is well founded (the module necessarily carries
identifier-shaped pattern strings), so the finding is as much about the blind
spot as the line: the one file that enforces this rule is the one file that can
break it unseen.

### Finding 5: the troubleshooting page states eight halt triggers; there are nine

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one sentence)
**Filed As:** BUG-0387 (Path C-A)

`docs/operator-troubleshooting.md:206` tells an operator the CM halt trigger is
"one of the eight enumerated in `docs/OPERATIONS.md`". That document enumerates
nine and says "nine" three times, and `docs/operator-commands.md:1982` says
nine. The count was corrected in `operator-commands.md` during this cycle and
left stale here, so the ninth trigger ("The release-notes status stamp failed")
is missing from the set the reader is told to expect.

### Finding 6: boundary-guard evidence records on every agent's test run

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (signal hygiene; no boundary was actually crossed)
**Filed As:** BUG-0388 (Path C-A)

This is the Step 6.12 question (5) YES, written up as its own finding. Three of
this cycle's build task folders carry `git-boundary-guard.jsonl` records — 11
for CODER-039, 19 for CODER-040, 16 for WRITER-035 — and this verification run
produced 11 more of its own under role `tester`, for 57 across four folders.
Every one is a
`git -C <temp-root fixture repo> fetch origin --tags --quiet` issued by
production code inside a pytest fixture repository.

Behaviourally probed rather than inferred: a temp-root repository whose
`origin` resolves to another temp-root path passes the guard with exit 0 and
**no** record, so the fixture-local exemption itself works. These records come
from the documented conservative fallback for a destination that cannot be
resolved at all (no `origin` configured, or the `-C` directory already gone) —
the guard refuses rather than assume local. Rated `low` per the reference's
own rule: the attempts were refused, read nothing, and moved no ref state.

The cost is that question (5) now answers YES every cycle, so a real crossing
would arrive indistinguishable from fixture noise.

## Lint Findings (non-blocking)

None. `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` and
`.../findings-integration.json` both read `{"findings": []}` (schema_version 1,
generated 2026-09-25T03:28:59+00:00 and 2026-09-25T03:42:12+00:00). No
quality-class finding was recorded by either runner, so nothing here is cited
or filed. The engine-size finding that both runners carried through v2.24.12 is
gone, which is CODER-039's acceptance criterion met.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=17d freshness=stale — Both conditions fail: the record is 17 days old against the 7-day window and names v2.9.20, not the v2.24.13 under verification; its `failed-to-build` verdict was already filed as BUG-0167 (cold-build claude-abliterated failure, now `done`) and BUG-0168, so nothing new is owed here.


## Autonomous criterion (Step 6.12)

**Autonomous operation criterion: Manual interventions required: 2 (questions 1
and 5 answer YES; questions 2, 3 and 4 answer NO).**

1. **Were any tasks force-promoted from WAITING to BACKLOG by hand, bypassing
   the wake script's prerequisite evaluation?** — **NO** for that exact shape,
   but a different manual state change did occur and is reported here rather
   than omitted. `tasks/CODER-20260923-038-dashboard-pane-race/status.md` was
   hand-edited: its `## State` reads `WONT-DO` while its own `## Summary` still
   reads "Reset to BACKLOG by wake reconciliation … exit code 137 … (cycle
   1/3)", its `## Zero Progress` block reads `count: 1, limit: 3` (below the
   cap, so the automation would have retried), its `## Next Recommended Step`
   still reads "In progress: locating the activation hook", and the Summary
   carries a human-voice instruction ("this system is a rented VPS and i
   already got throtled for a day"). The file's mode is `0600` where every
   sibling `status.md` is `0644`, and its mtime (2026-09-25T00:55) is 31 hours
   after the task's last agent log line. **Caught by:** the `status.md` audit
   trail plus file metadata. **Does it indicate a defect worth shipping as a
   follow-up?** Yes — two, both filed. The intervention itself was a legitimate
   operator safety action (the ticket's own README carries a mandatory
   RESOURCE SAFETY block added after a prior attempt at the same work put the
   host in a provider throttle jail), so it is not an autonomy failure of the
   pipeline's own making. What it leaves behind is: an unimplemented bundled
   item (Finding 2, BUG-0384) and a fix ticket whose stated verification method
   cannot be executed within the host's resource envelope — which is why
   BUG-0384 restates the reproduction contract in full rather than leaving the
   next agent to rediscover it.

2. **Was `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh`?** — **NO.** All four fields are well formed and the file's
   mtime (2026-09-23T13:39:28) matches `## RC Opened At` to the second.

3. **Were any scripts patched mid-build — a commit touching `team/scripts/`,
   `subagents/`, or other tooling while the RC was in flight?** — **NO.** Every
   commit in `ai_main..ai_rc/v2.24.13` belongs to one of the seven DONE tickets
   and is that ticket's own deliverable. Two operator actions on the live
   install are noted under Caveats; neither is a commit and neither patched a
   script.

4. **Were any queue files edited by hand?** — **NO.** Every RC task's marker
   matches its `status.md` state (`[x]` for the seven DONE and the two WONT-DO,
   `[A]` for this in-flight TESTER task), which is the reconciliation pass's own
   output shape.

5. **Did any agent other than CM invoke a remote git operation?** — **YES**, and
   the answer is read from the files rather than inferred.
   `logs/git-boundary-guard.jsonl` exists in four of this cycle's task
   folders: `CODER-20260923-039-discovery-engine-size` (11 records, role
   `coder`), `CODER-20260923-040-docs-citation-scan-widen` (19 records, role
   `coder`), `WRITER-20260923-035-shipped-docs-citations` (16 records, role
   `writer`), and this task's own folder (11 records, role `tester`) — produced
   by this verification's own gated-suite runs, which is itself confirmation of
   the mechanism rather than a separate event. 57 records in total. Every
   record's `argv` is a
   `git -C <path under $PGAI_AGENT_KANBAN_TEMP_DIR> fetch origin --tags --quiet`
   issued from inside a pytest fixture repository during the gated suites. No
   record carries role `unknown`. Every attempt was refused by the guard before
   reaching a remote; none read anything and none moved a ref, so the rating is
   `low` per the reference's rule. Reported as Finding 6 and filed as BUG-0388.

## Caveats

- **Two operator touches on the live install during the RC window, neither a
  pipeline intervention.** `install.sh` was run at 2026-09-24 12:38, refreshing
  374 files under `$PGAI_AGENT_KANBAN_ROOT_PATH/scripts` to the shipped
  v2.24.12; and `kanban.cfg` was edited at 2026-09-25 01:24. Both are operator
  configuration/maintenance of the running framework, not edits to the RC, to
  task state, or to queue files. The reinstall is worth noting next to Finding
  1: the last recorded TESTER verification round in this project observed
  `UNIT_EXIT=0`, and the guard-wiring code that causes today's twelve failures
  shipped in v2.24.10/v2.24.11, so the live install reaching v2.24.12 is the
  most likely moment the ambient pin began leaking into agent sessions. That is
  a timeline observation, not a verified cause; what is verified is that the
  failures reproduce on the pre-RC tip under today's environment.
- **A leftover worktree.** `git worktree list` shows
  `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/CODER-20260923-038-dashboard-pane-race`
  still registered at `4984387` on `ai_feature/CODER-20260923-038-dashboard-pane-race`,
  left behind when that task was withdrawn. The branch holds zero commits ahead
  of the RC, so nothing is unmerged; it is housekeeping the worktree-sweep check
  would normally reclaim. The sibling `ai_feature/CODER-20260923-041-provenance-lint-sweep`
  branch is likewise at zero commits ahead. Recorded as an observation, not a
  finding.
- **The pane-race test passed this run.** `test_dashboard_activation_hook_lifecycle.py`
  was collected and green in the integration suite. Because BUG-0379 documents
  the fault as intermittent, that is recorded as an observation and explicitly
  does not clear Finding 2.
- **Sequence 044 is unused** between TESTER-043 and CM-045. Not a collision and
  not a broken link — the bundle's `## Human Approval Required` reads `auto`, so
  no approval task was materialised.
- **`VERSION` still reads `v2.24.12` on the RC branch.** That matches
  `ai_main` and is the pre-tag shape; the bump is CM's at release. Not drift.
- **Test fidelity.** The new static audit for the bug discovery step
  (`TestBugDiscoveryStepWritesNoNarrationToStdout`) passes any line containing a
  `|` character, so an `echo` whose *message text* contains a pipe would slip
  through even if it wrote narration to stdout. It correctly caught the
  synthetic regression I planted, and the narration channel is independently
  covered by the dynamic tests in the same module, so this is a plausible rather
  than concrete concern — recorded here rather than filed.
