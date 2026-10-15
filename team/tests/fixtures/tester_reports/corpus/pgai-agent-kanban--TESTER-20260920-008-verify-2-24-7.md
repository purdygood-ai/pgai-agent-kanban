# Verification Report: ai_rc/v2.24.7

**Task:** TESTER-20260920-008-verify-2-24-7
**RC commit:** `3ff6eeca75ec0e8b5372218c01dcbbb4a18d2cc4` (`ai_rc/v2.24.7`)
**Baseline:** `ai_main` @ `f72cc9e`
**Requirements:** `projects/pgai-agent-kanban/requirements/v2.24.7-priority-bundle-20260919.md`,
bundling `priority/PRIORITY-0005-enforce-origin-boundary-guard.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260920-008-verify-2-24-7`
(detached HEAD at the RC commit)
**Workflow type:** release
**Verified:** 2026-09-20

## Summary

v2.24.7 turns the "CM is the ONLY toucher of any remote" swim-lane rule from
governance prose into an enforced, evidence-producing mechanism. It ships a
`git` shim (`team/scripts/wake/git-guard/git`) and its decision library
(`team/scripts/lib/git_boundary_guard.sh`), wires the shim onto `PATH` for every
non-CM wake dispatch (`team/scripts/lib/wake_driver_invoke.sh`), registers the
refusal as blocking-site census row 62 with a matching Locator Index entry, adds
a fifth question to TESTER's Step 6.12 autonomous-operation criterion, documents
the mechanism in `SOP-core.md` / `CODER.md` / `WRITER.md`, and adds three test
modules totalling 841 lines. All five prerequisite tasks are `DONE`, every
feature branch merged and was deleted, and the RC tip is a well-formed merge.

Verification ran to completion. The mechanism is correctly wired and behaves
exactly as documented for the plain invocation shape — I exercised all three
refused verbs, the CM pass-through, the non-remote pass-through, both fail-open
paths and the recursion guard against the shipped shim. Two defects dominate the
verdict. First, the guard reads only the first argument after `git`, so any
leading git global option (`git -C <path> fetch`, `git --no-pager push`,
`git -c k=v pull`) bypasses it entirely: the command reaches the real remote and
**no evidence record is written at all**, which means the Step 6.12 question (5)
shipped in this same release will report a false NO. `git -C <path>` is the
shape agents in this framework use by default, because their shell resets its
working directory between commands. Second, the wake-driver wiring grew
`team/scripts/lib/wake_driver_invoke.sh` from exactly 800 to 845 lines, tripping
the engine-size ceiling and turning `run-unit-tests.sh` red for the first time
since v2.24.6.

Six findings were filed via Path C (autonomous follow-up filing — non-blocking):
BUG-0349 through BUG-0354. None is destructive and nothing in the RC corrupts
state; the concerns are that the release's own deliverable does not hold for the
common case, and that the authoritative suite signal is red.

## Recommendation

SHIP-WITH-SERIOUS-CONCERNS

Reasoning, stated plainly so CM can weigh it. Nothing here damages a running
system or destroys state, and CM may well judge this shippable — the
recommendation is informational input, not a veto. It is set at the higher level
for two reasons that Step 8's branch (4) names directly. The release breaks the
release pipeline's own authoritative signal: `run-unit-tests.sh` exited 0 at
v2.24.6 and exits 1 at v2.24.7 because of this RC's own commit `c99bc24`, so no
subsequent RC verified against this tree can record a suite PASS until BUG-0350
lands. And it ships a verification check that will lie: Step 6.12 question (5)
is answered from an evidence file the guard fails to write for the invocation
shape agents actually use, so future reports will record "Manual interventions
required: none" on strictly weaker evidence than that sentence claims, which is
an autonomy failure making future cycles less reliable rather than more.

I considered SHIP-WITH-CONCERNS and rejected it. Both defects are cheap to fix
(`small` fix effort each) and neither is destructive, which is the argument for
the lower call. But a green-looking verdict on a release whose deliverable does
not hold for the common case, and whose own commit turned the gated suite red,
would misreport the state of the tree — and `SOP-core.md` ("Rating a Finding")
is explicit that a rating is never softened to reach a preferred ship outcome.

## Systemic Risk

high

The maximum across all findings, carried from BUG-0350: a regression in this RC
renders the gated unit runner — the only authoritative pass/fail signal the
release process has — red for every verification run against this tree until it
is fixed. That is framework-level rather than RC-specific. Every other finding
is `medium` or `low`.

## Suite results

**PASS evidence is the gated runners' exit codes, never a pytest "N passed"
line.** Both runners were invoked from the worktree root with `--coverage`.

| Suite | Command | Exit | Result |
|---|---|---|---|
| unit | `bash team/scripts/run-unit-tests.sh --coverage` | `UNIT_EXIT=1` | **FAIL** — 2 failed, 4922 passed, 31 skipped (796.77s) |
| integration | `bash team/scripts/run-integration-tests.sh --coverage` | `INTEGRATION_EXIT=1` | **FAIL** — pytest itself was green (521 passed, 1 skipped, 587.79s); the non-zero exit came from the post-suite scratch sweep |

Captured output: `/tmp/pgai_kanban_tmp/tester/unit.log`,
`/tmp/pgai_kanban_tmp/tester/integration.log`.

Both unit failures are `tests/unit/test_lint_engine_size_dupe.py`
(`test_clean_real_tree_has_no_size_violations`,
`test_clean_real_tree_main_exits_zero`), both reading
`{'lib/wake_driver_invoke.sh': 845}` against a ceiling of 800 — Finding 2.

The integration runner's exit 1 is **not** a test failure and **not** caused by
this RC. Every test passed; the runner then aborted in
`pgai_temp_cleanup_tests` on read-only leftovers a prior run left in the shared
temp root, under `set -euo pipefail`, before reaching its `exit 0` — Finding 6.
The three new guard test modules all passed:
`tests/integration/test_git_boundary_guard_wiring.py ..` (line 137 of the
integration log), and the two unit modules passed inside the 4922.

## Coverage (informational only — never enters the verdict)

Read from the `totals` object of each runner's JSON; field names verified
against the generated files rather than recalled.

| Suite | Line (`percent_statements_covered`) | Branch (`percent_branches_covered`) | Blended (`percent_covered`) |
|---|---|---|---|
| unit | 76.36 % | 63.15 % | 73.31 % |
| integration | 11.48 % | 9.21 % | 10.95 % |

Sources: `/tmp/pgai_kanban_tmp/coverage/unit.json`,
`/tmp/pgai_kanban_tmp/coverage/integration.json`. Coverage did not affect the
recommendation, the systemic-risk rating, or any finding.

## Pre-flight

- **Step 2 — RC intact.** Every task folder under
  `projects/pgai-agent-kanban/tasks/` whose `status.md` reads `DONE` and whose
  README names a `## Feature Branch` other than `none` was scanned. No such
  branch still exists locally, which passes both checks trivially — CODER and
  WRITER delete branches after merging, and that is the normal post-merge shape.
  The one surviving feature branch,
  `ai_feature/CODER-20260919-040-provenance-hygiene-sweep`, belongs to a
  `WONT-DO` task and is out of scope for Step 2.
- **Step 3 — working tree.** `git rev-parse HEAD` =
  `git rev-parse ai_rc/v2.24.7` = `3ff6eec`. `git status --porcelain` empty.
  `git stash list` empty.
- **Step 6.1 — release state.** `## Active RC` `v2.24.7`, `## RC Opened At`
  `2026-09-20T00:48:45+00:00`, `## RC Opened By Task`
  `CM-20260920-007-open-rc-2-24-7`, `## Last Released` `v2.24.6` — all four
  fields present and well formed. `pp_last_released_version "pgai-agent-kanban"`
  returns `v2.24.6`; `semver_gt v2.24.7 v2.24.6` is true (shared helper from
  `team/scripts/lib/semver.sh`, never an inline comparison). The RC branch
  exists and matches the recorded `Active RC`. No drift.

## Requirements acceptance criteria

| Criterion | Result |
|---|---|
| Each bundled item is addressed in the implementation | pass-with-caveat — PRIORITY-0005's asks 1 (prevention at the wake layer), 3 (a fifth Step 6.12 check) and 4 (documentation) are all implemented, and ask 2 (detection after the fact) is served by the per-task evidence file. The caveat is Finding 1: for the common invocation shape neither the prevention nor the detection leg fires. |
| All Python files compile cleanly | pass — `find team docker subagents -name '*.py' \| xargs python3 -m py_compile` exits 0, no output. |
| All bash files pass syntax check | pass — `bash -n` over every `*.sh` under `team/`, plus `install.sh` and the extensionless shim `team/scripts/wake/git-guard/git`: zero failures. |

## Findings

### Finding 1: the origin-boundary guard is bypassed by leading `git` global options

**Category:** bug
**Fix Effort:** small (1–2 CODER tasks)
**Systemic Risk:** medium (the same first-argument assumption recurs in any
future command-wrapper or shim work)
**Regression:** no (new functionality in this RC; nothing worked before it)
**Integrity:** none
**Filed As:** BUG-0349-git-guard-bypassed-by-global-opts (Path C-A — filed as
BUG: code defect / regression / test failure)

`git_boundary_guard_enforce` reads `local subcommand="${1:-}"` and switches on
it. `git` accepts a run of global options before the subcommand, so with any of
them present `$1` is the option, the `case` falls through to `*) return 0`, and
the shim `exec`s the real git. The remote operation is not refused **and no
evidence record is written**, because the append happens after the `case`.

Behavioral verification against the shipped shim at `3ff6eec`, run with a
throwaway evidence file and `--dry-run` against a non-existent remote so nothing
could reach a real origin:

| Invocation | Role | Refused? | Recorded? |
|---|---|---|---|
| `git fetch --dry-run <remote>` | coder | yes, exit 1 | yes |
| `git pull` | tester | yes, exit 1 | yes |
| `git push origin <branch>` | writer | yes, exit 1 | yes |
| `git -C <path> fetch --dry-run <remote>` | coder | **no** — real git ran (exit 128 from git) | **no** |
| `git --no-pager fetch --dry-run <remote>` | coder | **no** — real git ran | **no** |

The limitation is deliberate and documented in
`CODER-20260920-002-git-boundary-guard`'s `status.md` as design note (1),
"subcommand detection reads only the first argument (no scan past leading global
git flags) -- matches every tested invocation shape". It matched every *tested*
shape because no test covers one: none of the three new modules contains a
`-C`, `-c`, `--git-dir` or `--no-pager` case. Neither the shipped `SOP-core.md`
paragraph ("`fetch`, `pull`, and `push` are refused dual-channel") nor the
shim's own header ("every git invocation then runs through here first") mentions
the limitation, so the documented contract is wider than the code.

Why this shape is the common one rather than an edge case: agent threads in this
framework have their working directory reset between shell calls and are
instructed to use absolute paths, which makes `git -C <path> <verb>` the default
way an agent invokes git. The historical incident PRIORITY-0005 was filed
about — a literal `git fetch --all` — is caught; the same agent writing
`git -C <worktree> fetch --all` would not be.

Downstream effect, and the reason this is the heavier half of the verdict: Step
6.12 question (5), added in this same release, is answered from the evidence
file. A cycle in which an agent crossed the boundary this way produces exactly
the same evidence as a clean cycle — none — so the check reports NO and the
report states the boundary held when it did not.

### Finding 2: this RC's wake wiring puts `wake_driver_invoke.sh` over the engine ceiling and turns the unit suite red

**Category:** bug
**Fix Effort:** small (1–2 CODER tasks)
**Systemic Risk:** high (a regression that leaves the gated unit runner — the
release process's only authoritative pass/fail signal — red for every
verification against this tree until fixed)
**Regression:** yes (introduced by this RC's commit `c99bc24`; the same command
exited 0 at v2.24.6)
**Integrity:** breakage (gated unit suite fails)
**Filed As:** BUG-0350-wake-driver-invoke-over-engine-ceiling (Path C-A)

`team/scripts/lib/wake_driver_invoke.sh` is 845 lines on the RC and was exactly
800 at `ai_main` — at the ceiling before, 45 over it now. The growth is the
`GIT-BOUNDARY-GUARD-WIRING-BEGIN`…`-END` block: roughly 6 lines of wiring and
roughly 39 lines of block comment documenting the two exported variables. The
documentation itself is good practice under the Documentation Discipline Rule;
the file it landed in had no headroom.

Evidence: `wc -l` → 845; `git show ai_main:… | wc -l` → 800;
`bash team/scripts/run-unit-tests.sh --coverage; echo $?` → 1 with the two
`test_lint_engine_size_dupe.py` failures quoted above;
`python3 team/scripts/lint_engine_size_dupe.py` reports
`lib/wake_driver_invoke.sh -- 845 lines (ceiling: 800)`.

The filed bug explicitly rules out raising `DEFAULT_MAX_LINES`, per
`SOP-core.md` ("Blocking Severity: only real damage blocks") — a threshold
problem is not fixed "by tuning the value it measured" — and offers extraction
of the guard wiring into its own single-concern stage as the preferred shape.

### Finding 3: an advisory size heuristic is enforced as a hard pytest gate

**Category:** bug
**Fix Effort:** small (1–2 CODER tasks)
**Systemic Risk:** low (isolated to one test module)
**Filed As:** BUG-0351-engine-size-heuristic-hard-gates-suite (Path C-A)

The engine-size ceiling is enforced at two sites with contradictory enforcement
classes. `docs/blocking-site-census.md` row 53 classifies
`lint_engine_size_dupe.py` as `CONVERT`, and the runners honour that: the
violation printed, landed in `findings-unit.json` with `severity: quality`, and
the lint's own remedy line reads "this is a non-blocking quality finding and
does not fail the gated runner". Two assertions in
`team/tests/unit/test_lint_engine_size_dupe.py` then failed the suite on the
same reading.

`SOP-core.md` ("Blocking Severity: only real damage blocks") rules this out —
"size heuristics and advisory thresholds … are CONVERT by their nature" — and
names the remedy: "a hard gate no requirement authorized is itself a bug, filed
against the gate rather than worked around by tuning the value it measured".
This finding is that filing.

Scope note carried into the bug: the *duplication* half of the same lint is a
different animal and the filing does not ask to soften it. Two files defining
one shell function name in a shared reachability set means bash silently shadows
an implementation, which is real damage and a legitimate hard assertion.
`test_clean_real_tree_main_exits_zero` currently folds both halves into one
exit-code assertion, which is why a size-only violation fails it.

Per Step 6.11's rule that a finding must trace to something concrete: this traces
to a constraint in `SOP-core.md`, not to a preference, and it was observed
firing on this run.

### Finding 4: governance byte budgets crossed for `tester` and `cm`

**Category:** gap
**Fix Effort:** small (1–2 CODER or WRITER tasks)
**Systemic Risk:** low (advisory lint; narrates and never blocks)
**Filed As:** BUG-0352-governance-byte-budget-over-tester-cm (Path C-A;
quality-class aggregate, one bug per `lint` class per run)

The `governance_byte_budget` lint reported `tester` at 104191 / 102400 (over by
1791) and `cm` at 86708 / 86016 (over by 692). Both crossed in this release:
`SOP-core.md` grew 1130 bytes and `roles/TESTER.md` 677 bytes, so TESTER's stack
was 102384 bytes (16 under) and CM's 85578 (438 under) before it.

Recorded as a `gap` rather than a `pass-with-caveat` because a stated budget is
exceeded, but it is quality-class throughout: it does not feed the Step 8
decision tree and does not on its own move the recommendation. See
`## Lint Findings (non-blocking)`.

### Finding 5: completed tasks' worktrees are not torn down, forcing a hand-built merge commit

**Category:** gap
**Fix Effort:** small to medium (2–3 CODER tasks; the sweep for pre-existing
leftovers is the larger half)
**Systemic Risk:** medium (recurs every cycle, and each recurrence pushes an
agent off the sanctioned merge path onto direct ref manipulation)
**Filed As:** BUG-0353-completed-task-worktrees-not-torn-down (Path C-A)

Step 6.11 spot-check, state-drift category. `git worktree list` in the dev tree
shows three worktrees for tasks in terminal states; two of them
(`WRITER-20260920-004-tester-remote-check`,
`WRITER-20260920-005-boundary-doc-update`) hold `ai_rc/v2.24.7` as a checked-out
branch rather than a detached HEAD.

The consequence landed on the next ticket.
`CODER-20260920-006-guard-e2e-tests` could not run
`git checkout ai_rc/v2.24.7 && git merge --no-ff …` — git permits a branch to be
checked out in one worktree at a time — and hand-assembled the merge with
`merge-tree --write-tree`, `commit-tree` and `update-ref` instead, recording the
whole thing in its `status.md`.

I verified the result rather than taking the note on trust: `git cat-file -p
3ff6eec` shows a well-formed two-parent merge commit (parents `ef452da` and
`41908880`), and `git diff --stat ef452da 3ff6eec` shows exactly the feature
branch's two files. The merge is correct. The finding is that the sanctioned
merge path was unavailable and the fallback is direct ref manipulation on the
release branch, which has far less margin for error.

### Finding 6: the integration runner exits 1 on a fully green suite (pre-existing)

**Category:** bug (pre-existing — predates this RC)
**Fix Effort:** small (1–2 CODER tasks)
**Systemic Risk:** medium (while present, no RC verified on this host can record
a suite PASS, whatever its own quality)
**Regression:** no (none of the files involved appears in
`git diff --name-only ai_main..ai_rc/v2.24.7`)
**Integrity:** none (no test failed; the suite itself is green)
**Filed As:** BUG-0354-integration-runner-red-on-clean-suite (Path C-A)

`run-integration-tests.sh` printed `521 passed, 1 skipped` and
`Integration tests PASSED`, both cleanliness checks reported OK, and the script
then exited 1. Cause, traced rather than inferred: the script sets
`set -euo pipefail` (line 113); its last action before `exit 0` calls
`pgai_temp_cleanup_tests`, which ends in a bare `rm -rf "${root}/tests"` with no
permission recovery; two leftover `0555` directories under that tree made `rm`
return non-zero; `set -e` aborted the script before `exit 0`.

Reproduced in isolation against a throwaway root (per "Live-install safety"): a
single `0555` directory under `<sandbox>/tmp/tests/` makes
`pgai_temp_cleanup_tests` fail and the sentinel line after it never prints.

The leftovers are timestamped `2026-09-20T01:24`, before this verification run
began, so an earlier invocation exited without the `atexit` chmod-restore in
`team/tests/unit/test_verb_fixture_matrix.py` firing — that hook does not run if
the process is killed or times out. The interaction is already known to the test
module, whose docstring names "a bare `rm -rf` with no chmod-recovery" as the
reason the hook exists; the filing asks for the sweep to be made
permission-tolerant and non-fatal instead, so the invariant stops depending on
every producer remembering.

### Finding 7: guard behavior matches its documented contract for the plain invocation shape

**Category:** pass

Exercised against the shipped shim at `3ff6eec`, all cases with a throwaway
evidence file, and every pass-through case aimed at a non-existent remote with
`--dry-run` so no real origin could be reached:

- `fetch` / `pull` / `push` under `coder` / `tester` / `writer` — each refused
  with exit 1 and the full dual-channel block: a stable `error:` key
  (`git-boundary-guard.non-cm-remote-verb`), a plain-language `message:` naming
  the role and verb, and a `remedy:` naming CM as the owner of the operation.
- Role matching is case-insensitive as documented: `PGAI_AGENT_ROLE=WRITER`
  refused and recorded `"role":"writer"`.
- Evidence is one self-contained JSON object per line carrying `timestamp`,
  `role`, `task` and the full `argv`, e.g.
  `{"timestamp":"…","role":"coder","task":"CODER-TEST-001","argv":["git","fetch","--all"]}`.
  `PGAI_TASK_ID` is picked up from the environment, defaulting to `unknown`.
- `PGAI_AGENT_ROLE=cm` + `fetch`: passed through to the real git, exit status
  the real git's, nothing recorded.
- Non-remote subcommand under `coder`: passed through, correct output, nothing
  recorded.
- Role unset with an evidence file configured: failed **open** (command ran) and
  recorded `"role":"unknown"`, exactly as the header's decision table states.
- Evidence file unset with `role=coder`: failed open, recorded nothing.
- Recursion guard: with only the shim's own directory on `PATH` the shim exits
  127 rather than re-entering itself; the committed unit test
  `test_shim_resolves_stub_git_when_its_own_dir_leads_path` covers the
  production arrangement with a stub git behind the shim.

Every documented behavior holds for these shapes. Finding 1 is the boundary of
that statement, not a contradiction of it.

### Finding 8: wake wiring activates the guard for non-CM roles and never for CM

**Category:** pass

Read at the changed region and confirmed structurally and behaviorally.
`SUBAGENT` is assigned lowercase in `team/scripts/lib/wake_driver_prep.sh`
(`CODER)  SUBAGENT="coder"` and siblings), so `PGAI_AGENT_ROLE` carries the
lowercase token the guard compares against. `SCRIPT_DIR` is set at
`team/scripts/wake/claude.sh:205` and `codex.sh:204` from each script's own
location — `team/scripts/wake` — so `${SCRIPT_DIR}/git-guard` resolves to the
shipped shim directory. `wake-batch.sh` `exec`s the provider script as a fresh
process, so its own `SCRIPT_DIR` (`team/scripts`) never leaks into this path.
`PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE` is built from `task_log_dir`, which is
established earlier in the same function, so evidence lands in the task's own
`logs/` and never in bare `/tmp`.

The critical safety property — CM's `PATH` never gains the shim, so CM's release
operations resolve exactly as before — is asserted by
`test_cm_role_path_never_gains_shim_directory` (unit) and
`test_cm_dispatch_excludes_shim_from_path` (integration, driving the real
`claude.sh` entry point), both of which passed on this run. A missing shim
directory degrades to a logged warning naming the path rather than a refusal,
covered by `test_missing_shim_directory_logs_warning_and_dispatch_still_proceeds`.

The shim is committed mode `0755` and `install.sh` copies `team/scripts`
wholesale with `cp -r "$src/." "$dst/"`, which preserves the executable bit, so
the extensionless shim survives installation even though the later
`chmod +x "$KANBAN_ROOT/scripts/"*.sh` pass does not match it.

### Finding 9: census registration and the documentation layer are correct

**Category:** pass

- `docs/blocking-site-census.md` row 62 registers the refusal as BLOCK-WORTHY
  with anchor `git-boundary-guard-01`, and the Locator Index carries the
  matching `team/scripts/lib/git_boundary_guard.sh:git-boundary-guard-01
  BLOCK-WORTHY` line. The rollup was updated consistently (61→62 sites, 33→34
  BLOCK-WORTHY, 0 unclassified). `python3 team/scripts/lint_gate_census_compliance.py`
  exits 0. The refusal site carries its census class in a code comment beside
  the anchor, as the authoring contract requires.
- Documentation-layer files were read rather than grepped, per Step 6.3.
  `SOP-core.md`'s two new paragraphs, `CODER.md`'s and `WRITER.md`'s
  "A refused remote git command is the boundary, not a broken tool" additions,
  and `TESTER.md`'s Step 6.12 change are internally consistent, point at each
  other correctly under the single-canon rule, and tell an agent the right thing
  to do on a refusal (record the need in `## Summary`, do not route around it,
  do not set `BLOCKED`). The one inaccuracy is the unqualified refusal claim
  covered by Finding 1.
- Protected-surface handling is correct. Step 6.12 is inside TESTER's PROTECTED
  SURFACE; PRIORITY-0005 names the step explicitly and flags the requirement,
  and `WRITER-20260920-004`'s ticket carries both the explicit naming and
  regression language ("A reviewer diffing Step 6.12 must see additions only").
  The diff bears that out: the four existing questions are byte-identical, the
  fifth is appended, and `grep -n 'four'` over the shipped `roles/TESTER.md`
  finds no remaining stale count for this criterion — the four hits are
  unrelated (infrastructure-failure table, release-state fields, status-writer
  fields, external-process contracts).
- No hardcoded secrets, bug IDs, task IDs, RC names or version citations appear
  in any of the five new or changed source/test files
  (`grep -nE "v2\.24|BUG-[0-9]|PRIORITY-[0-9]|(CODER|WRITER|TESTER)-2026"`
  returns nothing).

### Finding 10: skip-cites-a-real-bug gate is clean

**Category:** pass

`bash team/scripts/lint_skip_bug_gate.sh` exits 0. `grep -rn "BUG-SKIP"` over
`team/tests/` returns nothing. No placeholder skip annotation, and no skip
citing a `BUG-` identifier that does not resolve to a file in
`projects/pgai-agent-kanban/bugs/`.

### Finding 11: test fidelity of the new modules

**Category:** pass-with-caveat

The three new modules are well built. The unit module drives the real shim
through `subprocess` and uses a stub `git` for every pass-through case, so a
CODER or TESTER test run can never itself reach a real origin — the right call,
and an easy one to get wrong. The integration module drives the real
`scripts/wake/claude.sh` entry point through real WRITER and CM dispatches
against a throwaway kanban root.

Two caveats, both concrete enough to name and neither large enough to be its own
finding beyond what is already filed:

- The `test_wake_guard_wiring.py` module extracts the sentinel-delimited wiring
  block from `wake_driver_invoke.sh` and runs it in a harness rather than
  invoking `wd_invoke_task`. That is the project's established pattern and the
  integration module covers the real entry point, so the gap is closed at the
  suite level — but the harness would not catch a change that moved the block to
  a point where `SCRIPT_DIR` or `task_log_dir` is not yet in scope.
- The condition mocked away is the one Finding 1 is about: every test builds its
  argument vector as `<verb> [args…]`, so the suite cannot observe the
  first-argument assumption failing. BUG-0349's acceptance criteria name the
  missing cases explicitly.

## Lint Findings (non-blocking)

Read from `/tmp/pgai_kanban_tmp/lint/findings-unit.json` (4 findings) and
`/tmp/pgai_kanban_tmp/lint/findings-integration.json` (4 findings). All eight
carry `severity: quality`; none is fatal and none feeds the Step 8 decision tree
or the `## Systemic Risk` rating above. Grouped by `lint` class, with the
cross-suite rule applied (one class appearing in both files is one class):

- `test_anti_patterns` — two `[AP3]` hits, both in
  `team/tests/unit/test_why_details_pane.py` (lines 462 and 489): a hardcoded
  `/tmp/pgai_kanban_tmp` literal that should route through `pgai_temp_dir` or
  `PGAI_AGENT_KANBAN_TEMP_DIR`. Neither line is in this RC's diff. — cites
  BUG-0345-test-anti-pattern-hardcoded-temp-literal (open), which already
  describes exactly this; not duplicated.
- `engine_size_dupe` — `lib/wake_driver_invoke.sh` at 845 lines against the
  800-line ceiling. Fired in both suites' findings files; one class, one filing.
  — cites BUG-0350-wake-driver-invoke-over-engine-ceiling (Path C-A, filed by
  this run). Not filed a second time as a lint aggregate: the same condition
  reached Step 7 as a real defect, and Step 10's one-filing-per-gap rule governs
  there.
- `governance_byte_budget` — two instances, `tester` 104191/102400 and `cm`
  86708/86016, in both suites' findings files. — filed
  BUG-0352-governance-byte-budget-over-tester-cm (Path C-A), listing both
  instances with role, measured size and budget, severity taken from the worst
  instance.

No lint finding in this section moved `## Recommendation` or `## Systemic Risk`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=12d freshness=stale — Both freshness conditions fail (12 days old against a 7-day window, and recorded against v2.9.20 rather than the v2.24.7 under verification), the red verdict is cited to the bug that run already filed, BUG-0167-cold-build-claude-abliterated-failure (now closed, and `docker/claude-abliterated/Dockerfile` line 3 in this RC is a well-formed single-argument `FROM`, so the record describes a state the tree has since left), and this project's `project.cfg` sets `[release] deep_verify = off`, so the lane has deliberately not run since.

Per Step 5d the lane was not run during this verification: no container build was
started and neither `run-cold-build-tests.sh` nor `deep-verify.sh` was called.
This line is a fact reported, not a verdict produced; it is not a `## Findings`
entry and it did not change the recommendation or the systemic-risk rating. The
ledger read cleanly through `deep_verify_ledger_read_latest` in
`scripts/lib/deep_verify_ledger.sh`, so neither of Step 5d's two gap faults
applies, and `team/scripts/deep-verify.sh` is present in the tree that ships the
three Dockerfiles.

## Autonomous criterion

**Autonomous operation criterion: Manual interventions required: none.** All
five Step 6.12 questions answer NO. (Step 6.12 gained its fifth question in this
very RC; it is answered here against the shipped five-question form.)

1. **Tasks force-promoted from WAITING to BACKLOG by hand? — NO.** All seven
   tasks in the cycle (`PM-…-001`, `CODER-…-002`, `CODER-…-003`,
   `WRITER-…-004`, `WRITER-…-005`, `CODER-…-006`, `CM-…-007`) read `DONE`, each
   with a wake-written promotion note in `status.md` and no hand-edit marker.
2. **`release-state.md` edited outside the CM scripts? — NO.** Its four fields
   are exactly what `cm/open-rc.sh` writes, its mtime (`00:48`) matches the
   recorded `## RC Opened At` (`2026-09-20T00:48:45+00:00`) and the
   `CM-20260920-007-open-rc-2-24-7` task, and no later write touched it.
3. **Scripts patched mid-build? — NO.** Every commit in
   `ai_main..ai_rc/v2.24.7` is a ticketed agent commit or its merge, ten in all,
   and `ai_main` received no commit after the RC opened at 00:48. Four of the
   commits touch `team/scripts/` — that is the RC's own deliverable arriving
   through its tickets, not out-of-band tooling patching.
4. **Queue files edited by hand? — NO.** Queue mtimes track wake-cycle activity
   and every marker corresponds to a task state; no orphaned or hand-shaped
   entry was found.
5. **Any agent other than CM invoked a remote git operation (`fetch`, `pull`,
   `push`)? — NO, but the evidence is weaker than the answer looks and the
   report says so rather than banking it.** `find tasks -name
   'git-boundary-guard.jsonl'` returns nothing across every task folder in the
   project, which is the NO the step defines. Two things qualify it. The guard
   ships *in* this RC and was not on any agent's `PATH` while this cycle ran, so
   no record could have been written by any of these tasks whatever they did.
   And Finding 1 means that even once it is live, the `git -C <path> <verb>`
   shape produces no record, so silence will not distinguish a clean cycle from
   a crossed boundary until BUG-0349 is fixed. Recorded as NO with that
   qualification attached, not as a clean pass.

No intervention indicates a defect, so this criterion contributes nothing to the
recommendation. The recommendation rests entirely on Findings 1 and 2.

## Caveats

- **Step 6.8, synthetic release simulation — not applicable, noted rather than
  skipped silently.** The diff touches no release-lifecycle file: not
  `cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`, or
  `team/scripts/lib/project_paths.sh`. The one `team/scripts/` file it does
  change, `lib/wake_driver_invoke.sh`, is agent dispatch rather than the RC
  open/close cycle. `team/tests/integration/test_cm_release_synthetic.py` ran
  anyway as part of the integration suite and passed.
- **Step 6.10, prerequisite dependency graph — clean.** Every task ID referenced
  in a `## Prerequisites` block resolves to an existing task folder, the
  sequence numbers 001–008 are consistent and non-colliding, there are no
  cycles, and the lifecycle bookends link correctly: CM-open at 007 (opened
  00:48, before every feature commit) and this TESTER task at 008 at the end.
- **A PATH shim cannot catch an absolute-path invocation.** `/usr/bin/git fetch`
  bypasses the guard by construction. This is inherent to the mechanism
  PRIORITY-0005's option 1 asked for and is not filed as a defect — an agent
  would have to go out of its way, whereas `git -C` (Finding 1) is what agents
  write by default. Worth stating because the shipped `SOP-core.md` wording
  ("That boundary is enforced, not merely asserted") reads as stronger than a
  PATH shim can be.
- **The guard's evidence-file check precedes its role check.** With
  `PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE` unset the guard fails open for every
  role, before `PGAI_AGENT_ROLE` is even read. This is the documented contract
  and I verified it behaves as written, so it is not a finding; noting it
  because it means the enforcement depends on an environment variable the
  agent's own process can unset. A defence-in-depth variant would refuse first
  and record best-effort.
- **`project.cfg` carries `defer_piggyback = any`.** `SOP-core.md`
  ("Deferral Posture") states this key accepts `true`/`false` only and that
  other spellings "refuse loud with the accepted set". The value predates this
  RC, is outside its spec, and nothing in this verification exercised the
  deferral path, so under Step 6.11's rule it is recorded as an **observation**
  for human consideration rather than a finding, and nothing is filed for it.
- **`team/scripts/wake/git-guard/git` ships no `--help`**, a deviation from
  `docs/coding-standards.md` directive 6. The deviation is explicit, argued in
  the file's own header (a shim-local `--help` branch would stop `git --help`
  reaching the real git), and accepted by the authorising ticket. Recorded as
  acceptable, not as a finding.
- **Prior-run artifacts.** `artifacts/` was empty at the start of this run and no
  `report.md.previous-RUN-N` existed, so this is a fresh start. Nothing from an
  earlier run was read or carried forward; every check above was executed in
  this session.
- **Out of scope by definition**, per `roles/TESTER.md` ("What TESTER does not
  verify"): performance and load testing, security auditing, cross-platform
  compatibility, and the quality of the requirements document itself. The
  requirements doc stated no performance or security criterion, so no exception
  applied.
