# Verification Report: ai_rc/v2.10.4

**Task:** TESTER-20260907-075-verify-2-10-4
**RC branch:** `ai_rc/v2.10.4` @ `c59b2a4c58848bdf236cb7a27d02e2a0f8570d50`
**Requirements doc:** `projects/pgai-agent-kanban/requirements/v2.10.4-bugfix-bundle-20260907.md`
**Worktree:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260907-075-verify-2-10-4` (detached HEAD, matches `ai_rc/v2.10.4`, clean, no stashes)
**Verified:** 2026-09-08. Fresh run — `artifacts/` was empty at start; no prior-run report or `.previous-RUN-N` file existed, and none was consulted.

## Summary

v2.10.4 is a discovery-bundled BUG release carrying six substantive items
(BUG-0178, 0181, 0183, 0184, 0185, 0187) plus nine hygiene ride-alongs
(BUG-0169, 0170, 0172, 0173, 0176, 0179, 0180, 0182, 0186), delivered by seven
CODER tickets and one WRITER ticket. Every item was verified behaviorally
rather than structurally: the Step 13c helper path was resolved under both the
dev-tree and a simulated installed layout and then driven end to end through
`helloworld-flow-check.sh`; `promote_bundled_items.py` was exercised on a
synthetic `running` bundle and confirmed to promote both the bundle and its
item to `done`; the `get_config` precedence fix was reproduced against the
exact env-override case the bug records; docs/ delivery parity was proved by a
real install (pre-fix tree) → strip `docs/` → upgrade (fixed tree) round trip
into a throwaway root, with all ten manifest paths landing; the new
`rc_empty_commits` lint was pointed at this RC's own range; and the widened
provenance scan was exercised with a synthetic violation planted at the
repository root.

Both gated runners exited 0. Every bundled item's acceptance criteria are met.
Three low-severity defects were found outside those criteria — two introduced
by this RC, one a documentation/implementation mismatch in this RC's own new
docstring — and all three are filed via Path C (autonomous follow-up filing —
non-blocking). Two quality-class lint classes are recorded in the non-blocking
Lint Findings section, each with its own Path C filing.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Suite Results

| Suite | Gated runner (authoritative) | Exit | Result |
|---|---|---|---|
| Unit | `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 3255 passed, 3 skipped in 398.87s |
| Integration | `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 339 passed, 1 skipped in 225.04s |

PASS evidence is the gated-runner exit codes above, not a direct-pytest line.
Both runners ran their full lint pre-flight; the breakage-class gate passed in
both and every quality-class lint that tripped is recorded under Lint Findings.

## Code Coverage

- Unit: line 74% | branch 61% (14138 stmts, 4318 branches; blended `percent_covered` 71%)
- Integration: line 8% | branch 6% (blended 7%)

Source: the `totals` objects in `${PGAI_AGENT_KANBAN_TEMP_DIR}/coverage/unit.json`
and `.../integration.json`; field names (`percent_statements_covered_display`,
`percent_branches_covered_display`, `percent_covered_display`) were read from
the generated files, not assumed. Informational only — coverage does not enter
the PASS precondition and is not a finding.

## Findings

### Finding 1: Step 13c bundle-promotion helper path (BUG-0181) — fixed and demonstrated end to end

**Category:** pass

`team/scripts/lib/cm/release_finalize.sh` now anchors the lookup to the stage
file's own scripts root (`$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)`)
and appends `cm/promote_bundled_items.py`. Verified by sourcing the exact
expression from the file's own location in two layouts:

- dev tree → `<worktree>/team/scripts/cm/promote_bundled_items.py`, exists
- simulated installed (`<root>/scripts/lib/cm/`) → `<root>/scripts/cm/promote_bundled_items.py`, exists

Acceptance 1: `bash team/scripts/harness/helloworld-flow-check.sh` exit 0,
`grep -c 'promote_bundled_items.py not found'` = 0, all three stages PASS
(decompose 4 tickets, verify green, ship tagged `ai_v0.1.0`).

Acceptance 2 (bundle file shows `## Status: done`) is not literally
demonstrable through the harness: the harness's intake requirements file is
operator-authored at `## Status: open`, and the helper promotes `running →
done` only, so it correctly logged `skip (status=open)`. The underlying
behavior was verified directly instead — a synthetic bundle at `running` with
one bundled bug at `running`, run through
`team/scripts/cm/promote_bundled_items.py <file> v9.9.9 <tasks> <bugs>`:
`promoted (running -> done)` for both, and both files read `done` afterwards.

Acceptance 3 (regression test in both layouts) is covered by the new
`team/tests/unit/test_cm_release_promote_helper_path.py` (3 tests, extracts
the resolution expression verbatim from the shell source). Acceptance 4: both
runners exit 0.

### Finding 2: explicit `--team-root` / `kanban_root` precedence (BUG-0184) — fixed

**Category:** pass

Reproduced the bug's own case on the RC tree from `team/pm-agent/`:

- `env -u PGAI_TASKS_DIR ... get_config(kanban_root=$SB)` → `$SB/tasks`
- `PGAI_TASKS_DIR=$HOME/pgai_agent_kanban/projects/pgai-agent-kanban/tasks ... get_config(kanban_root=$SB)` → `$SB/tasks` (was: the live install)
- with `PGAI_TASKS_DIR`, `PGAI_PROJECT_ROOT` and `PGAI_LOGS_DIR` all set to
  decoys, all six root-derived keys resolve under `$SB`
- `get_config()` with no explicit root → env still wins (`/live/tasks`), so
  the no-override path is unchanged

19 tests in `team/tests/unit/test_config.py`. See Finding 8 for the docstring
half of the same acceptance, which is not met.

### Finding 3: role-file path citations on an installed root (BUG-0187) — fixed

**Category:** pass

- `grep -rn 'PGAI_AGENT_KANBAN_ROOT_PATH/team/' team/roles/*.md` in the RC
  tree → no matches. Same grep against a root produced by the upgrade route →
  no matches.
- Sourcing the helper by the role file's own command line on that root
  succeeds: `source "$ROOT/scripts/lib/deep_verify_ledger.sh"` then
  `type -t deep_verify_ledger_read_latest` → `function`.
- Every `$PGAI_AGENT_KANBAN_ROOT_PATH/...` and `$KANBAN_ROOT/...` path ending
  in a file that installed governance names resolves under the root. The
  paths that do not resolve are runtime-state locations that legitimately do
  not exist on a fresh root (`HALT`, `projects/`, `logs/activity.log`,
  `artifacts/`, `locks/`), not shipped-file citations.

### Finding 4: install/upgrade docs parity (BUG-0185) — fixed and demonstrated

**Category:** pass

A shared manifest, `team/config/docs-manifest.txt`, is now the only list; both
`install.sh` (line ~735) and `team/scripts/upgrade.sh` (line ~1092) read it and
neither keeps a copy. The manifest is a proper superset of the transitive
closure of `docs/...` citations in `team/roles/*.md`, `team/README.md` and
`team/SOP-core.md` (nine cited paths, ten manifest entries, the extra being the
transitive `audits/v1.27.2-gate-remedy-census.md`).

Round trip, all inside `$PGAI_AGENT_KANBAN_TEMP_DIR`, never against the live
root: install the pre-fix tree (`ai_main`, stamped v2.10.3) into a throwaway
root → `rm -rf $ROOT/docs` to simulate the real operator situation (a root
upgraded from before the fresh-install fix) → `upgrade.sh --force --no-backup
--dev-tree <RC worktree>` from the fixed tree → exit 0 → all 10/10 manifest
paths present under `$ROOT/docs`, including the nested `audits/` entry.

### Finding 5: RC empty-commit detection (BUG-0183) — check exists, works, and fires on this RC

**Category:** pass-with-caveat

`team/scripts/lint_rc_empty_commits.py` (new, 280 lines) auto-discovers local
`<prefix>rc/vX.Y.Z` branches, excludes merges via `git rev-list --no-merges`,
and compares trees with `git diff-tree`. It is wired into
`run-integration-tests.sh` through `gated_runner_run_quality_lint` — a
CONVERT-class site with a `# CENSUS-ANCHOR: rc-empty-commits-01` marker and a
matching Locator Index entry; the census lint reports 0 offenders. `--help`
names both arguments and documents all three exit codes.

Pointed at this RC it exits 1 and names two commits. The caveat is the content
of that result, not the check: see Lint Findings and BUG-0192. Acceptance 2
(role files state the throwaway-directory rule) is met — both
`team/roles/CODER.md` and `team/roles/WRITER.md` gained the rule beside the
existing pre-completion self-check.

### Finding 6: reachability guard widened (BUG-0185 / BUG-0187 guard halves)

**Category:** pass

`team/tests/unit/test_installed_governance_docs_citations_resolve.py` grew from
92 to 632 changed lines and now carries six tests across four dimensions, each
with a green and a red case: docs/ citations under a fresh install; docs/
citations under an upgraded root (install → strip → upgrade); root-variable
command paths (`$PGAI_AGENT_KANBAN_ROOT_PATH/...`, `$KANBAN_ROOT/...` ending in
`.sh`/`.py`) resolving under the installed root; plus a regex-only test proving
a bare repository-relative `team/scripts/...` prose mention produces no
finding. The red cases build synthetic source copies with a deliberately
dangling citation and assert it is reported.

### Finding 7: hygiene ride-alongs (BUG-0169, 0170, 0172, 0173, 0176, 0179, 0180, 0182, 0186)

**Category:** pass

- BUG-0169 — `ubi9` gone from `doc-lint.sh` `FEATURE_PATTERNS`; the tree ships
  `debian`, `rocky9`, `claude-abliterated` only, and the remaining tokens match.
- BUG-0170 — `lint_dockerfile_static.py` docstring no longer cites v2.9.20.
- BUG-0172 / BUG-0179 — `test_project_paths_bash.py` and
  `test_gated_runner_findings_on_failure.py` are clean of bug citations.
- BUG-0173 — verified against its own four acceptance commands:
  `lint_comment_provenance.py --code-dirs team/scripts team/scripts/lib
  team/scripts/cm team/pgai_agent_kanban --prose-dirs release-notes` exits 0;
  `grep -rnE '(CODER|WRITER|TESTER|PM|CM)-2026[0-9]{4}' release-notes/` → no
  matches; `grep -n 'See TESTER report for filed bug IDs'
  team/scripts/lib/cm/release_notes.sh` → no matches; no
  `provenance-allowlist` marker in `release-notes/`. The `_TESTER_TASK_ID`
  plumbing is removed from the generator, not just the output line.
- BUG-0176 — the `nothing consumes that list yet` clause is gone from all
  seven `team/read-order/*.txt`; each now names `_wd_build_governance_line` in
  `team/scripts/lib/wake_driver_invoke.sh` as the active consumer.
- BUG-0180 — `lint_gate_census_compliance.py` reports `0 offenders` across 32
  files with 88 anchored sites exempted; the census rollup is `55 sites, 0
  unclassified` and the three previously-unclassified gated-runner refusal
  sites now carry classification comments and/or Locator Index entries.
- BUG-0182 — `helloworld-flow-check.sh --help` no longer says "this ticket",
  "this task's status.md", or "per this ticket's constraint"; the remaining
  uses of the word "ticket" are the domain's own noun (materialized kanban
  tickets).
- BUG-0186 — the two named files are clean; the scan set now includes
  `team/tests/` and the repository-root `.sh` files, demonstrated by planting a
  synthetic `BUG-9999` comment in a repo-root `install.sh` on a synthetic tree
  and seeing it reported. See Finding 9 for the remedy-command defect this
  widening left behind.
- BUG-0178 (bundled but already fixed at v2.10.3) — no v2.10.4 ticket was
  assigned to it, correctly: the fresh-install half shipped in v2.10.3 and is
  confirmed intact (the pre-fix v2.10.3 install in the Finding 4 round trip
  delivered `docs/` on its own). It re-entered this bundle because Step 13c
  never promoted its `## Status` — which is precisely what BUG-0181 fixes, so
  the re-bundling is a symptom that this RC removes.

### Finding 8: `lib/config.py` docstring claims a precedence layer the code does not implement

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (documentation/implementation mismatch in one module; the fixed dimension works)
**Regression:** yes (the inaccurate docstring text is new in this RC; the underlying cfg-layer behavior is unchanged)
**Integrity Damage:** no
**Filed As:** BUG-0190 (Path C-A — filed as BUG, code defect / regression / test failure)

The new docstring states that an explicit `kanban_root` "gets a precedence
layer of its own, above all four of the above" and that each root-derived key
"is re-derived from the explicit kanban_root after every other layer has
applied." The code guards only the environment loop (`if explicit_root and key
in suffix_map: continue`); the `user_cfg` and `install_cfg` `config.update`
calls run before it, unguarded. Demonstrated: with a throwaway `HOME` whose
`~/.config/pgai-kanban.cfg` sets `PGAI_TASKS_DIR=/ambient/user/cfg/tasks`,
`get_config(kanban_root=$SB)['PGAI_TASKS_DIR']` returns
`/ambient/user/cfg/tasks`, not `$SB/tasks`. BUG-0184's acceptance criterion 3
("the docstring states the precedence the code implements") is therefore not
met, though acceptance 1 and 2 are.

### Finding 9: the provenance lint's printed re-run command cannot see repository-root scripts

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (a false-clean signal for one newly added file class; the lint itself is non-fatal)
**Regression:** yes (the repo-root scan and its unreachable remedy both arrive in this RC)
**Integrity Damage:** no
**Filed As:** BUG-0188 (Path C-A)

`_collect_repo_root_scripts()` is called only when `--code-dirs` was NOT
supplied, but the two-step `To fix` recipe the lint prints on every finding
builds its step-2 verify command with `--code-dirs` named explicitly.
Following the printed recipe therefore cannot re-check the file that failed.
Reproduced on a synthetic tree: default run reports `install.sh:2 [BUG-ID]
provenance reference 'BUG-9999'`; the printed step-2 command, run verbatim
with the violation untouched, prints `OK — 0 findings across 0 file(s)` and
exits 0. Secondary, cosmetic: the `scanning N file(s) in <dirs>` banner lists
`code_dirs` only, so the repository root never appears in it.

### Finding 10: a new module's docstring delegates its rationale to kanban task state

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (prose only; no behavior affected)
**Regression:** yes (the instance in `lint_rc_empty_commits.py` is new in this RC; a sibling instance predates it)
**Integrity Damage:** no
**Filed As:** BUG-0189 (Path C-A)

`team/scripts/lint_rc_empty_commits.py:32` reads `see the "Constraints"
section of the task this lint was written for`. A reader of the shipped tree
cannot open that document, so the sentence explains nothing — the same shape
BUG-0186 recorded as a violation one release ago (`see this ticket's status.md
for the measured wall-clock number`). One pre-existing sibling:
`team/scripts/lint_dockerfile_static.py:76`, `explicitly out of scope for the
ticket that added this file` (introduced at v2.9.22, untouched by this RC's
one-line edit to that file). Neither is detectable by
`lint_comment_provenance.py`, whose patterns match identifier shapes only, so
this residue class falls outside the check that exists to prevent it.

### Finding 11: requirements-doc acceptance criteria

**Category:** pass

- "Each bundled item is addressed in the implementation" — met; see Findings
  1-7 for per-item evidence, including why BUG-0178 correctly needed no
  ticket.
- "All Python files compile cleanly" — `python3 -m compileall -q team
  install.sh` exit 0; all 337 `.py` files pass `python3 -m py_compile`.
- "All bash files pass syntax check" — every `.sh` file in the tree passes
  `bash -n`; zero failures.

### Finding 12: standing checks

**Category:** pass

- **Step 6.1 (release-state drift):** `pp_last_released_version` → `v2.10.3`;
  `semver_gt v2.10.4 v2.10.3` true. `release-state.md` holds `Active RC:
  v2.10.4`, a well-formed `## Last Released: v2.10.3`, and `RC Opened By Task:
  CM-20260907-074-open-rc-2-10-4`; the branch `ai_rc/v2.10.4` exists. No drift.
- **Step 2a (pre-flight merge):** no DONE task retains a live feature branch —
  all were deleted after merge per Procedure A. No unmerged branch, no
  zero-commits-ahead anomaly.
- **Step 6.8 (synthetic release simulation):** triggered (the diff touches
  `release_finalize.sh`, `release_notes.sh`, `install.sh`, `upgrade.sh`).
  `team/tests/integration/test_cm_release_synthetic.py` is present and passed
  inside the green integration suite.
- **Step 6.10 (prereq graph):** no cycles; all eight prerequisite task IDs on
  TESTER-075 exist and are DONE; bookends correct (CM-074 open-rc → seven
  CODER + one WRITER → TESTER-075 → CM-077 release, currently WAITING);
  sequence numbers non-colliding (076 unused, which is legal).
- **Step 6.13 (skip-cites-real-bug):** no placeholder skip IDs; no skip
  annotation cites a `BUG-NNNN` at all; `lint_skip_bug_gate.sh` exits 0 with
  "0 missing files".
- **Governance byte budgets:** all seven roles `under`.
- **Live-install safety:** every state-mutating invocation in this run
  (install.sh, upgrade.sh, helloworld-flow-check.sh, the config repros) ran
  with `PGAI_AGENT_KANBAN_ROOT_PATH` repointed at a `mktemp -d -p
  "$PGAI_AGENT_KANBAN_TEMP_DIR"` root. Verified afterwards: the live install's
  task folder count and `projects.cfg` are byte-identical to the pre-run
  snapshot. The worktree is clean and stash-free at the end of verification.

## Lint Findings (non-blocking)

Ingested from `${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/findings-unit.json` (1
finding) and `.../findings-integration.json` (2 findings). Two distinct lint
classes after aggregation; neither elevates the recommendation or the systemic
risk on its own.

- `comment_provenance` — 1043 instances, all under `team/tests/` (817 unit, 225
  integration, 1 archived artifact) across ~200 files, newly visible because
  this RC widened the default scan to `team/tests/` and the repository root.
  Zero instances remain in the surfaces that were already in scope. Anticipated
  by BUG-0186's Fix note, but BUG-0172 and BUG-0179 (the bugs it pointed at)
  each covered exactly one file and both close with this RC, so the remainder
  would otherwise be untracked. — filed BUG-0191 (Path C-A), one bug for the
  whole class, every instance listed inside it as `<path>:<line>`.
- `rc_empty_commits` — 2 instances: `48a8a50` and `a138279`, both `initial
  empty commit`, both on `ai_feature/CODER-20260907-073-hygiene-red-lints`,
  both merged into this RC, neither named in that task's `status.md`. The
  preventive role-file rule from WRITER-20260907-068 merged at 00:40:27, after
  both commits (23:54:51 and 00:29:13), so this is not the new rule failing —
  it is the class the new detection lint was built to catch, catching itself on
  its first run. — filed BUG-0192 (Path C-A). BUG-0183, which tracks the
  v2.10.2 instance, is `running` and closes with this RC, which is why a new
  filing rather than a citation.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=0d freshness=stale — the last lane run was less than a day ago but exercised v2.9.20, not the v2.10.4 under verification, so it is stale on the version condition alone; the `docker/claude-abliterated` flavor failed its `FROM` arity on both engines and `podman/rocky9` failed its `COPY --chmod=+x` step, all cited by BUG-0167 (filed by that deep-verify run, now `done` — the static Dockerfile lint added since covers both shapes).

Read through `deep_verify_ledger_read_latest` from
`$PGAI_AGENT_KANBAN_ROOT_PATH/scripts/lib/deep_verify_ledger.sh`; the verdict
is the ledger's own word and is reported, not re-derived. The lane was not run
during this verification, per Step 5d. No bug filed for the red record — the
deep-verify run that produced it filed its own.

## Autonomous criterion

Manual interventions required: **none**. All four checks answer NO.

1. **Tasks force-promoted from WAITING to BACKLOG by hand?** NO. Every
   RC-cycle task's `status.md` carries the wake script's own promotion note;
   no hand-edited State field was found.
2. **`release-state.md` edited outside the CM scripts?** NO. Its mtime is
   `2026-09-07 21:58`, matching its own `RC Opened At: 2026-09-07T21:58:22`
   to the second — untouched since `cm-open-rc.sh` wrote it.
3. **Scripts patched mid-build?** NO in the sense the check means. This RC's
   deliverables *are* changes under `team/scripts/`, all of them carried by
   their own tickets' feature branches and merged through the normal path; no
   out-of-band commit touches tooling.
4. **Queue files manually edited?** NO. All eight `tasks/queues/*.md` mtimes
   fall inside wake-script activity windows (21:49-01:49) and their contents
   are consistent with the tasks that ran. No `HALT`, `HALT-AFTER`, or
   project-scoped `HALT` flag is present.

The build ran PM materialization → seven CODER tickets → one WRITER ticket →
CM open-RC → TESTER with no operator action.

## Caveats

- **The live install is nine releases behind the RC.**
  `$HOME/pgai_agent_kanban/VERSION` reads `v2.9.19`, so the governance
  files this TESTER session actually read are v2.9.19's long-form role files,
  not the compact ones this RC ships. One visible consequence: the installed
  `roles/TESTER.md` still names
  `team/pgai_agent_kanban/reports/lint_finding_groups.py` as the lint-finding
  aggregation helper, and that module does not exist in the RC tree (it was
  last present at v2.7.6). The RC tree's own `team/roles/TESTER.md` and the
  `docs/` reference set no longer cite it, so the RC is self-consistent and
  this is not a defect in v2.10.4 — the aggregation was performed by hand
  instead, one bug per lint class, as the rule requires. Recorded because a
  reader comparing this report against the installed role file will notice
  the divergence.
- **Governance byte budgets are at the margin.** All seven roles are `under`,
  but `pm` sits at 65535/65536 and `cm` at 71679/71680 — one byte of headroom
  each, and the `cm` figure is the result of this RC compressing three
  sentences in `roles/CM.md` to make room for two added ones. Any future
  one-character addition to those two roles' assembled governance stack trips
  the budget. Not a finding (the criterion is met), but the next role-file
  edit in either lane should expect to have to remove text first.
- **Shipped release notes were edited.** `release-notes/v2.10.2.md` and
  `release-notes/v2.9.22.md` were rewritten by CODER-20260907-072. This is in
  tension with the "notes are final once stamped, committed, and tagged" rule
  that `roles/CM.md` states — and that this same RC restates — but it was
  explicitly requested by BUG-0173's Fix part 1, so it is a sanctioned
  exception rather than a process breach. Noted so the precedent is not read
  as a general licence.
- **`--code-dirs` semantics.** The provenance lint's `--code-dirs` flag
  replaces the whole default set rather than adding to it, which is documented
  in the module docstring but is the mechanism behind Finding 9. Anyone
  scripting the lint should pass no `--code-dirs` at all unless deliberately
  narrowing.
- **Census Locator Index form.** `team/SOP-core.md` describes index entries as
  `<path>:<token> <CLASS>`, while `docs/blocking-site-census.md` states — and
  `_CENSUS_LOCATOR_LINE_RE` enforces — the `<path>:<line> <CLASS>` form. The
  census document explains the divergence and the lint passes with 0
  offenders, so nothing is broken; the two documents predate this RC and the
  discrepancy is recorded as an observation only, not a finding.
