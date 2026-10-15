# Verification Report: ai_rc/v2.18.0

**Task:** TESTER-20260911-030-verify-2-18-0
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.18.0-deep-verify-tier.md`
**RC branch / commit:** `ai_rc/v2.18.0` @ `7423c4c`
**Base / last released:** `ai_main`, `ai_v2.17.0` (`pp_last_released_version` -> `v2.17.0`)
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260911-030-verify-2-18-0` — detached HEAD at the RC commit, clean, no stashes
**Verified:** 2026-09-11

## Summary

v2.18.0 is a deliberately thin conformance release against the `v2.18.0-deep-verify-tier`
brief. Four of the brief's five deliverables already shipped at v2.17.0 and earlier —
`team/scripts/deep-verify.sh`, `deep-verify-freshness-check.sh`, `cold-build-now.sh`,
`team/pgai_agent_kanban/deep_verify/`, the `testing-only` workflow plugin, TESTER Step 5d's
no-container RC rule, `pp_deep_verify_mode` / `pp_deep_verify_offhours_window`, and the
promotion-playbook step — and PM declined to manufacture re-implementation tickets for
shipped work. The RC therefore contains exactly three work commits across five files:
`create-project.sh` now scaffolds a `[release]` section naming both DEEP-VERIFY keys
(commented out), `project.cfg_example` gains the off-hours key, `REPORT-TEMPLATE.md` gains
the mandatory `## Deep-Verify Freshness` section, and a new unit test locks that template
and `team/roles/TESTER.md` into agreement on the section and its verdict vocabulary.

Both gated runners exited 0 (unit: 3828 passed / 3 skipped in 537 s; integration:
445 passed / 1 skipped in 402 s). Verification went beyond structural checks: the
project-creation script was executed against a throwaway kanban root and its generated
`project.cfg` inspected and fed back through both resolvers; the new regression test was
mutation-tested with three independent mutations and produced three distinct failures; the
pre-release freshness gate was driven to a real refusal; and the scheduled-dispatch path
was driven to a real refusal on a bad config value. No container engine was started at any
point (Step 5d).

One defect was found and it was introduced by this RC. The `[release]` comment block that
`create-project.sh` now writes into every new `project.cfg` states that `true|false` are
accepted aliases for `deep_verify`, and names `pp_deep_verify_mode()` as the reader — but
that function refuses both values, and `deep-verify.sh --scheduled` turns the refusal into
`deep_verify.invalid_schedule_mode` and runs no lane for the project. An operator who
follows the freshly scaffolded guidance switches their weekly DEEP-VERIFY lane off. Filed
as BUG-0243 (Path C-A — filed as BUG: code defect / regression / test failure). Everything
else in the brief verified clean.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

medium

## Test Suites (authoritative gated runners)

| Suite | Command | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3828 passed, 3 skipped (537.04 s) |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 445 passed, 1 skipped (402.28 s) |

`UNIT_EXIT == 0` and `INTEGRATION_EXIT == 0`. Both runners completed their full lint
pre-flight (breakage class, Dockerfile static, anti-pattern, skip-cites-real-bug, orphan
test, API parity, ICD freshness/compat, CHANGELOG freshness, engine size/dupe, env
bootstrap, help presence, hygiene, import completeness, dashboard network-git, HALT scope,
raw marker, dual-channel pair, census compliance, governance byte budget) before pytest.
The suite-pass precondition is met; the recommendation is elevated off `PASS` solely by the
Step-7 `bug` finding below.

## Code Coverage

- Unit:        line 75%  |  branch 62%   (14928 stmts, 4496 branches; blended `TOTAL` 72%)
- Integration: line 8%   |  branch 6%

Source: `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/{unit,integration}.json` `totals`
(`percent_statements_covered_display`, `percent_branches_covered_display`). Informational
only — coverage does not enter the PASS precondition and is never a finding.

## Findings

### Finding 1 — `deep_verify` true/false aliases are refused by the resolver the newly scaffolded comment names

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** medium (a real defect an operator can hit; it steers them into a refusal and switches their weekly lane off, but chain state and data are untouched and the fix is ordinary next-iteration work)
**Regression:** yes (this RC's change to `create-project.sh` is what produced the defect — before v2.18.0 the generated `project.cfg` carried no `[release]` section and made no claim at all; the underlying reader-vocabulary split predates the RC)
**Integrity Damage:** no
**Filed As:** BUG-0243 (Path C-A — filed as BUG: code defect / regression / test failure)

`team/scripts/create-project.sh` now writes this into every generated `project.cfg`:

```
# deep_verify: this project's DEEP-VERIFY scheduled-dispatch cadence —
# read by pp_deep_verify_mode() (team/scripts/lib/project_paths.sh).
# Accepted: weekly|off (true|false also accepted as aliases: true=weekly,
# false=off). Default: weekly
```

`pp_deep_verify_mode` accepts exactly `weekly` and `off` and fails loud on anything else.
Reproduced end to end against a throwaway kanban root:

1. `create-project.sh --project probe --workflow-type release` -> generated `[release]`
   block carries the alias claim above.
2. Set `deep_verify = true` (following the generated comment).
3. `pp_deep_verify_mode` -> `rc=1`, empty stdout, stderr
   `unrecognized value 'true' ... (expected 'weekly' or 'off'; blank/absent defaults to 'weekly')`.
4. `deep-verify.sh --scheduled` -> `error: deep_verify.invalid_schedule_mode`, exit 1, no
   lane run for that project. The refusal's own remedy line contradicts the comment the
   project was scaffolded with.
5. `deep_verify = false` behaves identically.

Two shipped readers of the same key disagree: `team/scripts/lib/config_loader.sh` accepts
`weekly|true` and `off|false` (documented there as a deliberate exception), while
`pp_deep_verify_mode` — the one the scheduled path actually consults — accepts only
`weekly|off`. `team/SOP.md` ("The per-project switch") names `weekly`/`off` and no aliases.
`project.cfg_example` and `config_loader.sh`'s refusal remedy carry the same alias claim
and predate this RC; what v2.18.0 changed is that the claim is now written into every new
project's own config next to a citation of the function that refuses it.

Blast radius is bounded: `deep-verify.sh --scheduled`'s fan-out loop deliberately does not
abort on one project's failure, so a misconfigured project does not hide another project's
result. The new integration test `TestCreateProjectDeepVerifyReleaseSection` asserts the
generated comment names `weekly|off` and `Default: weekly` but asserts nothing about the
alias sentence, which is why the mismatch shipped green.

### Finding 2 — Deliverable: cold-build out of per-RC verification entirely

**Category:** pass

`team/roles/TESTER.md` Step 5d in the RC tree states the rule directly ("TESTER runs no
container build, starts no container engine, and calls neither `run-cold-build-tests.sh`
nor `deep-verify.sh`"). `tests/unit/test_runner_pytest_scope_excludes_cold_build.py`
(8 tests) is green in this run, and `lint_orphan_tests` reports `tests/cold` as a collected
root kept outside the two gated runners' pytest scope. This verification run started no
container engine; the only Docker-adjacent work either runner performed was
`lint_dockerfile_static` (3 Dockerfiles, 0 violations), which is a text scan.

### Finding 3 — Deliverable: `deep-verify.sh --project <name>` on-demand umbrella verb

**Category:** pass-with-caveat

The verb ships and is executable, with `cold-build-now.sh` as its named alias and
`team/pgai_agent_kanban/deep_verify/{classify,ledger,freshness,schedule,bug_filer}.py`
behind it. The brief's outcomes are covered by green tests in this run:
`test_deep_verify_rc_isolation.py` (never touches an open release RC),
`test_deep_verify_bug_reaches_discovery_bundle.py` (findings file bugs that reach the
loop), `test_deep_verify_run.py`, `test_deep_verify_installed_shape.py`.

*Caveat:* the brief specifies the verb "runs as a testing-only workflow RC (git ro,
finalize=report)". The shipped implementation is a standalone script, not a workflow RC;
the `testing-only` plugin does exist separately at `team/workflows/testing-only/` with
exactly `git_mode = ro` and `finalize = report`. The brief's stated *outcomes* (bugs filed
for the loop, open release RCs untouched) are met by the implementation actually shipped,
and `team/SOP.md`'s DEEP-VERIFY section documents that shape as canonical, so this is
recorded as a mechanism deviation rather than a gap.

### Finding 4 — Deliverable: `deep_verify` + off-hours keys in `project.cfg`, scaffolded by `create-project.sh`

**Category:** pass-with-caveat

Behaviorally verified against a throwaway kanban root (never the live install): the
generated `project.cfg` contains a real `[release]` INI header, both keys commented out,
and both resolvers return the shipped defaults for the generated project
(`pp_deep_verify_mode` -> `weekly`, `pp_deep_verify_offhours_window` -> `03:00-03:59`) —
so the scaffolding changed no resolution behavior for new or existing projects.
`project.cfg_example` gains the matching off-hours block. Guard behavior exercised
directly: an explicit `off` resolves to `off`; a malformed window (`banana`) passes through
the bash reader unvalidated by design and is caught by
`pgai_agent_kanban.deep_verify.schedule.resolve_offhours_window`, which falls back to
`03:00-03:59` and returns an explanatory message, exactly as both the SOP and the generated
comment promise; an unrecognized mode value fails loud rather than being guessed.

*Caveat 1:* the accepted-value wording on the `deep_verify` key is wrong — Finding 1.

*Caveat 2:* the brief specifies "`deep_verify=true` in project.cfg enables the weekly
scheduled run … default false". The shipped tier is opt-**out**: values are `weekly|off`,
default `weekly`. PM recorded this deviation deliberately, on the ground that `team/SOP.md`
("The per-project switch") argues the opt-out default explicitly and outranks a
requirements doc under Instruction Precedence. Verification concurs with that reading and
records it rather than filing it as a gap — but the shipped default is the opposite
polarity from the brief's literal text, and an operator reading only the brief would expect
new projects to be silent by default when in fact they are scheduled by default.

### Finding 5 — Deliverable: pre-release law refuses without a fresh green deep-verify result

**Category:** pass

`team/scripts/deep-verify-freshness-check.sh` is a named step in
`docs/promotion-playbook.md`. The refusal was triggered, not inferred: run for
`--project pgai-agent-kanban --version v2.18.0` against the live ledger it exits **1** with
a complete dual-channel contract — `error: freshness_gate.failed_to_build`, a plain-language
message naming the project, the record timestamp and the failing pairs, and a runnable
remedy (`team/scripts/deep-verify.sh --project pgai-agent-kanban`). The age threshold is
configurable via `PGAI_DEEP_VERIFY_FRESHNESS_WINDOW_DAYS` (default 7). A malformed
`--version` is refused loud (`'2.18' is not well-formed semver (expected vX.Y.Z)`) rather
than guessed. `tests/integration/test_deep_verify_freshness_check.py` (14 tests) is green.
The check wrote nothing and started no lane.

### Finding 6 — Deliverable: freshness on the record in every TESTER report

**Category:** pass

`team/templates/agent/REPORT-TEMPLATE.md` now carries a `## Deep-Verify Freshness` section
placed immediately before `## Recommendation`, stating that the line is a fact and never a
verdict, that it is mandatory for any tree shipping Dockerfiles including never-run and
last-result-red cases, that a tree shipping no Dockerfiles omits the section, and that the
verdict word is the ledger's own (`built` / `failed-to-build` / `could-not-attempt`, never
PASS/FAIL). It defers to `team/roles/TESTER.md` Step 5d for form selection rather than
duplicating it.

The new regression test `team/tests/unit/test_report_template_freshness_agreement.py`
(5 tests) asserts against the shipped documents, not a fixture copy. It was
**mutation-tested** in a scratch copy of `team/`, three independent mutations, three
distinct failures:

| Mutation | Test that failed |
|---|---|
| Renamed the template heading to `## Deep Verify Freshness` | `test_report_template_carries_the_deep_verify_freshness_heading` |
| Respelled `` `failed-to-build` `` as `` `failed_to_build` `` in the template | `test_verdict_word_is_spelled_identically_in_both_documents[failed-to-build]` |
| Renamed the heading in TESTER.md's Step 9 report-fields list | `test_tester_role_names_the_same_heading_in_its_report_fields_list` |

The guard is real, not a tautology. This report itself carries the mandatory section below.

### Finding 7 — Acceptance: weekly fixture fires in-window

**Category:** pass

`tests/unit/test_deep_verify_schedule.py` and
`tests/integration/test_deep_verify_scheduled_dispatch.py` (7 tests) are green in this run,
covering the self-gate (ledger at least seven days old AND inside the project's off-hours
window). Confirmed directly that the live project's `deep_verify = off` now resolves
correctly through `pp_deep_verify_mode` (prior BUG-0171, which had the key in the wrong INI
section, is repaired), and that `deep-verify.sh --scheduled` fans out without aborting on a
single project's failure.

### Finding 8 — Release-state drift check (Step 6.1)

**Category:** pass

`pp_last_released_version` (git tags, `ai_`-prefixed) returns `v2.17.0`;
`semver_gt v2.18.0 v2.17.0` holds. `release-state.md` reads `Active RC: v2.18.0`,
`RC Opened By Task: CM-20260911-029-open-rc-2-18-0`, `Last Released: v2.17.0` — a
well-formed `vX.Y.Z` value. The `ai_rc/v2.18.0` branch exists and the worktree HEAD equals
it. No drift.

### Finding 9 — Constraint and hygiene negative checks (Steps 6.9, 6.13, 6.14)

**Category:** pass

`create-project.sh` retains `set -euo pipefail` and passes `bash -n`. No credential-shaped
literals anywhere in the RC diff. The skip-cites-real-bug grep-gate is clean: no
placeholder skip IDs in `team/tests/` (the only `BUG-NNNN` string hits are inside archived
coverage run logs under `team/tests/artifacts/`), and every cited bug ID resolves to a real
file in `bugs/`; the runners' own `lint_skip_bug_gate` agrees. Coding-standards spot-check
of the diff sites: no new operational value hardcoded, no duplicated `lib/` implementation,
no new script requiring `--help`. The one version reference in the new comment header
(`[release] — DEEP-VERIFY scheduled-dispatch settings (v2.18.0+)`) matches the existing
convention in the shipped `project.cfg` (`(v2.0.0+)` on the retry-ceiling block) and is not
flagged by the authoritative `lint_hygiene` run.

### Finding 10 — Prerequisite dependency graph (Step 6.10)

**Category:** pass

Six tasks, acyclic, every referenced task ID exists as a task folder, bookends correct
(`CM-…-029-open-rc` first with no prerequisites; `TESTER-…-030` then `CM-…-032-release`
last), sequence numbers unique and non-colliding. All three prerequisite tasks are `DONE`
with `Needs Human: no`.

## Pre-flight

- **Merge verification:** passed. No DONE task in this project retains a live feature
  branch — CODER/WRITER delete feature branches after merge, which the procedure treats as
  equivalent to a verified merge. Local branches present: `ai_main`, `ai_rc/v2.18.0`,
  `last_run_ai_main`, `main`. No UNMERGED and no BUG-92 (zero-commits-ahead) anomalies.
- **Working tree:** `git rev-parse HEAD` equals `git rev-parse ai_rc/v2.18.0`
  (`7423c4c…`); `git status --short` empty.
- **Stashes:** `git stash list` empty.

## Lint Findings (non-blocking)

- `hygiene` — `release-notes/v2.16.0.md:42`: `[task-ID]` provenance reference
  `TESTER-20260910-070` in a shipped operator-facing document; comments and output must
  describe behavior, not internal IDs — cites **BUG-0242** (open, names this exact file and
  line) and **BUG-0236** (open, the aggregate hygiene-provenance bug). No duplicate filed.

One lint class, one instance, identical in both runners' findings files
(`${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` generated 2026-09-11T10:22:26Z;
`findings-integration.json` generated 2026-09-11T10:29:30Z). Both are already tracked by
open bugs, so nothing was filed and nothing was silently ignored. Quality-class findings do
not move `## Recommendation` or `## Systemic Risk`.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=4d freshness=stale — the last lane run was red on the claude-abliterated flavor (both engines) and the podman/rocky9 COPY step, and it cites BUG-0167, which that run filed and which has since been closed; the record is stale on the version count alone (v2.9.20 is not the v2.18.0 under verification, while 4d is inside the 7-day window), so no lane has ever been run against this tree and nothing is currently known about whether its three images build.

## Autonomous Criterion (Step 6.12)

Manual interventions required: **none**.

1. Tasks force-promoted from WAITING to BACKLOG by hand? **NO** — all three prerequisite
   tasks reached DONE through the wake script; `Needs Human: no` throughout.
2. `release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` / `cm-cancel-rc.sh`?
   **NO** — the file holds the canonical four fields, `RC Opened By Task` names
   `CM-20260911-029-open-rc-2-18-0`, and `RC Opened At` (09:45:15Z) precedes the first RC
   work commit (09:50:21Z).
3. Scripts patched mid-build? **NO** — the RC contains six commits (three work, three
   merges) between 09:50:21Z and 10:09:50Z, all authored through the chain, all attributable
   to one of the three decomposed tickets. No out-of-band commit touches `team/scripts/`,
   `subagents/`, or other tooling.
4. Queue files manually edited? **NO** — `tester_backlog.md` shows the normal marker
   progression, this task carrying `[A]`.

## Caveats

- **The brief was largely already delivered.** Four of five deliverables shipped at or
  before v2.17.0; PM recorded this explicitly and scoped the RC to the two residual
  conformance gaps rather than manufacturing re-implementation tickets. Verification agrees
  with that scoping: the pre-existing deliverables were re-verified here against the RC tree
  (Findings 2, 3, 5, 7) rather than assumed. The operator-facing consequence PM raised
  stands — a brief that repeats already-delivered work will keep producing conformance-only
  releases like this one.
- **The brief's `default false` was not implemented, deliberately.** See Finding 4,
  Caveat 2. Recorded, not filed.
- **The installed kanban root is far behind the dev tree.**
  `$HOME/pgai_agent_kanban/VERSION` reads `v2.9.19` while the dev tree is at
  `v2.17.0`, so `roles/TESTER.md` in the live install (1508 lines) is a much older document
  than `team/roles/TESTER.md` in the RC (394 lines, restructured into `## Step N` headings).
  This verification followed the installed role file as instructed and cross-read the RC's
  own copy where the two could differ; the two agree on every rule applied here. This is
  operator install state, not an RC defect, and is recorded only so a reader is not
  surprised by the divergence.
- **The ledger's `detail` field still repeats each failing pair six times.** That is the
  2026-09-07 record, written before BUG-0168 was closed; it is historical data, not a live
  defect, and was not re-filed.
- **`deep-verify-freshness-check.sh --help` renders `--version` with no description or
  value placeholder** (the flag is outside the canonical value-flag set the shared help
  renderer knows). The file is untouched by this RC, `lint_help_presence` passes it, and it
  is outside the Step 6.14 spot-check scope (new or heavily modified code only). Recorded as
  an observation, not a finding.

## Artifacts

- `artifacts/report.md` — this report
- `artifacts/gaps.md` — the single gap, with severity
- `bugs/BUG-0243-deep-verify-alias-vocabulary-split.md` — Path C-A filing for Finding 1
- Captured runner output: `${PGAI_AGENT_KANBAN_TEMP_DIR}/tester/{unit,integration}_output.txt`
