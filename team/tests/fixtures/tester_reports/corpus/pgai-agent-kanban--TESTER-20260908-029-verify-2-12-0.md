# Verification Report: ai_rc/v2.12.0

**Task:** TESTER-20260908-029-verify-2-12-0
**RC branch / commit:** `ai_rc/v2.12.0` @ `1eb2669` (worktree detached HEAD verified equal)
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.12.0-ship-policy-and-operator-override.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260908-029-verify-2-12-0`
**Verified:** 2026-09-08

## Summary

Verified the v2.12.0 release candidate — the ship-policy regression/integrity
dimensions, the `SHIP-KNOWN-ISSUE` ship with its two records, the front-of-bundle
carry-forward ordering, the HALT scope law plus its new mechanical lint, the
operator-only `cm/release.sh --force --reason` override, and the four governance
documents brought level with them — against the requirements document's
deliverables and its five acceptance fixtures. Every acceptance fixture was
reproduced independently of the RC's own test suite: the matrix driven directly
over fifteen input combinations, the report-field parser over five synthetic
reports, the bundle-ordering partition over six files (marked, unmarked,
blank-marker, unreadable), the known-issue stamp and marker over a synthetic notes
file and bug pair, and all four `--force` refusals against the real script in a
throwaway kanban root. All behaved exactly as the shipped documents describe. The
**unit gated runner exits 1**: the RC's governance additions push all seven roles'
assembled governance over their configured byte budgets, a threshold that was
green on `ai_main` and is red on the RC. That is a real, RC-introduced defect and
it is what keeps this report off `PASS`; it breaks no runtime path, corrupts no
state, and is repaired by trimming or re-baselining in the next iteration.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Suite Results (gated runners — authoritative)

| Suite | Command | Exit | Verdict |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **1** | FAIL — 1 failed, 3389 passed, 3 skipped (420.8s) |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | PASS — 388 passed, 1 skipped (303.3s) |

The single unit failure is
`tests/unit/test_composer_governance_byte_budget.py::test_every_role_governance_assembly_stays_under_configured_budget`
— Finding 1. No other test in either suite failed; the RC's own five new test
modules (ship-policy acceptance, known-issue carry-forward, operator force
override, HALT-scope end-to-end, and the discovery marker-ordering unit tests)
all pass. Because `UNIT_EXIT != 0`, the suite-pass precondition for a `PASS`
recommendation is not met.

## Code Coverage

- Unit:        line 74% | branch 61%   (14228 stmts, 4350 branches; 10569 lines and 2654 branches covered)
- Integration: line 8%  | branch 6%    (same tree; the integration suite exercises scripts through subprocesses, so its in-process coverage is structurally low and is not a quality signal)

Read from `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json` and `.../integration.json`
(`totals.covered_lines / totals.num_statements` and
`totals.covered_branches / totals.num_branches`; field names confirmed against the
generated files). The `TOTAL` row printed by the runners (71% unit, 7% integration)
is the blended statement-plus-branch figure and is deliberately not reported as a
line number. Coverage is informational only and does not enter the recommendation.

## Findings

### Finding 1: every role's assembled governance is over its configured byte budget

**Category:** bug
**Fix Effort:** medium (3-5 CODER/WRITER tasks, or one operator decision to re-baseline the budgets)
**Systemic Risk:** medium (a real defect that ships and is fixed next iteration — no runtime path fails)
**Regression:** yes (green on `ai_main`, red on this RC; this RC's own governance additions caused it)
**Integrity:** none
**Integrity Damage:** no
**Filed As:** BUG-0199 (Path C-A — filed as BUG — code defect / regression / test failure)

The unit gated runner exits 1 on
`tests/unit/test_composer_governance_byte_budget.py::test_every_role_governance_assembly_stays_under_configured_budget`.
All seven roles are over the `[governance] budget_bytes_<role>` ceilings in the
checked-in `kanban.cfg_example`:

| Role | Assembled | Budget | Over by |
|---|---|---|---|
| po | 58124 | 57344 | +780 |
| pm | 68753 | 65536 | +3217 |
| coder | 63715 | 61440 | +2275 |
| writer | 63465 | 61440 | +2025 |
| tester | 96373 | 92160 | +4213 |
| cm | 78093 | 71680 | +6413 |
| overwatch | 68948 | 66560 | +2388 |

Run directly on both trees, the same lint the test drives shows the transition
cleanly — every role under budget on `ai_main`, every role over on the RC:

```
# ai_main                          # ai_rc/v2.12.0
po        55695  57344  under      po        58124  57344  OVER
pm        65508  65536  under      pm        68753  65536  OVER
coder     61286  61440  under      coder     63715  61440  OVER
writer    61036  61440  under      writer    63465  61440  OVER
tester    92037  92160  under      tester    96373  92160  OVER
cm        71652  71680  under      cm        78093  71680  OVER
overwatch 66519  66560  under      overwatch 68948  66560  OVER
```

Every role was within ~200 bytes of its ceiling on `ai_main`, so the shared
`team/SOP-core.md` growth alone ("Rating a Finding" plus the expanded HALT-scope
law) pushes all seven over; `team/roles/CM.md`, `team/roles/TESTER.md`, and
`team/roles/PM.md` add their own on top for those three roles. The additions
themselves are correct and required by the requirements doc; nothing here says the
text is wrong, only that the budget was not re-balanced against it.

Runtime impact is prompt size only. `lint_governance_byte_budgets.sh` is
explicitly a narrating check (it exits 0 while reporting `OVER`, confirmed by
running it), the wake path never calls `read_order_role_byte_total`, and no agent
refuses to assemble. The only enforcement is this unit test, which is why the
defect surfaces as a red suite rather than a broken chain.

### Finding 2: the new HALT-scope lint misses the `local var="${KANBAN_ROOT}/HALT"` shape

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks — one regex, one test)
**Systemic Risk:** low (a guard with a hole in it; the tree it guards is currently clean)
**Regression:** yes (the defect is in code this RC introduces; no prior behavior regressed)
**Integrity:** none
**Integrity Damage:** no
**Filed As:** BUG-0200 (Path C-A)

`team/scripts/lint_halt_scope_writers.py` is the RC's mechanical guard against
"the next unnamed root-HALT writer" (requirements deliverable 3). Driven against
synthetic fixtures under `--team-dir`, it correctly flags a bare assignment
(`halt_flag="${KANBAN_ROOT}/HALT"` then `touch "$halt_flag"`), a direct literal
`touch "${KANBAN_ROOT}/HALT"`, a `: > "$halt_flag"` redirect, and the Python
`.touch()` shape — and correctly passes a `global`-named scope, an allowlisted
site, and a project-scoped writer.

It does **not** flag the single-line declaration-and-assignment form:

```bash
repair_one_project() {
    local halt_flag="${KANBAN_ROOT}/HALT"
    touch "$halt_flag"          # lint reports OK — no violation
}
```

Cause: `_BASH_ROOT_HALT_ASSIGN_RE` is anchored at
`^\s*([A-Za-z_][A-Za-z0-9_]*)=`, so any `local` / `declare` / `typeset` /
`export` / `readonly` prefix breaks the variable-tracking match and the later
`touch "$halt_flag"` is never associated with a root-scoped path. `local x="..."`
is a common shape in this codebase (`release_ship_policy.sh` uses it throughout),
so the hole is reachable by ordinary authoring, not only by contrivance. The
lint's own unit file carries 16 tests and none covers this form.

The current tree is clean either way — the lint reports OK on the RC, and the one
site the RC moved (`overwatch_halt_first_fix`) is genuinely project-scoped now.

### Finding 3: the operator HALT playbook still names the fleet-wide root HALT for CM triggers

**Category:** gap
**Fix Effort:** small (1 WRITER task)
**Systemic Risk:** low (an operator-facing documentation defect; costs recovery time, not correctness)
**Regression:** no (CM's release halts were already project-scoped on `ai_main`; the doc drift predates this RC)
**Integrity:** none
**Integrity Damage:** no
**Filed As:** BUG-0201 (Path C-A)

The requirements doc's third deliverable is "Matrix HALTs are PROJECT-scoped
(`projects/<name>/HALT`) ... every HALT writer audited for scope." `team/SOP-core.md`
and `team/roles/CM.md` both now state that law, and the code has honored it for
CM's release halts since before this RC (`PROJECT_HALT_FILE="${_CM_PROJECT_ROOT}/HALT"`).
`docs/OPERATIONS.md` — the operator-facing playbook, and a file this RC edited, in
the very section it edited — still says the opposite:

- "CM creates `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT` for any of the following eight
  conditions" (the shipped trigger list is nine, and CM writes the project file).
- The "Operator response procedure" tells the operator to
  `cat "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"` and then
  `rm "$PGAI_AGENT_KANBAN_ROOT_PATH/HALT"` to resume.
- The "HALT-file lifecycle" table names `$PGAI_AGENT_KANBAN_ROOT_PATH/HALT` as
  the file CM creates and the operator removes.

An operator following it after an autonomous CM halt finds no file to read and
removes a file that is not there, while the project's own `projects/<name>/HALT`
keeps that lane parked. The new "Overriding a ship-policy stop" subsection
inserted between those passages is correct and scope-aware, which makes the
surrounding stale text more misleading, not less.

## Verification Detail (what was run)

### Pre-flight

- Worktree HEAD `1eb2669` equals `ai_rc/v2.12.0`; `git status --short` empty;
  `git stash list` empty.
- Merge verification over every DONE task with a live feature branch: no
  UNMERGED and no zero-commits-ahead (BUG-92-pattern) anomalies. **pass**
- `release-state.md`: `Active RC v2.12.0`, opened `2026-09-08T09:45:06+00:00` by
  `CM-20260908-028-open-rc-2-12-0`, `Last Released v2.11.1` (well-formed).
  `pp_last_released_version` resolves `ai_v2.11.1` from tags; `v2.12.0` is
  strictly greater. **pass**

### Deliverable 1 — ship-policy matrix with regression + fix-effort dimensions

`cm_ship_policy` driven directly over fifteen input combinations
(`team/scripts/lib/cm/release_ship_policy.sh` sourced in isolation):

| state / risk / recommendation / large / found / regression / integrity | result |
|---|---|
| DONE high SHIP-WITH-CONCERNS no yes **no none** | `SHIP-KNOWN-ISSUE` |
| DONE high ... **yes** none | `HALT` |
| DONE high ... no **breakage** | `HALT` |
| DONE high ... no **destructive** | `HALT` |
| DONE high ... **large** no none | `HALT` |
| DONE high ... **unknown unknown** | `HALT` |
| BLOCKED (any) | `HALT` |
| DONE low PASS | `SHIP-FUNCTIONAL` |
| DONE medium SHIP-WITH-CONCERNS | `SHIP-KNOWN-BUGS` |
| DONE low SHIP-WITH-SERIOUS-CONCERNS, no large | `SHIP-NON-FUNCTIONAL` |
| DONE low SHIP-WITH-SERIOUS-CONCERNS, large | `HALT` |
| WONT-DO (task found) | `HALT` |
| no TESTER task at all | `SHIP-FUNCTIONAL` |
| `done HIGH pass ... NO NONE` (mixed case) | `SHIP-KNOWN-ISSUE` |

Every row matches `team/roles/CM.md`'s rewritten twelve-row table, including
case-insensitivity and the fail-loud unrecognized-state row. **pass**

`read_ship_policy_finding_dimensions` over five synthetic reports: only findings
whose *own* Systemic Risk is `high` are consulted (a low finding's
`Regression: yes` did not leak); `yes`/`breakage` win across findings; prose after
a finding block (a `## Caveats` section containing the literal text
`Regression: yes`) is not misattributed; absent fields and a missing file both
yield `unknown unknown`, which the matrix refuses to clear. **pass**

### Deliverable 2 — severity rubric in SOP-core

`team/SOP-core.md` gains "Rating a Finding": the four severity values in plain
language, the two high-risk-only dimensions, both worked examples the
requirements doc asks for (pre-existing never-run artefact = `medium`; live A/B
regression = `high` and the release stops), and the explicit "a rating is never
softened to reach a preferred ship outcome" rule. `team/roles/TESTER.md` and
`team/roles/CM.md` both defer to it rather than restating it. **pass**

### Deliverable 3 — project-scoped matrix HALTs, every writer audited

- `overwatch_halt_first_fix` now touches `$(pp_project_root "$OVERWATCH_PROJECT")/HALT`
  instead of `$KANBAN_ROOT/HALT`, with the scope reasoning stated at the site. **pass**
- `team/scripts/lint_halt_scope_writers.py` runs clean on the tree (exit 0) and is
  wired into `run-unit-tests.sh` as a CONVERT-class quality lint. **pass**, with
  the false-negative hole recorded as Finding 2.
- `team/SOP-core.md` now enumerates the three fleet-scoped writers and states that
  CM's and OVERWATCH's autonomous halts are project-scoped. **pass**
- `docs/OPERATIONS.md` was not brought level — Finding 3.

### Deliverable 4 — `cm/release.sh --force --reason`

All four refusals exercised against the real script with
`PGAI_AGENT_KANBAN_ROOT_PATH` pointed at a throwaway temp root:

| Invocation | Result |
|---|---|
| `--force` (no reason) | exit 1, `error: cm-release.operator-force.reason-required`, dual-channel |
| `--force --reason "   "` | exit 1, same key (whitespace-only refused) |
| `--force --reason "..."` with `PGAI_AGENT_WAKE_ACTIVE=1` | exit 1, `error: cm-release.operator-force.agent-wake-refused` |
| `--force --reason` (flag with no value) | exit 1, `ERROR: --reason requires a value` + usage |

The throwaway root was still empty afterwards — no HALT file, no release-state
write, nothing. `--help` mentions `force` zero times, which is the documented
deliberate omission. The two new refusal sites carry `BLOCK-WORTHY` class comments
and `CENSUS-ANCHOR` markers, and `docs/blocking-site-census.md`'s Locator Index
carries their entries; `lint_gate_census_compliance.py` reports no new anchor
faults. The override path itself (`_cm_apply_matrix_trigger`) passes exactly
triggers 1/2/3 and calls `cm_halt` unchanged for every other row, matching
`docs/operator-commands.md`, `docs/OPERATIONS.md`, and `team/roles/CM.md`. The
`## Operator Override` block's field set (Timestamp / Version / Stop Overridden /
Reason) matches `docs/operator-commands.md` byte-for-byte. **pass**

### Deliverable 5 — TESTER aggregate lint filing rule

`team/roles/TESTER.md` Step 10 now carries "One lint class is one related thing"
(one bug per class per run, every instance listed, severity = worst instance), Step
5c points at it, and `docs/tester-verification-reference.md` carries the worked
example and the cross-suite case. This report's own lint disposition below follows
it. **pass**

### Acceptance fixture — pre-existing-high-small ships KNOWN-ISSUE with a front marker

- `cm_stamp_release_notes_status` with `KNOWN-ISSUE` plus a block writes the status
  and the `## Known Issues` body in one write; where the notes carry no
  `## Known Issues` heading it appends one rather than dropping the record;
  a re-stamp with the placeholder already gone exits 1 (the caller's grep guard
  is what prevents that path). **pass**
- `build_known_issue_notes_block` renders the bug's first Symptom sentence, not
  the ticket ID: `- The generator mishandles its --dry-run flag. (internal-only,
  not yet disclosed) — No workaround available.` **pass**
- `cm_stamp_known_issue_marker` stamps `## Known Issue Carry Forward: v2.12.0`
  onto an unmarked bug, leaves an already-valued marker (`v2.9.9`) untouched, and
  on a second run narrates "already complete, continuing". **pass**
- `_disc_order_bundle_items` stably partitions: marked items lead in arrival
  order, unmarked follow in arrival order, a blank marker is treated as unmarked
  and left in place, and an unreadable path is kept in place rather than dropped.
  Verified over six files including a missing path. **pass**
- `team/roles/PM.md` states the ordering obligation as binding, with the two
  things that outrank it (genuine dependency inside the marked work, and the
  materializer's bookends). **pass**

### Step 6.8 — synthetic release simulation

The RC touches release-lifecycle files, so the trigger condition is met.
`tests/integration/test_cm_release_synthetic.py` — 15 tests, all passing inside
the integration run.

### Step 6.9 / 6.14 — constraint and coding-standards spot-check

`bash -n` clean on every changed shell file; `py_compile` clean on the new lint;
`lint_engine_size_dupe.py` reports all 33 engine files within the 800-line ceiling
with no cross-file name collisions (the reason `release_known_issue.sh` and
`release_operator_override.sh` were split out at all). The new lint ships a
`--help` naming every argument and its exit codes. No hardcoded secrets, no new
hardcoded operational values, no duplicated `lib/` implementation.

### Step 6.13 — skip-cites-real-bug

`lint_skip_bug_gate.sh` exits 0; no placeholder skip IDs; no cited `BUG-NNNN`
without a file in `bugs/`. **pass**

## Lint Findings (non-blocking)

- `comment_provenance` — 1180 instances, every one under `team/tests/` (the RC's
  four new test modules add ~156 of them, all synthetic fixture bug/task IDs) —
  cites **BUG-0191** (open) `test-tree-provenance-backlog`, which describes this
  exact class and count trajectory. No duplicate filed.
- `gate_census_compliance` — 2 instances, `team/scripts/lib/wake_common.sh:130`
  and `team/scripts/run-unit-tests.sh:604`, both "missing census classification
  and dual-channel contract". Reproduced identically on `ai_main` (at line 591
  there — the RC's +13 lines in `run-unit-tests.sh` moved the site, nothing else
  changed) — cites **BUG-0196** (open) `census-locator-index-lines-stale`, which
  names both sites verbatim. No duplicate filed.

Both findings files were present and were read
(`findings-unit.json`: 3 findings across 2 classes; `findings-integration.json`:
1 finding, the same `comment_provenance` class). Grouping is by `lint` value
across both files per the aggregate rule, so the two classes above are the whole
disposition — every one cites an open bug, none is duplicated, and none moves the
recommendation or the systemic risk.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=1d freshness=stale — the claude-abliterated flavor failed its Dockerfile FROM parse and podman/rocky9 failed a COPY --chmod step; stale because the record names v2.9.20 rather than the v2.12.0 under verification, and the failure it records is cited by BUG-0167 whose `## Fixed In` reads v2.9.22, so the lane has not run against any tree carrying that fix and nothing is known about whether this RC's images build.

## Autonomous criterion

| # | Check | Answer |
|---|---|---|
| 1 | Any task force-promoted WAITING to BACKLOG by hand? | NO |
| 2 | `release-state.md` edited outside the CM scripts? | NO — written once at 09:45:06 by `cm-open-rc.sh` and untouched since |
| 3 | Any script patched mid-build? | NO — no file under the live install's `scripts/`, `roles/`, or `pgai_agent_kanban/` has an mtime after the RC opened |
| 4 | Any queue file manually edited? | NO — queue mtimes track wake activity only |

`Autonomous operation criterion: Manual interventions required: none.` All nine
prerequisite tasks plus `CM-20260908-028-open-rc-2-12-0` are DONE with
`Needs Human: no`. `WRITER-20260908-024` records one instruction conflict in its
own `## Instruction Conflicts` (its README asked for a byte-identical kanban root,
which the mandatory status/log writes make impossible); it was resolved per
Instruction Precedence and documented — a recorded resolution, not an intervention.

## Caveats

- **The report-field contract changed spelling in this RC.** The shipped
  `team/roles/TESTER.md` now asks for `**Integrity:** breakage|destructive|none`,
  while the currently *installed* role file at the kanban root still asks for
  `**Integrity Damage:** yes|no`. Confirmed by driving the parser: a report
  written in the old spelling yields `integrity=unknown`, which on a
  `systemic_risk=high` report refuses the release. That is the documented
  fail-safe ("a report that does not say is read as saying the worst") and not a
  defect — but the first report written under the new install must use the new
  spelling. This report carries **both** lines on every finding so either parser
  reads it correctly.
- The installed kanban root still carries a pre-v2.10 long-form `SOP.md` that the
  dev tree no longer ships (the tree ships `team/SOP-core.md`; `install.sh` copies
  only `SOP-core.md`). Its `--force` passage names an environment variable
  (`PGAI_AGENT_WAKE_CONTEXT`) that exists nowhere in the shipped tree, which uses
  `PGAI_AGENT_WAKE_ACTIVE`, and its `## Operator Override` block shape does not
  match the shipped one. This is stale install-time litter in the operator's
  environment, not something this RC ships, so it is recorded here rather than
  filed against the tree.
- `_disc_order_bundle_items` treats a marker section whose only body is an HTML
  comment as *marked*, while `cm_stamp_known_issue_marker` treats the same shape
  as *unset*. No bug template in the tree ships that heading, so the asymmetry is
  currently unreachable; noted as an observation, not a finding.
- `cm_stamp_release_notes_status` leaves one extra blank line above the injected
  `## Known Issues` bullet. Cosmetic only.

## Path C Filings (autonomous follow-up filing — non-blocking)

| Finding | Path | File | Severity |
|---|---|---|---|
| 1 — governance byte budgets over for all seven roles | C-A (filed as BUG — code defect / regression / test failure) | `projects/pgai-agent-kanban/bugs/BUG-0199-governance-budgets-over-every-role.md` | high |
| 2 — HALT-scope lint misses the `local` assignment shape | C-A | `projects/pgai-agent-kanban/bugs/BUG-0200-halt-scope-lint-misses-local-assign.md` | medium |
| 3 — operator HALT playbook names the fleet-wide root file | C-A | `projects/pgai-agent-kanban/bugs/BUG-0201-operations-halt-playbook-root-scoped.md` | medium |

Gap detail (criterion / expected / actual / severity) is in
`artifacts/gaps.md`. Two quality-class lint classes cite existing open bugs
(BUG-0191, BUG-0196) rather than being re-filed, per the cite-an-open-bug-or-file-one
rule; neither moves the recommendation or the systemic risk.
