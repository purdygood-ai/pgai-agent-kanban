# Verification Report: rc/v2.24.18

## Summary

Verified `ai_rc/v2.24.18` (HEAD `4ba04b0`) against
`requirements/v2.24.18-bugfix-bundle-20260927.md`, a three-item BUG bundle:
BUG-0402 (a configured throwaway root of `/projects/<name>` widened the git
boundary guard's fixture-local exemption to every absolute path on the host),
BUG-0403 (any directory whose last two components read `projects/<name>`
widened the exemption to that directory's whole parent tree), and BUG-0404
(one integration test left a guard evidence record on every suite run). The
RC's diff against `ai_main` is five files, 419 insertions and 99 deletions:
the guard lib, `temp.sh`, census row 62, and the two test modules. Both gated
runners exited 0 (`5030 passed / 31 skipped`; `548 passed / 1 skipped`) with
zero lint findings in either findings file. Every acceptance bullet of all
three bundled items was verified behaviourally through the shipped shim
`team/scripts/wake/git-guard/git`, not by reading the code — including the
original failure modes, reproduced against pre-fix libraries to confirm the
new tests actually discriminate the fix. Two follow-ups are filed, neither a
shortfall against a bundled item: a Step 6.12 question (5) YES caused by the
live install lagging the release that suppresses it, and a Step 6.11
untested-new-mechanism gap on the new provenance resolver in `temp.sh`.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Findings

### Finding 1: Requirements acceptance criterion 1 — each bundled item is addressed

**Category:** pass

All three bundled items are addressed on the RC and verified behaviourally.
Detail per item in Findings 2-4. The two CODER tickets that carried the work
(`CODER-20260927-010-guard-root-provenance-bound`,
`CODER-20260927-011-hygiene-guard-evidence-noise`) are both `DONE`, both
merged `--no-ff` (`e70650c`, `4ba04b0`), and both feature branches deleted —
the normal post-merge shape.

### Finding 2: BUG-0402 — universal exemption at a root of `/projects/<name>`

**Category:** pass

Every acceptance bullet verified. The fix replaces the text-derived widening
with a two-condition test — the bare `projects/<name>` shape AND the stripped
parent resolving at or below `pgai_configured_temp_root`'s answer — and routes
every prefix comparison through a new `_git_boundary_guard_path_at_or_below`
that refuses an empty or bare-`/` root outright.

Bullet 1, refusal with exactly one record and unchanged refs, exercised
end-to-end through the shim with a real local bare upstream:

```
PGAI_AGENT_KANBAN_TEMP_DIR=/projects/name, role=coder
git -C <sandbox>/outside/work push --push-option ci.skip origin main
  -> error:   git-boundary-guard.non-cm-remote-verb
     message: role 'coder' invoked 'git push', a remote operation only CM may run
     remedy:  ask CM to run 'git push' -- CM is the only role permitted to fetch, pull, or push against origin
  -> exit 1, evidence records = 1, upstream for-each-ref before == after (empty)
```

The dual-channel contract (`error`, `message`, `remedy`) is intact, as the
ticket's constraint required.

Bullet 2, a destination *inside* `/projects/<name>` still exempt, exercised
through the shim with a stub git (that path is not creatable on this host):
`push /projects/name/inside/work HEAD` reached the stub, exit 0, zero records;
the control with the same root and a destination outside it was refused with
one record.

Bullet 3, no prefix glob from an empty string for any root including `/`
itself: `_git_boundary_guard_path_at_or_below` returns 1 for an empty or `/`
root before comparing, and both the configured-root and widened-root clauses
now go through it. Probed at a configured root of `/`: `/etc` is guarded.

Bullet 4, header comments true of the code: the function header now states
both conditions, names `pgai_configured_temp_root` as the provenance source,
and explicitly documents the degenerate empty-string/`/` case and that the
widening is refused there. See Finding 9 for the file-header "Public API"
block, which the ticket's Required Output named.

Bullet 5, census row 62: rewritten to the shipped bound (see Finding 6).

Bullets 6 and 7, new test covering bullet 1 and failing pre-fix, and the
`v2.24.17` tests passing unchanged: see Finding 5.

### Finding 3: BUG-0403 — shape-only anchor trusting any `projects/<name>` pair

**Category:** pass

Every acceptance bullet verified. The bound is now derived from a value the
framework produces: `pgai_configured_temp_root` in
`team/scripts/lib/temp.sh`, which resolves `kanban.cfg [paths]
tmp_root`/`tmp_subdir` (or the self-locate / hard-coded tiers) and
deliberately ignores `PGAI_AGENT_KANBAN_TEMP_DIR`. That is candidate 1 of the
two the bug report named; CODER's `status.md` records why candidate 2 (a
dispatcher-exported companion variable) was rejected — no dispatcher narrows
the variable to a per-project value today — which is a design call the ticket
explicitly left to CODER.

Bullet 1, an operator-shaped root whose parent is not framework territory,
exercised end-to-end through the shim with the provenance root pointed at a
throwaway `kanban.cfg` naming a different directory:

```
provenance root -> <sandbox>/real_framework_root
PGAI_AGENT_KANBAN_TEMP_DIR=<sandbox>/operator/projects/myproj, role=coder
git -C <sandbox>/operator/outside/work push origin main
  -> dual-channel refusal, exit 1, evidence records = 1, upstream refs unchanged
```

Bullet 2, the fixture-noise reduction surviving, exercised with the real
framework layout:

```
PGAI_AGENT_KANBAN_TEMP_DIR=/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban, role=coder
git -C /tmp/pgai_kanban_tmp/pytest/<fixture>/work push origin main
  -> push completed, evidence records = 0
```

Direct helper probes confirm the whole shape matrix in one place:

| configured root | candidate path | verdict |
|---|---|---|
| `/projects/name` | `$HOME/develop/pgai-agent-kanban` | guarded |
| `/projects/name` | `$HOME` | guarded |
| `/projects/name` | `/etc` | guarded |
| `/projects/name` | `/tmp/pgai_kanban_tmp/pytest/x` | guarded |
| `/projects/name` | `/projects/name/inside/work` | EXEMPT (correct) |
| `$HOME/projects/pgai` | `$HOME/develop/pgai-agent-kanban` | guarded |
| `$HOME/projects/pgai` | `$HOME` | guarded |
| `$HOME/projects/pgai` | `$HOME/projects/pgai/sub` | EXEMPT (correct) |
| `/` | `/etc` | guarded |
| `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban` | `/tmp/pgai_kanban_tmp/pytest/x` | EXEMPT (correct) |
| `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban` | `/tmp/pgai_kanban_tmp/projects/other/y` | EXEMPT (correct) |
| `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban` | `$HOME/develop/pgai-agent-kanban` | guarded |
| `$HOME/projects/pgai/tmp` | `$HOME/develop/anything` | guarded (the `v2.24.17` narrowing, still held) |

Bullet 3, the bound derived from a framework-produced value: confirmed
directly — with `PGAI_AGENT_KANBAN_TEMP_DIR=/projects/name` set,
`pgai_configured_temp_root` returns `/tmp/pgai_kanban_tmp` while
`pgai_temp_dir` returns `/projects/name`. The two answers differing is the
mechanism.

Both call sites are covered, as the ticket's constraint required: the helper
is the single home for the answer and is consulted by both the destination
half of `_git_boundary_guard_is_fixture_local`'s normal path and the
`repo_path` half of its failed-probe path.

### Finding 4: BUG-0404 — integration suite leaving a guard evidence record

**Category:** pass

All four acceptance bullets verified, and the original failure mode
reproduced as well as the new normal case.

Bullet 1, the module in isolation: run from the dev tree's `team/` with
`PGAI_AGENT_ROLE=tester` and a fresh
`PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE` — `2 passed`, evidence records = 0.

Bullet 2, both full gated suites from this non-CM wake session: this task's
`logs/git-boundary-guard.jsonl` was never created by either run. (It exists
now, for an unrelated reason recorded as Finding 7.)

Bullet 3, a genuine non-CM crossing still recording exactly one: verified
repeatedly across Findings 2 and 3.

Bullet 4, both runners exit 0: Finding 12.

Original failure mode, reproduced: `ai_main`'s version of
`test_overwatch_scaffolding_gates_resolution.py` placed on the RC's libraries
writes **1** record (`role":"tester" ... "fetch","origin"` against the
throwaway `dev_repo`) while both tests still pass. So the test change is what
closed this, and the guard change alone does not. That matters because the
fix is the sanctioned remedy rather than a suppression: the bug report's own
Expected says a test driving a CM verb "should establish the role that verb
requires, or explicitly establish that it is not running as any role", and
`_establish_drill_environment` sets `PGAI_AGENT_ROLE=cm` for the drill while
`_run_create_project` pops the variable entirely. Nothing is silenced — the
evidence write, the evidence path, and the guard are all untouched.

Checked that establishing `cm` for the drill does not create a real-remote
exposure: no `remote add` or `remote set-url` exists anywhere in
`create-project.sh`, `cm/cancel-rc.sh`, `cm/unwind-rc.sh`, or the test's own
`_init_git_repo_with_direct_commit`, so the `fetch origin` the drill reaches
fails locally with "no such remote" and never resolves a network destination.

### Finding 5: new unit tests discriminate the fix; the `v2.24.17` tests pass unchanged

**Category:** pass

Three tests were added to `team/tests/unit/test_git_boundary_guard.py`. Run
against **pre-fix libraries** — the RC's test file with `ai_main`'s
`git_boundary_guard.sh` and `temp.sh` substituted in a sandbox, confirmed
pre-fix by `grep -c '_git_boundary_guard_framework_provenance_root'` -> 0:

```
FAILED test_push_outside_root_still_refused_when_configured_root_is_bare_projects_name
FAILED test_push_outside_root_still_refused_when_projects_parent_is_not_the_framework_root
2 failed, 1 passed
```

The two bullets the ticket required to fail pre-fix do fail, for the right
reason (`assert result.returncode != 0` with the push having completed), and
the third — the inside-the-root exemption — correctly passes on both sides.

The four tests `v2.24.17` added (`..._root_has_projects_component_at_depth`,
`..._sibling_of_bare_project_subtree_root_is_still_exempt`, and the two
`option_value_ahead_of_remote` cases) pass on the RC and are untouched by the
diff: the diff's removed lines contain zero `def test` lines.

### Finding 6: census row 62 describes the bound that shipped

**Category:** pass

Row 62 previously read "when that configured root is exactly
`<framework_root>/projects/<name>` ... under the framework root the project
subtree hangs from", which BUG-0403 correctly called a provenance claim the
code did not make. It now reads `<parent>/projects/<name>` with nothing after
`<name>` **AND** `<parent>` resolving at or below the framework's own temp
root "as `team/scripts/lib/temp.sh`'s `pgai_configured_temp_root` resolves it
(kanban.cfg's `[paths]` `tmp_root`/`tmp_subdir`, or its self-locate/hard-coded
fallback — never inferred from the configured root's own text)", and
enumerates the three un-widened cases including "whose stripped `<parent>` is
empty, `/`, or otherwise not framework-owned territory". That matches the
shipped code clause for clause. The class stays BLOCK-WORTHY, so the
refusal's census classification is unchanged and correct.

`python3 scripts/lint_gate_census_compliance.py` run standalone from
`team/`: `OK -- 36 file(s) checked, 0 offenders`, 180 census-anchored sites
exempted, empty `FINDINGS_JSON`.

### Finding 7: Step 6.12 question (5) answers YES — two refused guard records in the cycle

**Category:** bug
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (one wake-layer export; the boundary itself held on every record)
**Filed As:** BUG-0405 (Path C-A — filed as BUG — code defect / regression / test failure)

Two `git-boundary-guard.jsonl` records exist in this cycle's task folders,
both refusals, both `fetch origin --tags --quiet` against the real dev tree
`$HOME/develop/pgai-agent-kanban`:

- `PM-20260927-002-decompose-v2-24-18-bugfix-bundle-2026092`,
  `2026-09-27T06:08:10Z`, role `pm`.
- `TESTER-20260927-013-verify-2-24-18` (this task), `2026-09-27T08:33:07Z`,
  role `tester` — produced by this verification's own Step 6.1, which
  instructs TESTER to answer "what version are we at" through
  `pp_last_released_version`.

Rated by what the attempts did to a running system, per
`docs/tester-verification-reference.md`: both are refusals, no ref moved on
any destination, nothing was published or imported — `low`.

Not caused by this RC. The suppression shipped in `v2.24.16` and is present
and correct on the RC (`team/scripts/lib/project_paths.sh:863` gates the
fetch on `PGAI_GIT_LOCAL_ONLY` *and* a CM-or-unset role). The live install the
pipeline runs from is `v2.24.12` — `grep -c '_plrv_role_lc'` returns 0 there
and 3 on the RC — so the fix ships into a file the running system does not
read until an operator upgrades. The complementary lever is already honoured
by every version of the function and already used by seven dashboard scripts,
and the wake layer does not set it: `team/scripts/lib/wake_guard_wiring.sh`
exports `PGAI_AGENT_ROLE` at line 107 and no `PGAI_GIT_LOCAL_ONLY` beside it.
Verified both directions against the live install with a throwaway evidence
file — without the flag, 1 record and the answer `v2.24.17`; with
`PGAI_GIT_LOCAL_ONLY=1`, 0 records and the same answer.

Filed rather than merely noted because the signal Step 6.12 (5) reads is
being spent on every cycle: 252 of the 329 records ever written across all
task folders are this one shape (186 `coder`, 37 `tester`, 24 `writer`, 5
`pm`), every one a refusal. That is the same erosion BUG-0404 was filed
against, at a call site BUG-0396's fix did not reach into the running install.

### Finding 8: the new provenance resolver in `temp.sh` has no direct test coverage

**Category:** gap
**Fix Effort:** small (1-2 CODER tasks)
**Systemic Risk:** low (isolated to one lib's test coverage; no live defect)
**Filed As:** BUG-0406 (Path C-A — filed as BUG)

`v2.24.18` adds two functions to `team/scripts/lib/temp.sh` —
`pgai_configured_temp_root`, a new **public** entry in that file's own API
list, and `_pgai_temp_dir_from_config`, the shared three-tier resolver. Neither
has a test of its own: `grep -rn 'pgai_configured_temp_root' team/tests/`
returns two docstring sentences and nothing executable,
`_pgai_temp_dir_from_config` returns nothing at all, and `team/tests/unit/`
has no module for `temp.sh`. Three header-stated contract properties are
unasserted — that the function ignores `PGAI_AGENT_KANBAN_TEMP_DIR`, that the
self-locate and hard-coded tiers resolve as documented, and that it "never
returns empty or `/`".

This is Step 6.11's "untested new mechanisms" category, and it matters
because `pgai_configured_temp_root`'s answer *is* the bound the whole
BUG-0402/BUG-0403 fix is built on.

The incidental coverage was measured rather than assumed. Patching the
resolver in a sandbox to honour the override and re-running the two relevant
guard tests fails only
`test_push_to_sibling_of_bare_project_subtree_root_is_still_exempt` — a test
`v2.24.17` added for a different purpose — while the new
`..._projects_parent_is_not_the_framework_root` passes either way. So the
load-bearing property is held, today, by an incidental assertion in a test
nobody wrote for it.

The "never returns empty or `/`" claim is literally true and semantically
weaker than it reads: a `kanban.cfg` of `tmp_root = /` and `tmp_subdir = /`
makes the resolver return `///`, which is neither the empty string nor the
one-character `/` and so passes both equality tests while naming the top of
the filesystem. **No boundary is lost** — the guard canonicalises with
`realpath -m` first, `///` normalises to `/`, and
`_git_boundary_guard_path_at_or_below` refuses `/` — verified behaviourally
with that `kanban.cfg` in place: at configured roots of both
`$HOME/projects/pgai` and `/projects/name`, outside paths stayed
guarded. `pgai_temp_dir` on `ai_main` carries the same tier logic and the same
two equality tests, so the shape predates this release; `v2.24.18` factored
the logic out, it did not write it.

### Finding 9: the file-header "Public API" block was named in the ticket's Required Output and not changed

**Category:** pass-with-caveat

`CODER-20260927-010`'s Required Output listed "the file header's Public API
block" among the places to update. The diff does not touch it. Checked
whether that leaves an untrue claim, since the acceptance criterion binds
truthfulness rather than change: it does not. The block (lines 11-125)
describes the destination exemption as resolving "under
`$PGAI_AGENT_KANBAN_TEMP_DIR`" and defers the detail to
`_git_boundary_guard_is_fixture_local`'s and
`_git_boundary_guard_path_under_temp_root`'s own headers; `grep -in` for
`projects`, `normalis`, `framework root`, and `provenance` across the header
block returns nothing. It never described the upward normalisation, so there
was nothing in it to correct, and acceptance bullet 4 is met. Noting it only
because a reader comparing the Required Output against the diff will see the
mismatch and should not have to re-derive that it is benign.

### Finding 10: process-history phrasing in two newly authored comments

**Category:** pass-with-caveat

`SOP-core.md` ("Naming: describe behavior, not scaffolding or provenance")
extends to comments and docstrings: "describe BEHAVIOR, never process
history." Two added lines carry process-history phrasing:

- `team/scripts/lib/git_boundary_guard.sh`, function header: "This is the
  bound this ticket exists for".
- `team/tests/unit/test_git_boundary_guard.py`, new docstring: "the fix for
  the empty-widened-parent defect must not turn the configured root itself
  into a second casualty".

Recorded as a caveat rather than a bug because it is genuinely borderline on
both the letter and the project's own practice. The letter of the rule and of
the ticket's own constraint prohibits a bug identifier, task ID, RC, or
version citation reaching a shipped artifact, and neither phrase is one — the
diff's added lines contain zero matches for `BUG-[0-9]{4}`,
`CODER-[0-9]{8}`, `TESTER-[0-9]{8}`, or a `v2.24.x` literal. As practice,
"this ticket" appears in 73 files on `ai_main` and the shipped hygiene lint's
`provenance` class reported zero findings on both suites, so the phrasing sits
inside the bound the project's own tooling enforces.

### Finding 11: `CODER-20260927-011`'s Summary names an operative cause the evidence contradicts

**Category:** pass-with-caveat

The ticket's constraint required CODER to "state in the task Summary which
one the fix addresses", and the Summary does state one — that the pre-`010`
temp-root widening was the actual gap and "ticket 010 already carries the
matching correction (no further `git_boundary_guard.sh` change made here)".
The reproduction in Finding 4 contradicts that: `ai_main`'s test file on the
RC's **post-`010`** libraries still writes exactly 1 record. Had `010`'s
change been the operative correction, that run would have been clean.

No shipped artifact is affected — the code change CODER-011 made is correct,
targeted, and demonstrably closes the record — so this is a process-record
inaccuracy, not a defect. Recorded because the next reader of that Summary
would otherwise inherit a wrong causal story about which of BUG-0404's two
candidate causes was live.

### Finding 12: gated runners

**Category:** pass

Both run from the dev-tree root with `--coverage`, output teed under
`$PGAI_AGENT_KANBAN_TEMP_DIR/tester/`:

- `bash team/scripts/run-unit-tests.sh --coverage` -> `UNIT_EXIT=0`,
  `5030 passed, 31 skipped in 792.79s`. Post-suite gates all OK
  (`bare_tmp_cleanliness`, `listener_cleanliness`, `scratch_sweep`).
- `bash team/scripts/run-integration-tests.sh --coverage` ->
  `INTEGRATION_EXIT=0`, `548 passed, 1 skipped in 681.51s`. Same three gates
  OK.

Both exit codes are the runners' own, not a direct pytest line. Direct pytest
was used only for targeted diagnosis (the pre-fix reproductions, the isolated
module runs, the synthetic release module) and no PASS claim rests on it.

Coverage, informational only and never part of the verdict, read from the
runners' JSON at `$PGAI_AGENT_KANBAN_TEMP_DIR/coverage/` with the field names
verified against the generated files (`percent_statements_covered_display`,
`percent_branches_covered_display`):

| suite | line (statement) % | branch % |
|---|---|---|
| unit | 76 % | 63 % |
| integration | 11 % | 9 % |

### Finding 13: `release-state.md` schema and version ordering

**Category:** pass

All four fields present and well-formed: `## Active RC` `v2.24.18`,
`## RC Opened At` `2026-09-27T06:17:31+00:00`, `## RC Opened By Task`
`CM-20260927-012-open-rc-2-24-18`, `## Last Released` `v2.24.17` (a
well-formed `vX.Y.Z`; its presence is by design and not drift). The file's
mtime is `2026-09-27T06:17:31` — byte-identical to its own `RC Opened At`
value, so it has not been touched since `cm/open-rc.sh` wrote it.

Version ordering checked through the shared helper, never inline:
`pp_last_released_version pgai-agent-kanban` -> `v2.24.17`, and
`semver_gt v2.24.18 v2.24.17` -> true. The RC's committed `VERSION` file still
reads `v2.24.17`, which is the correct pre-release state — `cm/release.sh`
writes the bump at tag time.

### Finding 14: structural checks, syntax, and constraint negatives

**Category:** pass

- Requirements acceptance criterion 2, all Python compiles: every `.py` in
  the tree outside `.git` compiled — 0 failures.
- Requirements acceptance criterion 3, all bash passes syntax check: `bash -n`
  over every `.sh` in the tree outside `.git` — 0 failures.
- All five diff files exist at their expected paths; no new or migrated files
  to cross-check.
- `set -euo pipefail`: neither changed lib sets it on `ai_main` either — they
  are sourced libraries and the shim owns the setting. No regression.
- No new script under `team/scripts/` (the diff's only `team/scripts/` entries
  are two `M` modifications), so the `--help` coverage check does not apply.
- No newly duplicated `lib/` implementation: `grep` finds no pre-existing
  path-prefix helper, so `_git_boundary_guard_path_at_or_below` is not a
  duplicate, and the `temp.sh` change *removes* duplication by factoring the
  tier logic into one place. The two `/tmp/pgai_kanban_tmp` literals moved
  with their existing `anti-pattern-allowlist: 2` justifications intact.

### Finding 15: Step 6.13 skip-cites-a-real-bug gate

**Category:** pass

`bash team/scripts/lint_skip_bug_gate.sh` standalone: "OK — all skip
annotations are compliant", `BUG-NNNN` file-existence check 0 missing.
Independent greps for placeholder shapes (`BUG-SKIP`, `BUG-` followed by
letters) return only fixture and archived-artifact content — bug-ledger
fixtures under `team/tests/fixtures/`, a `v2.3.0` disposition document, and
archived coverage run logs — none of them skip annotations.

### Finding 16: Step 6.8 synthetic release simulation

**Category:** pass

Run because the diff touches `team/scripts/lib/`, which the open/close cycle
sources: `team/tests/integration/test_cm_release_synthetic.py` ->
`17 passed in 46.35s`, and zero guard evidence records written during it. No
real branch was touched.

### Finding 17: Step 6.10 prerequisite dependency graph

**Category:** pass

The `v2.24.18` lane is `CM-20260927-012-open-rc-2-24-18` ->
`CODER-20260927-010` -> `CODER-20260927-011` -> `TESTER-20260927-013` ->
`CM-20260927-015-release-2-24-18` (`WAITING`, prerequisites naming all three
predecessors). No cycles; every referenced task ID resolves to a real folder;
the lifecycle bookends are linked at both ends; sequence numbers `010`-`015`
do not collide. Queue markers across all backlog files are exclusively
`[ ]`/`[A]`/`[W]`/`[x]` with no malformed bracket content, and each cycle
task's marker matches its own `## State`.

## Lint Findings (non-blocking)

None. `$PGAI_AGENT_KANBAN_TEMP_DIR/lint/findings-unit.json` and
`.../findings-integration.json` were both present with `"findings": []`
(`schema_version` 1, generated `2026-09-27T08:20:02+00:00` and
`2026-09-27T08:31:56+00:00`).

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=20d freshness=stale — the last cold-build run failed on `docker/claude-abliterated` (a `FROM` arity parse error) and `podman/rocky9` (a `COPY --chmod` step) and filed BUG-0167, which is now `## Status: done`; the record is stale on both counts (20d exceeds the 7-day window, and v2.9.20 is not the v2.24.18 under verification), so nothing is known about whether this tree's images build today.

## Autonomous criterion

Four NO, one YES.

1. **Tasks force-promoted from WAITING to BACKLOG by hand, bypassing the wake
   script's prerequisite evaluation — NO.** This task's own pre-run Summary
   read "Promoted from WAITING to BACKLOG by wake script: prerequisites
   satisfied", and every cycle task's `## State` matches its queue marker.
   `CM-20260927-015` is still `WAITING`/`[W]` with unmet prerequisites, which
   is the wake script's evaluation holding rather than being bypassed.
2. **`release-state.md` edited outside `cm-open-rc.sh` / `cm-release.sh` /
   `cm-cancel-rc.sh` — NO.** Its mtime is byte-identical to its own
   `## RC Opened At` value, so `cm/open-rc.sh` is the last writer.
3. **Scripts patched mid-build (a commit touching `team/scripts/`,
   `subagents/`, or other tooling while the RC was in flight) — NO.** The only
   `team/scripts/` changes on the RC are the two library files the
   requirements doc and both CODER tickets authorise, landed on their own
   feature branches and merged `--no-ff`. The live install's scripts are
   untouched since `2026-09-24 12:38` — before this cycle opened at
   `06:17:31` on `2026-09-27`.
4. **Queue files edited by hand — NO.** All five backlog files carry only
   well-formed markers, every cycle entry's marker matches its task's own
   `## State`, and mtimes track the cycle's own transitions.
5. **An agent other than CM invoked a remote git operation — YES.** Two
   records, both refusals, detailed in Finding 7: role `pm` in
   `PM-20260927-002-...` at `06:08:10Z` and role `tester` in this task at
   `08:33:07Z`, both `git -C $HOME/develop/pgai-agent-kanban fetch
   origin --tags --quiet`. No ref moved on any destination and nothing was
   published or imported. Filed as BUG-0405; rated `low`, so it does not
   raise `## Systemic Risk`.

`Autonomous operation criterion: Manual interventions required: none.` The
one YES is not a manual intervention — it is a refused remote attempt from
instructed, automated behaviour, and its cause is the live install lagging the
release that suppresses it rather than a hand touch on the pipeline.

## Caveats

- **Test fidelity.** The RC's tests exercise the real breaking condition
  rather than a proxy: the new guard tests drive the shipped shim
  `team/scripts/wake/git-guard/git` as a subprocess against real `git init`
  repositories and assert on the destination's `for-each-ref` before and
  after, so a refusal that failed to prevent the push would be caught. The one
  place a stub git stands in — the destination inside `/projects/name` — is
  unavoidable, since that path is not creatable, and it is disclosed in the
  test's own docstring. The concrete fidelity concern I can point at is
  Finding 8: the property that `pgai_configured_temp_root` ignores the
  environment override is asserted by no test directly, and the incidental
  assertion that happens to hold it lives in a `v2.24.17` test written for
  another purpose.
- **Two leftover feature branches from WONT-DO tasks.**
  `ai_feature/CODER-20260926-017-provenance-sweep` and
  `ai_feature/CODER-20260927-005-provenance-sweep` still exist locally;
  neither is an ancestor of the RC, and both task folders read `WONT-DO`. Step
  2's pre-flight covers `DONE` tasks only, so neither is an anomaly under it.
  Their tips are merge commits on the then-current RC branches (`0738ada` on
  `ai_rc/v2.24.15`, `05d5866` on `ai_rc/v2.24.17`), i.e. they carry no work of
  their own — the tasks were declined before committing anything, and only the
  branch pointer was left behind. Recorded as an observation about branch
  hygiene, not filed: nothing is at risk and the condition predates this RC.
- **The live install is five releases behind.**
  `$PGAI_AGENT_KANBAN_ROOT_PATH/VERSION` reads `v2.24.12` while
  `pp_last_released_version` reports `v2.24.17` shipped, and the install's
  files are dated `2026-09-24 12:38`. Every agent wake therefore runs framework
  code that predates `v2.24.13`-`v2.24.17`. This is the proximate cause of
  Finding 7 and is an operator action (`upgrade.sh`) rather than anything the
  loop can close, which is why BUG-0405 asks for the version-independent lever
  instead. Flagging it here because it also means this verification confirms
  the RC's behaviour, not the running system's.
- **Task sequence number 014 is unused** in the `20260927` series (`012`,
  `013`, `015` exist). Consistent with an approval slot not materialised on a
  bundle whose `## Human Approval Required` is `auto`. Not a collision and not
  a gap; noted so a reader counting the sequence is not left wondering.
- **Prior-run artifacts.** None. The `artifacts/` directory was empty at
  start, with no `report.md.previous-RUN-N` present, so this run had no prior
  verdict available to re-issue and none was consulted.
- **Live-install safety.** Every reproduction ran in a throwaway sandbox under
  `$PGAI_AGENT_KANBAN_TEMP_DIR` with its own evidence file, its own
  `kanban.cfg` where one was needed, and its own `git init` repositories. The
  pre-fix comparisons used copies of `team/` with no `.git` of their own rather
  than a copied worktree, so no real branch could move. The only invocation
  that touched anything outside a sandbox is the `pp_last_released_version`
  call in Finding 7, which is read-only and was refused.
