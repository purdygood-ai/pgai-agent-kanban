# Verification Report: rc/v2.23.2

**Task:** TESTER-20260915-061-verify-2-23-2
**RC branch:** `ai_rc/v2.23.2` @ `7be3010f619eb6bf21195873e097b911acd37114` (worktree detached HEAD, clean, no stashes)
**Requirements:** `projects/pgai-agent-kanban/requirements/v2.23.2-priority-bundle-20260915.md`
**Bundled item:** `priority/PRIORITY-0003-docs-manifest-authoring-guard.md`
**Verified in:** `/tmp/pgai_kanban_tmp/projects/pgai-agent-kanban/worktrees/TESTER-20260915-061-verify-2-23-2`
**Fresh run:** artifacts/ was empty at start; no prior-run report or gaps file was read.

## Recommendation

SHIP-WITH-CONCERNS

## Systemic Risk

low

## Summary

v2.23.2 is a single-item priority bundle whose job is to give the docs-manifest
invariant an authoring-time signal instead of leaving it to a twelve-minute unit
suite at release-candidate time. The RC extracts the `docs/` citation regex,
seed-file rule, and transitive-closure walk out of the backstop test into a
shared module (`team/pgai_agent_kanban/lib/docs_citation_scan.py`), adds a
seconds-fast install-free source-tree check (`team/scripts/check_docs_citations.py`),
registers that check in `run-unit-tests.sh`'s quality-class lint block, adds a
red/green/wrapper test suite for it, and adds a pre-completion self-check bullet
to `team/roles/CODER.md` and `team/roles/WRITER.md`. Nine files, +975/-91, three
merge commits, one per ticket.

Every acceptance criterion in the bundled priority is met, and both gated
runners exit 0. All seven criteria were verified by running the check rather
than reading it, including three failure modes triggered by hand: a removed
manifest entry cited from a seed file, a removed entry cited only transitively
from another `docs/` page, and a brand-new citation added to a role file. Two
defects were found outside the criteria and are filed; both are hygiene-class,
neither touches behaviour an operator or agent depends on, and the release is
not made worse by shipping them. Hence SHIP-WITH-CONCERNS at `low` systemic
risk rather than PASS.

## Pre-flight

- Worktree `HEAD` = `7be3010f619eb6bf21195873e097b911acd37114` = `ai_rc/v2.23.2` — pass.
- `git status --short` empty — pass.
- `git stash list` empty — pass.
- Merge verification across every task under `tasks/` whose `status.md` is `DONE` and whose README names a live `## Feature Branch`: zero UNMERGED, zero zero-commits-ahead anomalies. All three cycle feature branches were deleted after merge (normal post-merge cleanup), and their three `--no-ff` merge commits are present on the RC — pass.
- Step 6.1 drift check: `pp_last_released_version pgai-agent-kanban` → `v2.23.1`; `semver_gt v2.23.2 v2.23.1` true. `release-state.md` `## Last Released` = `v2.23.1`, well-formed. `## Active RC` = `v2.23.2` and `ai_rc/v2.23.2` exists — no drift.
- Step 6.10 prereq graph: acyclic, every referenced task ID exists, bookends linked (`CM-...-060-open-rc-2-23-2` at the head, `TESTER-...-061` then `CM-...-063-release-2-23-2` at the tail), sequence numbers 057–063 non-colliding — pass.

## Test Suites (authoritative — gated runners)

| Runner | Exit | Result |
|---|---|---|
| `bash team/scripts/run-unit-tests.sh --coverage` | **0** | 4617 passed, 31 skipped in 782.57s (13m 03s) |
| `bash team/scripts/run-integration-tests.sh --coverage` | **0** | 510 passed, 1 skipped in 547.60s (9m 08s) |

`UNIT_EXIT == 0` AND `INTEGRATION_EXIT == 0`. No test failures, so no
`bug` / `stale-assertion` / `pre-existing-bug` test categorization was needed.
Captured output: `$PGAI_AGENT_KANBAN_TEMP_DIR/projects/pgai-agent-kanban/tester/unit_output.txt`
and `integration_output.txt`.

The new lint ran inside the real runner and was silent:

```
Running docs-citation manifest lint...
check_docs_citations: ok — 24 governance file(s) scanned, 13 docs/ citation(s) checked, all named in .../team/config/docs-manifest.txt.
[gated-runner] docs_citation_manifest: OK
```

`tests/unit/test_docs_citation_manifest_lint_wired.py` — 5 passed inside the
gated unit run.

## Code Coverage

Informational only; does not enter the PASS precondition.

- Unit:        line 77%  |  branch 63%   (16476 stmts, 4950 branches; blended `percent_covered` 73%)
- Integration: line 10%  |  branch 8%    (blended 9.5%)

Source: `$PGAI_AGENT_KANBAN_TEMP_DIR/coverage/unit.json` and `integration.json`,
`totals` object, fields `percent_statements_covered` / `percent_branches_covered`
confirmed against the generated files rather than assumed.

## Findings

### Finding 1: the seconds-fast check exists, is install-free, and completes in well under a second

**Category:** pass

Priority AC 1. `team/scripts/check_docs_citations.py` exists, is executable
(`-rwxr-xr-x`), imports the shared module, and runs no subprocess and no
install. Timed from the worktree root: elapsed 0s (sub-second; the authoring
session measured ~0.1s on the same host). Output:

```
check_docs_citations: ok — 24 governance file(s) scanned, 13 docs/ citation(s) checked, all named in .../team/config/docs-manifest.txt.
```

24 scanned = 4 flat root governance files + 7 `team/roles/*.md` + the 13 manifest
documents the walk followed one hop into. 13 citations checked = the 13 manifest
entries. `--help` names every one of its three flags (`--repo-root`,
`--team-dir`, `--manifest`), satisfying the coding-standards help directive.

### Finding 2: each finding names the citing file, the line, the cited path, and the exact manifest line to add

**Category:** pass

Priority AC 2. Triggered by removing `read-order-manifests.md` from a throwaway
copy of the tree under `$PGAI_AGENT_KANBAN_TEMP_DIR` (the dev tree's own
manifest was never opened for writing):

```
check_docs_citations: FAIL — 1 docs/ citation(s) reachable from the governance files are not named in .../docs-manifest.txt.

Cause(s):
  - team/README.md:43 cites 'docs/read-order-manifests.md', which .../docs-manifest.txt does not name

To fix:
  1. Add the following line(s) to .../docs-manifest.txt:
       read-order-manifests.md
  2. Re-run: python3 team/scripts/check_docs_citations.py
```

Citing file, 1-based line, cited path, and the verbatim remedy line are all
present, and the remedy is the exact text the manifest needs. Exit code 1.

### Finding 3: zero findings against the tree as shipped

**Category:** pass

Priority AC 3. Exit 0 both standalone and inside the gated runner (quoted
above). Independently corroborated by
`test_check_docs_citations_is_silent_against_the_shipped_tree`, which also
guards `citations_checked > 0` so a broken seed list cannot masquerade as a
clean tree.

### Finding 4: the red case is proven by a test, and by two further failure modes triggered by hand

**Category:** pass

Priority AC 4. `team/tests/unit/test_docs_citation_manifest_lint_wired.py`
copies `team/` to a throwaway root under `tmp_path`, deletes exactly one
manifest entry, and asserts the check reports exactly one finding whose
`cited`, `source` and `line` are all checked by identity — not merely a non-zero
exit — and then asserts the dev tree's manifest is byte-identical afterwards. A
second test drives the same fixture through the real
`gated_runner_run_quality_lint` call shape and asserts the wrapper still returns
0 while exactly one `docs_citation_manifest` finding lands in the findings file
with `severity: quality`. Both passed in the gated run.

Beyond the shipped tests, I triggered two failure modes the tests do not cover,
per Step 6.5:

1. **Second-hop citation.** Removing `audits/v1.27.2-gate-remedy-census.md` —
   cited only from `docs/blocking-site-census.md`, itself reached transitively —
   produced `docs/blocking-site-census.md:81 cites 'docs/audits/v1.27.2-gate-remedy-census.md'`,
   exit 1. The closure walk really is transitive, not seed-only.
2. **The historical failure class.** Appending `See docs/brand-new-thing.md for
   details.` to `team/roles/CODER.md` in the throwaway copy produced
   `team/roles/CODER.md:178 cites 'docs/brand-new-thing.md'` with remedy line
   `brand-new-thing.md`, exit 1. This is exactly the shape of BUG-0227,
   BUG-0244 and BUG-0266 — the three instances that motivated the priority — and
   the check catches it in under a second.

The three usage/internal failure paths also behave as documented: missing
manifest → exit 2 with a named path, comment-only manifest → exit 2 "empty or
contains only comments", missing team dir → exit 2 with the `--team-dir` remedy.

### Finding 5: an authoring agent meets the check on both surfaces

**Category:** pass

Priority AC 5 asks for the gated-runner lint set, or a role-file self-check, or
both. The RC ships both.

- `team/scripts/run-unit-tests.sh` registers it once as
  `gated_runner_run_quality_lint "docs_citation_manifest" -- python3 scripts/check_docs_citations.py`,
  placed in the pre-pytest quality-lint block after `cd "$REPO_ROOT/team"`, with
  a comment declaring the site CONVERT-class and explaining why. It is
  deliberately absent from `check_breakage_class.py` and from
  `run-integration-tests.sh`, and a test asserts the former.
- `team/roles/CODER.md` and `team/roles/WRITER.md` each gain one bolded
  pre-completion bullet in the PHASE 2 delivery section, immediately after the
  provenance-litter bullet, naming the command, what it prints, the remedy, and
  the fact that a finding is an authoring chore rather than a BLOCK. Read in
  full: both are accurate, both cross-reference the manifest header rather than
  duplicating it, and both name the gated runner and the unit suite as the later
  surfaces, so neither overclaims. The bare relative command path
  (`python3 team/scripts/check_docs_citations.py`) matches the adjacent
  pre-existing `lint_hygiene.py` bullet's convention and is correct for the dev
  tree worktree where CODER and WRITER actually work.

### Finding 6: the backstop test still exists, still proves the invariant, and was refactored without weakening

**Category:** pass

Priority AC 6, and the priority's "out of scope: removing or weakening the
backstop". `team/tests/unit/test_installed_governance_docs_citations_resolve.py`
is present and passed inside the gated unit run. Its diff was read line by line:
the private `_DOCS_CITATION_RE`, `_ROOT_GOVERNANCE_FILES`, `_governance_seed_files`
and BFS loop were deleted and replaced by calls into the shared module with
`is_resolved` = "is a real file under the installed root". The regex is
character-identical, the seed set is identical, and the walk is the same BFS;
dimension 3 (root-variable command paths) was left local and untouched, as the
module docstring states. The red-case tests still assert on the injected bogus
citation and its attribution to `roles/TESTER.md`. Duplication — the risk the
priority's own risk assessment names — is genuinely gone: the citation regex now
appears in exactly one source file.

### Finding 7: the requirements document's own three criteria

**Category:** pass

- "Each bundled item is addressed in the implementation" — PRIORITY-0003's four
  goals and seven criteria are all met (Findings 1–6).
- "All Python files compile cleanly" — `python3 -m py_compile` clean on all four
  added/changed Python files; the unit suite's own import-completeness lint is OK.
- "All bash files pass syntax check" — `bash -n team/scripts/run-unit-tests.sh`
  clean (the only changed shell file); `set -euo pipefail` and the
  `trap cleanup_on_exit EXIT` are preserved, and the new block sits before the
  findings-file write hook it depends on.

### Finding 8: the shared walk's docstring describes a call contract the code does not honour

**Category:** bug
**Fix Effort:** small (1 CODER task — reword one docstring paragraph, add one spy test)
**Systemic Risk:** low (hygiene; documentation that could steer a future third caller wrong, no effect on either shipped caller)
**Regression:** yes (the text is new in this RC; this RC's changes produced it)
**Integrity Damage:** no
**Filed As:** BUG-0294 (Path C-A — filed as BUG: code defect / regression / test failure)

`walk_docs_citation_closure` in `team/pgai_agent_kanban/lib/docs_citation_scan.py`
documents `resolved_target` as "Called for every citation regardless of
`is_resolved`'s answer — a caller that wants the walk to stop at an unresolved
citation returns `None` for it." The loop calls `resolved_target` only inside the
`if is_resolved(...)` branch, so it is never called for a citation that produced
a finding, and the documented way to "stop the walk at an unresolved citation"
controls nothing (the walk already stops there unconditionally). Verified by
reading both, and by the fact that both shipped callers carry a now-dead
resolution re-check inside their own `resolved_target` — which is why neither is
affected today. A third caller written against the documented contract would get
behaviour it did not read about. This is a new shared module whose whole purpose
is to be the one implementation two-plus callers share, so its contract prose is
load-bearing.

### Finding 9: three bug files entered the project's bug folder outside the session that claims to have filed them, one staged through bare `/tmp`

**Category:** bug
**Fix Effort:** small (1–2 CODER tasks — litter-check cleanup or runnable remedy, plus a provenance record at claim time)
**Systemic Risk:** low (no effect on the RC tree or on any shipped behaviour; it degrades the trustworthiness of the bug ledger's provenance headers)
**Regression:** yes (the events occurred inside this RC's build window; I could not establish that the pattern predates it)
**Integrity Damage:** no (nothing was lost or corrupted — the three bug files are well-formed and their content is valid; both copies of the stray file are byte-identical)
**Filed As:** BUG-0295 (Path C-A)

The wake log records
`litter check: CODER-20260915-058-docs-citation-check-wired left 1 bare /tmp entry(ies): /tmp/BUG-0293-provenance-sweep-convergence.md`.
That file is still on disk and is byte-identical, with the same
`2026-09-16T00:01:01Z` mtime, to
`projects/pgai-agent-kanban/bugs/BUG-0293-provenance-sweep-convergence.md`.
Writing to bare `/tmp` is forbidden by the temp-file convention, and the litter
check narrates the violation but leaves the file in place.

Separately, `BUG-0290` (23:54:53Z), `BUG-0291` (23:54:48Z) and `BUG-0293`
(00:01:01Z) all carry `Source Task: TESTER-20260915-054-verify-2-23-1`, a task
that finished at `22:25:39Z`. The only agent session running in that window was
`CODER-20260915-058-docs-citation-check-wired` (22:54:31 → 00:26:54); CM's wakes
in the window all exited early on `lock.contended`, and the PM, writer, overwatch
and cleanup logs record nothing. `BUG-0293` is not cited anywhere in TESTER-054's
own report, so it cannot have been authored by the session it names. No log line
records any of the three filings.

**Attribution is undetermined and the bug says so.** Either the CODER session
wrote into `bugs/` (a swim-lane violation — CODER does not file bugs), or an
operator hand-filed them from TESTER-054's report while that session happened to
be running (a manual intervention in a chain premised on needing none). Either
way the `Filed By` / `Source Task` headers are not a reliable provenance record,
and discovery and PM act on exactly those headers. Nothing about the RC tree is
implicated.

## Lint Findings (non-blocking)

Ingested from `$PGAI_AGENT_KANBAN_TEMP_DIR/lint/findings-unit.json` (9 findings)
and `findings-integration.json` (9 findings) — the same two lint classes in both
files, grouped per class for a single filing each.

- `hygiene` — 7 provenance instances, every one in a file this RC did not touch: `team/scripts/overwatch-attach-fix-rewrite.sh:26` (`BUG-0163`); `team/tests/integration/test_overwatch_scaffolding_gates_resolution.py:244` (`CODER-20260915-047`); `team/tests/unit/test_cleanup_root_override.py:50,417,425,444` (`BUG-0283`); `team/tests/unit/test_overwatch_lane_classification.py:222` (`CODER-20260915-047`) — cites **BUG-0291** (open), whose Symptom lists the identical seven file:line pairs. Also covered by **BUG-0292** (open, filed by the hygiene autofiler) and by **BUG-0293** (open) for the recurrence pattern. No duplicate filed.
- `governance_byte_budget` — 2 instances: `coder over governance byte budget: assembled=70322 bytes budget=69632 bytes`; `writer over governance byte budget: assembled=69908 bytes budget=69632 bytes`. No open or running bug covered the class, so one was filed: **BUG-0296** (Path C-A). Both breaches are attributable to this RC's two role-file bullets (the parent commit left `coder` 16 bytes of headroom and `writer` 430, against a 706-byte bullet), so the authoring ticket's "no new quality-class finding" criterion was unsatisfiable for CODER.md at any length; the authoring session recorded the trade-off and shipped, which is correct for a narrated non-gate.

Per the quality-class rule, neither class moves `## Recommendation` or
`## Systemic Risk`. Every other lint in both runners reported OK, including the
anti-pattern lint (0 findings across 516 files), the census-compliance lint (0
offenders, so the new call site needs no census anchor — the CONVERT wrapper
never refuses), the dual-channel pair lint, the runnable-remedy lint, the
doc-command-truth lint (649 citations, 0 findings), the help-presence lint, and
the two post-suite cleanliness checks.

## Deep-Verify Freshness

deep-verify: found=true verdict=failed-to-build version=v2.9.20 commit=d37bb68 timestamp=2026-09-07T06:52:06+00:00 age=8d freshness=stale — the docker/claude-abliterated and podman/claude-abliterated pairs failed on a Dockerfile `FROM` parse error and podman/rocky9 on a `COPY --chmod` step; cites BUG-0167, filed by that deep-verify run and since closed, and the record is stale on both counts (8d exceeds the 7-day window, and v2.9.20 is not the v2.23.2 under verification), so nothing has been built cold against this tree. Not re-filed and not a finding: TESTER runs no container build during RC verification.

## Autonomous criterion

1. Tasks force-promoted from WAITING to BACKLOG by hand? **NO.** Every transition in this cycle is logged by the wake script (`transitioned to WORKING [A]`, `stamped ## Model`); this task's own status recorded "Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied".
2. `release-state.md` edited outside the CM scripts? **NO.** It carries exactly the four canonical fields with values consistent with `cm-open-rc.sh`'s write (`Active RC: v2.23.2`, `RC Opened By Task: CM-20260915-060-open-rc-2-23-2`, `Last Released: v2.23.1`), and `ai_rc/v2.23.2` exists as claimed.
3. Scripts patched mid-build? **NO.** The RC's three commits map one-to-one to the three worker tickets; the only tooling file changed (`team/scripts/run-unit-tests.sh`) is the ticket's own deliverable, and no commit on the RC is unaccounted for.
4. Queue files manually edited? **NO.** `tasks/queues/tester_backlog.md` carries the standard `- [x]` / `- [A]` markers in wake-script shape.

**Manual interventions required: none** for the four enumerated checks. Two
process observations that do not fit them, recorded rather than counted:

- The bug-folder writes described in Finding 9 happened inside a CODER session's
  window. If their cause turns out to be an operator hand-filing, that is a
  manual intervention this RC's build carried — which is why the bug asks for the
  cause to be named at resolution time.
- A project-scoped `HALT-AFTER` holding `rc:v2.23.2` was armed and draining
  through 00:49:02Z (six log confirmations across the CM and PM wakes) and no
  longer exists at the time of this report. No `HALT` was created at either
  scope and no `## HALT Event` block was appended to `release-state.md`, so no
  auto-promotion fired — the shape is consistent with the documented operator
  resume (removing the sentinel), not with a drain. Noted because it changes what
  happens after this RC ships: nothing will park the lane unless it is re-armed.

## Caveats

- **The cheap check does not catch a manifest entry whose file is missing.**
  Verified by hand: adding `phantom.md` to the manifest and citing
  `docs/phantom.md` from a role file leaves the check at exit 0, because
  "resolved" means "named in the manifest", not "exists on disk". `install.sh`
  silently skips a named-but-absent path, so that variant still dangles on an
  installed root and is caught only by the twelve-minute backstop. This is the
  documented division of labour — both the module and the script docstrings say
  so explicitly, and the priority's AC asks only for the absent-from-manifest
  case — so it is recorded as a residual hole, not a gap. Closing it would cost
  one `.is_file()` call and is worth considering in a later loop.
- **The manifest header's seed-set parenthetical is narrower than the walk.**
  The header states the closure is seeded from "(team/roles/*.md, team/README.md,
  team/SOP-core.md)" and the new sentence claims the check "walks the same seed
  set ... this header states"; the check also seeds from `team/DIRECTIVES.md` and
  `team/OVERVIEW.md`. The parenthetical predates this RC and the check's superset
  errs safe (no citation escapes), so this is noted rather than filed.
- **The live kanban install is at v2.9.19** while the RC is v2.23.2. Consequence
  hit during this run: the installed `roles/TESTER.md` still carries the
  `$PGAI_AGENT_KANBAN_ROOT_PATH/team/scripts/lib/...` source paths that BUG-0187
  fixed, so following Step 5d verbatim fails with `command not found` on an
  installed root and the installed-path form had to be used instead. The RC tree
  itself is clean of that pattern (grep: zero `$VAR/team/` hits across all
  governance files), so this is an install-currency observation about the
  operator's root, not an RC defect.
- **Step 6.13's first grep produces false positives on this tree.** The
  placeholder-and-missing-ID greps report `BUG-1234`, `BUG-1235`, `BUG-1237`,
  `BUG-1239` as missing; all four are string literals inside
  `team/tests/unit/test_skip_bug_citation_scan.py`'s own fixtures, not real skip
  annotations. The authoritative gate (`lint_skip_bug_gate.sh`, run pre-pytest)
  reported `skip_cites_real_bug: OK`. No finding.
- **Step 6.8 skipped with note:** the diff touches no release-lifecycle file
  (`cm-release.sh`, `cm-open-rc.sh`, `cm-cancel-rc.sh`, `project_paths.sh`, or
  any script in the open/close cycle). `run-unit-tests.sh` is under
  `team/scripts/` but does not participate in the RC lifecycle.
- **Test-fidelity note (not a finding).** The wrapper-level red-case test sources
  `scripts/lib/gated_runner_lint.sh` by a path relative to the pytest working
  directory, which the gated runner sets to `$REPO_ROOT/team`. It is correct
  under the runner (and passed there), and it follows the neighbouring
  `test_doc_truth_lint_wiring.py` pattern, but it would not resolve if someone
  ran pytest from the repository root. Worth knowing; not worth a bug, since the
  gated runner is the only sanctioned way to run the suite.
- No secrets, credentials or hardcoded `/tmp` paths appear in any added file; no
  provenance litter (bug IDs, task IDs, version bundles) appears in the RC's own
  new files, which is why the 7 hygiene findings are all in untouched files.
- No file read during this verification contained prompt-injection-shaped text.
